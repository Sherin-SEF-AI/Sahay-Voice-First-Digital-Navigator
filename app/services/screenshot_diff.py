"""Smart Screenshot Diff Engine.

Compares consecutive screenshots to detect changed regions.
When less than 30% of the screen changed, crops only the changed
region and provides context about the unchanged areas — reducing
token usage by 2-3x for the Computer Use model.
"""

import io
import logging
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from PIL import Image, ImageDraw

logger = logging.getLogger(__name__)

# Threshold for considering a pixel "changed" (0-255 per channel)
PIXEL_CHANGE_THRESHOLD = 30
# If less than this fraction of screen changed, use diff mode
DIFF_MODE_THRESHOLD = 0.30
# Minimum region size to bother with (pixels)
MIN_REGION_SIZE = 20
# Padding around changed regions (pixels)
REGION_PADDING = 40


@dataclass
class DiffRegion:
    """A rectangular region that changed between screenshots."""
    x1: int
    y1: int
    x2: int
    y2: int

    @property
    def width(self) -> int:
        return self.x2 - self.x1

    @property
    def height(self) -> int:
        return self.y2 - self.y1

    @property
    def area(self) -> int:
        return self.width * self.height

    def to_dict(self) -> dict:
        return {"x1": self.x1, "y1": self.y1, "x2": self.x2, "y2": self.y2,
                "width": self.width, "height": self.height}


@dataclass
class DiffResult:
    """Result of comparing two screenshots."""
    changed_fraction: float
    regions: list[DiffRegion] = field(default_factory=list)
    use_diff_mode: bool = False
    cropped_screenshot: Optional[bytes] = None
    context_summary: str = ""
    diff_overlay_png: Optional[bytes] = None

    def to_dict(self) -> dict:
        return {
            "changed_fraction": round(self.changed_fraction, 3),
            "changed_percent": round(self.changed_fraction * 100, 1),
            "use_diff_mode": self.use_diff_mode,
            "num_regions": len(self.regions),
            "regions": [r.to_dict() for r in self.regions],
            "context_summary": self.context_summary,
        }


class ScreenshotDiffEngine:
    """Computes efficient diffs between consecutive browser screenshots."""

    def __init__(self) -> None:
        self._prev_screenshot: Optional[bytes] = None
        self._prev_array: Optional[np.ndarray] = None
        self._screen_width: int = 0
        self._screen_height: int = 0
        self._frame_count: int = 0
        self._total_tokens_saved: int = 0

    def reset(self) -> None:
        """Reset state for a new task."""
        self._prev_screenshot = None
        self._prev_array = None
        self._frame_count = 0

    @property
    def stats(self) -> dict:
        return {
            "frames_processed": self._frame_count,
            "estimated_tokens_saved": self._total_tokens_saved,
        }

    def compute_diff(
        self,
        current_screenshot: bytes,
        page_description: str = "",
    ) -> DiffResult:
        """Compare current screenshot with previous one.

        Args:
            current_screenshot: PNG bytes of the current screenshot.
            page_description: Optional text description of page layout.

        Returns:
            DiffResult with change analysis and optional cropped image.
        """
        self._frame_count += 1

        # Convert to numpy array
        current_img = Image.open(io.BytesIO(current_screenshot)).convert("RGB")
        current_array = np.array(current_img)
        self._screen_width = current_img.width
        self._screen_height = current_img.height

        # First frame — no comparison possible
        if self._prev_array is None:
            self._prev_screenshot = current_screenshot
            self._prev_array = current_array
            return DiffResult(changed_fraction=1.0, use_diff_mode=False)

        # Compute pixel-level difference
        diff = np.abs(current_array.astype(np.int16) - self._prev_array.astype(np.int16))
        # Max channel difference per pixel
        max_diff = diff.max(axis=2)
        # Binary mask of changed pixels
        changed_mask = max_diff > PIXEL_CHANGE_THRESHOLD

        total_pixels = changed_mask.size
        changed_pixels = int(changed_mask.sum())
        changed_fraction = changed_pixels / total_pixels if total_pixels > 0 else 0.0

        # Find bounding boxes of changed regions
        regions = self._find_changed_regions(changed_mask)

        # Generate diff overlay for frontend visualization
        diff_overlay = self._create_diff_overlay(changed_mask, regions, current_img)

        result = DiffResult(
            changed_fraction=changed_fraction,
            regions=regions,
            diff_overlay_png=diff_overlay,
        )

        if changed_fraction < DIFF_MODE_THRESHOLD and regions:
            result.use_diff_mode = True
            # Crop to the union of all changed regions (with padding)
            union = self._union_regions(regions)
            padded = DiffRegion(
                x1=max(0, union.x1 - REGION_PADDING),
                y1=max(0, union.y1 - REGION_PADDING),
                x2=min(self._screen_width, union.x2 + REGION_PADDING),
                y2=min(self._screen_height, union.y2 + REGION_PADDING),
            )
            cropped = current_img.crop((padded.x1, padded.y1, padded.x2, padded.y2))
            buf = io.BytesIO()
            cropped.save(buf, format="PNG", optimize=True)
            result.cropped_screenshot = buf.getvalue()

            # Generate context summary
            result.context_summary = self._generate_context_summary(
                regions, page_description
            )

            # Estimate token savings
            original_tokens = (self._screen_width * self._screen_height) // 750
            cropped_tokens = (padded.width * padded.height) // 750
            self._total_tokens_saved += max(0, original_tokens - cropped_tokens)

        # Update previous
        self._prev_screenshot = current_screenshot
        self._prev_array = current_array

        return result

    def _find_changed_regions(self, mask: np.ndarray) -> list[DiffRegion]:
        """Find bounding boxes of contiguous changed regions."""
        if not mask.any():
            return []

        regions = []

        # Simple approach: find connected components using row/col projections
        # Project to rows and columns
        row_has_change = mask.any(axis=1)
        col_has_change = mask.any(axis=0)

        # Find contiguous row ranges
        row_ranges = self._find_contiguous_ranges(row_has_change)
        col_ranges = self._find_contiguous_ranges(col_has_change)

        if not row_ranges or not col_ranges:
            return []

        # For each row range, find the actual column extent
        for r_start, r_end in row_ranges:
            row_slice = mask[r_start:r_end, :]
            col_active = row_slice.any(axis=0)
            c_ranges = self._find_contiguous_ranges(col_active)
            for c_start, c_end in c_ranges:
                region = DiffRegion(x1=c_start, y1=r_start, x2=c_end, y2=r_end)
                if region.width >= MIN_REGION_SIZE and region.height >= MIN_REGION_SIZE:
                    regions.append(region)

        # Merge overlapping regions
        regions = self._merge_regions(regions)
        return regions

    @staticmethod
    def _find_contiguous_ranges(mask_1d: np.ndarray) -> list[tuple[int, int]]:
        """Find contiguous True ranges in a 1D boolean array."""
        ranges = []
        in_range = False
        start = 0
        for i, val in enumerate(mask_1d):
            if val and not in_range:
                start = i
                in_range = True
            elif not val and in_range:
                ranges.append((start, i))
                in_range = False
        if in_range:
            ranges.append((start, len(mask_1d)))
        return ranges

    @staticmethod
    def _merge_regions(regions: list[DiffRegion], gap: int = 50) -> list[DiffRegion]:
        """Merge nearby regions to reduce fragmentation."""
        if len(regions) <= 1:
            return regions

        # Sort by y1, then x1
        regions.sort(key=lambda r: (r.y1, r.x1))
        merged = [regions[0]]

        for r in regions[1:]:
            prev = merged[-1]
            # Check overlap/proximity
            if (r.y1 <= prev.y2 + gap and r.x1 <= prev.x2 + gap
                    and r.x2 >= prev.x1 - gap):
                # Merge
                merged[-1] = DiffRegion(
                    x1=min(prev.x1, r.x1),
                    y1=min(prev.y1, r.y1),
                    x2=max(prev.x2, r.x2),
                    y2=max(prev.y2, r.y2),
                )
            else:
                merged.append(r)

        return merged

    @staticmethod
    def _union_regions(regions: list[DiffRegion]) -> DiffRegion:
        """Compute the bounding box union of all regions."""
        return DiffRegion(
            x1=min(r.x1 for r in regions),
            y1=min(r.y1 for r in regions),
            x2=max(r.x2 for r in regions),
            y2=max(r.y2 for r in regions),
        )

    def _generate_context_summary(
        self, regions: list[DiffRegion], page_desc: str
    ) -> str:
        """Describe what changed and what stayed the same."""
        parts = []

        # Describe unchanged zones
        changed_zones = set()
        for r in regions:
            cy = (r.y1 + r.y2) / 2
            cx = (r.x1 + r.x2) / 2
            v_zone = "top" if cy < self._screen_height / 3 else (
                "middle" if cy < 2 * self._screen_height / 3 else "bottom"
            )
            h_zone = "left" if cx < self._screen_width / 3 else (
                "center" if cx < 2 * self._screen_width / 3 else "right"
            )
            changed_zones.add(f"{v_zone}-{h_zone}")

        all_zones = {"top-left", "top-center", "top-right",
                     "middle-left", "middle-center", "middle-right",
                     "bottom-left", "bottom-center", "bottom-right"}
        unchanged_zones = all_zones - changed_zones

        if unchanged_zones:
            parts.append(
                f"UNCHANGED areas: {', '.join(sorted(unchanged_zones))} "
                f"(navigation, headers, sidebars remain the same)."
            )

        parts.append(
            f"CHANGED areas: {', '.join(sorted(changed_zones))}. "
            f"Focus on the changed region in the cropped screenshot."
        )

        if page_desc:
            parts.append(f"Page context: {page_desc}")

        return " ".join(parts)

    def _create_diff_overlay(
        self,
        changed_mask: np.ndarray,
        regions: list[DiffRegion],
        current_img: Image.Image,
    ) -> Optional[bytes]:
        """Create a semi-transparent overlay highlighting changed regions."""
        if not regions:
            return None

        try:
            overlay = Image.new("RGBA", current_img.size, (0, 0, 0, 0))
            draw = ImageDraw.Draw(overlay)

            for region in regions:
                # Draw semi-transparent highlight
                draw.rectangle(
                    [region.x1, region.y1, region.x2, region.y2],
                    outline=(0, 200, 255, 180),
                    width=2,
                )
                # Light fill
                draw.rectangle(
                    [region.x1 + 2, region.y1 + 2, region.x2 - 2, region.y2 - 2],
                    fill=(0, 200, 255, 30),
                )

            buf = io.BytesIO()
            overlay.save(buf, format="PNG", optimize=True)
            return buf.getvalue()
        except Exception as e:
            logger.debug("Failed to create diff overlay: %s", e)
            return None
