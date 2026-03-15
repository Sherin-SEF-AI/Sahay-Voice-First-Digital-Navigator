# SAHAY Architecture

## Overview

SAHAY uses a **dual-model architecture** where two specialized Gemini models collaborate:

1. **Voice Agent** (Gemini 2.5 Flash Native Audio) — handles all voice I/O
2. **Browser Agent** (Gemini 2.5 Computer Use) — navigates web interfaces visually

These agents communicate through the FastAPI server, which orchestrates task execution and streams results to the frontend dashboard.

## System Architecture Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                    USER (Voice + Screen)                     │
│                                                             │
│  ┌──────────┐  Audio/Text   ┌──────────────────────────┐   │
│  │ Mic/     │──────────────→│   Browser Dashboard      │   │
│  │ Speaker  │←──────────────│   (Live Screenshot View)  │   │
│  └──────────┘  Audio/Text   └──────────────────────────┘   │
│       ↕ WebSocket /ws/voice        ↕ WebSocket /ws/screen   │
└───────┼────────────────────────────┼────────────────────────┘
        │                            │
┌───────┼────────────────────────────┼────────────────────────┐
│       ↓         FastAPI Server     ↓                        │
│  ┌─────────────────────────────────────────────────────┐    │
│  │              WebSocket Handlers                      │    │
│  │  /ws/voice: Audio ↔ Live API                        │    │
│  │  /ws/screen: Screenshot stream                       │    │
│  └──────────┬──────────────────────┬───────────────────┘    │
│             │                      │                        │
│  ┌──────────▼──────────┐  ┌───────▼──────────────────┐     │
│  │   Voice Agent       │  │   Browser Agent           │     │
│  │   (ADK + Live API)  │  │   (ADK + Computer Use)    │     │
│  │                     │  │                            │     │
│  │ • Gemini 2.5 Flash  │  │ • Gemini 2.5 Computer Use │     │
│  │   Native Audio      │  │ • PlaywrightComputer      │     │
│  │ • LiveRequestQueue  │  │ • ComputerUseToolset      │     │
│  │ • Intent parsing    │  │ • Safety Gate             │     │
│  │ • Task coordination │  │ • Action Executor         │     │
│  └──────────┬──────────┘  └───────┬──────────────────┘     │
│             │ request_browser_     │                        │
│             │ action()             │                        │
│             └──────────────────────┘                        │
│                      │                                      │
│  ┌───────────────────▼─────────────────────────────────┐    │
│  │              Services Layer                          │    │
│  │  ┌──────────────┐ ┌────────────┐ ┌──────────────┐   │    │
│  │  │ Firestore    │ │  Task      │ │   Task       │   │    │
│  │  │ Service      │ │  Journal   │ │   Templates  │   │    │
│  │  └──────┬───────┘ └──────┬─────┘ └──────────────┘   │    │
│  └─────────┼────────────────┼──────────────────────────┘    │
│            │                │                               │
└────────────┼────────────────┼───────────────────────────────┘
             ↓                ↓
┌────────────────────────────────────────────────┐
│          Google Cloud                           │
│  ┌──────────────┐  ┌────────────────────────┐  │
│  │  Firestore   │  │  Vertex AI / Gemini    │  │
│  │  (Task Logs) │  │  (Model Inference)     │  │
│  └──────────────┘  └────────────────────────┘  │
│  ┌──────────────┐                              │
│  │  Cloud Run   │  (Hosts the application)     │
│  └──────────────┘                              │
└────────────────────────────────────────────────┘
```

## Data Flow

### Voice Pipeline (User → Agent → User)

```
1. User speaks into microphone
2. Browser captures audio via getUserMedia()
3. AudioWorklet converts to PCM 16-bit, 16kHz, mono
4. Base64-encoded PCM sent via WebSocket /ws/voice
5. FastAPI handler pushes to LiveRequestQueue
6. ADK Runner.run_live() sends to Gemini Live API
7. Gemini processes audio, generates response
8. Response audio streamed back as events
9. Audio events sent via WebSocket to browser
10. Browser plays audio via AudioContext
```

### Browser Automation Pipeline

```
1. Voice Agent calls request_browser_action(task, url)
2. FastAPI handler receives task
3. Intent parser extracts structured TaskIntent
4. Task templates provide context hints
5. Browser Agent receives augmented task
6. Loop:
   a. PlaywrightComputer takes screenshot
   b. Screenshot sent to Gemini Computer Use model
   c. Model returns actions (click, type, scroll, etc.)
   d. Safety Gate checks if confirmation needed
   e. If safe: execute via Playwright
   f. If needs confirmation: ask user via Voice Agent
   g. Screenshot streamed to /ws/screen for live view
   h. Journal entry recorded
   i. Repeat until TASK COMPLETE or TASK FAILED
7. Result returned to Voice Agent
8. Voice Agent speaks result to user
9. Task logged to Firestore
```

### Safety Gate Flow

```
Action received from Computer Use model
          │
          ▼
    ┌─────────────┐
    │ Check model  │──── "require_confirmation" ──→ NEEDS_CONFIRMATION
    │ safety field │
    └──────┬──────┘
           │ no flag
           ▼
    ┌─────────────┐
    │ Check action │──── Contains sensitive keyword ──→ NEEDS_CONFIRMATION
    │ keywords     │    (submit, pay, login, etc.)
    └──────┬──────┘
           │ no match
           ▼
    ┌─────────────┐
    │ Check URL    │──── Banking/gov domain ──→ NEEDS_CONFIRMATION
    │ domain       │
    └──────┬──────┘
           │ no match
           ▼
         SAFE → Execute automatically
```

## Firestore Data Model

### Collection: `sahay_tasks`

```json
{
  "id": "uuid",
  "user_session_id": "uuid",
  "timestamp": 1710000000.0,
  "task_description": "Book a train to Chennai",
  "language": "hi",
  "status": "completed | in_progress | failed",
  "steps": [
    {
      "step_number": 1,
      "timestamp": 1710000001.0,
      "action_type": "navigate",
      "action_description": "Opening IRCTC website",
      "url": "https://www.irctc.co.in",
      "success": true,
      "screenshot": "base64..."
    }
  ],
  "outcome": "Train booked: PNR 1234567890",
  "screenshots_count": 12,
  "completed_at": 1710000060.0
}
```

## Coordinate System

The Gemini Computer Use model outputs **normalized coordinates** (0-999). These are denormalized to actual screen pixels before Playwright execution:

```
actual_x = int(normalized_x / 1000 * SCREEN_WIDTH)   # 1440px
actual_y = int(normalized_y / 1000 * SCREEN_HEIGHT)   # 900px
```

## Cloud Run Deployment

- **Execution Environment**: Gen2 (required for Playwright/Chromium)
- **Memory**: 2Gi (Chromium is memory-intensive)
- **CPU**: 4 vCPUs
- **Timeout**: 3600s (tasks can be long-running)
- **Scaling**: 0-3 instances (min 0 for cost efficiency)

The Docker image includes Chromium installed via Playwright, with all required system dependencies for headless browser operation.
