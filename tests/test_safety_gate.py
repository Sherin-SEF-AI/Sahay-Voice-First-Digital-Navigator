"""Tests for the Safety Gate system."""

import pytest

from app.browser_agent.safety_gate import (
    SafetyDecision,
    analyze_safety,
    generate_confirmation_prompt,
    SENSITIVE_ACTIONS,
    _extract_site_name,
)


class TestSafetyAnalysis:
    """Tests for safety decision logic."""

    def test_model_require_confirmation(self):
        """Model's require_confirmation should always trigger confirmation."""
        result = analyze_safety(
            action_description="Click a button",
            current_url="https://www.google.com",
            model_safety_decision="require_confirmation",
        )
        assert result == SafetyDecision.NEEDS_CONFIRMATION

    def test_submit_keyword_detected(self):
        """'submit' in action description should need confirmation."""
        result = analyze_safety("Submit the form")
        assert result == SafetyDecision.NEEDS_CONFIRMATION

    def test_payment_keyword_detected(self):
        """'pay' in action should need confirmation."""
        result = analyze_safety("Pay the bill amount")
        assert result == SafetyDecision.NEEDS_CONFIRMATION

    def test_login_keyword_detected(self):
        """'login' in action should need confirmation."""
        result = analyze_safety("Click the login button")
        assert result == SafetyDecision.NEEDS_CONFIRMATION

    def test_delete_keyword_detected(self):
        """'delete' in action should need confirmation."""
        result = analyze_safety("Delete the account")
        assert result == SafetyDecision.NEEDS_CONFIRMATION

    def test_banking_url_detected(self):
        """Banking domain URLs should trigger confirmation."""
        result = analyze_safety(
            "Click next",
            current_url="https://www.onlinesbi.com/dashboard",
        )
        assert result == SafetyDecision.NEEDS_CONFIRMATION

    def test_irctc_url_detected(self):
        """IRCTC URL should trigger confirmation."""
        result = analyze_safety(
            "Click next",
            current_url="https://www.irctc.co.in/booking",
        )
        assert result == SafetyDecision.NEEDS_CONFIRMATION

    def test_password_field_detected(self):
        """Actions involving passwords should need confirmation."""
        result = analyze_safety("Type password in the password field")
        assert result == SafetyDecision.NEEDS_CONFIRMATION

    def test_otp_field_detected(self):
        """Actions involving OTP should need confirmation."""
        result = analyze_safety("Enter the OTP code")
        assert result == SafetyDecision.NEEDS_CONFIRMATION

    def test_safe_browse_action(self):
        """Normal browsing should be SAFE."""
        result = analyze_safety(
            "Click the search button",
            current_url="https://www.google.com",
        )
        assert result == SafetyDecision.SAFE

    def test_safe_scroll_action(self):
        """Scrolling should be SAFE."""
        result = analyze_safety("Scroll down the page")
        assert result == SafetyDecision.SAFE

    def test_safe_navigate_action(self):
        """Navigation should be SAFE on non-sensitive URLs."""
        result = analyze_safety(
            "Navigate to the homepage",
            current_url="https://www.example.com",
        )
        assert result == SafetyDecision.SAFE

    def test_all_sensitive_actions_detected(self):
        """Every keyword in SENSITIVE_ACTIONS should trigger confirmation."""
        for keyword in SENSITIVE_ACTIONS:
            result = analyze_safety(f"Action: {keyword}")
            assert result == SafetyDecision.NEEDS_CONFIRMATION, (
                f"Keyword '{keyword}' did not trigger NEEDS_CONFIRMATION"
            )


class TestConfirmationPrompt:
    """Tests for confirmation prompt generation."""

    def test_prompt_with_url(self):
        """Prompt should include site name from URL."""
        prompt = generate_confirmation_prompt(
            "submit the booking form",
            url="https://www.irctc.co.in/booking",
        )
        assert "submit the booking form" in prompt
        assert "IRCTC" in prompt

    def test_prompt_without_url(self):
        """Prompt should work without a URL."""
        prompt = generate_confirmation_prompt("submit the form")
        assert "submit the form" in prompt
        assert "Should I go ahead?" in prompt

    def test_prompt_with_site_name(self):
        """Prompt should use explicit site name when provided."""
        prompt = generate_confirmation_prompt(
            "pay 500 rupees",
            site_name="IRCTC",
        )
        assert "IRCTC" in prompt
        assert "pay 500 rupees" in prompt


class TestSiteNameExtraction:
    """Tests for URL to site name extraction."""

    def test_simple_domain(self):
        """Simple domains should extract correctly."""
        assert _extract_site_name("https://www.google.com") == "GOOGLE"

    def test_gov_domain(self):
        """Government domains should extract correctly."""
        assert _extract_site_name("https://www.irctc.co.in") == "IRCTC"

    def test_empty_url(self):
        """Empty URL should return empty string."""
        assert _extract_site_name("") == ""
