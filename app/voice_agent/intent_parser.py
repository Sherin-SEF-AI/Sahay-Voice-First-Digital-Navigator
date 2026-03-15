"""Intent parser — Extracts structured task intents from voice transcriptions.

Parses natural-language commands in any language into structured TaskIntent
objects that the browser agent can act on.
"""

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

INTENT_PATTERNS: list[dict] = [
    # Train booking
    {
        "keywords": ["train", "rail", "irctc", "ट्रेन", "रेल", "ट्रैन", "book train", "train book"],
        "action": "book",
        "target_service": "irctc",
        "start_url": "https://www.irctc.co.in",
    },
    # Pension / PF
    {
        "keywords": ["pension", "pf", "provident fund", "epfo", "epf", "पेंशन", "भविष्य निधि"],
        "action": "check_status",
        "target_service": "epfo",
        "start_url": "https://www.epfindia.gov.in",
    },
    # DigiLocker / Aadhaar
    {
        "keywords": ["digilocker", "aadhaar", "aadhar", "document", "डिजिलॉकर", "आधार", "दस्तावेज़"],
        "action": "download",
        "target_service": "digilocker",
        "start_url": "https://www.digilocker.gov.in",
    },
    # UMANG
    {
        "keywords": ["umang", "government service", "सरकारी सेवा", "उमंग"],
        "action": "navigate",
        "target_service": "umang",
        "start_url": "https://web.umang.gov.in",
    },
    # Passport
    {
        "keywords": ["passport", "पासपोर्ट", "passport seva"],
        "action": "book_appointment",
        "target_service": "passport_seva",
        "start_url": "https://www.passportindia.gov.in",
    },
    # Electricity bill
    {
        "keywords": ["electricity", "bijli", "bill", "बिजली", "बिल", "light bill"],
        "action": "pay_bill",
        "target_service": "utility",
        "start_url": "",
    },
    # Bank balance
    {
        "keywords": ["bank", "balance", "account", "बैंक", "बैलेंस", "खाता"],
        "action": "check_balance",
        "target_service": "banking",
        "start_url": "",
    },
    # Gas booking
    {
        "keywords": ["gas", "cylinder", "lpg", "गैस", "सिलेंडर"],
        "action": "book",
        "target_service": "gas_booking",
        "start_url": "",
    },
]


@dataclass
class TaskIntent:
    """Structured representation of a parsed user intent."""

    raw_text: str
    action: str
    target_service: str
    parameters: dict = field(default_factory=dict)
    language: str = "unknown"
    confidence: float = 0.0
    start_url: str = ""


def parse_intent(text: str) -> TaskIntent:
    """Parse a natural-language voice transcription into a structured TaskIntent.

    Performs keyword matching against known service patterns and extracts
    parameters like destinations, dates, and amounts.

    Args:
        text: The transcribed voice input text.

    Returns:
        A TaskIntent with the best matching action and service.
    """
    text_lower = text.lower().strip()

    best_match = None
    best_score = 0

    for pattern in INTENT_PATTERNS:
        score = 0
        for keyword in pattern["keywords"]:
            if keyword.lower() in text_lower:
                score += len(keyword)

        if score > best_score:
            best_score = score
            best_match = pattern

    if best_match and best_score > 0:
        params = _extract_parameters(text_lower, best_match["action"])
        language = _detect_language(text)
        confidence = min(best_score / max(len(text_lower), 1) * 5, 1.0)

        intent = TaskIntent(
            raw_text=text,
            action=best_match["action"],
            target_service=best_match["target_service"],
            parameters=params,
            language=language,
            confidence=confidence,
            start_url=best_match["start_url"],
        )
        logger.info("Parsed intent: %s → %s/%s (%.2f)", text[:50], intent.action, intent.target_service, confidence)
        return intent

    language = _detect_language(text)
    logger.info("No pattern match for: %s — using free-form intent", text[:50])
    return TaskIntent(
        raw_text=text,
        action="navigate",
        target_service="unknown",
        parameters={"query": text},
        language=language,
        confidence=0.3,
    )


def _extract_parameters(text: str, action: str) -> dict:
    """Extract parameters like city names, dates, amounts from text."""
    params: dict = {}

    cities = [
        "delhi", "mumbai", "chennai", "kolkata", "bangalore", "bengaluru",
        "hyderabad", "pune", "jaipur", "lucknow", "ahmedabad", "chandigarh",
        "patna", "bhopal", "thiruvananthapuram", "kochi", "guwahati",
        "दिल्ली", "मुंबई", "चेन्नई", "कोलकाता", "बेंगलुरु", "हैदराबाद",
    ]
    found_cities = [c for c in cities if c in text]
    if len(found_cities) >= 2:
        params["from"] = found_cities[0]
        params["to"] = found_cities[1]
    elif len(found_cities) == 1:
        params["destination"] = found_cities[0]

    amount_match = re.search(r"(\d+)\s*(rupees|rs|₹|रुपये)", text)
    if amount_match:
        params["amount"] = int(amount_match.group(1))

    date_match = re.search(
        r"(\d{1,2})\s*(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|"
        r"january|february|march|april|june|july|august|september|october|november|december)",
        text,
    )
    if date_match:
        params["date"] = date_match.group(0)

    return params


def _detect_language(text: str) -> str:
    """Basic language detection based on script analysis."""
    devanagari_count = sum(1 for c in text if "\u0900" <= c <= "\u097F")
    tamil_count = sum(1 for c in text if "\u0B80" <= c <= "\u0BFF")
    telugu_count = sum(1 for c in text if "\u0C00" <= c <= "\u0C7F")
    malayalam_count = sum(1 for c in text if "\u0D00" <= c <= "\u0D7F")
    bengali_count = sum(1 for c in text if "\u0980" <= c <= "\u09FF")
    kannada_count = sum(1 for c in text if "\u0C80" <= c <= "\u0CFF")

    script_counts = {
        "hi": devanagari_count,
        "ta": tamil_count,
        "te": telugu_count,
        "ml": malayalam_count,
        "bn": bengali_count,
        "kn": kannada_count,
    }

    max_script = max(script_counts, key=script_counts.get)
    if script_counts[max_script] > 0:
        return max_script

    return "en"
