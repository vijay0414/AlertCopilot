"""
simulator.py — Named demo scenario generators (Step 1b of pipeline)

Each scenario function returns a SensorReading with realistic synthetic values.
These are triggered via POST /sensors/simulate — no physical sensors required.
Great for live demo without wiring anything up.

Scenarios:
  - tilt_critical      Imminent tip-over (excavator on slope)
  - proximity_warning  Obstacle approaching, not yet critical
  - multi_alert        Multiple concurrent risks (the flagship demo)
  - fatigue_high       Operator nearing incapacitation
  - engine_overload    Engine temp + load both spiking
  - all_clear          All readings nominal
"""

from __future__ import annotations

from datetime import datetime
from typing import Dict

from models import SensorReading, TaskMode


# ──────────────────────────────────────────────
# Scenario definitions
# ──────────────────────────────────────────────

def _scenario_tilt_critical(machine_id: str) -> SensorReading:
    """
    Crane/excavator at 28° tilt — well above the 25° critical threshold.
    Active work mode amplifies the tilt score further.
    """
    return SensorReading(
        machine_id=machine_id,
        timestamp=datetime.utcnow(),
        proximity=8.0,          # safe distance — focus is on tilt
        tilt=28.0,              # CRITICAL: >25° → score 90+
        engine_temp=88.0,       # normal engine temp
        engine_load=65.0,       # moderate load
        fatigue=35.0,           # low fatigue
        task_mode=TaskMode.active,
    )


def _scenario_proximity_warning(machine_id: str) -> SensorReading:
    """
    Loader approaching a worker / vehicle at 2.3 m — high proximity risk.
    Transport mode, standard scoring.
    """
    return SensorReading(
        machine_id=machine_id,
        timestamp=datetime.utcnow(),
        proximity=2.3,          # HIGH: <3m → score 60-89
        tilt=3.0,               # negligible tilt
        engine_temp=92.0,       # slightly warm
        engine_load=55.0,       # moderate
        fatigue=40.0,           # moderate fatigue
        task_mode=TaskMode.transport,
    )


def _scenario_multi_alert(machine_id: str) -> SensorReading:
    """
    Flagship demo scenario: excavator with FOUR concurrent risks.
      - Proximity: 0.8 m (critical)
      - Tilt: 18°  (high)
      - Engine load: 91% + temp 105°C (high)
      - Fatigue: 72 (high → amplified to critical)
    Active mode → proximity & tilt scores both ×1.2.
    The arbitration engine must pick ONE instruction for the operator.
    """
    return SensorReading(
        machine_id=machine_id,
        timestamp=datetime.utcnow(),
        proximity=0.8,          # CRITICAL: <1m → score 92+
        tilt=18.0,              # HIGH: >15° → score 60-75
        engine_temp=105.0,      # HIGH: >100°C → score 60-75
        engine_load=91.0,       # HIGH: >85% → score 60+
        fatigue=72.0,           # HIGH: fatigue 72 → score ~79
        task_mode=TaskMode.active,
    )


def _scenario_fatigue_high(machine_id: str) -> SensorReading:
    """
    Operator fatigue at 85/100 — dangerously impaired.
    Idle mode: engine/fatigue would normally be suppressed 60%,
    but at this level the fatigue score still clears 60 after dampening.
    """
    return SensorReading(
        machine_id=machine_id,
        timestamp=datetime.utcnow(),
        proximity=12.0,         # safe
        tilt=2.0,               # negligible
        engine_temp=85.0,       # normal
        engine_load=45.0,       # low
        fatigue=85.0,           # CRITICAL: → score ~93
        task_mode=TaskMode.idle,
    )


def _scenario_engine_overload(machine_id: str) -> SensorReading:
    """
    Engine overheating (112°C) and overloaded (96%) simultaneously.
    Transport mode — standard scoring applies.
    """
    return SensorReading(
        machine_id=machine_id,
        timestamp=datetime.utcnow(),
        proximity=7.0,          # safe
        tilt=5.0,               # negligible
        engine_temp=112.0,      # CRITICAL: >110°C → score 90+
        engine_load=96.0,       # CRITICAL: >95% → score 92+
        fatigue=50.0,           # moderate fatigue
        task_mode=TaskMode.transport,
    )


def _scenario_all_clear(machine_id: str) -> SensorReading:
    """
    All readings nominal — useful for resetting the queue during demo.
    Should result in all low scores and no actionable alerts.
    """
    return SensorReading(
        machine_id=machine_id,
        timestamp=datetime.utcnow(),
        proximity=15.0,
        tilt=1.5,
        engine_temp=82.0,
        engine_load=40.0,
        fatigue=20.0,
        task_mode=TaskMode.transport,
    )


# ──────────────────────────────────────────────
# Registry — maps scenario name → generator function
# ──────────────────────────────────────────────

SCENARIO_REGISTRY: Dict[str, object] = {
    "tilt_critical":     _scenario_tilt_critical,
    "proximity_warning": _scenario_proximity_warning,
    "multi_alert":       _scenario_multi_alert,
    "fatigue_high":      _scenario_fatigue_high,
    "engine_overload":   _scenario_engine_overload,
    "all_clear":         _scenario_all_clear,
}


def generate_scenario(machine_id: str, scenario: str) -> SensorReading:
    """
    Entry-point called by POST /sensors/simulate.
    Raises ValueError if the scenario name is not recognised.
    """
    fn = SCENARIO_REGISTRY.get(scenario.lower())
    if fn is None:
        available = ", ".join(sorted(SCENARIO_REGISTRY.keys()))
        raise ValueError(
            f"Unknown scenario '{scenario}'. Available: {available}"
        )
    return fn(machine_id)  # type: ignore[operator]
