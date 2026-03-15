"""Safety Gate — Human-in-the-loop confirmation for sensitive actions.

Ensures that SAHAY never executes sensitive operations (payments, logins,
form submissions, account changes) without explicit user confirmation.
"""

import logging
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)

SENSITIVE_ACTIONS: list[str] = [
    "submit",
    "pay",
    "login",
    "sign in",
    "signin",
    "confirm",
    "purchase",
    "delete",
    "download",
    "transfer",
    "checkout",
    "place order",
    "send money",
    "approve",
    "authorize",
    "register",
    "sign up",
    "signup",
    "change password",
    "reset password",
    "withdraw",
]

SENSITIVE_DOMAINS: list[str] = [
    "bank",
    "sbi",
    "icici",
    "hdfc",
    "axis",
    "kotak",
    "paytm",
    "phonepe",
    "gpay",
    "razorpay",
    "paypal",
    "payment",
    "pay.",
    "upi",
    "netbanking",
    "onlinesbi",
    "irctc",
    "passport",
    "digilocker",
    "income",
    "tax",
    "epfo",
    "uidai",
    "aadhaar",
]


class SafetyDecision(str, Enum):
    """Safety decision for an action."""

    SAFE = "safe"
    NEEDS_CONFIRMATION = "needs_confirmation"
    BLOCKED = "blocked"


def analyze_safety(
    action_description: str,
    current_url: str = "",
    model_safety_decision: Optional[str] = None,
) -> SafetyDecision:
    """Analyze whether an action requires user confirmation.

    Args:
        action_description: Text description of the action being performed.
        current_url: The current browser URL.
        model_safety_decision: The Computer Use model's own safety_decision field.

    Returns:
        SafetyDecision indicating whether to proceed, confirm, or block.
    """
    if model_safety_decision == "require_confirmation":
        logger.info(
            "Safety gate: model requested confirmation for '%s'",
            action_description,
        )
        return SafetyDecision.NEEDS_CONFIRMATION

    description_lower = action_description.lower()
    for keyword in SENSITIVE_ACTIONS:
        if keyword in description_lower:
            logger.info(
                "Safety gate: keyword '%s' detected in action '%s'",
                keyword,
                action_description,
            )
            return SafetyDecision.NEEDS_CONFIRMATION

    url_lower = current_url.lower()
    for domain_keyword in SENSITIVE_DOMAINS:
        if domain_keyword in url_lower:
            logger.info(
                "Safety gate: sensitive domain '%s' detected in URL '%s'",
                domain_keyword,
                current_url,
            )
            return SafetyDecision.NEEDS_CONFIRMATION

    if any(
        term in description_lower
        for term in ["password", "otp", "pin", "cvv", "secret"]
    ):
        logger.info(
            "Safety gate: credential-related action detected: '%s'",
            action_description,
        )
        return SafetyDecision.NEEDS_CONFIRMATION

    return SafetyDecision.SAFE


def generate_confirmation_prompt(
    action_description: str,
    url: str = "",
    site_name: str = "",
) -> str:
    """Generate a human-readable confirmation prompt for the voice agent.

    Args:
        action_description: What the agent is about to do.
        url: Current page URL.
        site_name: Friendly name of the website.

    Returns:
        A natural-language prompt the voice agent can speak aloud.
    """
    site = site_name or _extract_site_name(url)

    if site:
        return (
            f"I'm about to {action_description} on {site}. "
            f"Should I go ahead?"
        )
    return (
        f"I'm about to {action_description}. "
        f"Should I go ahead?"
    )


def _extract_site_name(url: str) -> str:
    """Extract a human-friendly site name from a URL."""
    if not url:
        return ""
    try:
        from urllib.parse import urlparse

        parsed = urlparse(url)
        hostname = parsed.hostname or ""
        hostname = hostname.replace("www.", "")
        parts = hostname.split(".")
        # Handle country-code TLDs like .co.in, .org.in, .gov.in
        country_slds = {"co", "org", "gov", "ac", "net", "gen", "ind", "nic"}
        if len(parts) >= 3 and parts[-2] in country_slds:
            return parts[-3].upper()
        if len(parts) >= 2:
            return parts[-2].upper()
        return hostname.upper()
    except Exception:
        return ""
