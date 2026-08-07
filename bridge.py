import os
import json
import urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TOKEN = os.environ["DHAN_ACCESS_TOKEN"]

INTRADAY_URL = "https://api.dhan.co/v2/charts/intraday"
HISTORICAL_URL = "https://api.dhan.co/v2/charts/historical"

# Dhan index security IDs
INSTRUMENTS = {
    "NIFTY": "13",
    "BANKNIFTY": "25"
}

IST = ZoneInfo("Asia/Kolkata")
now = datetime.now(IST)


def dhan_request(url, payload):
    body = json.dumps(payload).encode("utf-8")

    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "access-token": TOKEN
        },
        method="POST"
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def normalize_candles(raw):
    timestamps = raw.get("timestamp", [])
    opens = raw.get("open", [])
    highs = raw.get("high", [])
    lows = raw.get("low", [])
    closes = raw.get("close", [])
    volumes = raw.get("volume", [])

    candles = []

    count = min(
        len(timestamps),
        len(opens),
        len(highs),
        len(lows),
        len(closes),
        len(volumes)
    )

    for i in range(count):
        candles.append({
            "timestamp": timestamps[i],
            "open": opens[i],
            "high": highs[i],
            "low": lows[i],
            "close": closes[i],
            "volume": volumes[i]
        })

    return candles


def fetch_intraday(security_id, interval):
    # Enough history for Phase 2 structure analysis.
    from_time = now - timedelta(days=30)

    payload = {
        "securityId": security_id,
        "exchangeSegment": "IDX_I",
        "instrument": "INDEX",
        "interval": str(interval),
        "oi": False,
        "fromDate": from_time.strftime("%Y-%m-%d 09:15:00"),
        "toDate": now.strftime("%Y-%m-%d %H:%M:%S")
    }

    raw = dhan_request(INTRADAY_URL, payload)
    return normalize_candles(raw)


def fetch_daily(security_id):
    # ~2 years gives sufficient daily + weekly structural history.
    from_date = now - timedelta(days=730)

    # Dhan historical toDate is non-inclusive.
    to_date = now + timedelta(days=1)

    payload = {
        "securityId": security_id,
        "exchangeSegment": "IDX_I",
        "instrument": "INDEX",
        "expiryCode": 0,
        "oi": False,
        "fromDate": from_date.strftime("%Y-%m-%d"),
        "toDate": to_date.strftime("%Y-%m-%d")
    }

    raw = dhan_request(HISTORICAL_URL, payload)
    return normalize_candles(raw)


def aggregate_weekly(daily_candles):
    weeks = {}

    for candle in daily_candles:
        timestamp = candle["timestamp"]

        dt = datetime.fromtimestamp(timestamp, IST)
        year, week, _ = dt.isocalendar()

        key = f"{year}-{week:02d}"

        if key not in weeks:
            weeks[key] = {
                "timestamp": timestamp,
                "open": candle["open"],
                "high": candle["high"],
                "low": candle["low"],
                "close": candle["close"],
                "volume": candle["volume"]
            }
        else:
            week_candle = weeks[key]

            week_candle["high"] = max(
                week_candle["high"],
                candle["high"]
            )

            week_candle["low"] = min(
                week_candle["low"],
                candle["low"]
            )

            week_candle["close"] = candle["close"]

            week_candle["volume"] += candle["volume"]

    return list(weeks.values())


output = {
    "status": "LIVE",
    "source": "DHAN",
    "generated_at": now.isoformat(),
    "markets": {}
}


for name, security_id in INSTRUMENTS.items():

    print(f"Fetching {name}...")

    daily = fetch_daily(security_id)
    weekly = aggregate_weekly(daily)

    output["markets"][name] = {
        "5m": fetch_intraday(security_id, 5),
        "15m": fetch_intraday(security_id, 15),
        "60m": fetch_intraday(security_id, 60),
        "1D": daily,
        "1W": weekly
    }


with open("market-live.json", "w") as file:
    json.dump(output, file, indent=2)


print("DHAN LIVE MARKET DATA GENERATED")
print("NIFTY + BANKNIFTY")
print("TIMEFRAMES: 5m / 15m / 60m / 1D / 1W")
