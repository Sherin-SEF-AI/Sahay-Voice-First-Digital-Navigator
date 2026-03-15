"""Task Journal & GPA Action Log — Visual step-by-step audit trail for SAHAY.

Records every browser action with before/after screenshots, providing
a complete visual history the voice agent can summarize and that gets
persisted to Firestore.

The GPAActionLog adds real-time streaming of action steps to the frontend
GPA panel with status indicators, self-healing badges, and timing stats.
"""

import base64
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Coroutine, Optional

from . import firestore_service

logger = logging.getLogger(__name__)


# ── Original Journal (kept for backward compat) ──────────────────────


@dataclass
class JournalEntry:
    """A single step in a task's execution journal."""

    step_number: int
    timestamp: float
    action_type: str
    action_description: str
    screenshot_before: Optional[bytes] = None
    screenshot_after: Optional[bytes] = None
    url: str = ""
    success: bool = True
    error: Optional[str] = None


class TaskJournal:
    """Records and manages a visual audit trail for a single task."""

    def __init__(self, task_id: str, task_description: str) -> None:
        self._task_id = task_id
        self._task_description = task_description
        self._entries: list[JournalEntry] = []
        self._start_time = time.time()

    @property
    def task_id(self) -> str:
        return self._task_id

    @property
    def entry_count(self) -> int:
        return len(self._entries)

    def add_entry(self, entry: JournalEntry) -> None:
        self._entries.append(entry)
        logger.info(
            "Journal[%s] step %d: %s — %s",
            self._task_id[:8],
            entry.step_number,
            entry.action_type,
            "OK" if entry.success else f"FAIL: {entry.error}",
        )

    def create_entry(
        self,
        action_type: str,
        action_description: str,
        screenshot_before: Optional[bytes] = None,
        screenshot_after: Optional[bytes] = None,
        url: str = "",
        success: bool = True,
        error: Optional[str] = None,
    ) -> JournalEntry:
        entry = JournalEntry(
            step_number=len(self._entries) + 1,
            timestamp=time.time(),
            action_type=action_type,
            action_description=action_description,
            screenshot_before=screenshot_before,
            screenshot_after=screenshot_after,
            url=url,
            success=success,
            error=error,
        )
        self.add_entry(entry)
        return entry

    def get_summary(self) -> str:
        if not self._entries:
            return "No actions have been performed yet."

        lines = [f"Task: {self._task_description}"]
        lines.append(f"Total steps: {len(self._entries)}")
        lines.append("")

        for entry in self._entries:
            status = "completed" if entry.success else "failed"
            lines.append(
                f"Step {entry.step_number}: {entry.action_description} — {status}"
            )

        successful = sum(1 for e in self._entries if e.success)
        failed = len(self._entries) - successful
        elapsed = time.time() - self._start_time

        lines.append("")
        lines.append(
            f"Summary: {successful} successful, {failed} failed, "
            f"completed in {elapsed:.0f} seconds."
        )

        return "\n".join(lines)

    def get_full_journal(self) -> list[JournalEntry]:
        return list(self._entries)

    def get_entries_for_display(self) -> list[dict]:
        return [
            {
                "step": e.step_number,
                "action": e.action_type,
                "description": e.action_description,
                "url": e.url,
                "success": e.success,
                "error": e.error,
                "timestamp": e.timestamp,
            }
            for e in self._entries
        ]

    async def save_to_firestore(self) -> bool:
        steps = []
        for entry in self._entries:
            step_data = {
                "step_number": entry.step_number,
                "timestamp": entry.timestamp,
                "action_type": entry.action_type,
                "action_description": entry.action_description,
                "url": entry.url,
                "success": entry.success,
                "error": entry.error,
            }
            if entry.screenshot_after:
                screenshot_b64 = base64.b64encode(entry.screenshot_after).decode(
                    "utf-8"
                )
                if len(screenshot_b64) < 900_000:
                    step_data["screenshot"] = screenshot_b64
            steps.append(step_data)

        return await firestore_service.update_task(
            self._task_id,
            {
                "steps": steps,
                "screenshots_count": sum(
                    1 for e in self._entries if e.screenshot_after
                ),
            },
        )


# ── GPA Action Log ───────────────────────────────────────────────────


class ActionStatus(Enum):
    PENDING = "pending"
    IN_PROGRESS = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    NEEDS_INPUT = "input"
    CONFIRMED = "confirmed"


@dataclass
class GPAStep:
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    step_number: int = 0
    action_type: str = ""
    element_description: str = ""
    action_detail: str = ""
    url: str = ""
    status: ActionStatus = ActionStatus.PENDING
    timestamp_start: float = field(default_factory=time.time)
    timestamp_end: Optional[float] = None
    duration_ms: Optional[int] = None
    error_message: Optional[str] = None
    screenshot_after_b64: Optional[str] = None
    self_healed: bool = False
    heal_description: Optional[str] = None
    is_replay: bool = False

    def complete(self, status: ActionStatus, error: str = None):
        self.timestamp_end = time.time()
        self.duration_ms = int((self.timestamp_end - self.timestamp_start) * 1000)
        self.status = status
        if error:
            self.error_message = error

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "step": self.step_number,
            "type": self.action_type,
            "element": self.element_description,
            "detail": self.action_detail,
            "url": self.url,
            "status": self.status.value,
            "duration_ms": self.duration_ms,
            "error": self.error_message,
            "self_healed": self.self_healed,
            "heal_description": self.heal_description,
            "is_replay": self.is_replay,
            "timestamp": self.timestamp_start,
        }


# Type alias for the streaming callback
StreamCallback = Callable[[dict[str, Any]], Coroutine[Any, Any, None]]


class GPAActionLog:
    """Real-time action log that streams to the frontend GPA panel."""

    def __init__(self, task_id: str, task_description: str):
        self.task_id = task_id
        self.task_description = task_description
        self.steps: list[GPAStep] = []
        self.created_at = time.time()
        self._step_counter = 0
        self._stream_callback: Optional[StreamCallback] = None
        self.is_replay_mode = False

    def set_stream_callback(self, callback: StreamCallback):
        """Set async callback to stream step updates to frontend."""
        self._stream_callback = callback

    async def add_step(
        self,
        action_type: str,
        element_description: str,
        action_detail: str,
        url: str = "",
        is_replay: bool = False,
    ) -> GPAStep:
        self._step_counter += 1
        step = GPAStep(
            step_number=self._step_counter,
            action_type=action_type,
            element_description=element_description,
            action_detail=action_detail,
            url=url,
            status=ActionStatus.IN_PROGRESS,
            is_replay=is_replay or self.is_replay_mode,
        )
        self.steps.append(step)
        await self._stream_update(step)
        return step

    async def complete_step(
        self,
        step: GPAStep,
        status: ActionStatus,
        error: str = None,
        screenshot_after: str = None,
    ):
        step.complete(status, error)
        if screenshot_after:
            step.screenshot_after_b64 = screenshot_after
        await self._stream_update(step)

    async def mark_self_healed(self, step: GPAStep, heal_description: str):
        step.self_healed = True
        step.heal_description = heal_description
        await self._stream_update(step)

    async def mark_needs_input(self, step: GPAStep, prompt: str):
        step.status = ActionStatus.NEEDS_INPUT
        step.action_detail = prompt
        await self._stream_update(step)

    async def _stream_update(self, step: GPAStep):
        if self._stream_callback:
            try:
                await self._stream_callback(
                    {
                        "type": "gpa_step",
                        "task_id": self.task_id,
                        "step": step.to_dict(),
                        "total_steps": self._step_counter,
                        "task_description": self.task_description,
                        "is_replay": self.is_replay_mode,
                        "stats": self.get_live_stats(),
                    }
                )
            except Exception as e:
                logger.warning("GPA stream callback failed: %s", e)

    def get_live_stats(self) -> dict:
        succeeded = sum(1 for s in self.steps if s.status == ActionStatus.SUCCESS)
        failed = sum(1 for s in self.steps if s.status == ActionStatus.FAILED)
        healed = sum(1 for s in self.steps if s.self_healed)
        total_time = sum(s.duration_ms or 0 for s in self.steps)
        return {
            "total": len(self.steps),
            "succeeded": succeeded,
            "failed": failed,
            "self_healed": healed,
            "total_time_ms": total_time,
        }

    def get_summary(self) -> dict:
        stats = self.get_live_stats()
        return {
            "task_id": self.task_id,
            "task_description": self.task_description,
            "is_replay": self.is_replay_mode,
            **stats,
            "steps": [s.to_dict() for s in self.steps],
        }
