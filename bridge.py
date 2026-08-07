import os
import json
import time
import threading
import urllib.request

from datetime import datetime, timedelta, time as dt_time
from zoneinfo import ZoneInfo

from flask import Flask, Response, jsonify


# ============================================================
# PSYCHO MARKET BRIDGE
# PHASE 2 LIVE DATA ENGINE
#
# FINAL SESSION-ISOLATED ARCHITECTURE
#
# NIFTY + BANK NIFTY
#
# REQUIRED MARKET DATA:
# 1M + 5M + 15M + 1H + 1D + 1W
# + CURRENT OPTION CHAIN
#
# LIVE WINDOW:
# 09:15 -> 15:40 IST
#
# CORE RULE:
# CURRENT-DAY INTRADAY DATA MUST NEVER MIX
# WITH PREVIOUS-DAY INTRADAY DATA.
# ============================================================


# ============================================================
# DHAN CREDENTIALS
# ============================================================

TOKEN = os.environ["DHAN_ACCESS_TOKEN"]
CLIENT_ID = os.environ["DHAN_CLIENT_ID"]


# ============================================================
# TIMEZONE
# ============================================================

IST = ZoneInfo("Asia/Kolkata")


# ============================================================
# DHAN ENDPOINTS
# ============================================================

INTRADAY_URL = (
    "https://api.dhan.co/v2/charts/intraday"
)

HISTORICAL_URL = (
    "https://api.dhan.co/v2/charts/historical"
)

EXPIRY_LIST_URL = (
    "https://api.dhan.co/v2/optionchain/expirylist"
)

OPTION_CHAIN_URL = (
    "https://api.dhan.co/v2/optionchain"
)


# ============================================================
# MARKET SESSION
# ============================================================

MARKET_OPEN = dt_time(9, 15)

# Bridge continues collecting through 15:40
# so final post-close state can be captured.
MARKET_CLOSE = dt_time(15, 40)

REFRESH_INTERVAL_SECONDS = 60


# ============================================================
# API / REFRESH PROTECTION
# ============================================================

OPTION_CHAIN_DELAY_SECONDS = 3.2

refresh_lock = threading.Lock()


# ============================================================
# INSTRUMENT CONFIGURATION
# ============================================================

INSTRUMENTS = {

    "NIFTY": {

        "display_name":
            "NIFTY",

        "security_id":
            "13",

        "market_file":
            "nifty-live.json",

        "option_file":
            "nifty-option-chain.json",

        "snapshot_file":
            "nifty-session-snapshot.json"
    },

    "BANKNIFTY": {

        "display_name":
            "BANK NIFTY",

        "security_id":
            "25",

        "market_file":
            "banknifty-live.json",

        "option_file":
            "banknifty-option-chain.json",

        "snapshot_file":
            "banknifty-session-snapshot.json"
    }
}


# ============================================================
# CANDLE LIMITS
#
# Intraday limits apply only AFTER current-session filtering.
#
# 1D and 1W intentionally retain historical structure.
# ============================================================

LIMITS = {

    "1M": 400,

    "5M": 200,

    "15M": 120,

    "1H": 100,

    "1D": 120,

    "1W": 80
}


# ============================================================
# OPTION CHAIN RANGE
#
# ATM + 10 strikes below + 10 strikes above
# ============================================================

OPTION_STRIKES_EACH_SIDE = 10


# ============================================================
# BASIC TIME HELPERS
# ============================================================

def now_ist():

    return datetime.now(IST)


def iso_now():

    return now_ist().isoformat()


def date_string(value=None):

    if value is None:
        value = now_ist()

    return value.strftime(
        "%Y-%m-%d"
    )


def is_weekday(value=None):

    if value is None:
        value = now_ist()

    return value.weekday() < 5


def is_market_window(value=None):

    if value is None:
        value = now_ist()

    if not is_weekday(value):
        return False

    current_time = value.time()

    return (
        MARKET_OPEN
        <= current_time
        <= MARKET_CLOSE
    )


# ============================================================
# MARKET STATUS
# ============================================================

def market_status():

    current = now_ist()

    if not is_weekday(current):

        return {

            "status":
                "CLOSED",

            "reason":
                "WEEKEND",

            "current_time":
                current.isoformat(),

            "market_open":
                "09:15 IST",

            "collection_end":
                "15:40 IST"
        }

    if current.time() < MARKET_OPEN:

        return {

            "status":
                "CLOSED",

            "reason":
                "PRE_MARKET",

            "current_time":
                current.isoformat(),

            "market_open":
                "09:15 IST",

            "collection_end":
                "15:40 IST"
        }

    if current.time() > MARKET_CLOSE:

        return {

            "status":
                "CLOSED",

            "reason":
                "SESSION_FINISHED",

            "current_time":
                current.isoformat(),

            "market_open":
                "09:15 IST",

            "collection_end":
                "15:40 IST"
        }

    return {

        "status":
            "OPEN",

        "reason":
            "LIVE_MARKET_WINDOW",

        "current_time":
            current.isoformat(),

        "market_open":
            "09:15 IST",

        "collection_end":
            "15:40 IST"
    }


# ============================================================
# SAFE ATOMIC JSON STORAGE
#
# Write temporary file first.
# Then replace destination atomically.
#
# Phase 2 therefore never reads half-written JSON.
# ============================================================

def write_json_atomic(
    filename,
    data
):

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

        file.flush()

        os.fsync(
            file.fileno()
        )

    os.replace(
        temp_filename,
        filename
    )


# ============================================================
# SAFE JSON READER
# ============================================================

def read_json_file(
    filename
):

    if not os.path.exists(
        filename
    ):

        return None

    try:

        with open(
            filename,
            "r",
            encoding="utf-8"
        ) as file:

            return json.load(
                file
            )

    except Exception as error:

        print(
            f"JSON READ ERROR "
            f"{filename}: {error}",
            flush=True
        )

        return None


# ============================================================
# DHAN HTTP REQUEST
# ============================================================

def dhan_request(
    url,
    payload,
    client_id_required=False
):

    body = json.dumps(
        payload
    ).encode(
        "utf-8"
    )

    headers = {

        "Accept":
            "application/json",

        "Content-Type":
            "application/json",

        "access-token":
            TOKEN
    }

    if client_id_required:

        headers[
            "client-id"
        ] = CLIENT_ID

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

        raw_text = (
            response
            .read()
            .decode(
                "utf-8"
            )
        )

        return json.loads(
            raw_text
        )


# ============================================================
# SAFE DHAN FETCH
#
# One failed request must not crash the bridge.
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
                str(error),

            "label":
                label,

            "generated_at":
                iso_now()
        }


# ============================================================
# NORMALIZE DHAN CANDLES
# ============================================================

def normalize_candles(raw):

    if not isinstance(
        raw,
        dict
    ):

        return []

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

    for index in range(
        count
    ):

        try:

            timestamp = int(
                timestamps[index]
            )

        except (
            TypeError,
            ValueError
        ):

            continue

        candles.append({

            "timestamp":
                timestamp,

            "open":
                opens[index],

            "high":
                highs[index],

            "low":
                lows[index],

            "close":
                closes[index],

            "volume":
                volumes[index]
        })

    candles.sort(
        key=lambda candle:
            candle["timestamp"]
    )

    return candles


# ============================================================
# TIMESTAMP -> IST DATETIME
# ============================================================

def candle_datetime(
    candle
):

    try:

        return datetime.fromtimestamp(
            int(
                candle[
                    "timestamp"
                ]
            ),
            IST
        )

    except Exception:

        return None


# ============================================================
# CURRENT SESSION FILTER
#
# CRITICAL:
# This function prevents yesterday's intraday candles
# from entering today's 1M / 5M / 15M / 1H datasets.
# ============================================================

def filter_session_candles(
    candles,
    session_date
):

    if not isinstance(
        candles,
        list
    ):

        return []

    filtered = []

    for candle in candles:

        candle_dt = candle_datetime(
            candle
        )

        if candle_dt is None:
            continue

        if (
            candle_dt.date()
            !=
            session_date
        ):

            continue

        candle_time = (
            candle_dt.time()
        )

        if (
            candle_time
            <
            MARKET_OPEN
        ):

            continue

        # We intentionally allow data only through
        # the bridge's collection end.
        if (
            candle_time
            >
            MARKET_CLOSE
        ):

            continue

        filtered.append(
            candle
        )

    filtered.sort(
        key=lambda candle:
            candle["timestamp"]
    )

    return filtered


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

        return []

    limit = LIMITS.get(
        timeframe
    )

    if not limit:

        return candles

    return candles[
        -limit:
    ]


# ============================================================
# FETCH INTRADAY
#
# DHAN supports:
# 1 / 5 / 15 / 60 minute intervals
#
# IMPORTANT:
# We request recent source history,
# then FILTER it to the required session date.
# This gives us strict current-day isolation.
# ============================================================

def fetch_intraday(
    security_id,
    interval,
    session_date=None
):

    current = now_ist()

    if session_date is None:

        session_date = (
            current.date()
        )

    # Request enough source history to survive
    # weekends / exchange gaps.
    from_time = (
        current
        -
        timedelta(
            days=10
        )
    )

    payload = {

        "securityId":
            security_id,

        "exchangeSegment":
            "IDX_I",

        "instrument":
            "INDEX",

        "interval":
            str(interval),

        "oi":
            False,

        "fromDate":
            from_time.strftime(
                "%Y-%m-%d 09:15:00"
            ),

        "toDate":
            current.strftime(
                "%Y-%m-%d %H:%M:%S"
            )
    }

    raw = dhan_request(
        INTRADAY_URL,
        payload
    )

    normalized = (
        normalize_candles(
            raw
        )
    )

    return (
        filter_session_candles(
            normalized,
            session_date
        )
    )


# ============================================================
# FETCH DAILY HISTORY
#
# Historical 1D data is intentionally NOT current-session
# isolated because Phase 2 requires market structure history.
# ============================================================

def fetch_daily(
    security_id
):

    current = now_ist()

    from_date = (
        current
        -
        timedelta(
            days=730
        )
    )

    # DHAN historical toDate behaves as non-inclusive.
    to_date = (
        current
        +
        timedelta(
            days=1
        )
    )

    payload = {

        "securityId":
            security_id,

        "exchangeSegment":
            "IDX_I",

        "instrument":
            "INDEX",

        "expiryCode":
            0,

        "oi":
            False,

        "fromDate":
            from_date.strftime(
                "%Y-%m-%d"
            ),

        "toDate":
            to_date.strftime(
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
# DAILY CANDLE DATE
# ============================================================

def candle_date(
    candle
):

    value = candle_datetime(
        candle
    )

    if value is None:
        return None

    return value.date()


# ============================================================
# FIND PREVIOUS COMPLETED TRADING-DAY CANDLE
#
# We do NOT assume "yesterday" because:
# - weekends exist
# - exchange holidays exist
#
# We use the latest daily candle strictly BEFORE
# the current session date.
# ============================================================

def find_previous_daily_candle(
    daily_candles,
    session_date
):

    if not isinstance(
        daily_candles,
        list
    ):

        return None

    candidates = []

    for candle in daily_candles:

        day = candle_date(
            candle
        )

        if day is None:
            continue

        if day < session_date:

            candidates.append(
                (
                    day,
                    candle
                )
            )

    if not candidates:
        return None

    candidates.sort(
        key=lambda item:
            item[0]
    )

    previous_day, previous = (
        candidates[-1]
    )

    return {

        "date":
            previous_day.isoformat(),

        "open":
            previous.get(
                "open"
            ),

        "high":
            previous.get(
                "high"
            ),

        "low":
            previous.get(
                "low"
            ),

        "close":
            previous.get(
                "close"
            ),

        "volume":
            previous.get(
                "volume"
            ),

        "timestamp":
            previous.get(
                "timestamp"
            )
    }


# ============================================================
# BUILD CURRENT DAILY CANDLE FROM TODAY'S INTRADAY
#
# This can represent the developing current-day 1D candle
# during live market hours and the completed candle after close.
# ============================================================

def build_current_daily_candle(
    intraday_candles,
    session_date
):

    if not isinstance(
        intraday_candles,
        list
    ):

        return None

    session = (
        filter_session_candles(
            intraday_candles,
            session_date
        )
    )

    if not session:
        return None

    session.sort(
        key=lambda candle:
            candle["timestamp"]
    )

    return {

        "timestamp":
            session[0][
                "timestamp"
            ],

        "open":
            session[0][
                "open"
            ],

        "high":
            max(
                candle["high"]
                for candle
                in session
            ),

        "low":
            min(
                candle["low"]
                for candle
                in session
            ),

        "close":
            session[-1][
                "close"
            ],

        "volume":
            sum(
                (
                    candle.get(
                        "volume"
                    )
                    or
                    0
                )
                for candle
                in session
            ),

        "session_date":
            session_date.isoformat(),

        "developing":
            is_market_window()
    }


# ============================================================
# MERGE CURRENT DAILY CANDLE INTO DAILY HISTORY
#
# Historical 1D structure remains.
# Today's developing/final candle is represented once only.
# ============================================================

def merge_current_daily(
    daily_history,
    current_daily,
    session_date
):

    if not isinstance(
        daily_history,
        list
    ):

        daily_history = []

    result = []

    for candle in daily_history:

        day = candle_date(
            candle
        )

        # Remove any existing copy of current session
        # before inserting our authoritative current candle.
        if (
            day is not None
            and
            day == session_date
        ):

            continue

        result.append(
            candle
        )

    if current_daily:

        result.append(
            current_daily
        )

    result.sort(
        key=lambda candle:
            candle.get(
                "timestamp",
                0
            )
    )

    return result


# ============================================================
# DAILY -> WEEKLY AGGREGATION
#
# Weekly structure is rebuilt from the final merged
# daily dataset, so the current week's candle automatically
# incorporates today's developing/final daily candle.
# ============================================================

def aggregate_weekly(
    daily_candles
):

    if not isinstance(
        daily_candles,
        list
    ):

        return []

    weeks = {}

    for candle in daily_candles:

        candle_dt = candle_datetime(
            candle
        )

        if candle_dt is None:
            continue

        iso_year, iso_week, _ = (
            candle_dt.isocalendar()
        )

        key = (
            f"{iso_year}-"
            f"{iso_week:02d}"
        )

        if key not in weeks:

            weeks[key] = {

                "timestamp":
                    candle.get(
                        "timestamp"
                    ),

                "week":
                    key,

                "open":
                    candle.get(
                        "open"
                    ),

                "high":
                    candle.get(
                        "high"
                    ),

                "low":
                    candle.get(
                        "low"
                    ),

                "close":
                    candle.get(
                        "close"
                    ),

                "volume":
                    (
                        candle.get(
                            "volume"
                        )
                        or
                        0
                    )
            }

        else:

            current_week = (
                weeks[key]
            )

            candle_high = (
                candle.get(
                    "high"
                )
            )

            candle_low = (
                candle.get(
                    "low"
                )
            )

            if candle_high is not None:

                if (
                    current_week[
                        "high"
                    ]
                    is None
                ):

                    current_week[
                        "high"
                    ] = candle_high

                else:

                    current_week[
                        "high"
                    ] = max(
                        current_week[
                            "high"
                        ],
                        candle_high
                    )

            if candle_low is not None:

                if (
                    current_week[
                        "low"
                    ]
                    is None
                ):

                    current_week[
                        "low"
                    ] = candle_low

                else:

                    current_week[
                        "low"
                    ] = min(
                        current_week[
                            "low"
                        ],
                        candle_low
                    )

            current_week[
                "close"
            ] = candle.get(
                "close"
            )

            current_week[
                "volume"
            ] += (
                candle.get(
                    "volume"
                )
                or
                0
            )

    weekly = list(
        weeks.values()
    )

    weekly.sort(
        key=lambda candle:
            candle.get(
                "timestamp",
                0
            )
    )

    return weekly


# ============================================================
# GAP ANALYSIS
#
# Previous trading-day close is carried only as
# explicit reference data.
#
# Yesterday's intraday candles are NOT carried forward.
# ============================================================

def calculate_gap(
    previous_session,
    current_session_open
):

    if not previous_session:

        return {

            "available":
                False,

            "type":
                "UNAVAILABLE",

            "points":
                None,

            "percent":
                None
        }

    previous_close = (
        previous_session.get(
            "close"
        )
    )

    if (
        previous_close is None
        or
        current_session_open is None
    ):

        return {

            "available":
                False,

            "type":
                "UNAVAILABLE",

            "points":
                None,

            "percent":
                None
        }

    try:

        previous_close = float(
            previous_close
        )

        current_session_open = float(
            current_session_open
        )

    except (
        TypeError,
        ValueError
    ):

        return {

            "available":
                False,

            "type":
                "UNAVAILABLE",

            "points":
                None,

            "percent":
                None
        }

    points = (
        current_session_open
        -
        previous_close
    )

    if previous_close != 0:

        percent = (
            points
            /
            previous_close
        ) * 100

    else:

        percent = None

    if points > 0:

        gap_type = "GAP_UP"

    elif points < 0:

        gap_type = "GAP_DOWN"

    else:

        gap_type = "FLAT"

    return {

        "available":
            True,

        "type":
            gap_type,

        "previous_close":
            previous_close,

        "current_open":
            current_session_open,

        "points":
            round(
                points,
                2
            ),

        "percent":
            (
                round(
                    percent,
                    4
                )
                if
                percent is not None
                else
                None
            )
    }


# ============================================================
# FETCH OPTION EXPIRY LIST
# ============================================================

def fetch_expiry_list(
    security_id
):

    payload = {

        "UnderlyingScrip":
            int(
                security_id
            ),

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
            "No active option expiry "
            "returned by DHAN"
        )

    return expiries


# ============================================================
# FETCH FULL OPTION CHAIN
# ============================================================

def fetch_option_chain(
    security_id,
    expiry
):

    payload = {

        "UnderlyingScrip":
            int(
                security_id
            ),

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
# END OF PART 1 / 3
# ============================================================

# ============================================================
# OPTION LEG CLEANER
# ============================================================

def clean_option_leg(leg):

    if not isinstance(
        leg,
        dict
    ):

        return None

    greeks = (
        leg.get("greeks")
        or
        {}
    )

    oi = leg.get(
        "oi"
    )

    previous_oi = leg.get(
        "previous_oi"
    )

    if (
        oi is not None
        and
        previous_oi is not None
    ):

        try:

            oi_change = (
                float(oi)
                -
                float(previous_oi)
            )

        except (
            TypeError,
            ValueError
        ):

            oi_change = None

    else:

        oi_change = None

    return {

        "security_id":
            leg.get(
                "security_id"
            ),

        "last_price":
            leg.get(
                "last_price"
            ),

        "average_price":
            leg.get(
                "average_price"
            ),

        "oi":
            oi,

        "previous_oi":
            previous_oi,

        "oi_change":
            oi_change,

        "volume":
            leg.get(
                "volume"
            ),

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
                greeks.get(
                    "delta"
                ),

            "theta":
                greeks.get(
                    "theta"
                ),

            "gamma":
                greeks.get(
                    "gamma"
                ),

            "vega":
                greeks.get(
                    "vega"
                )
        }
    }


# ============================================================
# COMPACT OPTION CHAIN
#
# Keeps ATM +/- 10 strikes.
# This is the current-session option-chain snapshot.
# ============================================================

def compact_option_chain(
    instrument_name,
    raw,
    expiry,
    session_date
):

    generated_at = (
        iso_now()
    )

    if not isinstance(
        raw,
        dict
    ):

        return {

            "status":
                "ERROR",

            "source":
                "DHAN",

            "instrument":
                instrument_name,

            "session_date":
                session_date.isoformat(),

            "generated_at":
                generated_at,

            "message":
                "Invalid option-chain response"
        }

    data = raw.get(
        "data",
        {}
    )

    if not isinstance(
        data,
        dict
    ):

        data = {}

    underlying_ltp = (
        data.get(
            "last_price"
        )
    )

    oc = data.get(
        "oc",
        {}
    )

    if not isinstance(
        oc,
        dict
    ):

        oc = {}

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

        if not isinstance(
            strike_data,
            dict
        ):

            strike_data = {}

        strike_rows.append(
            (
                strike_price,
                strike_data
            )
        )

    strike_rows.sort(
        key=lambda item:
            item[0]
    )

    if not strike_rows:

        return {

            "status":
                "ERROR",

            "source":
                "DHAN",

            "instrument":
                instrument_name,

            "session_date":
                session_date.isoformat(),

            "generated_at":
                generated_at,

            "expiry":
                expiry,

            "underlying_ltp":
                underlying_ltp,

            "message":
                "No option strikes returned"
        }

    if underlying_ltp is not None:

        try:

            underlying_value = float(
                underlying_ltp
            )

            atm_index = min(
                range(
                    len(
                        strike_rows
                    )
                ),
                key=lambda index:
                    abs(
                        strike_rows[
                            index
                        ][0]
                        -
                        underlying_value
                    )
            )

        except (
            TypeError,
            ValueError
        ):

            atm_index = (
                len(
                    strike_rows
                )
                //
                2
            )

    else:

        atm_index = (
            len(
                strike_rows
            )
            //
            2
        )

    atm_strike = (
        strike_rows[
            atm_index
        ][0]
    )

    start_index = max(
        0,
        atm_index
        -
        OPTION_STRIKES_EACH_SIDE
    )

    end_index = min(
        len(
            strike_rows
        ),
        atm_index
        +
        OPTION_STRIKES_EACH_SIDE
        +
        1
    )

    selected_rows = (
        strike_rows[
            start_index:
            end_index
        ]
    )

    strikes = {}

    for (
        strike_price,
        strike_data
    ) in selected_rows:

        strike_label = (
            str(
                strike_price
            )
        )

        strikes[
            strike_label
        ] = {

            "strike":
                strike_price,

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

        "session_date":
            session_date.isoformat(),

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
                OPTION_STRIKES_EACH_SIDE,

            "total_returned":
                len(
                    selected_rows
                )
        },

        "strikes":
            strikes
    }


# ============================================================
# CURRENT SESSION SUMMARY
# ============================================================

def build_current_session_summary(
    session_date,
    candles_1m,
    previous_session
):

    if not isinstance(
        candles_1m,
        list
    ):

        candles_1m = []

    if not candles_1m:

        return {

            "session_date":
                session_date.isoformat(),

            "available":
                False,

            "open":
                None,

            "high":
                None,

            "low":
                None,

            "last_price":
                None,

            "latest_candle_time":
                None,

            "gap":
                calculate_gap(
                    previous_session,
                    None
                )
        }

    candles_1m.sort(
        key=lambda candle:
            candle["timestamp"]
    )

    first_candle = (
        candles_1m[0]
    )

    last_candle = (
        candles_1m[-1]
    )

    session_open = (
        first_candle.get(
            "open"
        )
    )

    session_high_values = [

        candle.get(
            "high"
        )

        for candle
        in candles_1m

        if candle.get(
            "high"
        )
        is not None
    ]

    session_low_values = [

        candle.get(
            "low"
        )

        for candle
        in candles_1m

        if candle.get(
            "low"
        )
        is not None
    ]

    latest_dt = candle_datetime(
        last_candle
    )

    return {

        "session_date":
            session_date.isoformat(),

        "available":
            True,

        "open":
            session_open,

        "high":
            (
                max(
                    session_high_values
                )
                if
                session_high_values
                else
                None
            ),

        "low":
            (
                min(
                    session_low_values
                )
                if
                session_low_values
                else
                None
            ),

        "last_price":
            last_candle.get(
                "close"
            ),

        "latest_candle_time":
            (
                latest_dt.isoformat()
                if
                latest_dt
                else
                None
            ),

        "gap":
            calculate_gap(
                previous_session,
                session_open
            )
    }


# ============================================================
# SNAPSHOT SESSION DATE
# ============================================================

def get_snapshot_session_date(
    snapshot
):

    if not isinstance(
        snapshot,
        dict
    ):

        return None

    value = snapshot.get(
        "session_date"
    )

    if not value:
        return None

    try:

        return datetime.strptime(
            value,
            "%Y-%m-%d"
        ).date()

    except Exception:

        return None


# ============================================================
# READ LAST SESSION SNAPSHOT
#
# Used outside live hours so the latest completed trading
# session remains available until the next session starts.
# ============================================================

def read_last_snapshot(
    config
):

    return read_json_file(
        config[
            "snapshot_file"
        ]
    )


# ============================================================
# PRESERVE COMPLETED SNAPSHOT
#
# Every successful live refresh updates the snapshot.
# Therefore after 15:40 the final successful state remains.
# ============================================================

def save_session_snapshot(
    config,
    combined_snapshot
):

    write_json_atomic(
        config[
            "snapshot_file"
        ],
        combined_snapshot
    )


# ============================================================
# BUILD ONE INSTRUMENT
#
# CRITICAL SESSION RULE:
#
# 1M / 5M / 15M / 1H:
# CURRENT SESSION DATE ONLY.
#
# 1D / 1W:
# HISTORICAL STRUCTURE + CURRENT DEVELOPING DAY.
#
# PREVIOUS SESSION:
# ONLY explicit daily reference is carried forward.
#
# OPTION CHAIN:
# CURRENT REFRESH SNAPSHOT ONLY.
# ============================================================

def build_instrument(
    instrument_key,
    config,
    session_date
):

    instrument_name = (
        config[
            "display_name"
        ]
    )

    security_id = (
        config[
            "security_id"
        ]
    )

    print("")
    print(
        "=" * 70,
        flush=True
    )

    print(
        f"BUILDING {instrument_name} "
        f"SESSION {session_date.isoformat()}",
        flush=True
    )

    print(
        "=" * 70,
        flush=True
    )


    # --------------------------------------------------------
    # 1 MINUTE
    # --------------------------------------------------------

    candles_1m = safe_fetch(
        f"{instrument_name} 1M",
        lambda:
            fetch_intraday(
                security_id,
                1,
                session_date
            )
    )

    if not isinstance(
        candles_1m,
        list
    ):

        candles_1m = []


    # --------------------------------------------------------
    # 5 MINUTE
    # --------------------------------------------------------

    candles_5m = safe_fetch(
        f"{instrument_name} 5M",
        lambda:
            fetch_intraday(
                security_id,
                5,
                session_date
            )
    )

    if not isinstance(
        candles_5m,
        list
    ):

        candles_5m = []


    # --------------------------------------------------------
    # 15 MINUTE
    # --------------------------------------------------------

    candles_15m = safe_fetch(
        f"{instrument_name} 15M",
        lambda:
            fetch_intraday(
                security_id,
                15,
                session_date
            )
    )

    if not isinstance(
        candles_15m,
        list
    ):

        candles_15m = []


    # --------------------------------------------------------
    # 1 HOUR
    # --------------------------------------------------------

    candles_1h = safe_fetch(
        f"{instrument_name} 1H",
        lambda:
            fetch_intraday(
                security_id,
                60,
                session_date
            )
    )

    if not isinstance(
        candles_1h,
        list
    ):

        candles_1h = []


    # --------------------------------------------------------
    # DAILY HISTORY
    # --------------------------------------------------------

    daily_history = safe_fetch(
        f"{instrument_name} 1D",
        lambda:
            fetch_daily(
                security_id
            )
    )

    if not isinstance(
        daily_history,
        list
    ):

        daily_history = []


    # --------------------------------------------------------
    # PREVIOUS TRADING SESSION
    #
    # Latest completed daily candle strictly before today.
    # --------------------------------------------------------

    previous_session = (
        find_previous_daily_candle(
            daily_history,
            session_date
        )
    )


    # --------------------------------------------------------
    # CURRENT DEVELOPING DAILY CANDLE
    #
    # Build from today's isolated 1M data.
    # --------------------------------------------------------

    current_daily = (
        build_current_daily_candle(
            candles_1m,
            session_date
        )
    )


    # --------------------------------------------------------
    # MERGED 1D
    # --------------------------------------------------------

    daily = merge_current_daily(
        daily_history,
        current_daily,
        session_date
    )


    # --------------------------------------------------------
    # 1 WEEK
    # --------------------------------------------------------

    weekly = aggregate_weekly(
        daily
    )


    # --------------------------------------------------------
    # TRIM ALL TIMEFRAMES
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
    # CURRENT SESSION SUMMARY + GAP
    # --------------------------------------------------------

    current_session = (
        build_current_session_summary(
            session_date,
            candles_1m,
            previous_session
        )
    )


    # --------------------------------------------------------
    # MARKET DATA OUTPUT
    # --------------------------------------------------------

    market_generated_at = (
        iso_now()
    )

    market_output = {

        "status":
            "LIVE",

        "source":
            "DHAN",

        "instrument":
            instrument_name,

        "instrument_key":
            instrument_key,

        "security_id":
            security_id,

        "session_date":
            session_date.isoformat(),

        "generated_at":
            market_generated_at,

        "session_isolation":
            True,

        "previous_session":
            previous_session,

        "current_session":
            current_session,

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


    # --------------------------------------------------------
    # WRITE MARKET FILE IMMEDIATELY
    # --------------------------------------------------------

    write_json_atomic(
        config[
            "market_file"
        ],
        market_output
    )

    print(
        f"UPDATED: "
        f"{config['market_file']}",
        flush=True
    )


    # --------------------------------------------------------
    # OPTION EXPIRY LIST
    # --------------------------------------------------------

    expiries = safe_fetch(
        f"{instrument_name} "
        f"EXPIRY LIST",

        lambda:
            fetch_expiry_list(
                security_id
            )
    )


    # --------------------------------------------------------
    # OPTION CHAIN
    # --------------------------------------------------------

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
            f"NEAREST EXPIRY: "
            f"{nearest_expiry}",
            flush=True
        )

        # Dhan option-chain rate protection.
        time.sleep(
            OPTION_CHAIN_DELAY_SECONDS
        )

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
                    session_date
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

                "session_date":
                    session_date.isoformat(),

                "generated_at":
                    iso_now(),

                "message":
                    "Option-chain fetch failed",

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

            "session_date":
                session_date.isoformat(),

            "generated_at":
                iso_now(),

            "message":
                "Expiry-list fetch failed",

            "details":
                expiries
        }


    # --------------------------------------------------------
    # WRITE CURRENT OPTION CHAIN IMMEDIATELY
    # --------------------------------------------------------

    write_json_atomic(
        config[
            "option_file"
        ],
        option_output
    )

    print(
        f"UPDATED: "
        f"{config['option_file']}",
        flush=True
    )


    # --------------------------------------------------------
    # COMBINED SESSION SNAPSHOT
    #
    # This is what survives after market close.
    # It contains today's final/current market state
    # + today's latest option chain.
    # --------------------------------------------------------

    combined_snapshot = {

        "status":
            "LIVE",

        "source":
            "DHAN",

        "instrument":
            instrument_name,

        "instrument_key":
            instrument_key,

        "session_date":
            session_date.isoformat(),

        "snapshot_generated_at":
            iso_now(),

        "market":
            market_output,

        "option_chain":
            option_output
    }


    # --------------------------------------------------------
    # SAVE SNAPSHOT
    # --------------------------------------------------------

    save_session_snapshot(
        config,
        combined_snapshot
    )

    print(
        f"SNAPSHOT UPDATED: "
        f"{config['snapshot_file']}",
        flush=True
    )


    # Protect Dhan before another underlying.
    time.sleep(
        OPTION_CHAIN_DELAY_SECONDS
    )

    return combined_snapshot


# ============================================================
# GET SERVABLE INSTRUMENT STATE
#
# During live market:
# current files/snapshot represent current session.
#
# Outside market:
# last completed/latest snapshot remains available.
# ============================================================

def get_servable_instrument_state(
    instrument_key
):

    config = INSTRUMENTS[
        instrument_key
    ]

    snapshot = read_last_snapshot(
        config
    )

    if isinstance(
        snapshot,
        dict
    ):

        return snapshot

    market_data = read_json_file(
        config[
            "market_file"
        ]
    )

    option_data = read_json_file(
        config[
            "option_file"
        ]
    )

    if (
        market_data is None
        and
        option_data is None
    ):

        return {

            "status":
                "WAITING",

            "source":
                "DHAN",

            "instrument":
                config[
                    "display_name"
                ],

            "session_date":
                None,

            "snapshot_generated_at":
                None,

            "market":
                None,

            "option_chain":
                None
        }

    session_date = None

    if isinstance(
        market_data,
        dict
    ):

        session_date = (
            market_data.get(
                "session_date"
            )
        )

    if (
        session_date is None
        and
        isinstance(
            option_data,
            dict
        )
    ):

        session_date = (
            option_data.get(
                "session_date"
            )
        )

    return {

        "status":
            "AVAILABLE",

        "source":
            "DHAN",

        "instrument":
            config[
                "display_name"
            ],

        "session_date":
            session_date,

        "snapshot_generated_at":
            iso_now(),

        "market":
            market_data,

        "option_chain":
            option_data
    }


# ============================================================
# FULL REFRESH CYCLE
#
# Approximately once every minute during live window.
#
# Non-blocking lock prevents two refresh cycles
# from overlapping.
# ============================================================

def refresh_all():

    if not refresh_lock.acquire(
        blocking=False
    ):

        print(
            "REFRESH SKIPPED: "
            "previous cycle still running",
            flush=True
        )

        return False

    try:

        current = now_ist()

        session_date = (
            current.date()
        )

        cycle_started = (
            current.isoformat()
        )

        print("")
        print(
            "=" * 70,
            flush=True
        )

        print(
            "PSYCHO MARKET BRIDGE "
            "LIVE REFRESH START",
            flush=True
        )

        print(
            f"SESSION: "
            f"{session_date.isoformat()}",
            flush=True
        )

        print(
            f"STARTED: "
            f"{cycle_started}",
            flush=True
        )

        print(
            "=" * 70,
            flush=True
        )

        successful = 0

        for (
            instrument_key,
            config
        ) in INSTRUMENTS.items():

            try:

                result = build_instrument(
                    instrument_key,
                    config,
                    session_date
                )

                if isinstance(
                    result,
                    dict
                ):

                    successful += 1

            except Exception as error:

                # Failure of one instrument must not
                # kill the other instrument or worker.
                print(
                    f"INSTRUMENT BUILD ERROR: "
                    f"{instrument_key}: "
                    f"{error}",
                    flush=True
                )

        print(
            "=" * 70,
            flush=True
        )

        print(
            "PSYCHO MARKET BRIDGE "
            "LIVE REFRESH COMPLETE",
            flush=True
        )

        print(
            f"SUCCESSFUL INSTRUMENTS: "
            f"{successful}/"
            f"{len(INSTRUMENTS)}",
            flush=True
        )

        print(
            f"FINISHED: "
            f"{iso_now()}",
            flush=True
        )

        print(
            "=" * 70,
            flush=True
        )

        return (
            successful > 0
        )

    finally:

        refresh_lock.release()


# ============================================================
# SESSION ROLLOVER CHECK
#
# At a new trading day's 09:15:
#
# We do NOT copy yesterday's 1M/5M/15M/1H or option chain
# into today's live session.
#
# The new build fetches + filters TODAY only.
#
# Yesterday's latest completed daily close is independently
# recovered from historical 1D for gap analysis.
# ============================================================

def session_rollover_required():

    current = now_ist()

    if not is_market_window(
        current
    ):

        return False

    current_session_date = (
        current.date()
    )

    for (
        instrument_key,
        config
    ) in INSTRUMENTS.items():

        snapshot = read_last_snapshot(
            config
        )

        snapshot_date = (
            get_snapshot_session_date(
                snapshot
            )
        )

        if (
            snapshot_date
            !=
            current_session_date
        ):

            return True

    return False


# ============================================================
# END OF PART 2 / 3
# ============================================================

# ============================================================
# LIVE REFRESH WORKER
#
# BEHAVIOUR:
#
# 09:15 -> 15:40 IST
#   Refresh approximately every 60 seconds.
#
# After 15:40
#   Stop DHAN refresh.
#   Keep serving final session snapshot.
#
# Before next 09:15
#   Keep serving previous completed session.
#
# Next trading day at 09:15
#   New current-day isolated session begins automatically.
# ============================================================

def live_refresh_worker():

    print(
        "PSYCHO LIVE REFRESH WORKER STARTED",
        flush=True
    )

    last_market_state = None

    while True:

        try:

            current = now_ist()

            if is_market_window(
                current
            ):

                if (
                    last_market_state
                    !=
                    "OPEN"
                ):

                    print(
                        "=" * 70,
                        flush=True
                    )

                    print(
                        "MARKET WINDOW OPEN",
                        flush=True
                    )

                    print(
                        "CURRENT-DAY LIVE "
                        "REFRESH ACTIVE",
                        flush=True
                    )

                    print(
                        f"SESSION DATE: "
                        f"{current.date().isoformat()}",
                        flush=True
                    )

                    print(
                        "=" * 70,
                        flush=True
                    )

                    last_market_state = (
                        "OPEN"
                    )

                # --------------------------------------------
                # Start refresh cycle.
                # --------------------------------------------

                cycle_start = (
                    time.monotonic()
                )

                try:

                    refresh_all()

                except Exception as error:

                    # Never allow an unexpected cycle error
                    # to kill the permanent worker.
                    print(
                        "LIVE REFRESH WORKER "
                        f"CYCLE ERROR: {error}",
                        flush=True
                    )

                cycle_duration = (
                    time.monotonic()
                    -
                    cycle_start
                )

                # --------------------------------------------
                # Approximate 60-second cycle.
                #
                # API time is counted inside the minute.
                # If a cycle itself takes >60 sec,
                # immediately begin next cycle after 1 sec.
                # --------------------------------------------

                sleep_seconds = max(
                    1,
                    REFRESH_INTERVAL_SECONDS
                    -
                    cycle_duration
                )

                print(
                    "NEXT LIVE REFRESH IN "
                    f"{round(sleep_seconds, 1)} "
                    "SECONDS",
                    flush=True
                )

                time.sleep(
                    sleep_seconds
                )

            else:

                if (
                    last_market_state
                    !=
                    "CLOSED"
                ):

                    print(
                        "=" * 70,
                        flush=True
                    )

                    print(
                        "MARKET WINDOW CLOSED",
                        flush=True
                    )

                    print(
                        "DHAN LIVE REFRESH PAUSED",
                        flush=True
                    )

                    print(
                        "LATEST SESSION SNAPSHOT "
                        "REMAINS AVAILABLE",
                        flush=True
                    )

                    print(
                        "=" * 70,
                        flush=True
                    )

                    last_market_state = (
                        "CLOSED"
                    )

                # Outside live hours we do not burn
                # DHAN requests.
                #
                # Check twice per minute for next opening.
                time.sleep(
                    30
                )

        except Exception as error:

            # Absolute outer protection.
            # Worker should recover instead of dying.
            print(
                "BACKGROUND WORKER ERROR: "
                f"{error}",
                flush=True
            )

            time.sleep(
                10
            )


# ============================================================
# FLASK APPLICATION
# ============================================================

app = Flask(
    __name__
)


# ============================================================
# TEXT RESPONSE HELPER
#
# /phase2-live intentionally uses TEXT/PLAIN UTF-8.
#
# This is specifically designed to avoid the previous
# opaque/minified application/json retrieval problem.
# ============================================================

def text_response(
    text,
    status=200
):

    return Response(
        text,
        status=status,
        content_type=(
            "text/plain; "
            "charset=utf-8"
        )
    )


# ============================================================
# PRETTY VALUE
# ============================================================

def pretty_value(
    value
):

    if value is None:
        return "N/A"

    return str(
        value
    )


# ============================================================
# CANDLE TIME STRING
# ============================================================

def readable_candle_time(
    candle
):

    if not isinstance(
        candle,
        dict
    ):

        return "N/A"

    candle_dt = candle_datetime(
        candle
    )

    if candle_dt is None:
        return "N/A"

    return candle_dt.strftime(
        "%Y-%m-%d %H:%M:%S IST"
    )


# ============================================================
# FORMAT CANDLE FOR PHASE 2
# ============================================================

def format_candle_line(
    candle
):

    if not isinstance(
        candle,
        dict
    ):

        return "INVALID CANDLE"

    return (
        f"{readable_candle_time(candle)}"
        f" | O={pretty_value(candle.get('open'))}"
        f" | H={pretty_value(candle.get('high'))}"
        f" | L={pretty_value(candle.get('low'))}"
        f" | C={pretty_value(candle.get('close'))}"
        f" | V={pretty_value(candle.get('volume'))}"
    )


# ============================================================
# FORMAT TIMEFRAME
# ============================================================

def append_timeframe_section(
    lines,
    label,
    candles
):

    lines.append("")
    lines.append(
        "-" * 78
    )

    lines.append(
        f"TIMEFRAME: {label}"
    )

    lines.append(
        "-" * 78
    )

    if not isinstance(
        candles,
        list
    ):

        lines.append(
            "STATUS: UNAVAILABLE"
        )

        return

    lines.append(
        f"CANDLE COUNT: "
        f"{len(candles)}"
    )

    if not candles:

        lines.append(
            "NO CANDLES AVAILABLE"
        )

        return

    latest = candles[-1]

    lines.append(
        "LATEST CANDLE: "
        +
        readable_candle_time(
            latest
        )
    )

    lines.append("")

    for candle in candles:

        lines.append(
            format_candle_line(
                candle
            )
        )


# ============================================================
# FORMAT OPTION LEG FOR PHASE 2
# ============================================================

def format_option_leg(
    label,
    leg
):

    if not isinstance(
        leg,
        dict
    ):

        return (
            f"{label}: N/A"
        )

    greeks = (
        leg.get(
            "greeks"
        )
        or
        {}
    )

    return (
        f"{label}: "
        f"LTP={pretty_value(leg.get('last_price'))}"
        f" | OI={pretty_value(leg.get('oi'))}"
        f" | PrevOI={pretty_value(leg.get('previous_oi'))}"
        f" | OIChange={pretty_value(leg.get('oi_change'))}"
        f" | Vol={pretty_value(leg.get('volume'))}"
        f" | IV={pretty_value(leg.get('implied_volatility'))}"
        f" | Bid={pretty_value(leg.get('top_bid_price'))}"
        f" x {pretty_value(leg.get('top_bid_quantity'))}"
        f" | Ask={pretty_value(leg.get('top_ask_price'))}"
        f" x {pretty_value(leg.get('top_ask_quantity'))}"
        f" | Delta={pretty_value(greeks.get('delta'))}"
        f" | Theta={pretty_value(greeks.get('theta'))}"
        f" | Gamma={pretty_value(greeks.get('gamma'))}"
        f" | Vega={pretty_value(greeks.get('vega'))}"
    )


# ============================================================
# FORMAT OPTION CHAIN
# ============================================================

def append_option_chain_section(
    lines,
    option_chain
):

    lines.append("")
    lines.append(
        "=" * 78
    )

    lines.append(
        "OPTION CHAIN"
    )

    lines.append(
        "=" * 78
    )

    if not isinstance(
        option_chain,
        dict
    ):

        lines.append(
            "STATUS: UNAVAILABLE"
        )

        return

    lines.append(
        "STATUS: "
        +
        pretty_value(
            option_chain.get(
                "status"
            )
        )
    )

    lines.append(
        "SOURCE: "
        +
        pretty_value(
            option_chain.get(
                "source"
            )
        )
    )

    lines.append(
        "SESSION DATE: "
        +
        pretty_value(
            option_chain.get(
                "session_date"
            )
        )
    )

    lines.append(
        "GENERATED AT: "
        +
        pretty_value(
            option_chain.get(
                "generated_at"
            )
        )
    )

    lines.append(
        "EXPIRY: "
        +
        pretty_value(
            option_chain.get(
                "expiry"
            )
        )
    )

    lines.append(
        "UNDERLYING LTP: "
        +
        pretty_value(
            option_chain.get(
                "underlying_ltp"
            )
        )
    )

    lines.append(
        "ATM STRIKE: "
        +
        pretty_value(
            option_chain.get(
                "atm_strike"
            )
        )
    )

    strikes = option_chain.get(
        "strikes",
        {}
    )

    if not isinstance(
        strikes,
        dict
    ):

        strikes = {}

    lines.append(
        "STRIKES RETURNED: "
        +
        str(
            len(
                strikes
            )
        )
    )

    if not strikes:

        message = option_chain.get(
            "message"
        )

        if message:

            lines.append(
                "MESSAGE: "
                +
                str(
                    message
                )
            )

        return

    def strike_sort_key(
        item
    ):

        try:

            return float(
                item[0]
            )

        except Exception:

            return 0.0

    sorted_strikes = sorted(
        strikes.items(),
        key=strike_sort_key
    )

    for (
        strike_label,
        strike_data
    ) in sorted_strikes:

        lines.append("")
        lines.append(
            f"STRIKE: {strike_label}"
        )

        if not isinstance(
            strike_data,
            dict
        ):

            lines.append(
                "INVALID STRIKE DATA"
            )

            continue

        lines.append(
            format_option_leg(
                "CE",
                strike_data.get(
                    "CE"
                )
            )
        )

        lines.append(
            format_option_leg(
                "PE",
                strike_data.get(
                    "PE"
                )
            )
        )


# ============================================================
# FORMAT ONE INSTRUMENT FOR PHASE 2
# ============================================================

def append_instrument_section(
    lines,
    instrument_key,
    state
):

    config = INSTRUMENTS[
        instrument_key
    ]

    instrument_name = config[
        "display_name"
    ]

    lines.append("")
    lines.append("")
    lines.append(
        "#" * 78
    )

    lines.append(
        f"{instrument_name}"
    )

    lines.append(
        "#" * 78
    )

    if not isinstance(
        state,
        dict
    ):

        lines.append(
            "STATUS: UNAVAILABLE"
        )

        return

    lines.append(
        "SNAPSHOT STATUS: "
        +
        pretty_value(
            state.get(
                "status"
            )
        )
    )

    lines.append(
        "SOURCE: "
        +
        pretty_value(
            state.get(
                "source"
            )
        )
    )

    lines.append(
        "SESSION DATE: "
        +
        pretty_value(
            state.get(
                "session_date"
            )
        )
    )

    lines.append(
        "SNAPSHOT GENERATED AT: "
        +
        pretty_value(
            state.get(
                "snapshot_generated_at"
            )
        )
    )

    market = state.get(
        "market"
    )

    option_chain = state.get(
        "option_chain"
    )

    if not isinstance(
        market,
        dict
    ):

        lines.append("")
        lines.append(
            "MARKET DATA: UNAVAILABLE"
        )

        append_option_chain_section(
            lines,
            option_chain
        )

        return


    # --------------------------------------------------------
    # MARKET METADATA
    # --------------------------------------------------------

    lines.append("")
    lines.append(
        "=" * 78
    )

    lines.append(
        "MARKET DATA"
    )

    lines.append(
        "=" * 78
    )

    lines.append(
        "STATUS: "
        +
        pretty_value(
            market.get(
                "status"
            )
        )
    )

    lines.append(
        "SOURCE: "
        +
        pretty_value(
            market.get(
                "source"
            )
        )
    )

    lines.append(
        "GENERATED AT: "
        +
        pretty_value(
            market.get(
                "generated_at"
            )
        )
    )

    lines.append(
        "SESSION ISOLATION: "
        +
        pretty_value(
            market.get(
                "session_isolation"
            )
        )
    )


    # --------------------------------------------------------
    # PREVIOUS SESSION
    # --------------------------------------------------------

    previous_session = (
        market.get(
            "previous_session"
        )
    )

    lines.append("")
    lines.append(
        "-" * 78
    )

    lines.append(
        "PREVIOUS TRADING SESSION REFERENCE"
    )

    lines.append(
        "-" * 78
    )

    if isinstance(
        previous_session,
        dict
    ):

        lines.append(
            "DATE: "
            +
            pretty_value(
                previous_session.get(
                    "date"
                )
            )
        )

        lines.append(
            "OPEN: "
            +
            pretty_value(
                previous_session.get(
                    "open"
                )
            )
        )

        lines.append(
            "HIGH: "
            +
            pretty_value(
                previous_session.get(
                    "high"
                )
            )
        )

        lines.append(
            "LOW: "
            +
            pretty_value(
                previous_session.get(
                    "low"
                )
            )
        )

        lines.append(
            "CLOSE: "
            +
            pretty_value(
                previous_session.get(
                    "close"
                )
            )
        )

        lines.append(
            "VOLUME: "
            +
            pretty_value(
                previous_session.get(
                    "volume"
                )
            )
        )

    else:

        lines.append(
            "UNAVAILABLE"
        )


    # --------------------------------------------------------
    # CURRENT SESSION + GAP
    # --------------------------------------------------------

    current_session = (
        market.get(
            "current_session"
        )
        or
        {}
    )

    lines.append("")
    lines.append(
        "-" * 78
    )

    lines.append(
        "CURRENT TRADING SESSION"
    )

    lines.append(
        "-" * 78
    )

    lines.append(
        "SESSION DATE: "
        +
        pretty_value(
            current_session.get(
                "session_date"
            )
        )
    )

    lines.append(
        "AVAILABLE: "
        +
        pretty_value(
            current_session.get(
                "available"
            )
        )
    )

    lines.append(
        "OPEN: "
        +
        pretty_value(
            current_session.get(
                "open"
            )
        )
    )

    lines.append(
        "HIGH: "
        +
        pretty_value(
            current_session.get(
                "high"
            )
        )
    )

    lines.append(
        "LOW: "
        +
        pretty_value(
            current_session.get(
                "low"
            )
        )
    )

    lines.append(
        "LAST PRICE: "
        +
        pretty_value(
            current_session.get(
                "last_price"
            )
        )
    )

    lines.append(
        "LATEST CANDLE TIME: "
        +
        pretty_value(
            current_session.get(
                "latest_candle_time"
            )
        )
    )

    gap = (
        current_session.get(
            "gap"
        )
        or
        {}
    )

    lines.append("")
    lines.append(
        "GAP ANALYSIS"
    )

    lines.append(
        "AVAILABLE: "
        +
        pretty_value(
            gap.get(
                "available"
            )
        )
    )

    lines.append(
        "TYPE: "
        +
        pretty_value(
            gap.get(
                "type"
            )
        )
    )

    lines.append(
        "PREVIOUS CLOSE: "
        +
        pretty_value(
            gap.get(
                "previous_close"
            )
        )
    )

    lines.append(
        "CURRENT OPEN: "
        +
        pretty_value(
            gap.get(
                "current_open"
            )
        )
    )

    lines.append(
        "GAP POINTS: "
        +
        pretty_value(
            gap.get(
                "points"
            )
        )
    )

    lines.append(
        "GAP PERCENT: "
        +
        pretty_value(
            gap.get(
                "percent"
            )
        )
    )


    # --------------------------------------------------------
    # ALL SIX REQUIRED TIMEFRAMES
    # --------------------------------------------------------

    timeframes = market.get(
        "timeframes",
        {}
    )

    if not isinstance(
        timeframes,
        dict
    ):

        timeframes = {}

    append_timeframe_section(
        lines,
        "1M",
        timeframes.get(
            "1M"
        )
    )

    append_timeframe_section(
        lines,
        "5M",
        timeframes.get(
            "5M"
        )
    )

    append_timeframe_section(
        lines,
        "15M",
        timeframes.get(
            "15M"
        )
    )

    append_timeframe_section(
        lines,
        "1H",
        timeframes.get(
            "1H"
        )
    )

    append_timeframe_section(
        lines,
        "1D",
        timeframes.get(
            "1D"
        )
    )

    append_timeframe_section(
        lines,
        "1W",
        timeframes.get(
            "1W"
        )
    )


    # --------------------------------------------------------
    # OPTION CHAIN
    # --------------------------------------------------------

    append_option_chain_section(
        lines,
        option_chain
    )


# ============================================================
# BUILD PHASE 2 READABLE DOCUMENT
#
# IMPORTANT:
# This endpoint is deliberately NOT minified JSON.
# It is labelled, separated, multiline UTF-8 text.
# ============================================================

def build_phase2_live_text():

    current = now_ist()

    status = market_status()

    nifty_state = (
        get_servable_instrument_state(
            "NIFTY"
        )
    )

    banknifty_state = (
        get_servable_instrument_state(
            "BANKNIFTY"
        )
    )

    lines = []

    lines.append(
        "PSYCHO MARKET BRIDGE — PHASE 2 LIVE"
    )

    lines.append(
        "=" * 78
    )

    lines.append(
        "BRIDGE STATUS: ONLINE"
    )

    lines.append(
        "DATA SOURCE: DHAN"
    )

    lines.append(
        "SERVER TIME: "
        +
        current.strftime(
            "%Y-%m-%d %H:%M:%S IST"
        )
    )

    lines.append(
        "MARKET STATUS: "
        +
        pretty_value(
            status.get(
                "status"
            )
        )
    )

    lines.append(
        "MARKET STATUS REASON: "
        +
        pretty_value(
            status.get(
                "reason"
            )
        )
    )

    lines.append(
        "LIVE COLLECTION WINDOW: "
        "09:15-15:40 IST"
    )

    lines.append(
        "LIVE REFRESH TARGET: "
        f"~{REFRESH_INTERVAL_SECONDS} SECONDS"
    )

    lines.append(
        "REQUIRED TIMEFRAMES: "
        "1M + 5M + 15M + 1H + 1D + 1W"
    )

    lines.append(
        "OPTION CHAIN: "
        "ATM +/- 10 STRIKES"
    )

    lines.append(
        "SESSION POLICY: "
        "CURRENT-DAY INTRADAY ONLY; "
        "NO PREVIOUS-DAY INTRADAY OVERLAP"
    )

    lines.append(
        "OVERNIGHT POLICY: "
        "LATEST COMPLETED/LAST SUCCESSFUL "
        "SESSION SNAPSHOT REMAINS AVAILABLE "
        "UNTIL NEXT TRADING SESSION"
    )

    append_instrument_section(
        lines,
        "NIFTY",
        nifty_state
    )

    append_instrument_section(
        lines,
        "BANKNIFTY",
        banknifty_state
    )

    lines.append("")
    lines.append("")
    lines.append(
        "=" * 78
    )

    lines.append(
        "END — PSYCHO MARKET BRIDGE "
        "PHASE 2 LIVE"
    )

    lines.append(
        "=" * 78
    )

    return "\n".join(
        lines
    )


# ============================================================
# ROOT
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

        "refresh_target_seconds":
            REFRESH_INTERVAL_SECONDS,

        "session_policy":
            (
                "Current-day intraday "
                "data only"
            ),

        "phase2_endpoint":
            "/phase2-live",

        "endpoints": [

            "/phase2-live",

            "/bridge-status",

            "/nifty-live",

            "/nifty-option-chain",

            "/banknifty-live",

            "/banknifty-option-chain"
        ]
    })


# ============================================================
# PHASE 2 LIVE
#
# PRIMARY CHATGPT / PHASE 2 ENDPOINT
# ============================================================

@app.route(
    "/phase2-live"
)
def phase2_live():

    try:

        text = (
            build_phase2_live_text()
        )

        return text_response(
            text,
            200
        )

    except Exception as error:

        error_text = (

            "PSYCHO MARKET BRIDGE — "
            "PHASE 2 LIVE\n\n"

            "STATUS: ERROR\n"

            "MESSAGE: "
            +
            str(
                error
            )
        )

        return text_response(
            error_text,
            500
        )


# ============================================================
# BRIDGE STATUS
# ============================================================

@app.route(
    "/bridge-status"
)
def bridge_status():

    current = now_ist()

    instrument_status = {}

    for (
        instrument_key,
        config
    ) in INSTRUMENTS.items():

        snapshot = read_last_snapshot(
            config
        )

        instrument_status[
            instrument_key
        ] = {

            "market_file_exists":
                os.path.exists(
                    config[
                        "market_file"
                    ]
                ),

            "option_file_exists":
                os.path.exists(
                    config[
                        "option_file"
                    ]
                ),

            "snapshot_file_exists":
                os.path.exists(
                    config[
                        "snapshot_file"
                    ]
                ),

            "snapshot_session_date":
                (
                    snapshot.get(
                        "session_date"
                    )
                    if
                    isinstance(
                        snapshot,
                        dict
                    )
                    else
                    None
                ),

            "snapshot_generated_at":
                (
                    snapshot.get(
                        "snapshot_generated_at"
                    )
                    if
                    isinstance(
                        snapshot,
                        dict
                    )
                    else
                    None
                )
        }

    return jsonify({

        "service":
            "PSYCHO MARKET BRIDGE",

        "server":
            "ONLINE",

        "source":
            "DHAN",

        "server_time":
            current.isoformat(),

        "market":
            market_status(),

        "refresh_target_seconds":
            REFRESH_INTERVAL_SECONDS,

        "instruments":
            instrument_status
    })


# ============================================================
# MACHINE JSON ENDPOINT HELPER
# ============================================================

def json_file_response(
    filename,
    waiting_message
):

    data = read_json_file(
        filename
    )

    if data is None:

        return jsonify({

            "status":
                "WAITING",

            "source":
                "DHAN",

            "message":
                waiting_message
        }), 503

    return jsonify(
        data
    )


# ============================================================
# NIFTY MARKET JSON
# ============================================================

@app.route(
    "/nifty-live"
)
def nifty_live():

    return json_file_response(

        INSTRUMENTS[
            "NIFTY"
        ][
            "market_file"
        ],

        (
            "NIFTY current/latest "
            "market dataset is not "
            "available yet"
        )
    )


# ============================================================
# NIFTY OPTION CHAIN JSON
# ============================================================

@app.route(
    "/nifty-option-chain"
)
def nifty_option_chain():

    return json_file_response(

        INSTRUMENTS[
            "NIFTY"
        ][
            "option_file"
        ],

        (
            "NIFTY current/latest "
            "option chain is not "
            "available yet"
        )
    )


# ============================================================
# BANK NIFTY MARKET JSON
# ============================================================

@app.route(
    "/banknifty-live"
)
def banknifty_live():

    return json_file_response(

        INSTRUMENTS[
            "BANKNIFTY"
        ][
            "market_file"
        ],

        (
            "BANK NIFTY current/latest "
            "market dataset is not "
            "available yet"
        )
    )


# ============================================================
# BANK NIFTY OPTION CHAIN JSON
# ============================================================

@app.route(
    "/banknifty-option-chain"
)
def banknifty_option_chain():

    return json_file_response(

        INSTRUMENTS[
            "BANKNIFTY"
        ][
            "option_file"
        ],

        (
            "BANK NIFTY current/latest "
            "option chain is not "
            "available yet"
        )
    )


# ============================================================
# START BACKGROUND WORKER
# ============================================================

def start_background_worker():

    worker = threading.Thread(

        target=
            live_refresh_worker,

        daemon=True,

        name=
            "psycho-live-refresh"
    )

    worker.start()

    return worker


# ============================================================
# STARTUP
# ============================================================

if __name__ == "__main__":

    print(
        "=" * 70,
        flush=True
    )

    print(
        "PSYCHO MARKET BRIDGE STARTING",
        flush=True
    )

    print(
        "SOURCE: DHAN",
        flush=True
    )

    print(
        "INSTRUMENTS: "
        "NIFTY + BANK NIFTY",
        flush=True
    )

    print(
        "TIMEFRAMES: "
        "1M + 5M + 15M + 1H + 1D + 1W",
        flush=True
    )

    print(
        "OPTION CHAIN: "
        "ATM +/- 10 STRIKES",
        flush=True
    )

    print(
        "LIVE WINDOW: "
        "09:15-15:40 IST",
        flush=True
    )

    print(
        "LIVE REFRESH TARGET: "
        f"~{REFRESH_INTERVAL_SECONDS} SECONDS",
        flush=True
    )

    print(
        "SESSION ISOLATION: ENABLED",
        flush=True
    )

    print(
        "PHASE 2 READABLE ENDPOINT: "
        "/phase2-live",
        flush=True
    )

    print(
        "=" * 70,
        flush=True
    )

    # --------------------------------------------------------
    # Start permanent market-session worker.
    # --------------------------------------------------------

    start_background_worker()


    # --------------------------------------------------------
    # Render supplies PORT.
    # Local fallback = 10000.
    # --------------------------------------------------------

    port = int(
        os.environ.get(
            "PORT",
            10000
        )
    )


    # --------------------------------------------------------
    # Flask stays in main thread.
    # --------------------------------------------------------

    app.run(

        host=
            "0.0.0.0",

        port=
            port,

        threaded=
            True,

        use_reloader=
            False
    )


# ============================================================
# END OF PART 3 / 3
# END OF COMPLETE BRIDGE.PY
# ============================================================
