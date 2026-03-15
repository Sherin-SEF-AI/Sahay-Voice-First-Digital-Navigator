"""SAHAY Planner Agent — uses Google Search grounding to research tasks and create plans.

This agent uses gemini-2.5-flash with the google_search tool to:
1. Research the correct website for a given task
2. Understand the current workflow/steps
3. Create a structured execution plan for the Browser Agent
4. Replan on failure from the current state
"""

import json
import logging
from typing import Optional

from google import genai
from google.genai import types

from ..config import settings
from .plan_schema import TaskPlan, PlanStep, ReplanRequest

logger = logging.getLogger(__name__)


def _extract_json(text: str) -> Optional[dict]:
    """Extract JSON object from free-text response.

    Handles markdown code blocks, surrounding prose, etc.
    """
    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try extracting from markdown code block
    if "```" in text:
        for block in text.split("```"):
            block = block.strip()
            if block.startswith("json"):
                block = block[4:].strip()
            if block.startswith("{"):
                try:
                    return json.loads(block)
                except json.JSONDecodeError:
                    continue

    # Try finding JSON object in the text
    start = text.find("{")
    if start != -1:
        # Find matching closing brace
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        break

    return None

# Planner model — needs Google Search grounding support
PLANNER_MODEL = "gemini-2.5-flash"

PLANNER_SYSTEM_INSTRUCTION = """You are SAHAY's intelligent task planner. Create precise, minimal execution plans for a browser automation agent.

PLANNING PRINCIPLES:
1. RESEARCH: Use Google Search to find the exact URL. Search "[site name] official website" and "how to [task] on [site] 2026".
2. MINIMAL STEPS: Only include steps that require USER ACTION. Skip obvious things like "wait for page to load".
3. DIRECT URLs: Find the deepest possible URL. Instead of homepage → navigate → click, find the direct URL to the right page.
   Example: Instead of "Go to amazon.in, click search" → use "https://www.amazon.in/s?k=wireless+earbuds"
4. VISUAL DESCRIPTIONS: Describe what the agent will SEE, not HTML/CSS. "Large blue Search button" not "button.search-btn".
5. SMART SHORTCUTS:
   - For search: append search query to URL if possible (e.g., amazon.in/s?k=query, en.wikipedia.org/wiki/Topic)
   - For known pages: go directly to the subpage, skip homepage navigation
   - For info lookup: if Google Search already has the answer, set discovered_url="" and put answer in task_summary

RESPONSE FORMAT — ONLY valid JSON:
{
  "task_summary": "What we'll do in one line",
  "discovered_url": "https://most-direct-url-possible",
  "search_queries_used": ["queries used"],
  "source_confidence": "high|medium|low",
  "estimated_steps": 3,
  "requires_login": false,
  "requires_payment": false,
  "requires_otp": false,
  "user_inputs_needed": [],
  "steps": [
    {
      "step_number": 1,
      "action": "navigate|interact|input|extract",
      "description": "Clear action description",
      "visual_target": "What it looks like on screen",
      "target_url": "",
      "input_variable": "",
      "expected_result": "What should happen",
      "needs_user_input": false,
      "is_sensitive": false,
      "fallback": "Alternative if this fails"
    }
  ],
  "success_indicator": "How to know task is done",
  "fallback_search": "backup search query"
}

EXAMPLES OF GOOD PLANS:
- "Search earbuds on Amazon" → discovered_url: "https://www.amazon.in/s?k=wireless+earbuds+under+1000"  (1 step: extract results)
- "Taj Mahal on Wikipedia" → discovered_url: "https://en.wikipedia.org/wiki/Taj_Mahal" (1 step: read and summarize)
- "Weather in Delhi" → discovered_url: "" task_summary: "Current weather in Delhi is 32°C, partly cloudy" (0 steps — answer from search)
- "Pay KSEB bill" → discovered_url: "https://wss.kseb.in/selfservices/quickpay" (3 steps: enter number, verify, pay)

BAD PLANS (avoid):
- 10+ steps for a simple search
- "Go to homepage" then "Find search bar" then "Type query" — just use the search URL directly
- Steps that say "wait" or "observe" — the agent does this automatically
"""

REPLAN_INSTRUCTION = """The browser agent was executing a task but encountered a failure. Analyze the situation and create a CONTINUATION plan from the current state — do NOT restart from scratch.

CURRENT STATE:
- Original task: {original_task}
- Steps completed: {completed_steps}
- Failed step: {failed_step}
- Error: {error_description}
- Current URL: {current_url}
- What's on screen: {screenshot_description}

Research via Google Search if needed to find an alternative approach. Create a new plan that continues from the current state.

RESPOND WITH ONLY valid JSON matching the TaskPlan schema."""


def _get_planner_client() -> genai.Client:
    """Get a GenAI client for the planner.

    Google Search grounding REQUIRES Vertex AI (OAuth2/ADC).
    API keys are NOT supported. So we always use Vertex AI for the planner,
    even if an API key is configured (that's used for Computer Use only).
    """
    # Force Vertex AI for planner — google_search grounding needs it
    if settings.google_cloud_project:
        return genai.Client(
            vertexai=True,
            project=settings.google_cloud_project,
            location=settings.google_cloud_location,
        )
    # Fallback: try default credentials
    return genai.Client(vertexai=True)


async def plan_task(task_description: str) -> Optional[TaskPlan]:
    """Research a task via Google Search and create an execution plan.

    Args:
        task_description: Natural language description of what the user wants to do.

    Returns:
        TaskPlan with discovered URL and step-by-step instructions,
        or None if planning failed.
    """
    logger.info("Planner: researching task: %s", task_description[:80])

    client = _get_planner_client()

    try:
        response = await client.aio.models.generate_content(
            model=PLANNER_MODEL,
            contents=f"Plan this task for a browser automation agent: {task_description}",
            config=types.GenerateContentConfig(
                system_instruction=PLANNER_SYSTEM_INSTRUCTION,
                tools=[types.Tool(google_search=types.GoogleSearch())],
                # NOTE: response_mime_type NOT supported with google_search tool
                temperature=0.2,
            ),
        )

        if not response.text:
            logger.error("Planner returned empty response")
            return None

        # Extract JSON from free-text response (may have markdown, prose, etc.)
        raw = response.text.strip()
        plan_data = _extract_json(raw)
        if not plan_data:
            logger.error("Planner response had no valid JSON: %s", raw[:200])
            return None

        plan = TaskPlan(**plan_data)

        logger.info(
            "Planner created plan: %s (%d steps, confidence=%s, url=%s)",
            plan.task_summary,
            len(plan.steps),
            plan.source_confidence,
            plan.discovered_url,
        )

        if plan.search_queries_used:
            logger.info(
                "Planner searched: %s", ", ".join(plan.search_queries_used)
            )

        return plan

    except json.JSONDecodeError as e:
        logger.error("Planner returned invalid JSON: %s", e)
        return None
    except Exception as e:
        logger.error("Planner failed: %s", e)
        return None


async def replan_task(request: ReplanRequest) -> Optional[TaskPlan]:
    """Create a continuation plan after a step failure.

    Args:
        request: Details about the failure and current state.

    Returns:
        New TaskPlan continuing from current state, or None if replanning failed.
    """
    logger.info(
        "Planner: replanning after failure at step '%s'", request.failed_step
    )

    client = _get_planner_client()

    prompt = REPLAN_INSTRUCTION.format(
        original_task=request.original_task,
        completed_steps=", ".join(request.completed_steps) or "None",
        failed_step=request.failed_step,
        error_description=request.error_description,
        current_url=request.current_url,
        screenshot_description=request.screenshot_description or "Unknown",
    )

    try:
        response = await client.aio.models.generate_content(
            model=PLANNER_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=PLANNER_SYSTEM_INSTRUCTION,
                tools=[types.Tool(google_search=types.GoogleSearch())],
                temperature=0.3,
            ),
        )

        if not response.text:
            logger.error("Replanner returned empty response")
            return None

        raw = response.text.strip()
        plan_data = _extract_json(raw)
        if not plan_data:
            logger.error("Replanner response had no valid JSON: %s", raw[:200])
            return None

        plan = TaskPlan(**plan_data)

        logger.info(
            "Replanner created continuation plan: %d steps from current state",
            len(plan.steps),
        )
        return plan

    except Exception as e:
        logger.error("Replanner failed: %s", e)
        return None
