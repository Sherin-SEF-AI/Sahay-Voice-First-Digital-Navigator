PART 1: CLAUDE.md
markdown# CLAUDE.md — SAHAY (सहाय)

## Project Identity
- **Name**: SAHAY (सहाय — Hindi for "help/assistance")
- **Tagline**: "Just say what you need. SAHAY does the clicking."
- **Hackathon**: Gemini Live Agent Challenge (Devpost)
- **Track**: UI Navigator 🖥️ (Visual UI Understanding & Interaction)
- **Deadline**: March 16, 2026, 5:00 PM PDT
- **Author**: Sherin Joseph Roy, Co-Founder & Head of Products, DeepMost AI

## What SAHAY Does
SAHAY is a voice-controlled AI agent that watches any screen, understands what the user wants in their native language, and autonomously navigates web applications to complete tasks — with human confirmation before any sensitive action. The user never touches the keyboard. They just talk.

Target users: 900M+ digitally illiterate people globally, especially India's elderly population (85% digitally illiterate), who have devices and connectivity but cannot navigate complex web interfaces.

## Mandatory Tech Requirements (Challenge Rules — UI Navigator Track)
- MUST use Gemini multimodal to interpret screenshots/screen recordings
- MUST output executable actions based on visual UI understanding
- MUST use Google GenAI SDK OR Agent Development Kit (ADK) — we use ADK
- MUST use at least one Google Cloud service — we use Firestore + Cloud Run
- MUST be hosted on Google Cloud
- MUST break the "text box" paradigm
- Demo video MUST be < 4 minutes showing REAL features (NO MOCKUPS)
- Public GitHub repository required
- Architecture diagram required
- Automated cloud deployment via IaC required
- Blog post required (published publicly)
- GDG profile link required

## Judging Criteria (What We're Optimizing For)
1. **"Beyond Text" Factor** (weighted): Does it break the text box paradigm? Immersive, natural?
2. **Technical Execution** (weighted): Quality code, effective Gemini usage
3. **Potential Impact** (weighted): Real-world usefulness, broad market, significant problem
4. **Innovation / Wow Factor** (weighted): Novelty, originality
5. **Presentation / Demo** (weighted): Clear problem, effective demo
6. **Bonus**: Blog post (+0.6), IaC automation (+0.2), GDG membership (+0.2)

## Architecture Decisions (LOCKED)
- **Framework**: Google ADK (Agent Development Kit) with Computer Use Toolset
- **Computer Use Model**: gemini-2.5-computer-use-preview-10-2025 (specialized for UI interaction)
- **Voice Model**: Gemini 2.5 Flash Native Audio via Live API (for voice I/O)
- **Backend**: Python 3.11+ / FastAPI
- **Frontend**: Vanilla JS + Web Audio API (lightweight operator dashboard)
- **Browser Automation**: Playwright (Chromium) — controlled by Computer Use model
- **Database**: Google Cloud Firestore (task logs, action journals, user sessions)
- **Deployment**: Google Cloud Run (containerized via Docker)
- **IaC**: Terraform + deploy.sh
- **Voice Input**: Browser microphone → AudioWorklet → PCM base64 → WebSocket → Live API
- **Voice Output**: Live API audio → WebSocket → AudioContext playback
- **Screen Capture**: Playwright screenshots after each action → sent to Computer Use model
- **Screen Display**: Live browser view streamed to user via screenshots/video feed

## DUAL-MODEL Architecture (CRITICAL — Read Carefully)
SAHAY uses TWO Gemini models working together:

### Model 1: Voice Understanding Agent (Gemini 2.5 Flash Native Audio)
- Handles ALL voice input/output via Live API streaming
- Listens to the user's natural language commands in ANY language
- Speaks back confirmations, status updates, and results
- Translates high-level user intent into structured task descriptions
- Handles barge-in (user can interrupt anytime)
- Runs via ADK streaming with LiveRequestQueue

### Model 2: Browser Control Agent (Gemini 2.5 Computer Use)
- Receives structured task descriptions from the Voice Agent
- Takes screenshots of the browser
- Analyzes UI elements visually (no DOM access needed)
- Generates executable actions (click, type, scroll, navigate)
- Executes via Playwright
- Returns action results + new screenshots back to Voice Agent

### Communication Flow:
User speaks → Voice Agent (Live API) → parses intent → sends task to Browser Agent → Browser Agent screenshots + acts → returns results → Voice Agent speaks results to user

## Code Standards
- **NO PLACEHOLDERS** — every function production-complete
- **NO MOCK DATA** — real Gemini API calls, real Firestore writes
- **NO SIMULATIONS** — every listed feature must work end-to-end
- Type hints, docstrings, structured logging on all Python code
- Config via environment variables (.env) + Pydantic Settings
- Error handling: try/except with proper propagation, never silent failures
- Comments: only for non-obvious design decisions

## Project Structure (FOLLOW EXACTLY)
sahay/
├── CLAUDE.md
├── README.md
├── LICENSE                         # Apache 2.0
├── .env.example
├── .gitignore
├── Dockerfile
├── requirements.txt
├── pyproject.toml
├── deploy/
│   ├── terraform/
│   │   ├── main.tf
│   │   ├── variables.tf
│   │   └── outputs.tf
│   └── deploy.sh
├── app/
│   ├── main.py                     # FastAPI entry + WebSocket handlers
│   ├── config.py                   # Pydantic Settings
│   ├── voice_agent/
│   │   ├── init.py
│   │   ├── agent.py                # ADK Voice Agent (Live API streaming)
│   │   └── intent_parser.py        # Extracts structured tasks from voice
│   ├── browser_agent/
│   │   ├── init.py
│   │   ├── agent.py                # ADK Browser Agent (Computer Use)
│   │   ├── playwright_computer.py  # PlaywrightComputer implementation
│   │   ├── action_executor.py      # Coordinate denormalization + execution
│   │   └── safety_gate.py          # Human confirmation for sensitive actions
│   ├── services/
│   │   ├── init.py
│   │   ├── firestore_service.py    # Task logs, action journals
│   │   ├── task_journal.py         # Visual step-by-step audit trail
│   │   └── task_templates.py       # Pre-built workflows for Indian portals
│   └── static/
│       ├── index.html              # Main dashboard
│       ├── js/
│       │   ├── app.js              # Core application logic
│       │   ├── audio-processor.js  # AudioWorklet for mic capture
│       │   └── screen-viewer.js    # Live browser view display
│       └── css/
│           └── style.css           # Accessible, large-text UI
├── tests/
│   ├── test_voice_agent.py
│   ├── test_browser_agent.py
│   ├── test_safety_gate.py
│   └── test_task_journal.py
├── docs/
│   ├── architecture.md
│   ├── architecture-diagram.png
│   ├── blog-post.md
│   └── demo-script.md
└── scripts/
└── generate_architecture_diagram.py

## Key Technical Implementation Details

### Computer Use Agent Loop (Browser Agent):
1. Receive task description from Voice Agent
2. Take screenshot of current browser state via Playwright
3. Send screenshot + task to gemini-2.5-computer-use-preview-10-2025
4. Model returns function_call(s): click_at(x,y), type_text_at(x,y,text), scroll, navigate, etc.
5. Model may also return safety_decision: "require_confirmation" → trigger Safety Gate
6. Execute action via Playwright
7. Wait for page load, take new screenshot
8. Loop from step 3 until task complete or error

### Coordinate System:
- Computer Use model outputs NORMALIZED coordinates (0-999)
- Must denormalize: actual_x = int(x / 1000 * SCREEN_WIDTH)
- Screen size: 1440 x 900 (recommended by Google)

### Supported Computer Use Actions (implement ALL):
- open_web_browser, navigate, click_at, type_text_at
- scroll_document, scroll_at, select_option_at
- key_combination, wait
- DO NOT implement: drag_and_drop (exclude it)

### Voice Agent (Live API):
- Uses Runner.run_live() with LiveRequestQueue
- response_modalities: ["AUDIO"]
- input_audio_transcription: enabled
- output_audio_transcription: enabled
- Affective dialog: enabled
- Proactive audio: enabled (only speaks when relevant)

### Safety Gate Rules:
- ALWAYS confirm before: form submission, payment, login, file download, account changes
- ALWAYS confirm before: any action with safety_decision="require_confirmation"
- Confirmation is VOICE-BASED: agent speaks what it's about to do, user says "yes" or "no"
- If user says no: agent asks what to change, modifies approach

## Git Conventions
- Conventional commits: feat:, fix:, docs:, chore:, deploy:
- Branch: main only (hackathon speed)
- Tag final: v1.0.0

## What NOT to Do
- Do NOT use adk web for production — build custom FastAPI server
- Do NOT hardcode API keys
- Do NOT use synchronous blocking calls in async pipeline
- Do NOT add React/Next.js — keep frontend vanilla JS
- Do NOT skip the Safety Gate for ANY form submission or payment
- Do NOT use localStorage in frontend
- Do NOT attempt to bypass CAPTCHAs — hand off to user gracefully