# Alert Arbitration Co-Pilot — Backend

> **Edge-AI system that arbitrates safety alerts on heavy machinery (excavators, cranes, loaders) so the operator only ever sees ONE prioritized, plain-language instruction instead of multiple competing alerts.**

---

## Architecture

```
Sensor Input  →  Edge Scoring  →  Context Filter  →  Suppression Queue  →  LLM Instruction
(ingest/sim)     (rule-based)     (task_mode)        (priority queue)       (Groq / fallback)
```

Pipeline modules: `scoring.py` → `suppression.py` → `llm.py`  
State: `storage.py` (in-memory + JSONL log) | Events: `events.py` (MQTT-shaped in-process bus)

---

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Set up environment
cp .env.example .env
# Edit .env and add your GROQ_API_KEY from https://console.groq.com

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
| `LLM_TIMEOUT_SECONDS` | No | `2` | LLM call timeout before fallback |
| `FLEET_LOG_PATH` | No | `data/fleet_log.jsonl` | Fleet log file path |

---

## API Endpoints

### 🟢 Sensor Layer

#### `POST /sensors/ingest`
Ingest a real sensor reading. Runs full scoring + suppression pipeline immediately.

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

#### `POST /sensors/simulate`
Trigger a named demo scenario — no physical sensors needed.

```bash
# Flagship demo: four concurrent risks, one instruction wins
curl -X POST http://localhost:8000/sensors/simulate \
  -H "Content-Type: application/json" \
  -d '{"machine_id": "EXC-001", "scenario": "multi_alert"}'
```

Available scenarios:
| Scenario | Description |
|---|---|
| `tilt_critical` | Excavator at 28° tilt (imminent tip-over) |
| `proximity_warning` | Loader 2.3 m from obstacle |
| `multi_alert` | ⭐ FOUR concurrent risks — flagship demo |
| `fatigue_high` | Operator fatigue at 85/100 |
| `engine_overload` | Engine 112°C + 96% load |
| `all_clear` | All readings nominal |

#### `GET /sensors/scenarios`
List all available scenario names.

```bash
curl http://localhost:8000/sensors/scenarios
```

---

### 🔴 Alert Queue

#### `GET /alerts/{machine_id}/queue`
Full prioritized alert queue. Top alert is flagged `is_top: true` — this is the one shown to the operator. All others are suppressed.

```bash
curl http://localhost:8000/alerts/EXC-001/queue
```

**Example response:**
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
    {"alert_type": "tilt", "adjusted_score": 87.0, "is_top": false, ...},
    {"alert_type": "fatigue", "adjusted_score": 79.0, "is_top": false, ...},
    {"alert_type": "engine", "adjusted_score": 72.0, "is_top": false, ...}
  ]
}
```

#### `GET /alerts/{machine_id}/instruction`
Plain-language instruction for the top alert. Uses Groq LLM (or fallback templates).

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

> **Testing the fallback path:** Set `GROQ_API_KEY=bad_key` in `.env` and restart. The `source` field will show `"fallback"` and the instruction still works — zero connectivity required.

---

### 📊 Fleet Logging & Analytics

#### `GET /fleet/log`
Recent log entries from `data/fleet_log.jsonl`.

```bash
# Last 50 entries (default)
curl http://localhost:8000/fleet/log

# Last 10 entries
curl "http://localhost:8000/fleet/log?n=10"
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

---

### ⚙️ System

#### `GET /health`
Liveness check.
```bash
curl http://localhost:8000/health
```

#### `GET /docs`
Interactive Swagger UI — try all endpoints in browser.

---

## Demo Script (Live Q&A)

```bash
# Step 1: Run flagship multi-alert scenario
curl -X POST http://localhost:8000/sensors/simulate \
  -H "Content-Type: application/json" \
  -d '{"machine_id": "EXC-001", "scenario": "multi_alert"}'

# Step 2: Show the arbitration result (4 alerts → 1 winner)
curl http://localhost:8000/alerts/EXC-001/queue

# Step 3: Get the single operator instruction
curl http://localhost:8000/alerts/EXC-001/instruction

# Step 4: Show fleet analytics
curl http://localhost:8000/fleet/summary
```

---

## Scoring Logic Summary

| Signal | Critical (90+) | High (60-89) | Medium (30-59) | Low |
|---|---|---|---|---|
| Proximity | < 1 m | 1-3 m | 3-5 m | > 5 m |
| Tilt | > 25° | 15-25° | 8-15° | < 8° |
| Engine Temp | > 110°C | 100-110°C | 90-100°C | < 90°C |
| Engine Load | > 95% | 85-95% | 70-85% | < 70% |
| Fatigue | ≥ 77 (×1.1) | 40-70 | 20-40 | < 20 |

**Context filter multipliers:**
- `idle` mode: engine & fatigue × 0.4 (suppressed unless critical), tilt/proximity unchanged
- `transport` mode: all × 1.0 (standard)
- `active` mode: proximity & tilt × 1.2 (amplified, capped at 100)

---

## File Structure

```
TataInnovate/
├── main.py          # FastAPI app, all routes
├── models.py        # Pydantic models (request/response shapes)
├── scoring.py       # Rule-based edge scoring + context filter
├── suppression.py   # Per-machine priority queue (arbitration core)
├── llm.py           # Groq API + fallback template logic
├── storage.py       # In-memory state + JSONL fleet logger
├── simulator.py     # Named demo scenario generators
├── events.py        # MQTT-shaped in-process event bus
├── requirements.txt
├── .env.example     # Copy to .env and add GROQ_API_KEY
└── data/
    └── fleet_log.jsonl   # Auto-created on first run
```
