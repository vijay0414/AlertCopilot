# 🏗️ Alert Arbitration Co-Pilot

> **An edge-AI system that monitors heavy machinery sensors in real time, arbitrates competing safety alerts, and surfaces ONE prioritized plain-language instruction to the operator — eliminating alert fatigue.**

Built as a live hackathon demo. Streams real phone sensor data (tilt + sound) into a FastAPI backend that runs a 5-step scoring pipeline, then renders the result on a React dashboard.

---

## Table of Contents

- [What It Does](#what-it-does)
- [Architecture](#architecture)
- [Project Structure](#project-structure)
- [Tech Stack](#tech-stack)
- [Quick Start](#quick-start)
- [Configuration](#configuration)
- [API Reference](#api-reference)
- [Pipeline Deep-Dive](#pipeline-deep-dive)
- [Demo Scenarios](#demo-scenarios)
- [Phone Sensor Integration](#phone-sensor-integration)
- [Frontend Dashboard](#frontend-dashboard)
- [Troubleshooting](#troubleshooting)

---

## What It Does

On a real construction site, an excavator can trigger four alerts simultaneously — proximity sensor, tilt warning, engine overload, and operator fatigue — all at the same time. Without arbitration, the operator sees all four and has to decide which matters most under pressure.

**Alert Arbitration Co-Pilot** does that work automatically:

1. Ingests sensor readings from physical sensors, phone browser, or demo scenarios
2. Scores each signal with rule-based edge-AI (no ML model — explainable and fast)
3. Applies context multipliers based on operating mode (idle / transport / active digging)
4. Runs priority-queue suppression — only the **top alert** stays active
5. Calls Groq LLM (or uses local fallback templates) to generate a single operator instruction

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        INPUT SOURCES                                │
│                                                                     │
│  📱 Phone Browser      🎛 Demo Control Panel    🔌 Real Sensors     │
│  (tilt + mic)          (named scenarios)         (POST /sensors/ingest) │
└────────────┬───────────────────┬────────────────────┬──────────────┘
             │                   │                    │
             ▼                   ▼                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│                     FastAPI Backend  (port 8000)                    │
│                                                                     │
│  Step 1 ── Edge Scoring          scoring.py                         │
│            Rule-based risk score 0–100 per signal                   │
│                  ↓                                                  │
│  Step 2 ── Context Filter        scoring.py                         │
│            Multiply by task_mode  (idle×0.4 / transport×1.0 / active×1.2) │
│                  ↓                                                  │
│  Step 3 ── Suppression Queue     suppression.py                     │
│            Priority queue — only TOP alert is active (≥25 threshold) │
│            Alerts ≥10 shown in suppressed list; <10 omitted          │
│                  ↓                                                  │
│  Step 4 ── Fleet Logger          storage.py                         │
│            Append every alert to data/fleet_log.jsonl               │
│                  ↓                                                  │
│  Step 5 ── LLM Instruction       llm.py                             │
│            Groq API → 1 sentence | fallback template dict           │
└─────────────────────────┬───────────────────────────────────────────┘
                          │  REST / polling
                          ▼
┌─────────────────────────────────────────────────────────────────────┐
│                   React Dashboard  (port 5173)                      │
│  Active instruction card  │  Sensor readings  │  Suppressed queue   │
│  Phone live badge         │  Fleet stats      │  Demo control panel │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Project Structure

```
AlertCopilot/
│
├── backend/                    # FastAPI application
│   ├── main.py                 # App entrypoint, all route definitions
│   ├── scoring.py              # Step 1+2: raw scoring + context filter
│   ├── suppression.py          # Step 3: priority queue, ACTIVE_THRESHOLD=25
│   ├── storage.py              # Step 4: machine state store + fleet logger
│   ├── llm.py                  # Step 5: Groq LLM + fallback templates
│   ├── models.py               # Pydantic models (request/response shapes)
│   ├── simulator.py            # Named scenario generators (demo data)
│   ├── events.py               # MQTT-shaped event bus (in-memory)
│   ├── requirements.txt        # Python dependencies
│   ├── .env.example            # Copy to .env and fill in your Groq key
│   └── data/
│       └── fleet_log.jsonl     # Append-only alert log (auto-created)
│
├── frontend/                   # React + Vite dashboard
│   ├── src/
│   │   ├── App.jsx             # Main component, all UI logic
│   │   ├── App.css             # All styles (dark theme, responsive)
│   │   ├── api.js              # Backend fetch helpers
│   │   ├── main.jsx            # React root
│   │   └── index.css           # Global resets
│   ├── .env.example            # VITE_API_URL override for ngrok/tunnel
│   ├── vite.config.js
│   └── package.json
│
├── static/
│   └── phone-sensor-hub.html   # Mobile sensor page (served at /phone)
│
├── phone-sensor-hub.html       # Project-root copy (fallback)
├── start_demo.py               # One-command launcher with IP detection
└── README.md
```

---

## Tech Stack

| Layer | Technology |
|---|---|
| **Backend API** | Python 3.11, FastAPI, Uvicorn |
| **Data validation** | Pydantic v2 |
| **LLM** | Groq API (`llama-3.1-8b-instant`) with local fallback templates |
| **HTTP client** | httpx (async, with 2s timeout) |
| **Frontend** | React 19, Vite 8, vanilla CSS (no UI library) |
| **Phone sensors** | Web APIs: `DeviceOrientationEvent` (tilt), `getUserMedia` + `AnalyserNode` (mic) |
| **State** | In-memory (RAM) — no database required |
| **Fleet log** | Append-only JSON Lines file (`data/fleet_log.jsonl`) |
| **Event bus** | In-process async pub/sub (MQTT-shaped topics, real broker optional) |

---

## Quick Start

### Prerequisites

- Python 3.10+
- Node.js 18+
- A free Groq API key from [console.groq.com](https://console.groq.com) *(optional — fallback templates work without it)*

---

### 1. Clone & set up the backend

```bash
# Install Python dependencies
cd backend
pip install -r requirements.txt

# Create your environment file
cp .env.example .env
```

Open `backend/.env` and set your Groq API key:

```env
GROQ_API_KEY=gsk_your_real_key_here
```

> **Without a key:** the backend starts normally and uses the local fallback template dictionary. The instruction card will show `📋 Fallback Template` instead of `🤖 Groq LLM`.

---

### 2. Start the backend

**Option A — with phone sensor support (recommended for demo):**

```bash
# From the project root
python start_demo.py
```

This auto-detects your local IP, prints the phone URL, and starts uvicorn on `0.0.0.0:8000`.

**Option B — backend only:**

```bash
cd backend
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

---

### 3. Start the frontend dashboard

```bash
cd frontend
npm install      # first time only
npm run dev
```

Open **[http://localhost:5173](http://localhost:5173)** in your browser.

---

### 4. (Optional) Connect your phone as a live sensor

1. Run `python start_demo.py` — it prints your local IP, e.g. `192.168.1.13`
2. On your phone (same WiFi), open: `http://192.168.1.13:8000/phone`
3. Tap **Start sending sensor data**
4. The dashboard shows a green **Phone live** badge in the header

> **iOS Safari / HTTPS note:** Motion and microphone APIs require a secure context on iOS.
> If permissions don't prompt, use a free tunnel:
> ```bash
> ngrok http 8000
> # Then open  https://xxxx.ngrok-free.app/phone  on your phone
> ```

---

## Configuration

### Backend — `backend/.env`

| Variable | Default | Description |
|---|---|---|
| `GROQ_API_KEY` | *(required for LLM)* | Get free at [console.groq.com](https://console.groq.com) |
| `GROQ_MODEL` | `llama-3.1-8b-instant` | Any Groq-hosted model |
| `LLM_TIMEOUT_SECONDS` | `2` | Seconds before falling back to template |
| `FLEET_LOG_PATH` | `data/fleet_log.jsonl` | Path for the append-only alert log |

### Frontend — `frontend/.env`

| Variable | Default | Description |
|---|---|---|
| `VITE_API_URL` | `http://localhost:8000` | Override to point at an ngrok URL |

---

## API Reference

All endpoints are documented interactively at **[http://localhost:8000/docs](http://localhost:8000/docs)**.

### Sensors

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/sensors/ingest` | Ingest a partial sensor reading (fields merge with existing state) |
| `POST` | `/sensors/phone-ingest` | Slim endpoint for the phone page (`{machine_id, tilt, engine}`) |
| `POST` | `/sensors/simulate` | Trigger a named demo scenario |
| `GET` | `/sensors/scenarios` | List available scenario names |
| `GET` | `/sensors/{machine_id}/latest` | Latest merged reading + phone-connected flag |
| `GET` | `/phone` | Serves `phone-sensor-hub.html` (mobile sensor page) |

### Alerts

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/alerts/{machine_id}/queue` | Full priority queue (active + suppressed alerts) |
| `GET` | `/alerts/{machine_id}/instruction` | Plain-language operator instruction (LLM or fallback) |

### Fleet

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/fleet/log?n=50` | Last N entries from `fleet_log.jsonl` |
| `GET` | `/fleet/summary` | Aggregate stats (total, shown, suppressed, by type, LLM vs fallback) |

### System

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/health` | Liveness probe — always 200 |
| `GET` | `/` | API root with link list |

---

### Example curl commands

```bash
# Trigger the flagship 4-alert demo
curl -X POST http://localhost:8000/sensors/simulate \
  -H "Content-Type: application/json" \
  -d '{"machine_id": "EX-04", "scenario": "multi_alert"}'

# Get the active operator instruction
curl http://localhost:8000/alerts/EX-04/instruction

# Get the suppression queue
curl http://localhost:8000/alerts/EX-04/queue

# Ingest a real sensor reading (partial — merges with existing state)
curl -X POST http://localhost:8000/sensors/ingest \
  -H "Content-Type: application/json" \
  -d '{"machine_id": "EX-04", "tilt": 22, "engine": 80, "task_mode": "active"}'

# Reset to all-clear
curl -X POST http://localhost:8000/sensors/simulate \
  -H "Content-Type: application/json" \
  -d '{"machine_id": "EX-04", "scenario": "all_clear"}'
```

---

## Pipeline Deep-Dive

### Step 1 — Raw Scoring (`scoring.py`)

Each sensor signal produces an independent risk score from 0 to 100 using rule-based piecewise curves:

| Signal | Inputs | Key thresholds |
|---|---|---|
| **Proximity** | `proximity` (metres) | `<0.5m → 100`, `<1m → 90+`, `<3m → 60+`, `>10m → 5` |
| **Tilt** | `tilt` (degrees) | `>30° → 100`, `>25° → 90+`, `>15° → 60+`, `<3° → 5` |
| **Engine** | `engine_temp` (°C) + `engine_load` (%) | Takes the worse; `>110°C → 90+`, `>95% load → 90+` |
| **Fatigue** | `fatigue` (0–100) | Near 1:1; `≥70 → ×1.1 amplify`, `<40 → ×0.5 dampen` |

### Step 2 — Context Filter (`scoring.py`)

Scores are multiplied by a `task_mode` factor before entering the queue:

| Alert type | Idle | Transport | Active (digging) |
|---|---|---|---|
| Proximity | ×1.0 | ×1.0 | **×1.2** |
| Tilt | ×1.0 | ×1.0 | **×1.2** |
| Engine | **×0.4** | ×1.0 | ×1.0 |
| Fatigue | **×0.4** | ×1.0 | ×1.0 |

> In idle mode, tilt/proximity scores above 60 keep their full weight regardless.

### Step 3 — Suppression Queue (`suppression.py`)

- Alerts are sorted descending by adjusted score
- **Only the TOP alert is active** — all others are suppressed
- An alert must score **≥ 25** (`ACTIVE_THRESHOLD`) to trigger an instruction
- Alerts scoring **< 10** (`QUEUE_THRESHOLD`) are omitted from the suppressed list entirely
- When no alert clears the threshold, the instruction endpoint returns `{"active": false, "instruction_text": "All clear - no active alerts"}`

### Step 4 — Scenario Hold (`storage.py`)

When a scenario button is clicked in the dashboard, the fields that scenario set are **protected for 10 seconds** (`SCENARIO_HOLD_S`). Phone streaming cannot overwrite them during this window. Clicking **Reset / All Clear** releases the hold immediately.

### Step 5 — LLM Instruction (`llm.py`)

```
Active alert found (score ≥ 25)
        │
        ├─ GROQ_API_KEY present? ──YES──→ POST to Groq API (2s timeout)
        │                                    │
        │                          ┌─────────┴──────────┐
        │                       success              failure / timeout
        │                          │                    │
        │                   return text            fall through
        │
        └─ NO KEY or timeout ──→ look up fallback template dict
                                  (alert_type, severity_tier) → sentence
```

Instruction source is returned as `"llm"` or `"fallback"` and shown on the dashboard card.

---

## Demo Scenarios

Trigger these from the dashboard control panel or via `POST /sensors/simulate`:

| Scenario | ID | Description |
|---|---|---|
| ⚡ Multi-Alert | `multi_alert` | **Flagship demo** — 4 concurrent risks: proximity 0.8m + tilt 18° + engine 105°C/91% + fatigue 72. Arbitration picks ONE. |
| 📐 Tilt Critical | `tilt_critical` | 28° tilt in active mode — score 90+, instant instruction |
| 📡 Proximity Warning | `proximity_warning` | 2.3m obstacle in transport mode — score ~72 |
| 🌡️ Engine Overload | `engine_overload` | 112°C / 96% load — score 90+ |
| 😴 Fatigue High | `fatigue_high` | Fatigue 85/100 in idle mode — still clears threshold after 0.4× dampening |
| ✅ Reset / All Clear | `all_clear` | All readings nominal — clears scenario hold, returns to all-clear state |

---

## Phone Sensor Integration

The phone page (`/phone`) uses two Web APIs:

- **DeviceOrientation API** — reads `beta` (front-back tilt) and `gamma` (left-right tilt)
- **Web Audio API** — captures mic level via `AnalyserNode`, sends average byte value × 4 as engine-load proxy

### Payload sent every 500ms

```json
{
  "machine_id": "EX-04",
  "tilt": 18.5,
  "engine": 64
}
```

- `tilt` is `max(|beta|, |gamma|)` — the larger of the two axes
- `engine` is `min(100, round(audioAvg × 4))` — amplified so normal room noise produces visible movement
- The backend folds tilt values > 90° back into the 0–90 range (phone upside-down symmetry)

### HTTPS requirement

| Browser | Plain HTTP (`http://192.168.x.x`) | Secure (`https://`) |
|---|---|---|
| iOS Safari | ❌ Silently fails | ✅ Works |
| Android Chrome | ⚠️ Usually works | ✅ Works |
| Desktop Chrome | ✅ Works (localhost) | ✅ Works |

**Free HTTPS tunnels:**

```bash
# Option A — ngrok
ngrok http 8000
# Open: https://xxxx.ngrok-free.app/phone

# Option B — Cloudflare (no account needed)
cloudflared tunnel --url http://localhost:8000
# Open: https://xxxx.trycloudflare.com/phone
```

---

## Frontend Dashboard

The React dashboard (`frontend/src/App.jsx`) polls the backend on two intervals:

| Interval | Endpoints | Purpose |
|---|---|---|
| Every 2s | `/alerts/{id}/queue`, `/alerts/{id}/instruction`, `/fleet/summary` | Alert data, instruction, stats |
| Every 1s | `/sensors/{id}/latest` | Sensor readings + phone-connected badge |

### UI components

- **Instruction Card** — shows the active alert type, severity badge (CRITICAL / HIGH / MEDIUM / LOW), LLM/fallback badge, and the instruction text. Shows "🟢 ALL CLEAR" with neutral styling when `active === false`.
- **Sensor Row** — 4 cards: Proximity (m), Tilt (°), Engine (%), Fatigue (/100) — updated live from phone or scenario
- **Suppressed Queue** — shown only when ≥1 suppressed alert exists (score ≥ 10)
- **Fleet Footer** — total processed, shown vs suppressed, last phone update timestamp
- **Demo Control Panel** — 6 scenario buttons + loading spinner

---

## Troubleshooting

### Backend won't start

```bash
# Missing dependencies
pip install -r backend/requirements.txt

# Port already in use
# Change port: uvicorn main:app --port 8001
```

### Frontend shows "Backend not connected"

```bash
# Is the backend running?
curl http://localhost:8000/health

# CORS issue? Backend must be on port 8000 or VITE_API_URL must match
```

### Always showing "📋 Fallback Template" (not "🤖 Groq LLM")

1. Check `backend/.env` — `GROQ_API_KEY` must be set to a real key (not the placeholder)
2. Look at the backend console — it logs the Groq error body on failure
3. Get a free key at [console.groq.com](https://console.groq.com) (no credit card)

### Phone sensors not responding

- Ensure phone and laptop are on the **same WiFi network**
- iOS Safari requires HTTPS — use ngrok or Cloudflare tunnel (see [HTTPS requirement](#https-requirement))
- Grant microphone permission when prompted
- Tap the **Start sending** button — sensors require a user gesture to activate on mobile

### Finding your IP manually

```powershell
# Windows
ipconfig
# Look for "IPv4 Address" under your WiFi adapter

# Mac / Linux
ifconfig | grep "inet "
```
