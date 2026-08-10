import json, threading, time, urllib.request
from datetime import datetime, time as T, timedelta
from zoneinfo import ZoneInfo
from flask import Flask, jsonify, render_template_string

app = Flask(__name__)
IST = ZoneInfo("Asia/Kolkata")
BR = "https://psycho-market-bridge.onrender.com"
START, CLOSE = T(9, 15), T(15, 40)
SCAN = 15
MIN_SCORE = 78
LOCK = threading.RLock()

BOOK = {}
STATE = {"scan_time": None, "session": {}, "items": [], "trades": [], "hunts": []}

HTML = r'''<!doctype html>
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="15">
<style>
body{font-family:Arial;background:#080b12;color:#eef2f7;padding:18px}.wrap{max-width:1350px;margin:auto}
.banner,.card,.trades,.hunts{background:#111722;border:1px solid #293241;border-radius:16px;padding:16px;margin:12px 0}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}@media(max-width:850px){.grid{grid-template-columns:1fr}}
.row{display:flex;justify-content:space-between;padding:7px 0;border-bottom:1px solid #242c38}
.good{color:#54e39a}.bad{color:#ff6f7d}.warn{color:#ffd166}.blue{color:#6db8ff}.orange{color:#ffad5a}
.muted{color:#8995a5;font-size:12px}.reason{background:#0b1018;padding:10px;border-radius:10px;margin-top:10px;line-height:1.5;font-size:13px}
table{width:100%;border-collapse:collapse;font-size:12px}td,th{padding:8px;border-bottom:1px solid #29313d;text-align:left}
</style>
<div class="wrap">
<div class="banner">
<h1>PSYCHO INTRADAY OPPORTUNITY ENGINE</h1>
<h3>{{session.status}}</h3>
<div class="muted">{{session.detail}} • {{scan_time or 'waiting'}} • 15s scan</div>
</div>
<div class="grid">
{%for x in items%}
<div class="card">
<h2>{{x.name}}</h2>
<h3 class="{{x.cls}}">{{x.status}}</h3>
<div class="row"><span>State</span><b>{{x.state}}</b></div>
<div class="row"><span>Direction</span><b>{{x.direction}}</b></div>
<div class="row"><span>Setup</span><b>{{x.setup}}</b></div>
<div class="row"><span>Signal</span><b>{{x.signal_id}}</b></div>
<div class="row"><span>Contract</span><b>{{x.contract}}</b></div>
<div class="row"><span>Entry / Actual LTP</span><b>{{x.entry}}</b></div>
<div class="row"><span>Bid / Ask</span><b>{{x.bid}} / {{x.ask}}</b></div>
<div class="row"><span>SL</span><b>{{x.sl}}</b></div>
<div class="row"><span>TP</span><b>{{x.tp}}</b></div>
<div class="row"><span>Live premium</span><b>{{x.live}}</b></div>
<div class="row"><span>Score</span><b>{{x.score}}/100</b></div>
<div class="reason"><b>WHY</b><br>{{x.reason}}</div>
</div>
{%endfor%}
</div>
<div class="trades">
<h2>TODAY'S CLOSED TRADES</h2>
{%if trades%}
<table><tr><th>Exit</th><th>Instrument</th><th>Signal</th><th>Setup</th><th>Contract</th><th>Entry</th><th>Exit</th><th>Result</th></tr>
{%for t in trades%}<tr><td>{{t.exit_time}}</td><td>{{t.name}}</td><td>{{t.signal_id}}</td><td>{{t.setup}}</td><td>{{t.contract}}</td><td>{{t.entry}}</td><td>{{t.exit}}</td><td class="{{'good' if t.result=='TP TAKEN' else 'bad'}}">{{t.result}}</td></tr>{%endfor%}
</table>
{%else%}<div class="muted">No completed trades today.</div>{%endif%}
</div>
<div class="hunts">
<h2>TODAY'S HUNT / MISSED OPPORTUNITIES</h2>
{%if hunts%}
<table><tr><th>Time</th><th>Instrument</th><th>Setup</th><th>Direction</th><th>Score</th><th>Outcome</th></tr>
{%for h in hunts[-20:]%}<tr><td>{{h.time}}</td><td>{{h.name}}</td><td>{{h.setup}}</td><td>{{h.direction}}</td><td>{{h.score}}</td><td>{{h.outcome}}</td></tr>{%endfor%}
</table>
{%else%}<div class="muted">No rejected or missed hunts recorded today.</div>{%endif%}
</div>
</div>'''

def now():
    return datetime.now(IST)

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

def dt(v):
    try:
        s = str(v)
        if s.isdigit():
            n = int(s)
            return datetime.fromtimestamp(n / 1000 if n > 10**11 else n, IST)
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(IST)
    except Exception:
        return None

def get(path):
    req = urllib.request.Request(BR + path, headers={"Accept":"application/json","User-Agent":"PSYCHO-IOE/5.0"})
    with urllib.request.urlopen(req, timeout=12) as r:
        return json.loads(r.read().decode())

def candles(obj):
    out = []
    def walk(v):
        if isinstance(v, dict):
            if all(k in v for k in ("open","high","low","close")):
                d = dt(v.get("timestamp") or v.get("time") or v.get("datetime") or v.get("date"))
                if d:
                    try:
                        out.append({"dt":d,"open":float(v["open"]),"high":float(v["high"]),"low":float(v["low"]),"close":float(v["close"]),"volume":num(v.get("volume")) or 0})
                    except Exception:
                        pass
            for z in v.values(): walk(z)
        elif isinstance(v, list):
            for z in v: walk(z)
    walk(obj)
    return sorted({x["dt"]:x for x in out}.values(), key=lambda x:x["dt"])

def tf(obj, key):
    tfs = (obj.get("timeframes") or obj.get("candles") or {}) if isinstance(obj, dict) else {}
    return candles(tfs.get(key, []))

def atr(c, n=14):
    if len(c) < n + 1: return None
    tr=[]; prev_close=None
    for x in c:
        tr.append(x["high"]-x["low"] if prev_close is None else max(x["high"]-x["low"],abs(x["high"]-prev_close),abs(x["low"]-prev_close)))
        prev_close=x["close"]
    return sum(tr[-n:])/n

def ema(values, n=20):
    if not values: return None
    e=values[0]; a=2/(n+1)
    for x in values[1:]: e=a*x+(1-a)*e
    return e

def vwap(c):
    pv=vol=0
    for x in c:
        q=x["volume"]; pv+=((x["high"]+x["low"]+x["close"])/3)*q; vol+=q
    return pv/vol if vol else None

def rng(x): return max(x["high"]-x["low"],0.00001)
def body(x): return abs(x["close"]-x["open"])
def bull(x): return x["close"]>x["open"]
def bear(x): return x["close"]<x["open"]

def option_rows(raw):
    if not isinstance(raw,dict): return []
    n=raw.get("option_chain") or raw.get("data") or raw
    strikes=n.get("strikes") if isinstance(n,dict) else None
    pairs=list(strikes.items()) if isinstance(strikes,dict) else ([(x.get("strike"),x) for x in strikes] if isinstance(strikes,list) else [])
    out=[]
    for k,v in pairs:
        if not isinstance(v,dict): continue
        st=num(v.get("strike",k))
        for side in ("CE","PE"):
            q=v.get(side) or v.get(side.lower()) or {}
            ltp=num(q.get("last_price") or q.get("ltp") or q.get("lastPrice"))
            if st is None or ltp is None or ltp<=0: continue
            g=q.get("greeks") or {}
            out.append({"strike":st,"side":side,"ltp":ltp,"delta":num(g.get("delta") or q.get("delta")),"bid":num(q.get("top_bid_price") or q.get("bid")),"ask":num(q.get("top_ask_price") or q.get("ask")),"oi":num(q.get("oi") or q.get("open_interest")),"volume":num(q.get("volume"))})
    return out

def bundle(name):
    slug="nifty" if name=="NIFTY" else "banknifty"
    return get("/"+slug+"-live"), get("/"+slug+"-option-chain")

def pick(raw, underlying, direction):
    wanted="CE" if direction=="LONG" else "PE"
    rows=[x for x in option_rows(raw) if x["side"]==wanted]
    ranked=[]
    for x in rows:
        score=0; delta=abs(x["delta"] or 0); dist=abs(x["strike"]-underlying)
        score += 25 if dist<=max(50,underlying*0.0025) else 12 if dist<=max(100,underlying*0.005) else 0
        score += 25 if 0.40<=delta<=0.65 else 15 if 0.30<=delta<=0.75 else 0
        ltp,bid,ask=x["ltp"],x["bid"],x["ask"]
        if bid is not None and ask is not None and ask>=bid>0:
            spread=(ask-bid)/max(ltp,0.01); gap=abs(ask-ltp)/max(ltp,0.01)
            if spread<=0.005 and gap<=0.008: score+=30
            elif spread<=0.01 and gap<=0.015: score+=20
            elif spread<=0.02 and gap<=0.025: score+=8
            else: score-=35
        else: score-=20
        score += 10 if (x["oi"] or 0)>0 else 0
        score += 10 if (x["volume"] or 0)>0 else 0
        ranked.append((max(0,min(100,score)),x))
    return max(ranked,key=lambda z:z[0]) if ranked else (0,None)

def prev(c,t):
    old=[x for x in c if x["dt"].date()<t.date()]
    if not old: return None
    d=max(x["dt"].date() for x in old); z=[x for x in old if x["dt"].date()==d]
    return {"high":max(x["high"] for x in z),"low":min(x["low"] for x in z),"close":z[-1]["close"]}

def opening(c,t):
    z=[x for x in c if x["dt"].date()==t.date() and T(9,15)<=x["dt"].time()<T(9,30)]
    return max((x["high"] for x in z),default=None), min((x["low"] for x in z),default=None)

def detect(name,raw,t):
    c=tf(raw,"1M") or candles(raw); c=[x for x in c if x["dt"]+timedelta(minutes=1)<=t]
    c5,c15,c1h=tf(raw,"5M"),tf(raw,"15M"),tf(raw,"1H")
    if len(c)<25: return None
    u,a=c[-1]["close"],atr(c)
    if a is None: return None
    pd=prev(c,t); orh,orl=opening(c,t); day=[x for x in c if x["dt"].date()==t.date()]
    vw=vwap(day); e5=ema([x["close"] for x in c5],20); e15=ema([x["close"] for x in c15],20); e1h=ema([x["close"] for x in c1h],20)
    x1,x2,x3=c[-1],c[-2],c[-3]; candidates=[]
    if pd:
        if x1["low"]<pd["low"] and x1["close"]>pd["low"]: candidates.append((82,"LONG","LIQUIDITY SWEEP + RECLAIM",["swept previous-day low","reclaimed the level","rejection close"]))
        if x1["high"]>pd["high"] and x1["close"]<pd["high"]: candidates.append((82,"SHORT","LIQUIDITY SWEEP + RECLAIM",["swept previous-day high","reclaimed the level","rejection close"]))
        if x2["high"]>pd["high"] and x2["close"]<pd["high"] and x1["close"]<x2["low"]: candidates.append((84,"SHORT","FAILED BREAKOUT",["PDH breakout failed","returned inside structure","confirmation candle"]))
        if x2["low"]<pd["low"] and x2["close"]>pd["low"] and x1["close"]>x2["high"]: candidates.append((84,"LONG","FAILED BREAKDOWN",["PDL breakdown failed","returned inside structure","confirmation candle"]))
    if orh is not None and orl is not None:
        if x1["close"]>orh and x2["close"]>orh: candidates.append((80,"LONG","OPENING RANGE CONTINUATION",["accepted above opening-range high","two-candle acceptance"]))
        if x1["close"]<orl and x2["close"]<orl: candidates.append((80,"SHORT","OPENING RANGE CONTINUATION",["accepted below opening-range low","two-candle acceptance"]))
    if e5 and e15:
        if u>e5 and u>e15 and x2["low"]<=e5*1.001 and bull(x1) and x1["close"]>x2["high"]: candidates.append((83,"LONG","PULLBACK CONTINUATION",["5M/15M trend aligned","pullback into mean","bullish rejection/continuation"]))
        if u<e5 and u<e15 and x2["high"]>=e5*0.999 and bear(x1) and x1["close"]<x2["low"]: candidates.append((83,"SHORT","PULLBACK CONTINUATION",["5M/15M trend aligned","pullback into mean","bearish rejection/continuation"]))
    r=c[-8:]; avg_range=sum(rng(z) for z in r[:-2])/6
    if rng(x3)<avg_range*.75 and rng(x2)<avg_range*.75 and rng(x1)>avg_range*1.25:
        d="LONG" if bull(x1) else "SHORT" if bear(x1) else None
        if d: candidates.append((82,d,"VOLATILITY CONTRACTION → EXPANSION",["range compression","expansion candle","directional release"]))
    if len(c)>=6:
        if all(bull(z) for z in c[-3:]) and u>max(z["high"] for z in c[-6:-3]): candidates.append((80,"LONG","TREND CONTINUATION",["bullish candle sequence","fresh continuation high"]))
        if all(bear(z) for z in c[-3:]) and u<min(z["low"] for z in c[-6:-3]): candidates.append((80,"SHORT","TREND CONTINUATION",["bearish candle sequence","fresh continuation low"]))
    avg_vol=sum(z["volume"] for z in c[-21:-1])/20 if len(c)>=21 else 0
    if avg_vol and x1["volume"]>=avg_vol*1.5 and body(x1)>=.45*a and body(x1)/rng(x1)>=.6:
        d="LONG" if bull(x1) else "SHORT" if bear(x1) else None
        if d: candidates.append((79,d,"PRICE-FLOW EXPANSION PROXY",["relative volume expansion","wide body","strong close location"]))
    if not candidates: return None
    candidates.sort(key=lambda z:z[0],reverse=True); base,direction,setup,why=candidates[0]
    mtf=sum(v is not None and ((u>v) if direction=="LONG" else (u<v)) for v in (e5,e15,e1h)); vw_ok=vw is not None and ((u>vw) if direction=="LONG" else (u<vw))
    score=min(100,base+mtf*4+(4 if vw_ok else 0)); why += [f"MTF alignment {mtf}/3","VWAP aligned" if vw_ok else "VWAP not aligned"]
    return {"direction":direction,"setup":setup,"score":score,"why":why,"u":u,"atr":a}

def blank(name,status="SCANNING",reason="No qualified setup."):
    return {"name":name,"signal_id":"—","state":"SCANNING","status":status,"cls":"blue","direction":"—","setup":"NONE","contract":"—","entry":"—","bid":"—","ask":"—","sl":"—","tp":"—","live":"—","score":0,"reason":reason}

def build_signal(name,direction,opt,quality,t,det):
    entry=opt["ltp"]
    risk=max(entry*.10,det["atr"]*max(abs(opt.get("delta") or .5),.35)*.20); sl=round(max(.05,entry-risk),2); tp=round(entry+2*(entry-sl),2)
    sid=f"IOE-{name.replace(' ','')}-{t:%Y%m%d-%H%M%S}"
    return {"name":name,"signal_id":sid,"state":"ACTIVE","status":"🟢 SIGNAL ACTIVE — LOCKED","cls":"good","direction":direction,"setup":det["setup"],"contract":f"{int(opt['strike']):,} {opt['side']}","strike":opt["strike"],"side":opt["side"],"entry":fmt(entry),"entry_raw":entry,"bid":fmt(opt["bid"]),"ask":fmt(opt["ask"]),"sl":fmt(sl),"sl_raw":sl,"tp":fmt(tp),"tp_raw":tp,"live":fmt(entry),"score":det["score"],"reason":" • ".join(det["why"]+[f"option quality {quality}/100","ENTRY = CURRENT OPTION LTP","actual quote verified before activation","option locked; no reselection"]),"created":t.isoformat()}

def monitor(name,t):
    s=BOOK[name]["active"]; slug="nifty" if name=="NIFTY" else "banknifty"
    try: raw=get("/"+slug+"-option-chain")
    except Exception: s["status"]="🟢 SIGNAL ACTIVE — QUOTE FETCH RETRY"; return s
    rows=[x for x in option_rows(raw) if x["side"]==s["side"] and abs(x["strike"]-s["strike"])<.01]
    if not rows: return s
    q=rows[0]; live=q["ltp"]; s["live"]=fmt(live); s["bid"],s["ask"]=fmt(q["bid"]),fmt(q["ask"])
    result=None
    if live>=s["tp_raw"]: result="TP TAKEN"
    elif live<=s["sl_raw"]: result="SL TRIGGERED"
    elif t.time()>=CLOSE: result="SESSION CLOSED"
    if result:
        trade={"exit_time":t.strftime("%H:%M:%S IST"),"name":name,"signal_id":s["signal_id"],"setup":s["setup"],"contract":s["contract"],"entry":s["entry"],"exit":fmt(live),"result":result}
        BOOK[name]["trades"].append(trade); BOOK[name]["active"]=None; BOOK[name]["mode"]="PULLBACK_ONLY"; BOOK[name]["last"]=trade; return None
    return s

def session_info(t):
    if t.time()<START: return {"status":"PRE-MARKET","detail":"Building context; no trades before 09:15 IST."}
    if t.time()>CLOSE: return {"status":"MARKET CLOSED","detail":"Session finished; today's closed trades remain visible."}
    return {"status":"MARKET LIVE","detail":"15-second engine scan • current quote required for activation."}

def scan():
    t=now(); sess=session_info(t)
    with LOCK:
        for name in ("NIFTY","BANK NIFTY"):
            if BOOK.get(name,{}).get("date")!=t.date(): BOOK[name]={"date":t.date(),"active":None,"last":None,"mode":"INITIAL","trades":[],"hunts":[]}
        items=[]
        for name in ("NIFTY","BANK NIFTY"):
            b=BOOK[name]
            try:
                if t.time()<START: item=blank(name,"BUILDING CONTEXT","Waiting for the opening session.")
                elif t.time()>CLOSE: item=blank(name,"MARKET CLOSED","Session ended; closed trades remain in today's ledger.")
                elif b["active"]:
                    item=monitor(name,t)
                    if b["active"] is None: item=blank(name,"🟡 PULLBACK HUNTING","Previous trade closed. New entries are pullback-only.")
                else:
                    raw,opt=bundle(name); det=detect(name,raw,t)
                    if b["mode"]=="PULLBACK_ONLY" and (not det or det["setup"] not in ("PULLBACK CONTINUATION","LIQUIDITY SWEEP + RECLAIM","FAILED BREAKOUT","FAILED BREAKDOWN")):
                        item=blank(name,"🟡 PULLBACK HUNTING","Previous trade closed. Waiting for a pullback/rejection structure.")
                    elif not det: item=blank(name,"SCANNING","No qualifying setup detected.")
                    elif det["score"]<MIN_SCORE:
                        item={**blank(name,"WATCH — CONFLUENCE BELOW THRESHOLD","Setup detected but combined confirmation is below the entry threshold."),"direction":det["direction"],"setup":det["setup"],"score":det["score"],"reason":" • ".join(det["why"])}
                    else:
                        quality,opt_row=pick(opt,det["u"],det["direction"])
                        if not opt_row or quality<75:
                            item={**blank(name,"WATCH — OPTION QUOTE/LIQUIDITY INSUFFICIENT","Underlying setup qualifies, but the selected option quote is not tradeable enough."),"direction":det["direction"],"setup":det["setup"],"score":det["score"],"reason":" • ".join(det["why"])+" • option quote rejected"}
                        else:
                            b["active"]=build_signal(name,det["direction"],opt_row,quality,t,det); b["mode"]="ACTIVE"; item=b["active"]
            except Exception as e: item={**blank(name,"DATA ERROR","No signal fabricated from incomplete/broken data."),"cls":"warn","reason":str(e)}
            items.append(item)
        STATE.update(scan_time=t.strftime("%d %b %Y %H:%M:%S IST"),session=sess,items=items,trades=sum((b["trades"] for b in BOOK.values()),[]),hunts=sum((b["hunts"] for b in BOOK.values()),[]))

def worker():
    while True:
        try: scan()
        except Exception as e: print("IOE ERROR",e,flush=True)
        time.sleep(SCAN)

threading.Thread(target=worker,daemon=True).start()

@app.route("/")
def home():
    with LOCK: d=json.loads(json.dumps(STATE,default=str))
    return render_template_string(HTML,**d)

@app.route("/health")
def health(): return jsonify({"status":"ok","service":"psycho-opportunity-engine-v5","last_scan":STATE["scan_time"]})

@app.route("/api/state")
def api_state(): return jsonify(STATE)

@app.route("/api/trades")
def api_trades(): return jsonify({"date":now().date().isoformat(),"trades":STATE["trades"]})

if __name__=="__main__": app.run(host="0.0.0.0",port=10000)
