"""
events.py — In-process MQTT-shaped pub/sub event bus

NOTE: This is an MQTT-shaped implementation using Python asyncio queues.
      The publish/subscribe pattern is identical to real MQTT topics.
      To swap in a real Mosquitto broker later, replace the calls here
      with paho-mqtt publish/subscribe calls — no changes needed in main.py.

Topics used:
  - "sensors/{machine_id}/reading"   — raw sensor ingest
  - "alerts/{machine_id}/scored"     — scored alert list after edge processing
  - "alerts/{machine_id}/active"     — top alert (post-suppression)

For the hackathon, we process synchronously in the API handler rather than
through the bus, but the bus is wired up so events flow through it and any
subscriber (e.g., a future WebSocket push layer) can tap in.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any, Callable, Dict, List

logger = logging.getLogger(__name__)


class EventBus:
    """
    Minimal in-process pub/sub broker that mimics MQTT topic semantics.
    Subscribers are async callables registered per topic string.
    """

    def __init__(self) -> None:
        # topic → list of async subscriber coroutines
        self._subscribers: Dict[str, List[Callable]] = defaultdict(list)

    def subscribe(self, topic: str, handler: Callable) -> None:
        """Register an async handler for a topic."""
        self._subscribers[topic].append(handler)
        logger.debug("eventbus: subscribed to topic '%s'", topic)

    async def publish(self, topic: str, payload: Any) -> None:
        """
        Publish payload to a topic — all registered handlers are called
        concurrently via asyncio.gather.
        """
        handlers = self._subscribers.get(topic, [])
        if not handlers:
            logger.debug("eventbus: no subscribers for topic '%s'", topic)
            return
        await asyncio.gather(
            *(h(payload) for h in handlers),
            return_exceptions=True,  # don't let one bad handler crash others
        )
        logger.debug("eventbus: published to '%s' (%d handlers)", topic, len(handlers))


# Module-level singleton
event_bus = EventBus()


# ── Topic name helpers ──────────────────────────────────────────────────────

def topic_sensor_reading(machine_id: str) -> str:
    return f"sensors/{machine_id}/reading"

def topic_alerts_scored(machine_id: str) -> str:
    return f"alerts/{machine_id}/scored"

def topic_alert_active(machine_id: str) -> str:
    return f"alerts/{machine_id}/active"
