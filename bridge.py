import os
import json
import time
import urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


# ============================================================
# PSYCHO MARKET BRIDGE
# NIFTY + BANK NIFTY
# Separate Market + Option Chain Files
# ============================================================

TOKEN = os.environ["DHAN_ACCESS_TOKEN"]
CLIENT_ID = os.environ["DHAN_CLIENT_ID"]

IST = ZoneInfo("Asia/Kolkata")

INTRADAY_URL = "https://api.dhan.co/v2/charts/intraday"
HISTORICAL_URL = "https://api.dhan.co/v2/charts/historical"
EXPIRY_LIST_URL = "https://api.dhan.co/v2/optionchain/expirylist"
OPTION_CHAIN_URL = "https://api.dhan.co/v2/optionchain"


# ============================================================
# INSTRUMENT CONFIGURATION
# ============================================================

INSTRUMENTS = {
    "NIFTY": {
        "security_id": "13",
        "market_file": "nifty-live.json",
        "option_file": "nifty-option-chain.json"
    },
    "BANKNIFTY": {
        "security_id": "25",
        "market_file": "banknifty-live.json",
        "option_file": "banknifty-option-chain.json"
    }
}


# ============================================================
# HOW MUCH DATA TO KEEP
# Keeps files lightweight
# ============================================================

LIMITS = {
    "1M": 150,
    "5M": 120,
    "15M": 100,
    "1H": 80,
    "1D": 120,
    "1W": 80
}

# ATM + 10 strikes below + 10 above
OPTION_STRIKES_EACH_SIDE = 10


# ============================================================
# HTTP REQUEST
# ============================================================

def dhan_request(url, payload, client_id_required=False):

    body = json.dumps(payload).encode("utf-8")

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "access-token": TOKEN
    }

    if client_id_required:
        headers["client-id"] = CLIENT_ID

    request = urllib.request.Request(
        url,
        data=body,
        headers=headers,
        method="POST"
    )

    with urllib.request.urlopen(
        request,
        timeout=30
    ) as response:

        return json.loads(
            response.read().decode("utf-8")
        )


# ============================================================
# NORMALIZE DHAN CANDLES
# ============================================================

def normalize_candles(raw):

    timestamps = raw.get("timestamp", [])
    opens = raw.get("open", [])
    highs = raw.get("high", [])
    lows = raw.get("low", [])
    closes = raw.get("close", [])
    volumes = raw.get("volume", [])

    count = min(
        len(timestamps),
        len(opens),
        len(highs),
        len(lows),
        len(closes),
        len(volumes)
    )

    candles = []

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
# FETCH INTRADAY
# Supports 1 / 5 / 15 / 60 minutes
# ============================================================

def fetch_intraday(security_id, interval):

    now = datetime.now(IST)

    # We only need recent data for intraday structure.
    from_time = now - timedelta(days=10)

    payload = {
        "securityId": security_id,
        "exchangeSegment": "IDX_I",
        "instrument": "INDEX",
        "interval": str(interval),
        "oi": False,
        "fromDate": from_time.strftime(
            "%Y-%m-%d 09:15:00"
        ),
        "toDate": now.strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    }

    raw = dhan_request(
        INTRADAY_URL,
        payload
    )

    return normalize_candles(raw)


# ============================================================
# FETCH DAILY
# ============================================================

def fetch_daily(security_id):

    now = datetime.now(IST)

    # Enough source history to create weekly structure.
    from_date = now - timedelta(days=730)

    # Dhan historical toDate is non-inclusive.
    to_date = now + timedelta(days=1)

    payload = {
        "securityId": security_id,
        "exchangeSegment": "IDX_I",
        "instrument": "INDEX",
        "expiryCode": 0,
        "oi": False,
        "fromDate": from_date.strftime(
            "%Y-%m-%d"
        ),
        "toDate": to_date.strftime(
            "%Y-%m-%d"
        )
    }

    raw = dhan_request(
        HISTORICAL_URL,
        payload
    )

    return normalize_candles(raw)


# ============================================================
# DAILY -> WEEKLY
# ============================================================

def aggregate_weekly(daily_candles):

    weeks = {}

    for candle in daily_candles:

        timestamp = candle["timestamp"]

        dt = datetime.fromtimestamp(
            timestamp,
            IST
        )

        iso_year, iso_week, _ = dt.isocalendar()

        key = f"{iso_year}-{iso_week:02d}"

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

            current = weeks[key]

            current["high"] = max(
                current["high"],
                candle["high"]
            )

            current["low"] = min(
                current["low"],
                candle["low"]
            )

            current["close"] = candle["close"]

            current["volume"] += candle["volume"]

    return list(weeks.values())


# ============================================================
# EXPIRY LIST
# ============================================================

def fetch_expiry_list(security_id):

    payload = {
        "UnderlyingScrip": int(security_id),
        "UnderlyingSeg": "IDX_I"
    }

    raw = dhan_request(
        EXPIRY_LIST_URL,
        payload,
        client_id_required=True
    )

    expiries = raw.get("data", [])

    if not expiries:
        raise RuntimeError(
            "No active option expiry returned by Dhan"
        )

    return expiries


# ============================================================
# FULL OPTION CHAIN
# ============================================================

def fetch_option_chain(
    security_id,
    expiry
):

    payload = {
        "UnderlyingScrip": int(security_id),
        "UnderlyingSeg": "IDX_I",
        "Expiry": expiry
    }

    return dhan_request(
        OPTION_CHAIN_URL,
        payload,
        client_id_required=True
    )


# ============================================================
# OPTION LEG CLEANER
# Keep institutional fields Phase 2 needs
# ============================================================

def clean_option_leg(leg):

    if not isinstance(leg, dict):
        return None

    greeks = leg.get("greeks") or {}

    return {
        "security_id": leg.get("security_id"),
        "last_price": leg.get("last_price"),
        "average_price": leg.get("average_price"),

        "oi": leg.get("oi"),
        "previous_oi": leg.get("previous_oi"),

        "oi_change": (
            leg.get("oi", 0) -
            leg.get("previous_oi", 0)
            if leg.get("oi") is not None
            and leg.get("previous_oi") is not None
            else None
        ),

        "volume": leg.get("volume"),
        "previous_volume": leg.get(
            "previous_volume"
        ),

        "implied_volatility": leg.get(
            "implied_volatility"
        ),

        "previous_close_price": leg.get(
            "previous_close_price"
        ),

        "top_bid_price": leg.get(
            "top_bid_price"
        ),

        "top_bid_quantity": leg.get(
            "top_bid_quantity"
        ),

        "top_ask_price": leg.get(
            "top_ask_price"
        ),

        "top_ask_quantity": leg.get(
            "top_ask_quantity"
        ),

        "greeks": {
            "delta": greeks.get("delta"),
            "theta": greeks.get("theta"),
            "gamma": greeks.get("gamma"),
            "vega": greeks.get("vega")
        }
    }


# ============================================================
# COMPACT OPTION CHAIN
# ============================================================

def compact_option_chain(
    instrument_name,
    raw,
    expiry,
    generated_at
):

    data = raw.get("data", {})

    underlying_ltp = data.get("last_price")

    oc = data.get("oc", {})

    strike_rows = []

    for strike_key, strike_data in oc.items():

        try:
            strike_price = float(strike_key)
        except (TypeError, ValueError):
            continue

        strike_rows.append(
            (
                strike_price,
                strike_data
            )
        )

    strike_rows.sort(
        key=lambda row: row[0]
    )

    if not strike_rows:

        return {
            "status": "ERROR",
            "source": "DHAN",
            "instrument": instrument_name,
            "generated_at": generated_at,
            "expiry": expiry,
            "message": "No strikes returned"
        }

    # Find nearest strike to underlying LTP.
    if underlying_ltp is not None:

        atm_index = min(
            range(len(strike_rows)),
            key=lambda i: abs(
                strike_rows[i][0] -
                float(underlying_ltp)
            )
        )

    else:

        atm_index = len(strike_rows) // 2

    atm_strike = strike_rows[atm_index][0]

    start = max(
        0,
        atm_index - OPTION_STRIKES_EACH_SIDE
    )

    end = min(
        len(strike_rows),
        atm_index +
        OPTION_STRIKES_EACH_SIDE +
        1
    )

    selected = strike_rows[start:end]

    strikes = {}

    for strike_price, strike_data in selected:

        strikes[str(strike_price)] = {
            "CE": clean_option_leg(
                strike_data.get("ce")
            ),
            "PE": clean_option_leg(
                strike_data.get("pe")
            )
        }

    return {
        "status": "LIVE",
        "source": "DHAN",
        "instrument": instrument_name,
        "generated_at": generated_at,
        "expiry": expiry,
        "underlying_ltp": underlying_ltp,
        "atm_strike": atm_strike,
        "strike_range": {
            "below_atm": OPTION_STRIKES_EACH_SIDE,
            "above_atm": OPTION_STRIKES_EACH_SIDE
        },
        "strikes": strikes
    }


# ============================================================
# WRITE JSON
# Compact format prevents huge GitHub rendering
# ============================================================

def write_json(filename, data):

    with open(
        filename,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            data,
            file,
indent=2,
            ensure_ascii=False
        )


# ============================================================
# SAFE FETCH
# ============================================================

def safe_fetch(label, function):

    try:

        result = function()

        print(
            f"SUCCESS: {label}"
        )

        return result

    except Exception as error:

        print(
            f"ERROR: {label}: {error}"
        )

        return {
            "error": str(error)
        }


# ============================================================
# MAIN BRIDGE
# ============================================================

for instrument_name, config in INSTRUMENTS.items():

    security_id = config["security_id"]

    print("")
    print("=" * 60)
    print(
        f"PSYCHO BRIDGE — {instrument_name}"
    )
    print("=" * 60)

    # Fresh timestamp for this instrument.
    generated_at = datetime.now(
        IST
    ).isoformat()

    # --------------------------------------------------------
    # 1 MINUTE
    # --------------------------------------------------------

    candles_1m = safe_fetch(
        f"{instrument_name} 1M",
        lambda: fetch_intraday(
            security_id,
            1
        )
    )

    # --------------------------------------------------------
    # 5 MINUTE
    # --------------------------------------------------------

    candles_5m = safe_fetch(
        f"{instrument_name} 5M",
        lambda: fetch_intraday(
            security_id,
            5
        )
    )

    # --------------------------------------------------------
    # 15 MINUTE
    # --------------------------------------------------------

    candles_15m = safe_fetch(
        f"{instrument_name} 15M",
        lambda: fetch_intraday(
            security_id,
            15
        )
    )

    # --------------------------------------------------------
    # 1 HOUR
    # --------------------------------------------------------

    candles_1h = safe_fetch(
        f"{instrument_name} 1H",
        lambda: fetch_intraday(
            security_id,
            60
        )
    )

    # --------------------------------------------------------
    # DAILY
    # --------------------------------------------------------

    daily = safe_fetch(
        f"{instrument_name} 1D",
        lambda: fetch_daily(
            security_id
        )
    )

    # --------------------------------------------------------
    # WEEKLY
    # --------------------------------------------------------

    if isinstance(daily, list):

        weekly = aggregate_weekly(
            daily
        )

    else:

        weekly = {
            "error":
            "1W unavailable because 1D failed"
        }

    # --------------------------------------------------------
    # TRIM CANDLES
    # --------------------------------------------------------

    if isinstance(candles_1m, list):
        candles_1m = candles_1m[
            -LIMITS["1M"]:
        ]

    if isinstance(candles_5m, list):
        candles_5m = candles_5m[
            -LIMITS["5M"]:
        ]

    if isinstance(candles_15m, list):
        candles_15m = candles_15m[
            -LIMITS["15M"]:
        ]

    if isinstance(candles_1h, list):
        candles_1h = candles_1h[
            -LIMITS["1H"]:
        ]

    if isinstance(daily, list):
        daily = daily[
            -LIMITS["1D"]:
        ]

    if isinstance(weekly, list):
        weekly = weekly[
            -LIMITS["1W"]:
        ]

    # --------------------------------------------------------
    # MARKET DATA FILE
    # --------------------------------------------------------

    market_output = {
        "status": "LIVE",
        "source": "DHAN",
        "instrument": instrument_name,
        "generated_at": generated_at,

        "timeframes": {
            "1M": candles_1m,
            "5M": candles_5m,
            "15M": candles_15m,
            "1H": candles_1h,
            "1D": daily,
            "1W": weekly
        }
    }

    write_json(
        config["market_file"],
        market_output
    )

    print(
        f"CREATED: {config['market_file']}"
    )

    # --------------------------------------------------------
    # OPTION EXPIRY
    # --------------------------------------------------------

    expiries = safe_fetch(
        f"{instrument_name} EXPIRY LIST",
        lambda: fetch_expiry_list(
            security_id
        )
    )

    if isinstance(expiries, list) and expiries:

        nearest_expiry = expiries[0]

        print(
            f"{instrument_name} EXPIRY: "
            f"{nearest_expiry}"
        )

        # Dhan option chain rate limit:
        # one unique request every 3 seconds.
        time.sleep(3.2)

        raw_chain = safe_fetch(
            f"{instrument_name} OPTION CHAIN",
            lambda: fetch_option_chain(
                security_id,
                nearest_expiry
            )
        )

        if (
            isinstance(raw_chain, dict)
            and "error" not in raw_chain
        ):

            option_output = compact_option_chain(
                instrument_name,
                raw_chain,
                nearest_expiry,
                datetime.now(IST).isoformat()
            )

        else:

            option_output = {
                "status": "ERROR",
                "source": "DHAN",
                "instrument": instrument_name,
                "generated_at":
                    datetime.now(IST).isoformat(),
                "message":
                    "Option chain fetch failed",
                "details": raw_chain
            }

    else:

        option_output = {
            "status": "ERROR",
            "source": "DHAN",
            "instrument": instrument_name,
            "generated_at":
                datetime.now(IST).isoformat(),
            "message":
                "Expiry list fetch failed",
            "details": expiries
        }

    # --------------------------------------------------------
    # OPTION FILE
    # --------------------------------------------------------

    write_json(
        config["option_file"],
        option_output
    )

    print(
        f"CREATED: {config['option_file']}"
    )

    # Protect option-chain API before next underlying.
    time.sleep(3.2)


# ============================================================
# COMPLETE
# ============================================================

print("")
print("=" * 60)
print("PSYCHO MARKET BRIDGE COMPLETE")
print("=" * 60)

print("FILES:")
print("1. nifty-live.json")
print("2. nifty-option-chain.json")
print("3. banknifty-live.json")
print("4. banknifty-option-chain.json")

print("")
print(
    "TIMEFRAMES: "
    "1M / 5M / 15M / 1H / 1D / 1W"
)

print(
    "OPTION CHAIN: "
    "NEAREST EXPIRY / ATM ±10 STRIKES"
)

print("=" * 60)
# Keep Render Web Service alive
from flask import Flask, send_file
import threading
import os

app = Flask(__name__)

@app.route("/")
def home():
    return "PSYCHO MARKET BRIDGE ONLINE"

def run_server():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

threading.Thread(target=run_server, daemon=False).start()
@app.route("/nifty-live")
def nifty_live():
    return send_file("nifty-live.json")

@app.route("/nifty-option-chain")
def nifty_option_chain():
    return send_file("nifty-option-chain.json")

@app.route("/banknifty-live")
def banknifty_live():
    return send_file("banknifty-live.json")

@app.route("/banknifty-option-chain")
def banknifty_option_chain():
    return send_file("banknifty-option-chain.json")
