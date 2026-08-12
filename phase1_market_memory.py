"""PSYCHO SIGNAL FACTORY — Phase 1: Market Memory Engine.

Builds a research catalog from historical CSV/CSV.GZ data. It does not
produce trading signals; it converts historical data into reusable memory
for downstream phases.

No external Python dependencies are required.
"""
from __future__ import annotations

import csv
import gzip
import json
import math
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Tuple

ROOT = Path(os.getenv("PSYCHO_RESEARCH_ROOT", "research_data"))
OUT = Path(os.getenv("PSYCHO_MEMORY_ROOT", "phase1_memory"))
TIMESTAMP_KEYS = ("timestamp", "datetime", "date", "time")
NUMERIC_KEYS = ("open", "high", "low", "close", "volume", "oi", "iv", "strike", "spot")


def _open_text(path: Path):
    return gzip.open(path, "rt", encoding="utf-8", newline="") if path.suffix == ".gz" else path.open("r", encoding="utf-8", newline="")


def _find_key(fieldnames: List[str], candidates: Iterable[str]) -> Optional[str]:
    normalized = {f.strip().lower(): f for f in fieldnames}
    for candidate in candidates:
        if candidate in normalized:
            return normalized[candidate]
    return None


def _parse_number(value) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def _parse_timestamp(value) -> Optional[datetime]:
    if value is None or value == "":
        return None
    s = str(value).strip()
    try:
        if s.isdigit():
            return datetime.fromtimestamp(int(s), tz=timezone.utc)
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def iter_rows(path: Path) -> Iterator[dict]:
    with _open_text(path) as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            return
        ts_key = _find_key(reader.fieldnames, TIMESTAMP_KEYS)
        mapped = {name: _find_key(reader.fieldnames, (name,)) for name in NUMERIC_KEYS}
        for raw in reader:
            dt = _parse_timestamp(raw.get(ts_key)) if ts_key else None
            if dt is None:
                continue
            row = {"timestamp": dt.isoformat()}
            for name, key in mapped.items():
                row[name] = _parse_number(raw.get(key)) if key else None
            yield row


def _safe_return(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a in (None, 0) or b is None:
        return None
    return (b / a) - 1.0


def summarize_dataset(path: Path) -> Tuple[dict, List[dict]]:
    rows = list(iter_rows(path))
    sessions: Dict[str, List[dict]] = defaultdict(list)
    for row in rows:
        sessions[row["timestamp"][:10]].append(row)

    session_memory: List[dict] = []
    for day, day_rows in sorted(sessions.items()):
        day_rows.sort(key=lambda r: r["timestamp"])
        opens = [r["open"] for r in day_rows if r["open"] is not None]
        highs = [r["high"] for r in day_rows if r["high"] is not None]
        lows = [r["low"] for r in day_rows if r["low"] is not None]
        closes = [r["close"] for r in day_rows if r["close"] is not None]
        volumes = [r["volume"] for r in day_rows if r["volume"] is not None]
        ois = [r["oi"] for r in day_rows if r["oi"] is not None]
        if not closes:
            continue
        session_memory.append({
            "dataset": path.as_posix(),
            "session_date": day,
            "first_timestamp": day_rows[0]["timestamp"],
            "last_timestamp": day_rows[-1]["timestamp"],
            "open": opens[0] if opens else None,
            "high": max(highs) if highs else None,
            "low": min(lows) if lows else None,
            "close": closes[-1],
            "session_return": _safe_return(closes[0], closes[-1]),
            "range_pct": _safe_return(min(lows), max(highs)) if lows and highs else None,
            "volume": sum(volumes) if volumes else None,
            "oi_first": ois[0] if ois else None,
            "oi_last": ois[-1] if ois else None,
            "oi_change": (ois[-1] - ois[0]) if len(ois) >= 2 else None,
            "bars": len(day_rows),
        })

    closes = [r["close"] for r in rows if r["close"] is not None]
    meta = {
        "dataset": path.as_posix(),
        "rows": len(rows),
        "sessions": len(session_memory),
        "first_timestamp": rows[0]["timestamp"] if rows else None,
        "last_timestamp": rows[-1]["timestamp"] if rows else None,
        "has_ohlcv": all(any(r[k] is not None for r in rows) for k in ("open", "high", "low", "close", "volume")) if rows else False,
        "has_oi": any(r["oi"] is not None for r in rows),
        "has_iv": any(r["iv"] is not None for r in rows),
        "has_strike": any(r["strike"] is not None for r in rows),
        "has_spot": any(r["spot"] is not None for r in rows),
        "close_min": min(closes) if closes else None,
        "close_max": max(closes) if closes else None,
    }
    return meta, session_memory


def discover_files(root: Path) -> List[Path]:
    if not root.exists():
        return []
    return sorted(p for p in root.rglob("*") if p.is_file() and p.suffix in {".csv", ".gz"})


def build_memory() -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    files = discover_files(ROOT)
    catalog = []
    sessions_path = OUT / "session_memory.jsonl"
    with sessions_path.open("w", encoding="utf-8") as sink:
        for path in files:
            meta, sessions = summarize_dataset(path)
            catalog.append(meta)
            for item in sessions:
                sink.write(json.dumps(item, separators=(",", ":")) + "\n")

    manifest = {
        "phase": "PHASE_1_MARKET_MEMORY",
        "version": "1.0",
        "status": "BUILT",
        "source_root": str(ROOT),
        "dataset_count": len(catalog),
        "session_count": sum(x["sessions"] for x in catalog),
        "datasets": catalog,
        "outputs": {"dataset_catalog": str(OUT / "dataset_catalog.json"), "session_memory": str(sessions_path)},
    }
    (OUT / "dataset_catalog.json").write_text(json.dumps(catalog, indent=2), encoding="utf-8")
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


if __name__ == "__main__":
    print(json.dumps(build_memory(), indent=2))
