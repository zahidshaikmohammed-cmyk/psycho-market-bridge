"""PSYCHO 09:30-10:30 SIGNAL ENGINE

Dedicated time-window engine. It operates only from 09:30 through 10:30 IST.
Research-derived base structure for this window: 5M Inside Bar breakout.
Execution filters: 1H trend alignment, first retest, ATM option confirmation.
No order placement.
"""
import os, time, threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import requests
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn

IST=ZoneInfo("Asia/Kolkata")
BASE="https://api.dhan.co/v2"
HEAD={"access-token":os.environ.get("DHAN_ACCESS_TOKEN",""),"client-id":os.environ.get("DHAN_CLIENT_ID",""),"Content-Type":"application/json","Accept":"application/json"}
SYMS={"NIFTY":{"id":"13"},"BANKNIFTY":{"id":"25"}}
WINDOW_START=9*60+30; WINDOW_END=10*60+30
STATE={"engine":"0930-1030","status":"BOOTING","updated_at":None,"signals":{},"errors":[]}
LOCK=threading.Lock()


def post(path,payload):
    r=requests.post(BASE+path,headers=HEAD,json=payload,timeout=25)
    r.raise_for_status(); x=r.json()
    if isinstance(x,dict) and str(x.get("status","")).lower()=="failure":
        raise RuntimeError(x.get("remarks") or x.get("errorMessage") or str(x))
    return x

def candles(sec, interval, days=2):
    now=datetime.now(IST); start=(now-timedelta(days=days)).strftime("%Y-%m-%d 09:15:00"); end=now.strftime("%Y-%m-%d 15:30:00")
    p={"securityId":sec,"exchangeSegment":"IDX_I","instrument":"INDEX","interval":str(interval),"oi":False,"fromDate":start,"toDate":end}
    x=post("/charts/intraday",p); ts=x.get("timestamp",[])
    return [{"ts":int(t),"dt":datetime.fromtimestamp(int(t),IST),"o":float(x["open"][i]),"h":float(x["high"][i]),"l":float(x["low"][i]),"c":float(x["close"][i])} for i,t in enumerate(ts)]

def completed(rows, minutes):
    now=datetime.now(IST)
    return [r for r in rows if r["dt"]+timedelta(minutes=minutes)<=now]

def ema(values, period=20):
    if len(values)<period:return None
    a=2/(period+1); e=float(values[0])
    for v in values[1:]: e=a*float(v)+(1-a)*e
    return e

def option_chain(symbol, spot):
    sid=int(SYMS[symbol]["id"])
    ex=post("/optionchain/expirylist",{"UnderlyingScrip":sid,"UnderlyingSeg":"IDX_I"}).get("data",[])
    today=datetime.now(IST).date().isoformat(); expiry=next((x for x in ex if x>=today),None)
    if not expiry:return None
    data=post("/optionchain",{"UnderlyingScrip":sid,"UnderlyingSeg":"IDX_I","Expiry":expiry}).get("data",{})
    oc=data.get("oc",{}); candidates=[]
    for k,node in oc.items():
        try: candidates.append((abs(float(k)-spot),float(k),node))
        except: pass
    if not candidates:return None
    _,strike,node=min(candidates,key=lambda z:z[0])
    return expiry,strike,node

def option_intraday(sec):
    if not sec:return []
    now=datetime.now(IST); start=(now-timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S"); end=now.strftime("%Y-%m-%d %H:%M:%S")
    p={"securityId":str(sec),"exchangeSegment":"NSE_FNO","instrument":"OPTIDX","interval":"1","oi":True,"fromDate":start,"toDate":end}
    x=post("/charts/intraday",p); ts=x.get("timestamp",[])
    vol=x.get("volume",[]); close=x.get("close",[])
    return [{"dt":datetime.fromtimestamp(int(t),IST),"c":float(close[i]),"v":float(vol[i]) if i<len(vol) else 0.0} for i,t in enumerate(ts)]

def scan(symbol):
    now=datetime.now(IST); m=now.hour*60+now.minute
    if not (WINDOW_START<=m<WINDOW_END): return {"symbol":symbol,"status":"CLOSED","window":"09:30-10:30"}
    r5=completed(candles(SYMS[symbol]["id"],5,2),5); r1=completed(candles(SYMS[symbol]["id"],1,1),1); r60=completed(candles(SYMS[symbol]["id"],60,30),60)
    today=[x for x in r5 if x["dt"].date()==now.date() and WINDOW_START<=x["dt"].hour*60+x["dt"].minute<WINDOW_END]
    if len(today)<3:return {"symbol":symbol,"status":"WAITING","reason":"Need completed 5M candles"}
    # Latest completed candle is breakout candidate; previous completed candle is the inside bar.
    p=today[-2]; q=today[-3]; r=today[-1]
    inside=p["h"]<q["h"] and p["l"]>q["l"]
    base={"symbol":symbol,"window":"09:30-10:30","pattern":"5M_INSIDE_BAR","inside_bar_high":p["h"],"inside_bar_low":p["l"],"updated_at":now.isoformat()}
    if not inside:
        return {**base,"status":"WAIT_PATTERN"}
    direction=None; level=None
    if r["c"]>p["h"]: direction="LONG"; level=p["h"]
    elif r["c"]<p["l"]: direction="SHORT"; level=p["l"]
    else:return {**base,"status":"WAIT_BREAKOUT"}
    tr=[x["h"]-x["l"] for x in today[-14:]]; atr=sum(tr)/len(tr) if tr else 0
    current=r1[-1]["c"] if r1 else r["c"]
    retest=abs(current-level)<=0.15*atr if atr>0 else False
    closes=[x["c"] for x in r60]; e=ema(closes,20); slope=(e-ema(closes[-5:],20)) if e is not None and len(closes)>=25 else 0
    aligned=(e is not None and ((direction=="LONG" and current>e and slope>0) or (direction=="SHORT" and current<e and slope<0)))
    out={**base,"status":"FILTERED","direction":direction,"breakout_level":level,"atr5":atr,"retest":retest,"htf_aligned":aligned}
    if not (retest and aligned):return out
    chain=option_chain(symbol,current)
    if not chain:return {**out,"reason":"No active option expiry/chain"}
    expiry,strike,node=chain; side="ce" if direction=="LONG" else "pe"; opt=node.get(side,{})
    sid=opt.get("security_id")
    hist=option_intraday(sid)
    if not hist:return {**out,"reason":"No option 1M data"}
    latest=hist[-1]; prior=[x["c"] for x in hist[:-5] if x["c"] is not None]
    premium_up=latest["c"]>(hist[-6]["c"] if len(hist)>=6 else latest["c"])
    vols=[x["v"] for x in hist[:-1] if x["v"]>=0]
    median20=sorted(vols[-20:])[len(vols[-20:])//2] if vols else 0
    volume_ok=latest["v"]>1.1*median20 if median20>0 else False
    confirmed=premium_up and volume_ok
    out.update({"option":{"expiry":expiry,"strike":strike,"side":side.upper(),"security_id":sid,"ltp":opt.get("last_price"),"iv":opt.get("implied_volatility"),"oi":opt.get("oi"),"chain_volume":opt.get("volume"),"bid":opt.get("top_bid_price"),"ask":opt.get("top_ask_price")},"option_confirmation":{"premium_up_5m":premium_up,"current_1m_volume":latest["v"],"median_20m_volume":median20,"volume_threshold":1.1*median20 if median20 else None,"volume_ok":volume_ok},"status":"SIGNAL" if confirmed else "FILTERED"})
    if confirmed:
        entry=current; sl=entry-0.75*atr if direction=="LONG" else entry+0.75*atr; risk=abs(entry-sl); target=entry+risk if direction=="LONG" else entry-risk
        out["signal"]={"action":"BUY","underlying":symbol,"option":f"{strike} {side.upper()}","entry":round(entry,2),"stop_loss":round(sl,2),"target":round(target,2),"max_hold_minutes":30,"research_status":"SIGNAL ONLY — NO ORDER PLACEMENT"}
    return out

def loop():
    while True:
        try:
            results={s:scan(s) for s in SYMS}; now=datetime.now(IST)
            with LOCK: STATE.update({"status":"LIVE" if WINDOW_START<=now.hour*60+now.minute<WINDOW_END else "CLOSED","updated_at":now.isoformat(),"signals":results,"errors":[]})
        except Exception as e:
            with LOCK: STATE.update({"status":"ERROR","updated_at":datetime.now(IST).isoformat(),"errors":[str(e)]})
        time.sleep(60)

app=FastAPI(title="PSYCHO 09:30-10:30 Signal Engine")
@app.get("/api/signal")
def api():
    with LOCK:return JSONResponse(STATE)
@app.get("/health")
def health():return {"status":STATE["status"],"updated_at":STATE["updated_at"]}
@app.get("/",response_class=HTMLResponse)
def home():
    return '''<html><head><meta http-equiv="refresh" content="60"><title>PSYCHO 09:30-10:30</title><style>body{font-family:Arial;background:#080a0d;color:#eee;padding:24px}pre{white-space:pre-wrap;background:#141821;padding:20px;border-radius:12px}</style></head><body><h1>PSYCHO — 09:30–10:30 SIGNAL ENGINE</h1><p>Refresh: 60 seconds • NIFTY + BANK NIFTY • Research signal only</p><pre id="x">Loading...</pre><script>fetch('/api/signal').then(r=>r.json()).then(x=>document.getElementById('x').textContent=JSON.stringify(x,null,2))</script></body></html>'''

if __name__=="__main__":
    threading.Thread(target=loop,daemon=True).start(); uvicorn.run(app,host="0.0.0.0",port=int(os.environ.get("PORT","10000")))
