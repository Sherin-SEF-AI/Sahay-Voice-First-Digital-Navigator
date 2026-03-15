"""SAHAY Orchestrator — coordinates Planner, Browser, and Voice agents.

Flow:
1. Voice Agent captures user intent
2. Orchestrator calls Planner to research + create plan
3. Planner uses Google Search grounding to discover URLs and steps
4. Browser Agent executes plan step by step
5. On failure → Planner replans from current state
6. On completion → results spoken back via Voice Agent
"""

import asyncio
import logging
import time
from typing import Optional, Callable, Awaitable

from .planner_agent.agent import plan_task, replan_task
from .planner_agent.plan_schema import TaskPlan, PlanStep, ReplanRequest

logger = logging.getLogger(__name__)

# Max replanning attempts before giving up
MAX_REPLAN_ATTEMPTS = 2


class TaskOrchestrator:
    """Coordinates the three-agent system for task execution."""

    def __init__(
        self,
        broadcast_fn: Callable[[dict], Awaitable[None]],
    ):
        """Initialize orchestrator.

        Args:
            broadcast_fn: Async function to broadcast messages to frontend clients.
        """
        self._broadcast = broadcast_fn
        self._current_plan: Optional[TaskPlan] = None
        self._current_step_index: int = 0
        self._completed_steps: list[str] = []
        self._replan_count: int = 0

    async def plan(self, task_description: str) -> Optional[TaskPlan]:
        """Phase 1: Research and plan the task.

        Calls the Planner Agent which uses Google Search grounding
        to discover the correct website and create a step-by-step plan.
        """
        # Broadcast planning status
        await self._broadcast({
            "type": "gpa_step",
            "step": {
                "phase": "planning",
                "status": "in_progress",
                "description": f"Researching: {task_description[:60]}...",
                "detail": "Using Google Search to find the right website and steps...",
            },
        })

        plan = await plan_task(task_description)

        if not plan:
            await self._broadcast({
                "type": "gpa_step",
                "step": {
                    "phase": "planning",
                    "status": "failed",
                    "description": "Could not create a plan for this task",
                    "detail": "The planner was unable to research this task. Try rephrasing.",
                },
            })
            return None

        self._current_plan = plan
        self._current_step_index = 0
        self._completed_steps = []
        self._replan_count = 0

        # Broadcast the plan to frontend
        await self._broadcast({
            "type": "gpa_step",
            "step": {
                "phase": "planning",
                "status": "completed",
                "description": plan.task_summary,
                "detail": f"Found: {plan.discovered_url}",
                "confidence": plan.source_confidence,
                "total_steps": len(plan.steps),
                "requires_login": plan.requires_login,
                "requires_payment": plan.requires_payment,
                "user_inputs_needed": plan.user_inputs_needed,
            },
        })

        # Broadcast each plan step as preview
        await self._broadcast({
            "type": "plan_preview",
            "plan": {
                "task_summary": plan.task_summary,
                "discovered_url": plan.discovered_url,
                "confidence": plan.source_confidence,
                "steps": [
                    {
                        "step_number": s.step_number,
                        "description": s.description,
                        "action": s.action,
                        "needs_user_input": s.needs_user_input,
                        "is_sensitive": s.is_sensitive,
                    }
                    for s in plan.steps
                ],
                "user_inputs_needed": plan.user_inputs_needed,
            },
        })

        return plan

    def get_browser_prompt(self, plan: TaskPlan, user_inputs: Optional[dict] = None) -> str:
        """Convert a TaskPlan into a prompt for the Browser Agent.

        This creates a detailed, step-by-step instruction that the Computer Use
        model can follow visually.
        """
        lines = [
            f"TASK: {plan.task_summary}",
            f"TARGET WEBSITE: {plan.discovered_url}",
            f"CONFIDENCE: {plan.source_confidence}",
            "",
            "EXECUTE THESE STEPS IN ORDER:",
            "",
        ]

        for step in plan.steps:
            step_line = f"Step {step.step_number}: {step.description}"
            if step.action == "navigate":
                step_line += f"\n  → Navigate to: {step.target_url}"
            if step.visual_target:
                step_line += f"\n  → Look for: {step.visual_target}"
            if step.input_variable and user_inputs and step.input_variable in user_inputs:
                step_line += f"\n  → Enter: {user_inputs[step.input_variable]}"
            elif step.needs_user_input:
                step_line += f"\n  → ASK USER: Need '{step.input_variable}' — say NEED INPUT: {step.input_variable}"
            if step.is_sensitive:
                step_line += "\n  → ⚠️ SENSITIVE ACTION — confirm before proceeding"
            if step.expected_result:
                step_line += f"\n  → Expected: {step.expected_result}"
            if step.fallback:
                step_line += f"\n  → If fails: {step.fallback}"
            lines.append(step_line)
            lines.append("")

        lines.extend([
            f"SUCCESS: {plan.success_indicator}",
            "",
            "HOW TO EXECUTE:",
            "- Look at the screenshot. Click what you see. Act fast.",
            "- For buttons/links: click_at(x,y) — look at the screenshot, find the element, click its coordinates.",
            "- For form fields: click the field with click_at(x,y), then type with type_text_at(x,y, text).",
            "- For reading results: call get_page_text() ONCE, extract the answer, report TASK COMPLETE.",
            "- NEVER use :contains() selectors. Use click_at(x,y) instead.",
            "",
            "COMPLETION:",
            "- As SOON as you have the information or completed the action → TASK COMPLETE: [specific data]",
            "- Include numbers, prices, names, dates in your completion message.",
            "- If the page already shows what the user asked for → read it and report immediately.",
            "- If you need user info → NEED INPUT: [what]",
            "- If CAPTCHA → NEED CAPTCHA: [describe]",
            "- If impossible → TASK FAILED: [why]",
            "- NEVER stop without saying TASK COMPLETE or TASK FAILED.",
        ])

        return "\n".join(lines)

    async def handle_step_failure(
        self,
        original_task: str,
        failed_step_desc: str,
        error: str,
        current_url: str,
        screenshot_desc: str = "",
    ) -> Optional[TaskPlan]:
        """Handle a step failure by asking the Planner to replan.

        Returns a new continuation plan, or None if max retries exceeded.
        """
        self._replan_count += 1

        if self._replan_count > MAX_REPLAN_ATTEMPTS:
            logger.warning("Max replan attempts (%d) exceeded", MAX_REPLAN_ATTEMPTS)
            await self._broadcast({
                "type": "gpa_step",
                "step": {
                    "phase": "replanning",
                    "status": "failed",
                    "description": "Cannot recover — too many retries",
                    "detail": f"Failed at: {failed_step_desc}. Error: {error}",
                },
            })
            return None

        await self._broadcast({
            "type": "gpa_step",
            "step": {
                "phase": "replanning",
                "status": "in_progress",
                "description": f"Step failed — replanning (attempt {self._replan_count}/{MAX_REPLAN_ATTEMPTS})",
                "detail": f"Error: {error}",
            },
        })

        request = ReplanRequest(
            original_task=original_task,
            completed_steps=self._completed_steps,
            failed_step=failed_step_desc,
            error_description=error,
            current_url=current_url,
            screenshot_description=screenshot_desc,
        )

        new_plan = await replan_task(request)

        if new_plan:
            self._current_plan = new_plan
            self._current_step_index = 0

            await self._broadcast({
                "type": "gpa_step",
                "step": {
                    "phase": "replanning",
                    "status": "completed",
                    "description": f"New plan: {new_plan.task_summary}",
                    "detail": f"Continuing with {len(new_plan.steps)} steps",
                },
            })

        return new_plan

    def mark_step_completed(self, description: str) -> None:
        """Mark a step as completed for tracking."""
        self._completed_steps.append(description)
        self._current_step_index += 1

    @property
    def current_plan(self) -> Optional[TaskPlan]:
        """Get the current execution plan."""
        return self._current_plan
