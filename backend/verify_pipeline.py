"""
verify_pipeline.py — Offline verification of the core pipeline logic
Tests the deliverable checklist WITHOUT starting the server.
Run with: python verify_pipeline.py
"""

import asyncio
import sys
from datetime import datetime

# ── Force a bad API key so we test the fallback path ──────────────────────
import os
os.environ["GROQ_API_KEY"] = "bad_key_for_fallback_test"

from models import SensorReading, TaskMode
from scoring import compute_alerts
from suppression import suppression_queue
from storage import fleet_logger, machine_store
from simulator import generate_scenario
from llm import generate_instruction, get_fallback_instruction


PASS = "✅"
FAIL = "❌"
results = []

def check(name, condition, details=""):
    status = PASS if condition else FAIL
    results.append((status, name, details))
    print(f"  {status} {name}" + (f"\n     {details}" if details else ""))


print("\n" + "═" * 60)
print("  Alert Arbitration Co-Pilot — Pipeline Verification")
print("═" * 60)


# ──────────────────────────────────────────────────────────────
# CHECK 1: multi_alert scenario produces reading with all 4 risks
# ──────────────────────────────────────────────────────────────
print("\n[1] POST /sensors/simulate — scenario: multi_alert")
reading = generate_scenario("EXC-001", "multi_alert")
check("multi_alert reading has proximity", reading.proximity is not None, f"proximity={reading.proximity}m")
check("multi_alert reading has tilt",      reading.tilt is not None,      f"tilt={reading.tilt}°")
check("multi_alert reading has engine",    reading.engine_temp is not None, f"engine_temp={reading.engine_temp}°C")
check("multi_alert reading has fatigue",   reading.fatigue is not None,   f"fatigue={reading.fatigue}")
check("task_mode is active",               reading.task_mode == TaskMode.active)


# ──────────────────────────────────────────────────────────────
# CHECK 2: Scoring produces 4 alerts; queue is sorted correctly
# ──────────────────────────────────────────────────────────────
print("\n[2] GET /alerts/{machine_id}/queue — priority ordering")
alerts = compute_alerts(reading)
machine_store.upsert(reading)
suppression_queue.update("EXC-001", alerts)
queue_resp = suppression_queue.get_queue_response("EXC-001")

check("4 alerts scored",              len(alerts) == 4,                         f"got {len(alerts)}")
check("active_alert is set",          queue_resp.active_alert is not None)
check("top alert is_top=True",        queue_resp.active_alert.is_top if queue_resp.active_alert else False)
check("3 suppressed alerts",          len(queue_resp.suppressed_alerts) == 3,   f"got {len(queue_resp.suppressed_alerts)}")
check("top score >= all others",
      all(queue_resp.active_alert.adjusted_score >= s.adjusted_score for s in queue_resp.suppressed_alerts)
      if queue_resp.active_alert else False,
      f"top={queue_resp.active_alert.adjusted_score if queue_resp.active_alert else 'N/A'}")

print("\n   Alert priority order:")
if queue_resp.active_alert:
    print(f"   ★ TOP    [{queue_resp.active_alert.alert_type}] score={queue_resp.active_alert.adjusted_score}")
for s in queue_resp.suppressed_alerts:
    print(f"       ↓  [{s.alert_type}] score={s.adjusted_score} (suppressed)")


# ──────────────────────────────────────────────────────────────
# CHECK 3a: Fallback instruction path (bad API key)
# ──────────────────────────────────────────────────────────────
print("\n[3a] GET /alerts/EXC-001/instruction — FALLBACK path (bad API key)")

async def test_fallback():
    top = suppression_queue.top_alert("EXC-001")
    instruction_text, source = await generate_instruction(
        alert_type=top.alert_type.value if top else None,
        score=top.adjusted_score if top else 0,
        context=top.message_context if top else {},
    )
    return instruction_text, source

text, source = asyncio.run(test_fallback())
check("fallback instruction returned",   bool(text),   f'"{text}"')
check("source is fallback",              source.value == "fallback", f"source={source.value}")


# ──────────────────────────────────────────────────────────────
# CHECK 3b: Direct fallback template lookup
# ──────────────────────────────────────────────────────────────
print("\n[3b] Fallback template dictionary — all alert types")
from models import AlertType
for atype in AlertType:
    for score, tier in [(95, "critical"), (70, "high"), (45, "medium"), (10, "low")]:
        tmpl = get_fallback_instruction(atype.value, score)
        check(f"template [{atype.value}/{tier}]", bool(tmpl), tmpl[:60] + "...")


# ──────────────────────────────────────────────────────────────
# CHECK 4: Fleet summary shows non-zero counts
# ──────────────────────────────────────────────────────────────
print("\n[4] GET /fleet/summary — non-zero counts after simulations")

# Run a few more scenarios to populate counts
for scenario in ["proximity_warning", "tilt_critical", "fatigue_high", "engine_overload"]:
    r = generate_scenario("EXC-002", scenario)
    a = compute_alerts(r)
    machine_store.upsert(r)
    suppression_queue.update("EXC-002", a)
    now = datetime.utcnow().isoformat() + "Z"
    from models import FleetLogEntry
    for alert in a:
        fleet_logger.append(FleetLogEntry(
            machine_id="EXC-002",
            timestamp=now,
            alert_type=alert.alert_type.value,
            raw_score=alert.raw_score,
            adjusted_score=alert.adjusted_score,
            is_suppressed=alert.is_suppressed,
            instruction_text=None,
            instruction_source=None,
            scenario=scenario,
        ))

summary = fleet_logger.summary()
check("total_alerts_processed > 0",    summary.total_alerts_processed > 0,  f"total={summary.total_alerts_processed}")
check("alerts_shown > 0",              summary.alerts_shown > 0,            f"shown={summary.alerts_shown}")
check("alerts_suppressed > 0",         summary.alerts_suppressed > 0,       f"suppressed={summary.alerts_suppressed}")
check("by_alert_type has entries",     len(summary.by_alert_type) > 0,      str(summary.by_alert_type))

print(f"\n   Summary: {summary.model_dump_json(indent=2)}")


# ──────────────────────────────────────────────────────────────
# CHECK 5: Context filter — idle mode suppresses engine/fatigue
# ──────────────────────────────────────────────────────────────
print("\n[5] Context filter — idle mode dampens engine/fatigue")
idle_reading = SensorReading(
    machine_id="EXC-003",
    engine_temp=100.0,
    engine_load=80.0,
    fatigue=60.0,
    proximity=6.0,
    tilt=4.0,
    task_mode=TaskMode.idle,
)
idle_alerts = compute_alerts(idle_reading)

# Engine raw ~60; after idle (×0.4) should be ~24
engine_alert = next((a for a in idle_alerts if a.alert_type.value == "engine"), None)
fatigue_alert = next((a for a in idle_alerts if a.alert_type.value == "fatigue"), None)

if engine_alert:
    check("engine score dampened in idle",
          engine_alert.adjusted_score < engine_alert.raw_score,
          f"raw={engine_alert.raw_score} adj={engine_alert.adjusted_score}")
if fatigue_alert:
    check("fatigue score dampened in idle",
          fatigue_alert.adjusted_score < fatigue_alert.raw_score,
          f"raw={fatigue_alert.raw_score} adj={fatigue_alert.adjusted_score}")

# ──────────────────────────────────────────────────────────────
# FINAL REPORT
# ──────────────────────────────────────────────────────────────
print("\n" + "═" * 60)
passed = sum(1 for r in results if r[0] == PASS)
failed = sum(1 for r in results if r[0] == FAIL)
print(f"  RESULT: {passed} passed / {failed} failed")
print("═" * 60 + "\n")

if failed > 0:
    sys.exit(1)
