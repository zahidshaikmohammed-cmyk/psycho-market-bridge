"""PSYCHO Batch 2A — Continuous Futures Participation (daily)

Dhan does not expose expired index-futures intraday contracts. Batch 2A therefore
uses Dhan's continuous near-month futures representation at DAILY timeframe.
The dataset is intended for regime/participation context, not intraday backtests.
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


def validate(data):
    # OHLCV is mandatory for Batch 2A. OI is retained when Dhan supplies it,
    # but is optional because continuous daily responses may omit it.
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

    # Optional fields must align if present.
    optional_lengths = {}
    for k in ("oi",):
        if k in data:
            optional_lengths[k] = len(data[k])
            if len(data[k]) != n:
                return {"ok": False, "reason": f"optional field length mismatch: {k}", "lengths": {**required_lengths, **optional_lengths}}

    ts = [int(float(x)) for x in data["timestamp"]]
    dup = n - len(set(ts))
    ordered = all(ts[i] < ts[i + 1] for i in range(n - 1))

    bad = 0
    for i in range(n):
        try:
            o, h, l, c = map(float, (data["open"][i], data["high"][i], data["low"][i], data["close"][i]))
            if h < max(o, c) or l > min(o, c) or h < l:
                bad += 1
        except Exception:
            bad += 1

    return {
        "ok": dup == 0 and ordered and bad == 0,
        "rows": n,
        "duplicates": dup,
        "ordered": ordered,
        "bad_ohlc": bad,
        "first_epoch": ts[0],
        "last_epoch": ts[-1],
        "fields": sorted(data.keys()),
        "oi_present": "oi" in data,
    }


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
        data = post("/charts/historical", payload)
        raw = data.get("data", data) if isinstance(data, dict) else data
        validation = validate(raw)
        out = ROOT / f"{symbol}_continuous_daily.json"
        out.write_text(json.dumps({"symbol": symbol, "security_id": sid, "near_contract_expiry_used": expiry, "payload": payload, "validation": validation, "data": raw}, indent=2))
        manifest["datasets"][symbol] = {"security_id": sid, "near_contract_expiry_used": expiry, "validation": validation, "file": out.name}
        if not validation.get("ok"):
            raise RuntimeError(f"{symbol} continuous daily validation failed: {validation}")

    manifest["status"] = "VALIDATED"
    (ROOT / "batch2a_manifest.json").write_text(json.dumps(manifest, indent=2))
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
