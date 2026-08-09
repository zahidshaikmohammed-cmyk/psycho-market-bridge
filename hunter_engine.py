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
OPEN, SCAN_START, CLOSE = dt_time(9, 15), dt_time(9, 30), dt_time(15, 40)
SCAN_SECONDS = 60
QUALIFY_SCORE = 85

HTML = '''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="refresh" content="60"><title>PSYCHO HUNTER ENGINE</title><style>
body{font-family:Arial;background:#090b0f;color:#f4f6f8;margin:0;padding:18px}.wrap{max-width:1100px;margin:auto}h1{margin:0;font-size:29px}.sub{color:#9aa3ad;margin:6px 0 18px}.banner,.card{background:#14181e;border:1px solid #29303a;border-radius:14px;padding:18px;margin-bottom:16px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}@media(max-width:800px){.grid{grid-template-columns:1fr}}.title{font-size:22px;font-weight:800}.status{padding:13px;border-radius:10px;background:#202631;font-weight:800;font-size:18px;margin:12px 0}.green{color:#63e6a7}.yellow{color:#ffd166}.blue{color:#73b7ff}.row{display:flex;justify-content:space-between;border-bottom:1px solid #232a33;padding:7px 0}.label{color:#aab3bf}.value{font-weight:700;text-align:right}.section{margin-top:16px;font-weight:800;font-size:12px;color:#8ea0b4;letter-spacing:.08em}.small{font-size:12px;color:#8d96a2;line-height:1.5;margin-top:12px}.hero{font-size:30px;font-weight:900}.footer{color:#707985;font-size:11px;margin-top:14px}
</style></head><body><div class="wrap"><h1>PSYCHO HUNTER ENGINE</h1><div class="sub">Independent Intraday Opportunity & Execution System • 1-minute scan</div>
<div class="banner"><div class="hero">{{session.status}}</div><div class="sub">{{session.detail}}</div><div class="small">Last hunter scan: {{scan_time}} • Automatic scan every 60 seconds</div></div>
<div class="grid">{% for x in items %}<div class="card"><div class="title">{{x.name}}</div><div class="status {{x.cls}}">{{x.hunter_decision}}</div>
<div class="row"><span class="label">Hunter Status</span><span class="value">{{x.status}}</span></div><div class="row"><span class="label">Setup Detection</span><span class="value">{{x.detected}}</span></div><div class="row"><span class="label">Probability Score*</span><span class="value">{{x.score}}/100</span></div><div class="row"><span class="label">Trade Direction</span><span class="value">{{x.direction}}</span></div>
<div class="section">MARKET TARGET</div><div class="row"><span class="label">Underlying</span><span class="value">{{x.underlying}}</span></div><div class="row"><span class="label">PDH / PDL</span><span class="value">{{x.pdh}} / {{x.pdl}}</span></div><div class="row"><span class="label">VWAP</span><span class="value">{{x.vwap}}</span></div><div class="row"><span class="label">ATR(14)</span><span class="value">{{x.atr}}</span></div>
<div class="section">SETUP DETECTION</div><div class="row"><span class="label">Regime</span><span class="value">{{x.regime}}</span></div><div class="row"><span class="label">Level Event</span><span class="value">{{x.level_event}}</span></div><div class="row"><span class="label">Acceptance</span><span class="value">{{x.acceptance}}</span></div><div class="row"><span class="label">MTF Alignment</span><span class="value">{{x.mtf}}</span></div>
<div class="section">WEAPON SELECTED</div><div class="row"><span class="label">Contract</span><span class="value">{{x.contract}}</span></div><div class="row"><span class="label">Premium</span><span class="value">{{x.premium}}</span></div><div class="row"><span class="label">Delta</span><span class="value">{{x.delta}}</span></div><div class="row"><span class="label">OI / ΔOI</span><span class="value">{{x.oi}} / {{x.oi_change}}</span></div><div class="row"><span class="label">Volume</span><span class="value">{{x.volume}}</span></div><div class="row"><span class="label">Bid / Ask</span><span class="value">{{x.bid}} / {{x.ask}}</span></div><div class="row"><span class="label">Option Quality</span><span class="value">{{x.opt_score}}/100</span></div>
<div class="section">EXECUTION</div><div class="row"><span class="label">Entry</span><span class="value">{{x.entry}}</span></div><div class="row"><span class="label">Stop Loss</span><span class="value">{{x.sl}}</span></div><div class="row"><span class="label">Target</span><span class="value">{{x.tp}}</span></div><div class="row"><span class="label">Risk : Reward</span><span class="value">{{x.rr}}</span></div>
<div class="section">HUNTING REASON</div><div class="small">{{x.reason}}</div><div class="footer">* Evidence-weighted research score, not a calibrated win probability. This system does not place orders.</div></div>{% endfor %}</div></div></body></html>'''

LOCK=threading.Lock(); STATE={"scan_time":None,"items":[],"session":{}}

def now(): return datetime.now(IST)
def fmt(v,d=2): return "—" if v is None else f"{v:,.{d}f}"

def session_state(t):
    if t.weekday()>=5:return {"status":"MARKET CLOSED — WEEKEND","detail":"HUNTER idle. It will resume on the next trading session."}
    if t.time()<OPEN:return {"status":"MARKET CLOSED — PRE-OPEN","detail":"Waiting for 09:15 IST."}
    if t.time()<SCAN_START:return {"status":"MARKET OPEN — BUILDING CONTEXT","detail":"Hunter eligibility gate opens at 09:30 IST."}
    if t.time()>CLOSE:return {"status":"MARKET CLOSED — SESSION COMPLETE","detail":"Hunter stopped scanning at 15:40 IST."}
    return {"status":"HUNTER ACTIVE — SCANNING","detail":"Waiting only for its own qualified setup rules."}

def get_json(path):
    req=urllib.request.Request(BRIDGE+path,headers={"Accept":"application/json"})
    with urllib.request.urlopen(req,timeout=20) as r:return json.loads(r.read().decode())

def candles(obj):
    out=[]
    def walk(v):
        if isinstance(v,dict):
            if all(k in v for k in ("open","high","low","close")):
                try:
                    ts=v.get("timestamp") or v.get("time") or v.get("datetime")
                    if ts is None:return
                    d=datetime.fromisoformat(ts.replace("Z","+00:00")).astimezone(IST) if isinstance(ts,str) and not ts.isdigit() else datetime.fromtimestamp(int(ts),IST)
                    out.append({"dt":d,"open":float(v["open"]),"high":float(v["high"]),"low":float(v["low"]),"close":float(v["close"]),"volume":float(v.get("volume") or 0)})
                except Exception:pass
            for z in v.values():walk(z)
        elif isinstance(v,list):
            for z in v:walk(z)
    walk(obj);return sorted({x["dt"]:x for x in out}.values(),key=lambda x:x["dt"])

def atr(c,n=14):
    if len(c)<n+1:return None
    tr=[];prev=None
    for x in c:
        tr.append(x["high"]-x["low"] if prev is None else max(x["high"]-x["low"],abs(x["high"]-prev),abs(x["low"]-prev)));prev=x["close"]
    return sum(tr[-n:])/n

def vwap(c):
    pv=vol=0
    for x in c:
        q=(x["high"]+x["low"]+x["close"])/3;pv+=q*x["volume"];vol+=x["volume"]
    return pv/vol if vol else None

def ema(c,n=9):
    if not c:return None
    e=c[0];a=2/(n+1)
    for x in c[1:]:e=a*x+(1-a)*e
    return e

def options(raw):
    strikes=((raw.get("option_chain") or {}).get("strikes") or {});out=[]
    for k,s in strikes.items():
        try:st=float(s.get("strike",k))
        except:continue
        for side in ("CE","PE"):
            l=s.get(side) or {};p=l.get("last_price")
            if p is None:continue
            try:p=float(p)
            except:continue
            g=l.get("greeks") or {}
            def n(k):
                try:return float(l[k]) if l.get(k) is not None else None
                except:return None
            try:d=float(g["delta"]) if g.get("delta") is not None else None
            except:d=None
            out.append({"strike":st,"side":side,"ltp":p,"oi":n("oi"),"oi_change":n("oi_change"),"volume":n("volume"),"bid":n("top_bid_price"),"ask":n("top_ask_price"),"delta":d})
    return out

def option_quality(o,under,a):
    s=0;dist=abs(o["strike"]-under)
    s+=20 if a and dist<=.75*a else 14 if a and dist<=1.5*a else 4
    if o["bid"] is not None and o["ask"] is not None and o["ltp"]>0:
        sp=(o["ask"]-o["bid"])/o["ltp"]
        s+=20 if sp<=.01 else 14 if sp<=.02 else 6 if sp<=.04 else 0
    d=abs(o["delta"]) if o["delta"] is not None else None
    s+=20 if d is not None and .40<=d<=.65 else 14 if d is not None and .30<=d<=.75 else 5
    if o["volume"] and o["volume"]>0:s+=10
    if o["oi"] and o["oi"]>0:s+=10
    if o["ltp"]>=50:s+=10
    return min(s,100)

def choose(raw,under,direction,a):
    side="CE" if direction=="LONG" else "PE";rows=[o for o in options(raw) if o["side"]==side]
    ranked=sorted(((option_quality(o,under,a),o) for o in rows),key=lambda z:z[0],reverse=True)
    return ranked[0] if ranked else (0,None)

def analyse(name,path):
    raw=get_json(path);m=raw.get("market") or raw;allc=candles(m);t=now();today=[x for x in allc if x["dt"].date()==t.date()];prior=[x for x in allc if x["dt"].date()<t.date()]
    dates=sorted({x["dt"].date() for x in prior});prev=[x for x in prior if dates and x["dt"].date()==dates[-1]];pdh=max([x["high"] for x in prev],default=None);pdl=min([x["low"] for x in prev],default=None);under=today[-1]["close"] if today else (allc[-1]["close"] if allc else None);a=atr(allc);vw=vwap(today)
    tfs=m.get("timeframes") or {};c5=candles(tfs.get("5M") or []);c15=candles(tfs.get("15M") or []);c1h=candles(tfs.get("1H") or []);last5=c5[-1] if c5 else None;prev5=c5[-2] if len(c5)>1 else None
    direction=None;score=0;accept="NOT TESTED";events=[];regime="UNDEFINED";mtf="NOT ALIGNED"
    if under is not None:
        votes=sum([bool(c5 and under>ema([x["close"] for x in c5])),bool(c15 and under>ema([x["close"] for x in c15])),bool(c1h and under>ema([x["close"] for x in c1h]))])
        regime="BULLISH" if votes>=2 else "BEARISH" if votes<=1 else "MIXED"
    if last5 and pdh is not None and a and last5["close"]>pdh and last5["close"]-pdh>=.20*a:
        direction="LONG";events.append("PDH DISPLACEMENT");accept="CONFIRMED" if (prev5 and prev5["close"]>pdh) or last5["low"]>pdh else "PENDING"
    elif last5 and pdl is not None and a and last5["close"]<pdl and pdl-last5["close"]>=.20*a:
        direction="SHORT";events.append("PDL DISPLACEMENT");accept="CONFIRMED" if (prev5 and prev5["close"]<pdl) or last5["high"]<pdl else "PENDING"
    if direction=="LONG" and vw and under>vw:score+=15
    if direction=="SHORT" and vw and under<vw:score+=15
    if direction and accept=="CONFIRMED":score+=30;events.append("ACCEPTANCE")
    elif direction and accept=="PENDING":score+=10
    def above(c):return bool(c and under>ema([x["close"] for x in c]))
    if direction=="LONG" and all(above(c) for c in (c5,c15,c1h)):score+=20;mtf="ALIGNED LONG"
    elif direction=="SHORT" and all(not above(c) for c in (c5,c15,c1h)):score+=20;mtf="ALIGNED SHORT"
    if direction and pdh is not None and pdl is not None:score+=15;events.append("KEY LEVEL")
    opt_score,opt=choose(raw,under,direction,a) if direction and under is not None else (0,None)
    if opt and opt_score>=75:score+=20;events.append("OPTION CONFIRMATION")
    score=min(score,100);detected=bool(direction and accept=="CONFIRMED" and mtf.startswith("ALIGNED") and opt and opt_score>=75 and score>=QUALIFY_SCORE)
    hunter="🔥 FIRE — EXECUTION SIGNAL" if detected else "🟡 WAIT — CONFIRMATION REQUIRED" if direction else "⚪ WAIT — NO QUALIFIED TARGET"
    cls="green" if detected else "yellow" if direction else "blue";entry=sl=tp=rr="—"
    if detected:
        entry=opt["ask"] if opt["ask"] and opt["ask"]>0 else opt["ltp"];invalid=pdl if direction=="LONG" else pdh;ur=abs(under-invalid) if invalid is not None else (a*.20 if a else under*.001);d=abs(opt["delta"]) if opt["delta"] else .5;pr=ur*d;sl=round(max(0,entry-pr),2);risk=entry-sl;tp=round(entry+risk*2,2);rr="1 : 2"
    return {"name":name,"generated":t.strftime("%d %b %Y %H:%M:%S IST"),"hunter_decision":hunter,"status":"HUNTER ACTIVE" if detected else "HUNTING","detected":"YES" if detected else "NO","score":score,"direction":direction or "—","underlying":fmt(under),"pdh":fmt(pdh),"pdl":fmt(pdl),"vwap":fmt(vw),"atr":fmt(a),"regime":regime,"level_event":" + ".join(events) if events else "NONE","acceptance":accept,"mtf":mtf,"contract":(fmt(opt["strike"],0)+" "+opt["side"]) if opt else "—","premium":fmt(opt["ltp"]) if opt else "—","delta":fmt(opt["delta"]) if opt else "—","oi":fmt(opt["oi"],0) if opt else "—","oi_change":fmt(opt["oi_change"],0) if opt else "—","volume":fmt(opt["volume"],0) if opt else "—","bid":fmt(opt["bid"]) if opt else "—","ask":fmt(opt["ask"]) if opt else "—","opt_score":opt_score,"entry":fmt(entry),"sl":fmt(sl),"tp":fmt(tp),"rr":rr,"reason":" + ".join(events) if events else "No qualified setup. Hunter is waiting." ,"cls":cls}

def closed(name):
    return {"name":name,"hunter_decision":"⚪ WAIT — MARKET CLOSED","status":"HUNTER IDLE","detected":"NO","score":0,"direction":"—","underlying":"—","pdh":"—","pdl":"—","vwap":"—","atr":"—","regime":"—","level_event":"—","acceptance":"—","mtf":"—","contract":"—","premium":"—","delta":"—","oi":"—","oi_change":"—","volume":"—","bid":"—","ask":"—","opt_score":0,"entry":"—","sl":"—","tp":"—","rr":"—","reason":"Hunter is outside its trading session.","cls":"blue"}

def scan():
    t=now();ss=session_state(t)
    if t.weekday()>=5 or t.time()<OPEN or t.time()>CLOSE or t.time()<SCAN_START:items=[closed("NIFTY"),closed("BANK NIFTY")]
    else:
        items=[]
        for n,p in (("NIFTY","/nifty-live"),("BANK NIFTY","/banknifty-live")):
            try:items.append(analyse(n,p))
            except Exception as e:
                x=closed(n);x["hunter_decision"]="🟡 BRIDGE UNAVAILABLE";x["status"]="HUNTER WAITING";x["reason"]=str(e);x["cls"]="yellow";items.append(x)
    with LOCK:STATE.update(scan_time=t.strftime("%d %b %Y %H:%M:%S IST"),items=items,session=ss)

def worker():
    while True:
        try:scan()
        except Exception as e:print("HUNTER ERROR",e,flush=True)
        time.sleep(SCAN_SECONDS)
threading.Thread(target=worker,daemon=True).start()

@app.route("/")
def home():
    with LOCK:data=json.loads(json.dumps(STATE))
    return render_template_string(HTML,items=data["items"],session=data["session"],scan_time=data["scan_time"] or "starting")
@app.route("/health")
def health():return jsonify({"status":"ok","service":"psycho-hunter-engine","last_scan":STATE["scan_time"],"session":session_state(now())})
@app.route("/api/state")
def api_state():return jsonify(STATE)

if __name__=="__main__":app.run(host="0.0.0.0",port=10000)
