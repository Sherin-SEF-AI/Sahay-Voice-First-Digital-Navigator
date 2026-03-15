"""Task Templates — Pre-built knowledge for common Indian web services.

Provides the Browser Agent with contextual hints about how common
Indian government portals and services work. These are NOT hardcoded
scripts — they give the agent better priming so it can navigate
visually more effectively.
"""

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ServiceTemplate:
    """Template describing a known service the browser agent may navigate."""

    name: str
    display_name: str
    start_url: str
    description: str
    common_steps: str
    known_patterns: list[str] = field(default_factory=list)
    tips: list[str] = field(default_factory=list)


TEMPLATES: dict[str, ServiceTemplate] = {
    "irctc": ServiceTemplate(
        name="irctc",
        display_name="IRCTC (Indian Railways)",
        start_url="https://www.irctc.co.in/nget/train-search",
        description="Indian Railways ticket booking portal for train reservations.",
        common_steps=(
            "1. Navigate to irctc.co.in\n"
            "2. Login with username and password (CAPTCHA required — hand off to user)\n"
            "3. On the booking page, fill From Station, To Station, Journey Date, Class\n"
            "4. Click 'Search' to find trains\n"
            "5. Select a train and class from the results\n"
            "6. Fill passenger details (name, age, gender, berth preference)\n"
            "7. Review booking summary — CONFIRM with user before payment\n"
            "8. Payment page — CONFIRM with user before proceeding"
        ),
        known_patterns=[
            "Login page has CAPTCHA — always hand off",
            "Station names use autocomplete — type first few letters and select",
            "Date picker is a calendar widget — click the date",
            "Class options: SL (Sleeper), 3A (3rd AC), 2A (2nd AC), 1A (1st AC), CC (Chair Car)",
            "After login, there may be a popup/alert — close it",
        ],
        tips=[
            "IRCTC often shows promotional popups — close them",
            "Session expires after 30 minutes of inactivity",
            "CAPTCHA is mandatory — cannot be bypassed",
        ],
    ),
    "digilocker": ServiceTemplate(
        name="digilocker",
        display_name="DigiLocker",
        start_url="https://www.digilocker.gov.in",
        description="Government digital document storage — Aadhaar, PAN, driving license, etc.",
        common_steps=(
            "1. Navigate to digilocker.gov.in\n"
            "2. Click 'Sign In' or 'Sign Up'\n"
            "3. Enter Aadhaar number or mobile number\n"
            "4. Enter OTP sent to registered mobile\n"
            "5. Set security PIN if first time\n"
            "6. Navigate to 'Issued Documents' or 'Pulled Documents'\n"
            "7. Select document type (Aadhaar, PAN, DL, etc.)\n"
            "8. Download or view document"
        ),
        known_patterns=[
            "Login requires Aadhaar number + OTP",
            "Documents are under 'Issued Documents' tab",
            "Some documents need to be 'pulled' from issuing authority first",
            "PDF download button is usually on the document view page",
        ],
        tips=[
            "OTP is sent to Aadhaar-linked mobile number",
            "Some documents require additional verification",
        ],
    ),
    "umang": ServiceTemplate(
        name="umang",
        display_name="UMANG (Unified Mobile App for New-age Governance)",
        start_url="https://web.umang.gov.in",
        description="Unified portal for accessing multiple government services.",
        common_steps=(
            "1. Navigate to web.umang.gov.in\n"
            "2. Login with mobile number + OTP\n"
            "3. Search for the desired service (e.g., 'EPFO', 'Passport')\n"
            "4. Select the service and follow its specific flow\n"
            "5. Each service has its own verification and form flow"
        ),
        known_patterns=[
            "Homepage has a search bar for finding services",
            "Services are categorized by department",
            "Each service redirects to a sub-portal within UMANG",
            "Login is phone number + OTP based",
        ],
        tips=[
            "UMANG aggregates many services — search is the fastest way",
            "Some services may redirect to external portals",
        ],
    ),
    "passport_seva": ServiceTemplate(
        name="passport_seva",
        display_name="Passport Seva",
        start_url="https://www.passportindia.gov.in",
        description="Indian passport application and appointment booking portal.",
        common_steps=(
            "1. Navigate to passportindia.gov.in\n"
            "2. Click 'Existing User Login' or 'Register Now'\n"
            "3. Login with email + password\n"
            "4. Select 'Apply for Fresh Passport' or 'Reissue'\n"
            "5. Fill the multi-page application form\n"
            "6. Upload documents as required\n"
            "7. Schedule appointment at Passport Seva Kendra\n"
            "8. Pay fees — CONFIRM with user"
        ),
        known_patterns=[
            "Registration requires email verification",
            "Application form has multiple pages — save progress at each step",
            "Appointment slots are limited — may need to check multiple dates",
            "Payment can be done via SBI/other banks",
        ],
        tips=[
            "Application form is very long — be patient",
            "Document upload has specific format requirements",
        ],
    ),
    "epfo": ServiceTemplate(
        name="epfo",
        display_name="EPFO (Employees' Provident Fund Organization)",
        start_url="https://www.epfindia.gov.in",
        description="Check PF balance, pension status, and EPF-related services.",
        common_steps=(
            "1. Navigate to epfindia.gov.in\n"
            "2. Click on 'For Employees' → 'Member Passbook'\n"
            "3. Or go to unifiedportal-mem.epfindia.gov.in for unified portal\n"
            "4. Login with UAN (Universal Account Number) + password\n"
            "5. Navigate to 'View' → 'Passbook' to see PF balance\n"
            "6. Or check 'Pension' section for pension status"
        ),
        known_patterns=[
            "UAN is a 12-digit number",
            "CAPTCHA is required on login page",
            "Passbook shows employer and employee contributions",
            "Pension details are under a separate section",
        ],
        tips=[
            "CAPTCHA on EPFO portal — hand off to user",
            "UAN can be found on salary slip or by asking employer",
        ],
    ),
    "utility": ServiceTemplate(
        name="utility",
        display_name="Electricity/Water Bill Payment",
        start_url="",
        description="Utility bill payment — varies by state and provider.",
        common_steps=(
            "1. Identify the state and utility provider\n"
            "2. Navigate to the provider's website\n"
            "3. Enter consumer number / account number\n"
            "4. View current bill amount\n"
            "5. Select payment method\n"
            "6. CONFIRM amount with user before payment\n"
            "7. Complete payment"
        ),
        known_patterns=[
            "Each state has different electricity boards",
            "Consumer number is usually on the physical bill",
            "Some portals accept UPI, others only net banking",
        ],
        tips=[
            "Ask user for their state and provider name",
            "Ask for consumer/account number from their physical bill",
            "Always confirm the bill amount before payment",
        ],
    ),
}


def get_template(service_name: str) -> ServiceTemplate | None:
    """Look up a service template by name.

    Args:
        service_name: The service key (e.g., 'irctc', 'digilocker').

    Returns:
        ServiceTemplate if found, None otherwise.
    """
    return TEMPLATES.get(service_name.lower())


def get_context_hint(service_name: str) -> str:
    """Get a context hint string for the browser agent.

    Args:
        service_name: The service key.

    Returns:
        A formatted hint string, or empty string if no template exists.
    """
    template = get_template(service_name)
    if not template:
        return ""

    lines = [
        f"SERVICE CONTEXT: {template.display_name}",
        f"URL: {template.start_url}",
        f"Description: {template.description}",
        "",
        "Common Steps:",
        template.common_steps,
        "",
        "Known Patterns:",
    ]
    for pattern in template.known_patterns:
        lines.append(f"  - {pattern}")

    if template.tips:
        lines.append("")
        lines.append("Tips:")
        for tip in template.tips:
            lines.append(f"  - {tip}")

    return "\n".join(lines)


def find_service_by_keyword(text: str) -> ServiceTemplate | None:
    """Find a service template by matching keywords in text.

    Args:
        text: User input text to match against service names.

    Returns:
        Best matching ServiceTemplate, or None.
    """
    text_lower = text.lower()
    for key, template in TEMPLATES.items():
        if key in text_lower or template.display_name.lower() in text_lower:
            return template
    return None
