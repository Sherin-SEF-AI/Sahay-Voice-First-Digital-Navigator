"""SAHAY FastAPI Server — WebSocket endpoints for voice and screen streaming.

Orchestrates the Voice Agent (Live API) and Browser Agent (Computer Use),
connecting them via WebSockets to the frontend dashboard.

Integrates GPA features: real-time action log, self-healing, workflow
recording/replay, entity extraction, and multi-step orchestration.
"""

import asyncio
import base64
import json
import logging
import re
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, Optional

from dotenv import load_dotenv
load_dotenv()  # Load .env BEFORE any Google SDK imports

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from google.adk.agents.live_request_queue import LiveRequestQueue
from google.adk.runners import Runner
from google.adk.runners import RunConfig
from google.adk.sessions import InMemorySessionService
from google.genai import types

from .config import settings
from .voice_agent.agent import create_voice_agent, get_live_run_config
from .voice_agent.intent_parser import parse_intent
from .browser_agent.agent import create_browser_agent
from .browser_agent.action_executor import ActionExecutor
from .browser_agent.safety_gate import (
    analyze_safety,
    generate_confirmation_prompt,
    SafetyDecision,
)
from .services import firestore_service
from .services.task_journal import TaskJournal, GPAActionLog, ActionStatus
from .services.task_templates import get_context_hint, find_service_by_keyword
from .services.entity_extractor import EntityExtractor
from .services.workflow_recorder import WorkflowRecorder
from .services.workflow_orchestrator import WorkflowOrchestrator
from .services.guardian_service import GuardianService
from .services.upi_service import UPIService
from .services.screenshot_diff import ScreenshotDiffEngine
from .orchestrator import TaskOrchestrator
from .planner_agent.agent import plan_task

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Global state
_browser_agent = None
_browser_computer = None
_browser_lock = asyncio.Lock()
_browser_task_lock = asyncio.Lock()  # Serialize browser tasks — only one at a time
_browser_task_running = False
_last_browser_task_desc = ""  # Prevent duplicate task submissions
_last_browser_task_time = 0.0
_screen_clients: list[WebSocket] = []
_voice_clients: list[WebSocket] = []
_active_journals: dict[str, TaskJournal] = {}

# Max steps before force-stopping a browser task
MAX_BROWSER_STEPS = 999  # Unlimited — only stops on TASK COMPLETE, TASK FAILED, or user Stop button

# GPA services (singletons)
_entity_extractor = EntityExtractor()
_workflow_recorder = WorkflowRecorder()
_workflow_orchestrator = WorkflowOrchestrator()

# Guardian + UPI services
_guardian_service = GuardianService()
_upi_service = UPIService()

# Form Memory — stores extracted user info for auto-filling future forms
_form_memory: dict = {}

# Safety gate state — used for voice-based confirmation before sensitive actions
_safety_gate_event: Optional[asyncio.Event] = None
_safety_gate_response: bool = False  # True = approved, False = denied
SAFETY_GATE_TIMEOUT = 30  # seconds to wait for user response

# Global reference to the active Live API queue — allows safety gate to inject speech
_active_live_queue: Optional[LiveRequestQueue] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan — initialize and cleanup resources."""
    logger.info("SAHAY starting up on port %d", settings.app_port)
    yield
    logger.info("SAHAY shutting down")
    global _browser_computer
    if _browser_computer:
        await _browser_computer.close()
        _browser_computer = None


app = FastAPI(
    title="SAHAY (सहाय)",
    description="Voice-controlled AI agent with GPA engine for navigating web applications",
    version="2.0.0",
    lifespan=lifespan,
)


async def _safety_gate_callback(action_description: str, url: str) -> bool:
    """Safety gate callback — pauses browser action and asks user for voice confirmation.

    Broadcasts a confirmation request to all voice and screen clients,
    then waits for the user to approve or deny via voice or button click.
    Returns True to proceed, False to cancel.
    """
    global _safety_gate_event, _safety_gate_response

    # Generate the confirmation prompt
    prompt = generate_confirmation_prompt(action_description, url)

    logger.info("Safety gate activated: %s", prompt)

    # Create a fresh event for this confirmation
    _safety_gate_event = asyncio.Event()
    _safety_gate_response = False

    # Visual Confirmation — capture screenshot showing what's about to happen
    confirmation_screenshot = ""
    if _browser_computer and _browser_computer._page:
        try:
            ss_bytes = await _browser_computer._page.screenshot(type="png")
            confirmation_screenshot = base64.b64encode(ss_bytes).decode()
        except Exception:
            pass

    # Broadcast confirmation UI to all clients
    confirmation_msg = {
        "type": "safety_confirmation",
        "action": action_description,
        "url": url,
        "prompt": prompt,
        "screenshot": confirmation_screenshot,
    }
    for ws in list(_voice_clients):
        try:
            await ws.send_json(confirmation_msg)
        except Exception:
            pass
    for ws in list(_screen_clients):
        try:
            await ws.send_json(confirmation_msg)
            # No browser TTS — Live API voice handles speaking
        except Exception:
            pass

    # Inject the confirmation prompt into the Live API so Gemini SPEAKS it aloud
    if _active_live_queue:
        try:
            speech_prompt = (
                f"IMPORTANT: Stop what you are doing and ask the user this safety question. "
                f"Say exactly: '{prompt}' "
                f"Then wait for the user to say yes or no. Do not continue until they respond."
            )
            _active_live_queue.send_content(
                types.Content(
                    role="user",
                    parts=[types.Part(text=speech_prompt)],
                )
            )
            logger.info("Safety gate prompt injected into Live API for voice output")
        except Exception as e:
            logger.warning("Failed to inject safety prompt into Live API: %s", e)

    # Wait for user response with timeout
    try:
        await asyncio.wait_for(_safety_gate_event.wait(), timeout=SAFETY_GATE_TIMEOUT)
    except asyncio.TimeoutError:
        logger.warning("Safety gate timed out after %ds — DENYING action", SAFETY_GATE_TIMEOUT)
        _safety_gate_response = False
        # Notify clients that it timed out
        timeout_msg = {
            "type": "safety_timeout",
            "message": "Confirmation timed out. Action was cancelled for safety.",
        }
        await _broadcast_to_all(timeout_msg)

    result = _safety_gate_response
    _safety_gate_event = None
    return result


# Sites that REQUIRE headed browser (they block headless)
_HEADED_DOMAINS = {
    "irctc.co.in", "makemytrip.com", "cleartrip.com", "goibibo.com",
    "google.com", "gmail.com", "accounts.google.com",
    "flipkart.com", "myntra.com",
    "sbi", "icicibank", "hdfcbank", "axisbank",
    "paytm.com", "phonepe.com",
}

# Track current browser mode
_current_browser_headless: Optional[bool] = None


def _should_use_headed(url: str, task_desc: str = "") -> bool:
    """Decide if a URL/task needs headed browser (bot detection) or headless is fine."""
    check_text = (url + " " + task_desc).lower()
    for domain in _HEADED_DOMAINS:
        if domain in check_text:
            return True
    # Login/payment pages always use headed
    if any(kw in check_text for kw in ["login", "signin", "checkout", "payment", "auth", "book", "ticket"]):
        return True
    return False


async def _get_browser(
    start_url: str = "https://www.google.com",
    task_desc: str = "",
):
    """Get or create the browser agent and computer.

    Smart browser selection: uses headed browser for sites that block bots,
    headless for everything else (faster).
    """
    global _browser_agent, _browser_computer, _current_browser_headless
    async with _browser_lock:
        # Determine if we need headed or headless based on URL + task description
        needs_headed = _should_use_headed(start_url, task_desc)
        want_headless = not needs_headed

        # Check if we need to switch browser mode
        need_new = _browser_computer is None
        if not need_new:
            try:
                if _browser_computer._page is None or _browser_computer._page.is_closed():
                    need_new = True
                elif _current_browser_headless != want_headless:
                    # Need to switch mode
                    logger.info(
                        "Switching browser: %s → %s for %s",
                        "headless" if _current_browser_headless else "headed",
                        "headless" if want_headless else "headed",
                        start_url[:50],
                    )
                    need_new = True
            except Exception:
                need_new = True

        if need_new:
            if _browser_computer:
                try:
                    await _browser_computer.close()
                except Exception:
                    pass

            agent, computer = create_browser_agent(headless=want_headless)
            _browser_agent = agent
            _browser_computer = computer
            _current_browser_headless = want_headless

            _browser_computer.set_preview_callback(_click_preview_callback)
            _browser_computer.set_safety_gate_callback(_safety_gate_callback)

            await _browser_computer.initialize()
            logger.info("Browser created: %s mode", "headless" if want_headless else "headed")

        if start_url and start_url != _browser_computer.get_current_url():
            await _browser_computer.navigate(start_url)

        return _browser_agent, _browser_computer


async def _broadcast_screenshot(
    screenshot: bytes, url: str = "", step: int = 0, action: str = ""
) -> None:
    """Send a screenshot to all connected screen viewers."""
    message = {
        "type": "screenshot",
        "data": base64.b64encode(screenshot).decode("utf-8"),
        "url": url,
        "step": step,
    }
    if action:
        message["action"] = action

    disconnected = []
    for ws in _screen_clients:
        try:
            await ws.send_json(message)
        except Exception:
            disconnected.append(ws)

    for ws in disconnected:
        _screen_clients.remove(ws)


async def _broadcast_action_overlay(description: str) -> None:
    """Send an action description overlay to screen viewers."""
    message = {
        "type": "action_overlay",
        "description": description,
    }
    for ws in list(_screen_clients):
        try:
            await ws.send_json(message)
        except Exception:
            pass


async def _broadcast_gpa_update(gpa_message: dict) -> None:
    """Broadcast a GPA step update to all voice + screen clients."""
    for ws in list(_screen_clients):
        try:
            await ws.send_json(gpa_message)
        except Exception:
            pass
    for ws in list(_voice_clients):
        try:
            await ws.send_json(gpa_message)
        except Exception:
            pass


async def _broadcast_to_all(message: dict) -> None:
    """Broadcast a message to all connected clients."""
    for ws in list(_screen_clients) + list(_voice_clients):
        try:
            await ws.send_json(message)
        except Exception:
            pass


async def _click_preview_callback(
    screenshot: bytes, x: int, y: int, action_type: str, description: str
) -> None:
    """Send a visual click preview to all screen clients.

    Shows a pulsing indicator at the target coordinates on the
    already-displayed screenshot — no extra image sent, no delay.
    """
    screen_w = settings.screen_width
    screen_h = settings.screen_height
    x_pct = (x / screen_w) * 100
    y_pct = (y / screen_h) * 100

    message = {
        "type": "click_preview",
        "x_pct": round(x_pct, 2),
        "y_pct": round(y_pct, 2),
        "action_type": action_type,
        "description": description,
    }

    for ws in list(_screen_clients):
        try:
            await ws.send_json(message)
        except Exception:
            pass


async def _run_browser_task(
    task_description: str,
    start_url: str,
    session_id: str,
    voice_ws: Optional[WebSocket] = None,
) -> str:
    """Execute a browser automation task with GPA logging.

    Args:
        task_description: What the browser should do.
        start_url: URL to start from.
        session_id: User session ID for logging.
        voice_ws: Optional WebSocket to stream GPA updates to.

    Returns:
        Result summary string.
    """
    task_id = await firestore_service.create_task(
        user_session_id=session_id,
        task_description=task_description,
        language="auto",
    )
    task_id_str = task_id or f"local-{uuid.uuid4().hex[:8]}"
    journal = TaskJournal(task_id_str, task_description)
    _active_journals[session_id] = journal

    # Create GPA Action Log
    gpa_log = GPAActionLog(task_id_str, task_description)

    # Set up GPA streaming callback
    async def stream_gpa(msg: dict):
        await _broadcast_gpa_update(msg)

    gpa_log.set_stream_callback(stream_gpa)

    # Check for workflow replay opportunity
    existing_workflow = await _workflow_recorder.find_matching_workflow(
        task_description
    )
    if existing_workflow:
        gpa_log.is_replay_mode = True
        logger.info(
            "Found replay workflow: %s (used %d times, %.0f%% success)",
            existing_workflow.name,
            existing_workflow.use_count,
            existing_workflow.success_rate * 100,
        )

    try:
        # ═══════════════════════════════════════════════════
        # PHASE 1: PLANNER AGENT — Research & Plan via Google Search
        # Runs BEFORE browser starts so we know WHERE to navigate
        # ═══════════════════════════════════════════════════
        orchestrator = TaskOrchestrator(broadcast_fn=_broadcast_gpa_update)

        logger.info("Calling Planner Agent for: %s", task_description[:60])
        try:
            plan = await asyncio.wait_for(
                orchestrator.plan(task_description),
                timeout=15,  # 15s max for planner
            )
        except asyncio.TimeoutError:
            logger.warning("Planner timed out after 30s, using fallback")
            plan = None

        # Determine start URL from planner or fallback
        if plan and plan.discovered_url:
            start_page = plan.discovered_url
            logger.info(
                "Planner found URL: %s (%d steps, confidence=%s)",
                plan.discovered_url,
                len(plan.steps),
                plan.source_confidence,
            )
        else:
            start_page = "https://duckduckgo.com"
            if plan:
                logger.warning("Planner created plan but no URL, using DuckDuckGo")
            else:
                logger.warning("Planner failed, using DuckDuckGo fallback")

        # ═══════════════════════════════════════════════════
        # PHASE 2: Initialize browser at the RIGHT page
        # ═══════════════════════════════════════════════════
        browser_agent, computer = await _get_browser(start_page, task_description)

        # Reset state from previous tasks
        computer.reset_task_state()
        try:
            if computer._page:
                await computer._page.evaluate("""() => {
                    ['sahay-nav-error', 'sahay-redirect-info'].forEach(id => {
                        const el = document.getElementById(id);
                        if (el) el.remove();
                    });
                }""")
        except Exception:
            pass

        executor = ActionExecutor(computer)

        session_service = InMemorySessionService()
        runner = Runner(
            agent=browser_agent,
            app_name="sahay",
            session_service=session_service,
        )

        # Build browser prompt from plan or fallback
        if plan:
            planning_prompt = orchestrator.get_browser_prompt(plan)
        else:
            planning_prompt = f"""TASK: {task_description}

Navigate to the appropriate website and complete this task.
Use DuckDuckGo to search if you don't know the URL.
After EVERY navigation/click, call get_page_text() to read the page.
Use click_element(selector) and fill_field(selector, value) for precise interaction.
When done: "TASK COMPLETE: [summary]"
If stuck: "TASK FAILED: [reason]"
If need info: "NEED INPUT: [what you need]" """

        # MANDATORY suffix — forces the agent to report completion
        planning_prompt += """

MANDATORY — YOU MUST DO THESE:
1. After EVERY page load, call get_page_text() FIRST. Do NOT skip this.
2. Use click_element(selector) and fill_field(selector, value) — NOT click_at(x,y).
3. You MUST end with either "TASK COMPLETE: [detailed summary]" or "TASK FAILED: [reason]".
4. Do NOT exit silently. ALWAYS report your final result."""

        if _form_memory:
            memory_str = ", ".join(f"{k}: {v}" for k, v in _form_memory.items())
            planning_prompt += f"\n\nUSER'S SAVED INFO (use these to fill forms): {memory_str}"

        session = await session_service.create_session(
            app_name="sahay",
            user_id=session_id,
        )

        content = types.Content(
            role="user",
            parts=[types.Part(text=planning_prompt)],
        )

        step_count = 0
        result_text = ""
        _last_summarized_url = ""
        task_start_time = time.time()
        no_change_count = 0

        # Set up cancel event for task stop
        global _task_cancel_event
        _task_cancel_event = asyncio.Event()

        # Loop detection — track recent actions to detect repetition
        _recent_actions: list[str] = []

        # Pattern to detect when browser agent needs user input
        _input_needed_pattern = re.compile(
            r"NEED\s+(INPUT|OTP|CHOICE|CAPTCHA|CLARIFICATION|CONFIRMATION):\s*(.+)",
            re.IGNORECASE,
        )

        logger.info("Starting runner.run_async loop for task: %s", task_id_str[:20])
        event_count = 0
        async for event in runner.run_async(
            user_id=session_id,
            session_id=session.id,
            new_message=content,
        ):
            # IMMEDIATE cancel check — first thing in every iteration
            if _task_cancel_event and _task_cancel_event.is_set():
                logger.info("Task CANCELLED at event #%d, step %d", event_count, step_count)
                result_text += "\n[Task stopped by user]\n"
                break

            event_count += 1
            if event.content and event.content.parts:
                for part in event.content.parts:
                    if part.text:
                        result_text += part.text + "\n"
                        await _broadcast_action_overlay(part.text)

                        # Instant voice response on TASK COMPLETE/FAILED
                        if "TASK COMPLETE" in part.text or "TASK FAILED" in part.text:
                            if _active_live_queue:
                                try:
                                    _active_live_queue.send_content(
                                        types.Content(
                                            role="user",
                                            parts=[types.Part(text=f"Tell the user: {part.text[:200]}. Then ask if they need anything else.")],
                                        )
                                    )
                                except Exception:
                                    pass

                        # Agent Reasoning — broadcast thinking to GPA panel
                        reasoning_msg = {
                            "type": "agent_reasoning",
                            "text": part.text[:300],
                            "step": step_count,
                        }
                        for ws in list(_screen_clients):
                            try:
                                await ws.send_json(reasoning_msg)
                            except Exception:
                                pass

                        # Detect NEED INPUT/OTP/CHOICE/etc. patterns
                        for line in part.text.split("\n"):
                            m = _input_needed_pattern.search(line)
                            if m:
                                need_type = m.group(1).upper()
                                need_detail = m.group(2).strip()
                                input_msg = {
                                    "type": "input_needed",
                                    "need_type": need_type,
                                    "message": need_detail,
                                }
                                logger.info(
                                    "Agent needs user input [%s]: %s",
                                    need_type, need_detail[:100],
                                )
                                # Broadcast to all voice and screen clients
                                for ws in list(_voice_clients):
                                    try:
                                        await ws.send_json(input_msg)
                                    except Exception:
                                        pass
                                for ws in list(_screen_clients):
                                    try:
                                        await ws.send_json(input_msg)
                                    except Exception:
                                        pass

                    # Log function calls as GPA steps
                    if part.function_call:
                        fc = part.function_call
                        action_type = executor.get_action_type(fc.name)
                        element_desc = executor.describe_element(
                            fc.name, fc.args or {}
                        )
                        action_detail = executor.describe_action(
                            fc.name, fc.args or {}
                        )

                        gpa_step = await gpa_log.add_step(
                            action_type=action_type,
                            element_description=element_desc,
                            action_detail=action_detail,
                            url=computer.get_current_url(),
                        )

                    if part.function_response:
                        # Complete the most recent in-progress GPA step
                        in_progress = [
                            s
                            for s in gpa_log.steps
                            if s.status == ActionStatus.IN_PROGRESS
                        ]
                        if in_progress:
                            latest = in_progress[-1]
                            await gpa_log.complete_step(
                                latest, ActionStatus.SUCCESS
                            )

            # Check if user pressed Stop
            if _task_cancel_event and _task_cancel_event.is_set():
                logger.info("Task cancelled by user at step %d", step_count)
                result_text += "\n[Task stopped by user]\n"
                break

            state = await computer.current_state()
            if state.screenshot:
                step_count += 1

                # Check step limit AFTER increment so we stop immediately
                if step_count >= MAX_BROWSER_STEPS:
                    logger.warning(
                        "Browser task hit max step limit (%d). Force stopping.",
                        MAX_BROWSER_STEPS,
                    )
                    await _broadcast_action_overlay(
                        f"Task stopped after {MAX_BROWSER_STEPS} steps."
                    )
                    result_text += f"\n[Task stopped: reached {MAX_BROWSER_STEPS} step limit]\n"
                    break

                # Stuck detection
                current_url = state.url or ""

                # If on about:blank (CAPTCHA redirect) or Google sorry page, count as stuck
                if "about:blank" in current_url or "google.com/sorry" in current_url:
                    no_change_count += 1
                    if no_change_count >= 2:
                        logger.warning("Browser stuck on error/CAPTCHA page: %s", current_url[:80])
                        result_text += "\n[Task stopped: search engine blocked]\n"
                        await _broadcast_action_overlay("Search engine blocked. Task stopped.")
                        break
                else:
                    no_change_count = 0

                # Use screenshot diff to detect truly stuck state (not URL)
                # SPAs don't change URL when navigating within the app
                diff_check = computer.diff_engine.compute_diff(state.screenshot)
                if diff_check.changed_fraction < 0.01:
                    # Screenshot almost identical — agent might be stuck
                    no_change_count += 1
                else:
                    # Screenshot changed — agent is making progress
                    no_change_count = 0

                # Only declare stuck if 8+ consecutive steps with no visual change
                if no_change_count >= 8:
                    logger.warning(
                        "Browser agent stuck for 8 steps with no visual change: %s",
                        current_url[:80],
                    )
                    result_text += "\n[Task stopped: agent stuck — no visual progress]\n"
                    await _broadcast_action_overlay(
                        "Agent appears stuck — stopping task."
                    )
                    break

                # Loop detection — track screenshot hashes to detect visual repetition
                import hashlib
                ss_hash = hashlib.md5(state.screenshot[:1000]).hexdigest()[:8]
                _recent_actions.append(ss_hash)
                if len(_recent_actions) > 8:
                    _recent_actions.pop(0)
                # If last 4 screenshots are identical — agent is visually stuck
                if len(_recent_actions) >= 6 and len(set(_recent_actions[-6:])) == 1:
                    logger.warning("Loop detected: 6 identical screenshots in a row")
                    result_text += "\nTASK FAILED: Agent is stuck on the same screen. Try a different approach or use Take Over.\n"
                    await _broadcast_action_overlay("Agent stuck — use Take Over to help.")
                    break

                journal.create_entry(
                    action_type="browser_step",
                    action_description=(
                        result_text.split("\n")[-2]
                        if result_text.strip()
                        else "Processing..."
                    ),
                    screenshot_after=state.screenshot,
                    url=state.url or "",
                    success=True,
                )

                # Smart screenshot diff
                diff_result = computer.diff_engine.compute_diff(
                    state.screenshot
                )
                diff_data = None
                if diff_result.use_diff_mode:
                    diff_data = {
                        "changed_percent": diff_result.to_dict()["changed_percent"],
                        "num_regions": len(diff_result.regions),
                        "context": diff_result.context_summary[:200],
                    }

                await _broadcast_screenshot(
                    state.screenshot,
                    url=state.url or "",
                    step=step_count,
                    action=(
                        result_text.split("\n")[-2]
                        if result_text.strip()
                        else ""
                    ),
                )

                # Send diff overlay to frontend if available
                if diff_data:
                    await _broadcast_to_all({
                        "type": "screenshot_diff",
                        "diff": diff_data,
                        "tokens_saved": computer.diff_engine.stats["estimated_tokens_saved"],
                    })

                # Live page summarizer — visual only (no voice), on URL change
                if state.url and state.url != _last_summarized_url:
                    _last_summarized_url = state.url
                    try:
                        page_text = await computer.get_page_text()
                        if page_text and len(page_text) > 50:
                            summary = page_text[:200].replace("\n", " ").strip()
                            await _broadcast_action_overlay(f"Page: {summary[:100]}...")
                    except Exception:
                        pass

        logger.info("Runner loop exited after %d events, %d steps", event_count, step_count)

        # Check if agent reported completion — if not, CONTINUE with a follow-up prompt
        task_reported = "TASK COMPLETE" in result_text or "TASK FAILED" in result_text
        continuation_attempts = 0
        cancelled = _task_cancel_event and _task_cancel_event.is_set()
        while not task_reported and not cancelled and continuation_attempts < 10:
            continuation_attempts += 1
            logger.warning(
                "Agent exited without TASK COMPLETE/FAILED — sending continuation (attempt %d/3)",
                continuation_attempts,
            )

            # Read current page to give model context
            try:
                page_text = await computer.get_page_text()
                current_url = computer.get_current_url()
            except Exception:
                page_text = ""
                current_url = ""

            # Create a new session + re-run with continuation prompt
            continuation_session = await session_service.create_session(
                app_name="sahay",
                user_id=session_id,
            )
            continuation_prompt = types.Content(
                role="user",
                parts=[types.Part(text=f"""YOU MUST TAKE ACTION NOW. Do NOT describe the page. Do NOT explain. Just ACT.

TASK: {task_description}
URL: {current_url}

WHAT TO DO RIGHT NOW:
- If you see a form → fill_field() or type_text_at() to fill it
- If you see a button → click_at() to click it
- If you need info from user → say "NEED INPUT: [what]"
- If you need OTP → say "NEED OTP: check your phone"
- If you see CAPTCHA → say "NEED CAPTCHA: [describe it]"
- If task is impossible → say "TASK FAILED: [reason]"
- If task is done → say "TASK COMPLETE: [result]"

TAKE AN ACTION. Do NOT just describe what you see.""")],
            )

            async for event in runner.run_async(
                user_id=session_id,
                session_id=continuation_session.id,
                new_message=continuation_prompt,
            ):
                # Check cancel in continuation loop too
                if _task_cancel_event and _task_cancel_event.is_set():
                    result_text += "\n[Task stopped by user]\n"
                    break

                event_count += 1
                if event.content and event.content.parts:
                    for part in event.content.parts:
                        if part.text:
                            result_text += part.text + "\n"
                            await _broadcast_action_overlay(part.text)

                state = await computer.current_state()
                if state.screenshot:
                    step_count += 1
                    journal.create_entry(
                        action_type="browser_step",
                        action_description="Continuing task...",
                        screenshot_after=state.screenshot,
                        url=state.url or "",
                        success=True,
                    )
                    screenshot_b64 = base64.b64encode(state.screenshot).decode()
                    for ws in list(_screen_clients):
                        try:
                            await ws.send_json({
                                "type": "screenshot",
                                "data": screenshot_b64,
                                "url": state.url or "",
                                "step": step_count,
                            })
                        except Exception:
                            pass

                if step_count >= MAX_BROWSER_STEPS:
                    break

            task_reported = "TASK COMPLETE" in result_text or "TASK FAILED" in result_text
            cancelled = _task_cancel_event and _task_cancel_event.is_set()
            if cancelled:
                break

        # If still no report after continuations, extract page content as summary
        if not task_reported:
            logger.warning("Agent never reported completion — extracting page as summary")
            try:
                page_text = await computer.get_page_text()
                current_url = computer.get_current_url()
                if page_text:
                    result_text += f"\nTASK COMPLETE: Reached {current_url}. Content: {page_text[:500]}\n"
            except Exception:
                result_text += "\nTASK COMPLETE: Browser task finished.\n"

        # Complete any remaining in-progress steps
        for s in gpa_log.steps:
            if s.status == ActionStatus.IN_PROGRESS:
                await gpa_log.complete_step(s, ActionStatus.SUCCESS)

        # Entity extraction on final page
        extracted_result = {}
        try:
            page_text = await computer.get_page_text()
            extracted_result = await _entity_extractor.extract_task_result(
                page_text, task_description, source_url=computer.get_current_url()
            )
            if extracted_result:
                voice_summary = _entity_extractor.format_for_voice(
                    extracted_result
                )
                if voice_summary and voice_ws:
                    try:
                        await voice_ws.send_json(
                            {"type": "text", "data": voice_summary}
                        )
                    except Exception:
                        pass
        except Exception as e:
            logger.debug("Entity extraction skipped: %s", e)

        # Form Memory — save extracted entities for future tasks
        if extracted_result:
            for key, val in extracted_result.items():
                if val and key in ("name", "phone", "email", "aadhaar", "address", "pincode", "dob"):
                    _form_memory[key] = val
                    logger.info("Form memory saved: %s", key)

        # UPI Payment Detection — disabled (too many false positives)
        # Can be re-enabled for specific payment demo tasks

        # Record workflow for future replay — only if task was NOT stuck/failed
        task_failed = any(
            marker in result_text
            for marker in ["[Task stopped:", "TASK FAILED", "CAPTCHA", "stuck"]
        )
        gpa_summary = gpa_log.get_summary()
        if gpa_summary["succeeded"] >= 2 and not task_failed:
            try:
                await _workflow_recorder.record_from_gpa_log(
                    gpa_log, task_description
                )
            except Exception as e:
                logger.debug("Workflow recording skipped: %s", e)
        elif task_failed:
            logger.info("Skipping workflow recording — task was stuck/failed")

        # Update workflow stats if this was a replay
        if existing_workflow:
            duration = int((time.time() - task_start_time) * 1000)
            await _workflow_recorder.update_workflow_stats(
                existing_workflow, success=True, duration_ms=duration
            )

        if task_id:
            outcome = result_text.strip() or "Task completed"
            await firestore_service.complete_task(task_id, outcome, step_count)
            await journal.save_to_firestore()

        # Guardian notification
        try:
            await _guardian_service.notify_guardian(
                user_id=session_id,
                task_description=task_description,
                outcome=result_text.strip() or "Task completed",
                steps_count=step_count,
                domain=computer.get_current_url(),
            )
        except Exception:
            pass

        # Keep results on screen — don't navigate away
        # Next task will navigate to the right page via planner
        return result_text.strip() or "Task completed successfully."

    except Exception as e:
        logger.error("Browser task failed: %s", e, exc_info=True)

        # Mark remaining GPA steps as failed
        for s in gpa_log.steps:
            if s.status == ActionStatus.IN_PROGRESS:
                await gpa_log.complete_step(
                    s, ActionStatus.FAILED, error=str(e)
                )

        if existing_workflow:
            duration = int((time.time() - task_start_time) * 1000)
            await _workflow_recorder.update_workflow_stats(
                existing_workflow, success=False, duration_ms=duration
            )

        if task_id:
            await firestore_service.fail_task(task_id, str(e))

        return f"Task failed: {e}"


# ── Routes ──────────────────────────────────────────────────────────────


@app.get("/")
async def serve_dashboard():
    """Serve the main dashboard."""
    return FileResponse("app/static/index.html")


@app.get("/health")
async def health_check():
    """Health check endpoint for Cloud Run."""
    return {
        "status": "healthy",
        "service": "sahay",
        "version": "3.0.0",
        "features": [
            "gpa_action_log",
            "self_healing",
            "workflow_replay",
            "entity_extraction",
            "multi_step_orchestration",
            "visual_click_preview",
            "guardian_mode",
            "smart_screenshot_diff",
            "upi_payment",
            "safety_gate_voice_confirmation",
        ],
    }


@app.post("/api/safety-gate/respond")
async def safety_gate_respond(body: dict):
    """User responds to a safety gate confirmation.

    Body: {"approved": true/false}
    """
    global _safety_gate_response, _safety_gate_event
    approved = body.get("approved", False)
    _safety_gate_response = bool(approved)

    if _safety_gate_event:
        _safety_gate_event.set()
        decision = "APPROVED" if approved else "DENIED"
        logger.info("Safety gate response: %s", decision)

        # Broadcast to conversation so user sees it
        msg_text = "Safety check approved — proceeding with action." if approved else "Safety check denied — action cancelled."
        for ws in list(_voice_clients):
            try:
                await ws.send_json({"type": "text", "data": msg_text})
            except Exception:
                pass
        for ws in list(_screen_clients):
            try:
                await ws.send_json({"type": "action_overlay", "description": msg_text})
            except Exception:
                pass

        return {"status": "ok", "decision": "approved" if approved else "denied"}
    else:
        logger.warning("Safety gate response received but no pending confirmation")
        return {"status": "no_pending", "message": "No action awaiting confirmation"}


# Global flag to signal task cancellation
_task_cancel_event: Optional[asyncio.Event] = None


@app.post("/api/task/stop")
async def stop_task():
    """Stop the currently running browser task immediately."""
    global _browser_task_running, _task_cancel_event
    if _browser_task_running:
        # Set cancel flag
        if _task_cancel_event:
            _task_cancel_event.set()
        _browser_task_running = False
        logger.info("Task FORCE STOP requested")

        # Navigate to blank to kill pending actions — keeps browser window open
        if _browser_computer and _browser_computer._page:
            try:
                await _browser_computer._page.goto("about:blank", timeout=2000)
            except Exception:
                pass

        # Broadcast stop to clients
        for ws in list(_screen_clients):
            try:
                await ws.send_json({"type": "action_overlay", "description": "Task stopped."})
            except Exception:
                pass
        return {"status": "stopped"}
    return {"status": "no_task_running"}


@app.post("/api/takeover/click")
async def takeover_click(body: dict):
    """User clicks directly on the browser screenshot to help the agent.

    Body: {"x_pct": 50.0, "y_pct": 30.0}
    Coordinates are percentages (0-100) of the viewport.
    """
    x_pct = body.get("x_pct", 0)
    y_pct = body.get("y_pct", 0)

    if not _browser_computer or not _browser_computer._page:
        return {"status": "error", "message": "No browser running"}

    # Convert percentage to pixel coordinates
    x = int(x_pct / 100 * settings.screen_width)
    y = int(y_pct / 100 * settings.screen_height)

    logger.info("Takeover click at (%d, %d) from user", x, y)

    try:
        await _browser_computer._page.mouse.click(x, y)
        await asyncio.sleep(1)
        state = await _browser_computer.current_state()
        if state.screenshot:
            ss_b64 = base64.b64encode(state.screenshot).decode()
            for ws in list(_screen_clients):
                try:
                    await ws.send_json({
                        "type": "screenshot",
                        "data": ss_b64,
                        "url": state.url or "",
                    })
                except Exception:
                    pass
        return {"status": "clicked", "x": x, "y": y}
    except Exception as e:
        logger.warning("Takeover click failed: %s", e)
        return {"status": "error", "message": str(e)}


@app.post("/api/task")
async def submit_task(body: dict):
    """Submit a text-based task (fallback for non-voice input).

    Expects JSON: {"task": "...", "session_id": "..."}
    """
    task = body.get("task", "")
    session_id = body.get("session_id", str(uuid.uuid4()))
    start_url = body.get("start_url", "")

    if not task:
        return JSONResponse(
            status_code=400,
            content={"error": "Task description is required"},
        )

    intent = parse_intent(task)
    url = start_url or intent.start_url or ""

    # Check multi-step decomposition
    steps = _workflow_orchestrator.decompose_task(task)
    if len(steps) > 1:
        logger.info("Multi-step task detected: %d steps", len(steps))

    result = await _run_browser_task(task, url, session_id)
    return {"task_id": session_id, "result": result}


@app.get("/api/history/{session_id}")
async def get_task_history(session_id: str, limit: int = 10):
    """Get task history for a session."""
    tasks = await firestore_service.get_recent_tasks(
        n=limit, user_session_id=session_id
    )
    return {"session_id": session_id, "tasks": tasks}


# ── Guardian API ────────────────────────────────────────────────────────


@app.post("/api/guardian/setup")
async def setup_guardian(body: dict):
    """Set up guardian mode for a user.

    Body: {guardian_name, guardian_phone, user_name, allowed_domains?, spending_cap?}
    """
    config = await _guardian_service.create_guardian(
        guardian_name=body.get("guardian_name", ""),
        guardian_phone=body.get("guardian_phone", ""),
        user_name=body.get("user_name", ""),
        allowed_domains=body.get("allowed_domains"),
        spending_cap=body.get("spending_cap", 5000.0),
    )
    return {"status": "created", "config": config.to_dict()}


@app.get("/api/guardian/{guardian_id}")
async def get_guardian_dashboard(guardian_id: str):
    """Get guardian dashboard data: config + recent notifications."""
    config = await _guardian_service.get_config_by_guardian(guardian_id)
    if not config:
        return JSONResponse(status_code=404, content={"error": "Guardian not found"})

    notifications = await _guardian_service.get_notifications(guardian_id)
    return {
        "config": config.to_dict(),
        "notifications": [n.to_dict() for n in notifications],
    }


@app.post("/api/guardian/{guardian_id}/credentials")
async def add_guardian_credential(guardian_id: str, body: dict):
    """Store encrypted credentials for a service.

    Body: {service_domain, username, password, label?}
    """
    config = await _guardian_service.get_config_by_guardian(guardian_id)
    if not config:
        return JSONResponse(status_code=404, content={"error": "Guardian not found"})

    success = await _guardian_service.add_credential(
        user_id=config.user_id,
        service_domain=body.get("service_domain", ""),
        username=body.get("username", ""),
        password=body.get("password", ""),
        label=body.get("label", ""),
    )
    return {"status": "stored" if success else "failed"}


@app.post("/api/guardian/{guardian_id}/config")
async def update_guardian_config(guardian_id: str, body: dict):
    """Update guardian configuration.

    Body: {allowed_domains?, spending_cap_inr?, require_confirmation_above?, ...}
    """
    config = await _guardian_service.get_config_by_guardian(guardian_id)
    if not config:
        return JSONResponse(status_code=404, content={"error": "Guardian not found"})

    updated = await _guardian_service.update_config(config.user_id, body)
    return {"status": "updated", "config": updated.to_dict() if updated else None}


@app.get("/api/guardian/{guardian_id}/notifications")
async def get_guardian_notifications(guardian_id: str, limit: int = 20):
    """Get recent notifications for a guardian."""
    notifications = await _guardian_service.get_notifications(guardian_id, limit)
    return {"notifications": [n.to_dict() for n in notifications]}


# ── Shareable Journal ──────────────────────────────────────────────────


@app.post("/api/journal/share")
async def share_journal(body: dict):
    """Create a shareable link for a task journal.

    Body: {task_id, journal_data}
    """
    task_id = body.get("task_id", "")
    journal_data = body.get("journal_data", {})
    token = await _guardian_service.create_shareable_journal(task_id, journal_data)
    return {"share_url": f"/journal/{token}", "token": token}


@app.get("/journal/{token}")
async def view_shared_journal(token: str):
    """View a shared journal (returns JSON for now)."""
    data = await _guardian_service.get_shared_journal(token)
    if not data:
        return JSONResponse(status_code=404, content={"error": "Journal not found"})
    return data


# ── WebSocket: Voice ────────────────────────────────────────────────────


@app.websocket("/ws/voice")
async def websocket_voice(ws: WebSocket):
    """WebSocket endpoint for voice I/O via Live API.

    Handles bidirectional audio streaming between the frontend
    and the Gemini Voice Agent.
    """
    await ws.accept()
    session_id = str(uuid.uuid4())
    logger.info("Voice WebSocket connected: %s", session_id)

    # Replace old voice connections — don't close them (triggers browser reconnect loop)
    _voice_clients.clear()
    _voice_clients.append(ws)

    voice_agent = create_voice_agent()
    session_service = InMemorySessionService()
    runner = Runner(
        agent=voice_agent,
        app_name="sahay",
        session_service=session_service,
    )
    live_queue = LiveRequestQueue()

    # Make queue globally accessible for safety gate voice injection
    global _active_live_queue
    _active_live_queue = live_queue

    session = await session_service.create_session(
        app_name="sahay",
        user_id=session_id,
    )

    live_config = get_live_run_config()
    run_config = RunConfig(**live_config)

    async def upstream():
        """Read audio/text from WebSocket and send to Live API."""
        try:
            while True:
                data = await ws.receive()

                if "text" in data:
                    msg = json.loads(data["text"])
                    if msg.get("type") == "audio":
                        audio_bytes = base64.b64decode(msg["data"])
                        live_queue.send_realtime(
                            types.Blob(
                                data=audio_bytes,
                                mime_type="audio/pcm;rate=16000",
                            )
                        )
                    elif msg.get("type") == "text":
                        user_text = msg.get("data", "").strip().lower()
                        # Rollback — go back / undo
                        if user_text in ("go back", "undo", "back", "peeche jao", "wapas jao"):
                            if _browser_computer and _browser_computer._page:
                                try:
                                    await _browser_computer._page.go_back(timeout=5000)
                                    logger.info("Rollback: navigated back")
                                    state = await _browser_computer.current_state()
                                    if state.screenshot:
                                        ss_b64 = base64.b64encode(state.screenshot).decode()
                                        for sc_ws in list(_screen_clients):
                                            try:
                                                await sc_ws.send_json({"type": "screenshot", "data": ss_b64, "url": state.url or ""})
                                            except Exception:
                                                pass
                                    for v_ws in list(_voice_clients):
                                        try:
                                            await v_ws.send_json({"type": "speak_tts", "text": "Going back to the previous page."})
                                        except Exception:
                                            pass
                                except Exception as e:
                                    logger.warning("Rollback failed: %s", e)
                        else:
                            live_queue.send_content(
                                types.Content(
                                    role="user",
                                    parts=[types.Part(text=msg["data"])],
                                )
                            )
                    elif msg.get("type") == "confirmation_response":
                        live_queue.send_content(
                            types.Content(
                                role="user",
                                parts=[types.Part(text=msg["data"])],
                            )
                        )
                    elif msg.get("type") == "activity_start":
                        live_queue.send_activity_start()
                    elif msg.get("type") == "activity_end":
                        live_queue.send_activity_end()

                elif "bytes" in data:
                    live_queue.send_realtime(
                        types.Blob(
                            data=data["bytes"],
                            mime_type="audio/pcm;rate=16000",
                        )
                    )
        except WebSocketDisconnect:
            logger.info("Voice upstream disconnected: %s", session_id)
        except Exception as e:
            logger.error("Voice upstream error: %s", e)
        finally:
            live_queue.close()

    async def downstream():
        """Stream Live API events back to the WebSocket.

        On each reconnection, creates a fresh LiveRequestQueue, Runner,
        and session so old Live API connections are fully released and
        don't pile up toward the concurrent session limit.
        """
        global _safety_gate_response
        nonlocal live_queue, runner, session

        reconnect_attempts = 0
        max_reconnect_attempts = 10

        while reconnect_attempts < max_reconnect_attempts:
            try:
                async for event in runner.run_live(
                    user_id=session_id,
                    session_id=session.id,
                    live_request_queue=live_queue,
                    run_config=run_config,
                ):
                    # Reset reconnect counter on successful activity
                    reconnect_attempts = 0

                    # Check for function calls (browser_action tool)
                    # Throttle: max 1 browser_action per 10 seconds
                    for fc in event.get_function_calls():
                        if fc.name == "browser_action" and fc.args:
                            if _browser_task_running:
                                continue  # Skip silently if task running
                            task_desc = fc.args.get("task_description", "")
                            start_url = fc.args.get("start_url", "")
                            if task_desc:
                                # Skip vague/meta tasks that aren't real user requests
                                skip_phrases = [
                                    "refresh", "reload", "load content",
                                    "check if", "see if", "try again",
                                ]
                                task_lower = task_desc.lower()
                                if any(p in task_lower for p in skip_phrases) and len(task_desc) < 60:
                                    logger.warning(
                                        "Skipping vague browser task: %s", task_desc[:80]
                                    )
                                    continue

                                logger.info("Browser action triggered: %s", task_desc[:100])
                                asyncio.create_task(
                                    _handle_browser_tool_call_direct(
                                        task_desc, start_url, session_id, ws
                                    )
                                )

                    if event.content and event.content.parts:
                        for part in event.content.parts:
                            if part.inline_data:
                                blob = part.inline_data
                                if blob.mime_type and "audio" in blob.mime_type:
                                    await ws.send_json({
                                        "type": "audio",
                                        "data": base64.b64encode(blob.data).decode("utf-8"),
                                        "mime_type": blob.mime_type,
                                    })

                            if part.text:
                                text = part.text
                                await ws.send_json({
                                    "type": "text",
                                    "data": text,
                                })

                    if event.input_transcription:
                        tr = event.input_transcription
                        text_val = tr.text if hasattr(tr, "text") else str(tr)
                        if text_val:
                            await ws.send_json({
                                "type": "transcript",
                                "role": "user",
                                "text": text_val,
                            })

                            # Check if this is a safety gate response
                            if _safety_gate_event and not _safety_gate_event.is_set():
                                text_lower = text_val.strip().lower()
                                # Use word boundary regex to avoid false positives
                                # e.g. "ha" in "what" or "no" in "don't know"
                                import re as _re
                                _YES_PATTERNS = [
                                    r'\byes\b', r'\byeah\b', r'\byep\b',
                                    r'\bok\b', r'\bokay\b', r'\bsure\b',
                                    r'\bgo ahead\b', r'\bproceed\b', r'\bconfirm\b',
                                    r'\bdo it\b', r'\bhaan\b', r'\btheek hai\b',
                                    r'\bkar do\b', r'\bchalo\b', r'\bbilkul\b',
                                    r'^ha+$', r'^haa+$',  # Only match standalone "ha"/"haa"
                                ]
                                _NO_PATTERNS = [
                                    r'\bno\b', r'\bnope\b', r'\bstop\b',
                                    r'\bcancel\b', r"\bdon'?t\b",
                                    r'\bnahi\b', r'\bnah\b', r'\bmat karo\b',
                                    r'\bruk\b', r'\bband karo\b',
                                    r'\bruko\b', r'\bmat\b', r'\bnahin\b',
                                ]
                                if any(_re.search(p, text_lower) for p in _YES_PATTERNS):
                                    _safety_gate_response = True
                                    _safety_gate_event.set()
                                    logger.info("Safety gate APPROVED via voice: '%s'", text_val)
                                elif any(_re.search(p, text_lower) for p in _NO_PATTERNS):
                                    _safety_gate_response = False
                                    _safety_gate_event.set()
                                    logger.info("Safety gate DENIED via voice: '%s'", text_val)

                    if event.output_transcription:
                        tr = event.output_transcription
                        text_val = tr.text if hasattr(tr, "text") else str(tr)
                        if text_val:
                            await ws.send_json({
                                "type": "transcript",
                                "role": "agent",
                                "text": text_val,
                            })

                # run_live ended normally — close old queue, create fresh resources
                reconnect_attempts += 1
                logger.info(
                    "Live API session ended. Cleaning up before reconnect (%d/%d): %s",
                    reconnect_attempts, max_reconnect_attempts, session_id,
                )

                # Close the old queue so the old Live API connection is released
                try:
                    live_queue.close()
                except Exception:
                    pass

                # Wait before reconnecting
                delay = min(2 ** reconnect_attempts, 10)
                await asyncio.sleep(delay)

                # Create fresh resources for the new connection
                live_queue = LiveRequestQueue()
                _active_live_queue = live_queue
                session_service_new = InMemorySessionService()
                runner = Runner(
                    agent=voice_agent,
                    app_name="sahay",
                    session_service=session_service_new,
                )
                session = await session_service_new.create_session(
                    app_name="sahay",
                    user_id=session_id,
                )
                logger.info("Reconnecting with fresh session: %s", session_id)

            except WebSocketDisconnect:
                logger.info("Voice downstream disconnected: %s", session_id)
                return
            except Exception as e:
                err_str = str(e)
                if "RESOURCE_EXHAUSTED" in err_str:
                    logger.error("Live API session limit reached: %s", session_id)
                    try:
                        await ws.send_json({
                            "type": "text",
                            "data": "Voice service is busy. Please wait a moment and try again.",
                        })
                    except Exception:
                        pass
                    # Wait longer before retrying on resource exhaustion
                    reconnect_attempts += 1
                    await asyncio.sleep(15)

                    # Create completely fresh resources
                    try:
                        live_queue.close()
                    except Exception:
                        pass
                    live_queue = LiveRequestQueue()
                    _active_live_queue = live_queue
                    session_service_new = InMemorySessionService()
                    runner = Runner(
                        agent=voice_agent,
                        app_name="sahay",
                        session_service=session_service_new,
                    )
                    session = await session_service_new.create_session(
                        app_name="sahay",
                        user_id=session_id,
                    )
                    logger.info("Retrying after RESOURCE_EXHAUSTED cooldown: %s", session_id)

                elif "1000" in err_str:
                    reconnect_attempts += 1
                    logger.info(
                        "Live API idle timeout. Cleaning up (%d/%d): %s",
                        reconnect_attempts, max_reconnect_attempts, session_id,
                    )

                    try:
                        live_queue.close()
                    except Exception:
                        pass

                    delay = min(2 ** reconnect_attempts, 10)
                    await asyncio.sleep(delay)

                    # Fresh resources
                    live_queue = LiveRequestQueue()
                    _active_live_queue = live_queue
                    session_service_new = InMemorySessionService()
                    runner = Runner(
                        agent=voice_agent,
                        app_name="sahay",
                        session_service=session_service_new,
                    )
                    session = await session_service_new.create_session(
                        app_name="sahay",
                        user_id=session_id,
                    )

                elif "not found" in err_str.lower() or "Tool '" in err_str:
                    reconnect_attempts += 1
                    logger.warning("Live API tool error (reconnecting): %s", err_str[:200])
                    await asyncio.sleep(2)
                else:
                    logger.error("Voice downstream error: %s", e)
                    return

        logger.warning("Max reconnect attempts reached: %s", session_id)

    try:
        await asyncio.gather(upstream(), downstream())
    except Exception as e:
        logger.error("Voice WebSocket error: %s", e)
    finally:
        if ws in _voice_clients:
            _voice_clients.remove(ws)
        logger.info("Voice WebSocket closed: %s", session_id)


async def _handle_browser_tool_call_direct(
    task_desc: str, start_url: str, session_id: str, ws: WebSocket
) -> None:
    """Handle a browser task triggered by function call detection.

    Only one browser task can run at a time. If another task is already
    running, this call is rejected to prevent concurrent page conflicts.
    """
    global _browser_task_running, _last_browser_task_desc, _last_browser_task_time

    # Strip ALL Google references — Google blocks headless browsers
    task_desc = re.sub(
        r'\b(on|using|via|through|with|from)\s+Google\b', '', task_desc, flags=re.IGNORECASE
    ).strip()
    task_desc = re.sub(
        r'\bGoogle\s+(search|it|for)\b', 'Search', task_desc, flags=re.IGNORECASE
    ).strip()
    task_desc = re.sub(
        r'\bSearch\s+Google\s+(for)?\b', 'Search ', task_desc, flags=re.IGNORECASE
    ).strip()
    task_desc = re.sub(
        r'\b(on|in)\s+Google\b', '', task_desc, flags=re.IGNORECASE
    ).strip()

    # Single gate — reject if running, no race conditions
    if _browser_task_running:
        return  # Silent reject — don't spam logs

    now = time.time()
    if (task_desc.strip() == _last_browser_task_desc.strip()
            and now - _last_browser_task_time < 30):
        return  # Silent reject duplicate

    try:
        async with _browser_task_lock:
            if _browser_task_running:
                return  # Lost race — another task got the lock first

            _browser_task_running = True
            _last_browser_task_desc = task_desc
            _last_browser_task_time = now
            logger.info("Starting browser task: %s (url: %s)", task_desc[:100], start_url)
            await ws.send_json({
                "type": "text",
                "data": f"Starting browser task: {task_desc}",
            })

            # Task Queue — split ONLY on "then"/"after that" (not "and" — too aggressive)
            import re as _re
            task_parts = _re.split(r'\bthen\b|\bafter that\b', task_desc, flags=_re.IGNORECASE)
            task_parts = [t.strip() for t in task_parts if t.strip() and len(t.strip()) > 20]

            try:
                if len(task_parts) > 1:
                    logger.info("Task queue: split into %d sub-tasks", len(task_parts))
                    combined_result = ""
                    for i, sub_task in enumerate(task_parts):
                        logger.info("Task queue: executing sub-task %d/%d: %s", i+1, len(task_parts), sub_task[:50])
                        # Broadcast sub-task progress
                        for screen_ws in list(_screen_clients):
                            try:
                                await screen_ws.send_json({"type": "action_overlay", "description": f"Sub-task {i+1}/{len(task_parts)}: {sub_task[:60]}"})
                            except Exception:
                                pass
                        sub_result = await _run_browser_task(sub_task, "", session_id, voice_ws=ws)
                        combined_result += f"\nSub-task {i+1}: {sub_result}\n"
                    result = combined_result
                else:
                    result = await _run_browser_task(
                        task_desc, start_url, session_id, voice_ws=ws
                    )
            except asyncio.CancelledError:
                result = "Task was cancelled."

            # Determine if task actually succeeded or failed
            task_failed = any(
                marker in result
                for marker in ["[Task stopped:", "TASK FAILED", "CAPTCHA", "stuck", "timed out", "Task failed"]
            )
            status_prefix = "Browser task FAILED" if task_failed else "Browser task complete"
            await ws.send_json({
                "type": "text",
                "data": f"{status_prefix}: {result[:500]}",
            })

            # Inject result into Live API so voice agent can speak it + ask follow-up
            live_prefix = "BROWSER ERROR" if task_failed else "BROWSER RESULT"
            if _active_live_queue:
                try:
                    followup = (
                        " After telling the user this result, ask if they want to do anything else. "
                        "Suggest 1-2 related follow-up tasks. Keep it short and friendly. "
                        "For example: 'Would you like me to compare prices on another site?' or "
                        "'Should I look for more options?'"
                    )
                    _active_live_queue.send_content(
                        types.Content(
                            role="user",
                            parts=[types.Part(text=f"{live_prefix}: {result[:300]}{followup}")],
                        )
                    )
                except Exception:
                    pass
    except Exception as e:
        logger.error("Browser tool call handler error: %s", e)
        error_msg = f"Task failed: {e}"
        try:
            await ws.send_json({
                "type": "text",
                "data": error_msg,
            })
            # Also inject error into Live API so voice agent speaks it
            if _active_live_queue:
                _active_live_queue.send_content(
                    types.Content(
                        role="user",
                        parts=[types.Part(text=f"BROWSER ERROR: {error_msg}")],
                    )
                )
        except Exception:
            pass
    finally:
        _browser_task_running = False


async def _handle_browser_tool_call(
    text: str, session_id: str, ws: WebSocket
) -> None:
    """Handle a browser task request from text marker (fallback)."""
    try:
        parts = text.split(" | ")
        task_desc = parts[0].replace("BROWSER_TASK_REQUESTED: ", "").strip()
        start_url = ""
        if len(parts) > 1:
            start_url = parts[1].replace("START_URL: ", "").strip()

        await _handle_browser_tool_call_direct(task_desc, start_url, session_id, ws)
    except Exception as e:
        logger.error("Browser tool call handler error: %s", e)


# ── WebSocket: Screen ───────────────────────────────────────────────────


@app.websocket("/ws/screen")
async def websocket_screen(ws: WebSocket):
    """WebSocket endpoint for streaming browser screenshots to the frontend."""
    await ws.accept()
    _screen_clients.append(ws)
    logger.info("Screen WebSocket connected (total: %d)", len(_screen_clients))

    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        if ws in _screen_clients:
            _screen_clients.remove(ws)
        logger.info("Screen WebSocket disconnected (total: %d)", len(_screen_clients))


# ── Static Files ────────────────────────────────────────────────────────

app.mount("/static", StaticFiles(directory="app/static"), name="static")
