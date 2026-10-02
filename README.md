# AlertCopilot — Backend

> **An edge-AI backend that arbitrates competing safety alerts on heavy machinery (excavators, cranes, loaders) and surfaces ONE prioritized, plain-language instruction to the operator — eliminating alert fatigue.**

---

## How It Works

When a machine is running under tough conditions, multiple sensors can fire simultaneously — proximity, tilt, engine overload, and operator fatigue all at once. Without arbitration, the operator sees all four alerts at the same time and has to decide which one matters most. AlertCopilot does that work automatically.

Every sensor reading goes through a **5-step pipeline**:

```
Sensor Input
    │
    ▼
[1] Edge Scoring          scoring.py
    Rule-based, per-signal risk score (0–100)
    │
    ▼
[2] Context Filter         scoring.py
    Adjust scores by task_mode (idle / transport / active)
    │
    ▼
[3] Suppression Queue      suppression.py
    Priority queue — only the TOP alert stays "active"
    All others are suppressed
    │
    ▼
[4] Fleet Logging          storage.py
    Every alert + instruction logged to JSONL
    │
    ▼
[5] LLM Instruction        llm.py
    Groq (llama-3.1-8b-instant) OR local fallback templates
    → One short imperative sentence for the operator
```

---

## Project Structure

```
backend/
├── main.py           # FastAPI app — all routes + pipeline orchestration
├── models.py         # Pydantic request/response models
├── scoring.py        # Rule-based edge scoring + context filter (Steps 1–2)
├── suppression.py    # Per-machine priority queue — arbitration core (Step 3)
├── llm.py            # Groq LLM call + fallback template dictionary (Step 5)
├── storage.py        # In-memory machine state + JSONL fleet logger (Step 4)
├── simulator.py      # Named demo scenario generators (no sensors needed)
├── events.py         # MQTT-shaped in-process event bus
├── verify_pipeline.py # Standalone pipeline smoke test
├── requirements.txt
├── .env.example      # Copy to .env and add GROQ_API_KEY
└── data/
    └── fleet_log.jsonl   # Auto-created on first run
```

---

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Set up environment
cp .env.example .env
# Open .env and set your GROQ_API_KEY (get a free one at https://console.groq.com)

# 3. Start the server
uvicorn main:app --reload --port 8000

# 4. Open the interactive API docs
# http://localhost:8000/docs
```

---

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `GROQ_API_KEY` | Yes (for LLM) | — | Groq API key. Without it, fallback templates are used automatically. |
| `GROQ_MODEL` | No | `llama-3.1-8b-instant` | Override the Groq model |
| `LLM_TIMEOUT_SECONDS` | No | `2` | Seconds before LLM call times out and falls back to templates |
| `FLEET_LOG_PATH` | No | `data/fleet_log.jsonl` | Path to the fleet log file |

> **Tip:** The server works fully offline. If `GROQ_API_KEY` is missing, expired, or times out, the `source` field in the response will read `"fallback"` and a pre-written template instruction is returned instead.

---

## Pipeline Deep Dive

### Step 1 — Sensor Ingest

Two entry points feed data into the pipeline:

- **Live data** → `POST /sensors/ingest` — accepts a JSON body with real sensor readings
- **Simulated data** → `POST /sensors/simulate` — generates synthetic readings for a named scenario (no physical sensors required)

Both routes call the same internal `_run_pipeline()` function in `main.py`.

---

### Step 2 — Edge Scoring (`scoring.py`)

Each sensor signal is independently converted to a **risk score from 0 to 100** using rule-based threshold curves:

| Signal | Critical (90+) | High (60–89) | Medium (30–59) | Low |
|---|---|---|---|---|
| Proximity | < 1 m | 1–3 m | 3–5 m | > 5 m |
| Tilt | > 25° | 15–25° | 8–15° | < 8° |
| Engine Temp | > 110°C | 100–110°C | 90–100°C | < 90°C |
| Engine Load | > 95% | 85–95% | 70–85% | < 70% |
| Fatigue | ≥ 70 (×1.1) | 40–70 | 20–40 | < 20 |

No ML model is involved — thresholds are explicit and tunable without retraining anything.

---

### Step 3 — Context Filter (`scoring.py`)

After raw scoring, each score is multiplied by a **task_mode modifier** before entering the suppression queue:

| Alert Type | `idle` | `transport` | `active` |
|---|---|---|---|
| proximity | ×1.0 | ×1.0 | **×1.2** |
| tilt | ×1.0 | ×1.0 | **×1.2** |
| engine | **×0.4** | ×1.0 | ×1.0 |
| fatigue | **×0.4** | ×1.0 | ×1.0 |

**Safety guard:** In `idle` mode, `tilt` and `proximity` alerts that already score ≥ 60 are *never* dampened — a machine tipping over is critical regardless of what it's doing.

---

### Step 4 — Suppression Queue (`suppression.py`)

A **thread-safe, per-machine priority queue** holds all scored alerts sorted by `adjusted_score` descending.

- The **top alert** (`is_top: true`) is the only one shown to the operator.
- All other alerts are **held suppressed** — visible in the queue for analytics but not surfaced to the operator.
- The queue is **replaced on every new sensor reading** — it always reflects the current machine state.

This is the core of the alert arbitration: when 4 alerts fire simultaneously, exactly 1 wins.

---

### Step 5 — LLM Instruction Generation (`llm.py`)

The top alert's type, score, and context are sent to **Groq's API** (`llama-3.1-8b-instant`) with a tight prompt:

```
Given this machine safety alert: proximity, severity 100/100, context: distance_m=0.8.
Respond with ONE short imperative safety instruction sentence (max 12 words)
a heavy machinery operator can act on immediately.
No preamble, no explanation — just the instruction.
```

**Automatic fallback:** If Groq is unavailable, times out (default: 2s), or returns an error, a **local template dictionary** is used instead. The `source` field in the response shows `"llm"` or `"fallback"`.

Example fallback templates:

| Alert | Severity | Instruction |
|---|---|---|
| proximity | critical | *Stop immediately — obstacle within 1 metre, do not move.* |
| tilt | high | *Cease digging — tilt angle dangerously high, level the machine now.* |
| engine | critical | *Shut down engine immediately — critical overtemperature or overload detected.* |
| fatigue | high | *Park machine safely and rest — operator fatigue is dangerously high.* |

---

## API Reference

### Sensor Layer

#### `POST /sensors/ingest`
Ingest a real sensor reading and run the full pipeline immediately.

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

All sensor fields are optional — only the fields present in the payload are scored.

---

#### `POST /sensors/simulate`
Trigger a named demo scenario without physical sensors.

```bash
curl -X POST http://localhost:8000/sensors/simulate \
  -H "Content-Type: application/json" \
  -d '{"machine_id": "EXC-001", "scenario": "multi_alert"}'
```

**Available scenarios:**

| Scenario | What it simulates |
|---|---|
| `multi_alert` | ⭐ **Flagship demo** — 4 concurrent risks: proximity 0.8m + tilt 18° + engine 105°C/91% + fatigue 72 |
| `tilt_critical` | Excavator at 28° tilt (imminent tip-over), active mode |
| `proximity_warning` | Loader 2.3m from obstacle, transport mode |
| `fatigue_high` | Operator fatigue at 85/100, idle mode |
| `engine_overload` | Engine at 112°C and 96% load simultaneously |
| `all_clear` | All readings nominal — resets the alert queue |

---

#### `GET /sensors/scenarios`
List all available scenario names.

```bash
curl http://localhost:8000/sensors/scenarios
```

---

### Alert Queue

#### `GET /alerts/{machine_id}/queue`
Returns the full prioritized alert queue for a machine. The top alert is flagged `is_top: true`; all others are suppressed.

```bash
curl http://localhost:8000/alerts/EXC-001/queue
```

**Example response (after `multi_alert` scenario):**
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
    {"alert_type": "fatigue",  "adjusted_score": 87.0, "is_top": false},
    {"alert_type": "tilt",    "adjusted_score": 82.8, "is_top": false},
    {"alert_type": "engine",  "adjusted_score": 75.0, "is_top": false}
  ]
}
```

---

#### `GET /alerts/{machine_id}/instruction`
Generates a plain-language operator instruction for the top alert using Groq LLM (or fallback templates).

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

- `source: "llm"` → Groq API was used
- `source: "fallback"` → local template was used (no API key / timeout / error)

---

### Fleet Logging & Analytics

#### `GET /fleet/log`
Returns recent log entries from `data/fleet_log.jsonl`.

```bash
curl http://localhost:8000/fleet/log          # last 50 entries (default)
curl "http://localhost:8000/fleet/log?n=10"   # last 10 entries
```

---

#### `GET /fleet/summary`
Aggregate statistics across all machines and sessions.

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

---

### System

#### `GET /health`
Liveness check. Returns `200 OK` with all currently tracked machine IDs.

```bash
curl http://localhost:8000/health
```

#### `GET /docs`
Interactive Swagger UI — try every endpoint directly in the browser.

---

## End-to-End Demo Script

Run these four commands in order to walk through the entire system:

```bash
# 1. Trigger the flagship scenario — 4 simultaneous risks on one machine
curl -X POST http://localhost:8000/sensors/simulate \
  -H "Content-Type: application/json" \
  -d '{"machine_id": "EXC-001", "scenario": "multi_alert"}'

# 2. See the arbitration result — 4 alerts ranked, 1 winner
curl http://localhost:8000/alerts/EXC-001/queue

# 3. Get the single plain-language instruction for the operator
curl http://localhost:8000/alerts/EXC-001/instruction

# 4. Review fleet analytics
curl http://localhost:8000/fleet/summary
```

---

## Event Bus (`events.py`)

An MQTT-shaped **in-process event bus** publishes three topic types on every pipeline run:

| Topic | Published when |
|---|---|
| `sensor/{machine_id}/reading` | Raw sensor data received |
| `sensor/{machine_id}/alerts_scored` | All scored alerts computed |
| `sensor/{machine_id}/alert_active` | Top alert determined |

This is designed to be a drop-in replacement for a real MQTT broker — swap `event_bus.publish()` with an actual `paho-mqtt` client when deploying to hardware.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Web framework | FastAPI + Uvicorn |
| LLM | Groq API (`llama-3.1-8b-instant`) |
| HTTP client | `httpx` (async) |
| Data validation | Pydantic v2 |
| Fleet logging | JSONL flat file (`data/fleet_log.jsonl`) |
| Concurrency | Python `asyncio` + `threading.RLock` for queue safety |
