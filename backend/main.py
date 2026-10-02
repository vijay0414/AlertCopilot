"""
main.py — FastAPI application for Alert Arbitration Co-Pilot
============================================================

Full pipeline per sensor ingest:
  POST /sensors/ingest or /sensors/simulate
    → scoring.compute_alerts()          (Sense → Score)
    → context filter (inside compute_alerts)   (Score → Suppress)
    → suppression_queue.update()        (priority queue arbitration)
    → fleet_logger.append() per alert   (fleet logging)
    → event_bus.publish()               (MQTT-shaped event emission)

  GET /alerts/{machine_id}/instruction
    → reads top alert from queue
    → llm.generate_instruction()        (plain-language output)
    → fleet_logger.append() instruction log

Run with:
  uvicorn main:app --reload --port 8000
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()

# ── Local modules ──────────────────────────────────────────────────────────
from events import event_bus, topic_alert_active, topic_alerts_scored, topic_sensor_reading
from llm import generate_instruction
from models import (
    AlertQueue,
    FleetLogEntry,
    FleetSummary,
    Instruction,
    InstructionSource,
    SensorReading,
    SimulateRequest,
)
from scoring import compute_alerts
from simulator import SCENARIO_REGISTRY, generate_scenario
from storage import fleet_logger, machine_store
from suppression import suppression_queue

# ── Logging setup ──────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("main")

# ── FastAPI app ────────────────────────────────────────────────────────────
app = FastAPI(
    title="Alert Arbitration Co-Pilot",
    description=(
        "Edge-AI backend that arbitrates safety alerts on heavy machinery "
        "(excavators, cranes, loaders) and surfaces ONE prioritized plain-language "
        "instruction to the operator."
    ),
    version="1.0.0",
)

# CORS — wide open for local frontend dev during hackathon
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ══════════════════════════════════════════════════════════════════════════════
# Internal pipeline helper
# ══════════════════════════════════════════════════════════════════════════════

async def _run_pipeline(reading: SensorReading, scenario: Optional[str] = None) -> List[dict]:
    """
    Shared pipeline called by both /ingest and /simulate endpoints.
    Returns a list of serialisable alert dicts (for the API response body).
    """
    machine_id = reading.machine_id

    # 1. Store latest sensor reading
    machine_store.upsert(reading)

    # 2. Publish raw reading to event bus (MQTT-shaped)
    await event_bus.publish(topic_sensor_reading(machine_id), reading)

    # 3. Edge-AI scoring + context filter
    scored_alerts = compute_alerts(reading)

    # 4. Update the suppression priority queue
    if scored_alerts:
        suppression_queue.update(machine_id, scored_alerts)
    else:
        # No sensor data → clear the queue for this machine
        suppression_queue.clear(machine_id)

    # 5. Publish scored alerts
    await event_bus.publish(topic_alerts_scored(machine_id), scored_alerts)

    # 6. Publish the top (active) alert
    top = suppression_queue.top_alert(machine_id)
    if top:
        await event_bus.publish(topic_alert_active(machine_id), top)

    # 7. Fleet logging — one entry per scored alert
    now_str = datetime.utcnow().isoformat() + "Z"
    for alert in scored_alerts:
        fleet_logger.append(FleetLogEntry(
            machine_id=machine_id,
            timestamp=now_str,
            alert_type=alert.alert_type.value,
            raw_score=alert.raw_score,
            adjusted_score=alert.adjusted_score,
            is_suppressed=alert.is_suppressed,
            instruction_text=None,   # instruction logged separately in /instruction endpoint
            instruction_source=None,
            scenario=scenario,
        ))

    logger.info(
        "pipeline: machine=%s scenario=%s alerts=%d top_score=%.1f",
        machine_id,
        scenario or "live",
        len(scored_alerts),
        top.adjusted_score if top else 0.0,
    )

    return [
        {
            "alert_type": a.alert_type.value,
            "raw_score": a.raw_score,
            "adjusted_score": a.adjusted_score,
            "is_suppressed": a.is_suppressed,
            "message_context": a.message_context,
        }
        for a in scored_alerts
    ]


# ══════════════════════════════════════════════════════════════════════════════
# Routes — Sensor Layer
# ══════════════════════════════════════════════════════════════════════════════

@app.post(
    "/sensors/ingest",
    summary="Ingest a real sensor reading",
    tags=["Sensors"],
)
async def ingest_sensor(reading: SensorReading) -> Dict[str, Any]:
    """
    Accept a JSON payload with any subset of sensor fields for a machine.
    Immediately runs the full scoring + suppression pipeline.

    Returns the list of scored alerts so the caller can verify what was computed.
    """
    alerts = await _run_pipeline(reading)
    return {
        "status": "ok",
        "machine_id": reading.machine_id,
        "alerts_scored": len(alerts),
        "scored_alerts": alerts,
    }


@app.post(
    "/sensors/simulate",
    summary="Trigger a named demo scenario",
    tags=["Sensors"],
)
async def simulate_scenario(request: SimulateRequest) -> Dict[str, Any]:
    """
    Generate a synthetic sensor reading for a named scenario and run the
    full pipeline — no physical sensors required.

    Available scenarios: **tilt_critical**, **proximity_warning**,
    **multi_alert**, **fatigue_high**, **engine_overload**, **all_clear**
    """
    try:
        reading = generate_scenario(request.machine_id, request.scenario)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    alerts = await _run_pipeline(reading, scenario=request.scenario)
    return {
        "status": "ok",
        "scenario": request.scenario,
        "machine_id": request.machine_id,
        "synthetic_reading": reading.model_dump(mode="json"),
        "alerts_scored": len(alerts),
        "scored_alerts": alerts,
    }


@app.get(
    "/sensors/scenarios",
    summary="List available demo scenarios",
    tags=["Sensors"],
)
async def list_scenarios() -> Dict[str, Any]:
    """Return the names of all built-in demo scenarios."""
    return {"available_scenarios": sorted(SCENARIO_REGISTRY.keys())}


# ══════════════════════════════════════════════════════════════════════════════
# Routes — Alert Queue
# ══════════════════════════════════════════════════════════════════════════════

@app.get(
    "/alerts/{machine_id}/queue",
    response_model=AlertQueue,
    summary="Get the full prioritized alert queue for a machine",
    tags=["Alerts"],
)
async def get_alert_queue(machine_id: str) -> AlertQueue:
    """
    Returns the full suppression queue for a machine.
    The top alert (highest adjusted score) is flagged with `is_top: true`.
    All others are held suppressed — the operator only sees the top one.
    """
    return suppression_queue.get_queue_response(machine_id)


# ══════════════════════════════════════════════════════════════════════════════
# Routes — Instruction Generation
# ══════════════════════════════════════════════════════════════════════════════

@app.get(
    "/alerts/{machine_id}/instruction",
    response_model=Instruction,
    summary="Get plain-language operator instruction for the top alert",
    tags=["Alerts"],
)
async def get_instruction(machine_id: str) -> Instruction:
    """
    Calls the Groq LLM (or falls back to local templates) to generate a
    single, operator-ready imperative instruction sentence for the
    highest-priority active alert on this machine.

    The `source` field tells you which path was used: **"llm"** or **"fallback"**.
    """
    top = suppression_queue.top_alert(machine_id)

    if top is None:
        # No active alerts — return an all-clear message
        return Instruction(
            machine_id=machine_id,
            alert_type=None,
            severity=None,
            instruction_text="All systems nominal — proceed with normal operations.",
            source=InstructionSource.fallback,
            generated_at=datetime.utcnow().isoformat() + "Z",
        )

    instruction_text, source = await generate_instruction(
        alert_type=top.alert_type.value,
        score=top.adjusted_score,
        context=top.message_context,
    )

    now_str = datetime.utcnow().isoformat() + "Z"

    # Log the instruction event to fleet log
    fleet_logger.append(FleetLogEntry(
        machine_id=machine_id,
        timestamp=now_str,
        alert_type=top.alert_type.value,
        raw_score=top.raw_score,
        adjusted_score=top.adjusted_score,
        is_suppressed=False,
        instruction_text=instruction_text,
        instruction_source=source.value,
    ))

    logger.info(
        "instruction: machine=%s type=%s score=%.1f source=%s",
        machine_id, top.alert_type.value, top.adjusted_score, source.value,
    )

    return Instruction(
        machine_id=machine_id,
        alert_type=top.alert_type.value,
        severity=top.adjusted_score,
        instruction_text=instruction_text,
        source=source,
        generated_at=now_str,
    )


# ══════════════════════════════════════════════════════════════════════════════
# Routes — Fleet Logging & Analytics
# ══════════════════════════════════════════════════════════════════════════════

@app.get(
    "/fleet/log",
    summary="Get recent fleet log entries",
    tags=["Fleet"],
)
async def get_fleet_log(
    n: int = Query(default=50, ge=1, le=500, description="Number of recent entries to return"),
) -> Dict[str, Any]:
    """
    Returns the last N entries from data/fleet_log.jsonl.
    Useful for an analytics dashboard or post-demo review.
    """
    entries = fleet_logger.recent_entries(n)
    return {
        "count": len(entries),
        "entries": entries,
    }


@app.get(
    "/fleet/summary",
    response_model=FleetSummary,
    summary="Get aggregate fleet alert statistics",
    tags=["Fleet"],
)
async def get_fleet_summary() -> FleetSummary:
    """
    Returns aggregate statistics:
    - Total alerts processed
    - Alerts shown vs suppressed (core arbitration metric)
    - Breakdown by alert type
    - LLM vs fallback instruction call counts
    """
    return fleet_logger.summary()


# ══════════════════════════════════════════════════════════════════════════════
# Routes — Health check
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/health", tags=["System"])
async def health_check() -> Dict[str, Any]:
    """Liveness probe — always returns 200 if the server is running."""
    return {
        "status": "ok",
        "service": "Alert Arbitration Co-Pilot",
        "machines_tracked": len(machine_store.all_ids()),
        "machine_ids": machine_store.all_ids(),
    }


@app.get("/", tags=["System"])
async def root() -> Dict[str, str]:
    return {
        "message": "Alert Arbitration Co-Pilot API is running.",
        "docs": "http://localhost:8000/docs",
        "health": "http://localhost:8000/health",
    }
