"""Higher-level action execution layer for Browser Agent.

Parses function_call responses from the Computer Use model and maps
them to PlaywrightComputer methods with coordinate denormalization.

Integrates with GPAActionLog for real-time step streaming and
SelfHealer for automatic recovery from UI changes.
"""

import asyncio
import base64
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from .playwright_computer import PlaywrightComputer
from .self_healer import SelfHealer

logger = logging.getLogger(__name__)


@dataclass
class ActionResult:
    """Result of a single executed action."""

    action_name: str
    success: bool
    details: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    screenshot: bytes | None = None
    url: str | None = None
    self_healed: bool = False
    heal_description: str = ""


# Maps action types to human-readable icon/descriptions
ACTION_ICONS: dict[str, str] = {
    "navigate": "navigate",
    "open_web_browser": "navigate",
    "click_at": "click",
    "click_element": "click",
    "hover_at": "click",
    "type_text_at": "type",
    "fill_field": "type",
    "scroll_document": "scroll",
    "scroll_at": "scroll",
    "wait": "wait",
    "key_combination": "type",
    "go_back": "navigate",
    "go_forward": "navigate",
    "search": "navigate",
    "select_option_at": "click",
    "get_dom_snapshot": "extract",
    "get_form_fields": "extract",
    "get_page_text": "extract",
    "confirm": "confirm",
}


class ActionExecutor:
    """Executes Computer Use model actions via PlaywrightComputer.

    Handles coordinate denormalization, action dispatch, error recovery,
    GPA step logging, and self-healing.
    """

    def __init__(self, computer: PlaywrightComputer) -> None:
        self._computer = computer
        self._healer = SelfHealer(computer)

    @property
    def self_healer(self) -> SelfHealer:
        return self._healer

    async def execute_action(
        self, action_name: str, params: dict[str, Any]
    ) -> ActionResult:
        """Execute a single action from the Computer Use model."""
        try:
            handler = self._get_handler(action_name)
            if handler is None:
                return ActionResult(
                    action_name=action_name,
                    success=False,
                    error=f"Unknown action: {action_name}",
                )

            state = await handler(params)
            return ActionResult(
                action_name=action_name,
                success=True,
                details=params,
                screenshot=state.screenshot if state else None,
                url=state.url if state else None,
            )
        except Exception as e:
            logger.error("Action %s failed: %s", action_name, e, exc_info=True)
            return ActionResult(
                action_name=action_name,
                success=False,
                details=params,
                error=str(e),
            )

    async def execute_with_healing(
        self,
        action_name: str,
        params: dict[str, Any],
        original_goal: str = "",
    ) -> ActionResult:
        """Execute an action with self-healing on failure."""
        result = await self.execute_action(action_name, params)

        if result.success:
            return result

        # Attempt self-healing
        logger.info(
            "Action %s failed, attempting self-heal: %s",
            action_name,
            result.error,
        )
        heal_result = await self._healer.attempt_heal(
            failed_action=action_name,
            failed_args=params,
            error_message=result.error or "Unknown error",
            original_goal=original_goal,
        )

        if heal_result and heal_result.get("success"):
            result.success = True
            result.self_healed = True
            result.heal_description = heal_result.get("description", "Auto-recovered")
            result.error = None
            # Get fresh state after healing
            try:
                state = await self._computer.current_state()
                result.screenshot = state.screenshot
                result.url = state.url
            except Exception:
                pass
            logger.info("Self-healed: %s", result.heal_description)

        return result

    async def execute_actions(
        self, actions: list[tuple[str, dict[str, Any]]]
    ) -> list[ActionResult]:
        """Execute a list of actions sequentially."""
        results = []
        for action_name, params in actions:
            result = await self.execute_action(action_name, params)
            results.append(result)
            if not result.success:
                logger.warning(
                    "Action %s failed, continuing: %s",
                    action_name,
                    result.error,
                )
        return results

    def describe_element(self, action_name: str, args: dict) -> str:
        """Generate human-readable element description."""
        if action_name == "navigate":
            url = args.get("url", "")
            try:
                from urllib.parse import urlparse
                domain = urlparse(url).netloc or url[:40]
                return f"Opening {domain}"
            except Exception:
                return f"Opening {url[:40]}"

        if action_name == "open_web_browser":
            return "Opening browser"

        if action_name in ("click_at", "hover_at"):
            x, y = args.get("x", 0), args.get("y", 0)
            return f"Element at ({x}, {y})"

        if action_name == "click_element":
            selector = args.get("selector", "")
            return f"Element: {selector[:50]}"

        if action_name == "type_text_at":
            text = args.get("text", "")
            preview = text[:20] + "..." if len(text) > 20 else text
            return f"Text field (typing '{preview}')"

        if action_name == "fill_field":
            selector = args.get("selector", "")
            return f"Form field: {selector[:30]}"

        if action_name in ("scroll_document", "scroll_at"):
            direction = args.get("direction", "down")
            return f"Page ({direction})"

        if action_name == "select_option_at":
            option = args.get("option_text", "")
            return f"Dropdown → {option[:30]}"

        if action_name == "key_combination":
            keys = args.get("keys", [])
            if isinstance(keys, list):
                return f"Keyboard: {'+'.join(keys)}"
            return f"Keyboard: {keys}"

        if action_name == "wait":
            secs = args.get("seconds", 2)
            return f"Waiting {secs}s"

        if action_name in ("go_back", "go_forward"):
            return "Navigation"

        if action_name == "get_dom_snapshot":
            return "Reading page structure"

        if action_name == "get_form_fields":
            return "Scanning form fields"

        if action_name == "get_page_text":
            return "Reading page content"

        return action_name.replace("_", " ").title()

    def describe_action(self, action_name: str, args: dict) -> str:
        """Generate action detail text."""
        if action_name == "navigate":
            return f"Navigating to {args.get('url', '')[:60]}"

        if action_name in ("click_at", "hover_at"):
            verb = "Clicking" if "click" in action_name else "Hovering"
            return f"{verb} at ({args.get('x', 0)}, {args.get('y', 0)})"

        if action_name == "click_element":
            return f"Clicking: {args.get('selector', '')[:50]}"

        if action_name == "type_text_at":
            text = args.get("text", "")
            preview = text[:30] + "..." if len(text) > 30 else text
            return f"Typing '{preview}' at ({args.get('x', 0)}, {args.get('y', 0)})"

        if action_name == "fill_field":
            val = args.get("value", "")
            preview = val[:20] + "..." if len(val) > 20 else val
            return f"Filling '{preview}' into {args.get('selector', '')[:30]}"

        if action_name in ("scroll_document", "scroll_at"):
            return f"Scrolling {args.get('direction', 'down')}"

        if action_name == "wait":
            return f"Waiting {args.get('seconds', 2)} seconds"

        if action_name == "key_combination":
            keys = args.get("keys", [])
            combo = "+".join(keys) if isinstance(keys, list) else str(keys)
            return f"Pressing {combo}"

        return action_name.replace("_", " ").title()

    def get_action_type(self, action_name: str) -> str:
        """Map action name to GPA action type."""
        return ACTION_ICONS.get(action_name, "click")

    async def get_screenshot_thumbnail_b64(self, max_size: int = 400) -> Optional[str]:
        """Get a small base64 screenshot for the GPA log."""
        try:
            if not self._computer._page:
                return None
            screenshot = await self._computer._page.screenshot(
                type="jpeg", quality=60
            )
            return base64.b64encode(screenshot).decode("utf-8")
        except Exception:
            return None

    def _get_handler(self, action_name: str):
        """Map action name to handler coroutine."""
        handlers = {
            "open_web_browser": self._handle_open_browser,
            "navigate": self._handle_navigate,
            "click_at": self._handle_click,
            "hover_at": self._handle_hover,
            "type_text_at": self._handle_type_text,
            "scroll_document": self._handle_scroll_document,
            "scroll_at": self._handle_scroll_at,
            "select_option_at": self._handle_select_option,
            "key_combination": self._handle_key_combination,
            "wait": self._handle_wait,
            "go_back": self._handle_go_back,
            "go_forward": self._handle_go_forward,
            "search": self._handle_search,
        }
        return handlers.get(action_name)

    async def _handle_open_browser(self, params: dict) -> Any:
        return await self._computer.open_web_browser()

    async def _handle_navigate(self, params: dict) -> Any:
        url = params.get("url", "")
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        return await self._computer.navigate(url)

    async def _handle_click(self, params: dict) -> Any:
        x = int(params.get("x", 0))
        y = int(params.get("y", 0))
        return await self._computer.click_at(x, y)

    async def _handle_hover(self, params: dict) -> Any:
        x = int(params.get("x", 0))
        y = int(params.get("y", 0))
        return await self._computer.hover_at(x, y)

    async def _handle_type_text(self, params: dict) -> Any:
        x = int(params.get("x", 0))
        y = int(params.get("y", 0))
        text = str(params.get("text", ""))
        press_enter = bool(params.get("press_enter", True))
        clear_before = bool(params.get("clear_before_typing", True))
        return await self._computer.type_text_at(
            x, y, text, press_enter=press_enter, clear_before_typing=clear_before
        )

    async def _handle_scroll_document(self, params: dict) -> Any:
        direction = params.get("direction", "down")
        return await self._computer.scroll_document(direction)

    async def _handle_scroll_at(self, params: dict) -> Any:
        x = int(params.get("x", 0))
        y = int(params.get("y", 0))
        direction = params.get("direction", "down")
        magnitude = int(params.get("magnitude", 3))
        return await self._computer.scroll_at(x, y, direction, magnitude)

    async def _handle_select_option(self, params: dict) -> Any:
        x = int(params.get("x", 0))
        y = int(params.get("y", 0))
        option_text = str(params.get("option_text", ""))
        return await self._computer.select_option_at(x, y, option_text)

    async def _handle_key_combination(self, params: dict) -> Any:
        keys = params.get("keys", [])
        if isinstance(keys, str):
            keys = [keys]
        return await self._computer.key_combination(keys)

    async def _handle_wait(self, params: dict) -> Any:
        seconds = int(params.get("seconds", 2))
        seconds = min(seconds, 10)
        return await self._computer.wait(seconds)

    async def _handle_go_back(self, params: dict) -> Any:
        return await self._computer.go_back()

    async def _handle_go_forward(self, params: dict) -> Any:
        return await self._computer.go_forward()

    async def _handle_search(self, params: dict) -> Any:
        return await self._computer.search()
