#!/usr/bin/env python3
"""PSY29 Stage 4: resolve the locked 29-stock universe to Dhan NSE_EQ security IDs.

The resolver deliberately uses Dhan's instrument master as the source of truth.
It fails closed on missing or ambiguous mappings and never substitutes a symbol.
"""

from __future__ import annotations

import csv
import io
import json
import os
import urllib.request
from pathlib import Path
from typing import Dict, Iterable

INSTRUMENT_MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"
UNIVERSE_PATH = Path("config/ps29_universe.json")
OUTPUT_PATH = Path("config/ps29_dhan_security_map.json")


def load_universe(path: Path = UNIVERSE_PATH) -> list[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    symbols = [row["symbol"] for row in data["stocks"]]
    if len(symbols) != 29 or len(set(symbols)) != 29:
        raise RuntimeError("PSY29 universe must contain exactly 29 unique symbols")
    return symbols


def fetch_master(url: str = INSTRUMENT_MASTER_URL) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "PSY29/1.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8-sig")


def _field(row: Dict[str, str], *names: str) -> str:
    for name in names:
        value = row.get(name)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def resolve(symbols: Iterable[str], csv_text: str) -> dict:
    reader = csv.DictReader(io.StringIO(csv_text))
    rows = list(reader)
    result = {}
    failures = {}

    for symbol in symbols:
        matches = []
        for row in rows:
            exchange = _field(row, "SEM_EXM_EXCH_ID", "EXCH_ID").upper()
            segment = _field(row, "SEM_SEGMENT", "SEGMENT").upper()
            trading_symbol = _field(row, "SEM_TRADING_SYMBOL", "SM_SYMBOL_NAME", "SYMBOL_NAME").upper()
            instrument = _field(row, "SEM_INSTRUMENT_NAME", "INSTRUMENT").upper()
            security_id = _field(row, "SEM_SMST_SECURITY_ID", "SECURITY_ID", "securityId")

            if exchange != "NSE":
                continue
            if segment not in ("E", "NSE_EQ", "EQUITY"):
                continue
            if trading_symbol != symbol.upper():
                continue
            if instrument and instrument not in ("EQUITY", "EQ"):
                continue
            if not security_id:
                continue
            matches.append({
                "symbol": symbol,
                "security_id": security_id,
                "exchange_segment": "NSE_EQ",
                "instrument": "EQUITY"
            })

        if len(matches) == 1:
            result[symbol] = matches[0]
        elif not matches:
            failures[symbol] = "SECURITY_ID_NOT_FOUND"
        else:
            failures[symbol] = f"AMBIGUOUS_SECURITY_ID:{len(matches)}"

    return {
        "schema_version": "1.0",
        "source": "DHAN_INSTRUMENT_MASTER",
        "source_url": INSTRUMENT_MASTER_URL,
        "exchange_segment": "NSE_EQ",
        "instrument": "EQUITY",
        "required_symbols": list(symbols),
        "resolved": result,
        "failures": failures,
        "complete": len(result) == 29 and not failures,
    }


def build_mapping(output: Path = OUTPUT_PATH) -> dict:
    symbols = load_universe()
    payload = resolve(symbols, fetch_master())
    if not payload["complete"]:
        raise RuntimeError(json.dumps(payload, indent=2))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


if __name__ == "__main__":
    build_mapping()
    print("PSY29 Stage 4: 29/29 Dhan security IDs resolved")
