import os
import json
import urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TOKEN = os.environ["DHAN_ACCESS_TOKEN"]

URL = "https://api.dhan.co/v2/charts/intraday"

# Dhan index security IDs
INSTRUMENTS = {
    "NIFTY": "13",
    "BANKNIFTY": "25"
}

IST = ZoneInfo("Asia/Kolkata")
now = datetime.now(IST)
start = now - timedelta(days=5)

def fetch_candles(security_id, interval):
    payload = {
        "securityId": security_id,
        "exchangeSegment": "IDX_I",
        "instrument": "INDEX",
        "interval": str(interval),
        "oi": False,
        "fromDate": start.strftime("%Y-%m-%d %H:%M:%S"),
        "toDate": now.strftime("%Y-%m-%d %H:%M:%S")
    }

    req = urllib.request.Request(
        URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "access-token": TOKEN
        },
        method="POST"
    )

    with urllib.request.urlopen(req, timeout=30) as response:
        raw = json.loads(response.read().decode("utf-8"))

    candles = []

    count = min(
        len(raw.get("open", [])),
        len(raw.get("high", [])),
        len(raw.get("low", [])),
        len(raw.get("close", [])),
        len(raw.get("volume", [])),
        len(raw.get("timestamp", []))
    )

    for i in range(count):
        candles.append({
            "timestamp": raw["timestamp"][i],
            "open": raw["open"][i],
            "high": raw["high"][i],
            "low": raw["low"][i],
            "close": raw["close"][i],
            "volume": raw["volume"][i]
        })

    return candles


output = {
    "status": "LIVE",
    "source": "DHAN",
    "generated_at": now.isoformat(),
    "markets": {}
}

for name, security_id in INSTRUMENTS.items():
    output["markets"][name] = {
        "5m": fetch_candles(security_id, 5),
        "15m": fetch_candles(security_id, 15),
        "60m": fetch_candles(security_id, 60)
    }

with open("market-live.json", "w") as f:
    json.dump(output, f, indent=2)

print("DHAN LIVE MARKET DATA GENERATED")
