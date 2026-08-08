import json
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
DISPLACEMENT_ATR = 0.20
HOLD_BARS = 12

HTML = r'''<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>PSYCHO PDRA Monitor</title>
<style>
body{font-family:Arial,sans-serif;background:#0b0d10;color:#f3f4f6;margin:0;padding:24px}.wrap{max-width:1100px;margin:auto}
h1{margin:0 0 6px;font-size:28px}.sub{color:#9ca3af;margin-bottom:22px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:18px}@media(max-width:800px){.grid{grid-template-columns:1fr}}
.card{background:#14181d;border:1px solid #2a3038;border-radius:14px;padding:20px}.title{font-size:20px;font-weight:700;margin-bottom:4px}.time{color:#9ca3af;font-size:13px;margin-bottom:16px}
.row{display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid #222831}.label{color:#aeb6c2}.value{font-weight:700;text-align:right}.status{font-size:20px;font-weight:800;padding:14px;border-radius:10px;margin:16px 0;background:#20252d}.green{color:#62e6a7}.red{color:#ff7777}.yellow{color:#ffd166}.blue{color:#73b7ff}.muted{color:#9ca3af}.footer{margin-top:18px;color:#7f8895;font-size:12px}
.banner{background:#14181d;border:1px solid #2a3038;border-radius:14px;padding:18px;margin-bottom:18px}.banner-title{font-size:22px;font-weight:800}.banner-sub{color:#9ca3af;margin-top:6px}
</style></head><body><div class="wrap">
<h1>PSYCHO PDRA MONITOR</h1><div class="sub">Previous-Day Range Acceptance • refresh manually • eligibility from 09:30 IST</div>
<div class="banner"><div class="banner-title">{{session.status}}</div><div class="banner-sub">{{session.detail}}</div></div>
<div class="grid">{% for x in instruments %}<div class="card">
<div class="title">{{x.name}}</div><div class="time">{{x.generated}}</div>
<div class="row"><span class="label">PDH</span><span class="value">{{x.pdh}}</span></div>
<div class="row"><span class="label">PDL</span><span class="value">{{x.pdl}}</span></div>
<div class="row"><span class="label">Today's Open</span><span class="value">{{x.open}}</span></div>
<div class="row"><span class="label">ATR(14), 5M</span><span class="value">{{x.atr}}</span></div>
<div class="row"><span class="label">0.20 ATR</span><span class="value">{{x.threshold}}</span></div>
<div class="row"><span class="label">Long Trigger</span><span class="value">{{x.long_trigger}}</span></div>
<div class="row"><span class="label">Short Trigger</span><span class="value">{{x.short_trigger}}</span></div>
<div class="status {{x.status_class}}">{{x.status}}</div>
<div class="row"><span class="label">Displacement</span><span class="value">{{x.displacement}}</span></div>
<div class="row"><span class="label">Acceptance</span><span class="value">{{x.acceptance}}</span></div>
<div class="row"><span class="label">Direction</span><span class="value">{{x.direction}}</span></div>
<div class="row"><span class="label">Entry</span><span class="value">{{x.entry}}</span></div>
<div class="row"><span class="label">SL</span><span class="value">{{x.sl}}</span></div>
<div class="row"><span class="label">TP (1R)</span><span class="value">{{x.tp}}</span></div>
<div class="row"><span class="label">Bars remaining</span><span class="value">{{x.bars}}</span></div>
<div class="footer">Research/paper-trading monitor. No order is placed by this application.</div>
</div>{% endfor %}</div></div></body></html>'''


def get_json(path):
    req = urllib.request.Request(f"{BRIDGE}{path}", headers={"Accept": "application/json"})
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


def find_candles(obj):
    found = []
    for x in flatten(obj):
        if isinstance(x, dict) and all(k in x for k in ("open", "high", "low", "close")):
            try:
                ts = x.get("timestamp") or x.get("time") or x.get("datetime")
                if ts is not None:
                    if isinstance(ts, str) and not ts.isdigit():
                        dt = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(IST)
                    else:
                        dt = datetime.fromtimestamp(int(ts), IST)
                    found.append({"dt": dt, "open": float(x["open"]), "high": float(x["high"]), "low": float(x["low"]), "close": float(x["close"])})
            except Exception:
                pass
    unique = {c["dt"]: c for c in found}
    return sorted(unique.values(), key=lambda c: c["dt"])


def atr(candles):
    if len(candles) < ATR_LENGTH + 1:
        return None
    trs = []
    prev = None
    for c in candles:
        if prev is None:
            tr = c["high"] - c["low"]
        else:
            tr = max(c["high"] - c["low"], abs(c["high"] - prev), abs(c["low"] - prev))
        trs.append(tr)
        prev = c["close"]
    return sum(trs[-ATR_LENGTH:]) / ATR_LENGTH


def fmt(v):
    if v is None:
        return "—"
    return f"{v:,.2f}" if isinstance(v, float) and not v.is_integer() else f"{v:,.0f}"


def session_state(now):
    wd = now.weekday()
    if wd >= 5:
        return {"status": "MARKET CLOSED — WEEKEND", "detail": "PDRA will become eligible after 09:30 IST on the next trading session."}
    if now.time() < MARKET_OPEN:
        return {"status": "MARKET CLOSED — PRE-OPEN", "detail": "Waiting for the 09:15 IST market open."}
    if now.time() < ELIGIBILITY:
        return {"status": "MARKET OPEN — PDRA BUILDING", "detail": "The opening range is forming. Strategy eligibility begins at 09:30 IST."}
    if now.time() > MARKET_CLOSE:
        return {"status": "MARKET CLOSED — SESSION COMPLETE", "detail": "PDRA session ended at 15:40 IST. Refresh on the next trading session."}
    return {"status": "MARKET OPEN — PDRA ACTIVE", "detail": "Current 5-minute structure is being evaluated."}


def closed_card(name, now, status):
    return {"name": name, "generated": now.strftime("%d %b %Y %H:%M:%S IST"), "pdh": "—", "pdl": "—", "open": "—", "atr": "—", "threshold": "—", "long_trigger": "—", "short_trigger": "—", "status": status, "status_class": "blue", "displacement": "NOT ACTIVE", "acceptance": "NOT ACTIVE", "direction": "—", "entry": "—", "sl": "—", "tp": "—", "bars": "—"}


def analyse(name, path, now):
    raw = get_json(path)
    candles = find_candles(raw)
    sessions = {}
    for c in candles:
        sessions.setdefault(c["dt"].date(), []).append(c)
    dates = sorted(sessions)
    today = sessions.get(now.date(), [])
    prior_dates = [d for d in dates if d < now.date()]
    prev = sessions.get(prior_dates[-1], []) if prior_dates else []
    pdh = max((c["high"] for c in prev), default=None)
    pdl = min((c["low"] for c in prev), default=None)
    op = today[0]["open"] if today else None
    a = atr(candles)
    threshold = a * DISPLACEMENT_ATR if a is not None else None
    out = {"name": name, "generated": now.strftime("%d %b %Y %H:%M:%S IST"), "pdh": fmt(pdh), "pdl": fmt(pdl), "open": fmt(op), "atr": fmt(a), "threshold": fmt(threshold), "long_trigger": fmt(pdh + threshold if pdh is not None and threshold is not None else None), "short_trigger": fmt(pdl - threshold if pdl is not None and threshold is not None else None), "status": "WAITING", "status_class": "yellow", "displacement": "WAITING", "acceptance": "WAITING", "direction": "—", "entry": "—", "sl": "—", "tp": "—", "bars": "—"}

    if now.weekday() >= 5:
        return closed_card(name, now, "MARKET CLOSED — WEEKEND")
    if now.time() < MARKET_OPEN:
        return closed_card(name, now, "MARKET CLOSED — PRE-OPEN")
    if now.time() > MARKET_CLOSE:
        return closed_card(name, now, "MARKET CLOSED — SESSION COMPLETE")
    if now.time() < ELIGIBILITY:
        out["status"] = "PDRA STARTS 09:30"
        return out

    if not today or pdh is None or pdl is None or a is None:
        out["status"] = "DATA UNAVAILABLE"
        out["status_class"] = "red"
        return out
    if not (pdl < op < pdh):
        out["status"] = "NO TRADE — OPEN OUTSIDE RANGE"
        out["status_class"] = "red"
        return out

    completed = [c for c in today if c["dt"].minute % 5 == 0 and c["dt"].replace(second=0, microsecond=0) + timedelta(minutes=5) <= now]
    if len(completed) < 2:
        out["status"] = "WAITING FOR COMPLETED 5M CANDLE"
        return out

    trigger = None
    direction = None
    for c in completed:
        if c["close"] >= pdh + threshold:
            trigger = c
            direction = "LONG"
            break
        if c["close"] <= pdl - threshold:
            trigger = c
            direction = "SHORT"
            break
    if trigger is None:
        out["status"] = "WAITING — NO DISPLACEMENT"
        return out

    out["displacement"] = "CONFIRMED"
    out["direction"] = direction
    idx = completed.index(trigger)
    if idx + 1 >= len(completed):
        out["status"] = "DISPLACEMENT CONFIRMED — ACCEPTANCE WAITING"
        return out

    confirm = completed[idx + 1]
    accepted = confirm["close"] > pdh if direction == "LONG" else confirm["close"] < pdl
    if not accepted:
        out["status"] = "NO TRADE — ACCEPTANCE FAILED"
        out["status_class"] = "red"
        out["acceptance"] = "FAILED"
        return out

    out["acceptance"] = "CONFIRMED"
    entry_bar = completed[idx + 2] if idx + 2 < len(completed) else None
    if entry_bar is None:
        out["status"] = "ACCEPTANCE CONFIRMED — ENTRY WAITING"
        return out

    entry = entry_bar["open"]
    sl = pdh if direction == "LONG" else pdl
    risk = abs(entry - sl)
    tp = entry + risk if direction == "LONG" else entry - risk
    out.update({"status": f"{direction} — EXECUTION VALID", "status_class": "green", "entry": fmt(entry), "sl": fmt(sl), "tp": fmt(tp), "bars": str(HOLD_BARS)})
    return out


def error_card(name, now, error):
    return {"name": name, "generated": now.strftime("%d %b %Y %H:%M:%S IST"), "pdh": "—", "pdl": "—", "open": "—", "atr": "—", "threshold": "—", "long_trigger": "—", "short_trigger": "—", "status": "BRIDGE UNAVAILABLE", "status_class": "yellow", "displacement": "Bridge returned no live data", "acceptance": "—", "direction": "—", "entry": "—", "sl": "—", "tp": "—", "bars": "—"}


@app.route("/")
def home():
    now = datetime.now(IST)
    session = session_state(now)
    items = []

    # Do not call the live bridge while the market is closed. A closed market is
    # a valid state, not a data error. This also prevents the bridge's intentional
    # 503 response outside its live window from being displayed as DATA ERROR.
    market_closed = now.weekday() >= 5 or now.time() < MARKET_OPEN or now.time() > MARKET_CLOSE
    if market_closed:
        status = session["status"]
        for name in ("NIFTY", "BANK NIFTY"):
            items.append(closed_card(name, now, status))
    else:
        for name, path in (("NIFTY", "/nifty-live"), ("BANK NIFTY", "/banknifty-live")):
            try:
                items.append(analyse(name, path, now))
            except Exception as e:
                items.append(error_card(name, now, str(e)))

    return render_template_string(HTML, instruments=items, session=session)


@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "pdra-monitor", "market": session_state(datetime.now(IST))})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
