import json
import os
import urllib.request
from datetime import datetime, timedelta, time as dt_time
from zoneinfo import ZoneInfo

from flask import Flask, render_template_string

app = Flask(__name__)
TOKEN = os.environ["DHAN_ACCESS_TOKEN"]
CLIENT_ID = os.environ.get("DHAN_CLIENT_ID", "")
IST = ZoneInfo("Asia/Kolkata")
INTRADAY_URL = "https://api.dhan.co/v2/charts/intraday"
HISTORICAL_URL = "https://api.dhan.co/v2/charts/historical"
MARKET_OPEN = dt_time(9, 15)
ELIGIBILITY = dt_time(9, 30)

INSTRUMENTS = {
    "NIFTY": "13",
    "BANK NIFTY": "25",
}

HTML = """
<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>PDRA Intraday Monitor</title>
<style>
body{font-family:Arial,sans-serif;background:#0b0f14;color:#e8edf2;margin:0;padding:24px} .wrap{max-width:1100px;margin:auto}
h1{margin:0 0 6px;font-size:28px}.sub{color:#8fa0ad;margin-bottom:22px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:18px}
.card{background:#121922;border:1px solid #263342;border-radius:14px;padding:20px}.title{font-size:22px;font-weight:700}.time{color:#8fa0ad;font-size:13px;margin:5px 0 18px}
.row{display:flex;justify-content:space-between;border-bottom:1px solid #202b36;padding:8px 0}.label{color:#91a0ad}.value{font-weight:700}.section{margin-top:18px;font-size:13px;letter-spacing:.08em;color:#7f93a3}
.status{margin-top:18px;padding:14px;border-radius:10px;font-size:18px;font-weight:800}.green{background:#123d2a;color:#65e6a1}.red{background:#431b22;color:#ff8793}.amber{background:#443816;color:#ffd66b}.gray{background:#26313b;color:#b9c3cc}
.note{margin-top:20px;color:#8999a7;font-size:12px;line-height:1.5}.refresh{display:inline-block;margin-bottom:20px;padding:9px 14px;border:1px solid #405160;border-radius:9px;color:#dce5eb;text-decoration:none}
@media(max-width:760px){.grid{grid-template-columns:1fr}body{padding:14px}}
</style></head><body><div class='wrap'>
<h1>PDRA INTRADAY MONITOR</h1><div class='sub'>Previous-Day Range Acceptance • 5M • research execution monitor</div>
<a class='refresh' href='/'>↻ Refresh calculation</a>
<div class='grid'>
{% for x in states %}<div class='card'>
<div class='title'>{{x.name}}</div><div class='time'>{{x.time}}</div>
<div class='section'>PREVIOUS DAY</div><div class='row'><span class='label'>PDH</span><span class='value'>{{x.pdh}}</span></div><div class='row'><span class='label'>PDL</span><span class='value'>{{x.pdl}}</span></div>
<div class='section'>TODAY</div><div class='row'><span class='label'>Open</span><span class='value'>{{x.open}}</span></div><div class='row'><span class='label'>Opening condition</span><span class='value'>{{x.open_status}}</span></div>
<div class='section'>VOLATILITY</div><div class='row'><span class='label'>ATR(14) 5M</span><span class='value'>{{x.atr}}</span></div><div class='row'><span class='label'>0.20 ATR</span><span class='value'>{{x.threshold}}</span></div><div class='row'><span class='label'>Long trigger</span><span class='value'>{{x.long_trigger}}</span></div><div class='row'><span class='label'>Short trigger</span><span class='value'>{{x.short_trigger}}</span></div>
<div class='section'>EVENT</div><div class='row'><span class='label'>Displacement</span><span class='value'>{{x.displacement}}</span></div><div class='row'><span class='label'>Acceptance</span><span class='value'>{{x.acceptance}}</span></div>
<div class='section'>EXECUTION</div><div class='row'><span class='label'>Direction</span><span class='value'>{{x.direction}}</span></div><div class='row'><span class='label'>Entry</span><span class='value'>{{x.entry}}</span></div><div class='row'><span class='label'>Stop</span><span class='value'>{{x.stop}}</span></div><div class='row'><span class='label'>Risk</span><span class='value'>{{x.risk}}</span></div><div class='row'><span class='label'>Target (1R)</span><span class='value'>{{x.target}}</span></div>
<div class='status {{x.css}}'>{{x.status}}</div>
<div class='note'>Data source: DHAN. Calculation is performed server-side on each page refresh. Eligibility gate: 09:30 IST. This monitor does not place orders.</div>
</div>{% endfor %}</div></div></body></html>
"""

def dhan_post(url, payload):
    body = json.dumps(payload).encode()
    headers = {"Accept":"application/json","Content-Type":"application/json","access-token":TOKEN}
    if CLIENT_ID:
        headers["client-id"] = CLIENT_ID
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())

def candles(raw):
    if not isinstance(raw, dict): return []
    keys=["timestamp","open","high","low","close","volume"]
    a={k:(raw.get(k) or []) for k in keys}
    n=min((len(a[k]) for k in keys), default=0)
    out=[]
    for i in range(n):
        try: ts=int(a["timestamp"][i])
        except Exception: continue
        out.append({k:a[k][i] for k in keys}|{"timestamp":ts})
    return sorted(out,key=lambda z:z["timestamp"])

def dt(c):
    return datetime.fromtimestamp(int(c["timestamp"]),IST)

def tr(c, prev_close):
    h,l=float(c["high"]),float(c["low"])
    return max(h-l,abs(h-prev_close),abs(l-prev_close))

def atr14(series, end_index):
    if end_index < 14: return None
    vals=[]
    start=max(1,end_index-14)
    for i in range(start,end_index):
        vals.append(tr(series[i],float(series[i-1]["close"])))
    return sum(vals)/len(vals) if vals else None

def fmt(v):
    if v is None:return "—"
    try:return f"{float(v):,.2f}"
    except:return str(v)

def fetch_state(name, sid, now):
    today=now.date()
    intraday_raw=dhan_post(INTRADAY_URL,{"securityId":sid,"exchangeSegment":"IDX_I","instrument":"INDEX","interval":"5","oi":False,"fromDate":(now-timedelta(days=10)).strftime("%Y-%m-%d 09:15:00"),"toDate":now.strftime("%Y-%m-%d %H:%M:%S")})
    all5=candles(intraday_raw)
    session=[c for c in all5 if dt(c).date()==today and MARKET_OPEN<=dt(c).time()<=dt_time(15,40)]
    hist=[c for c in all5 if dt(c).date()<today]
    daily_raw=dhan_post(HISTORICAL_URL,{"securityId":sid,"exchangeSegment":"IDX_I","instrument":"INDEX","expiryCode":0,"oi":False,"fromDate":(now-timedelta(days=30)).strftime("%Y-%m-%d"),"toDate":(today+timedelta(days=1)).strftime("%Y-%m-%d")})
    daily=candles(daily_raw)
    prev=[c for c in daily if dt(c).date()<today]
    pd=prev[-1] if prev else None
    state={"name":name,"time":now.strftime("%d %b %Y • %H:%M:%S IST"),"pdh":fmt(pd.get("high") if pd else None),"pdl":fmt(pd.get("low") if pd else None),"open":fmt(session[0].get("open") if session else None),"open_status":"—","atr":"—","threshold":"—","long_trigger":"—","short_trigger":"—","displacement":"WAITING","acceptance":"WAITING","direction":"—","entry":"—","stop":"—","risk":"—","target":"—","status":"WAITING FOR MARKET DATA","css":"gray"}
    if not pd or not session:
        state["status"]="NO CURRENT SESSION DATA"; return state
    opening=float(session[0]["open"]); pdh=float(pd["high"]); pdl=float(pd["low"])
    inside=pdl<opening<pdh
    state["open_status"]="INSIDE RANGE ✓" if inside else "OUTSIDE RANGE ✕"
    # Build a pre-session ATR history and use the last 14 completed 5M true ranges before each trigger.
    combined=sorted(hist+session,key=lambda z:z["timestamp"])
    if now.time()<ELIGIBILITY:
        state["status"]="WAITING FOR 09:30 IST"; state["css"]="amber"; return state
    if not inside:
        state["status"]="NO TRADE — OPEN OUTSIDE PREVIOUS-DAY RANGE"; state["css"]="red"; return state
    # Find first valid displacement candle whose close is >= 0.20 ATR beyond PDH/PDL.
    event=None
    for i,c in enumerate(session):
        if dt(c).time()<MARKET_OPEN: continue
        gi=next((j for j,z in enumerate(combined) if z["timestamp"]==c["timestamp"]),None)
        if gi is None or gi<15: continue
        a=atr14(combined,gi)
        if not a: continue
        close=float(c["close"]); threshold=.20*a
        if close>=pdh+threshold:
            direction="LONG"
        elif close<=pdl-threshold:
            direction="SHORT"
        else: continue
        event=(i,c,direction,a); break
    if not event:
        state["status"]="NO PDRA DISPLACEMENT YET"; state["css"]="gray"; return state
    i,disp,direction,a=event; threshold=.20*a
    state["atr"]=fmt(a); state["threshold"]=fmt(threshold); state["long_trigger"]=fmt(pdh+threshold); state["short_trigger"]=fmt(pdl-threshold)
    state["displacement"]=f"✓ {direction} @ {fmt(disp['close'])}"
    if i+1>=len(session):
        state["status"]="DISPLACEMENT CONFIRMED — WAITING FOR ACCEPTANCE"; state["css"]="amber"; return state
    accept=session[i+1]; ac=float(accept["close"])
    accepted=ac>pdh if direction=="LONG" else ac<pdl
    state["acceptance"]=f"✓ {fmt(ac)}" if accepted else f"✕ {fmt(ac)}"
    if not accepted:
        state["status"]="NO TRADE — ACCEPTANCE FAILED"; state["css"]="red"; return state
    if i+2>=len(session):
        state["status"]="ACCEPTANCE CONFIRMED — WAITING FOR ENTRY CANDLE"; state["css"]="amber"; return state
    entry=float(session[i+2]["open"])
    stop=pdh if direction=="LONG" else pdl
    risk=abs(entry-stop)
    target=entry+risk if direction=="LONG" else entry-risk
    state.update({"direction":direction,"entry":fmt(entry),"stop":fmt(stop),"risk":fmt(risk),"target":fmt(target),"status":f"PDRA {direction} — EXECUTION VALID","css":"green"})
    return state

@app.route("/")
def index():
    now=datetime.now(IST)
    states=[]
    for name,sid in INSTRUMENTS.items():
        try: states.append(fetch_state(name,sid,now))
        except Exception as e:
            states.append({"name":name,"time":now.strftime("%d %b %Y • %H:%M:%S IST"),"pdh":"—","pdl":"—","open":"—","open_status":"ERROR","atr":"—","threshold":"—","long_trigger":"—","short_trigger":"—","displacement":"ERROR","acceptance":"ERROR","direction":"—","entry":"—","stop":"—","risk":"—","target":"—","status":"DATA ERROR — refresh / inspect server logs","css":"red"})
    return render_template_string(HTML,states=states)

@app.get("/health")
def health(): return {"status":"ok","service":"PDRA monitor"}

if __name__ == "__main__":
    app.run(host="0.0.0.0",port=int(os.environ.get("PORT","10000")))
