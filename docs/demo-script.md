# SAHAY Demo Script (< 4 minutes)

## Setup Before Recording
- Open browser to `http://localhost:8080`
- Ensure mic is enabled
- Clear transcript panel
- Browser panel shows Google homepage

---

## SCENE 1: The Problem (15 sec)
**[Voiceover — do NOT show app yet]**
> "900 million people worldwide are digitally illiterate. In India, 85% of elderly
> citizens have smartphones but cannot navigate complex web interfaces. They have
> connectivity — but no ability to use it. SAHAY changes that. Just speak.
> SAHAY does the clicking."

---

## SCENE 2: Voice-Controlled Web Navigation (45 sec)
**[Shows: Voice input, live browser, click preview, Hindi]**

**Say (English):**
> "Search for weather in Delhi"

**What viewers see:**
- Voice waveform activates
- Transcript shows user speech in real-time
- Browser opens Google
- Pulsing red dot on search box (Click Preview)
- Agent types "weather in Delhi"
- Results appear, agent reads weather aloud
- Diff badge: "12% changed — 840 tokens saved"

**Then say (Hindi):**
> "Amazon par headphones dikhao 500 rupaye se kam"

**What viewers see:**
- Hindi transcribed live
- Browser navigates to Amazon.in
- Agent searches, filters by price
- Click preview dots on every interaction
- Agent speaks results back in Hindi

---

## SCENE 3: Safety Gate — The Trust Layer (45 sec)
**[Shows: Voice confirmation before sensitive action]**

**Say:**
> "Log in to my Gmail account"

**What viewers see:**
- Browser goes to Gmail login
- Agent types email
- On password field — **SAFETY GATE FIRES:**
  - Dark overlay with amber warning card
  - Text: "I'm about to enter your password on GOOGLE. Should I go ahead?"
  - 30-second countdown timer bar
  - Two buttons: "Yes, Proceed" / "Cancel"

**Say:**
> "Haan, kar do" *(Yes, do it)*

**What viewers see:**
- Gate approves via voice
- Agent continues action
- Transcript: "Safety check approved"

**[Voiceover]:**
> "SAHAY never submits forms, makes payments, or enters credentials
> without your explicit voice permission. Every sensitive action pauses and asks."

---

## SCENE 4: Smart Shopping Task (45 sec)
**[Shows: Multi-step navigation, entity extraction]**

**Say:**
> "Flipkart pe sabse sasta Samsung 5G phone dikhao"
> *(Show me the cheapest Samsung 5G phone on Flipkart)*

**What viewers see:**
- Browser opens Flipkart
- Agent searches "Samsung 5G phone"
- Applies price filter (low to high)
- Scrolls through results
- Speaks: "Maine Samsung Galaxy M14 5G dhundha, Rs. 10,499 mein"
- Entity extraction: product name, price, rating, URL

---

## SCENE 5: Guardian Mode + Spending Cap (30 sec)
**[Shows: Family safety controls]**

**[Voiceover]:**
> "Guardian Mode lets a family member set up SAHAY remotely —
> restricting websites, setting spending caps, getting notifications."

**Say:**
> "Buy this phone"

**What viewers see:**
- **SPENDING BLOCKED** toast:
  - "Payment of Rs. 10,499 blocked. Exceeds guardian spending cap of Rs. 2,000"
- Agent speaks: "I cannot buy this. Your spending limit is 2000 rupees."

---

## SCENE 6: Architecture (15 sec)
**[Show architecture diagram]**

**[Voiceover]:**
> "Two Gemini models work together — Flash for native voice in any language,
> Computer Use for visual screen navigation. No DOM scraping, no brittle selectors.
> It sees the screen like a human. Deployed on Cloud Run, audited in Firestore."

---

## TOTAL: ~3 min 15 sec

---

## Backup Tasks (if a site blocks)
- "Search for train tickets from Delhi to Mumbai"
- "Wikipedia par Taj Mahal ke baare mein batao"
- "YouTube par cooking recipe search karo"
- "Google par aaj ka cricket score dikhao"

## Features Hit Per Scene
| Feature | Scene |
|---------|-------|
| Voice Control (English) | 2 |
| Voice Control (Hindi) | 2, 3, 4 |
| Click Preview (pulsing dot) | 2, 3, 4 |
| Screenshot Diff | 2 |
| Safety Gate (voice confirm) | 3 |
| Entity Extraction | 4 |
| Guardian Mode / Spending Cap | 5 |
| Architecture / Gemini / Cloud | 6 |

## Demo Tips
1. **Start with impact** — problem statement hooks judges
2. **Hindi voice** is the wow moment — show it early
3. **Safety Gate** = trust differentiator — let it breathe on screen 3-4 sec
4. **Don't rush** — let each feature register
5. **Narrate** as agent works — "watch the red dot appear before it clicks"
6. **If a site blocks**: "SAHAY detects blocked sites and tells the user — no silent failures"
