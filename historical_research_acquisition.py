"""PSYCHO Historical Research Acquisition — BATCH 1

NIFTY + BANK NIFTY underlying structure only.
Downloads 1M, 15M and 60M candles for the research window.
The existing 5M dataset remains the base layer and is not overwritten.

Dhan v2 historical intraday API permits intervals 1/5/15/25/60 and up to
90 days per request. This module chunks the requested period into <=90-day
windows, converts Dhan's columnar response into timestamped rows, validates
ordering/duplicates/required fields, and writes CSV + validation metadata.

Credentials are NEVER stored in source. Set DHAN_ACCESS_TOKEN and DHAN_CLIENT_ID.
"""
import csv, json, os, time, urllib.request
from datetime import datetime, timedelta
from pathlib import Path

API = "https://api.dhan.co/v2/charts/intraday"
ROOT = Path(os.getenv("PSYCHO_RESEARCH_ROOT", "research_data")) / "batch1_underlying"
ROOT.mkdir(parents=True, exist_ok=True)

INSTRUMENTS = {
    "NIFTY": {"security_id": "13", "exchange_segment": "IDX_I", "instrument": "INDEX"},
    "BANKNIFTY": {"security_id": "25", "exchange_segment": "IDX_I", "instrument": "INDEX"},
}
INTERVALS = {"1M": "1", "15M": "15", "1H": "60"}
START = os.getenv("BATCH1_FROM", "2025-08-11 09:15:00")
END = os.getenv("BATCH1_TO", "2026-08-08 15:40:00")
REQUIRED = ("timestamp", "open", "high", "low", "close", "volume")


def request(payload):
    token = os.environ.get("DHAN_ACCESS_TOKEN")
    client = os.environ.get("DHAN_CLIENT_ID")
    if not token or not client:
        raise RuntimeError("Missing DHAN_ACCESS_TOKEN or DHAN_CLIENT_ID runtime secret")
    req = urllib.request.Request(API, data=json.dumps(payload).encode(), method="POST", headers={
        "Accept": "application/json", "Content-Type": "application/json",
        "access-token": token, "client-id": client,
    })
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def parse_timestamp(v):
    if isinstance(v, (int, float)):
        return datetime.fromtimestamp(v).isoformat()
    s = str(v)
    if s.isdigit():
        return datetime.fromtimestamp(int(s)).isoformat()
    return s


def response_rows(data):
    ts = data.get("timestamp") or []
    keys = ["open", "high", "low", "close", "volume"]
    n = len(ts)
    if any(len(data.get(k) or []) != n for k in keys):
        raise ValueError("Column length mismatch in Dhan response")
    return [{"timestamp": parse_timestamp(ts[i]), **{k: data[k][i] for k in keys}} for i in range(n)]


def chunks(start, end, days=89):
    cur = start
    while cur < end:
        nxt = min(cur + timedelta(days=days), end)
        yield cur, nxt
        cur = nxt


def validate(rows, name):
    errors, seen, prev = [], set(), None
    for i, r in enumerate(rows):
        if any(r.get(k) is None for k in REQUIRED): errors.append(f"row {i}: missing required field")
        ts = r.get("timestamp")
        if ts in seen: errors.append(f"row {i}: duplicate timestamp {ts}")
        seen.add(ts)
        if prev is not None and ts <= prev: errors.append(f"row {i}: timestamps not strictly increasing")
        prev = ts
        try:
            o,h,l,c = map(float, (r["open"], r["high"], r["low"], r["close"]))
            if h < max(o,c) or l > min(o,c) or h < l: errors.append(f"row {i}: invalid OHLC relationship")
        except Exception: errors.append(f"row {i}: non-numeric OHLC")
    return {"dataset": name, "rows": len(rows), "unique_timestamps": len(seen), "valid": not errors,
            "errors": errors[:50], "first_timestamp": rows[0]["timestamp"] if rows else None,
            "last_timestamp": rows[-1]["timestamp"] if rows else None, "required_fields": list(REQUIRED)}


def write_dataset(rows, name):
    csv_path = ROOT / f"{name}.csv"
    meta = validate(rows, name)
    with csv_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(REQUIRED)); w.writeheader(); w.writerows(rows)
    (ROOT / f"{name}.validation.json").write_text(json.dumps(meta, indent=2))
    return meta


def fetch_dataset(symbol, interval_name, interval):
    cfg = INSTRUMENTS[symbol]; rows = []
    for a, b in chunks(datetime.fromisoformat(START), datetime.fromisoformat(END)):
        payload = {"securityId": cfg["security_id"], "exchangeSegment": cfg["exchange_segment"],
                   "instrument": cfg["instrument"], "interval": interval, "oi": False,
                   "fromDate": a.strftime("%Y-%m-%d %H:%M:%S"), "toDate": b.strftime("%Y-%m-%d %H:%M:%S")}
        rows.extend(response_rows(request(payload)))
        time.sleep(0.25)
    rows = list({r["timestamp"]: r for r in rows}.values()); rows.sort(key=lambda r: r["timestamp"])
    return write_dataset(rows, f"{symbol.lower()}_{interval_name}")


def run_batch1():
    manifest = {"batch": "BATCH_1_UNDERLYING_STRUCTURE", "from": START, "to": END, "status": "RUNNING", "datasets": []}
    (ROOT / "batch1_manifest.json").write_text(json.dumps(manifest, indent=2))
    for symbol in INSTRUMENTS:
        for name, interval in INTERVALS.items():
            print(f"FETCH {symbol} {name}", flush=True)
            meta = fetch_dataset(symbol, name, interval); manifest["datasets"].append(meta)
            if not meta["valid"]:
                manifest["status"] = "VALIDATION_FAILED"
                (ROOT / "batch1_manifest.json").write_text(json.dumps(manifest, indent=2))
                raise RuntimeError(f"Validation failed: {symbol} {name}")
    manifest["status"] = "VALIDATED"
    (ROOT / "batch1_manifest.json").write_text(json.dumps(manifest, indent=2))
    print(json.dumps(manifest, indent=2), flush=True)


if __name__ == "__main__":
    run_batch1()
