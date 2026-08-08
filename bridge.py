import os
import csv
import io
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
# DATA LAYERS
# 1M / 5M / 15M / 1H / 1D / 1W underlying candles
# Underlying OHLCV (volume preserved when supplied by DHAN)
# Current nearest-expiry option chain
# Current nearest index-futures price + OI + volume
# Current futures market quote + 5-level depth snapshot
#
# CORE RULE
# Current-day intraday data never mixes with previous-day data.
# ============================================================

TOKEN = os.environ["DHAN_ACCESS_TOKEN"]
CLIENT_ID = os.environ["DHAN_CLIENT_ID"]

IST = ZoneInfo("Asia/Kolkata")

INTRADAY_URL = "https://api.dhan.co/v2/charts/intraday"
HISTORICAL_URL = "https://api.dhan.co/v2/charts/historical"
EXPIRY_LIST_URL = "https://api.dhan.co/v2/optionchain/expirylist"
OPTION_CHAIN_URL = "https://api.dhan.co/v2/optionchain"
MARKET_QUOTE_URL = "https://api.dhan.co/v2/marketfeed/quote"
INSTRUMENT_MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master-detailed.csv"

MARKET_OPEN = dt_time(9, 15)
MARKET_CLOSE = dt_time(15, 40)
REFRESH_INTERVAL_SECONDS = 60
OPTION_CHAIN_DELAY_SECONDS = 3.2

INSTRUMENTS = {
    "NIFTY": {
        "display_name": "NIFTY",
        "security_id": "13",
        "underlying_symbol": "NIFTY",
        "market_file": "nifty-live.json",
        "option_file": "nifty-option-chain.json",
        "snapshot_file": "nifty-session-snapshot.json",
        "futures_file": "nifty-futures-live.json"
    },
    "BANKNIFTY": {
        "display_name": "BANK NIFTY",
        "security_id": "25",
        "underlying_symbol": "BANKNIFTY",
        "market_file": "banknifty-live.json",
        "option_file": "banknifty-option-chain.json",
        "snapshot_file": "banknifty-session-snapshot.json",
        "futures_file": "banknifty-futures-live.json"
    }
}

LIMITS = {"1M": 400, "5M": 200, "15M": 120, "1H": 100, "1D": 120, "1W": 80}
OPTION_STRIKES_EACH_SIDE = 10
refresh_lock = threading.Lock()


def now_ist():
    return datetime.now(IST)


def iso_now():
    return now_ist().isoformat()


def is_weekday(value=None):
    value = value or now_ist()
    return value.weekday() < 5


def is_market_window(value=None):
    value = value or now_ist()
    return is_weekday(value) and MARKET_OPEN <= value.time() <= MARKET_CLOSE


def market_status():
    current = now_ist()
    if not is_weekday(current):
        reason = "WEEKEND"
    elif current.time() < MARKET_OPEN:
        reason = "PRE_MARKET"
    elif current.time() > MARKET_CLOSE:
        reason = "SESSION_FINISHED"
    else:
        return {"status": "OPEN", "reason": "LIVE_MARKET_WINDOW", "current_time": current.isoformat(), "market_open": "09:15 IST", "collection_end": "15:40 IST"}
    return {"status": "CLOSED", "reason": reason, "current_time": current.isoformat(), "market_open": "09:15 IST", "collection_end": "15:40 IST"}


def write_json_atomic(filename, data):
    tmp = filename + ".tmp"
    with open(tmp, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=2, ensure_ascii=False)
        file.flush()
        os.fsync(file.fileno())
    os.replace(tmp, filename)


def read_json_file(filename):
    if not os.path.exists(filename):
        return None
    try:
        with open(filename, "r", encoding="utf-8") as file:
            return json.load(file)
    except Exception as error:
        print(f"JSON READ ERROR {filename}: {error}", flush=True)
        return None


def dhan_request(url, payload, client_id_required=False):
    body = json.dumps(payload).encode("utf-8")
    headers = {"Accept": "application/json", "Content-Type": "application/json", "access-token": TOKEN}
    if client_id_required:
        headers["client-id"] = CLIENT_ID
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def safe_fetch(label, function):
    try:
        result = function()
        print(f"SUCCESS: {label}", flush=True)
        return result
    except Exception as error:
        print(f"ERROR: {label}: {error}", flush=True)
        return {"error": str(error), "label": label, "generated_at": iso_now()}


def normalize_candles(raw):
    if not isinstance(raw, dict):
        return []
    keys = ["timestamp", "open", "high", "low", "close", "volume"]
    arrays = {key: raw.get(key, []) or [] for key in keys}
    count = min(len(arrays[key]) for key in keys)
    candles = []
    for i in range(count):
        try:
            timestamp = int(arrays["timestamp"][i])
        except (TypeError, ValueError):
            continue
        candles.append({
            "timestamp": timestamp,
            "open": arrays["open"][i],
            "high": arrays["high"][i],
            "low": arrays["low"][i],
            "close": arrays["close"][i],
            "volume": arrays["volume"][i]
        })
    candles.sort(key=lambda x: x["timestamp"])
    return candles


def candle_datetime(candle):
    try:
        return datetime.fromtimestamp(int(candle["timestamp"]), IST)
    except Exception:
        return None


def filter_session_candles(candles, session_date):
    result = []
    for candle in candles or []:
        dt = candle_datetime(candle)
        if dt is None or dt.date() != session_date:
            continue
        if MARKET_OPEN <= dt.time() <= MARKET_CLOSE:
            result.append(candle)
    return sorted(result, key=lambda x: x["timestamp"])


def trim_candles(candles, timeframe):
    return (candles or [])[-LIMITS.get(timeframe, len(candles or [])):]


def fetch_intraday(security_id, interval, session_date=None, exchange_segment="IDX_I", instrument="INDEX", oi=False):
    current = now_ist()
    session_date = session_date or current.date()
    from_time = current - timedelta(days=10)
    payload = {
        "securityId": str(security_id),
        "exchangeSegment": exchange_segment,
        "instrument": instrument,
        "interval": str(interval),
        "oi": bool(oi),
        "fromDate": from_time.strftime("%Y-%m-%d 09:15:00"),
        "toDate": current.strftime("%Y-%m-%d %H:%M:%S")
    }
    raw = dhan_request(INTRADAY_URL, payload)
    return filter_session_candles(normalize_candles(raw), session_date)


def fetch_daily(security_id):
    current = now_ist()
    payload = {
        "securityId": str(security_id),
        "exchangeSegment": "IDX_I",
        "instrument": "INDEX",
        "expiryCode": 0,
        "oi": False,
        "fromDate": (current - timedelta(days=730)).strftime("%Y-%m-%d"),
        "toDate": (current + timedelta(days=1)).strftime("%Y-%m-%d")
    }
    return normalize_candles(dhan_request(HISTORICAL_URL, payload))


def candle_date(candle):
    dt = candle_datetime(candle)
    return dt.date() if dt else None


def previous_daily_candle(daily, session_date):
    candidates = [(candle_date(c), c) for c in daily if candle_date(c) and candle_date(c) < session_date]
    if not candidates:
        return None
    _, c = max(candidates, key=lambda x: x[0])
    return {"date": candle_date(c).isoformat(), "open": c.get("open"), "high": c.get("high"), "low": c.get("low"), "close": c.get("close"), "volume": c.get("volume"), "timestamp": c.get("timestamp")}


def build_current_daily(candles, session_date):
    session = filter_session_candles(candles, session_date)
    if not session:
        return None
    return {
        "timestamp": session[0]["timestamp"],
        "open": session[0].get("open"),
        "high": max(c["high"] for c in session if c.get("high") is not None),
        "low": min(c["low"] for c in session if c.get("low") is not None),
        "close": session[-1].get("close"),
        "volume": sum(c.get("volume") or 0 for c in session),
        "session_date": session_date.isoformat(),
        "developing": is_market_window()
    }


def merge_daily(history, current, session_date):
    result = [c for c in (history or []) if candle_date(c) != session_date]
    if current:
        result.append(current)
    return sorted(result, key=lambda x: x.get("timestamp", 0))


def aggregate_weekly(daily):
    weeks = {}
    for candle in daily or []:
        dt = candle_datetime(candle)
        if not dt:
            continue
        year, week, _ = dt.isocalendar()
        key = f"{year}-{week:02d}"
        if key not in weeks:
            weeks[key] = {"timestamp": candle.get("timestamp"), "week": key, "open": candle.get("open"), "high": candle.get("high"), "low": candle.get("low"), "close": candle.get("close"), "volume": candle.get("volume") or 0}
        else:
            w = weeks[key]
            w["high"] = max(w["high"], candle.get("high"))
            w["low"] = min(w["low"], candle.get("low"))
            w["close"] = candle.get("close")
            w["volume"] += candle.get("volume") or 0
    return sorted(weeks.values(), key=lambda x: x.get("timestamp", 0))


def calculate_gap(previous, current_open):
    if not previous or previous.get("close") is None or current_open is None:
        return {"available": False, "type": "UNAVAILABLE", "points": None, "percent": None}
    prev = float(previous["close"])
    op = float(current_open)
    points = op - prev
    return {"available": True, "type": "GAP_UP" if points > 0 else "GAP_DOWN" if points < 0 else "FLAT", "previous_close": prev, "current_open": op, "points": round(points, 2), "percent": round(points / prev * 100, 4) if prev else None}

# ============================================================
# FUTURES DISCOVERY
# Dhan's instrument master supplies Security IDs and expiry data.
# We select the nearest non-expired FUTIDX contract for each index.
# ============================================================

def discover_nearest_futures(underlying_symbol):
    request = urllib.request.Request(INSTRUMENT_MASTER_URL, headers={"User-Agent": "PSYCHO-MARKET-BRIDGE"})
    with urllib.request.urlopen(request, timeout=30) as response:
        text = response.read().decode("utf-8", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    today = now_ist().date()
    matches = []
    for row in reader:
        try:
            if row.get("EXCH_ID") != "NSE" or row.get("SEGMENT") != "D":
                continue
            if row.get("INSTRUMENT") != "FUTIDX":
                continue
            if (row.get("UNDERLYING_SYMBOL") or "").upper() != underlying_symbol.upper():
                continue
            expiry_raw = row.get("SM_EXPIRY_DATE") or ""
            expiry = datetime.strptime(expiry_raw[:10], "%Y-%m-%d").date()
            if expiry >= today:
                matches.append((expiry, row))
        except Exception:
            continue
    if not matches:
        raise RuntimeError(f"No active FUTIDX contract found for {underlying_symbol}")
    expiry, row = min(matches, key=lambda x: x[0])
    return {"security_id": str(row.get("SECURITY_ID") or row.get("SEM_SMST_SECURITY_ID")), "expiry": expiry.isoformat(), "trading_symbol": row.get("SEM_TRADING_SYMBOL") or row.get("DISPLAY_NAME") or row.get("SYMBOL_NAME"), "underlying_symbol": underlying_symbol}


def fetch_futures_data(contract, session_date):
    candles = fetch_intraday(contract["security_id"], 5, session_date, exchange_segment="NSE_FNO", instrument="FUTIDX", oi=True)
    latest = candles[-1] if candles else None
    return {"status": "LIVE" if latest else "UNAVAILABLE", "source": "DHAN", "generated_at": iso_now(), "security_id": contract["security_id"], "trading_symbol": contract.get("trading_symbol"), "expiry": contract.get("expiry"), "underlying_symbol": contract.get("underlying_symbol"), "timeframe": "5M", "latest": latest, "candles_5m": trim_candles(candles, "5M"), "oi_enabled": True}


def fetch_market_quote(security_id):
    payload = {"NSE_FNO": [int(security_id)]}
    return dhan_request(MARKET_QUOTE_URL, payload, client_id_required=True)


def clean_futures_quote(raw, security_id):
    data = (((raw or {}).get("data") or {}).get("NSE_FNO") or {}).get(str(security_id)) or {}
    depth = data.get("depth") or {}
    return {
        "security_id": security_id,
        "last_price": data.get("last_price"),
        "last_quantity": data.get("last_quantity"),
        "last_trade_time": data.get("last_trade_time"),
        "average_price": data.get("average_price"),
        "volume": data.get("volume"),
        "oi": data.get("oi"),
        "oi_day_high": data.get("oi_day_high"),
        "oi_day_low": data.get("oi_day_low"),
        "buy_quantity": data.get("buy_quantity"),
        "sell_quantity": data.get("sell_quantity"),
        "ohlc": data.get("ohlc"),
        "depth": {"buy": depth.get("buy", []), "sell": depth.get("sell", [])}
    }


def fetch_expiries(security_id):
    raw = dhan_request(EXPIRY_LIST_URL, {"UnderlyingScrip": int(security_id), "UnderlyingSeg": "IDX_I"}, client_id_required=True)
    expiries = raw.get("data", [])
    if not expiries:
        raise RuntimeError("No active option expiry returned by DHAN")
    return expiries


def clean_option_leg(leg):
    if not isinstance(leg, dict):
        return None
    oi = leg.get("oi")
    prev_oi = leg.get("previous_oi")
    try:
        oi_change = float(oi) - float(prev_oi) if oi is not None and prev_oi is not None else None
    except (TypeError, ValueError):
        oi_change = None
    return {
        "security_id": leg.get("security_id"),
        "last_price": leg.get("last_price"),
        "average_price": leg.get("average_price"),
        "oi": oi,
        "previous_oi": prev_oi,
        "oi_change": oi_change,
        "volume": leg.get("volume"),
        "previous_volume": leg.get("previous_volume"),
        "implied_volatility": leg.get("implied_volatility"),
        "top_bid_price": leg.get("top_bid_price"),
        "top_bid_quantity": leg.get("top_bid_quantity"),
        "top_ask_price": leg.get("top_ask_price"),
        "top_ask_quantity": leg.get("top_ask_quantity"),
        "greeks": leg.get("greeks") or {}
    }


def build_option_chain(security_id, session_date):
    expiries = fetch_expiries(security_id)
    expiry = expiries[0]
    time.sleep(OPTION_CHAIN_DELAY_SECONDS)
    raw = dhan_request(OPTION_CHAIN_URL, {"UnderlyingScrip": int(security_id), "UnderlyingSeg": "IDX_I", "Expiry": expiry}, client_id_required=True)
    data = raw.get("data") or {}
    underlying_ltp = data.get("last_price")
    oc = data.get("oc") or {}
    rows = []
    for strike, strike_data in oc.items():
        try:
            rows.append((float(strike), strike_data if isinstance(strike_data, dict) else {}))
        except (TypeError, ValueError):
            continue
    rows.sort(key=lambda x: x[0])
    if not rows:
        raise RuntimeError("No option strikes returned")
    try:
        atm_index = min(range(len(rows)), key=lambda i: abs(rows[i][0] - float(underlying_ltp))) if underlying_ltp is not None else len(rows) // 2
    except (TypeError, ValueError):
        atm_index = len(rows) // 2
    selected = rows[max(0, atm_index - OPTION_STRIKES_EACH_SIDE):min(len(rows), atm_index + OPTION_STRIKES_EACH_SIDE + 1)]
    strikes = {}
    for strike, strike_data in selected:
        strikes[str(strike)] = {"strike": strike, "CE": clean_option_leg(strike_data.get("ce")), "PE": clean_option_leg(strike_data.get("pe"))}
    return {"status": "LIVE", "source": "DHAN", "instrument_security_id": str(security_id), "session_date": session_date.isoformat(), "generated_at": iso_now(), "expiry": expiry, "underlying_ltp": underlying_ltp, "atm_strike": rows[atm_index][0], "strike_range": {"below_atm": OPTION_STRIKES_EACH_SIDE, "above_atm": OPTION_STRIKES_EACH_SIDE, "total_returned": len(selected)}, "strikes": strikes}


def build_instrument(key, config, session_date):
    name = config["display_name"]
    sid = config["security_id"]
    print(f"BUILDING {name} {session_date.isoformat()}", flush=True)

    candles_1m = safe_fetch(f"{name} 1M", lambda: fetch_intraday(sid, 1, session_date))
    candles_5m = safe_fetch(f"{name} 5M", lambda: fetch_intraday(sid, 5, session_date))
    candles_15m = safe_fetch(f"{name} 15M", lambda: fetch_intraday(sid, 15, session_date))
    candles_1h = safe_fetch(f"{name} 1H", lambda: fetch_intraday(sid, 60, session_date))
    daily_history = safe_fetch(f"{name} 1D", lambda: fetch_daily(sid))
    for variable_name in ["candles_1m", "candles_5m", "candles_15m", "candles_1h", "daily_history"]:
        if not isinstance(locals()[variable_name], list):
            locals()[variable_name] = []

    previous = previous_daily_candle(daily_history, session_date)
    current_daily = build_current_daily(candles_1m, session_date)
    daily = merge_daily(daily_history, current_daily, session_date)
    weekly = aggregate_weekly(daily)

    market_output = {
        "status": "LIVE",
        "source": "DHAN",
        "instrument": name,
        "security_id": sid,
        "session_date": session_date.isoformat(),
        "generated_at": iso_now(),
        "session_isolation": True,
        "previous_session": previous,
        "current_session": {"available": bool(candles_1m), "open": candles_1m[0].get("open") if candles_1m else None, "high": max([c.get("high") for c in candles_1m if c.get("high") is not None], default=None), "low": min([c.get("low") for c in candles_1m if c.get("low") is not None], default=None), "last_price": candles_1m[-1].get("close") if candles_1m else None, "latest_candle_time": candle_datetime(candles_1m[-1]).isoformat() if candles_1m and candle_datetime(candles_1m[-1]) else None, "gap": calculate_gap(previous, candles_1m[0].get("open") if candles_1m else None)},
        "timeframes": {"1M": trim_candles(candles_1m, "1M"), "5M": trim_candles(candles_5m, "5M"), "15M": trim_candles(candles_15m, "15M"), "1H": trim_candles(candles_1h, "1H"), "1D": trim_candles(daily, "1D"), "1W": trim_candles(weekly, "1W")},
        "volume_note": "Underlying index volume is preserved exactly as returned by DHAN; index volume may be zero/non-traded by instrument design. Futures volume is captured separately."
    }
    write_json_atomic(config["market_file"], market_output)

    # Nearest index future: price, 5M OHLCV and OI, plus live quote/depth.
    futures_output = {"status": "UNAVAILABLE", "source": "DHAN", "generated_at": iso_now()}
    try:
        contract = discover_nearest_futures(config["underlying_symbol"])
        futures_output = fetch_futures_data(contract, session_date)
        quote = safe_fetch(f"{name} FUTURES QUOTE/DEPTH", lambda: fetch_market_quote(contract["security_id"]))
        if isinstance(quote, dict) and "error" not in quote:
            futures_output["live_quote"] = clean_futures_quote(quote, contract["security_id"])
        else:
            futures_output["live_quote"] = {"status": "ERROR", "details": quote}
    except Exception as error:
        print(f"FUTURES ERROR {name}: {error}", flush=True)
        futures_output = {"status": "ERROR", "source": "DHAN", "generated_at": iso_now(), "message": str(error)}
    write_json_atomic(config["futures_file"], futures_output)

    option_output = safe_fetch(f"{name} OPTION CHAIN", lambda: build_option_chain(sid, session_date))
    if not isinstance(option_output, dict) or "error" in option_output:
        option_output = {"status": "ERROR", "source": "DHAN", "session_date": session_date.isoformat(), "generated_at": iso_now(), "details": option_output}
    write_json_atomic(config["option_file"], option_output)

    snapshot = {"status": "LIVE", "source": "DHAN", "instrument": name, "session_date": session_date.isoformat(), "snapshot_generated_at": iso_now(), "market": market_output, "futures": futures_output, "option_chain": option_output}
    write_json_atomic(config["snapshot_file"], snapshot)
    return snapshot


def refresh_all():
    if not refresh_lock.acquire(blocking=False):
        print("REFRESH SKIPPED: previous cycle still running", flush=True)
        return False
    try:
        session_date = now_ist().date()
        successful = 0
        for key, config in INSTRUMENTS.items():
            try:
                if isinstance(build_instrument(key, config, session_date), dict):
                    successful += 1
            except Exception as error:
                print(f"INSTRUMENT BUILD ERROR {key}: {error}", flush=True)
        print(f"REFRESH COMPLETE {successful}/{len(INSTRUMENTS)}", flush=True)
        return successful > 0
    finally:
        refresh_lock.release()


def live_refresh_worker():
    print("PSYCHO LIVE REFRESH WORKER STARTED", flush=True)
    last_state = None
    while True:
        try:
            current = now_ist()
            if is_market_window(current):
                if last_state != "OPEN":
                    print(f"MARKET WINDOW OPEN {current.isoformat()}", flush=True)
                    last_state = "OPEN"
                started = time.monotonic()
                refresh_all()
                duration = time.monotonic() - started
                time.sleep(max(1, REFRESH_INTERVAL_SECONDS - duration))
            else:
                if last_state != "CLOSED":
                    print(f"MARKET WINDOW CLOSED {current.isoformat()}", flush=True)
                    last_state = "CLOSED"
                time.sleep(30)
        except Exception as error:
            print(f"BACKGROUND WORKER ERROR: {error}", flush=True)
            time.sleep(10)


def get_servable_state(key):
    config = INSTRUMENTS[key]
    snapshot = read_json_file(config["snapshot_file"])
    if snapshot:
        return snapshot
    return {"status": "WAITING", "source": "DHAN", "instrument": config["display_name"], "market": read_json_file(config["market_file"]), "futures": read_json_file(config["futures_file"]), "option_chain": read_json_file(config["option_file"])}


def pretty_value(value):
    return "N/A" if value is None else str(value)


def readable_candle_time(candle):
    dt = candle_datetime(candle)
    return dt.strftime("%Y-%m-%d %H:%M:%S IST") if dt else "N/A"


def build_phase2_live_text():
    lines = [
        "PSYCHO MARKET BRIDGE — PHASE 2 LIVE",
        "=" * 78,
        "BRIDGE STATUS: ONLINE",
        "DATA SOURCE: DHAN",
        f"SERVER TIME: {now_ist().strftime('%Y-%m-%d %H:%M:%S IST')}",
        f"MARKET STATUS: {market_status()['status']}",
        "LIVE COLLECTION WINDOW: 09:15-15:40 IST",
        f"LIVE REFRESH TARGET: ~{REFRESH_INTERVAL_SECONDS} SECONDS",
        "UNDERLYING TIMEFRAMES: 1M + 5M + 15M + 1H + 1D + 1W",
        "DERIVATIVES: NEAREST INDEX FUTURE + 5M OHLCV/OI + LIVE QUOTE/5-LEVEL DEPTH",
        "OPTIONS: NEAREST EXPIRY + ATM +/- 10 STRIKES",
        "SESSION POLICY: CURRENT-DAY INTRADAY ONLY"
    ]
    for key in INSTRUMENTS:
        state = get_servable_state(key)
        lines += ["", "#" * 78, INSTRUMENTS[key]["display_name"], "#" * 78]
        lines.append(f"SNAPSHOT STATUS: {state.get('status')}")
        market = state.get("market") or {}
        lines.append(f"SESSION DATE: {market.get('session_date', state.get('session_date'))}")
        lines.append(f"GENERATED AT: {market.get('generated_at')}")
        current = market.get("current_session") or {}
        lines.append(f"LAST PRICE: {pretty_value(current.get('last_price'))}")
        lines.append(f"GAP: {pretty_value((current.get('gap') or {}).get('type'))} {pretty_value((current.get('gap') or {}).get('points'))}")
        tfs = market.get("timeframes") or {}
        for tf in ["1M", "5M", "15M", "1H", "1D", "1W"]:
            candles = tfs.get(tf) or []
            lines.append(f"{tf}: {len(candles)} candles | latest={readable_candle_time(candles[-1]) if candles else 'N/A'}")
        futures = state.get("futures") or {}
        lines += ["", "FUTURES", f"STATUS: {futures.get('status')}", f"SYMBOL: {futures.get('trading_symbol')}", f"EXPIRY: {futures.get('expiry')}", f"SECURITY ID: {futures.get('security_id')}"]
        latest = futures.get("latest") or {}
        lines.append(f"5M FUTURES LATEST: {readable_candle_time(latest)} | C={pretty_value(latest.get('close'))} | V={pretty_value(latest.get('volume'))} | OI={pretty_value(latest.get('oi'))}")
        quote = futures.get("live_quote") or {}
        lines.append(f"LIVE FUTURES QUOTE: LTP={pretty_value(quote.get('last_price'))} | VOL={pretty_value(quote.get('volume'))} | OI={pretty_value(quote.get('oi'))} | BUYQ={pretty_value(quote.get('buy_quantity'))} | SELLQ={pretty_value(quote.get('sell_quantity'))}")
        depth = quote.get("depth") or {}
        if depth:
            lines.append(f"DEPTH LEVELS: BUY={len(depth.get('buy', []))} | SELL={len(depth.get('sell', []))}")
        options = state.get("option_chain") or {}
        lines += ["", "OPTION CHAIN", f"STATUS: {options.get('status')}", f"EXPIRY: {options.get('expiry')}", f"UNDERLYING LTP: {options.get('underlying_ltp')}", f"ATM: {options.get('atm_strike')}", f"STRIKES: {len(options.get('strikes') or {})}"]
    lines += ["", "=" * 78, "END — PSYCHO MARKET BRIDGE PHASE 2 LIVE", "=" * 78]
    return "\n".join(lines)


app = Flask(__name__)


def text_response(text, status=200):
    return Response(text, status=status, content_type="text/plain; charset=utf-8")


@app.route("/")
def home():
    return jsonify({"service": "PSYCHO MARKET BRIDGE", "status": "ONLINE", "source": "DHAN", "timezone": "Asia/Kolkata", "market_window": "09:15-15:40 IST", "refresh_target_seconds": REFRESH_INTERVAL_SECONDS, "phase2_endpoint": "/phase2-live", "endpoints": ["/phase2-live", "/bridge-status", "/nifty-live", "/nifty-option-chain", "/nifty-futures-live", "/banknifty-live", "/banknifty-option-chain", "/banknifty-futures-live"]})


@app.route("/phase2-live")
def phase2_live():
    try:
        return text_response(build_phase2_live_text())
    except Exception as error:
        return text_response(f"PSYCHO MARKET BRIDGE — PHASE 2 LIVE\n\nSTATUS: ERROR\nMESSAGE: {error}", 500)


@app.route("/bridge-status")
def bridge_status():
    status = {}
    for key, config in INSTRUMENTS.items():
        snap = read_json_file(config["snapshot_file"])
        status[key] = {"market_file_exists": os.path.exists(config["market_file"]), "option_file_exists": os.path.exists(config["option_file"]), "futures_file_exists": os.path.exists(config["futures_file"]), "snapshot_file_exists": os.path.exists(config["snapshot_file"]), "session_date": snap.get("session_date") if isinstance(snap, dict) else None, "snapshot_generated_at": snap.get("snapshot_generated_at") if isinstance(snap, dict) else None}
    return jsonify({"service": "PSYCHO MARKET BRIDGE", "server": "ONLINE", "source": "DHAN", "server_time": iso_now(), "market": market_status(), "refresh_target_seconds": REFRESH_INTERVAL_SECONDS, "instruments": status})


def file_json(filename, waiting):
    data = read_json_file(filename)
    return (jsonify(data), 200) if data is not None else (jsonify({"status": "WAITING", "source": "DHAN", "message": waiting}), 503)


@app.route("/nifty-live")
def nifty_live(): return file_json(INSTRUMENTS["NIFTY"]["market_file"], "NIFTY market data unavailable")

@app.route("/nifty-option-chain")
def nifty_option_chain(): return file_json(INSTRUMENTS["NIFTY"]["option_file"], "NIFTY option chain unavailable")

@app.route("/nifty-futures-live")
def nifty_futures_live(): return file_json(INSTRUMENTS["NIFTY"]["futures_file"], "NIFTY futures data unavailable")

@app.route("/banknifty-live")
def banknifty_live(): return file_json(INSTRUMENTS["BANKNIFTY"]["market_file"], "BANK NIFTY market data unavailable")

@app.route("/banknifty-option-chain")
def banknifty_option_chain(): return file_json(INSTRUMENTS["BANKNIFTY"]["option_file"], "BANK NIFTY option chain unavailable")

@app.route("/banknifty-futures-live")
def banknifty_futures_live(): return file_json(INSTRUMENTS["BANKNIFTY"]["futures_file"], "BANK NIFTY futures data unavailable")


if __name__ == "__main__":
    print("PSYCHO MARKET BRIDGE STARTING", flush=True)
    print("SOURCE: DHAN", flush=True)
    print("DATA: 1M/5M/15M/1H/1D/1W + VOLUME + FUTURES OI + OPTION CHAIN + DEPTH", flush=True)
    print("LIVE WINDOW: 09:15-15:40 IST", flush=True)
    threading.Thread(target=live_refresh_worker, daemon=True, name="psycho-live-refresh").start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, threaded=True, use_reloader=False)
