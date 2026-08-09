"""PSYCHO Batch 2A — Continuous Futures Participation (daily)

Dhan does not expose expired index-futures intraday contracts. Batch 2A therefore
uses Dhan's continuous/current near-month futures representation at DAILY timeframe.
The dataset is intended for regime/participation context, not intraday backtests.

Important validation policy:
- Preserve the raw Dhan response unchanged.
- Normalize Dhan's `open_interest` field to canonical `oi` when present.
- Do not discard an entire acquisition because of an isolated malformed OHLC row.
  Such rows are quarantined and reported explicitly.
- The validated dataset contains only structurally valid rows.
"""
import os, io, json
from pathlib import Path
from datetime import datetime
import requests
import pandas as pd

ROOT = Path("research_data/batch2a_continuous_futures")
ROOT.mkdir(parents=True, exist_ok=True)
BASE = "https://api.dhan.co/v2"
FROM = os.getenv("BATCH2A_FROM", "2025-08-11")
TO = os.getenv("BATCH2A_TO", "2026-08-09")
CLIENT_ID = os.environ["DHAN_CLIENT_ID"]
TOKEN = os.environ["DHAN_ACCESS_TOKEN"]
HEADERS = {"access-token": TOKEN, "client-id": CLIENT_ID, "Content-Type": "application/json"}


def post(path, payload):
    r = requests.post(BASE + path, headers=HEADERS, json=payload, timeout=60)
    r.raise_for_status()
    data = r.json()
    if isinstance(data, dict) and str(data.get("status", "")).lower() == "failure":
        raise RuntimeError(data.get("remarks") or data.get("errorMessage") or str(data))
    return data


def normalize_response(data):
    """Normalize known Dhan field aliases without changing the raw response."""
    if not isinstance(data, dict):
        raise RuntimeError("Dhan historical response is not a JSON object")
    out = dict(data)
    if "oi" not in out and "open_interest" in out:
        out["oi"] = out["open_interest"]
    return out


def find_bad_ohlc_rows(data):
    bad = []
    ts = data.get("timestamp", [])
    for i in range(len(ts)):
        try:
            o, h, l, c = map(float, (data["open"][i], data["high"][i], data["low"][i], data["close"][i]))
            if h < max(o, c) or l > min(o, c) or h < l:
                bad.append({"index": i, "timestamp": ts[i], "open": o, "high": h, "low": l, "close": c, "reason": "OHLC relationship invalid"})
        except Exception as exc:
            bad.append({"index": i, "timestamp": ts[i] if i < len(ts) else None, "reason": f"non-numeric OHLC: {exc}"})
    return bad


def validate(data):
    required = {"timestamp", "open", "high", "low", "close", "volume"}
    missing = required - set(data)
    if missing:
        return {"ok": False, "reason": f"missing fields: {sorted(missing)}"}

    n = len(data["timestamp"])
    required_lengths = {k: len(data[k]) for k in required}
    if len(set(required_lengths.values())) != 1:
        return {"ok": False, "reason": "required array length mismatch", "lengths": required_lengths}
    if n == 0:
        return {"ok": False, "reason": "zero rows"}

    optional_lengths = {}
    for k in ("oi", "open_interest"):
        if k in data:
            optional_lengths[k] = len(data[k])
            if len(data[k]) != n:
                return {"ok": False, "reason": f"optional field length mismatch: {k}", "lengths": {**required_lengths, **optional_lengths}}

    ts = [int(float(x)) for x in data["timestamp"]]
    dup = n - len(set(ts))
    ordered = all(ts[i] < ts[i + 1] for i in range(n - 1))
    bad_rows = find_bad_ohlc_rows(data)

    return {
        "ok": dup == 0 and ordered and n - len(bad_rows) > 0,
        "rows": n,
        "valid_rows": n - len(bad_rows),
        "quarantined_bad_ohlc_rows": len(bad_rows),
        "duplicates": dup,
        "ordered": ordered,
        "first_epoch": ts[0],
        "last_epoch": ts[-1],
        "fields": sorted(data.keys()),
        "oi_present": "oi" in data or "open_interest" in data,
        "bad_ohlc_rows": bad_rows,
    }


def clean_valid_rows(data):
    """Return a structurally valid dataset while retaining raw data separately."""
    bad_indices = {x["index"] for x in find_bad_ohlc_rows(data)}
    n = len(data["timestamp"])
    cleaned = {}
    for key, values in data.items():
        if isinstance(values, list) and len(values) == n:
            cleaned[key] = [v for i, v in enumerate(values) if i not in bad_indices]
        else:
            cleaned[key] = values
    if "oi" not in cleaned and "open_interest" in cleaned:
        cleaned["oi"] = cleaned["open_interest"]
    return cleaned


def resolve_current_near(df, symbol):
    x = df[(df["SEM_EXM_EXCH_ID"].astype(str).str.upper() == "NSE") &
           (df["SEM_SEGMENT"].astype(str).str.upper() == "D") &
           (df["SEM_INSTRUMENT_NAME"].astype(str).str.upper() == "FUTIDX")].copy()
    x["sym"] = x["SM_SYMBOL_NAME"].astype(str).str.upper().str.strip()
    x["trade"] = x["SEM_TRADING_SYMBOL"].astype(str).str.upper().str.strip()
    x["expiry"] = pd.to_datetime(x["SEM_EXPIRY_DATE"], errors="coerce")
    x = x[x["sym"].eq(symbol) | x["trade"].str.startswith(symbol + "-")]
    today = pd.Timestamp(datetime.now().date())
    x = x[x["expiry"] >= today].sort_values("expiry")
    if x.empty:
        raise RuntimeError(f"No active/forward {symbol} FUTIDX contract found in current Dhan master")
    return x.iloc[0]


def main():
    master_url = "https://images.dhan.co/api-data/api-scrip-master.csv"
    r = requests.get(master_url, timeout=60)
    r.raise_for_status()
    (ROOT / "dhan_scrip_master.csv").write_text(r.text)
    df = pd.read_csv(io.StringIO(r.text), low_memory=False)
    needed = ["SEM_EXM_EXCH_ID", "SEM_SEGMENT", "SEM_SMST_SECURITY_ID", "SEM_INSTRUMENT_NAME", "SEM_EXPIRY_DATE", "SEM_TRADING_SYMBOL", "SM_SYMBOL_NAME"]
    missing = [c for c in needed if c not in df.columns]
    if missing:
        raise RuntimeError(f"Required master columns missing: {missing}")

    manifest = {"status": "STARTED", "from": FROM, "to": TO, "datasets": {}}
    for symbol in ["NIFTY", "BANKNIFTY"]:
        row = resolve_current_near(df, symbol)
        sid = str(row["SEM_SMST_SECURITY_ID"])
        expiry = str(pd.Timestamp(row["expiry"]).date())
        payload = {"securityId": sid, "exchangeSegment": "NSE_FNO", "instrument": "FUTIDX", "expiryCode": 0, "oi": True, "fromDate": FROM, "toDate": TO}
        response = post("/charts/historical", payload)
        raw = response.get("data", response) if isinstance(response, dict) else response
        raw_path = ROOT / f"{symbol}_continuous_daily_raw.json"
        raw_path.write_text(json.dumps(raw, indent=2))

        normalized = normalize_response(raw)
        validation = validate(normalized)
        if not validation.get("ok"):
            raise RuntimeError(f"{symbol} continuous daily structural validation failed: {validation}")

        cleaned = clean_valid_rows(normalized)
        cleaned_validation = validate(cleaned)
        if not cleaned_validation.get("ok"):
            raise RuntimeError(f"{symbol} cleaned continuous daily validation failed: {cleaned_validation}")

        out = ROOT / f"{symbol}_continuous_daily.json"
        out.write_text(json.dumps({
            "symbol": symbol,
            "security_id": sid,
            "near_contract_expiry_used": expiry,
            "payload": payload,
            "raw_validation": validation,
            "data": cleaned,
        }, indent=2))

        manifest["datasets"][symbol] = {
            "security_id": sid,
            "near_contract_expiry_used": expiry,
            "raw_validation": validation,
            "cleaned_validation": cleaned_validation,
            "raw_file": raw_path.name,
            "file": out.name,
        }

    manifest["status"] = "VALIDATED"
    (ROOT / "batch2a_manifest.json").write_text(json.dumps(manifest, indent=2))
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
