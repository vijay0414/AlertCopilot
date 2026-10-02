"""
models.py — Pydantic data models for Alert Arbitration Co-Pilot
All request/response shapes are defined here; FastAPI validates them automatically.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ──────────────────────────────────────────────
# Enums
# ──────────────────────────────────────────────

class TaskMode(str, Enum):
    idle = "idle"
    transport = "transport"
    active = "active"


class AlertType(str, Enum):
    proximity = "proximity"
    tilt = "tilt"
    engine = "engine"
    fatigue = "fatigue"


class InstructionSource(str, Enum):
    llm = "llm"
    fallback = "fallback"


# ──────────────────────────────────────────────
# Sensor layer
# ──────────────────────────────────────────────

class SensorReading(BaseModel):
    """Raw sensor payload — any subset of fields may be present."""
    machine_id: str = Field(..., description="Unique machine identifier, e.g. 'EXC-001'")
    timestamp: Optional[datetime] = Field(default_factory=datetime.utcnow)

    # Proximity sensor (metres)
    proximity: Optional[float] = Field(None, ge=0, description="Distance to nearest obstacle in metres")

    # IMU / inclinometer (degrees)
    tilt: Optional[float] = Field(None, ge=0, le=90, description="Machine tilt angle in degrees")

    # Engine telemetry
    engine_temp: Optional[float] = Field(None, description="Engine temperature in °C")
    engine_load: Optional[float] = Field(None, ge=0, le=100, description="Engine load percentage 0-100")

    # Operator fatigue score from wearable / camera (0-100)
    fatigue: Optional[float] = Field(None, ge=0, le=100, description="Operator fatigue score 0-100")

    # Operational mode
    task_mode: Optional[TaskMode] = Field(TaskMode.transport, description="Current operational mode")


class SimulateRequest(BaseModel):
    """Trigger a named demo scenario for a machine."""
    machine_id: str = Field(..., description="Target machine identifier")
    scenario: str = Field(
        ...,
        description=(
            "Named scenario: 'tilt_critical' | 'proximity_warning' | "
            "'multi_alert' | 'fatigue_high' | 'engine_overload' | 'all_clear'"
        ),
    )


# ──────────────────────────────────────────────
# Scoring layer
# ──────────────────────────────────────────────

class ScoredAlert(BaseModel):
    """A single scored concern before suppression."""
    alert_type: AlertType
    raw_score: float = Field(..., ge=0, le=100)
    adjusted_score: float = Field(..., ge=0, le=100)
    message_context: Dict[str, Any] = Field(default_factory=dict,
        description="Contextual values (e.g. {'distance_m': 0.8}) used in instruction generation")
    is_suppressed: bool = Field(False,
        description="True if this alert lost to a higher-priority alert")
    timestamp: datetime = Field(default_factory=datetime.utcnow)


# ──────────────────────────────────────────────
# Suppression / queue layer
# ──────────────────────────────────────────────

class AlertQueueEntry(BaseModel):
    """Entry in the per-machine priority queue."""
    alert_type: AlertType
    adjusted_score: float
    raw_score: float
    message_context: Dict[str, Any]
    is_top: bool = False          # True only for the single highest-priority alert
    timestamp: str                 # ISO string for JSON serialisability


class AlertQueue(BaseModel):
    """Full priority queue response for a machine."""
    machine_id: str
    queried_at: str
    active_alert: Optional[AlertQueueEntry] = None
    suppressed_alerts: List[AlertQueueEntry] = []


# ──────────────────────────────────────────────
# Instruction layer
# ──────────────────────────────────────────────

class Instruction(BaseModel):
    """Plain-language instruction returned to the operator display."""
    machine_id: str
    alert_type: Optional[str] = None
    severity: Optional[float] = None
    instruction_text: str
    source: InstructionSource
    generated_at: str


# ──────────────────────────────────────────────
# Fleet log / analytics
# ──────────────────────────────────────────────

class FleetLogEntry(BaseModel):
    """One row appended to data/fleet_log.jsonl."""
    machine_id: str
    timestamp: str
    alert_type: Optional[str]
    raw_score: Optional[float]
    adjusted_score: Optional[float]
    is_suppressed: bool
    instruction_text: Optional[str]
    instruction_source: Optional[str]   # "llm" | "fallback" | None
    scenario: Optional[str] = None      # populated when triggered via /simulate


class FleetSummary(BaseModel):
    """Aggregate stats for the fleet analytics view."""
    total_alerts_processed: int
    alerts_shown: int          # top-of-queue alerts (not suppressed)
    alerts_suppressed: int
    by_alert_type: Dict[str, int]
    llm_calls: int
    fallback_calls: int
