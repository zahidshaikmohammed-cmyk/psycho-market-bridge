"""PSYCHO Batch 2B — Expired Options Microstructure Acquisition

Research-only acquisition. Uses Dhan /charts/rollingoption, which provides
expired index-option minute data on a rolling basis without requiring expired
option security IDs. The endpoint supports up to 30 days per request and up to
5 years of history, with OHLC, IV, volume, OI, strike and spot.

Scope is deliberately research-focused and bounded:
- NIFTY (securityId 13), BANKNIFTY (securityId 25)
- 1-minute data
- Weekly near expiry: ATM-10..ATM+10
- Monthly near/next/far: ATM-3..ATM+3
- CE and PE
- requiredData: OHLC, IV, volume, strike, OI, spot

The acquisition is resumable: completed request keys are recorded in the
manifest and existing files are not fetched again.
"""
import os, json, gzip, time, hashlib
from pathlib import Path
from datetime import date, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

ROOT = Path("research_data/batch2b_expired_options")
ROOT.mkdir(parents=True, exist_ok=True)
BASE = "https://api.dhan.co/v2/charts/rollingoption"
FROM = date.fromisoformat(os.getenv("BATCH2B_FROM", "2025-08-11"))
TO = date.fromisoformat(os.getenv("BATCH2B_TO", "2026-08-09"))
CLIENT_ID = os.environ["DHAN_CLIENT_ID"]
TOKEN = os.environ["DHAN_ACCESS_TOKEN"]
HEADERS = {"access-token": TOKEN, "client-id": CLIENT_ID, "Content-Type": "application/json", "Accept": "application/json"}

# Dhan's rolling expired-options API supports ATM +/-10 for index options near expiry.
CONFIGS = []
for symbol, sid in (("NIFTY", "13"), ("BANKNIFTY", "25")):
    CONFIGS.append((symbol, sid, "WEEK", 1, list(range(-10, 11))))
    for expiry_code in (1, 2, 3):
        CONFIGS.append((symbol, sid, "MONTH", expiry_code, list(range(-3, 4))))


def strike_name(offset):
    if offset == 0:
        return "ATM"
    return f"ATM+{offset}" if offset > 0 else f"ATM{offset}"


def request_once(payload):
    for attempt in range(4):
        try:
            r = requests.post(BASE, headers=HEADERS, json=payload, timeout=90)
            if r.status_code == 429:
                time.sleep(2.0 * (attempt + 1))
                continue
            r.raise_for_status()
            obj = r.json()
            if isinstance(obj, dict) and str(obj.get("status", "")).lower() == "failure":
                raise RuntimeError(obj.get("remarks") or obj.get("errorMessage") or str(obj))
            return obj
        except Exception:
            if attempt == 3:
                raise
            time.sleep(1.5 * (attempt + 1))


def validate_side(side):
    if not side:
        return {"present": False, "ok": True, "rows": 0}
    required = {"timestamp", "open", "high", "low", "close", "volume"}
    missing = sorted(required - set(side))
    if missing:
        return {"present": True, "ok": False, "reason": f"missing fields: {missing}"}
    n = len(side["timestamp"])
    lengths = {k: len(v) for k, v in side.items() if isinstance(v, list)}
    bad_lengths = {k: v for k, v in lengths.items() if v != n}
    if bad_lengths:
        return {"present": True, "ok": False, "reason": "array length mismatch", "lengths": lengths}
    ts = [int(float(x)) for x in side["timestamp"]]
    duplicates = n - len(set(ts))
    ordered = all(ts[i] < ts[i+1] for i in range(n-1))
    bad_ohlc = 0
    for i in range(n):
        try:
            o,h,l,c = map(float, (side["open"][i], side["high"][i], side["low"][i], side["close"][i]))
            if h < max(o,c) or l > min(o,c) or h < l:
                bad_ohlc += 1
        except Exception:
            bad_ohlc += 1
    return {"present": True, "ok": duplicates == 0 and ordered and bad_ohlc == 0,
            "rows": n, "duplicates": duplicates, "ordered": ordered,
            "bad_ohlc": bad_ohlc, "fields": sorted(side.keys()),
            "first_epoch": ts[0] if n else None, "last_epoch": ts[-1] if n else None}


def validate_response(obj):
    data = obj.get("data", obj) if isinstance(obj, dict) else obj
    ce = data.get("ce") if isinstance(data, dict) else None
    pe = data.get("pe") if isinstance(data, dict) else None
    vce = validate_side(ce)
    vpe = validate_side(pe)
    present_rows = vce.get("rows", 0) + vpe.get("rows", 0)
    # Empty responses can legitimately occur for a relative strike/expiry bucket.
    # They are recorded, not treated as corruption. Any non-empty side must validate.
    ok = vce.get("ok", False) and vpe.get("ok", False)
    return {"ok": ok, "rows": present_rows, "ce": vce, "pe": vpe}


def chunks(start, end, days=30):
    cur = start
    while cur < end:
        nxt = min(cur + timedelta(days=days), end)
        yield cur, nxt
        cur = nxt


def key_for(symbol, sid, flag, expiry_code, strike, option_type, start, end):
    return f"{symbol}|{sid}|{flag}|{expiry_code}|{strike}|{option_type}|{start}|{end}"


def out_path(symbol, flag, expiry_code, strike, option_type, start, end):
    safe = f"{symbol}_{flag}_{expiry_code}_{strike}_{option_type}_{start}_{end}.json.gz"
    return ROOT / safe


def load_manifest():
    p = ROOT / "batch2b_manifest.json"
    if p.exists():
        return json.loads(p.read_text())
    return {"version": 1, "status": "STARTED", "requests": {}}


def save_manifest(m):
    (ROOT / "batch2b_manifest.json").write_text(json.dumps(m, indent=2, sort_keys=True))


def task(spec):
    symbol, sid, flag, expiry_code, offset, option_type, start, end = spec
    strike = strike_name(offset)
    key = key_for(symbol, sid, flag, expiry_code, strike, option_type, start, end)
    path = out_path(symbol, flag, expiry_code, strike, option_type, start, end)
    if path.exists():
        return key, {"status": "EXISTS", "file": path.name}
    payload = {
        "exchangeSegment": "NSE_FNO", "interval": "1", "securityId": sid,
        "instrument": "OPTIDX", "expiryFlag": flag, "expiryCode": expiry_code,
        "strike": strike, "drvOptionType": option_type,
        "requiredData": ["open", "high", "low", "close", "iv", "volume", "strike", "oi", "spot"],
        "fromDate": start.isoformat(), "toDate": end.isoformat()
    }
    obj = request_once(payload)
    validation = validate_response(obj)
    record = {"request": payload, "validation": validation, "data": obj.get("data", obj)}
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(record, f, separators=(",", ":"))
    if not validation["ok"]:
        # Preserve the raw response for diagnosis but mark this request invalid.
        raise RuntimeError(f"validation failed for {key}: {validation}")
    return key, {"status": "VALIDATED", "file": path.name, "validation": validation}


def main():
    manifest = load_manifest()
    specs = []
    for symbol, sid, flag, expiry_code, offsets in CONFIGS:
        for offset in offsets:
            for option_type in ("CALL", "PUT"):
                for start, end in chunks(FROM, TO, 30):
                    key = key_for(symbol, sid, flag, expiry_code, strike_name(offset), option_type, start, end)
                    if manifest["requests"].get(key, {}).get("status") == "VALIDATED":
                        continue
                    specs.append((symbol, sid, flag, expiry_code, offset, option_type, start, end))

    manifest["status"] = "RUNNING"
    manifest["planned_requests"] = len(specs)
    save_manifest(manifest)

    failures = []
    # Conservative concurrency to avoid hammering the data endpoint.
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(task, s): s for s in specs}
        for fut in as_completed(futures):
            spec = futures[fut]
            try:
                key, result = fut.result()
                manifest["requests"][key] = result
            except Exception as exc:
                symbol, sid, flag, expiry_code, offset, option_type, start, end = spec
                key = key_for(symbol, sid, flag, expiry_code, strike_name(offset), option_type, start, end)
                manifest["requests"][key] = {"status": "FAILED", "error": str(exc)}
                failures.append(key)
            if len(manifest["requests"]) % 25 == 0:
                save_manifest(manifest)

    manifest["failed_requests"] = failures
    manifest["validated_requests"] = sum(1 for x in manifest["requests"].values() if x.get("status") == "VALIDATED")
    manifest["status"] = "VALIDATED" if not failures else "FAILED"
    save_manifest(manifest)
    if failures:
        raise RuntimeError(f"Batch 2B failed requests: {len(failures)}; see batch2b_manifest.json")
    print(json.dumps({"status": manifest["status"], "validated_requests": manifest["validated_requests"]}, indent=2))


if __name__ == "__main__":
    main()
