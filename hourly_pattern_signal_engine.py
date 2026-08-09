"""PSYCHO HOURLY PATTERN SIGNAL ENGINE
Independent intraday signal engine. Refreshes every 60 seconds.
No order placement. Uses hour-specific historical pattern profiles.
"""
import os, json, time, threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import requests
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn

IST=ZoneInfo("Asia/Kolkata"); BASE="https://api.dhan.co/v2"
HEAD={"access-token":os.environ.get("DHAN_ACCESS_TOKEN",""),"client-id":os.environ.get("DHAN_CLIENT_ID",""),"Content-Type":"application/json","Accept":"application/json"}
PROFILE=json.load(open("hourly_pattern_profile.json"))
SYMS={"NIFTY":{"id":"13"},"BANKNIFTY":{"id":"25"}}
WINDOWS=[("09:30-10:30",570,630),("10:30-11:30",630,690),(11:=690,750)]
WINDOWS=[("09:30-10:30",570,630),("10:30-11:30",630,690),("11:30-12:30",690,750),("12:30-13:30",750,810),("13:30-14:30",810,870),("14:30-15:30",870,930)]
STATE={"status":"BOOTING","updated_at":None,"signals":{},"errors":[]}; LOCK=threading.Lock(); LAST_CHAIN={}; LAST_EXPIRY={}

def post(path,payload):
    r=requests.post(BASE+path,headers=HEAD,json=payload,timeout=25); r.raise_for_status(); x=r.json()
    if isinstance(x,dict) and str(x.get("status","")).lower()=="failure": raise RuntimeError(x.get("remarks") or x.get("errorMessage") or str(x))
    return x

def intraday(sec,seg,instrument,interval):
    now=datetime.now(IST); day=now.strftime("%Y-%m-%d")
    p={"securityId":str(sec),"exchangeSegment":seg,"instrument":instrument,"interval":str(interval),"oi":False,"fromDate":day+" 09:15:00","toDate":day+" 15:30:00"}
    x=post("/charts/intraday",p); ts=x.get("timestamp",[]); rows=[]; vols=x.get("volume",[0]*len(ts))
    for i,t in enumerate(ts): rows.append({"ts":int(t),"o":float(x["open"][i]),"h":float(x["high"][i]),"l":float(x["low"][i]),"c":float(x["close"][i]),"v":float(vols[i])})
    return rows

def completed(rows,mins):
    now=datetime.now(IST); return [r for r in rows if datetime.fromtimestamp(r["ts"],IST)+timedelta(minutes=mins)<=now]

def bucket():
    n=datetime.now(IST); m=n.hour*60+n.minute
    for name,lo,hi in WINDOWS:
        if lo<=m<hi:return name
    return None

def find_breakout(rows,pattern):
    if len(rows)<10:return None
    def rng(x):return x["h"]-x["l"]
    # Search only recent completed candles; don't reuse an already-invalidated breakout.
    for i in range(len(rows)-1,max(1,len(rows)-8),-1):
        p,q=rows[i-1],rows[i-2]
        if pattern=="INSIDE_BAR": ok=p["h"]<q["h"] and p["l"]>q["l"]
        elif pattern=="NR4": ok=rng(p)<=min(rng(x) for x in rows[i-5:i-1])
        elif pattern=="NR7": ok=rng(p)<=min(rng(x) for x in rows[i-8:i-1])
        else: ok=False
        if not ok: continue
        r=rows[i]
        if r["c"]>p["h"]: direction="LONG"; level=p["h"]
        elif r["c"]<p["l"]: direction="SHORT"; level=p["l"]
        else: continue
        # Invalidate if any later completed 5M candle closes through the opposite side.
        invalid=False
        for z in rows[i+1:]:
            if (direction=="LONG" and z["c"]<p["l"]) or (direction=="SHORT" and z["c"]>p["h"]): invalid=True; break
        if invalid: continue
        atr=sum(rng(x) for x in rows[max(0,i-14):i+1])/min(14,i+1)
        return {"stage":"BREAKOUT","pattern":pattern,"direction":direction,"breakout_level":level,"compression_high":p["h"],"compression_low":p["l"],"atr5":atr,"breakout_ts":r["ts"],"breakout_close":r["c"]}
    return {"stage":"WAIT_BREAKOUT","pattern":pattern}

def one_hour_regime(rows5,rows60,direction):
    if len(rows60)<21:return False,None,None
    c=[x["c"] for x in rows60]; ema=[]; e=c[0]; a=2/21
    for v in c: e=a*v+(1-a)*e; ema.append(e)
    slope=ema[-1]-ema[-2]; price=rows5[-1]["c"]
    aligned=(direction=="LONG" and price>ema[-1] and slope>0) or (direction=="SHORT" and price<ema[-1] and slope<0)
    return aligned,ema[-1],slope

def nearest_expiry(symbol):
    sid=int(SYMS[symbol]["id"]); today=datetime.now(IST).date().isoformat()
    ex=post("/optionchain/expirylist",{"UnderlyingScrip":sid,"UnderlyingSeg":"IDX_I"}).get("data",[])
    return next((e for e in ex if e>=today),None)

def chain(symbol):
    expiry=LAST_EXPIRY.get(symbol)
    if not expiry or expiry<datetime.now(IST).date().isoformat(): expiry=nearest_expiry(symbol); LAST_EXPIRY[symbol]=expiry
    if not expiry:return None
    x=post("/optionchain",{"UnderlyingScrip":int(SYMS[symbol]["id"]),"UnderlyingSeg":"IDX_I","Expiry":expiry}).get("data",{}); LAST_CHAIN[symbol]=(expiry,x); return expiry,x

def option_confirm(symbol,direction,spot):
    got=LAST_CHAIN.get(symbol)
    if not got or got[0]<datetime.now(IST).date().isoformat(): got=chain(symbol)
    if not got:return None
    expiry,data=got; oc=data.get("oc",{}); candidates=[]
    for k,node in oc.items():
        try:s=float(k)
        except:continue
        candidates.append((abs(s-spot),s,node))
    if not candidates:return None
    _,strike,node=min(candidates,key=lambda z:z[0]); side="ce" if direction=="LONG" else "pe"; opt=node.get(side,{})
    sid=opt.get("security_id")
    if not sid:return None
    one=completed(intraday(sid,"NSE_FNO","OPTIDX",1),1)
    if len(one)<21:return None
    prem_now=one[-1]["c"]; prem_prev=one[-6]["c"]; med=np_median([x["v"] for x in one[-21:-1]])
    vol_ok=one[-1]["v"]>1.1*med if med>0 else False; momentum=prem_now>prem_prev
    return {"expiry":expiry,"strike":strike,"side":side.upper(),"security_id":sid,"premium":prem_now,"premium_5m_change":prem_now-prem_prev,"volume_1m":one[-1]["v"],"volume_median_20m":med,"volume_ok":vol_ok,"premium_momentum":momentum,"iv":opt.get("implied_volatility"),"oi":opt.get("oi"),"bid":opt.get("top_bid_price"),"ask":opt.get("top_ask_price"),"confirmed":momentum and vol_ok}

def np_median(a):
    a=sorted(float(x) for x in a); n=len(a)
    return (a[n//2] if n%2 else (a[n//2-1]+a[n//2])/2) if n else 0

def scan(symbol):
    now=datetime.now(IST); win=bucket()
    if not win:return {"symbol":symbol,"status":"OUTSIDE_WINDOW","window":None}
    prof=PROFILE["profiles"][symbol][win]; rows5=completed(intraday(SYMS[symbol]["id"],"IDX_I","INDEX",5),5); rows1=completed(intraday(SYMS[symbol]["id"],"IDX_I","INDEX",1),1); rows60=completed(intraday(SYMS[symbol]["id"],"IDX_I","INDEX",60),60)
    det=find_breakout(rows5,prof["pattern"]); out={"symbol":symbol,"window":win,"historical_profile":prof,"detection":det,"updated_at":now.isoformat(),"status":"WATCHING"}
    if not det or det.get("stage")!="BREAKOUT":return out
    spot=rows1[-1]["c"] if rows1 else det["breakout_close"]; level=det["breakout_level"]; retest=abs(spot-level)<=0.15*det["atr5"]
    aligned,ema,slope=one_hour_regime(rows5,rows60,det["direction"]); out.update({"retest":retest,"htf_aligned":aligned,"ema20_1h":ema,"ema_slope":slope})
    if not (retest and aligned): out["status"]="FILTERED"; return out
    opt=option_confirm(symbol,det["direction"],spot); out["option_confirmation"]=opt
    if not opt or not opt["confirmed"]:out["status"]="FILTERED";return out
    # Signal geometry is defined on the underlying. Option is the selected vehicle.
    if det["direction"]=="LONG": uentry=spot; usl=uentry-0.75*det["atr5"]; utp=uentry+(uentry-usl)
    else:uentry=spot; usl=uentry+0.75*det["atr5"]; utp=uentry-(usl-uentry)
    out["status"]="SIGNAL"; out["signal"]={"direction":det["direction"],"vehicle":f"{symbol} {opt['strike']} {opt['side']}","option_security_id":opt["security_id"],"underlying_entry":round(uentry,2),"underlying_stop":round(usl,2),"underlying_target":round(utp,2),"atr5":round(det["atr5"],2),"max_hold_minutes":30,"time_window":win,"note":"Research signal only. No order placement."}
    return out

def loop():
    while True:
        try:
            now=datetime.now(IST); results={s:scan(s) for s in SYMS};
            with LOCK:STATE.update({"status":"LIVE","updated_at":now.isoformat(),"signals":results,"errors":[]})
        except Exception as e:
            with LOCK:STATE.update({"status":"ERROR","updated_at":datetime.now(IST).isoformat(),"errors":[str(e)]})
        time.sleep(60)

app=FastAPI(title="PSYCHO Hourly Pattern Signal Engine")
@app.get("/api/hourly")
def api_hourly():
    with LOCK:return JSONResponse(STATE)
@app.get("/health")
def health():return {"status":STATE["status"],"updated_at":STATE["updated_at"]}
@app.get("/",response_class=HTMLResponse)
def home():
    return '''<!doctype html><html><head><meta http-equiv="refresh" content="60"><meta name="viewport" content="width=device-width,initial-scale=1"><title>PSYCHO HOURLY PATTERN ENGINE</title><style>body{font-family:Arial;background:#090b0f;color:#eee;padding:20px;max-width:1100px;margin:auto}pre{white-space:pre-wrap;background:#141821;padding:18px;border-radius:12px;overflow:auto}.live{font-size:12px;opacity:.7}</style></head><body><h1>PSYCHO — HOURLY PATTERN ENGINE</h1><div class="live">60-second refresh • 09:30–15:30 IST • NIFTY + BANK NIFTY • signal-only</div><pre id="x">Loading...</pre><script>fetch('/api/hourly').then(r=>r.json()).then(x=>document.getElementById('x').textContent=JSON.stringify(x,null,2)).catch(e=>document.getElementById('x').textContent=e)</script></body></html>'''

if __name__=="__main__":
    threading.Thread(target=loop,daemon=True).start(); uvicorn.run(app,host="0.0.0.0",port=int(os.environ.get("PORT","10000")))
