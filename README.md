# SAHAY (सहाय)

**A voice-controlled AI agent that navigates the internet for people who cannot.**

Built for the [Gemini Live Agent Challenge](https://devpost.com/) — UI Navigator Track

Author: [Sherin Joseph Roy](https://github.com/Sherin-SEF-AI)

`#GeminiLiveAgentChallenge`

## Demo Video

[![SAHAY Demo Video]
https://youtu.be/tfWZ_wPbtug
---

## Why This Exists

India digitized everything. Pensions went online. Train tickets moved to IRCTC. Aadhaar downloads require navigating UIDAI portals. Electricity bills are paid through web forms with CAPTCHAs, dropdowns, and OTP verification.

But 85% of India's elderly population cannot use any of these services. They own smartphones. They have internet connections. What they lack is the ability to navigate complex web interfaces — to find the right button, fill the right form, understand what a dropdown menu is asking for.

This is not a literacy problem. This is an interface problem. And it affects over 900 million people globally.

SAHAY solves it by replacing the interface entirely. The user speaks. SAHAY listens, opens a browser, visually understands what is on the screen, and autonomously performs the task — clicking buttons, filling forms, navigating pages — all through visual screen understanding. No DOM parsing. No site-specific scrapers. No APIs. SAHAY looks at the screen the same way a human would, and acts on it.

The name comes from Hindi: सहाय means "help."

---

## What It Does

A user says, in Hindi: "Mera Aadhaar download karna hai." SAHAY responds by voice, asks for their Aadhaar number, repeats it back for confirmation, then opens the UIDAI portal, navigates to the download page, fills in the number, requests an OTP, asks the user to read it aloud, enters it, and downloads the PDF. The user never touches the screen.

Another user says in Malayalam: "Amazon il wireless earbuds kaanichu tharoo." SAHAY searches Amazon with price filters, reads out the top results with prices and ratings, and asks if they want to buy one.

The system handles 24 languages natively. English, Hindi, Malayalam, Tamil, Telugu, Kannada, Bengali, Marathi — the user speaks in whatever language they are comfortable with, and SAHAY responds in the same language.

---

## How It Works

SAHAY runs three specialized Gemini agents that coordinate to complete any web task:

### The Planner

When a user describes a task, the Planner Agent receives it first. The Planner uses Gemini 2.5 Flash with Google Search grounding to research the task in real time. It searches for the correct website, understands the current workflow (websites change frequently), and produces a structured execution plan with numbered steps, expected outcomes, and fallback strategies.

For example, "pay my KSEB electricity bill" triggers the Planner to search for the official KSEB portal, discover the direct quick-pay URL, and create a 4-step plan: navigate to quick-pay page, enter consumer number, verify details, confirm payment.

The Planner never guesses URLs. It always searches.

### The Browser Agent

The Browser Agent receives the plan and executes it step by step. It controls a real Chromium browser through Playwright, but it does not read the DOM or use CSS selectors to understand the page. Instead, it sends screenshots to Gemini's Computer Use model, which analyzes the visual layout — identifying buttons, text fields, links, and navigation elements the same way a human would by looking at the screen.

The Computer Use model returns coordinates for where to click, what text to type, and where to scroll. The Browser Agent executes these actions through Playwright, takes a new screenshot, and sends it back to the model for the next step.

This approach works on any website without modification. Government portals, e-commerce sites, banking interfaces — the agent sees and interacts with whatever is on screen.

### The Voice Agent

The Voice Agent handles all communication with the user. It runs on Gemini 2.5 Flash Native Audio through the Live API, supporting bidirectional streaming audio. The user can speak naturally, interrupt mid-sentence (barge-in), and receive spoken responses in real time.

The Voice Agent also enforces sensitive input verification. When the user provides personal data — phone numbers, Aadhaar numbers, email addresses — the agent repeats the information back and waits for explicit confirmation before proceeding. This prevents data entry errors that the user cannot see or correct on their own.

### Smart Browser Selection

SAHAY automatically chooses between headed and headless browser modes depending on the target website. Sites known to block automated browsers (IRCTC, MakeMyTrip, Flipkart) get a visible Chromium window that bypasses bot detection. Simple sites (Wikipedia, Amazon) use headless mode for speed. The decision is made based on both the URL and the task description.

---

## Architecture

![SAHAY Architecture Diagram](docs/architecture-diagram.png)

```
User speaks
  |
  v
Voice Agent (Gemini 2.5 Flash Native Audio — Live API)
  |
  |-- Parses intent, gathers required inputs
  |-- Confirms sensitive data by voice
  |
  v
Planner Agent (Gemini 2.5 Flash — Google Search Grounding)
  |
  |-- Searches internet for correct website
  |-- Creates step-by-step execution plan
  |-- Replans on failure from current state
  |
  v
Browser Agent (Gemini Computer Use — Screenshot Analysis)
  |
  |-- Receives plan, navigates to discovered URL
  |-- Screenshots -> Gemini analyzes -> Returns actions
  |-- Playwright executes: click, type, scroll, navigate
  |-- Loops until task complete or needs user input
  |
  v
Results spoken back to user via Voice Agent
Task logged to Firestore with full audit trail
```

See [docs/architecture.md](docs/architecture.md) for detailed documentation.

---

## Features

**Core**

- Three-agent orchestration: Planner, Browser, Voice
- Dynamic task planning via Google Search grounding — no hardcoded URLs
- Visual screen understanding via Gemini Computer Use — no DOM parsing
- Multilingual voice I/O in 24 languages with automatic language matching
- Adaptive replanning when steps fail

**Safety and Trust**

- Safety Gate: voice or button confirmation before every login, payment, form submission, or download
- Sensitive input verification: agent repeats back phone numbers, Aadhaar numbers, emails and waits for "yes"
- Take Over mode: user clicks directly on the browser screenshot to help with passwords or CAPTCHAs
- Visual confirmation screenshot shown during Safety Gate

**Intelligence**

- Smart browser selection: headed for bot-protected sites, headless for speed
- Smart retry with screenshot analysis: on failure, sends screenshot to Gemini Flash for recovery strategy
- Loop detection: stops agent if 6 consecutive screenshots are identical
- Form memory: remembers user details (name, phone, email) for future tasks
- Task queue: splits compound requests ("search for X then find Y") and executes sequentially

**User Experience**

- Live browser view with real-time screenshot streaming
- Plan preview panel showing discovered URL, confidence level, and step progress
- Agent reasoning display with collapsible thought cards
- Thinking pulse animation between agent actions
- Stop button (Escape key) for instant task cancellation
- Rollback: type "go back" or "undo" to navigate to previous page
- Accessible UI: 18px+ fonts, high contrast, large touch targets

---

## Technology Stack

| Component | Technology | Purpose |
|---|---|---|
| Agent Framework | Google ADK 1.27 | Agent orchestration, Live API streaming |
| Computer Use | gemini-2.5-computer-use-preview-10-2025 | Visual screen analysis and action generation |
| Voice | gemini-2.5-flash-native-audio | Bidirectional audio streaming, 24 languages |
| Planner | gemini-2.5-flash + Google Search | URL discovery and task planning |
| Self-Healing | gemini-2.5-flash | Screenshot analysis for failure recovery |
| Browser | Playwright (Chromium) | Action execution on real web pages |
| Backend | Python 3.12, FastAPI | WebSocket server, REST API |
| Frontend | Vanilla JavaScript, Web Audio API | Dashboard, audio capture/playback |
| Database | Google Cloud Firestore | Task logs, session state, audit trails |
| Hosting | Google Cloud Run | Containerized deployment |
| IaC | Terraform + deploy.sh | Automated infrastructure |

---

## Running Locally

### Prerequisites

- Python 3.11 or higher
- A Google Cloud project with Vertex AI API enabled
- Authenticated `gcloud` CLI (run `gcloud auth application-default login`)
- A Gemini API key (for the Computer Use model, which requires AI Studio)

### Setup

```bash
git clone https://github.com/Sherin-SEF-AI/Sahay-Voice-First-Digital-Navigator.git
cd Sahay-Voice-First-Digital-Navigator

python -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
playwright install chromium

cp .env.example .env
```

Edit `.env` and set:
```
GOOGLE_CLOUD_PROJECT=your-project-id
GOOGLE_API_KEY=your-gemini-api-key
BROWSER_HEADLESS=false
```

### Start

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8080
```

Open `http://localhost:8080` in your browser. Allow microphone access when prompted.

### Usage

Click the microphone button and speak your request:

- "Find wireless earbuds under 1000 rupees on Amazon"
- "Go to Wikipedia and tell me about the Taj Mahal"
- "Download my Aadhaar card from UIDAI"
- "YouTube par yoga videos search karo" (Hindi)
- "Amazon il headphones kaanichu tharoo" (Malayalam)

The browser will open, navigate to the right website, and complete the task. Results are spoken back in the same language you used.

To stop a running task, press Escape or click the Stop button.
To go back, type "go back" or "undo" in the text box.
To help the agent with passwords or CAPTCHAs, click "Take Over" and click directly on the browser screenshot.

---

## Deploying to Google Cloud Run

### Using the deployment script

```bash
chmod +x deploy/deploy.sh
./deploy/deploy.sh YOUR_PROJECT_ID us-central1
```

### Using Terraform

```bash
cd deploy/terraform
terraform init
terraform plan -var="project_id=YOUR_PROJECT_ID"
terraform apply -var="project_id=YOUR_PROJECT_ID"
```

Note: Cloud Run deployment uses headless browser mode. Set `BROWSER_HEADLESS=true` in the Cloud Run environment variables.

---

## Project Structure

```
sahay/
├── app/
│   ├── main.py                         FastAPI server, WebSocket handlers, orchestration
│   ├── config.py                       Environment configuration (Pydantic Settings)
│   ├── orchestrator.py                 Three-agent coordinator
│   ├── planner_agent/
│   │   ├── agent.py                    Gemini Flash + Google Search grounding
│   │   └── plan_schema.py             TaskPlan, PlanStep models
│   ├── voice_agent/
│   │   ├── agent.py                    Live API voice agent with language matching
│   │   └── intent_parser.py           Intent classification
│   ├── browser_agent/
│   │   ├── agent.py                    Computer Use agent with DOM tools
│   │   ├── playwright_computer.py     Playwright wrapper, smart browser selection
│   │   ├── action_executor.py         Coordinate denormalization
│   │   ├── safety_gate.py             Sensitive action detection
│   │   └── self_healer.py             Screenshot-based failure recovery
│   ├── services/
│   │   ├── firestore_service.py       Task persistence
│   │   ├── task_journal.py            Visual audit trail with GPA engine
│   │   ├── entity_extractor.py        Structured data extraction from results
│   │   ├── workflow_recorder.py       Record and replay task workflows
│   │   ├── guardian_service.py        Family guardian mode
│   │   └── screenshot_diff.py        Token-saving screenshot comparison
│   └── static/
│       ├── index.html                 Dashboard
│       ├── js/app.js                  Application logic
│       ├── js/screen-viewer.js        Live browser view, safety gate UI
│       ├── js/audio-processor.js      Microphone capture (AudioWorklet)
│       └── css/style.css              Accessible, high-contrast design
├── deploy/
│   ├── deploy.sh                      Automated Cloud Run deployment
│   └── terraform/                     Infrastructure as Code
├── tests/                             Test suite
├── docs/
│   ├── architecture.md                Detailed architecture documentation
│   ├── architecture-diagram.png       System diagram
│   ├── blog-post.md                   Hackathon blog post
│   └── demo-script.md                Demo video script
├── Dockerfile                         Multi-stage container build
├── requirements.txt                   Python dependencies
└── README.md
```

---

## Testing

### Automated Tests

```bash
python -m pytest tests/ -v
```

The test suite covers the browser agent, voice agent, safety gate, task planner, and orchestration logic.

### Reproducible Manual Testing

Follow these steps to verify the system works end to end.

**Prerequisites:**
- Python 3.11+
- Google Cloud project with Vertex AI API enabled
- Valid `GOOGLE_API_KEY` or Application Default Credentials configured
- Microphone access in your browser

**Step 1: Start the server**

```bash
cp .env.example .env
# Fill in your GOOGLE_API_KEY and GOOGLE_CLOUD_PROJECT in .env

pip install -r requirements.txt
playwright install chromium

python -m uvicorn app.main:app --host 0.0.0.0 --port 8080
```

**Step 2: Open the dashboard**

Open `http://localhost:8080` in Chrome or Edge. Allow microphone access when prompted. You should see the SAHAY dashboard with a browser viewport on the left and a conversation panel on the right.

**Step 3: Test voice command (English)**

Speak into your microphone: "Find earbuds under 1000 rupees on Amazon"

Expected result: The Planner Agent searches for Amazon, creates a plan, and the Browser Agent navigates to Amazon search results with the price filter applied. The plan steps appear in the right panel and turn green as they complete.

**Step 4: Test voice command (Hindi)**

Speak: "Wikipedia par Taj Mahal ke baare mein batao"

Expected result: The agent responds in Hindi, navigates to the Wikipedia article for Taj Mahal, and speaks a summary back in Hindi.

**Step 5: Test Safety Gate**

Speak: "Log in to DigiLocker"

Expected result: The agent navigates to DigiLocker. When it reaches the login page, a Safety Gate overlay appears asking for confirmation before proceeding with the login action. Click "Yes, Proceed" to continue or "Cancel" to abort.

**Step 6: Test Stop and Rollback**

Start any task. Press the Escape key to stop it immediately. Then type "go back" in the text input to undo the last navigation.

**Step 7: Test Take Over**

Click the "Take Over" button during any task. Click directly on the browser screenshot to interact with the page manually. Press Escape to return control to the agent.

**Sample tasks that work reliably:**
- "Find the best rated power bank under 1500 rupees on Amazon"
- "Go to Wikipedia and tell me about ISRO"
- "Search for yoga videos on YouTube"
- "Amazon il wireless earbuds kaanichu tharoo" (Malayalam)
- "Flipkart par Samsung 5G phone dikhao" (Hindi)

---

## Limitations

SAHAY works best on websites with standard HTML forms and clear visual layouts. Some websites present challenges:

- Sites with aggressive bot detection (some banking portals) may block even headed browsers
- CAPTCHAs cannot be solved automatically — SAHAY asks the user to help via Take Over mode
- The Computer Use model occasionally misclicks on very small or overlapping UI elements
- Complex single-page applications with custom JavaScript widgets (autocomplete dropdowns, date pickers) sometimes require multiple attempts

These limitations are inherent to the visual-only approach. The tradeoff is universality — SAHAY works on any website without site-specific configuration.

---

## What Makes This Different

Most accessibility tools and automation agents rely on DOM access, APIs, or site-specific integrations. They break when websites change their HTML structure. They require maintenance for every new portal.

SAHAY takes a fundamentally different approach. It sees the screen. It understands what a button looks like, where a text field is, what a navigation menu contains — all from pixels. This means it works on any website, in any language, without any prior integration.

The three-agent architecture (Planner + Browser + Voice) separates concerns cleanly. The Planner handles the "what" and "where" using live internet search. The Browser handles the "how" using visual understanding. The Voice handles the "who" — speaking to the user in their own language with patience, clarity, and respect.

This is not a chatbot. It is a pair of hands for people who need them.

---

## License

Apache License 2.0. See [LICENSE](LICENSE).

---

Built for the Gemini Live Agent Challenge by [Sherin Joseph Roy](https://github.com/Sherin-SEF-AI), DeepMost AI.

Live deployment: https://sahay-1073092648184.us-central1.run.app
