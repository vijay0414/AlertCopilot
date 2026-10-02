"""
suppression.py — Per-machine priority queue (Step 4 of pipeline)

Maintains one queue per machine_id.  Only the TOP scored alert is "active"
(shown to the operator); all others are suppressed and re-evaluated on every
new sensor ingest.

This is the core arbitration logic that prevents alert fatigue — the whole
point of the Co-Pilot.
"""

from __future__ import annotations

import threading
from datetime import datetime
from typing import Dict, List, Optional

from models import AlertQueue, AlertQueueEntry, AlertType, ScoredAlert


class SuppressionQueue:
    """
    Thread-safe, per-machine priority queue.

    Internals
    ---------
    _queues: machine_id → list[ScoredAlert] sorted descending by adjusted_score
    _lock:   single RLock guards the entire dict (fine for single-server demo)
    """

    def __init__(self) -> None:
        self._queues: Dict[str, List[ScoredAlert]] = {}
        self._lock = threading.RLock()

    # ── Public API ──────────────────────────────────────────────

    def update(self, machine_id: str, alerts: List[ScoredAlert]) -> None:
        """
        Replace the queue for machine_id with a fresh list of scored alerts.
        Alerts are expected to arrive pre-sorted from scoring.compute_alerts().
        We re-sort here defensively and re-apply the suppression flag so the
        queue is always self-consistent.
        """
        sorted_alerts = sorted(alerts, key=lambda a: a.adjusted_score, reverse=True)

        # Re-stamp suppression flags
        for i, alert in enumerate(sorted_alerts):
            alert.is_suppressed = i > 0

        with self._lock:
            self._queues[machine_id] = sorted_alerts

    def top_alert(self, machine_id: str) -> Optional[ScoredAlert]:
        """Return the highest-priority (non-suppressed) alert, or None."""
        with self._lock:
            queue = self._queues.get(machine_id, [])
            return queue[0] if queue else None

    def get_queue_response(self, machine_id: str) -> AlertQueue:
        """
        Build the AlertQueue response body for GET /alerts/{machine_id}/queue.
        Returns a structured object with the top alert flagged separately from
        the suppressed list — ready for dashboard display.
        """
        with self._lock:
            alerts = self._queues.get(machine_id, [])

        now_str = datetime.utcnow().isoformat() + "Z"

        if not alerts:
            return AlertQueue(
                machine_id=machine_id,
                queried_at=now_str,
                active_alert=None,
                suppressed_alerts=[],
            )

        def _to_entry(alert: ScoredAlert, is_top: bool) -> AlertQueueEntry:
            return AlertQueueEntry(
                alert_type=alert.alert_type,
                adjusted_score=alert.adjusted_score,
                raw_score=alert.raw_score,
                message_context=alert.message_context,
                is_top=is_top,
                timestamp=alert.timestamp.isoformat() + "Z",
            )

        active = _to_entry(alerts[0], is_top=True)
        suppressed = [_to_entry(a, is_top=False) for a in alerts[1:]]

        return AlertQueue(
            machine_id=machine_id,
            queried_at=now_str,
            active_alert=active,
            suppressed_alerts=suppressed,
        )

    def all_machines(self) -> List[str]:
        """Return all machine IDs currently tracked."""
        with self._lock:
            return list(self._queues.keys())

    def clear(self, machine_id: str) -> None:
        """Clear the queue for a machine (e.g., after all-clear scenario)."""
        with self._lock:
            self._queues.pop(machine_id, None)


# Module-level singleton — imported by main.py and other modules
suppression_queue = SuppressionQueue()
