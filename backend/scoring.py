"""
scoring.py — Rule-based edge-AI scoring + context filter (Step 2 & 3 of pipeline)

No ML model is used here — this is intentional for demo speed and explainability.
The threshold curves below can be tuned without retraining anything.

Pipeline: Sensor reading → raw score per signal → context-adjusted score
"""

from __future__ import annotations

from typing import List, Optional

from models import AlertType, ScoredAlert, TaskMode


# ──────────────────────────────────────────────
# Raw scoring — Step 2: Sense → Score
# ──────────────────────────────────────────────

def score_proximity(distance_m: float) -> float:
    """
    Convert distance-to-obstacle (metres) to a risk score 0-100.
    Curve: exponential-ish tiers that spike sharply inside 1 m.
    """
    if distance_m < 0.5:
        return 100.0
    elif distance_m < 1.0:
        return 90.0 + (1.0 - distance_m) * 20      # 90-100
    elif distance_m < 2.0:
        return 75.0 + (2.0 - distance_m) * 15      # 75-90
    elif distance_m < 3.0:
        return 60.0 + (3.0 - distance_m) * 15      # 60-75
    elif distance_m < 5.0:
        return 30.0 + (5.0 - distance_m) * 15      # 30-60
    elif distance_m < 10.0:
        return 10.0 + (10.0 - distance_m) * 4      # 10-30
    else:
        return 5.0  # effectively safe


def score_tilt(angle_deg: float) -> float:
    """
    Convert tilt angle (degrees) to risk score 0-100.
    >25° is imminent tip-over territory on most excavators / cranes.
    """
    if angle_deg > 30:
        return 100.0
    elif angle_deg > 25:
        return 90.0 + (angle_deg - 25) * 2         # 90-100
    elif angle_deg > 20:
        return 75.0 + (angle_deg - 20) * 3         # 75-90
    elif angle_deg > 15:
        return 60.0 + (angle_deg - 15) * 3         # 60-75
    elif angle_deg > 8:
        return 30.0 + (angle_deg - 8) * (30 / 7)  # 30-60
    elif angle_deg > 3:
        return 10.0 + (angle_deg - 3) * 4          # 10-30
    else:
        return 5.0


def score_engine(temp_c: Optional[float], load_pct: Optional[float]) -> float:
    """
    Engine risk from temperature (°C) and/or load (%).
    Takes the worse of the two sub-scores.
    Typical excavator engine: normal ~85°C, warning >100°C, critical >110°C.
    """
    temp_score = 0.0
    load_score = 0.0

    if temp_c is not None:
        if temp_c > 115:
            temp_score = 100.0
        elif temp_c > 110:
            temp_score = 90.0 + (temp_c - 110) * 2
        elif temp_c > 100:
            temp_score = 60.0 + (temp_c - 100) * 3
        elif temp_c > 90:
            temp_score = 30.0 + (temp_c - 90) * 3
        elif temp_c > 80:
            temp_score = 10.0 + (temp_c - 80) * 2
        else:
            temp_score = 5.0

    if load_pct is not None:
        if load_pct > 95:
            load_score = 90.0 + (load_pct - 95) * 2
        elif load_pct > 85:
            load_score = 60.0 + (load_pct - 85) * 3
        elif load_pct > 70:
            load_score = 30.0 + (load_pct - 70) * 2
        elif load_pct > 50:
            load_score = 10.0 + (load_pct - 50) * 1
        else:
            load_score = 5.0

    return min(100.0, max(temp_score, load_score))


def score_fatigue(fatigue_score: float) -> float:
    """
    Fatigue maps roughly 1:1 to risk; small dead-zone at the low end.
    A fatigue_score of 70 → risk 70, fatigue 30 → risk ~15, etc.
    """
    if fatigue_score >= 70:
        return min(100.0, fatigue_score * 1.1)     # amplify high fatigue
    elif fatigue_score >= 40:
        return fatigue_score
    else:
        return fatigue_score * 0.5                  # low fatigue = low risk


# ──────────────────────────────────────────────
# Context filter — Step 3: task_mode adjustments
# ──────────────────────────────────────────────

# Multipliers per (alert_type, task_mode)
_CONTEXT_MULTIPLIERS: dict[tuple[AlertType, TaskMode], float] = {
    # --- idle mode: suppress non-critical engine/fatigue, keep safety alerts ---
    (AlertType.engine, TaskMode.idle):    0.4,
    (AlertType.fatigue, TaskMode.idle):   0.4,
    (AlertType.proximity, TaskMode.idle): 1.0,   # never dampen safety-critical
    (AlertType.tilt, TaskMode.idle):      1.0,

    # --- transport mode: standard (neutral) ---
    (AlertType.engine, TaskMode.transport):    1.0,
    (AlertType.fatigue, TaskMode.transport):   1.0,
    (AlertType.proximity, TaskMode.transport): 1.0,
    (AlertType.tilt, TaskMode.transport):      1.0,

    # --- active (digging/lifting): amplify proximity + tilt sensitivity ---
    (AlertType.engine, TaskMode.active):    1.0,
    (AlertType.fatigue, TaskMode.active):   1.0,
    (AlertType.proximity, TaskMode.active): 1.2,
    (AlertType.tilt, TaskMode.active):      1.2,
}

# In idle mode, only suppress scores below this threshold (don't suppress truly critical alerts)
_IDLE_SUPPRESS_THRESHOLD = 60.0


def apply_context_filter(
    alert_type: AlertType,
    raw_score: float,
    task_mode: TaskMode,
) -> float:
    """
    Adjust a raw 0-100 score by the current task_mode context.
    Returns an adjusted score clamped to [0, 100].
    """
    multiplier = _CONTEXT_MULTIPLIERS.get((alert_type, task_mode), 1.0)

    # Safety guard: in idle mode, never suppress tilt/proximity if already severe
    if task_mode == TaskMode.idle:
        if alert_type in (AlertType.tilt, AlertType.proximity) and raw_score >= _IDLE_SUPPRESS_THRESHOLD:
            multiplier = 1.0  # keep full weight for critical physical risks

    adjusted = raw_score * multiplier
    return round(min(100.0, max(0.0, adjusted)), 2)


# ──────────────────────────────────────────────
# Master scoring entry-point
# ──────────────────────────────────────────────

def compute_alerts(reading) -> List[ScoredAlert]:
    """
    Given a SensorReading, compute a ScoredAlert for every sensor field
    that is present in the reading. Returns a list sorted descending by
    adjusted_score — used by the suppression queue.

    'reading' is typed as Any to avoid circular import; it quacks like SensorReading.
    """
    from datetime import datetime

    task_mode: TaskMode = reading.task_mode or TaskMode.transport
    results: List[ScoredAlert] = []

    # — Proximity —
    if reading.proximity is not None:
        raw = score_proximity(reading.proximity)
        adj = apply_context_filter(AlertType.proximity, raw, task_mode)
        results.append(ScoredAlert(
            alert_type=AlertType.proximity,
            raw_score=round(raw, 2),
            adjusted_score=adj,
            message_context={"distance_m": reading.proximity},
            timestamp=datetime.utcnow(),
        ))

    # — Tilt —
    if reading.tilt is not None:
        raw = score_tilt(reading.tilt)
        adj = apply_context_filter(AlertType.tilt, raw, task_mode)
        results.append(ScoredAlert(
            alert_type=AlertType.tilt,
            raw_score=round(raw, 2),
            adjusted_score=adj,
            message_context={"angle_deg": reading.tilt},
            timestamp=datetime.utcnow(),
        ))

    # — Engine —
    if reading.engine_temp is not None or reading.engine_load is not None:
        raw = score_engine(reading.engine_temp, reading.engine_load)
        adj = apply_context_filter(AlertType.engine, raw, task_mode)
        ctx: dict = {}
        if reading.engine_temp is not None:
            ctx["temp_c"] = reading.engine_temp
        if reading.engine_load is not None:
            ctx["load_pct"] = reading.engine_load
        results.append(ScoredAlert(
            alert_type=AlertType.engine,
            raw_score=round(raw, 2),
            adjusted_score=adj,
            message_context=ctx,
            timestamp=datetime.utcnow(),
        ))

    # — Fatigue —
    if reading.fatigue is not None:
        raw = score_fatigue(reading.fatigue)
        adj = apply_context_filter(AlertType.fatigue, raw, task_mode)
        results.append(ScoredAlert(
            alert_type=AlertType.fatigue,
            raw_score=round(raw, 2),
            adjusted_score=adj,
            message_context={"fatigue_score": reading.fatigue},
            timestamp=datetime.utcnow(),
        ))

    # Sort descending by adjusted score (highest priority first)
    results.sort(key=lambda a: a.adjusted_score, reverse=True)

    # Mark all but the top alert as suppressed
    for i, alert in enumerate(results):
        alert.is_suppressed = (i > 0)

    return results
