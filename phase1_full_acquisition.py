"""PSYCHO SIGNAL FACTORY — Phase 1 full historical acquisition.

Acquires the historical evidence needed by the downstream signal factory:
- NIFTY and BANKNIFTY index candles: 1m/5m/15m/60m, up to Dhan's 5-year limit.
- NIFTY/BANKNIFTY index futures: 5m candles with OI for contracts present in
  Dhan's scrip master and inside the research window.
- Expired index options: 5m rolling data, including OHLC, IV, volume, OI,
  strike and spot, for ATM +/- 10, for weekly and monthly near-expiry series.

The program is deliberately acquisition-only. Phase 1's memory builder then
indexes the resulting CSV.GZ files. Secrets are read only from environment.
"""
from __future__ import annotations

import csv
import gzip
import json
import os
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.request import Request, urlopen

DHAN_BASE = "https://api.dhan.co/v2"
MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master-detailed.csv"
TOKEN = os.environ["DHAN_ACCESS_TOKEN"]
IST_OFFSET = "+05:30"

START = datetime.strptime(os.environ.get("HISTORICAL_START_DATE", (date.today() - timedelta(days=365*5)).isoformat()), "%Y-%m-%d").date()
END = datetime.strptime(os.environ.get("HISTORICAL_END_DATE", date.today().isoformat()), "%Y-%m-%d").date()
ROOT = Path(os.environ.get("HISTORICAL_OUTPUT_DIR", "research_data"))
DELAY = float(os.environ.get("HISTORICAL_REQUEST_DELAY", "0.25"))
MAX_CHUNK_DAYS = 90

UNDERLYINGS = {
    "NIFTY": {"security_id": "13", "symbol": "NIFTY"},
    "BANKNIFTY": {"security_id": "25", "symbol": "BANKNIFTY"},
}
INDEX_INTERVALS = ("1", "5", "15", "60")
FUTURE_INTERVAL = "5"
OPTION_INTERVAL = "5"
OPTION_STRIKES = tuple(["ATM"] + [f"ATM+{i}" for i in range(1, 11)] + [f"ATM-{i}" for i in range(1, 11)])


def post(path: str, payload: dict) -> dict:
    body = json.dumps(payload).encode("utf-8")
    req = Request(
        DHAN_BASE + path,
        data=body,
        headers={"Accept": "application/json", "Content-Type": "application/json", "access-token": TOKEN},
        method="POST",
    )
    last = None
    for attempt in range(5):
        try:
            with urlopen(req, timeout=90) as response:
                raw = response.read().decode("utf-8")
                if not raw:
                    raise RuntimeError("empty Dhan response")
                data = json.loads(raw)
                if isinstance(data, dict) and data.get("status") == "failure":
                    raise RuntimeError(str(data))
                return data
        except Exception as exc:
            last = exc
            time.sleep(min(2 ** attempt, 16))
    raise RuntimeError(f"Dhan request failed {path}: {last}")


def rows_from_ohlcv(raw: dict, extra: dict | None = None):
    ts = raw.get("timestamp") or raw.get("start_Time") or []
    arrays = {k: raw.get(k, []) for k in ("open", "high", "low", "close", "volume", "open_interest")}
    n = len(ts)
    for i in range(n):
        row = {"timestamp": int(ts[i])}
        for key, arr in arrays.items():
            if i < len(arr):
                row[key] = arr[i]
        if extra:
            row.update(extra)
        yield row


def write_rows(path: Path, rows, fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def download_index(name: str, start: date, end: date):
    sid = UNDERLYINGS[name]["security_id"]
    for interval in INDEX_INTERVALS:
        out = []
        cursor = start
        while cursor < end:
            chunk_end = min(cursor + timedelta(days=MAX_CHUNK_DAYS), end)
            print(f"INDEX {name} {interval}M {cursor}->{chunk_end}", flush=True)
            raw = post("/charts/intraday", {
                "securityId": sid, "exchangeSegment": "IDX_I", "instrument": "INDEX",
                "interval": interval, "oi": False,
                "fromDate": f"{cursor} 09:15:00", "toDate": f"{chunk_end} 15:40:00",
            })
            out.extend(rows_from_ohlcv(raw, {"instrument": name, "timeframe": f"{interval}M", "dataset_type": "INDEX"}))
            cursor = chunk_end
            time.sleep(DELAY)
        write_rows(ROOT / "indices" / f"{name.lower()}_{interval}m_{start}_{end}.csv.gz", out,
                   ["timestamp", "open", "high", "low", "close", "volume", "open_interest", "instrument", "timeframe", "dataset_type"])


def fetch_master():
    req = Request(MASTER_URL, headers={"User-Agent": "PSYCHO-Signal-Factory/1.0"})
    with urlopen(req, timeout=120) as response:
        return response.read().decode("utf-8", errors="replace")


def future_contracts(start: date, end: date):
    text = fetch_master()
    reader = csv.DictReader(text.splitlines())
    contracts = []
    for r in reader:
        seg = (r.get("SEGMENT") or r.get("SEM_SEGMENT") or "").upper()
        inst = (r.get("INSTRUMENT") or r.get("SEM_INSTRUMENT_NAME") or "").upper()
        sym = (r.get("UNDERLYING_SYMBOL") or "").upper()
        if seg not in {"D", "NSE_FNO"} or inst not in {"FUTIDX", "FUTURE INDEX", "FUTIDX"}:
            continue
        if sym not in UNDERLYINGS:
            continue
        expiry_raw = r.get("SM_EXPIRY_DATE") or r.get("SEM_EXPIRY_DATE") or ""
        try:
            expiry = datetime.strptime(expiry_raw[:10], "%Y-%m-%d").date()
        except ValueError:
            continue
        if not (start <= expiry <= end):
            continue
        sid = r.get("SECURITY_ID") or r.get("SEM_SMST_SECURITY_ID") or r.get("security_id")
        if not sid:
            continue
        contracts.append((sym, str(sid), expiry, r.get("DISPLAY_NAME") or r.get("SEM_CUSTOM_SYMBOL") or ""))
    # unique by security id
    return list({sid: (sym, sid, expiry, label) for sym, sid, expiry, label in contracts}.values())


def download_futures(start: date, end: date):
    contracts = future_contracts(start, end)
    print(f"FUTURES contracts discovered: {len(contracts)}", flush=True)
    fields = ["timestamp", "open", "high", "low", "close", "volume", "open_interest", "instrument", "timeframe", "security_id", "expiry", "trading_symbol", "dataset_type"]
    for sym, sid, expiry, label in contracts:
        contract_start = max(start, expiry - timedelta(days=120))
        contract_end = min(end, expiry + timedelta(days=1))
        raw = post("/charts/intraday", {
            "securityId": sid, "exchangeSegment": "NSE_FNO", "instrument": "FUTIDX",
            "interval": FUTURE_INTERVAL, "oi": True,
            "fromDate": f"{contract_start} 09:15:00", "toDate": f"{contract_end} 15:40:00",
        })
        rows = rows_from_ohlcv(raw, {"instrument": sym, "timeframe": "5M", "security_id": sid,
                                     "expiry": expiry.isoformat(), "trading_symbol": label, "dataset_type": "FUTURE"})
        write_rows(ROOT / "futures" / sym.lower() / f"{sid}_{expiry}_5m.csv.gz", rows, fields)
        time.sleep(DELAY)


def download_rolling_options(start: date, end: date):
    fields = ["timestamp", "open", "high", "low", "close", "volume", "oi", "iv", "strike", "spot",
              "instrument", "timeframe", "expiry_flag", "expiry_code", "strike_relation", "option_type", "dataset_type"]
    cursor = start
    while cursor < end:
        chunk_end = min(cursor + timedelta(days=30), end)
        for name, meta in UNDERLYINGS.items():
            for expiry_flag in ("WEEK", "MONTH"):
                # Near-expiry rolling series is the cleanest common basis for cross-history comparisons.
                for relation in OPTION_STRIKES:
                    for option_type in ("CALL", "PUT"):
                        print(f"OPTIONS {name} {expiry_flag} {relation} {option_type} {cursor}->{chunk_end}", flush=True)
                        raw = post("/charts/rollingoption", {
                            "exchangeSegment": "NSE_FNO", "interval": OPTION_INTERVAL,
                            "securityId": meta["security_id"], "instrument": "OPTIDX",
                            "expiryFlag": expiry_flag, "expiryCode": 0,
                            "strike": relation, "drvOptionType": option_type,
                            "requiredData": ["open", "high", "low", "close", "iv", "volume", "strike", "oi", "spot"],
                            "fromDate": cursor.isoformat(), "toDate": chunk_end.isoformat(),
                        })
                        data = (raw.get("data") or {}).get("ce") if option_type == "CALL" else (raw.get("data") or {}).get("pe")
                        if data and data.get("timestamp"):
                            rows = []
                            for i, ts in enumerate(data.get("timestamp", [])):
                                row = {"timestamp": int(ts)}
                                for key in ("open", "high", "low", "close", "volume", "oi", "iv", "strike", "spot"):
                                    arr = data.get(key, [])
                                    row[key] = arr[i] if i < len(arr) else None
                                row.update({"instrument": name, "timeframe": "5M", "expiry_flag": expiry_flag,
                                            "expiry_code": 0, "strike_relation": relation, "option_type": option_type,
                                            "dataset_type": "EXPIRED_OPTION"})
                                rows.append(row)
                            safe = relation.replace("+", "p").replace("-", "m")
                            path = ROOT / "options" / name.lower() / expiry_flag.lower() / f"{cursor}_{chunk_end}_{safe}_{option_type.lower()}.csv.gz"
                            write_rows(path, rows, fields)
                        time.sleep(DELAY)
        cursor = chunk_end


def main():
    if START >= END:
        raise ValueError("Historical start date must be earlier than end date")
    ROOT.mkdir(parents=True, exist_ok=True)
    print(f"FULL PHASE 1 ACQUISITION: {START} -> {END}", flush=True)
    for name in UNDERLYINGS:
        download_index(name, START, END)
    download_futures(START, END)
    download_rolling_options(START, END)
    manifest = {
        "phase": "PHASE_1_MARKET_MEMORY",
        "acquisition": "FULL",
        "start_date": START.isoformat(),
        "end_date_exclusive": END.isoformat(),
        "indices": ["NIFTY", "BANKNIFTY"],
        "index_timeframes": list(INDEX_INTERVALS),
        "futures_timeframe": FUTURE_INTERVAL,
        "options_timeframe": OPTION_INTERVAL,
        "options_strikes": list(OPTION_STRIKES),
        "options_expiry_flags": ["WEEK", "MONTH"],
        "options_expiry_code": 0,
        "source": "DhanHQ v2 historical/intraday/rollingoption + Dhan scrip master",
    }
    (ROOT / "acquisition_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print("FULL_PHASE1_ACQUISITION=PASS", flush=True)


if __name__ == "__main__":
    main()
