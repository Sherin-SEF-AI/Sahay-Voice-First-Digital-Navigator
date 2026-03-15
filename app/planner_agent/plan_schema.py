"""Pydantic models for task planning and execution."""

from __future__ import annotations

from pydantic import BaseModel, Field


class PlanStep(BaseModel):
    """A single step in a task execution plan."""

    step_number: int
    action: str = Field(
        description="Type of action: navigate, interact, input, wait, checkpoint, extract"
    )
    description: str = Field(description="Human-readable step description")
    visual_target: str = Field(
        default="",
        description="What to look for visually, e.g. 'Blue Login button in header'",
    )
    target_url: str = Field(default="", description="URL for navigate actions")
    input_variable: str = Field(
        default="",
        description="Variable name for input actions, maps to user_inputs",
    )
    expected_result: str = Field(
        default="", description="What should happen after this step"
    )
    needs_user_input: bool = Field(
        default=False, description="Whether this step requires user input"
    )
    is_sensitive: bool = Field(
        default=False,
        description="Whether this step needs Safety Gate confirmation",
    )
    fallback: str = Field(
        default="",
        description="What to do if this step fails",
    )


class TaskPlan(BaseModel):
    """Complete execution plan for a user task."""

    task_summary: str = Field(description="One-line summary of what we're doing")
    discovered_url: str = Field(description="The URL found via search")
    search_queries_used: list[str] = Field(
        default_factory=list,
        description="Google searches that were performed",
    )
    source_confidence: str = Field(
        default="medium",
        description="high, medium, or low confidence in the plan",
    )
    estimated_steps: int = Field(default=0)
    requires_login: bool = Field(default=False)
    requires_payment: bool = Field(default=False)
    requires_otp: bool = Field(default=False)
    user_inputs_needed: list[str] = Field(
        default_factory=list,
        description="List of inputs needed from the user before execution",
    )
    steps: list[PlanStep] = Field(default_factory=list)
    success_indicator: str = Field(
        default="",
        description="How to know the task is complete",
    )
    fallback_search: str = Field(
        default="",
        description="Alternative search query if primary approach fails",
    )


class ReplanRequest(BaseModel):
    """Request to replan after a step failure."""

    original_task: str
    completed_steps: list[str] = Field(default_factory=list)
    failed_step: str
    error_description: str
    current_url: str
    screenshot_description: str = ""
