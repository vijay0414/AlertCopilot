"""
storage.py — In-memory machine state + JSON-Lines fleet logger

Two responsibilities:
  1. MachineStateStore  — holds the latest SensorReading per machine (in RAM)
  2. FleetLogger        — appends structured log entries to data/fleet_log.jsonl
                          (no MongoDB needed; the file is JSONL so it's trivially
                          parseable by pandas/jq for any post-demo analytics)

Both are module-level singletons imported by main.py.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from dotenv import load_dotenv

from models import FleetLogEntry, FleetSummary, SensorReading

load_dotenv()

logger = logging.getLogger(__name__)

# Path for the JSONL fleet log file
FLEET_LOG_PATH = Path(os.getenv("FLEET_LOG_PATH", "data/fleet_log.jsonl"))


# ──────────────────────────────────────────────
# 1. In-memory machine state store
# ──────────────────────────────────────────────

class MachineStateStore:
    """
    Stores the most recent SensorReading per machine_id.
    Thread-safe with a simple RLock.
    """

    def __init__(self) -> None:
        self._state: Dict[str, SensorReading] = {}
        self._lock = threading.RLock()

    def upsert(self, reading: SensorReading) -> None:
        """Store (or overwrite) the latest reading for a machine."""
        with self._lock:
            self._state[reading.machine_id] = reading
        logger.debug("storage: Updated state for machine %s", reading.machine_id)

    def get(self, machine_id: str) -> Optional[SensorReading]:
        with self._lock:
            return self._state.get(machine_id)

    def all_ids(self) -> List[str]:
        with self._lock:
            return list(self._state.keys())


# ──────────────────────────────────────────────
# 2. Fleet logger (JSON-Lines file)
# ──────────────────────────────────────────────

class FleetLogger:
    """
    Appends FleetLogEntry objects as newline-delimited JSON to FLEET_LOG_PATH.
    Also maintains in-memory counters for fast /fleet/summary responses.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        # Ensure data directory exists
        FLEET_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

        # In-memory counters (reset on server restart — fine for demo)
        self._total: int = 0
        self._shown: int = 0
        self._suppressed: int = 0
        self._by_type: Dict[str, int] = defaultdict(int)
        self._llm_calls: int = 0
        self._fallback_calls: int = 0

    def append(self, entry: FleetLogEntry) -> None:
        """Write one log entry to the JSONL file and update counters."""
        with self._lock:
            # Write to file
            try:
                with FLEET_LOG_PATH.open("a", encoding="utf-8") as f:
                    f.write(entry.model_dump_json() + "\n")
            except OSError as exc:
                logger.error("storage: Failed to write fleet log: %s", exc)

            # Update counters
            self._total += 1
            if entry.is_suppressed:
                self._suppressed += 1
            else:
                self._shown += 1

            if entry.alert_type:
                self._by_type[entry.alert_type] += 1

            if entry.instruction_source == "llm":
                self._llm_calls += 1
            elif entry.instruction_source == "fallback":
                self._fallback_calls += 1

        logger.debug(
            "storage: Logged alert [%s] machine=%s suppressed=%s",
            entry.alert_type, entry.machine_id, entry.is_suppressed,
        )

    def recent_entries(self, n: int = 50) -> List[dict]:
        """Return the last N entries from the JSONL file as raw dicts."""
        with self._lock:
            if not FLEET_LOG_PATH.exists():
                return []
            try:
                lines = FLEET_LOG_PATH.read_text(encoding="utf-8").splitlines()
                tail = lines[-n:] if len(lines) >= n else lines
                return [json.loads(line) for line in tail if line.strip()]
            except (OSError, json.JSONDecodeError) as exc:
                logger.error("storage: Failed to read fleet log: %s", exc)
                return []

    def summary(self) -> FleetSummary:
        """Return aggregate stats from in-memory counters (fast, no file I/O)."""
        with self._lock:
            return FleetSummary(
                total_alerts_processed=self._total,
                alerts_shown=self._shown,
                alerts_suppressed=self._suppressed,
                by_alert_type=dict(self._by_type),
                llm_calls=self._llm_calls,
                fallback_calls=self._fallback_calls,
            )


# ──────────────────────────────────────────────
# Module-level singletons
# ──────────────────────────────────────────────

machine_store = MachineStateStore()
fleet_logger = FleetLogger()
