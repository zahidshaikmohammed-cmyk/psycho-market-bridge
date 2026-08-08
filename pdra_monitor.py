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
DISPLACEMENT_ATR = 0.20
TARGET_DELTA = 0.50
MIN_DELTA = 0.30
MAX_DELTA = 0.70
MAX_SPREAD_PCT = 0.025

HTML = '''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>PSYCHO PDRA Option Execution Monitor</title><style>
body{font-family:Arial,sans-serif;background:#0b0d10;color:#f3f4f6;margin:0;padding:24px}.wrap{max-width:1150px;margin:auto}h1{margin:0 0 6px;font-size:28px}.sub{color:#9ca3af;margin-bottom:22px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:18px}@media(max-width:850px){.grid{grid-template-columns:1fr}}.card,.banner{background:#14181d;border:1px solid #2a3038;border-radius:14px;padding:20px}.banner{margin-bottom:18px}.banner-title{font-size:22px;font-weight:800}.banner-sub{color:#9ca3af;margin-top:6px}.title{font-size:20px;font-weight:700}.time{color:#9ca3af;font-size:13px;margin:5px 0 16px}.row{display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid #222831}.label{color:#aeb6c2}.value{font-weight:700;text-align:right}.status{font-size:20px;font-weight:800;padding:14px;border-radius:10px;margin:16px 0;background:#20252d}.green{color:#62e6a7}.red{color:#ff7777}.yellow{color:#ffd166}.blue{color:#73b7ff}.exec{margin-top:18px;padding:16px;border:1px solid #33404d;border-radius:12px;background:#10151a}.exec-title{font-size:18px;font-weight:800;margin-bottom:10px}.footer{margin-top:18px;color:#7f8895;font-size:12px}</style></head><body><div class="wrap"><h1>PSYCHO PDRA OPTION EXECUTION MONITOR</h1><div class="sub">Underlying structure → live Dhan option chain → best liquid premium candidate • refresh manually • 09:30 IST eligibility</div><div class="banner"><div class="banner-title">{{session.status}}</div><div class="banner-sub">{{session.detail}}</div></div><div class="grid">{% for x in instruments %}<div class="card"><div class="title">{{x.name}}</div><div class="time">{{x.generated}}</div>
<div class="row"><span class="label">PDH</span><span class="value">{{x.pdh}}</span></div><div class="row"><span class="label">PDL</span><span class="value">{{x.pdl}}</span></div><div class="row"><span class="label">Today's Open</span><span class="value">{{x.open}}</span></div><div class="row"><span class="label">Underlying LTP</span><span class="value">{{x.underlying_ltp}}</span></div><div class="row"><span class="label">ATR(14), 5M</span><span class="value">{{x.atr}}</span></div><div class="row"><span class="label">0.20 ATR</span><span class="value">{{x.threshold}}</span></div><div class="row"><span class="label">Long Trigger</span><span class="value">{{x.long_trigger}}</span></div><div class="row"><span class="label">Short Trigger</span><span class="value">{{x.short_trigger}}</span></div><div class="status {{x.status_class}}">{{x.status}}</div><div class="row"><span class="label">Displacement</span><span class="value">{{x.displacement}}</span></div><div class="row"><span class="label">Acceptance</span><span class="value">{{x.acceptance}}</span></div><div class="row"><span class="label">Direction</span><span class="value">{{x.direction}}</span></div>
<div class="exec"><div class="exec-title">OPTION EXECUTION</div><div class="row"><span class="label">Selected</span><span class="value">{{x.option}}</span></div><div class="row"><span class="label">Expiry</span><span class="value">{{x.expiry}}</span></div><div class="row"><span class="label">Option Entry</span><span class="value">{{x.option_entry}}</span></div><div class="row"><span class="label">Delta</span><span class="value">{{x.delta}}</span></div><div class="row"><span class="label">Spread</span><span class="value">{{x.spread}}</span></div><div class="row"><span class="label">OI</span><span class="value">{{x.oi}}</span></div><div class="row"><span class="label">Volume</span><span class="value">{{x.volume}}</span></div><div class="row"><span class="label">Premium SL</span><span class="value">{{x.option_sl}}</span></div><div class="row"><span class="label">Premium TP</span><span class="value">{{x.option_tp}}</span></div><div class="row"><span class="label">Option Status</span><span class="value">{{x.option_status}}</span></div></div><div class="footer">Research/paper-trading only. No order is placed. Premium SL/TP are delta-based estimates from the underlying invalidation and 1R levels; they are not historically validated option exits.</div></div>{% endfor %}</div></div></body></html>'''

def get_json(path):
    req=urllib.request.Request(f"{BRIDGE}{path}",headers={"Accept":"application/json"})
    with urllib.request.urlopen(req,timeout=15) as r:return json.loads(r.read().decode("utf-8"))

def flatten(obj):
    if isinstance(obj,dict):
        yield obj
        for v in obj.values():yield from flatten(v)
    elif isinstance(obj,list):
        for v in obj:yield from flatten(v)

def candles(obj):
    out=[]
    for x in flatten(obj):
        if not isinstance(x,dict) or not all(k in x for k in ("open","high","low","close")):continue
        try:
            ts=x.get("timestamp") or x.get("time") or x.get("datetime")
            dt=datetime.fromisoformat(ts.replace("Z","+00:00")).astimezone(IST) if isinstance(ts,str) and not ts.isdigit() else datetime.fromtimestamp(int(ts),IST)
            out.append({"dt":dt,"open":float(x["open"]),"high":float(x["high"]),"low":float(x["low"]),"close":float(x["close"])})
        except Exception:pass
    return sorted({x["dt"]:x for x in out}.values(),key=lambda x:x["dt"])

def atr(cs):
    if len(cs)<ATR_LENGTH+1:return None
    trs=[];prev=None
    for c in cs:
        tr=c["high"]-c["low"] if prev is None else max(c["high"]-c["low"],abs(c["high"]-prev),abs(c["low"]-prev))
        trs.append(tr);prev=c["close"]
    return sum(trs[-ATR_LENGTH:])/ATR_LENGTH

def num(v):
    try:return float(v)
    except:return None

def fmt(v):
    if v is None:return "—"
    try:return f"{float(v):,.2f}"
    except:return str(v)

def session_state(now):
    if now.weekday()>=5:return {"status":"MARKET CLOSED — WEEKEND","detail":"Refresh on the next trading session. No live chain call is required."}
    if now.time()<MARKET_OPEN:return {"status":"MARKET CLOSED — PRE-OPEN","detail":"Waiting for 09:15 IST."}
    if now.time()<ELIGIBILITY:return {"status":"MARKET OPEN — PDRA BUILDING","detail":"Opening structure is forming. Eligibility begins at 09:30 IST."}
    if now.time()>MARKET_CLOSE:return {"status":"MARKET CLOSED — SESSION COMPLETE","detail":"PDRA session ended at 15:40 IST."}
    return {"status":"MARKET OPEN — PDRA ACTIVE","detail":"Underlying structure and live option chain are being evaluated."}

def blank(name,now,status):
    return {"name":name,"generated":now.strftime("%d %b %Y %H:%M:%S IST"),"pdh":"—","pdl":"—","open":"—","underlying_ltp":"—","atr":"—","threshold":"—","long_trigger":"—","short_trigger":"—","status":status,"status_class":"blue","displacement":"NOT ACTIVE","acceptance":"NOT ACTIVE","direction":"—","option":"—","expiry":"—","option_entry":"—","delta":"—","spread":"—","oi":"—","volume":"—","option_sl":"—","option_tp":"—","option_status":"NOT ACTIVE"}

def choose_option(chain,direction):
    wanted="CE" if direction=="LONG" else "PE"; rows=[]
    for data in (chain.get("strikes") or {}).values():
        strike=num(data.get("strike"));leg=data.get(wanted) or {}
        if strike is None or not isinstance(leg,dict):continue
        ltp=num(leg.get("last_price"));bid=num(leg.get("top_bid_price"));ask=num(leg.get("top_ask_price"));oi=num(leg.get("oi"));vol=num(leg.get("volume"));delta=num((leg.get("greeks") or {}).get("delta"))
        if ltp is None or ltp<=0:continue
        spread=(ask-bid) if bid is not None and ask is not None and ask>=bid else None
        mid=((ask+bid)/2) if bid is not None and ask is not None and ask+bid>0 else ltp
        spread_pct=(spread/mid) if spread is not None and mid>0 else 0.10
        ad=abs(delta) if delta is not None else None
        if ad is not None and not MIN_DELTA<=ad<=MAX_DELTA:continue
        if spread_pct>MAX_SPREAD_PCT:continue
        delta_score=1-abs((ad if ad is not None else TARGET_DELTA)-TARGET_DELTA)/TARGET_DELTA
        liquidity=math.log1p(max(oi or 0,0))+math.log1p(max(vol or 0,0))
        spread_score=max(0,1-spread_pct/MAX_SPREAD_PCT)
        score=.50*delta_score+.30*spread_score+.20*min(liquidity/25,1)
        rows.append({"strike":strike,"side":wanted,"ltp":ltp,"delta":delta,"oi":oi,"volume":vol,"spread":spread,"score":score})
    return max(rows,key=lambda x:x["score"]) if rows else None

def projected_premium(opt,under_entry,under_sl,under_tp):
    d=opt.get("delta")
    if d is None:return None,None
    return max(.05,opt["ltp"]+d*(under_sl-under_entry)),max(.05,opt["ltp"]+d*(under_tp-under_entry))

def analyse(name,path,opt_path,now):
    cs=candles(get_json(path)); sessions={}
    for c in cs:sessions.setdefault(c["dt"].date(),[]).append(c)
    dates=sorted(sessions);today=sessions.get(now.date(),[]);prior=[d for d in dates if d<now.date()];prev=sessions.get(prior[-1],[]) if prior else []
    pdh=max((c["high"] for c in prev),default=None);pdl=min((c["low"] for c in prev),default=None);op=today[0]["open"] if today else None;a=atr(cs);th=a*DISPLACEMENT_ATR if a is not None else None
    out={"name":name,"generated":now.strftime("%d %b %Y %H:%M:%S IST"),"pdh":fmt(pdh),"pdl":fmt(pdl),"open":fmt(op),"underlying_ltp":fmt(today[-1]["close"] if today else None),"atr":fmt(a),"threshold":fmt(th),"long_trigger":fmt(pdh+th if pdh is not None and th is not None else None),"short_trigger":fmt(pdl-th if pdl is not None and th is not None else None),"status":"WAITING","status_class":"yellow","displacement":"WAITING","acceptance":"WAITING","direction":"—","option":"—","expiry":"—","option_entry":"—","delta":"—","spread":"—","oi":"—","volume":"—","option_sl":"—","option_tp":"—","option_status":"NOT ACTIVE"}
    if now.weekday()>=5:return blank(name,now,"MARKET CLOSED — WEEKEND")
    if now.time()<MARKET_OPEN:return blank(name,now,"MARKET CLOSED — PRE-OPEN")
    if now.time()>MARKET_CLOSE:return blank(name,now,"MARKET CLOSED — SESSION COMPLETE")
    if now.time()<ELIGIBILITY:out["status"]="PDRA STARTS 09:30";return out
    if not today or pdh is None or pdl is None or a is None:out["status"]="DATA UNAVAILABLE";out["status_class"]="red";return out
    if not pdl<op<pdh:out["status"]="NO TRADE — OPEN OUTSIDE RANGE";out["status_class"]="red";return out
    completed=[c for c in today if c["dt"].minute%5==0 and c["dt"].replace(second=0,microsecond=0)+timedelta(minutes=5)<=now]
    trigger=None;direction=None
    for c in completed:
        if c["close"]>=pdh+th:trigger=c;direction="LONG";break
        if c["close"]<=pdl-th:trigger=c;direction="SHORT";break
    if trigger is None:out["status"]="WAITING — NO DISPLACEMENT";return out
    out["displacement"]="CONFIRMED";out["direction"]=direction;idx=completed.index(trigger)
    if idx+1>=len(completed):out["status"]="DISPLACEMENT CONFIRMED — ACCEPTANCE WAITING";return out
    confirm=completed[idx+1];accepted=confirm["close"]>pdh if direction=="LONG" else confirm["close"]<pdl
    if not accepted:out["status"]="NO TRADE — ACCEPTANCE FAILED";out["status_class"]="red";out["acceptance"]="FAILED";return out
    out["acceptance"]="CONFIRMED"
    if idx+2>=len(completed):out["status"]="ACCEPTANCE CONFIRMED — ENTRY WAITING";return out
    entry=completed[idx+2]["open"];sl=pdh if direction=="LONG" else pdl;risk=abs(entry-sl);tp=entry+risk if direction=="LONG" else entry-risk
    out["status"]=f"{direction} — UNDERLYING VALID";out["status_class"]="green"
    try:
        chain=get_json(opt_path)
        if chain.get("status")!="LIVE":out["option_status"]="OPTION CHAIN UNAVAILABLE";return out
        opt=choose_option(chain,direction)
        if not opt:out["option_status"]="NO LIQUID OPTION PASSED FILTER";return out
        osl,otp=projected_premium(opt,entry,sl,tp)
        out.update({"option":f'{int(opt["strike"]):,} {opt["side"]}',"expiry":chain.get("expiry") or "—","option_entry":fmt(opt["ltp"]),"delta":fmt(opt["delta"]),"spread":fmt(opt["spread"]),"oi":fmt(opt["oi"]),"volume":fmt(opt["volume"]),"option_sl":fmt(osl),"option_tp":fmt(otp),"option_status":"🟢 OPTION EXECUTION CANDIDATE" if osl is not None else "🟡 OPTION CANDIDATE — DELTA UNAVAILABLE"})
    except Exception:out["option_status"]="OPTION CHAIN UNAVAILABLE"
    return out

@app.route("/")
def home():
    now=datetime.now(IST);session=session_state(now);items=[];closed=now.weekday()>=5 or now.time()<MARKET_OPEN or now.time()>MARKET_CLOSE
    if closed:
        items=[blank(n,now,session["status"]) for n in ("NIFTY","BANK NIFTY")]
    else:
        for n,p,o in (("NIFTY","/nifty-live","/nifty-option-chain"),("BANK NIFTY","/banknifty-live","/banknifty-option-chain")):
            try:items.append(analyse(n,p,o,now))
            except Exception:items.append(blank(n,now,"BRIDGE UNAVAILABLE"))
    return render_template_string(HTML,instruments=items,session=session)

@app.route("/health")
def health():return jsonify({"status":"ok","service":"pdra-monitor","market":session_state(datetime.now(IST))})

if __name__=="__main__":app.run(host="0.0.0.0",port=10000)
