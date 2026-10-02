"""
llm.py — Plain-language instruction generation (Step 5 of pipeline)

Two-path design:
  1. PRIMARY: Groq API (llama-3.1-8b-instant) — fast, ~200ms typical latency
  2. FALLBACK: Local template dictionary — works with ZERO connectivity / bad key

The fallback is chosen automatically if:
  - The Groq API key is missing / invalid
  - The API call raises any exception
  - The API call takes longer than LLM_TIMEOUT_SECONDS (default: 2s)

Which path was used is logged (source: "llm" | "fallback") — useful demo talking point.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

import httpx
from dotenv import load_dotenv

from models import AlertType, InstructionSource

load_dotenv()

logger = logging.getLogger(__name__)

GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL: str = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
LLM_TIMEOUT: float = float(os.getenv("LLM_TIMEOUT_SECONDS", "2"))


# ──────────────────────────────────────────────
# Fallback template dictionary
# keyed by (AlertType, severity_tier: "low"|"medium"|"high"|"critical")
# ──────────────────────────────────────────────

def _severity_tier(score: float) -> str:
    if score >= 85:
        return "critical"
    elif score >= 60:
        return "high"
    elif score >= 30:
        return "medium"
    else:
        return "low"


_FALLBACK_TEMPLATES: dict[tuple[str, str], str] = {
    # Proximity
    ("proximity", "critical"): "Stop immediately — obstacle within 1 metre, do not move.",
    ("proximity", "high"):     "Halt movement — obstacle detected within 3 metres ahead.",
    ("proximity", "medium"):   "Slow down and check surroundings — obstacle within 5 metres.",
    ("proximity", "low"):      "Proceed with caution, monitor proximity sensor readings.",

    # Tilt
    ("tilt", "critical"):      "Stop all movement — machine tilt exceeds safe limits, engage stabilisers.",
    ("tilt", "high"):          "Cease digging — tilt angle dangerously high, level the machine now.",
    ("tilt", "medium"):        "Reduce bucket load and re-position — tilt angle above recommended threshold.",
    ("tilt", "low"):           "Monitor tilt — angle slightly elevated, avoid lateral movement.",

    # Engine
    ("engine", "critical"):    "Shut down engine immediately — critical overtemperature or overload detected.",
    ("engine", "high"):        "Reduce engine load now — temperature or load in danger zone.",
    ("engine", "medium"):      "Ease off throttle — engine running above optimal parameters.",
    ("engine", "low"):         "Engine warm — continue monitoring temperature and load gauges.",

    # Fatigue
    ("fatigue", "critical"):   "Stop operations now — fatigue level critical, take a mandatory break.",
    ("fatigue", "high"):       "Park machine safely and rest — operator fatigue is dangerously high.",
    ("fatigue", "medium"):     "Take a 15-minute break within the next 30 minutes — fatigue building.",
    ("fatigue", "low"):        "Stay alert — mild fatigue detected, hydrate and stay focused.",

    # All-clear default
    ("none", "low"):           "All systems nominal — proceed with normal operations.",
}


def get_fallback_instruction(alert_type: Optional[str], score: float) -> str:
    """Look up a pre-written instruction from the local template dictionary."""
    tier = _severity_tier(score) if score > 0 else "low"
    key = (str(alert_type).lower() if alert_type else "none", tier)
    return _FALLBACK_TEMPLATES.get(key, "Monitor all systems and proceed with caution.")


# ──────────────────────────────────────────────
# Groq API call
# ──────────────────────────────────────────────

def _build_prompt(alert_type: str, score: float, context: dict) -> str:
    ctx_str = ", ".join(f"{k}={v}" for k, v in context.items())
    return (
        f"Given this machine safety alert: {alert_type}, "
        f"severity {round(score)}/100, context: {ctx_str}. "
        "Respond with ONE short imperative safety instruction sentence "
        "(max 12 words) a heavy machinery operator can act on immediately. "
        "No preamble, no explanation — just the instruction."
    )


async def call_groq(alert_type: str, score: float, context: dict) -> Optional[str]:
    """
    Call Groq's OpenAI-compatible endpoint.
    Returns the instruction string, or None on any failure.
    Times out after LLM_TIMEOUT seconds to guarantee snappy fallback.
    """
    if not GROQ_API_KEY or GROQ_API_KEY == "your_groq_api_key_here":
        logger.warning("llm: No valid GROQ_API_KEY found — skipping LLM call.")
        return None

    prompt = _build_prompt(alert_type, score, context)

    payload = {
        "model": GROQ_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 40,
        "temperature": 0.3,  # Low temperature = more deterministic safety instructions
    }
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=LLM_TIMEOUT) as client:
            response = await client.post(GROQ_API_URL, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
            text: str = data["choices"][0]["message"]["content"].strip()
            # Strip leading/trailing quotes if the model added them
            text = text.strip('"').strip("'")
            logger.info("llm: Groq responded successfully (model=%s)", GROQ_MODEL)
            return text

    except httpx.TimeoutException:
        logger.warning("llm: Groq API timed out after %.1fs — using fallback.", LLM_TIMEOUT)
        return None
    except httpx.HTTPStatusError as exc:
        logger.warning("llm: Groq API HTTP error %s — using fallback.", exc.response.status_code)
        return None
    except Exception as exc:
        logger.warning("llm: Groq API call failed (%s) — using fallback.", exc)
        return None


# ──────────────────────────────────────────────
# Public entry-point used by main.py
# ──────────────────────────────────────────────

async def generate_instruction(
    alert_type: Optional[str],
    score: float,
    context: dict,
) -> tuple[str, InstructionSource]:
    """
    Returns (instruction_text, source) where source is "llm" or "fallback".
    Always succeeds — the fallback dictionary guarantees a non-empty response.
    """
    if alert_type is None or score == 0:
        return get_fallback_instruction(None, 0), InstructionSource.fallback

    # Try primary LLM path
    llm_result = await call_groq(str(alert_type), score, context)

    if llm_result:
        return llm_result, InstructionSource.llm

    # Automatic fallback
    fallback_text = get_fallback_instruction(str(alert_type), score)
    logger.info("llm: Using fallback template for %s @ score=%.0f", alert_type, score)
    return fallback_text, InstructionSource.fallback
