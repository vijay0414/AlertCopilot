# AlertCopilot — Alert Arbitration Co-Pilot

> **Edge-AI backend that monitors safety sensors on heavy machinery (excavators, cranes, loaders), scores every risk signal in real time, arbitrates competing alerts, and surfaces ONE prioritised plain-language instruction to the operator — instead of an overwhelming alert storm.**

---

## How It Works

When multiple danger signals fire simultaneously (e.g. tilt + proximity + engine overload + fatigue), operators cannot safely respond to all of them at once. AlertCopilot solves this with a 5-step pipeline that runs on every sensor reading:

```
Sensor Reading
      │
      ▼
  [1] Store + Publish (machine_store, event_bus)
      │
      ▼
  [2] Edge Scoring  ──────────────────────────────────────────
      │   scoring.py                                          │
      │   • score_proximity()  → 0-100 (spikes inside 1 m)   │
      │   • score_tilt()       → 0-100 (critical > 25°)      │
      │   • score_engine()     → 0-100 (temp °C + load %)    │
      │   • score_fatigue()    → 0-100 (amplified ≥ 70)      │
      │                                                       │
      ▼                                                       │
  [3] Context Filter (apply_context_filter)                   │
      │   Adjusts scores by task_mode:                        │
      │   • idle      → engine & fatigue × 0.4               │
      │   • transport → all × 1.0 (neutral)                  │
      │   • active    → proximity & tilt × 1.2 (amplified)   │
      │                                                       │
      ▼                                                       │
  [4] Suppression Queue  (suppression.py)                     │
      │   Per-machine priority queue sorted by adjusted score │
      │   Top alert → is_suppressed = False                   │
      │   All others → is_suppressed = True                   │
      │                                                       │
      ▼                                                       │
  [5] LLM Instruction  (llm.py)                              │
      │   Top alert → Groq LLM (llama-3.1-8b-instant)        │
      │   On timeout/failure → local fallback templates       │
      │   Result: one imperative sentence for the operator    │
      │                                                       │
      ▼                                                       │
  Fleet Logger  (storage.py)                                  │
      └── Appends every alert + instruction to fleet_log.jsonl│
```

---

## Tech Stack

| Layer | Technology |
|---|---|
| API Framework | FastAPI (Python) |
| LLM Provider | Groq API (`llama-3.1-8b-instant`) |
| Fallback | Local rule-based templates |
| Event Bus | MQTT-shaped in-process async bus (`events.py`) |
| Storage | In-memory state + JSONL fleet log |
| Server | Uvicorn (ASGI) |

---

## Quick Start

```bash
# 1. Install dependencies
cd backend
pip install -r requirements.txt

# 2. Set up environment
cp .env.example .env
# Open .env and set your GROQ_API_KEY from https://console.groq.com

# 3. Start the server
uvicorn main:app --reload --port 8000

# 4. Open interactive API docs
# http://localhost:8000/docs
```

---

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `GROQ_API_KEY` | **Yes** (for LLM path) | — | Groq API key. Without it, fallback templates are used. |
| `GROQ_MODEL` | No | `llama-3.1-8b-instant` | Groq model override |
| `LLM_TIMEOUT_SECONDS` | No | `2` | Seconds before falling back to templates |
| `FLEET_LOG_PATH` | No | `data/fleet_log.jsonl` | Path to fleet log file |

> **Note:** Never commit a real API key. Copy `.env.example` → `.env` and fill in your key locally.

---

## API Reference

### 🟢 Sensor Layer

#### `POST /sensors/ingest`
Ingest a live sensor reading. Immediately runs the full scoring → suppression → logging pipeline.

```bash
curl -X POST http://localhost:8000/sensors/ingest \
  -H "Content-Type: application/json" \
  -d '{
    "machine_id": "EXC-001",
    "proximity": 2.3,
    "tilt": 12.0,
    "engine_temp": 95.0,
    "engine_load": 78.0,
    "fatigue": 55.0,
    "task_mode": "active"
  }'
```

All sensor fields are optional — only fields provided will produce alerts.

#### `POST /sensors/simulate`
Trigger a named demo scenario — no physical sensors required.

```bash
# Flagship demo: four concurrent risks → one winner
curl -X POST http://localhost:8000/sensors/simulate \
  -H "Content-Type: application/json" \
  -d '{"machine_id": "EXC-001", "scenario": "multi_alert"}'
```

**Available scenarios:**

| Scenario | What it simulates |
|---|---|
| `tilt_critical` | Excavator at 28° tilt — imminent tip-over |
| `proximity_warning` | Loader 2.3 m from obstacle |
| `multi_alert` | ⭐ Four concurrent risks (flagship demo) |
| `fatigue_high` | Operator fatigue index at 85/100 |
| `engine_overload` | Engine at 112 °C + 96% load |
| `all_clear` | All readings nominal |

#### `GET /sensors/scenarios`
List all available scenario names.

```bash
curl http://localhost:8000/sensors/scenarios
```

---

### 🔴 Alert Queue

#### `GET /alerts/{machine_id}/queue`
Returns the full prioritised alert queue for a machine. The highest-scoring alert is flagged `is_top: true` — this is the one shown to the operator. All others are held suppressed.

```bash
curl http://localhost:8000/alerts/EXC-001/queue
```

**Example response (multi_alert scenario):**
```json
{
  "machine_id": "EXC-001",
  "queried_at": "2024-01-15T10:30:00Z",
  "active_alert": {
    "alert_type": "proximity",
    "adjusted_score": 100.0,
    "raw_score": 92.0,
    "message_context": {"distance_m": 0.8},
    "is_top": true,
    "timestamp": "2024-01-15T10:29:58Z"
  },
  "suppressed_alerts": [
    {"alert_type": "tilt",    "adjusted_score": 87.0, "is_top": false},
    {"alert_type": "fatigue", "adjusted_score": 79.0, "is_top": false},
    {"alert_type": "engine",  "adjusted_score": 72.0, "is_top": false}
  ]
}
```

#### `GET /alerts/{machine_id}/instruction`
Generates a plain-language instruction for the top alert using Groq LLM (or fallback templates).

```bash
curl http://localhost:8000/alerts/EXC-001/instruction
```

**Example response:**
```json
{
  "machine_id": "EXC-001",
  "alert_type": "proximity",
  "severity": 100.0,
  "instruction_text": "Stop immediately — obstacle within 1 metre, do not move.",
  "source": "llm",
  "generated_at": "2024-01-15T10:30:01Z"
}
```

The `source` field is either `"llm"` (Groq responded in time) or `"fallback"` (timeout or no key).

> **Testing fallback:** Set `GROQ_API_KEY=bad_key` in `.env` and restart. Instructions still work — zero connectivity required.

---

### 📊 Fleet Logging & Analytics

#### `GET /fleet/log`
Returns recent entries from `data/fleet_log.jsonl` — every alert scored and every instruction generated.

```bash
curl http://localhost:8000/fleet/log          # last 50 entries (default)
curl "http://localhost:8000/fleet/log?n=10"   # last 10 entries
```

#### `GET /fleet/summary`
Aggregate statistics — run a few simulations first for non-zero counts.

```bash
curl http://localhost:8000/fleet/summary
```

**Example response:**
```json
{
  "total_alerts_processed": 24,
  "alerts_shown": 6,
  "alerts_suppressed": 18,
  "by_alert_type": {
    "proximity": 6,
    "tilt": 6,
    "engine": 6,
    "fatigue": 6
  },
  "llm_calls": 4,
  "fallback_calls": 2
}
```

The key metric: **18 of 24 alerts suppressed** — the operator saw 6 clean instructions, not 24 competing warnings.

---

### ⚙️ System

#### `GET /health`
Liveness check — returns 200 if the server is running.

```bash
curl http://localhost:8000/health
```

#### `GET /docs`
Interactive Swagger UI — explore and test all endpoints in the browser.

---

## Scoring Logic

### Raw Score Tiers

| Signal | Critical (90–100) | High (60–89) | Medium (30–59) | Low |
|---|---|---|---|---|
| Proximity | < 1 m | 1–3 m | 3–5 m | > 5 m |
| Tilt | > 25° | 15–25° | 8–15° | < 8° |
| Engine Temp | > 110 °C | 100–110 °C | 90–100 °C | < 90 °C |
| Engine Load | > 95% | 85–95% | 70–85% | < 70% |
| Fatigue Index | ≥ 70 (× 1.1 boost) | 40–70 | 20–40 | < 20 |

### Context Filter Multipliers

| Task Mode | Engine | Fatigue | Proximity | Tilt |
|---|---|---|---|---|
| `idle` | × 0.4 | × 0.4 | × 1.0 | × 1.0 |
| `transport` | × 1.0 | × 1.0 | × 1.0 | × 1.0 |
| `active` | × 1.0 | × 1.0 | × **1.2** | × **1.2** |

Safety guard: even in `idle` mode, tilt/proximity scores ≥ 60 are **never dampened**.

---

## Demo Script

Run this sequence to show the full arbitration pipeline end-to-end:

```bash
# Step 1 — Fire four concurrent risks
curl -X POST http://localhost:8000/sensors/simulate \
  -H "Content-Type: application/json" \
  -d '{"machine_id": "EXC-001", "scenario": "multi_alert"}'

# Step 2 — See arbitration result (4 alerts → 1 winner)
curl http://localhost:8000/alerts/EXC-001/queue

# Step 3 — Get the single operator instruction
curl http://localhost:8000/alerts/EXC-001/instruction

# Step 4 — Fleet analytics
curl http://localhost:8000/fleet/summary
```

---

## File Structure

```
backend/
├── main.py           # FastAPI app, all routes, pipeline orchestration
├── models.py         # Pydantic models (SensorReading, ScoredAlert, Instruction, ...)
├── scoring.py        # Rule-based edge scoring + context filter (Steps 2 & 3)
├── suppression.py    # Per-machine priority queue — arbitration core (Step 4)
├── llm.py            # Groq API call + local fallback templates (Step 5)
├── storage.py        # In-memory machine state + JSONL fleet logger
├── simulator.py      # Named demo scenario generators
├── events.py         # MQTT-shaped in-process async event bus
├── verify_pipeline.py # Quick smoke-test script (no server needed)
├── requirements.txt
├── .env.example      # Copy to .env and set GROQ_API_KEY
└── data/
    └── fleet_log.jsonl   # Auto-created on first ingest/simulate call
```
