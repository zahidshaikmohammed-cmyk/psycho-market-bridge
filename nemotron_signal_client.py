import json
import os
from typing import Any, Dict

from openai import OpenAI

NEMOTRON_BASE_URL = os.getenv("NEMOTRON_BASE_URL", "https://integrate.api.nvidia.com/v1")
NEMOTRON_MODEL = os.getenv("NEMOTRON_MODEL", "nvidia/nemotron-3-nano-30b-a3b")

SYSTEM_PROMPT = """You are the Phase 4 BANKNIFTY market-state reasoning layer.\n\nYou receive deterministic V6R1 market-state features. Do not invent missing market data. Do not calculate option entry, stop-loss, target, or contract selection. Your job is only to determine whether the supplied state represents a fresh actionable directional opportunity.\n\nReturn ONLY valid JSON with keys: decision, direction, confidence, signal_quality, reason_codes, invalidations.\ndecision must be TRADE or NO_TRADE.\ndirection must be LONG, SHORT, or NONE. confidence must be 0..1. signal_quality must be A, B, C, D, or REJECT. reason_codes and invalidations must be arrays of short strings.\n"""


def _client() -> OpenAI:
    key = os.getenv("NVIDIA_API_KEY")
    if not key:
        raise RuntimeError("NVIDIA_API_KEY is not configured")
    return OpenAI(base_url=NEMOTRON_BASE_URL, api_key=key)


def evaluate_market_state(state: Dict[str, Any]) -> Dict[str, Any]:
    client = _client()
    payload = json.dumps(state, separators=(",", ":"), ensure_ascii=False)
    response = client.chat.completions.create(
        model=NEMOTRON_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": payload},
        ],
        temperature=0.0,
        top_p=1.0,
        max_tokens=1200,
    )
    text = response.choices[0].message.content or ""
    try:
        result = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError("NEMOTRON_INVALID_JSON") from exc

    required = {"decision", "direction", "confidence", "signal_quality", "reason_codes", "invalidations"}
    if set(result) < required:
        raise RuntimeError("NEMOTRON_SCHEMA_INVALID")
    if result["decision"] not in {"TRADE", "NO_TRADE"}:
        raise RuntimeError("NEMOTRON_DECISION_INVALID")
    if result["direction"] not in {"LONG", "SHORT", "NONE"}:
        raise RuntimeError("NEMOTRON_DIRECTION_INVALID")
    result["confidence"] = max(0.0, min(1.0, float(result["confidence"])))
    return result
