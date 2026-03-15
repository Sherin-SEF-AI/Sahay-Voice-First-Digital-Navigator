"""Tests for the Browser Agent components."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.browser_agent.playwright_computer import PlaywrightComputer
from app.browser_agent.action_executor import ActionExecutor, ActionResult


class TestPlaywrightComputer:
    """Tests for PlaywrightComputer coordinate handling and initialization."""

    def test_default_screen_size(self):
        """Default screen size should be 1440x900."""
        pc = PlaywrightComputer()
        assert pc._screen_width == 1440
        assert pc._screen_height == 900

    def test_custom_screen_size(self):
        """Custom screen size should be stored correctly."""
        pc = PlaywrightComputer(screen_size=(1920, 1080))
        assert pc._screen_width == 1920
        assert pc._screen_height == 1080

    def test_denormalize_x_zero(self):
        """Normalized 0 should map to pixel 0."""
        pc = PlaywrightComputer(screen_size=(1440, 900))
        assert pc.denormalize_x(0) == 0

    def test_denormalize_x_midpoint(self):
        """Normalized 500 should map to ~720 for 1440px width."""
        pc = PlaywrightComputer(screen_size=(1440, 900))
        assert pc.denormalize_x(500) == 720

    def test_denormalize_x_max(self):
        """Normalized 999 should map close to screen width."""
        pc = PlaywrightComputer(screen_size=(1440, 900))
        result = pc.denormalize_x(999)
        assert result == 1438  # int(999/1000 * 1440)

    def test_denormalize_y_zero(self):
        """Normalized 0 should map to pixel 0."""
        pc = PlaywrightComputer(screen_size=(1440, 900))
        assert pc.denormalize_y(0) == 0

    def test_denormalize_y_midpoint(self):
        """Normalized 500 should map to 450 for 900px height."""
        pc = PlaywrightComputer(screen_size=(1440, 900))
        assert pc.denormalize_y(500) == 450

    def test_denormalize_y_max(self):
        """Normalized 999 should map close to screen height."""
        pc = PlaywrightComputer(screen_size=(1440, 900))
        result = pc.denormalize_y(999)
        assert result == 899  # int(999/1000 * 900)

    def test_headless_default(self):
        """Default headless mode should be True."""
        pc = PlaywrightComputer()
        assert pc._headless is True

    def test_headless_configurable(self):
        """Headless mode should be configurable."""
        pc = PlaywrightComputer(headless=False)
        assert pc._headless is False

    def test_action_log_empty_initially(self):
        """Action log should start empty."""
        pc = PlaywrightComputer()
        assert pc.get_action_log() == []

    def test_get_current_url_before_init(self):
        """URL should be empty before browser is initialized."""
        pc = PlaywrightComputer()
        assert pc.get_current_url() == ""


class TestActionExecutor:
    """Tests for the ActionExecutor action dispatch."""

    def setup_method(self):
        """Create a mock PlaywrightComputer for each test."""
        self.mock_computer = MagicMock(spec=PlaywrightComputer)
        self.executor = ActionExecutor(self.mock_computer)

    def test_unknown_action_returns_failure(self):
        """Unknown action names should return a failed result."""
        result = asyncio.get_event_loop().run_until_complete(
            self.executor.execute_action("nonexistent_action", {})
        )
        assert result.success is False
        assert "Unknown action" in result.error

    def test_handler_mapping_complete(self):
        """All expected action names should have handlers."""
        expected_actions = [
            "open_web_browser",
            "navigate",
            "click_at",
            "hover_at",
            "type_text_at",
            "scroll_document",
            "scroll_at",
            "select_option_at",
            "key_combination",
            "wait",
            "go_back",
            "go_forward",
            "search",
        ]
        for action in expected_actions:
            handler = self.executor._get_handler(action)
            assert handler is not None, f"Missing handler for: {action}"

    def test_navigate_adds_https(self):
        """Navigate handler should prepend https:// if missing."""
        mock_state = MagicMock()
        mock_state.screenshot = b"fake"
        mock_state.url = "https://example.com"
        self.mock_computer.navigate = AsyncMock(return_value=mock_state)

        result = asyncio.get_event_loop().run_until_complete(
            self.executor.execute_action("navigate", {"url": "example.com"})
        )
        self.mock_computer.navigate.assert_called_once_with("https://example.com")
        assert result.success is True

    def test_wait_caps_at_10_seconds(self):
        """Wait handler should cap at 10 seconds max."""
        mock_state = MagicMock()
        mock_state.screenshot = b"fake"
        mock_state.url = ""
        self.mock_computer.wait = AsyncMock(return_value=mock_state)

        asyncio.get_event_loop().run_until_complete(
            self.executor.execute_action("wait", {"seconds": 60})
        )
        self.mock_computer.wait.assert_called_once_with(10)
