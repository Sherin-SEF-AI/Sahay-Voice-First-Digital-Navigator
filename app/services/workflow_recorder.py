"""Workflow Recording & Replay — learns from successful task executions.

When SAHAY completes a task, the workflow is saved as a replayable template.
Next time a similar task is requested, SAHAY replays the recorded workflow
(much faster), falling back to full AI analysis only when a step fails.
"""

import asyncio
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from . import firestore_service
from .task_journal import GPAActionLog, GPAStep, ActionStatus

logger = logging.getLogger(__name__)

WORKFLOW_COLLECTION = "sahay_workflows"


@dataclass
class RecordedStep:
    action_type: str
    args: dict = field(default_factory=dict)
    url_pattern: str = ""
    element_context: str = ""
    wait_after_ms: int = 1000
    is_dynamic: bool = False
    variable_name: str = ""

    def to_dict(self) -> dict:
        return {
            "action_type": self.action_type,
            "args": self.args,
            "url_pattern": self.url_pattern,
            "element_context": self.element_context,
            "wait_after_ms": self.wait_after_ms,
            "is_dynamic": self.is_dynamic,
            "variable_name": self.variable_name,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "RecordedStep":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class RecordedWorkflow:
    id: str = ""
    name: str = ""
    description: str = ""
    target_url: str = ""
    steps: list[RecordedStep] = field(default_factory=list)
    variables: list[str] = field(default_factory=list)
    success_indicator: str = ""
    created_at: float = 0.0
    last_used_at: float = 0.0
    use_count: int = 0
    success_rate: float = 1.0
    avg_duration_ms: int = 0
    keywords: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "target_url": self.target_url,
            "steps": [s.to_dict() for s in self.steps],
            "variables": self.variables,
            "success_indicator": self.success_indicator,
            "created_at": self.created_at,
            "last_used_at": self.last_used_at,
            "use_count": self.use_count,
            "success_rate": self.success_rate,
            "avg_duration_ms": self.avg_duration_ms,
            "keywords": self.keywords,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "RecordedWorkflow":
        steps_data = d.pop("steps", [])
        # Filter to only known fields
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        wf = cls(**known)
        wf.steps = [RecordedStep.from_dict(s) for s in steps_data]
        return wf


class WorkflowRecorder:
    """Records successful task executions as replayable workflows."""

    DYNAMIC_INDICATORS = [
        "otp",
        "password",
        "phone",
        "mobile",
        "captcha",
        "date",
        "amount",
        "name",
        "email",
        "address",
        "aadhar",
        "aadhaar",
        "pan",
    ]

    def __init__(self):
        self._cache: dict[str, RecordedWorkflow] = {}

    async def record_from_gpa_log(
        self, gpa_log: GPAActionLog, task_description: str
    ) -> Optional[RecordedWorkflow]:
        """Convert a successful GPAActionLog into a RecordedWorkflow."""
        successful_steps = [
            s for s in gpa_log.steps if s.status == ActionStatus.SUCCESS
        ]
        if len(successful_steps) < 2:
            return None

        steps = []
        for gpa_step in successful_steps:
            recorded = RecordedStep(
                action_type=gpa_step.action_type,
                args=self._extract_args(gpa_step),
                url_pattern=self._url_to_pattern(gpa_step.url),
                element_context=gpa_step.element_description,
                wait_after_ms=min(gpa_step.duration_ms or 1000, 5000),
                is_dynamic=self._is_dynamic_step(gpa_step),
                variable_name=(
                    self._infer_variable(gpa_step)
                    if self._is_dynamic_step(gpa_step)
                    else ""
                ),
            )
            steps.append(recorded)

        keywords = self._extract_keywords(task_description)
        workflow = RecordedWorkflow(
            id=str(uuid.uuid4())[:12],
            name=self._generate_workflow_name(task_description),
            description=task_description,
            target_url=gpa_log.steps[0].url if gpa_log.steps else "",
            steps=steps,
            variables=self._extract_variables(steps),
            success_indicator=self._infer_success_indicator(gpa_log),
            created_at=time.time(),
            last_used_at=time.time(),
            keywords=keywords,
        )

        await self._save_to_firestore(workflow)
        self._cache[workflow.id] = workflow
        logger.info(
            "Workflow recorded: %s (%d steps, %d variables)",
            workflow.name,
            len(steps),
            len(workflow.variables),
        )
        return workflow

    async def find_matching_workflow(
        self, task_description: str
    ) -> Optional[RecordedWorkflow]:
        """Find a previously recorded workflow that matches the task."""
        keywords = self._extract_keywords(task_description)
        if not keywords:
            return None

        best_match: Optional[RecordedWorkflow] = None
        best_score = 0.0

        # Check cache first
        for wf in self._cache.values():
            score = self._match_score(keywords, wf.keywords)
            if score > best_score and wf.success_rate >= 0.7:
                best_score = score
                best_match = wf

        # Also check Firestore
        try:
            client = firestore_service._get_client()
            if client:
                query = client.collection(WORKFLOW_COLLECTION).limit(20)
                docs = query.stream()
                for doc in docs:
                    data = doc.to_dict()
                    wf = RecordedWorkflow.from_dict(data)
                    score = self._match_score(keywords, wf.keywords)
                    if score > best_score and wf.success_rate >= 0.7:
                        best_score = score
                        best_match = wf
                        self._cache[wf.id] = wf
        except Exception as e:
            logger.debug("Firestore workflow lookup failed: %s", e)

        if best_match and best_score >= 0.5:
            logger.info(
                "Found matching workflow: %s (score=%.2f)", best_match.name, best_score
            )
            return best_match

        return None

    async def update_workflow_stats(
        self, workflow: RecordedWorkflow, success: bool, duration_ms: int
    ):
        """Update usage stats after a replay attempt."""
        workflow.use_count += 1
        workflow.last_used_at = time.time()

        # Rolling success rate
        total = workflow.use_count
        prev_successes = int(workflow.success_rate * (total - 1))
        new_successes = prev_successes + (1 if success else 0)
        workflow.success_rate = new_successes / total if total > 0 else 0.0

        # Rolling average duration
        if workflow.avg_duration_ms > 0:
            workflow.avg_duration_ms = (
                workflow.avg_duration_ms + duration_ms
            ) // 2
        else:
            workflow.avg_duration_ms = duration_ms

        await self._save_to_firestore(workflow)

    def _extract_args(self, step: GPAStep) -> dict:
        """Extract replayable arguments from a GPA step."""
        detail = step.action_detail
        args: dict[str, Any] = {"raw_detail": detail}
        if step.url:
            args["url"] = step.url
        return args

    def _url_to_pattern(self, url: str) -> str:
        """Convert a URL to a regex pattern for matching."""
        if not url:
            return ""
        # Remove query params and fragments, keep domain + path
        clean = re.sub(r"[?#].*$", "", url)
        # Escape for regex but keep path structure
        return re.escape(clean).replace(r"\/", "/")

    def _is_dynamic_step(self, step: GPAStep) -> bool:
        """Detect if a step involves dynamic/user-specific data."""
        text = (step.action_detail + " " + step.element_description).lower()
        return any(ind in text for ind in self.DYNAMIC_INDICATORS)

    def _infer_variable(self, step: GPAStep) -> str:
        """Infer the variable name for a dynamic step."""
        text = (step.action_detail + " " + step.element_description).lower()
        if "otp" in text:
            return "otp"
        if "phone" in text or "mobile" in text:
            return "phone_number"
        if "password" in text:
            return "password"
        if "email" in text:
            return "email"
        if "name" in text:
            return "name"
        if "aadhar" in text or "aadhaar" in text:
            return "aadhaar"
        if "pan" in text:
            return "pan_number"
        if "date" in text:
            return "date"
        if "amount" in text:
            return "amount"
        return "user_input"

    def _extract_variables(self, steps: list[RecordedStep]) -> list[str]:
        """Get list of unique variable names from dynamic steps."""
        return list(
            {s.variable_name for s in steps if s.is_dynamic and s.variable_name}
        )

    def _generate_workflow_name(self, description: str) -> str:
        """Generate a short workflow name from the task description."""
        # Take first 50 chars, capitalize
        name = description[:50].strip()
        if len(description) > 50:
            name += "..."
        return name

    def _infer_success_indicator(self, gpa_log: GPAActionLog) -> str:
        """Infer how to detect task completion."""
        last_success = None
        for step in reversed(gpa_log.steps):
            if step.status == ActionStatus.SUCCESS:
                last_success = step
                break
        if last_success:
            return f"Last step succeeded: {last_success.element_description}"
        return "All steps completed"

    def _extract_keywords(self, text: str) -> list[str]:
        """Extract searchable keywords from a task description."""
        stop_words = {
            "the",
            "a",
            "an",
            "is",
            "are",
            "was",
            "were",
            "be",
            "been",
            "to",
            "of",
            "and",
            "or",
            "in",
            "on",
            "at",
            "for",
            "with",
            "from",
            "by",
            "my",
            "me",
            "i",
            "you",
            "your",
            "please",
            "help",
            "want",
            "need",
            "can",
            "do",
            "it",
            "this",
            "that",
            "how",
            "what",
        }
        words = re.findall(r"[a-zA-Z]+", text.lower())
        return [w for w in words if w not in stop_words and len(w) > 2]

    def _match_score(self, query_kw: list[str], workflow_kw: list[str]) -> float:
        """Calculate keyword overlap score between query and workflow."""
        if not query_kw or not workflow_kw:
            return 0.0
        query_set = set(query_kw)
        wf_set = set(workflow_kw)
        overlap = query_set & wf_set
        return len(overlap) / max(len(query_set), 1)

    async def _save_to_firestore(self, workflow: RecordedWorkflow):
        """Save workflow to Firestore."""
        try:
            client = firestore_service._get_client()
            if client:
                client.collection(WORKFLOW_COLLECTION).document(workflow.id).set(
                    workflow.to_dict()
                )
        except Exception as e:
            logger.warning("Failed to save workflow to Firestore: %s", e)
