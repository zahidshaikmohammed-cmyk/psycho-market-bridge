import json
import threading
import time
import urllib.request
from datetime import datetime, time as dt_time
from zoneinfo import ZoneInfo

from flask import Flask, jsonify, render_template_string

app = Flask(__name__)
IST = ZoneInfo("Asia/Kolkata")
BRIDGE = "https://psycho-market-bridge.onrender.com"
OPEN = dt_time(9, 15)
SCAN_START = dt_time(9, 30)
CLOSE = dt_time(15, 40)
SCAN_SECONDS = 60
MIN_SCORE = 80

HTML = r'''<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>PSYCHO Opportunity Engine</title>
<style>
body{font-family:Arial,sans-serif;background:#090b0f;color:#f4f6f8;margin:0;padding:18px}.wrap{max-width:1150px;margin:auto}
h1{margin:0;font-size:28px}.sub{color:#9aa3ad;margin:6px 0 18px}.banner,.card{background:#14181e;border:1px solid #29303a;border-radius:14px;padding:18px;margin-bottom:16px}.banner b{font-size:22px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}@media(max-width:800px){.grid{grid-template-columns:1fr}}
.title{font-size:21px;font-weight:800}.time{color:#8e98a5;font-size:12px;margin:5px 0 14px}.status{padding:13px;border-radius:10px;background:#202631;font-weight:800;font-size:19px;margin:12px 0}.green{color:#63e6a7}.yellow{color:#ffd166}.red{color:#ff7777}.blue{color:#73b7ff}.row{display:flex;justify-content:space-between;border-bottom:1px solid #232a33;padding:7px 0}.label{color:#aab3bf}.value{font-weight:700;text-align:right}.section{margin-top:16px;font-weight:800;font-size:13px;color:#8ea0b4;letter-spacing:.06em}.small{font-size:12px;color:#8d96a2;line-height:1.5;margin-top:12px}.pill{display:inline-block;padding:5px 9px;border-radius:8px;background:#202631;margin-right:5px;font-size:12px}.footer{color:#707985;font-size:11px;margin-top:14px}
</style></head><body><div class="wrap">
<h1>PSYCHO INTRADAY OPPORTUNITY ENGINE</h1>
<div class="sub">1-minute scan • multi-timeframe structure • option-chain selection • research score</div>
<div class="banner"><b>{{session.status}}</b><div class="sub">{{session.detail}}</div><div class="small">Last engine scan: {{scan_time}} • Next automatic scan: within 60 seconds</div></div>
<div class="grid">{% for x in items %}<div class="card">
<div class="title">{{x.name}}</div><div class="time">{{x.generated}}</div>
<div class="status {{x.status_class}}">{{x.status}}</div>
<div class="row"><span class="label">Probability Score*</span><span class="value">{{x.score}}/100</span></div>
<div class="row"><span class="label">Setup Detected</span><span class="value">{{x.detected}}</span></div>
<div class="row"><span class="label">Direction</span><span class="value">{{x.direction}}</span></div>
<div class="section">MARKET STATE</div>
<div class="row"><span class="label">Underlying</span><span class="value">{{x.underlying}}</span></div>
<div class="row"><span class="label">PDH / PDL</span><span class="value">{{x.pdh}} / {{x.pdl}}</span></div>
<div class="row"><span class="label">VWAP</span><span class="value">{{x.vwap}}</span></div>
<div class="row"><span class="label">ATR(14)</span><span class="value">{{x.atr}}</span></div>
<div class="row"><span class="label">Structure</span><span class="value">{{x.structure}}</span></div>
<div class="row"><span class="label">Momentum</span><span class="value">{{x.momentum}}</span></div>
<div class="section">SELECTED OPTION</div>
<div class="row"><span class="label">Contract</span><span class="value">{{x.contract}}</span></div>
<div class="row"><span class="label">Premium</span><span class="value">{{x.premium}}</span></div>
<div class="row"><span class="label">Delta</span><span class="value">{{x.delta}}</span></div>
<div class="row"><span class="label">OI / ΔOI</span><span class="value">{{x.oi}} / {{x.oi_change}}</span></div>
<div class="row"><span class="label">Volume</span><span class="value">{{x.volume}}</span></div>
<div class="row"><span class="label">Bid / Ask</span><span class="value">{{x.bid}} / {{x.ask}}</span></div>
<div class="section">EXECUTION</div>
<div class="row"><span class="label">Entry</span><span class="value">{{x.entry}}</span></div>
<div class="row"><span class="label">Stop Loss</span><span class="value">{{x.sl}}</span></div>
<div class="row"><span class="label">Target</span><span class="value">{{x.tp}}</span></div>
<div class="row"><span class="label">Risk : Reward</span><span class="value">{{x.rr}}</span></div>
<div class="small">{{x.reason}}</div>
<div class="footer">* Score is an evidence-weighted research score, NOT a statistically calibrated win probability until validated against historical outcomes. No orders are placed.</div>
</div>{% endfor %}</div></div></body></html>'''

state_lock = threading.Lock()
STATE = {"scan_time": None, "items": [], "session": {}}


def now():
    return datetime.now(IST)


def session_state(t):
    if t.weekday() >= 5:
        return {"status":"MARKET CLOSED — WEEKEND","detail":"Engine idle. It will resume on the next trading session."}
    if t.time() < OPEN:
        return {"status":"MARKET CLOSED — PRE-OPEN","detail":"Waiting for 09:15 IST."}
    if t.time() < SCAN_START:
        return {"status":"MARKET OPEN — BUILDING CONTEXT","detail":"Engine waits for the 09:30 eligibility gate."}
    if t.time() > CLOSE:
        return {"status":"MARKET CLOSED — SESSION COMPLETE","detail":"Intraday scan ended at 15:40 IST."}
    return {"status":"MARKET OPEN — SCANNING","detail":"Scanning NIFTY and BANK NIFTY for qualified setups."}


def get_json(path):
    req = urllib.request.Request(BRIDGE + path, headers={"Accept":"application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def flatten_candles(obj):
    found=[]
    def walk(v):
        if isinstance(v,dict):
            if all(k in v for k in ("open","high","low","close")):
                try:
                    ts=v.get("timestamp") or v.get("time") or v.get("datetime")
                    if ts is not None:
                        if isinstance(ts,str) and not ts.isdigit():
                            dt=datetime.fromisoformat(ts.replace("Z","+00:00")).astimezone(IST)
                        else:
                            dt=datetime.fromtimestamp(int(ts),IST)
                        found.append({"dt":dt,"open":float(v["open"]),"high":float(v["high"]),"low":float(v["low"]),"close":float(v["close"]),"volume":float(v.get("volume") or 0)})
                except Exception: pass
            for x in v.values(): walk(x)
        elif isinstance(v,list):
            for x in v: walk(x)
    walk(obj)
    return sorted({x["dt"]:x for x in found}.values(),key=lambda x:x["dt"])


def ema(vals,n):
    if not vals: return None
    a=2/(n+1); e=vals[0]
    for v in vals[1:]: e=a*v+(1-a)*e
    return e


def atr14(c):
    if len(c)<15:return None
    trs=[]; prev=None
    for x in c:
        tr=x["high"]-x["low"] if prev is None else max(x["high"]-x["low"],abs(x["high"]-prev),abs(x["low"]-prev))
        trs.append(tr); prev=x["close"]
    return sum(trs[-14:])/14


def vwap(c):
    if not c:return None
    pv=0; vol=0
    for x in c:
        typical=(x["high"]+x["low"]+x["close"])/3
        pv += typical*x["volume"]; vol += x["volume"]
    return pv/vol if vol else None


def f(v,dec=2):
    if v is None:return "—"
    return f"{v:,.{dec}f}"


def parse_option_chain(raw):
    oc=((raw.get("option_chain") or {}).get("strikes") or {}) if isinstance(raw,dict) else {}
    out=[]
    for k,s in oc.items():
        try: strike=float(s.get("strike",k))
        except Exception: continue
        for side in ("CE","PE"):
            leg=s.get(side) or {}
            ltp=leg.get("last_price")
            if ltp is None: continue
            try: ltp=float(ltp)
            except Exception: continue
            greeks=leg.get("greeks") or {}
            def num(key):
                try:return float(leg.get(key)) if leg.get(key) is not None else None
                except Exception:return None
            out.append({"strike":strike,"side":side,"ltp":ltp,"oi":num("oi"),"oi_change":num("oi_change"),"volume":num("volume"),"iv":num("implied_volatility"),"bid":num("top_bid_price"),"ask":num("top_ask_price"),"delta":greeks.get("delta")})
    return out


def candidate_score(c, underlying, direction, atrv):
    score=0
    # Moneyness: prefer ATM or one step ITM/OTM, avoiding far OTM lottery premiums.
    dist=abs(c["strike"]-underlying)
    if atrv and dist <= 0.75*atrv: score+=20
    elif atrv and dist <= 1.5*atrv: score+=14
    else: score+=5
    # Liquidity / spread.
    if c["bid"] is not None and c["ask"] is not None and c["ask"]>=c["bid"] and c["ltp"]>0:
        spread=c["ask"]-c["bid"]
        if spread/c["ltp"] <= .01: score+=20
        elif spread/c["ltp"] <= .02: score+=14
        elif spread/c["ltp"] <= .04: score+=7
    # Delta: favour responsive but not extreme contracts.
    d=abs(float(c["delta"])) if c["delta"] is not None else None
    if d is not None:
        if .40 <= d <= .65: score+=20
        elif .30 <= d <= .75: score+=14
        else: score+=7
    else: score+=5
    # Activity.
    if c["volume"] and c["volume"]>0: score+=10
    if c["oi"] and c["oi"]>0: score+=10
    # Premium should not be a zero/near-zero contract.
    if c["ltp"] >= 50: score+=10
    return min(score,100)


def choose_option(chain, underlying, direction, atrv):
    side="CE" if direction=="LONG" else "PE"
    cs=[x for x in chain if x["side"]==side]
    ranked=sorted(((candidate_score(x,underlying,direction,atrv),x) for x in cs),key=lambda z:z[0],reverse=True)
    return ranked[0] if ranked else (0,None)


def analyse(name,path):
    raw=get_json(path)
    market=raw.get("market") or raw
    candles=flatten_candles(market)
    nowt=now()
    today=[x for x in candles if x["dt"].date()==nowt.date()]
    prior=[x for x in candles if x["dt"].date()<nowt.date()]
    dates=sorted({x["dt"].date() for x in prior})
    prev=[x for x in prior if x["dt"].date()==dates[-1]] if dates else []
    pdh=max((x["high"] for x in prev),default=None); pdl=min((x["low"] for x in prev),default=None)
    closes=[x["close"] for x in today]; last=closes[-1] if closes else None
    a=atr14(candles)
    vw=vwap(today)
    score=0; direction="—"; structure="NEUTRAL"; momentum="NEUTRAL"; detected="NO"
    reason=[]
    if last is not None and vw is not None:
        if last>vw: score+=10; direction="LONG"; structure="ABOVE VWAP"
        elif last<vw: score+=10; direction="SHORT"; structure="BELOW VWAP"
    if last is not None and pdh is not None and pdl is not None and a:
        if last>pdh+0.20*a: score+=25; direction="LONG"; momentum="RANGE EXPANSION UP"; reason.append("PDH displacement")
        elif last<pdl-0.20*a: score+=25; direction="SHORT"; momentum="RANGE EXPANSION DOWN"; reason.append("PDL displacement")
        elif last>pdh: score+=12; direction="LONG"; momentum="ABOVE PDH"
        elif last<pdl: score+=12; direction="SHORT"; momentum="BELOW PDL"
    # 5M/1H directional confirmation from bridge timeframe arrays.
    tfs=market.get("timeframes") or {}
    c5=flatten_candles(tfs.get("5M") or [])
    c15=flatten_candles(tfs.get("15M") or [])
    c1h=flatten_candles(tfs.get("1H") or [])
    for cc,weight in ((c5,15),(c15,10),(c1h,10)):
        if len(cc)>=2:
            e=ema([x["close"] for x in cc],9)
            if direction=="LONG" and cc[-1]["close"]>e: score+=weight
            elif direction=="SHORT" and cc[-1]["close"]<e: score+=weight
    # Option-chain confirmation and selection.
    chain=parse_option_chain(raw)
    option_score=0; selected=None
    if direction in ("LONG","SHORT") and last is not None and chain:
        option_score,selected=choose_option(chain,last,direction,a)
        if selected:
            score += round(option_score*0.30)
            reason.append("option execution quality")
    score=min(score,100)
    if score>=MIN_SCORE and selected:
        detected="YES"
        status="🟢 HIGH-PROBABILITY CANDIDATE"
        cls="green"
    elif score>=65:
        status="🟡 WATCH — NOT QUALIFIED"
        cls="yellow"
    else:
        status="⚪ NO QUALIFIED SETUP"
        cls="blue"
    entry=sl=tp=rr="—"
    if selected and detected=="YES":
        p=selected["ltp"]
        entry=p
        # Premium risk is estimated from a conservative 16% premium stop; this is not a calibrated stop model.
        sl=round(p*0.84,2)
        risk=p-sl; tp=round(p+risk*2,2)
        rr="1 : 2"
    return {"name":name,"generated":nowt.strftime("%d %b %Y %H:%M:%S IST"),"score":score,"detected":detected,"direction":direction,"status":status,"status_class":cls,"underlying":f(last),"pdh":f(pdh),"pdl":f(pdl),"vwap":f(vw),"atr":f(a),"structure":structure,"momentum":momentum,"contract":(f(selected['strike'],0)+" "+selected['side']) if selected else "—","premium":f(selected['ltp']) if selected else "—","delta":f(float(selected['delta'])) if selected and selected['delta'] is not None else "—","oi":f(selected['oi'],0) if selected else "—","oi_change":f(selected['oi_change'],0) if selected else "—","volume":f(selected['volume'],0) if selected else "—","bid":f(selected['bid']) if selected else "—","ask":f(selected['ask']) if selected else "—","entry":f(entry),"sl":f(sl),"tp":f(tp),"rr":rr,"reason":" • ".join(reason) if reason else "No qualifying confluence yet."}


def closed_item(name):
    return {"name":name,"generated":now().strftime("%d %b %Y %H:%M:%S IST"),"score":0,"detected":"NO","direction":"—","status":"MARKET CLOSED","status_class":"blue","underlying":"—","pdh":"—","pdl":"—","vwap":"—","atr":"—","structure":"—","momentum":"—","contract":"—","premium":"—","delta":"—","oi":"—","oi_change":"—","volume":"—","bid":"—","ask":"—","entry":"—","sl":"—","tp":"—","rr":"—","reason":"Engine is idle outside the trading session."}


def scan_once():
    t=now(); ss=session_state(t)
    if t.weekday()>=5 or t.time()<OPEN or t.time()>CLOSE:
        items=[closed_item("NIFTY"),closed_item("BANK NIFTY")]
    elif t.time()<SCAN_START:
        items=[closed_item("NIFTY"),closed_item("BANK NIFTY")]
        for x in items: x["status"]="BUILDING CONTEXT"; x["reason"]="Waiting for the 09:30 eligibility gate."
    else:
        items=[]
        for n,p in (("NIFTY","/nifty-live"),("BANK NIFTY","/banknifty-live")):
            try: items.append(analyse(n,p))
            except Exception as e: items.append({**closed_item(n),"status":"BRIDGE UNAVAILABLE","status_class":"yellow","reason":str(e)})
    with state_lock:
        STATE["scan_time"]=t.strftime("%d %b %Y %H:%M:%S IST"); STATE["items"]=items; STATE["session"]=ss


def worker():
    while True:
        try: scan_once()
        except Exception as e: print("ENGINE SCAN ERROR",e,flush=True)
        time.sleep(SCAN_SECONDS)

threading.Thread(target=worker,daemon=True).start()

@app.route("/")
def home():
    with state_lock: data=json.loads(json.dumps(STATE))
    return render_template_string(HTML,items=data["items"],session=data["session"],scan_time=data["scan_time"] or "starting")

@app.route("/health")
def health():
    with state_lock: st=STATE["scan_time"]
    return jsonify({"status":"ok","service":"psycho-opportunity-engine","last_scan":st,"session":session_state(now())})

@app.route("/api/state")
def api_state():
    with state_lock: return jsonify(STATE)

if __name__=="__main__":
    app.run(host="0.0.0.0",port=10000)
