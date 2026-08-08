import csv
import gzip
import json
import os
import time
from datetime import date, datetime, timedelta
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

INSTRUMENTS = {
    "NIFTY": {"security_id": "13"},
    "BANKNIFTY": {"security_id": "25"},
}

INTERVAL = "5"
MAX_CHUNK_DAYS = 90
DEFAULT_DAYS = 365
OUTPUT_DIR = Path(os.environ.get("HISTORICAL_OUTPUT_DIR", "historical_data"))
REQUEST_DELAY_SECONDS = float(os.environ.get("HISTORICAL_REQUEST_DELAY", "0.25"))


def parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def request_chunk(security_id: str, start: date, end: date) -> dict:
    # DHAN's toDate is treated as non-inclusive by the downloader.
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

        # DHAN timestamps are epoch seconds. Convert explicitly to IST;
        # never depend on the host machine's local timezone.
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
    # Key by timestamp for deterministic de-duplication at chunk boundaries.
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
        "generated_at_utc": datetime.now(tz=ZoneInfo("UTC")).isoformat(),
        "start_date": start.isoformat(),
        "end_date_exclusive": end.isoformat(),
        "timeframe": "5M",
        "source": "DHAN /v2/charts/intraday",
        "instruments": {},
    }

    for name in INSTRUMENTS:
        rows = download_instrument(name, start, end)
        output = write_gzip_csv(name, rows, start, end)
        manifest["instruments"][name] = {
            "security_id": INSTRUMENTS[name]["security_id"],
            "candles": len(rows),
            "file": str(output),
        }

    manifest_path = OUTPUT_DIR / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"WROTE {manifest_path}", flush=True)


if __name__ == "__main__":
    main()
