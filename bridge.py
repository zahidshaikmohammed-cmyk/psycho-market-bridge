import os
import json
import time
import threading
import urllib.request
from datetime import datetime, timedelta, time as dt_time
from zoneinfo import ZoneInfo

from flask import Flask, send_file, jsonify


# ============================================================
# PSYCHO MARKET BRIDGE
# NIFTY + BANK NIFTY
# LIVE MARKET ENGINE
# ============================================================

TOKEN = os.environ["DHAN_ACCESS_TOKEN"]
CLIENT_ID = os.environ["DHAN_CLIENT_ID"]

IST = ZoneInfo("Asia/Kolkata")

INTRADAY_URL = "https://api.dhan.co/v2/charts/intraday"
HISTORICAL_URL = "https://api.dhan.co/v2/charts/historical"
EXPIRY_LIST_URL = "https://api.dhan.co/v2/optionchain/expirylist"
OPTION_CHAIN_URL = "https://api.dhan.co/v2/optionchain"


# ============================================================
# MARKET SESSION
# ============================================================

MARKET_OPEN = dt_time(9, 15)
MARKET_CLOSE = dt_time(15, 40)

# Full refresh approximately once per minute.
REFRESH_INTERVAL_SECONDS = 60

# Prevent overlapping refresh cycles.
refresh_lock = threading.Lock()


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
# DATA LIMITS
# ============================================================

LIMITS = {
    "1M": 150,
    "5M": 120,
    "15M": 100,
    "1H": 80,
    "1D": 120,
    "1W": 80
}

# ATM + 10 below + 10 above
OPTION_STRIKES_EACH_SIDE = 10


# ============================================================
# HTTP REQUEST
# ============================================================

def dhan_request(
    url,
    payload,
    client_id_required=False
):

    body = json.dumps(
        payload
    ).encode("utf-8")

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

    timestamps = raw.get(
        "timestamp",
        []
    )

    opens = raw.get(
        "open",
        []
    )

    highs = raw.get(
        "high",
        []
    )

    lows = raw.get(
        "low",
        []
    )

    closes = raw.get(
        "close",
        []
    )

    volumes = raw.get(
        "volume",
        []
    )

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

def fetch_intraday(
    security_id,
    interval
):

    now = datetime.now(IST)

    # Recent source history for intraday structure.
    from_time = now - timedelta(
        days=10
    )

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

    return normalize_candles(
        raw
    )


# ============================================================
# FETCH DAILY
# ============================================================

def fetch_daily(security_id):

    now = datetime.now(IST)

    # Enough history to construct weekly structure.
    from_date = now - timedelta(
        days=730
    )

    # Dhan historical toDate is non-inclusive.
    to_date = now + timedelta(
        days=1
    )

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

    return normalize_candles(
        raw
    )


# ============================================================
# COMPLETE DAILY CANDLE FROM INTRADAY
# ============================================================

def append_completed_daily_from_intraday(
    daily,
    intraday
):

    if (
        not isinstance(daily, list)
        or
        not isinstance(intraday, list)
    ):
        return daily

    if not intraday:
        return daily

    now = datetime.now(IST)

    # Only construct today's completed daily candle
    # after normal NSE close.
    if (now.hour, now.minute) < (15, 30):
        return daily

    today = now.date()

    today_candles = []

    for candle in intraday:

        dt = datetime.fromtimestamp(
            candle["timestamp"],
            IST
        )

        if dt.date() == today:
            today_candles.append(
                candle
            )

    if not today_candles:
        return daily

    # Avoid duplicate daily candle.
    for candle in daily:

        dt = datetime.fromtimestamp(
            candle["timestamp"],
            IST
        )

        if dt.date() == today:
            return daily

    today_candles.sort(
        key=lambda x: x["timestamp"]
    )

    daily_candle = {

        "timestamp":
            today_candles[0]["timestamp"],

        "open":
            today_candles[0]["open"],

        "high":
            max(
                c["high"]
                for c in today_candles
            ),

        "low":
            min(
                c["low"]
                for c in today_candles
            ),

        "close":
            today_candles[-1]["close"],

        "volume":
            sum(
                c["volume"]
                for c in today_candles
            )
    }

    daily.append(
        daily_candle
    )

    daily.sort(
        key=lambda x: x["timestamp"]
    )

    return daily


# ============================================================
# DAILY -> WEEKLY
# ============================================================

def aggregate_weekly(
    daily_candles
):

    weeks = {}

    for candle in daily_candles:

        timestamp = candle[
            "timestamp"
        ]

        dt = datetime.fromtimestamp(
            timestamp,
            IST
        )

        iso_year, iso_week, _ = (
            dt.isocalendar()
        )

        key = (
            f"{iso_year}-"
            f"{iso_week:02d}"
        )

        if key not in weeks:

            weeks[key] = {

                "timestamp":
                    timestamp,

                "open":
                    candle["open"],

                "high":
                    candle["high"],

                "low":
                    candle["low"],

                "close":
                    candle["close"],

                "volume":
                    candle["volume"]
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

            current["close"] = (
                candle["close"]
            )

            current["volume"] += (
                candle["volume"]
            )

    weekly = list(
        weeks.values()
    )

    weekly.sort(
        key=lambda x: x["timestamp"]
    )

    return weekly


# ============================================================
# EXPIRY LIST
# ============================================================

def fetch_expiry_list(
    security_id
):

    payload = {
        "UnderlyingScrip":
            int(security_id),

        "UnderlyingSeg":
            "IDX_I"
    }

    raw = dhan_request(
        EXPIRY_LIST_URL,
        payload,
        client_id_required=True
    )

    expiries = raw.get(
        "data",
        []
    )

    if not expiries:

        raise RuntimeError(
            "No active option expiry returned by Dhan"
        )

    return expiries


# ============================================================
# END OF PART 1 / 3
# ============================================================

# ============================================================
# FULL OPTION CHAIN
# ============================================================

def fetch_option_chain(
    security_id,
    expiry
):

    payload = {
        "UnderlyingScrip":
            int(security_id),

        "UnderlyingSeg":
            "IDX_I",

        "Expiry":
            expiry
    }

    return dhan_request(
        OPTION_CHAIN_URL,
        payload,
        client_id_required=True
    )


# ============================================================
# OPTION LEG CLEANER
# ============================================================

def clean_option_leg(leg):

    if not isinstance(leg, dict):
        return None

    greeks = leg.get(
        "greeks"
    ) or {}

    oi = leg.get("oi")
    previous_oi = leg.get(
        "previous_oi"
    )

    if (
        oi is not None
        and
        previous_oi is not None
    ):

        oi_change = (
            oi - previous_oi
        )

    else:

        oi_change = None

    return {

        "security_id":
            leg.get("security_id"),

        "last_price":
            leg.get("last_price"),

        "average_price":
            leg.get("average_price"),

        "oi":
            oi,

        "previous_oi":
            previous_oi,

        "oi_change":
            oi_change,

        "volume":
            leg.get("volume"),

        "previous_volume":
            leg.get(
                "previous_volume"
            ),

        "implied_volatility":
            leg.get(
                "implied_volatility"
            ),

        "previous_close_price":
            leg.get(
                "previous_close_price"
            ),

        "top_bid_price":
            leg.get(
                "top_bid_price"
            ),

        "top_bid_quantity":
            leg.get(
                "top_bid_quantity"
            ),

        "top_ask_price":
            leg.get(
                "top_ask_price"
            ),

        "top_ask_quantity":
            leg.get(
                "top_ask_quantity"
            ),

        "greeks": {

            "delta":
                greeks.get("delta"),

            "theta":
                greeks.get("theta"),

            "gamma":
                greeks.get("gamma"),

            "vega":
                greeks.get("vega")
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

    data = raw.get(
        "data",
        {}
    )

    underlying_ltp = data.get(
        "last_price"
    )

    oc = data.get(
        "oc",
        {}
    )

    strike_rows = []

    for (
        strike_key,
        strike_data
    ) in oc.items():

        try:

            strike_price = float(
                strike_key
            )

        except (
            TypeError,
            ValueError
        ):

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
            "instrument":
                instrument_name,
            "generated_at":
                generated_at,
            "expiry":
                expiry,
            "message":
                "No strikes returned"
        }

    if underlying_ltp is not None:

        atm_index = min(
            range(
                len(strike_rows)
            ),
            key=lambda i: abs(
                strike_rows[i][0]
                -
                float(
                    underlying_ltp
                )
            )
        )

    else:

        atm_index = (
            len(strike_rows) // 2
        )

    atm_strike = (
        strike_rows[
            atm_index
        ][0]
    )

    start = max(
        0,
        atm_index
        -
        OPTION_STRIKES_EACH_SIDE
    )

    end = min(
        len(strike_rows),
        atm_index
        +
        OPTION_STRIKES_EACH_SIDE
        +
        1
    )

    selected = (
        strike_rows[
            start:end
        ]
    )

    strikes = {}

    for (
        strike_price,
        strike_data
    ) in selected:

        strikes[
            str(strike_price)
        ] = {

            "CE":
                clean_option_leg(
                    strike_data.get(
                        "ce"
                    )
                ),

            "PE":
                clean_option_leg(
                    strike_data.get(
                        "pe"
                    )
                )
        }

    return {

        "status":
            "LIVE",

        "source":
            "DHAN",

        "instrument":
            instrument_name,

        "generated_at":
            generated_at,

        "expiry":
            expiry,

        "underlying_ltp":
            underlying_ltp,

        "atm_strike":
            atm_strike,

        "strike_range": {
            "below_atm":
                OPTION_STRIKES_EACH_SIDE,

            "above_atm":
                OPTION_STRIKES_EACH_SIDE
        },

        "strikes":
            strikes
    }


# ============================================================
# WRITE JSON
# ============================================================

def write_json(
    filename,
    data
):

    # Write to temporary file first.
    # Prevents /phase2-live from reading a half-written JSON.
    temp_filename = (
        filename + ".tmp"
    )

    with open(
        temp_filename,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            data,
            file,
            indent=2,
            ensure_ascii=False
        )

    os.replace(
        temp_filename,
        filename
    )


# ============================================================
# SAFE FETCH
# ============================================================

def safe_fetch(
    label,
    function
):

    try:

        result = function()

        print(
            f"SUCCESS: {label}",
            flush=True
        )

        return result

    except Exception as error:

        print(
            f"ERROR: {label}: "
            f"{error}",
            flush=True
        )

        return {
            "error":
                str(error)
        }


# ============================================================
# TRIM CANDLES
# ============================================================

def trim_candles(
    candles,
    timeframe
):

    if not isinstance(
        candles,
        list
    ):
        return candles

    return candles[
        -LIMITS[timeframe]:
    ]


# ============================================================
# BUILD ONE INSTRUMENT
# ============================================================

def build_instrument(
    instrument_name,
    config
):

    security_id = config[
        "security_id"
    ]

    print("")
    print(
        "=" * 60,
        flush=True
    )

    print(
        f"PSYCHO BRIDGE — "
        f"{instrument_name}",
        flush=True
    )

    print(
        "=" * 60,
        flush=True
    )

    generated_at = (
        datetime.now(
            IST
        ).isoformat()
    )


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

    if (
        isinstance(
            daily,
            list
        )
        and
        isinstance(
            candles_5m,
            list
        )
    ):

        daily = (
            append_completed_daily_from_intraday(
                daily,
                candles_5m
            )
        )


    # --------------------------------------------------------
    # WEEKLY
    # --------------------------------------------------------

    if isinstance(
        daily,
        list
    ):

        weekly = (
            aggregate_weekly(
                daily
            )
        )

    else:

        weekly = {
            "error":
                "1W unavailable because "
                "1D failed"
        }


    # --------------------------------------------------------
    # TRIM
    # --------------------------------------------------------

    candles_1m = trim_candles(
        candles_1m,
        "1M"
    )

    candles_5m = trim_candles(
        candles_5m,
        "5M"
    )

    candles_15m = trim_candles(
        candles_15m,
        "15M"
    )

    candles_1h = trim_candles(
        candles_1h,
        "1H"
    )

    daily = trim_candles(
        daily,
        "1D"
    )

    weekly = trim_candles(
        weekly,
        "1W"
    )


    # --------------------------------------------------------
    # MARKET OUTPUT
    # --------------------------------------------------------

    market_output = {

        "status":
            "LIVE",

        "source":
            "DHAN",

        "instrument":
            instrument_name,

        "generated_at":
            generated_at,

        "timeframes": {

            "1M":
                candles_1m,

            "5M":
                candles_5m,

            "15M":
                candles_15m,

            "1H":
                candles_1h,

            "1D":
                daily,

            "1W":
                weekly
        }
    }

    write_json(
        config[
            "market_file"
        ],
        market_output
    )

    print(
        f"CREATED: "
        f"{config['market_file']}",
        flush=True
    )


    # --------------------------------------------------------
    # OPTION EXPIRY
    # --------------------------------------------------------

    expiries = safe_fetch(
        f"{instrument_name} "
        f"EXPIRY LIST",

        lambda:
            fetch_expiry_list(
                security_id
            )
    )

    if (
        isinstance(
            expiries,
            list
        )
        and
        expiries
    ):

        nearest_expiry = (
            expiries[0]
        )

        print(
            f"{instrument_name} "
            f"EXPIRY: "
            f"{nearest_expiry}",
            flush=True
        )

        # Dhan option-chain rate protection.
        time.sleep(3.2)

        raw_chain = safe_fetch(
            f"{instrument_name} "
            f"OPTION CHAIN",

            lambda:
                fetch_option_chain(
                    security_id,
                    nearest_expiry
                )
        )

        if (
            isinstance(
                raw_chain,
                dict
            )
            and
            "error"
            not in raw_chain
        ):

            option_output = (
                compact_option_chain(
                    instrument_name,
                    raw_chain,
                    nearest_expiry,
                    datetime.now(
                        IST
                    ).isoformat()
                )
            )

        else:

            option_output = {

                "status":
                    "ERROR",

                "source":
                    "DHAN",

                "instrument":
                    instrument_name,

                "generated_at":
                    datetime.now(
                        IST
                    ).isoformat(),

                "message":
                    "Option chain "
                    "fetch failed",

                "details":
                    raw_chain
            }

    else:

        option_output = {

            "status":
                "ERROR",

            "source":
                "DHAN",

            "instrument":
                instrument_name,

            "generated_at":
                datetime.now(
                    IST
                ).isoformat(),

            "message":
                "Expiry list "
                "fetch failed",

            "details":
                expiries
        }


    # --------------------------------------------------------
    # OPTION OUTPUT
    # --------------------------------------------------------

    write_json(
        config[
            "option_file"
        ],
        option_output
    )

    print(
        f"CREATED: "
        f"{config['option_file']}",
        flush=True
    )

    # Protect Dhan option-chain API before
    # requesting another underlying.
    time.sleep(3.2)


# ============================================================
# FULL REFRESH CYCLE
# ============================================================

def refresh_all():

    # If another refresh is still running,
    # do not start an overlapping one.
    if not refresh_lock.acquire(
        blocking=False
    ):

        print(
            "REFRESH SKIPPED: "
            "previous cycle still running",
            flush=True
        )

        return

    try:

        cycle_started = (
            datetime.now(
                IST
            )
        )

        print("")
        print(
            "=" * 60,
            flush=True
        )

        print(
            "PSYCHO MARKET BRIDGE "
            "REFRESH START",
            flush=True
        )

        print(
            cycle_started.isoformat(),
            flush=True
        )

        print(
            "=" * 60,
            flush=True
        )

        for (
            instrument_name,
            config
        ) in INSTRUMENTS.items():

            try:

                build_instrument(
                    instrument_name,
                    config
                )

            except Exception as error:

                # One instrument failure must not
                # kill the entire live bridge.
                print(
                    f"FATAL INSTRUMENT ERROR: "
                    f"{instrument_name}: "
                    f"{error}",
                    flush=True
                )

        cycle_finished = (
            datetime.now(
                IST
            )
        )

        print(
            "=" * 60,
            flush=True
        )

        print(
            "PSYCHO MARKET BRIDGE "
            "REFRESH COMPLETE",
            flush=True
        )

        print(
            cycle_finished.isoformat(),
            flush=True
        )

        print(
            "=" * 60,
            flush=True
        )

    finally:

        refresh_lock.release()


# ============================================================
# END OF PART 2 / 3
# ============================================================

# ============================================================
# MARKET SESSION HELPERS
# ============================================================

def is_weekday(now=None):

    if now is None:
        now = datetime.now(IST)

    # Monday = 0
    # Friday = 4
    return now.weekday() < 5


def is_market_window(now=None):

    if now is None:
        now = datetime.now(IST)

    if not is_weekday(now):
        return False

    current_time = now.time()

    return (
        MARKET_OPEN
        <= current_time
        <= MARKET_CLOSE
    )


def market_status():

    now = datetime.now(IST)

    if not is_weekday(now):

        return {
            "status": "CLOSED",
            "reason": "WEEKEND",
            "current_time":
                now.isoformat(),
            "market_open": "09:15 IST",
            "bridge_stop": "15:40 IST"
        }

    if now.time() < MARKET_OPEN:

        return {
            "status": "CLOSED",
            "reason": "PRE_MARKET_WINDOW",
            "current_time":
                now.isoformat(),
            "market_open": "09:15 IST",
            "bridge_stop": "15:40 IST"
        }

    if now.time() > MARKET_CLOSE:

        return {
            "status": "CLOSED",
            "reason": "SESSION_FINISHED",
            "current_time":
                now.isoformat(),
            "market_open": "09:15 IST",
            "bridge_stop": "15:40 IST"
        }

    return {
        "status": "OPEN",
        "reason": "LIVE_MARKET_WINDOW",
        "current_time":
            now.isoformat(),
        "market_open": "09:15 IST",
        "bridge_stop": "15:40 IST"
    }


# ============================================================
# LIVE REFRESH WORKER
# ============================================================

def live_refresh_worker():

    print(
        "PSYCHO LIVE REFRESH WORKER STARTED",
        flush=True
    )

    last_state = None

    while True:

        now = datetime.now(IST)

        if is_market_window(now):

            if last_state != "OPEN":

                print(
                    "MARKET WINDOW OPEN — "
                    "LIVE DHAN REFRESH ACTIVE",
                    flush=True
                )

                last_state = "OPEN"

            cycle_start = time.monotonic()

            try:

                refresh_all()

            except Exception as error:

                # Worker must survive unexpected
                # refresh-level failures.
                print(
                    "REFRESH WORKER ERROR: "
                    f"{error}",
                    flush=True
                )

            cycle_duration = (
                time.monotonic()
                -
                cycle_start
            )

            # Aim for approximately one complete
            # refresh cycle every 60 seconds.
            sleep_seconds = max(
                1,
                REFRESH_INTERVAL_SECONDS
                -
                cycle_duration
            )

            time.sleep(
                sleep_seconds
            )

        else:

            if last_state != "CLOSED":

                print(
                    "MARKET WINDOW CLOSED — "
                    "DHAN AUTO REFRESH PAUSED",
                    flush=True
                )

                last_state = "CLOSED"

            # Outside market hours we only check
            # periodically whether the window opened.
            time.sleep(30)


# ============================================================
# FLASK APPLICATION
# ============================================================

app = Flask(__name__)


# ============================================================
# HOME
# ============================================================

@app.route("/")
def home():

    return jsonify({

        "service":
            "PSYCHO MARKET BRIDGE",

        "status":
            "ONLINE",

        "source":
            "DHAN",

        "timezone":
            "Asia/Kolkata",

        "market_window":
            "09:15-15:40 IST",

        "refresh_interval_seconds":
            REFRESH_INTERVAL_SECONDS,

        "market":
            market_status(),

        "endpoints": [
            "/nifty-live",
            "/nifty-option-chain",
            "/banknifty-live",
            "/banknifty-option-chain",
            "/phase2-live",
            "/bridge-status"
        ]
    })


# ============================================================
# BRIDGE STATUS
# ============================================================

@app.route("/bridge-status")
def bridge_status():

    files_status = {}

    for (
        instrument_name,
        config
    ) in INSTRUMENTS.items():

        files_status[
            instrument_name
        ] = {

            "market_file":
                os.path.exists(
                    config[
                        "market_file"
                    ]
                ),

            "option_file":
                os.path.exists(
                    config[
                        "option_file"
                    ]
                )
        }

    return jsonify({

        "service":
            "PSYCHO MARKET BRIDGE",

        "server":
            "ONLINE",

        "source":
            "DHAN",

        "time":
            datetime.now(
                IST
            ).isoformat(),

        "market":
            market_status(),

        "refresh_interval_seconds":
            REFRESH_INTERVAL_SECONDS,

        "files":
            files_status
    })


# ============================================================
# NIFTY LIVE
# ============================================================

@app.route("/nifty-live")
def nifty_live():

    filename = (
        INSTRUMENTS[
            "NIFTY"
        ]["market_file"]
    )

    if not os.path.exists(
        filename
    ):

        return jsonify({
            "status": "WAITING",
            "message":
                "NIFTY market data "
                "not generated yet"
        }), 503

    return send_file(
        filename,
        mimetype="application/json"
    )


# ============================================================
# NIFTY OPTION CHAIN
# ============================================================

@app.route("/nifty-option-chain")
def nifty_option_chain():

    filename = (
        INSTRUMENTS[
            "NIFTY"
        ]["option_file"]
    )

    if not os.path.exists(
        filename
    ):

        return jsonify({
            "status": "WAITING",
            "message":
                "NIFTY option chain "
                "not generated yet"
        }), 503

    return send_file(
        filename,
        mimetype="application/json"
    )


# ============================================================
# BANK NIFTY LIVE
# ============================================================

@app.route("/banknifty-live")
def banknifty_live():

    filename = (
        INSTRUMENTS[
            "BANKNIFTY"
        ]["market_file"]
    )

    if not os.path.exists(
        filename
    ):

        return jsonify({
            "status": "WAITING",
            "message":
                "BANK NIFTY market data "
                "not generated yet"
        }), 503

    return send_file(
        filename,
        mimetype="application/json"
    )


# ============================================================
# BANK NIFTY OPTION CHAIN
# ============================================================

@app.route("/banknifty-option-chain")
def banknifty_option_chain():

    filename = (
        INSTRUMENTS[
            "BANKNIFTY"
        ]["option_file"]
    )

    if not os.path.exists(
        filename
    ):

        return jsonify({
            "status": "WAITING",
            "message":
                "BANK NIFTY option chain "
                "not generated yet"
        }), 503

    return send_file(
        filename,
        mimetype="application/json"
    )


# ============================================================
# PHASE 2 LIVE
# Human / ChatGPT readable combined endpoint
# ============================================================

@app.route("/phase2-live")
def phase2_live():

    files = [

        (
            "NIFTY LIVE",
            INSTRUMENTS[
                "NIFTY"
            ]["market_file"]
        ),

        (
            "NIFTY OPTION CHAIN",
            INSTRUMENTS[
                "NIFTY"
            ]["option_file"]
        ),

        (
            "BANKNIFTY LIVE",
            INSTRUMENTS[
                "BANKNIFTY"
            ]["market_file"]
        ),

        (
            "BANKNIFTY OPTION CHAIN",
            INSTRUMENTS[
                "BANKNIFTY"
            ]["option_file"]
        )
    ]

    output = []

    output.append(
        "PSYCHO MARKET BRIDGE — "
        "PHASE 2 LIVE"
    )

    output.append(
        "SERVER TIME: "
        +
        datetime.now(
            IST
        ).isoformat()
    )

    status = market_status()

    output.append(
        "MARKET STATUS: "
        +
        status["status"]
    )

    output.append(
        "MARKET WINDOW: "
        "09:15-15:40 IST"
    )

    for title, filename in files:

        output.append(
            "\n"
            +
            "=" * 80
        )

        output.append(
            title
        )

        output.append(
            "=" * 80
        )

        try:

            with open(
                filename,
                "r",
                encoding="utf-8"
            ) as file:

                data = json.load(
                    file
                )

            output.append(
                json.dumps(
                    data,
                    indent=2,
                    ensure_ascii=False
                )
            )

        except Exception as error:

            output.append(
                "ERROR READING "
                +
                filename
                +
                ": "
                +
                str(error)
            )

    return (
        "\n".join(
            output
        ),
        200,
        {
            "Content-Type":
                "text/plain; charset=utf-8"
        }
    )


# ============================================================
# STARTUP
# ============================================================

def start_background_worker():

    worker = threading.Thread(
        target=live_refresh_worker,
        daemon=True,
        name="psycho-live-refresh"
    )

    worker.start()


if __name__ == "__main__":

    print(
        "=" * 60,
        flush=True
    )

    print(
        "PSYCHO MARKET BRIDGE STARTING",
        flush=True
    )

    print(
        "LIVE WINDOW: 09:15-15:40 IST",
        flush=True
    )

    print(
        "REFRESH: APPROX. EVERY "
        f"{REFRESH_INTERVAL_SECONDS} SECONDS",
        flush=True
    )

    print(
        "=" * 60,
        flush=True
    )

    # Start automatic DHAN refresh engine.
    start_background_worker()

    # Start Render web server in main thread.
    port = int(
        os.environ.get(
            "PORT",
            10000
        )
    )

    app.run(
        host="0.0.0.0",
        port=port,
        threaded=True,
        use_reloader=False
    )


# ============================================================
# END OF PART 3 / 3
# ============================================================
