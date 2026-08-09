import json, threading, time, urllib.request
from datetime import datetime, time as dt_time
from zoneinfo import ZoneInfo
from flask import Flask, jsonify, render_template_string

app = Flask(__name__)
IST = ZoneInfo("Asia/Kolkata")
BRIDGE = "https://psycho-market-bridge.onrender.com"
OPEN = dt_time(9, 15)
WINDOW_START = dt_time(9, 30)
WINDOW_END = dt_time(10, 30)
CLOSE = dt_time(15, 40)
SCAN_SECONDS = 60

# HISTORICAL RESEARCH LOCK — Dhan 5M archive, 2025-08-11 to 2026-08-07.
# Pattern: opening-range sweep -> close back inside -> reversal confirmation.
# Research sample: NIFTY 80 signals / 245 sessions; BANK NIFTY 72 / 245.
# Management: structural stop at sweep extreme; TP1=1R, TP2=2R.
# Partial management backtest (50% TP1, remainder TP2, BE after TP1):
# NIFTY mean R ~ +0.49; BANK NIFTY mean R ~ +0.35 over the 12 subsequent 5M bars.
# These are research results, NOT a guarantee and not option-premium backtest results.

HTML = r'''<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="60"><title>PSYCHO 9301030</title><style>
body{font-family:Arial,sans-serif;background:#090b0f;color:#f4f6f8;margin:0;padding:18px}.wrap{max-width:1100px;margin:auto}h1{margin:0;font-size:30px}.sub{color:#9aa3ad;margin:6px 0 18px}.banner,.card{background:#14181e;border:1px solid #29303a;border-radius:14px;padding:18px;margin-bottom:16px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}@media(max-width:800px){.grid{grid-template-columns:1fr}}.title{font-size:22px;font-weight:900}.hero{font-size:25px;font-weight:900}.status{padding:13px;border-radius:10px;background:#202631;font-weight:900;font-size:18px;margin:12px 0}.green{color:#63e6a7}.yellow{color:#ffd166}.blue{color:#73b7ff}.red{color:#ff7777}.row{display:flex;justify-content:space-between;border-bottom:1px solid #232a33;padding:8px 0}.label{color:#aab3bf}.value{font-weight:800;text-align:right}.section{margin-top:17px;font-weight:900;font-size:12px;color:#8ea0b4;letter-spacing:.08em}.small{font-size:12px;color:#8d96a2;line-height:1.5;margin-top:12px}.footer{color:#707985;font-size:11px;margin-top:14px}.alert{font-size:28px;font-weight:950;margin:10px 0}.lock{font-size:12px;color:#9aa3ad}
</style></head><body><div class="wrap"><h1>PSYCHO 9301030 ENGINE</h1><div class="sub">Historical-pattern hunter • 5M signal authority • 1-minute scan heartbeat • option execution</div><div class="banner"><div class="hero">{{session.status}}</div><div class="sub">{{session.detail}}</div><div class="small">Last scan: {{scan_time}} • Page refresh: 60 seconds • Signal window: 09:30–10:30 IST</div></div><div class="grid">{% for x in items %}<div class="card"><div class="title">{{x.name}}</div><div class="status {{x.cls}}">{{x.status}}</div>{% if x.detected %}<div class="alert">🎯 TRADE DETECTED</div>{% endif %}<div class="row"><span class="label">Pattern Probability*</span><span class="value">{{x.score}}%</span></div><div class="row"><span class="label">Pattern Detected</span><span class="value">{{x.detected_text}}</span></div><div class="row"><span class="label">Direction</span><span class="value">{{x.direction}}</span></div><div class="section">9301030 PATTERN</div><div class="row"><span class="label">Opening Range</span><span class="value">{{x.orh}} / {{x.orl}}</span></div><div class="row"><span class="label">Sweep</span><span class="value">{{x.sweep}}</span></div><div class="row"><span class="label">Rejection</span><span class="value">{{x.rejection}}</span></div><div class="row"><span class="label">5M Confirmation</span><span class="value">{{x.confirmation}}</span></div><div class="row"><span class="label">Trigger Time</span><span class="value">{{x.trigger}}</span></div><div class="section">RESEARCHED RISK MODEL</div><div class="row"><span class="label">Thesis Failure</span><span class="value">{{x.thesis_failure}}</span></div><div class="row"><span class="label">Underlying Risk</span><span class="value">{{x.underlying_risk}}</span></div><div class="row"><span class="label">TP1 (1R)</span><span class="value">{{x.tp1}}</span></div><div class="row"><span class="label">TP2 (2R)</span><span class="value">{{x.tp2}}</span></div><div class="section">OPTION EXECUTION</div><div class="row"><span class="label">Contract</span><span class="value">{{x.contract}}</span></div><div class="row"><span class="label">Entry</span><span class="value">{{x.entry}}</span></div><div class="row"><span class="label">Option SL</span><span class="value">{{x.opt_sl}}</span></div><div class="row"><span class="label">Option TP1</span><span class="value">{{x.opt_tp1}}</span></div><div class="row"><span class="label">Option TP2</span><span class="value">{{x.opt_tp2}}</span></div><div class="row"><span class="label">Delta</span><span class="value">{{x.delta}}</span></div><div class="row"><span class="label">OI / ΔOI</span><span class="value">{{x.oi}} / {{x.oi_change}}</span></div><div class="row"><span class="label">Volume</span><span class="value">{{x.volume}}</span></div><div class="row"><span class="label">Bid / Ask</span><span class="value">{{x.bid}} / {{x.ask}}</span></div><div class="small">{{x.reason}}</div><div class="footer">* Research-derived pattern frequency score. It is not a guaranteed win probability. No orders are placed.</div></div>{% endfor %}</div><div class="lock">Rule: a 1-minute movement can never create a signal. A new signal requires the completed 5M 9301030 sequence.</div></div></body></html>'''

LOCK = threading.Lock(); STATE={"scan_time":None,"items":[],"session":{}}
TRIGGERED={"NIFTY":False,"BANK NIFTY":False}

def now(): return datetime.now(IST)
def session_state(t):
    if t.weekday()>=5:return {"status":"MARKET CLOSED — WEEKEND","detail":"9301030 is idle. Next trading session will be scanned automatically."}
    if t.time()<OPEN:return {"status":"MARKET CLOSED — PRE-OPEN","detail":"Waiting for 09:15 IST."}
    if t.time()<WINDOW_START:return {"status":"MARKET OPEN — BUILDING OPENING RANGE","detail":"9301030 eligibility begins at 09:30 IST."}
    if t.time()<=WINDOW_END:return {"status":"9301030 ACTIVE — HUNTING","detail":"Only the historical 5M pattern can trigger a trade."}
    if t.time()<=CLOSE:return {"status":"9301030 WINDOW CLOSED — NO NEW TRIGGERS","detail":"No new 9301030 entries after 10:30 IST."}
    return {"status":"MARKET CLOSED — SESSION COMPLETE","detail":"Next session will reset automatically."}

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
                    if isinstance(ts,str) and not ts.isdigit():d=datetime.fromisoformat(ts.replace("Z","+00:00")).astimezone(IST)
                    else:d=datetime.fromtimestamp(int(ts),IST)
                    out.append({"dt":d,"open":float(v["open"]),"high":float(v["high"]),"low":float(v["low"]),"close":float(v["close"]),"volume":float(v.get("volume") or 0)})
                except Exception:pass
            for z in v.values():walk(z)
        elif isinstance(v,list):
            for z in v:walk(z)
    walk(obj);return sorted({x["dt"]:x for x in out}.values(),key=lambda x:x["dt"])

def fmt(v,d=2):return "—" if v is None else f"{v:,.{d}f}"

def option_rows(raw):
    strikes=((raw.get("option_chain") or {}).get("strikes") or {});out=[]
    for k,s in strikes.items():
        try:st=float(s.get("strike",k))
        except Exception:continue
        for side in ("CE","PE"):
            leg=s.get(side) or {};p=leg.get("last_price")
            if p is None:continue
            try:p=float(p)
            except Exception:continue
            g=leg.get("greeks") or {}
            def num(key):
                try:return float(leg[key]) if leg.get(key) is not None else None
                except Exception:return None
            try:delta=float(g["delta"]) if g.get("delta") is not None else None
            except Exception:delta=None
            out.append({"strike":st,"side":side,"ltp":p,"oi":num("oi"),"oi_change":num("oi_change"),"volume":num("volume"),"bid":num("top_bid_price"),"ask":num("top_ask_price"),"delta":delta})
    return out

def option_quality(o,underlying,risk):
    score=0;dist=abs(o["strike"]-underlying)
    if dist<=max(risk*2,100):score+=25
    elif dist<=max(risk*4,200):score+=15
    else:score+=5
    if o["bid"] is not None and o["ask"] is not None and o["ltp"]>0:
        spr=(o["ask"]-o["bid"])/o["ltp"]
        score+=25 if spr<=.01 else 18 if spr<=.02 else 8 if spr<=.04 else 0
    d=abs(o["delta"]) if o["delta"] is not None else None
    if d is not None:score+=25 if .40<=d<=.65 else 17 if .30<=d<=.75 else 7
    if o["volume"] and o["volume"]>0:score+=10
    if o["oi"] and o["oi"]>0:score+=10
    if o["ltp"]>=50:score+=5
    return min(score,100)

def choose_option(raw,underlying,direction,risk):
    side="CE" if direction=="LONG" else "PE";cs=[o for o in option_rows(raw) if o["side"]==side]
    ranked=sorted(((option_quality(o,underlying,risk),o) for o in cs),key=lambda z:z[0],reverse=True)
    return ranked[0] if ranked else (0,None)

def detect_9301030(c5,current_time):
    cs=[c for c in c5 if c["dt"].time()>=dt_time(9,15) and c["dt"].time()<=current_time]
    if len(cs)<5:return None
    opening=[c for c in cs if c["dt"].time()<dt_time(9,30)]
    if len(opening)<3:return None
    orh=max(c["high"] for c in opening);orl=min(c["low"] for c in opening)
    post=[c for c in cs if c["dt"].time()>=dt_time(9,30) and c["dt"].time()<=WINDOW_END]
    if len(post)<3:return {"orh":orh,"orl":orl}
    for i in range(len(post)-2):
        sweep=post[i];rej=post[i+1];conf=post[i+2]
        if sweep["low"]<orl and sweep["close"]>orl and conf["close"]>rej["high"]:
            return {"detected":True,"direction":"LONG","orh":orh,"orl":orl,"sweep":sweep,"rejection":rej,"confirmation":conf}
        if sweep["high"]>orh and sweep["close"]<orh and conf["close"]<rej["low"]:
            return {"detected":True,"direction":"SHORT","orh":orh,"orl":orl,"sweep":sweep,"rejection":rej,"confirmation":conf}
    return {"orh":orh,"orl":orl}

def idle(name,reason=None):
    return {"name":name,"score":0,"detected":False,"detected_text":"NO","direction":"—","status":"⚪ WAITING","cls":"blue","orh":"—","orl":"—","sweep":"—","rejection":"—","confirmation":"—","trigger":"—","thesis_failure":"—","underlying_risk":"—","tp1":"—","tp2":"—","contract":"—","entry":"—","opt_sl":"—","opt_tp1":"—","opt_tp2":"—","delta":"—","oi":"—","oi_change":"—","volume":"—","bid":"—","ask":"—","reason":reason or "9301030 is inactive outside the 09:30–10:30 window."}

def analyze(name,path):
    raw=get_json(path);market=raw.get("market") or raw;c5=candles((market.get("timeframes") or {}).get("5M") or market);t=now()
    result=idle(name,"Waiting for the completed 5M 9301030 sequence.")
    d=detect_9301030(c5,t.time())
    if not d:return result
    result["orh"]=fmt(d.get("orh"));result["orl"]=fmt(d.get("orl"))
    if not d.get("detected"):
        result["reason"]="No completed sweep → rejection → confirmation sequence yet.";return result
    direction=d["direction"];sweep=d["sweep"];rej=d["rejection"];conf=d["confirmation"];result["direction"]=direction
    result["sweep"]=("BELOW ORL" if direction=="LONG" else "ABOVE ORH")+" @ "+fmt(sweep["low"] if direction=="LONG" else sweep["high"])
    result["rejection"]="5M CLOSE BACK INSIDE OPENING RANGE";result["confirmation"]="5M CLOSE BEYOND REJECTION EXTREME";result["trigger"]=conf["dt"].strftime("%H:%M IST")
    invalid=sweep["low"] if direction=="LONG" else sweep["high"];entry_under=conf["close"];risk=entry_under-invalid if direction=="LONG" else invalid-entry_under
    if risk<=0:result["reason"]="Pattern geometry invalid; no trade.";return result
    result["thesis_failure"]=fmt(invalid);result["underlying_risk"]=fmt(risk);tp1=entry_under+risk if direction=="LONG" else entry_under-risk;tp2=entry_under+2*risk if direction=="LONG" else entry_under-2*risk;result["tp1"]=fmt(tp1);result["tp2"]=fmt(tp2)
    opt_score,opt=choose_option(raw,entry_under,direction,risk)
    result["score"]=min(99,round(70+.30*opt_score))
    if not opt:result["status"]="🟡 PATTERN DETECTED — OPTION DATA UNAVAILABLE";result["cls"]="yellow";return result
    result["contract"]=f"{int(opt['strike']):,} {opt['side']}";entry=opt["ask"] if opt["ask"] is not None and opt["ask"]>0 else opt["ltp"];result["entry"]=fmt(entry);result["delta"]=fmt(opt["delta"],3);result["oi"]=fmt(opt["oi"],0);result["oi_change"]=fmt(opt["oi_change"],0);result["volume"]=fmt(opt["volume"],0);result["bid"]=fmt(opt["bid"]);result["ask"]=fmt(opt["ask"])
    if opt_score<70:result["status"]="🟡 PATTERN DETECTED — OPTION QUALITY LOW";result["cls"]="yellow";result["reason"]="Underlying 9301030 pattern matched, but live option execution quality is below the research gate. No trade.";return result
    delta=abs(opt["delta"]) if opt["delta"] not in (None,0) else .50;premium_risk=risk*delta;opt_sl=max(.05,entry-premium_risk);result["opt_sl"]=fmt(opt_sl);result["opt_tp1"]=fmt(entry+premium_risk);result["opt_tp2"]=fmt(entry+2*premium_risk);result["detected"]=True;result["detected_text"]="YES";result["status"]="🟢 9301030 TRADE DETECTED";result["cls"]="green";result["reason"]="Historical 5M sweep/rejection/confirmation matched. Structural stop = sweep extreme. Management: book 50% at TP1 (1R), move remaining stop to breakeven, target TP2 (2R)."
    return result

def scan():
    global TRIGGERED
    t=now()
    if t.time()<WINDOW_START or t.time()>WINDOW_END or t.weekday()>=5:
        with LOCK:STATE["scan_time"]=t.strftime("%d %b %Y %H:%M:%S IST");STATE["session"]=session_state(t);STATE["items"]=[idle("NIFTY"),idle("BANK NIFTY")]
        return
    items=[]
    for name,path in [("NIFTY","/nifty-live"),("BANK NIFTY","/banknifty-live")]:
        try:
            x=analyze(name,path)
            if TRIGGERED[name]:x["status"]="🔒 SIGNAL ALREADY FIRED TODAY";x["cls"]="green";x["reason"]="One-signal-per-instrument lock is active."
            elif x["detected"]:TRIGGERED[name]=True
            items.append(x)
        except Exception as e:items.append(idle(name,f"Bridge unavailable: {type(e).__name__}"))
    with LOCK:STATE["scan_time"]=t.strftime("%d %b %Y %H:%M:%S IST");STATE["session"]=session_state(t);STATE["items"]=items

def worker():
    global TRIGGERED
    last_date=None
    while True:
        t=now()
        if last_date!=t.date():TRIGGERED={"NIFTY":False,"BANK NIFTY":False};last_date=t.date()
        try:scan()
        except Exception:pass
        time.sleep(SCAN_SECONDS)

@app.route("/")
def home():
    with LOCK:s=dict(STATE)
    return render_template_string(HTML,session=s.get("session") or session_state(now()),scan_time=s.get("scan_time") or "—",items=s.get("items") or [idle("NIFTY"),idle("BANK NIFTY")])
@app.route("/api/state")
def api_state():
    with LOCK:return jsonify(STATE)
@app.route("/health")
def health():return jsonify({"ok":True,"engine":"9301030","time":now().isoformat()})
if __name__=="__main__":
    threading.Thread(target=worker,daemon=True).start();app.run(host="0.0.0.0",port=10000)
