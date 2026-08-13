#!/usr/bin/env python3
"""PSY29 Stage 3: deterministic live-data validation and normalization."""

from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite
from typing import Any, Dict, Iterable, List

FRESH_SECONDS = 90
STALE_SECONDS = 180
REQUIRED_TFS = ("1m", "5m")


def _num(value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError("boolean is not numeric")
    value = float(value)
    if not isfinite(value):
        raise ValueError("non-finite numeric value")
    return value


def normalize_candles(candles: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    last_ts = None
    for raw in candles or []:
        ts = int(raw["timestamp"])
        if last_ts is not None and ts <= last_ts:
            raise ValueError("non-monotonic candle timestamps")
        o, h, l, c, v = (_num(raw[k]) for k in ("open", "high", "low", "close", "volume"))
        if h < max(o, c) or l > min(o, c):
            raise ValueError("invalid OHLC relationship")
        if v < 0:
            raise ValueError("negative volume")
        out.append({"timestamp": ts, "open": o, "high": h, "low": l, "close": c, "volume": v})
        last_ts = ts
    return out


def freshness(generated_at: str, now: datetime | None = None) -> tuple[int, str]:
    dt = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    age = max(0, int((now - dt.astimezone(timezone.utc)).total_seconds()))
    status = "FRESH" if age <= FRESH_SECONDS else "STALE" if age <= STALE_SECONDS else "INVALID"
    return age, status


def validate_stock_payload(payload: Dict[str, Any], canonical_symbols: set[str], now: datetime | None = None) -> Dict[str, Any]:
    errors: List[str] = []
    symbol = str(payload.get("symbol", "")).upper()
    if symbol not in canonical_symbols:
        errors.append("UNKNOWN_SYMBOL")
    if not payload.get("security_id"):
        errors.append("MISSING_SECURITY_ID")
    generated_at = payload.get("generated_at")
    age = None
    freshness_status = "INVALID"
    if not generated_at:
        errors.append("MISSING_GENERATED_AT")
    else:
        try:
            age, freshness_status = freshness(generated_at, now)
        except Exception:
            errors.append("INVALID_GENERATED_AT")
    candles = payload.get("candles") or {}
    normalized: Dict[str, List[Dict[str, Any]]] = {}
    for tf in REQUIRED_TFS:
        if not candles.get(tf):
            errors.append(f"MISSING_{tf.upper()}_CANDLES")
            continue
        try:
            normalized[tf] = normalize_candles(candles[tf])
        except (KeyError, TypeError, ValueError):
            errors.append(f"INVALID_{tf.upper()}_CANDLES")
    if freshness_status == "INVALID":
        errors.append("DATA_TOO_OLD")
    status = "VALID" if not errors else "INVALID"
    return {
        "symbol": symbol,
        "status": status,
        "data_status": freshness_status if not errors else "INVALID",
        "signal_status": "ELIGIBLE_FOR_EVALUATION" if status == "VALID" and freshness_status == "FRESH" else "NO_TRADE",
        "data_age_seconds": age,
        "errors": errors,
        "normalized": normalized,
    }


def validate_universe(payloads: Iterable[Dict[str, Any]], canonical_symbols: set[str], now: datetime | None = None) -> Dict[str, Any]:
    results = [validate_stock_payload(p, canonical_symbols, now) for p in payloads]
    return {
        "total": len(results),
        "valid": sum(r["status"] == "VALID" for r in results),
        "invalid": sum(r["status"] == "INVALID" for r in results),
        "results": results,
    }


if __name__ == "__main__":
    print("PSY29 Stage 3 validator module: OK")
