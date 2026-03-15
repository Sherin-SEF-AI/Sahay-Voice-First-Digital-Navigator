"""Self-Healing Automation — recovers from UI changes during browser tasks.

When an action fails (element not found, click missed, timeout), the healer
re-analyzes the page using DOM snapshot and attempts to find an alternative
path to achieve the same goal.
"""

import logging
from typing import Any, Optional

from google import genai
from google.genai import types
from ..config import settings

logger = logging.getLogger(__name__)


class SelfHealer:
    """Self-healing automation that recovers from UI changes.

    When an action fails, the healer:
    1. Takes a fresh DOM snapshot of the current page state
    2. Analyzes the error and the DOM to find alternative selectors/coordinates
    3. Returns alternative action(s) if found
    """

    def __init__(self, computer: Any):
        self._computer = computer
        self.max_heal_attempts = 2
        self.heal_history: list[dict] = []

    async def analyze_failure_with_vision(
        self,
        screenshot: bytes,
        failed_action: str,
        error: str,
        task_goal: str,
    ) -> str:
        """Analyze a failed action using Gemini Flash vision to suggest recovery.

        Sends the screenshot along with the failed action context to
        gemini-2.5-flash for visual analysis.  The model inspects the
        current UI state and proposes a concrete recovery action.

        Args:
            screenshot: PNG screenshot bytes of the current browser state.
            failed_action: Description of the action that failed.
            error: The error message from the failure.
            task_goal: The high-level goal the task is trying to achieve.

        Returns:
            A suggested recovery action as plain text.
        """
        prompt = (
            f"You are a browser automation recovery assistant.\n"
            f"The user was trying to accomplish: {task_goal}\n"
            f"The following action failed: {failed_action}\n"
            f"Error: {error}\n\n"
            f"Look at the attached screenshot of the current browser state. "
            f"Describe what you see and suggest ONE concrete recovery action "
            f"the automation agent should take next to get back on track. "
            f"Be specific — mention exact UI elements, buttons, or fields visible."
        )

        image_part = types.Part.from_bytes(data=screenshot, mime_type="image/png")
        text_part = types.Part.from_text(text=prompt)

        try:
            if settings.google_api_key:
                client = genai.Client(api_key=settings.google_api_key)
            else:
                client = genai.Client(
                    vertexai=True,
                    project=settings.google_cloud_project,
                    location=settings.google_cloud_location,
                )

            response = await client.aio.models.generate_content(
                model="gemini-2.5-flash",
                contents=types.Content(
                    role="user",
                    parts=[image_part, text_part],
                ),
            )

            suggestion = response.text.strip() if response.text else ""
            if suggestion:
                logger.info(
                    "Vision-based recovery suggestion: %s", suggestion[:120]
                )
                return suggestion

            return "Could not determine recovery action from screenshot."

        except Exception as exc:
            logger.warning("analyze_failure_with_vision failed: %s", exc)
            return f"Vision analysis unavailable: {exc}"

    async def attempt_heal(
        self,
        failed_action: str,
        failed_args: dict,
        error_message: str,
        original_goal: str,
    ) -> Optional[dict]:
        """Attempt to recover from a failed action.

        Uses DOM snapshot to find alternative elements when the original
        target fails (moved, renamed, hidden behind popup).

        Returns:
            dict with {"success": True, "description": "..."} or None.
        """
        for attempt in range(self.max_heal_attempts):
            try:
                result = await self._try_heal(
                    failed_action, failed_args, error_message, original_goal, attempt
                )
                if result:
                    self.heal_history.append(
                        {
                            "action": failed_action,
                            "error": error_message,
                            "healed": True,
                            "description": result["description"],
                            "attempt": attempt + 1,
                        }
                    )
                    return result
            except Exception as e:
                logger.warning("Heal attempt %d failed: %s", attempt + 1, e)

        self.heal_history.append(
            {
                "action": failed_action,
                "error": error_message,
                "healed": False,
            }
        )
        return None

    async def _try_heal(
        self,
        action: str,
        args: dict,
        error: str,
        goal: str,
        attempt: int,
    ) -> Optional[dict]:
        """Single heal attempt using DOM analysis."""

        # Strategy 1: For click failures, try to find element by DOM snapshot
        if action in ("click_at", "click_element"):
            return await self._heal_click(args, error)

        # Strategy 2: For type failures, find the input field in DOM
        if action in ("type_text_at", "fill_field"):
            return await self._heal_type(args, error)

        # Strategy 3: For navigation failures, try alternative URL patterns
        if action == "navigate":
            return await self._heal_navigate(args, error)

        # Strategy 4: Dismiss blocking overlays (cookie banners, popups)
        if "timeout" in error.lower() or "not visible" in error.lower():
            return await self._dismiss_overlays()

        return None

    async def _heal_click(self, args: dict, error: str) -> Optional[dict]:
        """Try to find and click the target element via DOM snapshot."""
        try:
            dom = await self._computer.get_dom_snapshot(max_length=10000)

            # Try dismissing overlays first
            dismissed = await self._dismiss_overlays()
            if dismissed:
                # Retry the original click after dismissing overlay
                if "selector" in args:
                    await self._computer.click_element(args["selector"])
                elif "x" in args and "y" in args:
                    await self._computer.click_at(int(args["x"]), int(args["y"]))
                return {
                    "success": True,
                    "description": "Dismissed overlay and retried click",
                }

            # If we have a selector that failed, try text-based alternatives
            if "selector" in args:
                selector = args["selector"]
                # Try broader selectors
                alternatives = self._generate_alternative_selectors(selector)
                for alt in alternatives:
                    try:
                        await self._computer.click_element(alt)
                        return {
                            "success": True,
                            "description": f"Used alternative selector: {alt}",
                        }
                    except Exception:
                        continue

        except Exception as e:
            logger.debug("Heal click failed: %s", e)

        return None

    async def _heal_type(self, args: dict, error: str) -> Optional[dict]:
        """Try to find the input field via DOM and fill it."""
        try:
            fields_text = await self._computer.get_form_fields()
            if "No form fields" in fields_text:
                return None

            value = args.get("value", args.get("text", ""))
            if not value:
                return None

            # Try the first visible input that's not already filled
            if "selector" in args:
                alternatives = self._generate_alternative_selectors(args["selector"])
                for alt in alternatives:
                    try:
                        await self._computer.fill_field(alt, value)
                        return {
                            "success": True,
                            "description": f"Filled field using alternative: {alt}",
                        }
                    except Exception:
                        continue

        except Exception as e:
            logger.debug("Heal type failed: %s", e)

        return None

    async def _heal_navigate(self, args: dict, error: str) -> Optional[dict]:
        """Try alternative URL patterns."""
        url = args.get("url", "")
        if not url:
            return None

        # Try with/without www
        alternatives = []
        if "www." in url:
            alternatives.append(url.replace("www.", ""))
        else:
            parts = url.split("://")
            if len(parts) == 2:
                alternatives.append(f"{parts[0]}://www.{parts[1]}")

        # Try HTTP vs HTTPS
        if url.startswith("https://"):
            alternatives.append(url.replace("https://", "http://"))

        for alt_url in alternatives:
            try:
                await self._computer.navigate(alt_url)
                return {
                    "success": True,
                    "description": f"Navigated to alternative URL: {alt_url}",
                }
            except Exception:
                continue

        return None

    async def _dismiss_overlays(self) -> Optional[dict]:
        """Try to dismiss common popups, cookie banners, modals."""
        dismiss_selectors = [
            # Cookie banners
            "button[id*='cookie'] >> text=Accept",
            "button[id*='cookie'] >> text=OK",
            "[class*='cookie'] button",
            "#onetrust-accept-btn-handler",
            ".cc-dismiss",
            # Generic close buttons
            "button[aria-label='Close']",
            "button[aria-label='Dismiss']",
            ".modal-close",
            ".close-button",
            "button.close",
            "[data-dismiss='modal']",
            # Overlay dismiss
            ".overlay-close",
        ]

        for selector in dismiss_selectors:
            try:
                page = self._computer._page
                if page:
                    el = await page.query_selector(selector)
                    if el and await el.is_visible():
                        await el.click()
                        logger.info("Dismissed overlay: %s", selector)
                        return {
                            "success": True,
                            "description": f"Dismissed overlay ({selector})",
                        }
            except Exception:
                continue

        return None

    def _generate_alternative_selectors(self, original: str) -> list[str]:
        """Generate alternative CSS selectors from the original."""
        alternatives = []

        # If it's an ID selector, try name, aria-label, placeholder
        if original.startswith("#"):
            name = original[1:]
            alternatives.extend(
                [
                    f'[name="{name}"]',
                    f'[aria-label*="{name}" i]',
                    f'[placeholder*="{name}" i]',
                ]
            )

        # If it's a class selector, try partial match
        if original.startswith("."):
            cls = original[1:]
            alternatives.append(f'[class*="{cls}"]')

        # Try text-based selectors
        if "=" not in original and not original.startswith((".", "#", "[")):
            alternatives.append(f"text={original}")
            alternatives.append(f'button:has-text("{original}")')
            alternatives.append(f'a:has-text("{original}")')

        return alternatives
