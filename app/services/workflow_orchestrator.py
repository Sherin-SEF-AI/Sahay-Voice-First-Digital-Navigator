"""Multi-Step Workflow Orchestration — decomposes complex tasks.

Some tasks require multiple sub-tasks in sequence (e.g., "Book a train
and download the ticket"). The orchestrator breaks these into sequential
steps, passing data between them.
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from .task_journal import GPAActionLog

logger = logging.getLogger(__name__)


@dataclass
class WorkflowStep:
    step_id: str = ""
    description: str = ""
    start_url: str = ""
    depends_on: list[str] = field(default_factory=list)
    variables_from: dict[str, str] = field(default_factory=dict)
    status: str = "pending"
    result: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "step_id": self.step_id,
            "description": self.description,
            "start_url": self.start_url,
            "depends_on": self.depends_on,
            "variables_from": self.variables_from,
            "status": self.status,
        }


# Known multi-step task patterns with their decompositions
MULTI_STEP_PATTERNS: list[dict[str, Any]] = [
    {
        "patterns": [r"book.*train.*download.*ticket", r"train.*ticket.*download"],
        "steps": [
            {
                "step_id": "search_train",
                "description": "Search for train on IRCTC",
                "start_url": "https://www.irctc.co.in",
            },
            {
                "step_id": "book_train",
                "description": "Book the selected train",
                "depends_on": ["search_train"],
            },
            {
                "step_id": "download_ticket",
                "description": "Download the booking confirmation/ticket",
                "depends_on": ["book_train"],
                "variables_from": {"pnr_number": "book_train.pnr_number"},
            },
        ],
    },
    {
        "patterns": [r"check.*status.*download", r"status.*save"],
        "steps": [
            {
                "step_id": "check_status",
                "description": "Check the status",
            },
            {
                "step_id": "save_result",
                "description": "Save or download the result",
                "depends_on": ["check_status"],
            },
        ],
    },
    {
        "patterns": [r"login.*and.*then", r"sign.*in.*and"],
        "steps": [
            {
                "step_id": "login",
                "description": "Login to the portal",
            },
            {
                "step_id": "main_task",
                "description": "Complete the main task after login",
                "depends_on": ["login"],
            },
        ],
    },
    {
        "patterns": [r"pay.*bill.*download.*receipt", r"payment.*receipt"],
        "steps": [
            {
                "step_id": "pay_bill",
                "description": "Pay the bill",
            },
            {
                "step_id": "download_receipt",
                "description": "Download the payment receipt",
                "depends_on": ["pay_bill"],
                "variables_from": {
                    "transaction_id": "pay_bill.reference_number"
                },
            },
        ],
    },
]


class WorkflowOrchestrator:
    """Orchestrates multi-step workflows that span multiple pages/sites."""

    def decompose_task(self, task_description: str) -> list[WorkflowStep]:
        """Break a complex task into sequential sub-tasks.

        Uses pattern matching against known multi-step task patterns.
        Returns a single-step list for simple tasks.
        """
        task_lower = task_description.lower()

        for pattern_group in MULTI_STEP_PATTERNS:
            for pattern in pattern_group["patterns"]:
                if re.search(pattern, task_lower):
                    steps = []
                    for step_data in pattern_group["steps"]:
                        step = WorkflowStep(
                            step_id=step_data["step_id"],
                            description=step_data.get(
                                "description", task_description
                            ),
                            start_url=step_data.get("start_url", ""),
                            depends_on=step_data.get("depends_on", []),
                            variables_from=step_data.get("variables_from", {}),
                        )
                        steps.append(step)

                    logger.info(
                        "Decomposed task into %d steps: %s",
                        len(steps),
                        [s.step_id for s in steps],
                    )
                    return steps

        # Single-step task (most common case)
        return [
            WorkflowStep(
                step_id="main",
                description=task_description,
            )
        ]

    def is_multi_step(self, task_description: str) -> bool:
        """Check if a task would be decomposed into multiple steps."""
        return len(self.decompose_task(task_description)) > 1

    def get_step_variables(
        self, step: WorkflowStep, previous_results: dict[str, dict]
    ) -> dict[str, str]:
        """Resolve variable references from previous step results."""
        variables: dict[str, str] = {}
        for var_name, source in step.variables_from.items():
            parts = source.split(".", 1)
            if len(parts) == 2:
                source_step_id, entity_name = parts
                if source_step_id in previous_results:
                    value = previous_results[source_step_id].get(entity_name, "")
                    if value:
                        variables[var_name] = str(value)

        return variables

    def get_progress_summary(self, steps: list[WorkflowStep]) -> str:
        """Generate a voice-friendly progress summary."""
        total = len(steps)
        completed = sum(1 for s in steps if s.status == "completed")
        current = next(
            (s for s in steps if s.status == "in_progress"), None
        )

        if completed == total:
            return f"All {total} steps completed successfully."

        parts = [f"Progress: {completed} of {total} steps done."]
        if current:
            parts.append(f"Currently working on: {current.description}")

        remaining = [s for s in steps if s.status == "pending"]
        if remaining:
            parts.append(
                f"Remaining: {', '.join(s.description for s in remaining[:3])}"
            )

        return " ".join(parts)
