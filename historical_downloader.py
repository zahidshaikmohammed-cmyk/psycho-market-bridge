import csv
import gzip
import json
import os
import time
from datetime import date, datetime, timedelta, time as dt_time
from pathlib import Path
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

# ============================================================
# PSYCHO MARKET BRIDGE
# HISTORICAL RESEARCH DOWNLOADER
#
# PURPOSE:
# Download immutable historical 5-minute index candles from DHAN
# for research. This component is deliberately separate from
# bridge.py and MUST NOT modify the live Phase-2 pipeline.
# ============================================================

DHAN_URL = "https://api.dhan.co/v2/charts/intraday"
TOKEN = os.environ["DHAN_ACCESS_TOKEN"]
IST = ZoneInfo("Asia/Kolkata")
UTC = ZoneInfo("UTC")

INSTRUMENTS = {
    "NIFTY": {"security_id": "13"},
    "BANKNIFTY": {"security_id": "25"},
}

INTERVAL = "5"
MAX_CHUNK_DAYS = 90
DEFAULT_DAYS = 365
OUTPUT_DIR = Path(os.environ.get("HISTORICAL_OUTPUT_DIR", "historical_data"))
REQUEST_DELAY_SECONDS = float(os.environ.get("HISTORICAL_REQUEST_DELAY", "0.25"))

MARKET_OPEN = dt_time(9, 15)
MARKET_CLOSE = dt_time(15, 40)


def parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def request_chunk(security_id: str, start: date, end: date) -> dict:
    payload = {
        "securityId": security_id,
        "exchangeSegment": "IDX_I",
        "instrument": "INDEX",
        "interval": INTERVAL,
        "oi": False,
        "fromDate": f"{start:%Y-%m-%d} 09:15:00",
        "toDate": f"{end:%Y-%m-%d} 15:40:00",
    }

    body = json.dumps(payload).encode("utf-8")
    request = Request(
        DHAN_URL,
        data=body,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "access-token": TOKEN,
        },
        method="POST",
    )

    with urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def normalize(raw: dict):
    timestamps = raw.get("timestamp", [])
    opens = raw.get("open", [])
    highs = raw.get("high", [])
    lows = raw.get("low", [])
    closes = raw.get("close", [])
    volumes = raw.get("volume", [])

    count = min(
        len(timestamps), len(opens), len(highs),
        len(lows), len(closes), len(volumes)
    )

    rows = []
    for i in range(count):
        try:
            ts = int(timestamps[i])
        except (TypeError, ValueError):
            continue

        dt = datetime.fromtimestamp(ts, tz=IST)
        rows.append({
            "timestamp": ts,
            "datetime": dt.isoformat(),
            "date": dt.strftime("%Y-%m-%d"),
            "time": dt.strftime("%H:%M:%S"),
            "open": opens[i],
            "high": highs[i],
            "low": lows[i],
            "close": closes[i],
            "volume": volumes[i],
        })

    return rows


def download_instrument(name: str, start: date, end: date):
    candles = {}
    chunk_start = start

    while chunk_start < end:
        chunk_end = min(chunk_start + timedelta(days=MAX_CHUNK_DAYS), end)
        print(f"{name}: requesting {chunk_start} -> {chunk_end}", flush=True)

        raw = request_chunk(
            INSTRUMENTS[name]["security_id"],
            chunk_start,
            chunk_end,
        )

        rows = normalize(raw)
        for row in rows:
            candles[row["timestamp"]] = row

        print(
            f"{name}: received {len(rows)} candles; unique={len(candles)}",
            flush=True,
        )
        chunk_start = chunk_end
        if chunk_start < end:
            time.sleep(REQUEST_DELAY_SECONDS)

    return [candles[key] for key in sorted(candles)]


def write_gzip_csv(name: str, rows, start: date, end: date):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    filename = OUTPUT_DIR / f"{name.lower()}_5m_{start}_{end}.csv.gz"

    fields = [
        "timestamp", "datetime", "date", "time",
        "open", "high", "low", "close", "volume",
    ]

    with gzip.open(filename, "wt", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    print(f"WROTE {filename} ({len(rows)} candles)", flush=True)
    return filename


def audit_rows(name: str, rows):
    """Create a deterministic quality report without changing raw data."""
    report = {
        "instrument": name,
        "timeframe": "5M",
        "total_candles": len(rows),
        "trading_sessions": 0,
        "duplicate_timestamps": 0,
        "missing_ohlcv": 0,
        "invalid_ohlc": 0,
        "timestamp_errors": 0,
        "session_gap_count": 0,
        "opening_candle_anomalies": [],
        "sessions": [],
        "overall_status": "PASS",
    }

    seen = set()
    by_date = {}

    for row in rows:
        try:
            ts = int(row["timestamp"])
            dt = datetime.fromtimestamp(ts, tz=IST)
            if row.get("datetime") != dt.isoformat():
                report["timestamp_errors"] += 1
        except Exception:
            report["timestamp_errors"] += 1
            continue

        if ts in seen:
            report["duplicate_timestamps"] += 1
        seen.add(ts)

        values = [row.get(k) for k in ("open", "high", "low", "close", "volume")]
        if any(v in (None, "") for v in values):
            report["missing_ohlcv"] += 1
        else:
            try:
                o, h, l, c = map(float, values[:4])
                if h < max(o, c) or l > min(o, c) or h < l:
                    report["invalid_ohlc"] += 1
            except (TypeError, ValueError):
                report["invalid_ohlc"] += 1

        by_date.setdefault(dt.date(), []).append((dt, row))

    report["trading_sessions"] = len(by_date)

    for session_date in sorted(by_date):
        entries = sorted(by_date[session_date], key=lambda x: x[0])
        first_dt = entries[0][0]
        last_dt = entries[-1][0]
        gaps = []

        for (prev_dt, _), (curr_dt, _) in zip(entries, entries[1:]):
            minutes = int((curr_dt - prev_dt).total_seconds() / 60)
            if minutes > 5:
                gaps.append({
                    "from": prev_dt.strftime("%H:%M:%S"),
                    "to": curr_dt.strftime("%H:%M:%S"),
                    "gap_minutes": minutes,
                })

        report["session_gap_count"] += len(gaps)

        session_record = {
            "date": session_date.isoformat(),
            "candles": len(entries),
            "first_candle": first_dt.strftime("%H:%M:%S"),
            "last_candle": last_dt.strftime("%H:%M:%S"),
            "gaps_gt_5m": gaps,
        }

        # Opening timestamp is recorded as a research anomaly only.
        # It does NOT by itself change overall_status.
        if first_dt.time() != MARKET_OPEN:
            report["opening_candle_anomalies"].append({
                "date": session_date.isoformat(),
                "expected": MARKET_OPEN.strftime("%H:%M:%S"),
                "actual": first_dt.strftime("%H:%M:%S"),
            })

        report["sessions"].append(session_record)

    hard_failures = (
        report["duplicate_timestamps"]
        + report["missing_ohlcv"]
        + report["invalid_ohlc"]
        + report["timestamp_errors"]
        + report["session_gap_count"]
    )

    # Opening-candle anomalies are explicitly non-fatal research flags.
    # Only structural/data-integrity failures produce FLAG status.
    if hard_failures > 0:
        report["overall_status"] = "FLAG"

    return report


def write_quality_report(reports, start: date, end: date):
    report_path = OUTPUT_DIR / f"quality_report_{start}_{end}.json"
    payload = {
        "generated_at_utc": datetime.now(tz=UTC).isoformat(),
        "start_date": start.isoformat(),
        "end_date_exclusive": end.isoformat(),
        "timeframe": "5M",
        "source": "DHAN /v2/charts/intraday",
        "reports": reports,
        "overall_status": "PASS" if all(
            r["overall_status"] == "PASS" for r in reports.values()
        ) else "FLAG",
    }
    report_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"WROTE {report_path}", flush=True)
    return report_path


def main():
    end = parse_date(
        os.environ.get("HISTORICAL_END_DATE", date.today().isoformat())
    )
    start = parse_date(
        os.environ.get(
            "HISTORICAL_START_DATE",
            (end - timedelta(days=DEFAULT_DAYS)).isoformat(),
        )
    )

    if start >= end:
        raise ValueError(
            "HISTORICAL_START_DATE must be earlier than HISTORICAL_END_DATE"
        )

    print(
        f"Historical research download: {start} -> {end} (end exclusive)",
        flush=True,
    )
    print(
        f"Timeframe: {INTERVAL} minute | max API chunk: {MAX_CHUNK_DAYS} days",
        flush=True,
    )

    manifest = {
        "generated_at_utc": datetime.now(tz=UTC).isoformat(),
        "start_date": start.isoformat(),
        "end_date_exclusive": end.isoformat(),
        "timeframe": "5M",
        "source": "DHAN /v2/charts/intraday",
        "instruments": {},
    }
    quality_reports = {}

    for name in INSTRUMENTS:
        rows = download_instrument(name, start, end)
        output = write_gzip_csv(name, rows, start, end)
        quality_reports[name] = audit_rows(name, rows)
        manifest["instruments"][name] = {
            "security_id": INSTRUMENTS[name]["security_id"],
            "candles": len(rows),
            "file": str(output),
            "quality_status": quality_reports[name]["overall_status"],
        }
        print(
            f"{name}: QUALITY={quality_reports[name]['overall_status']}",
            flush=True,
        )
        if quality_reports[name]["opening_candle_anomalies"]:
            print(
                f"{name}: OPENING_CANDLE_ANOMALY="
                f"{len(quality_reports[name]['opening_candle_anomalies'])}",
                flush=True,
            )

    manifest_path = OUTPUT_DIR / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"WROTE {manifest_path}", flush=True)

    write_quality_report(quality_reports, start, end)


if __name__ == "__main__":
    main()
