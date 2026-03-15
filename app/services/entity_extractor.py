"""Structured Entity Extraction — captures important data from pages.

During navigation, SAHAY extracts structured data (booking IDs, amounts,
statuses, confirmation messages) from page text and DOM snapshots so the
voice agent can speak results naturally.
"""

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class ExtractedEntity:
    entity_type: str
    value: str
    confidence: float
    source_url: str = ""
    context: str = ""

    def to_dict(self) -> dict:
        return {
            "type": self.entity_type,
            "value": self.value,
            "confidence": self.confidence,
            "source_url": self.source_url,
            "context": self.context,
        }


# Regex patterns for common Indian entities
ENTITY_PATTERNS: dict[str, list[re.Pattern]] = {
    "pnr_number": [
        re.compile(r"\bPNR\s*[:#]?\s*(\d{10})\b", re.IGNORECASE),
        re.compile(r"\b(\d{10})\b.*PNR", re.IGNORECASE),
    ],
    "reference_number": [
        re.compile(
            r"(?:Ref|Reference|Txn|Transaction)\s*(?:No|Number|ID|#)[.:\s]*([A-Z0-9-]{6,20})",
            re.IGNORECASE,
        ),
    ],
    "booking_id": [
        re.compile(
            r"(?:Booking|Order|Reservation)\s*(?:ID|No|Number|#)[.:\s]*([A-Z0-9-]{6,20})",
            re.IGNORECASE,
        ),
    ],
    "amount": [
        re.compile(
            r"(?:Rs\.?|INR|₹)\s*([\d,]+\.?\d*)", re.IGNORECASE
        ),
        re.compile(
            r"(?:Total|Amount|Price|Fee|Fare|Cost)[:\s]*(?:Rs\.?|INR|₹)?\s*([\d,]+\.?\d*)",
            re.IGNORECASE,
        ),
    ],
    "status": [
        re.compile(
            r"(?:Status|State)[:\s]*(Confirmed|Pending|Active|Cancelled|Rejected|Approved|Processing|Completed|Failed|Successful|Waiting|Booked|Reserved)",
            re.IGNORECASE,
        ),
    ],
    "date": [
        re.compile(
            r"\b(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\b"
        ),
        re.compile(
            r"\b(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\s+\d{4})\b",
            re.IGNORECASE,
        ),
    ],
    "phone": [
        re.compile(r"\b(\+91[\s-]?\d{10})\b"),
        re.compile(r"\b((?:\+91|91|0)?[6-9]\d{9})\b"),
    ],
    "email": [
        re.compile(r"\b([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})\b"),
    ],
    "confirmation_message": [
        re.compile(
            r"((?:successfully|confirmed|completed|booked|approved|paid|submitted|registered|downloaded).{0,100})",
            re.IGNORECASE,
        ),
    ],
    "error_message": [
        re.compile(
            r"((?:error|failed|invalid|expired|denied|rejected|unauthorized|timeout).{0,100})",
            re.IGNORECASE,
        ),
    ],
}


class EntityExtractor:
    """Extract structured entities from page text during navigation."""

    async def extract_from_text(
        self, page_text: str, source_url: str = "", context: str = ""
    ) -> list[ExtractedEntity]:
        """Extract entities using regex patterns on page text.

        Args:
            page_text: Text content from the page.
            source_url: URL where the text was found.
            context: Additional context about what we're looking for.

        Returns:
            List of extracted entities with confidence scores.
        """
        entities: list[ExtractedEntity] = []
        seen_values: set[str] = set()

        for entity_type, patterns in ENTITY_PATTERNS.items():
            for pattern in patterns:
                for match in pattern.finditer(page_text):
                    value = match.group(1) if match.lastindex else match.group(0)
                    value = value.strip()

                    if not value or value in seen_values:
                        continue
                    seen_values.add(value)

                    # Get surrounding context
                    start = max(0, match.start() - 40)
                    end = min(len(page_text), match.end() + 40)
                    surrounding = page_text[start:end].strip()

                    confidence = self._calculate_confidence(
                        entity_type, value, surrounding, context
                    )

                    entities.append(
                        ExtractedEntity(
                            entity_type=entity_type,
                            value=value,
                            confidence=confidence,
                            source_url=source_url,
                            context=surrounding,
                        )
                    )

        # Sort by confidence descending
        entities.sort(key=lambda e: e.confidence, reverse=True)
        return entities

    async def extract_task_result(
        self, page_text: str, task_description: str, source_url: str = ""
    ) -> dict[str, Any]:
        """Extract the final result of a completed task.

        Returns a dict with key result information the voice agent can speak.
        """
        entities = await self.extract_from_text(
            page_text, source_url=source_url, context=task_description
        )

        result: dict[str, Any] = {}
        for entity in entities:
            if entity.confidence >= 0.5:
                key = entity.entity_type
                if key not in result:
                    result[key] = entity.value
                elif isinstance(result[key], list):
                    result[key].append(entity.value)
                else:
                    result[key] = [result[key], entity.value]

        # Add a natural language summary
        if result:
            result["_summary"] = self._build_summary(result, task_description)

        return result

    def format_for_voice(self, result: dict[str, Any]) -> str:
        """Format extracted results for the voice agent to speak."""
        if not result:
            return "I completed the task but couldn't extract specific details."

        summary = result.get("_summary", "")
        if summary:
            return summary

        parts = []
        for key, value in result.items():
            if key.startswith("_"):
                continue
            label = key.replace("_", " ").title()
            if isinstance(value, list):
                parts.append(f"{label}: {', '.join(str(v) for v in value)}")
            else:
                parts.append(f"{label}: {value}")

        return "Here's what I found: " + ". ".join(parts) + "."

    def _calculate_confidence(
        self, entity_type: str, value: str, surrounding: str, context: str
    ) -> float:
        """Calculate confidence score for an extracted entity."""
        score = 0.6  # Base confidence for regex match

        # Boost if entity type is mentioned in the context
        if entity_type.replace("_", " ") in context.lower():
            score += 0.2

        # Boost for known high-confidence patterns
        if entity_type == "pnr_number" and len(value) == 10 and value.isdigit():
            score += 0.2
        elif entity_type == "amount" and re.match(r"[\d,]+\.?\d*$", value):
            score += 0.15
        elif entity_type == "email" and "@" in value:
            score += 0.2
        elif entity_type == "confirmation_message":
            score += 0.1
        elif entity_type == "status":
            score += 0.15

        # Penalize very short or very long values
        if len(value) < 3:
            score -= 0.2
        if len(value) > 100:
            score -= 0.1

        return min(max(score, 0.0), 1.0)

    def _build_summary(self, result: dict[str, Any], task_description: str) -> str:
        """Build a natural language summary of extracted results."""
        parts = []

        if "confirmation_message" in result:
            parts.append(str(result["confirmation_message"]))

        if "status" in result:
            parts.append(f"Status: {result['status']}")

        if "pnr_number" in result:
            parts.append(f"PNR number is {result['pnr_number']}")

        if "booking_id" in result:
            parts.append(f"Booking ID: {result['booking_id']}")

        if "reference_number" in result:
            parts.append(f"Reference number: {result['reference_number']}")

        if "amount" in result:
            parts.append(f"Amount: {result['amount']} rupees")

        if "date" in result:
            parts.append(f"Date: {result['date']}")

        if not parts:
            return ""

        return ". ".join(parts) + "."
