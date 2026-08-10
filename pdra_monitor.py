import json
import math
import urllib.request
from datetime import datetime, time as dt_time, timedelta
from zoneinfo import ZoneInfo
from flask import Flask, jsonify, render_template_string

app = Flask(__name__)
IST = ZoneInfo("Asia/Kolkata")
BRIDGE = "https://psycho-market-bridge.onrender.com"

MARKET_OPEN = dt_time(9, 15)
ELIGIBILITY = dt_time(9, 30)
MARKET_CLOSE = dt_time(15, 40)

ATR_LENGTH = 14
DISPLACEMENT_ATR = 0.35
MIN_BODY_ATR = 0.45
VOLUME_MULTIPLIER = 1.15
TARGET_DELTA = 0.50
MIN_DELTA = 0.35
MAX_DELTA = 0.65
MAX_SPREAD_PCT = 0.020

# Signal state is intentionally separate from the live scanner.
# The feed keeps updating, but an issued signal is frozen for this process/day.
# Durable cross-restart storage should be added before production use.
SIGNAL_STATE = {
    "NIFTY": {"session_date": None, "locked": False, "signal": None},
    "BANK NIFTY": {"session_date": None, "locked": False, "signal": None},
}

HTML = '''<!doctype html>
<html>
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="15">
<title>PSYCHO PDRA — Signal Engine</title>
<style>
body{font-family:Arial,sans-serif;background:#080b10;color:#eef2f7;margin:0;padding:20px}
.wrap{max-width:1200px;margin:auto}.top{display:flex;justify-content:space-between;gap:15px;align-items:center;margin-bottom:18px}
h1{margin:0;font-size:27px}.sub{color:#8f9aaa;font-size:13px;margin-top:5px}
.banner,.card{background:#11161d;border:1px solid #29313c;border-radius:14px;padding:18px}
.banner{margin-bottom:18px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:18px}
@media(max-width:850px){.grid{grid-template-columns:1fr}}
.title{font-size:22px;font-weight:800}.time{color:#8f9aaa;font-size:12px;margin:5px 0 14px}
.row{display:flex;justify-content:space-between;gap:15px;padding:7px 0;border-bottom:1px solid #202731}
.label{color:#9da8b7}.value{font-weight:700;text-align:right}
.status{font-size:20px;font-weight:900;padding:14px;border-radius:10px;margin:15px 0;background:#1b222b}
.green{color:#59e3a1}.red{color:#ff7272}.yellow{color:#ffd166}.blue{color:#71b7ff}
.lock{border:1px solid #59e3a1;background:#0d2118;padding:15px;border-radius:11px;margin-top:15px}
.lock h3{margin:0 0 8px}.small{font-size:12px;color:#8994a3;margin-top:14px;line-height:1.45}
.exec{margin-top:16px;padding:15px;border:1px solid #303a47;border-radius:11px;background:#0c1117}
.exec-title{font-size:17px;font-weight:800;margin-bottom:8px}
</style></head>
<body><div class="wrap">
<div class="top"><div><h1>PSYCHO PDRA — SIGNAL ENGINE</h1>
<div class="sub">PDH/PDL → displacement → acceptance → locked option signal</div></div>
<div class="sub">AUTO REFRESH 15s</div></div>
<div class="banner"><b>{{session.status}}</b><div class="sub">{{session.detail}}</div></div>
<div class="grid">{% for x in instruments %}
<div class="card"><div class="title">{{x.name}}</div><div class="time">{{x.generated}}</div>
<div class="row"><span class="label">Session</span><span class="value">{{x.session_date}}</span></div>
<div class="row"><span class="label">PDH</span><span class="value">{{x.pdh}}</span></div>
<div class="row"><span class="label">PDL</span><span class="value">{{x.pdl}}</span></div>
<div class="row"><span class="label">Today's Open</span><span class="value">{{x.open}}</span></div>
<div class="row"><span class="label">Underlying LTP</span><span class="value">{{x.underlying_ltp}}</span></div>
<div class="row"><span class="label">ATR(14) 5M</span><span class="value">{{x.atr}}</span></div>
<div class="row"><span class="label">Displacement threshold</span><span class="value">{{x.threshold}}</span></div>
<div class="status {{x.status_class}}">{{x.status}}</div>
<div class="row"><span class="label">Displacement</span><span class="value">{{x.displacement}}</span></div>
<div class="row"><span class="label">Acceptance</span><span class="value">{{x.acceptance}}</span></div>
<div class="row"><span class="label">Direction</span><span class="value">{{x.direction}}</span></div>
{% if x.signal_locked %}
<div class="lock"><h3>🔒 SIGNAL LOCKED</h3>
<div class="row"><span class="label">Signal ID</span><span class="value">{{x.signal_id}}</span></div>
<div class="row"><span class="label">Signal time</span><span class="value">{{x.signal_time}}</span></div>
<div class="row"><span class="label">Direction</span><span class="value">{{x.locked_direction}}</span></div>
<div class="row"><span class="label">Underlying entry</span><span class="value">{{x.under_entry}}</span></div>
<div class="row"><span class="label">Underlying SL</span><span class="value">{{x.under_sl}}</span></div>
<div class="row"><span class="label">Underlying TP</span><span class="value">{{x.under_tp}}</span></div>
<div class="row"><span class="label">Signal state</span><span class="value">{{x.trade_state}}</span></div>
<div class="exec"><div class="exec-title">LOCKED OPTION</div>
<div class="row"><span class="label">Selected</span><span class="value">{{x.option}}</span></div>
<div class="row"><span class="label">Expiry</span><span class="value">{{x.expiry}}</span></div>
<div class="row"><span class="label">Entry snapshot</span><span class="value">{{x.option_entry}}</span></div>
<div class="row"><span class="label">Delta snapshot</span><span class="value">{{x.delta}}</span></div>
<div class="row"><span class="label">Spread snapshot</span><span class="value">{{x.spread}}</span></div>
<div class="row"><span class="label">Premium SL</span><span class="value">{{x.option_sl}}</span></div>
<div class="row"><span class="label">Premium TP</span><span class="value">{{x.option_tp}}</span></div>
</div></div>
{% else %}
<div class="exec"><div class="exec-title">OPTION SCAN</div>
<div class="row"><span class="label">Candidate</span><span class="value">{{x.option}}</span></div>
<div class="row"><span class="label">Option state</span><span class="value">{{x.option_status}}</span></div></div>
{% endif %}
<div class="small">Research/paper-trading only. No order is placed. A locked signal is not rewritten when the live market moves; management state may change to SL/TP while the original signal payload remains frozen.</div>
</div>{% endfor %}</div>
</div></body></html>'''

def get_json(path):
    req = urllib.request.Request(f"{BRIDGE}{path}", headers={"Accept":"application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))

def flatten(obj):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from flatten(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from flatten(v)

def parse_dt(ts):
    if ts is None:
        return None
    try:
        if isinstance(ts, (int, float)) or (isinstance(ts, str) and ts.isdigit()):
            return datetime.fromtimestamp(int(ts), IST)
        return datetime.fromisoformat(str(ts).replace("Z","+00:00")).astimezone(IST)
    except Exception:
        return None

def candles(obj):
    out = []
    for x in flatten(obj):
        if not isinstance(x, dict) or not all(k in x for k in ("open","high","low","close")):
            continue
        dt = parse_dt(x.get("timestamp") or x.get("time") or x.get("datetime"))
        if dt is None:
            continue
        try:
            out.append({
                "dt": dt.replace(second=0, microsecond=0),
                "open": float(x["open"]), "high": float(x["high"]),
                "low": float(x["low"]), "close": float(x["close"]),
                "volume": float(x.get("volume") or x.get("vol") or 0),
            })
        except Exception:
            continue
    dedup = {}
    for c in out:
        dedup[c["dt"]] = c
    return sorted(dedup.values(), key=lambda x:x["dt"])

def completed_5m(cs, now):
    cutoff = now.replace(second=0, microsecond=0)
    return [c for c in cs if c["dt"].minute % 5 == 0 and c["dt"] + timedelta(minutes=5) <= cutoff]

def atr(cs):
    if len(cs) < ATR_LENGTH + 1:
        return None
    trs = []
    prev = None
    for c in cs:
        tr = c["high"]-c["low"] if prev is None else max(
            c["high"]-c["low"], abs(c["high"]-prev), abs(c["low"]-prev)
        )
        trs.append(tr)
        prev = c["close"]
    return sum(trs[-ATR_LENGTH:]) / ATR_LENGTH

def median(values):
    vals = sorted(v for v in values if v is not None and v > 0)
    if not vals:
        return None
    n = len(vals)
    return vals[n//2] if n % 2 else (vals[n//2-1] + vals[n//2]) / 2

def num(v):
    try:
        return float(v)
    except Exception:
        return None

def fmt(v):
    if v is None:
        return "—"
    try:
        return f"{float(v):,.2f}"
    except Exception:
        return str(v)

def session_state(now):
    if now.weekday() >= 5:
        return {"status":"MARKET CLOSED — WEEKEND","detail":"No signal is generated outside a trading session."}
    if now.time() < MARKET_OPEN:
        return {"status":"MARKET CLOSED — PRE-OPEN","detail":"Waiting for 09:15 IST."}
    if now.time() < ELIGIBILITY:
        return {"status":"MARKET OPEN — BUILDING","detail":"Opening structure is collected. PDRA signal eligibility begins at 09:30 IST."}
    if now.time() > MARKET_CLOSE:
        return {"status":"MARKET CLOSED — SESSION COMPLETE","detail":"Today's signal state is retained only for this running engine instance."}
    return {"status":"MARKET OPEN — PDRA ACTIVE","detail":"Scanning completed 5M structure. A qualified signal is locked once issued."}

def blank(name, now, status):
    return {
        "name":name, "generated":now.strftime("%d %b %Y %H:%M:%S IST"),
        "session_date":now.date().isoformat(), "pdh":"—","pdl":"—","open":"—",
        "underlying_ltp":"—","atr":"—","threshold":"—","long_trigger":"—","short_trigger":"—",
        "status":status,"status_class":"blue","displacement":"NOT ACTIVE","acceptance":"NOT ACTIVE",
        "direction":"—","option":"—","expiry":"—","option_entry":"—","delta":"—",
        "spread":"—","oi":"—","volume":"—","option_sl":"—","option_tp":"—",
        "option_status":"NOT ACTIVE","signal_locked":False,"signal_id":"—",
        "signal_time":"—","locked_direction":"—","under_entry":"—","under_sl":"—",
        "under_tp":"—","trade_state":"—"
    }

def choose_option(chain, direction):
    wanted = "CE" if direction == "LONG" else "PE"
    rows = []
    for data in (chain.get("strikes") or {}).values():
        if not isinstance(data, dict):
            continue
        strike = num(data.get("strike"))
        leg = data.get(wanted) or {}
        if strike is None or not isinstance(leg, dict):
            continue
        ltp = num(leg.get("last_price"))
        bid = num(leg.get("top_bid_price"))
        ask = num(leg.get("top_ask_price"))
        oi = num(leg.get("oi"))
        vol = num(leg.get("volume"))
        delta = num((leg.get("greeks") or {}).get("delta"))
        if ltp is None or ltp <= 0 or delta is None:
            continue
        if not MIN_DELTA <= abs(delta) <= MAX_DELTA:
            continue
        if bid is None or ask is None or ask < bid or ask <= 0:
            continue
        spread = ask - bid
        mid = (ask + bid) / 2
        spread_pct = spread / mid if mid > 0 else 1
        if spread_pct > MAX_SPREAD_PCT:
            continue
        liquidity = math.log1p(max(oi or 0,0)) + math.log1p(max(vol or 0,0))
        delta_score = 1 - abs(abs(delta)-TARGET_DELTA)/TARGET_DELTA
        spread_score = max(0, 1-spread_pct/MAX_SPREAD_PCT)
        liquidity_score = min(liquidity/25, 1)
        score = 0.50*delta_score + 0.30*spread_score + 0.20*liquidity_score
        rows.append({
            "strike":strike,"side":wanted,"ltp":ltp,"delta":delta,
            "oi":oi,"volume":vol,"spread":spread,"score":score
        })
    return max(rows, key=lambda x:x["score"]) if rows else None

def projected_premium(opt, entry, sl, tp):
    d = opt.get("delta")
    if d is None:
        return None, None
    return (
        max(0.05, opt["ltp"] + d*(sl-entry)),
        max(0.05, opt["ltp"] + d*(tp-entry))
    )

def reset_state_if_new_day(name, now):
    state = SIGNAL_STATE[name]
    today = now.date().isoformat()
    if state["session_date"] != today:
        state.clear()
        state.update({"session_date":today,"locked":False,"signal":None})
    return state

def manage_locked(state, now, current_price):
    sig = state["signal"]
    if not sig or sig["trade_state"] in ("TP HIT","SL HIT","INVALIDATED","SESSION CLOSED"):
        return
    if now.time() > MARKET_CLOSE:
        sig["trade_state"] = "SESSION CLOSED"
        return
    if current_price is None:
        return
    if sig["direction"] == "LONG":
        if current_price <= sig["under_sl"]:
            sig["trade_state"] = "SL HIT"
        elif current_price >= sig["under_tp"]:
            sig["trade_state"] = "TP HIT"
        else:
            sig["trade_state"] = "ACTIVE"
    else:
        if current_price >= sig["under_sl"]:
            sig["trade_state"] = "SL HIT"
        elif current_price <= sig["under_tp"]:
            sig["trade_state"] = "TP HIT"
        else:
            sig["trade_state"] = "ACTIVE"

def locked_output(out, sig):
    out.update({
        "signal_locked":True,"signal_id":sig["signal_id"],"signal_time":sig["signal_time"],
        "locked_direction":sig["direction"],"under_entry":fmt(sig["under_entry"]),
        "under_sl":fmt(sig["under_sl"]),"under_tp":fmt(sig["under_tp"]),
        "trade_state":sig["trade_state"],"direction":sig["direction"],
        "status":f"🔒 SIGNAL LOCKED — {sig['direction']}","status_class":"green",
        "displacement":"CONFIRMED","acceptance":"CONFIRMED",
        "option":sig["option"],"expiry":sig["expiry"],
        "option_entry":fmt(sig["option_entry"]),"delta":fmt(sig["delta"]),
        "spread":fmt(sig["spread"]),"oi":fmt(sig["oi"]),"volume":fmt(sig["volume"]),
        "option_sl":fmt(sig["option_sl"]),"option_tp":fmt(sig["option_tp"]),
        "option_status":"🔒 FROZEN AT SIGNAL"
    })
    if sig["trade_state"] == "TP HIT":
        out["status"] = "🔒 SIGNAL LOCKED — TP HIT"
    elif sig["trade_state"] == "SL HIT":
        out["status"] = "🔒 SIGNAL LOCKED — SL HIT"
        out["status_class"] = "red"
    elif sig["trade_state"] == "SESSION CLOSED":
        out["status"] = "🔒 SIGNAL LOCKED — SESSION CLOSED"
        out["status_class"] = "blue"
    return out

def analyse(name, path, opt_path, now):
    state = reset_state_if_new_day(name, now)
    try:
        cs = candles(get_json(path))
    except Exception:
        return blank(name, now, "BRIDGE UNAVAILABLE")

    completed_all = completed_5m(cs, now)
    sessions = {}
    for c in completed_all:
        sessions.setdefault(c["dt"].date(), []).append(c)

    today = sessions.get(now.date(), [])
    prior_dates = [d for d in sessions if d < now.date()]
    prev = sessions.get(max(prior_dates), []) if prior_dates else []
    pdh = max((c["high"] for c in prev), default=None)
    pdl = min((c["low"] for c in prev), default=None)
    op = today[0]["open"] if today else None
    current_price = today[-1]["close"] if today else None
    a = atr(completed_all)
    threshold = a * DISPLACEMENT_ATR if a is not None else None

    out = blank(name, now, "WAITING")
    out.update({
        "session_date":now.date().isoformat(),"pdh":fmt(pdh),"pdl":fmt(pdl),
        "open":fmt(op),"underlying_ltp":fmt(current_price),"atr":fmt(a),
        "threshold":fmt(threshold),
        "long_trigger":fmt(pdh+threshold if pdh is not None and threshold is not None else None),
        "short_trigger":fmt(pdl-threshold if pdl is not None and threshold is not None else None),
    })

    if now.weekday() >= 5:
        return blank(name, now, "MARKET CLOSED — WEEKEND")
    if now.time() < MARKET_OPEN:
        return blank(name, now, "MARKET CLOSED — PRE-OPEN")
    if now.time() > MARKET_CLOSE:
        if state["locked"] and state["signal"]:
            state["signal"]["trade_state"] = "SESSION CLOSED"
            return locked_output(out, state["signal"])
        return blank(name, now, "MARKET CLOSED — SESSION COMPLETE")
    if now.time() < ELIGIBILITY:
        out["status"] = "PDRA BUILDING — SIGNALS START 09:30"
        return out

    # Once locked, NEVER rescan or reselect the option.
    if state["locked"] and state["signal"]:
        manage_locked(state, now, current_price)
        return locked_output(out, state["signal"])

    if not today or pdh is None or pdl is None or a is None or threshold is None:
        out["status"] = "DATA UNAVAILABLE"
        out["status_class"] = "red"
        return out

    if not pdl < op < pdh:
        out["status"] = "NO TRADE — OPEN OUTSIDE PD RANGE"
        out["status_class"] = "red"
        return out

    trigger_idx = None
    direction = None
    vol_base = median([c["volume"] for c in today[:-1][-20:]]) or 0

    for i, c in enumerate(today):
        if c["dt"].time() < ELIGIBILITY:
            continue
        body_atr = abs(c["close"]-c["open"]) / a if a else 0
        volume_ok = True if vol_base <= 0 else c["volume"] >= vol_base*VOLUME_MULTIPLIER
        if c["close"] >= pdh + threshold and body_atr >= MIN_BODY_ATR and volume_ok:
            trigger_idx, direction = i, "LONG"
            break
        if c["close"] <= pdl - threshold and body_atr >= MIN_BODY_ATR and volume_ok:
            trigger_idx, direction = i, "SHORT"
            break

    if trigger_idx is None:
        out["status"] = "WAITING — NO QUALIFIED DISPLACEMENT"
        return out

    out["displacement"] = "CONFIRMED"
    out["direction"] = direction

    if trigger_idx + 1 >= len(today):
        out["status"] = "DISPLACEMENT CONFIRMED — ACCEPTANCE WAITING"
        return out

    confirm = today[trigger_idx+1]
    accepted = confirm["close"] > pdh if direction == "LONG" else confirm["close"] < pdl
    if not accepted:
        out["status"] = "SETUP REJECTED — ACCEPTANCE FAILED"
        out["status_class"] = "red"
        out["acceptance"] = "FAILED"
        return out

    out["acceptance"] = "CONFIRMED"

    # Signal is issued immediately after the acceptance candle closes.
    # Entry uses that close, not a future candle open, removing look-ahead bias.
    entry_candle = confirm
    entry = confirm["close"]
    sl = pdh if direction == "LONG" else pdl
    risk = abs(entry-sl)
    if risk <= 0:
        out["status"] = "SETUP REJECTED — ZERO RISK DISTANCE"
        out["status_class"] = "red"
        return out
    tp = entry+risk if direction == "LONG" else entry-risk

    try:
        chain = get_json(opt_path)
        if chain.get("status") != "LIVE":
            out["status"] = "UNDERLYING SIGNAL READY — OPTION DATA UNAVAILABLE"
            out["option_status"] = "OPTION CHAIN UNAVAILABLE"
            return out
        opt = choose_option(chain, direction)
        if not opt:
            out["status"] = "UNDERLYING SIGNAL READY — NO LIQUID OPTION"
            out["option_status"] = "NO OPTION PASSED LIQUIDITY FILTER"
            return out

        osl, otp = projected_premium(opt, entry, sl, tp)
        signal_time = (entry_candle["dt"] + timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S IST")
        signal_id = f"PDRA-{name.replace(' ','')}-{now.date().strftime('%Y%m%d')}-{entry_candle['dt'].strftime('%H%M')}"
        sig = {
            "signal_id":signal_id,"signal_time":signal_time,"direction":direction,
            "under_entry":entry,"under_sl":sl,"under_tp":tp,
            "option":f"{int(opt['strike']):,} {opt['side']}",
            "expiry":chain.get("expiry") or "—","option_entry":opt["ltp"],
            "delta":opt["delta"],"spread":opt["spread"],"oi":opt["oi"],
            "volume":opt["volume"],"option_sl":osl,"option_tp":otp,
            "trade_state":"ACTIVE"
        }
        state["locked"] = True
        state["signal"] = sig
        return locked_output(out, sig)
    except Exception:
        out["status"] = "UNDERLYING SIGNAL READY — OPTION DATA ERROR"
        out["option_status"] = "OPTION CHAIN ERROR"
        return out

@app.route("/")
def home():
    now = datetime.now(IST)
    session = session_state(now)
    items = [
        analyse("NIFTY","/nifty-live","/nifty-option-chain",now),
        analyse("BANK NIFTY","/banknifty-live","/banknifty-option-chain",now),
    ]
    return render_template_string(HTML, instruments=items, session=session)

@app.route("/signal")
def signal_api():
    now = datetime.now(IST)
    result = {
        "NIFTY":analyse("NIFTY","/nifty-live","/nifty-option-chain",now),
        "BANK NIFTY":analyse("BANK NIFTY","/banknifty-live","/banknifty-option-chain",now),
    }
    return jsonify({
        "service":"PSYCHO PDRA SIGNAL ENGINE","generated_at":now.isoformat(),
        "market":session_state(now),"signals":result,
        "policy":{
            "signal_lock":"ONE LOCKED SIGNAL PER INSTRUMENT PER SESSION",
            "option_reselection_after_lock":False,
            "current_market_data_continues_refreshing":True,
            "research_only":True
        }
    })

@app.route("/health")
def health():
    return jsonify({
        "status":"ok","service":"pdra-monitor",
        "market":session_state(datetime.now(IST)),
        "locks":{k:v["locked"] for k,v in SIGNAL_STATE.items()}
    })

if __name__ == "__main__":
    import os
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT","10000")))
