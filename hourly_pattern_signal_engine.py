"""PSYCHO HOURLY PATTERN SIGNAL ENGINE
Independent intraday signal engine. Refreshes every 60 seconds.
No order placement.
"""
import os, json, time, threading
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo
import requests
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn

IST=ZoneInfo("Asia/Kolkata")
BASE="https://api.dhan.co/v2"
HEAD={"access-token":os.environ.get("DHAN_ACCESS_TOKEN",""),"client-id":os.environ.get("DHAN_CLIENT_ID",""),"Content-Type":"application/json","Accept":"application/json"}
PROFILE=json.load(open("hourly_pattern_profile.json"))
SYMS={"NIFTY":{"id":"13","seg":"IDX_I"},"BANKNIFTY":{"id":"25","seg":"IDX_I"}}
WINDOWS=[("09:30-10:30",570,630),("10:30-11:30",630,690),("11:30-12:30",690,750),("12:30-13:30",750,810),("13:30-14:30",810,870),("14:30-15:30",870,930)]
STATE={"status":"BOOTING","updated_at":None,"signals":{},"errors":[]}
LOCK=threading.Lock()


def post(path,payload):
    r=requests.post(BASE+path,headers=HEAD,json=payload,timeout=20)
    r.raise_for_status(); x=r.json()
    if isinstance(x,dict) and str(x.get("status","")).lower()=="failure": raise RuntimeError(x.get("remarks") or x.get("errorMessage") or str(x))
    return x

def candles(sec, interval, start=None, end=None):
    now=datetime.now(IST); day=now.strftime("%Y-%m-%d")
    p={"securityId":sec,"exchangeSegment":"IDX_I","instrument":"INDEX","interval":str(interval),"oi":False}
    if start and end: p.update({"fromDate":start,"toDate":end})
    else: p.update({"fromDate":day+" 09:15:00","toDate":day+" 15:30:00"})
    x=post("/charts/intraday",p); n=x.get("timestamp",[])
    rows=[]
    for i,t in enumerate(n): rows.append({"ts":int(t),"o":float(x["open"][i]),"h":float(x["high"][i]),"l":float(x["low"][i]),"c":float(x["close"][i]),"v":float(x.get("volume",[0]*len(n))[i])})
    return rows

def bucket(now):
    m=now.hour*60+now.minute
    for name,lo,hi in WINDOWS:
        if lo<=m<hi:return name
    return None

def completed_5m(rows):
    # Dhan may include the currently forming candle; use only candles whose end is <= current minute.
    now=datetime.now(IST); out=[]
    for r in rows:
        dt=datetime.fromtimestamp(r["ts"],IST)
        # timestamps represent candle start; a 5m candle is complete 5 minutes after start
        if dt + __import__('datetime').timedelta(minutes=5) <= now: out.append(r)
    return out

def detect(rows, pattern):
    if len(rows)<10:return None
    r=rows[-1]; p=rows[-2]; q=rows[-3]
    rng=lambda x:x["h"]-x["l"]
    ispat=False
    if pattern=="INSIDE_BAR": ispat=(p["h"]<q["h"] and p["l"]>q["l"])
    elif pattern=="NR4": ispat=rng(p)<=min(rng(x) for x in rows[-5:-1])
    elif pattern=="NR7": ispat=rng(p)<=min(rng(x) for x in rows[-8:-1])
    # breakout is the latest completed candle relative to the pattern candle
    if not ispat:return {"stage":"WAIT_PATTERN","pattern":pattern,"level_high":p["h"],"level_low":p["l"]}
    if r["c"]>p["h"]: direction="LONG"; level=p["h"]
    elif r["c"]<p["l"]: direction="SHORT"; level=p["l"]
    else:return {"stage":"WAIT_BREAKOUT","pattern":pattern,"level_high":p["h"],"level_low":p["l"]}
    atr=sum(rng(x) for x in rows[-14:])/min(14,len(rows[-14:]))
    return {"stage":"BREAKOUT","pattern":pattern,"direction":direction,"breakout_level":level,"atr5":atr,"breakout_close":r["c"]}

def option_snapshot(symbol, direction, spot):
    # Find nearest active expiry, then ATM strike. Option-chain request is cached per scan.
    expiries=post("/optionchain/expirylist",{"UnderlyingScrip":int(SYMS[symbol]["id"]),"UnderlyingSeg":"IDX_I"}).get("data",[])
    today=datetime.now(IST).date().isoformat(); expiry=next((e for e in expiries if e>=today),None)
    if not expiry:return None
    chain=post("/optionchain",{"UnderlyingScrip":int(SYMS[symbol]["id"]),"UnderlyingSeg":"IDX_I","Expiry":expiry}).get("data",{})
    oc=chain.get("oc",{}); strikes=[]
    for k,v in oc.items():
        try:strike=float(k)
        except:continue
        strikes.append((abs(strike-spot),strike,v))
    if not strikes:return None
    _,strike,node=min(strikes,key=lambda x:x[0]); side="ce" if direction=="LONG" else "pe"; opt=node.get(side,{})
    if not opt:return None
    return {"expiry":expiry,"strike":strike,"side":side.upper(),"security_id":opt.get("security_id"),"ltp":opt.get("last_price"),"iv":opt.get("implied_volatility"),"oi":opt.get("oi"),"volume":opt.get("volume"),"bid":opt.get("top_bid_price"),"ask":opt.get("top_ask_price")}

def scan_symbol(symbol):
    now=datetime.now(IST); win=bucket(now)
    if not win:return {"symbol":symbol,"status":"OUTSIDE_WINDOW","window":None}
    prof=PROFILE["profiles"][symbol][win]; rows5=completed_5m(candles(SYMS[symbol]["id"],5)); rows1=candles(SYMS[symbol]["id"],1); rows60=candles(SYMS[symbol]["id"],60)
    det=detect(rows5,prof["pattern"])
    out={"symbol":symbol,"window":win,"historical_pattern":prof,"detection":det,"status":"WATCHING","updated_at":now.isoformat()}
    if det and det.get("stage")=="BREAKOUT":
        c1=rows1[-1]["c"] if rows1 else det["breakout_close"]
        # Retest: current price is within 0.15 ATR of breakout level.
        level=det["breakout_level"]; retest=abs(c1-level)<=0.15*det["atr5"]
        # 1H regime
        if len(rows60)>=21:
            closes=[x["c"] for x in rows60]; ema=closes[0]; alpha=2/21
            for c in closes[1:]: ema=alpha*c+(1-alpha)*ema
            slope=ema-(sum(closes[-5:-1])/4)
            aligned=(det["direction"]=="LONG" and c1>ema and slope>0) or (det["direction"]=="SHORT" and c1<ema and slope<0)
        else: aligned=False
        opt=option_snapshot(symbol,det["direction"],c1) if retest and aligned else None
        confirmed=bool(opt and opt.get("ltp") is not None and opt.get("volume") is not None and opt.get("ask") is not None)
        out.update({"retest":retest,"htf_aligned":aligned,"option":opt,"status":"SIGNAL" if confirmed else "FILTERED"})
        if confirmed:
            entry=float(opt["ask"] or opt["ltp"]); risk=max(entry*0.10,0.01)
            out["signal"]={"action":"BUY","instrument":f"{symbol} {opt['strike']} {opt['side']}","entry":round(entry,2),"stop_loss":round(entry-risk,2),"target":round(entry+risk,2),"risk":round(risk,2),"note":"Research signal only; no order placement."}
    return out

def loop():
    while True:
        try:
            now=datetime.now(IST); results={s:scan_symbol(s) for s in SYMS}
            with LOCK: STATE.update({"status":"LIVE","updated_at":now.isoformat(),"signals":results,"errors":[]})
        except Exception as e:
            with LOCK: STATE.update({"status":"ERROR","updated_at":datetime.now(IST).isoformat(),"errors":[str(e)]})
        time.sleep(60)

app=FastAPI(title="PSYCHO Hourly Pattern Engine")
@app.get("/api/hourly")
def hourly():
    with LOCK:return JSONResponse(STATE)
@app.get("/health")
def health():return {"status":STATE["status"],"updated_at":STATE["updated_at"]}
@app.get("/",response_class=HTMLResponse)
def home():
    return '''<html><head><meta http-equiv="refresh" content="60"><title>PSYCHO HOURLY PATTERN ENGINE</title><style>body{font-family:Arial;background:#0b0d10;color:#eee;padding:24px}pre{white-space:pre-wrap;background:#151922;padding:20px;border-radius:12px}h1{letter-spacing:2px}</style></head><body><h1>PSYCHO — HOURLY PATTERN ENGINE</h1><p>Refresh: 60 seconds • Research signal only • NIFTY + BANK NIFTY</p><pre id="x">Loading...</pre><script>fetch('/api/hourly').then(r=>r.json()).then(x=>document.getElementById('x').textContent=JSON.stringify(x,null,2))</script></body></html>'''

if __name__=="__main__":
    threading.Thread(target=loop,daemon=True).start()
    uvicorn.run(app,host="0.0.0.0",port=int(os.environ.get("PORT","10000")))
