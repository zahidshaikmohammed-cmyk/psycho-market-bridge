import os
import json
import urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TOKEN = os.environ["DHAN_ACCESS_TOKEN"]
CLIENT_ID = os.environ["DHAN_CLIENT_ID"]

INTRADAY_URL = "https://api.dhan.co/v2/charts/intraday"
HISTORICAL_URL = "https://api.dhan.co/v2/charts/historical"

EXPIRY_LIST_URL = "https://api.dhan.co/v2/optionchain/expirylist"
OPTION_CHAIN_URL = "https://api.dhan.co/v2/optionchain"

# Dhan index security IDs
INSTRUMENTS = {
    "NIFTY": "13",
    "BANKNIFTY": "25"
}

IST = ZoneInfo("Asia/Kolkata")
now = datetime.now(IST)


# ============================================================
# DHAN REQUEST — CHART DATA
# ============================================================

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


# ============================================================
# DHAN REQUEST — OPTION CHAIN
# Requires both access-token and client-id
# ============================================================

def dhan_option_request(url, payload):
    body = json.dumps(payload).encode("utf-8")

    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "access-token": TOKEN,
            "client-id": CLIENT_ID
        },
        method="POST"
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


# ============================================================
# NORMALIZE CANDLES
# ============================================================

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


# ============================================================
# INTRADAY DATA
# ============================================================

def fetch_intraday(security_id, interval):
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


# ============================================================
# DAILY DATA
# ============================================================

def fetch_daily(security_id):
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


# ============================================================
# WEEKLY AGGREGATION
# ============================================================

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


# ============================================================
# OPTION EXPIRY
# ============================================================

def fetch_expiries(security_id):
    payload = {
        "UnderlyingScrip": int(security_id),
        "UnderlyingSeg": "IDX_I"
    }

    response = dhan_option_request(
        EXPIRY_LIST_URL,
        payload
    )

    expiries = response.get("data", [])

    if not expiries:
        raise RuntimeError(
            f"No option expiries returned for security ID {security_id}"
        )

    return expiries


# ============================================================
# OPTION CHAIN
# ============================================================

def fetch_option_chain(security_id, expiry):
    payload = {
        "UnderlyingScrip": int(security_id),
        "UnderlyingSeg": "IDX_I",
        "Expiry": expiry
    }

    response = dhan_option_request(
        OPTION_CHAIN_URL,
        payload
    )

    if response.get("status") != "success":
        raise RuntimeError(
            f"Option chain request failed: {response}"
        )

    return response.get("data", {})


# ============================================================
# BUILD OPTION CHAIN OUTPUT
# ============================================================

def build_option_chain(security_id):
    expiries = fetch_expiries(security_id)

    # Dhan returns active expiries.
    # First active expiry = nearest expiry.
    nearest_expiry = expiries[0]

    chain_data = fetch_option_chain(
        security_id,
        nearest_expiry
    )

    underlying_ltp = chain_data.get("last_price")
    raw_chain = chain_data.get("oc", {})

    strikes = {}

    for strike, option_data in raw_chain.items():

        ce = option_data.get("ce")
        pe = option_data.get("pe")

        strike_output = {}

        if ce:
            ce_oi = ce.get("oi", 0)
            ce_previous_oi = ce.get("previous_oi", 0)

            strike_output["CE"] = {
                "security_id": ce.get("security_id"),
                "ltp": ce.get("last_price"),
                "average_price": ce.get("average_price"),

                "oi": ce_oi,
                "previous_oi": ce_previous_oi,
                "oi_change": ce_oi - ce_previous_oi,

                "volume": ce.get("volume"),
                "previous_volume": ce.get("previous_volume"),

                "iv": ce.get("implied_volatility"),

                "bid_price": ce.get("top_bid_price"),
                "bid_quantity": ce.get("top_bid_quantity"),

                "ask_price": ce.get("top_ask_price"),
                "ask_quantity": ce.get("top_ask_quantity"),

                "previous_close": ce.get(
                    "previous_close_price"
                ),

                "greeks": ce.get("greeks", {})
            }

        if pe:
            pe_oi = pe.get("oi", 0)
            pe_previous_oi = pe.get("previous_oi", 0)

            strike_output["PE"] = {
                "security_id": pe.get("security_id"),
                "ltp": pe.get("last_price"),
                "average_price": pe.get("average_price"),

                "oi": pe_oi,
                "previous_oi": pe_previous_oi,
                "oi_change": pe_oi - pe_previous_oi,

                "volume": pe.get("volume"),
                "previous_volume": pe.get("previous_volume"),

                "iv": pe.get("implied_volatility"),

                "bid_price": pe.get("top_bid_price"),
                "bid_quantity": pe.get("top_bid_quantity"),

                "ask_price": pe.get("top_ask_price"),
                "ask_quantity": pe.get("top_ask_quantity"),

                "previous_close": pe.get(
                    "previous_close_price"
                ),

                "greeks": pe.get("greeks", {})
            }

        strikes[strike] = strike_output

    return {
        "generated_at": datetime.now(IST).isoformat(),
        "expiry": nearest_expiry,
        "underlying_ltp": underlying_ltp,
        "strikes": strikes
    }


# ============================================================
# MAIN OUTPUT
# ============================================================

output = {
    "status": "LIVE",
    "source": "DHAN",
    "generated_at": now.isoformat(),
    "markets": {}
}


# ============================================================
# MARKET STRUCTURE DATA
# ============================================================

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


# ============================================================
# BANK NIFTY LIVE OPTION CHAIN
# ============================================================

print("Fetching BANKNIFTY option expiries...")

banknifty_option_chain = build_option_chain(
    INSTRUMENTS["BANKNIFTY"]
)

output["markets"]["BANKNIFTY"]["option_chain"] = (
    banknifty_option_chain
)


# ============================================================
# WRITE LIVE BRIDGE
# ============================================================

with open("market-live.json", "w") as file:
    json.dump(output, file, indent=2)


print("DHAN LIVE MARKET DATA GENERATED")
print("NIFTY + BANKNIFTY")
print("TIMEFRAMES: 5m / 15m / 60m / 1D / 1W")
print(
    "BANKNIFTY OPTION CHAIN:",
    banknifty_option_chain["expiry"]
)
