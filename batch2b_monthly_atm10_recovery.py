"""PSYCHO Batch 2B-X — BANKNIFTY Monthly ATM±10 Recovery

Research-only acquisition. This file is intentionally separate from the existing
Batch 2B acquisition and requests only BANKNIFTY MONTHLY expired options using
Dhan /charts/rollingoption.
"""
import os, json, gzip, time
from pathlib import Path
from datetime import date, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

ROOT = Path("research_data/batch2b_monthly_atm10_recovery")
ROOT.mkdir(parents=True, exist_ok=True)
BASE = "https://api.dhan.co/v2/charts/rollingoption"
FROM = date.fromisoformat(os.getenv("BATCH2BX_FROM", "2025-08-11"))
TO = date.fromisoformat(os.getenv("BATCH2BX_TO", "2026-08-09"))
CLIENT_ID = os.environ["DHAN_CLIENT_ID"]
TOKEN = os.environ["DHAN_ACCESS_TOKEN"]
HEADERS = {"access-token": TOKEN, "client-id": CLIENT_ID, "Content-Type": "application/json", "Accept": "application/json"}

SYMBOL = "BANKNIFTY"
SECURITY_ID = "25"
EXPIRY_FLAG = "MONTH"
OFFSETS = list(range(-10, 11))
OPTION_TYPES = ("CALL", "PUT")
EXPIRY_CODES = (1, 2, 3)


def strike_name(offset):
    if offset == 0:
        return "ATM"
    return f"ATM+{offset}" if offset > 0 else f"ATM{offset}"


def chunks(start, end, days=30):
    cur = start
    while cur < end:
        nxt = min(cur + timedelta(days=days), end)
        yield cur, nxt
        cur = nxt


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
    ordered = all(ts[i] < ts[i + 1] for i in range(n - 1))
    bad_ohlc = 0
    bad_volume = 0
    for i in range(n):
        try:
            o, h, l, c = map(float, (side["open"][i], side["high"][i], side["low"][i], side["close"][i]))
            if h < max(o, c) or l > min(o, c) or h < l:
                bad_ohlc += 1
            if float(side["volume"][i]) < 0:
                bad_volume += 1
        except Exception:
            bad_ohlc += 1
    return {
        "present": True,
        "ok": duplicates == 0 and ordered and bad_ohlc == 0 and bad_volume == 0,
        "rows": n,
        "duplicates": duplicates,
        "ordered": ordered,
        "bad_ohlc": bad_ohlc,
        "bad_volume": bad_volume,
        "fields": sorted(side.keys()),
        "first_epoch": ts[0] if n else None,
        "last_epoch": ts[-1] if n else None,
    }


def validate_response(obj):
    data = obj.get("data", obj) if isinstance(obj, dict) else obj
    ce = data.get("ce") if isinstance(data, dict) else None
    pe = data.get("pe") if isinstance(data, dict) else None
    vce = validate_side(ce)
    vpe = validate_side(pe)
    return {"ok": vce.get("ok", False) and vpe.get("ok", False), "rows": vce.get("rows", 0) + vpe.get("rows", 0), "ce": vce, "pe": vpe}, data


def task(spec):
    expiry_code, offset, option_type, start, end = spec
    strike = strike_name(offset)
    payload = {
        "exchangeSegment": "NSE_FNO",
        "interval": "1",
        "securityId": SECURITY_ID,
        "instrument": "OPTIDX",
        "expiryFlag": EXPIRY_FLAG,
        "expiryCode": expiry_code,
        "strike": strike,
        "drvOptionType": option_type,
        "requiredData": ["open", "high", "low", "close", "iv", "volume", "strike", "oi", "spot"],
        "fromDate": start.isoformat(),
        "toDate": end.isoformat(),
    }
    obj = request_once(payload)
    validation, data = validate_response(obj)
    filename = f"{SYMBOL}_{EXPIRY_FLAG}_{expiry_code}_{strike}_{option_type}_{start}_{end}.json.gz"
    path = ROOT / filename
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump({"request": payload, "validation": validation, "data": data}, f, separators=(",", ":"))
    if not validation["ok"]:
        raise RuntimeError(f"validation failed: {filename}: {validation}")
    return filename, {"status": "VALIDATED", "validation": validation}


def main():
    specs = [(expiry_code, offset, option_type, start, end)
             for expiry_code in EXPIRY_CODES
             for offset in OFFSETS
             for option_type in OPTION_TYPES
             for start, end in chunks(FROM, TO, 30)]
    manifest = {
        "version": 1,
        "status": "RUNNING",
        "symbol": SYMBOL,
        "security_id": SECURITY_ID,
        "expiry_flag": EXPIRY_FLAG,
        "expiry_codes": list(EXPIRY_CODES),
        "offsets": OFFSETS,
        "option_types": list(OPTION_TYPES),
        "from": FROM.isoformat(),
        "to": TO.isoformat(),
        "planned_requests": len(specs),
        "results": {},
        "failures": [],
    }
    failures = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(task, s): s for s in specs}
        for fut in as_completed(futures):
            spec = futures[fut]
            try:
                filename, result = fut.result()
                manifest["results"][filename] = result
            except Exception as exc:
                failures.append({"spec": list(spec), "error": str(exc)})
    manifest["failures"] = failures
    manifest["validated_requests"] = len(manifest["results"])
    manifest["status"] = "VALIDATED" if not failures else "FAILED"
    (ROOT / "batch2bx_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True))
    if failures:
        raise RuntimeError(f"Batch 2B-X failed requests: {len(failures)}; see batch2bx_manifest.json")
    print(json.dumps({"status": manifest["status"], "validated_requests": manifest["validated_requests"]}, indent=2))


if __name__ == "__main__":
    main()
