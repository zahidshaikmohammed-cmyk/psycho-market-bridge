"""PSYCHO INTRADAY SIGNAL ENGINE
Independent live signal engine. No order placement.

Strategy:
5M inside-bar breakout -> 1H EMA20 alignment -> first retest -> ATM option
premium confirmation -> 0.75 * 5M ATR(14) stop -> 1R target -> 30m max hold.

The service refreshes once per minute when run continuously (e.g. Render).
"""
import json, os, threading, time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import requests

IST = ZoneInfo("Asia/Kolkata")
BASE = "https://api.dhan.co/v2"
PORT = int(os.getenv("PORT", "10000"))
CLIENT_ID = os.environ.get("DHAN_CLIENT_ID", "")
TOKEN = os.environ.get("DHAN_ACCESS_TOKEN", "")
REFRESH_SECONDS = 60

UNDERLYINGS = {
    "NIFTY": {"sid": "13", "segment": "IDX_I", "strike_step": 50},
    "BANK NIFTY": {"sid": "25", "segment": "IDX_I", "strike_step": 100},
}

state = {"updated_at": None, "market": "UNKNOWN", "signals": [], "errors": [], "engine": "READY"}
lock = threading.Lock()


def headers():
    return {"access-token": TOKEN, "client-id": CLIENT_ID, "Content-Type": "application/json", "Accept": "application/json"}


def post(path, payload, timeout=25):
    r = requests.post(BASE + path, headers=headers(), json=payload, timeout=timeout)
    if r.status_code >= 400:
        raise RuntimeError(f"Dhan HTTP {r.status_code}: {r.text[:300]}")
    obj = r.json()
    if isinstance(obj, dict) and str(obj.get("status", "")).lower() == "failure":
        raise RuntimeError(obj.get("remarks") or obj.get("errorMessage") or str(obj))
    return obj


def arrays_to_rows(obj):
    d = obj.get("data", obj) if isinstance(obj, dict) else obj
    if not isinstance(d, dict) or not d.get("timestamp"):
        return []
    keys = [k for k in ("open", "high", "low", "close", "volume", "oi") if k in d]
    n = len(d["timestamp"])
    rows=[]
    for i in range(n):
        row={"ts": int(float(d["timestamp"][i]))}
        for k in keys:
            if i < len(d[k]): row[k]=float(d[k][i])
        rows.append(row)
    return sorted(rows, key=lambda x:x["ts"])


def fetch_intraday(sid, interval, days=1):
    now = datetime.now(IST)
    start = (now - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    end = now.strftime("%Y-%m-%d %H:%M:%S")
    payload={"securityId":sid,"exchangeSegment":"IDX_I","instrument":"INDEX","interval":str(interval),"oi":False,"fromDate":start,"toDate":end}
    return arrays_to_rows(post("/charts/intraday", payload))


def ema(values, period):
    if not values: return None
    a=2/(period+1); e=float(values[0])
    for x in values[1:]: e=a*float(x)+(1-a)*e
    return e


def atr(rows, period=14):
    if len(rows)<period+1: return None
    trs=[]
    for i in range(1,len(rows)):
        h,l,pc=rows[i]["high"],rows[i]["low"],rows[i-1]["close"]
        trs.append(max(h-l,abs(h-pc),abs(l-pc)))
    return sum(trs[-period:])/period


def latest_completed_5m(rows, now):
    # Exclude the currently forming 5-minute bar.
    cutoff = now.replace(second=0,microsecond=0) - timedelta(minutes=now.minute % 5)
    epoch=cutoff.timestamp()
    return [r for r in rows if r["ts"] < epoch]


def current_price(rows):
    return rows[-1]["close"] if rows else None


def expiry_list(sid, segment):
    obj=post("/optionchain/expirylist", {"UnderlyingScrip":int(sid),"UnderlyingSeg":segment})
    data=obj.get("data", obj)
    if not isinstance(data,list): raise RuntimeError("Invalid expiry list")
    today=datetime.now(IST).date().isoformat()
    return sorted(x for x in data if x >= today)


def option_chain(sid, segment, expiry):
    obj=post("/optionchain", {"UnderlyingScrip":int(sid),"UnderlyingSeg":segment,"Expiry":expiry})
    return obj.get("data", obj)


def option_confirmation(option_sid, direction):
    rows=fetch_option_intraday(option_sid)
    if len(rows)<6: return {"ok":False,"reason":"insufficient option 1M history"}
    closes=[r["close"] for r in rows]
    vols=[r.get("volume",0) for r in rows]
    positive=closes[-1] > closes[-6]
    base=sorted(vols[-21:-1])
    med=base[len(base)//2] if base else 0
    volume_ok=bool(med) and vols[-1] > 1.1*med
    return {"ok":positive and volume_ok,"premium_5m_change_pct":((closes[-1]/closes[-6])-1)*100 if closes[-6] else None,"volume":vols[-1],"volume_median_20":med,"volume_ratio":(vols[-1]/med if med else None)}


def fetch_option_intraday(option_sid):
    now=datetime.now(IST)
    start=(now-timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
    end=now.strftime("%Y-%m-%d %H:%M:%S")
    payload={"securityId":str(option_sid),"exchangeSegment":"NSE_FNO","instrument":"OPTIDX","interval":"1","oi":True,"fromDate":start,"toDate":end}
    return arrays_to_rows(post("/charts/intraday",payload))


def build_signal(symbol, cfg, rows1m, rows5m, rows1h):
    now=datetime.now(IST)
    c5=latest_completed_5m(rows5m, now)
    if len(c5)<25: return {"symbol":symbol,"status":"WAIT","reason":"insufficient completed 5M candles"}
    # Latest completed candle is the breakout candidate; prior candle is inside bar.
    inside=c5[-2]; breakout=c5[-1]
    if not (inside["high"] < c5[-3]["high"] and inside["low"] > c5[-3]["low"]):
        # Also permit a conventional inside bar against its immediate predecessor.
        if not (inside["high"] < c5[-3]["high"] and inside["low"] > c5[-3]["low"]):
            return {"symbol":symbol,"status":"WAIT","reason":"no fresh inside-bar breakout"}
    direction=None
    level=None
    if breakout["close"] > inside["high"]: direction="LONG"; level=inside["high"]
    elif breakout["close"] < inside["low"]: direction="SHORT"; level=inside["low"]
    else: return {"symbol":symbol,"status":"WAIT","reason":"inside bar has no directional breakout"}

    ema1h=ema([r["close"] for r in rows1h],20)
    prev_ema=ema([r["close"] for r in rows1h[:-1]],20) if len(rows1h)>20 else None
    price1h=rows1h[-1]["close"] if rows1h else None
    aligned=(direction=="LONG" and price1h>ema1h and ema1h>prev_ema) or (direction=="SHORT" and price1h<ema1h and ema1h<prev_ema)
    if not aligned: return {"symbol":symbol,"status":"WAIT","reason":"1H trend not aligned","direction":direction}

    p1m=current_price(rows1m); a=atr(c5,14)
    if p1m is None or a is None: return {"symbol":symbol,"status":"WAIT","reason":"insufficient 1M/ATR data"}
    # First-retest geometry: current 1M price must touch the broken level after breakout,
    # but remain on the correct side at evaluation time.
    touched=(p1m <= level + 0.10*a and p1m >= level - 0.10*a)
    correct_side=(p1m>=level if direction=="LONG" else p1m<=level)
    if not (touched and correct_side):
        return {"symbol":symbol,"status":"ARMED","reason":"breakout found; waiting first retest","direction":direction,"level":level}

    expiries=expiry_list(cfg["sid"],cfg["segment"])
    if not expiries: return {"symbol":symbol,"status":"WAIT","reason":"no active option expiry"}
    chain=option_chain(cfg["sid"],cfg["segment"],expiries[0])
    spot=float(chain.get("last_price") or p1m)
    strikes=sorted(float(x) for x in chain.get("oc",{}).keys())
    if not strikes: return {"symbol":symbol,"status":"WAIT","reason":"empty option chain"}
    atm=min(strikes,key=lambda x:abs(x-spot))
    side="ce" if direction=="LONG" else "pe"
    node=chain["oc"][f"{atm:.6f}"] if f"{atm:.6f}" in chain["oc"] else chain["oc"][str(min(chain["oc"],key=lambda x:abs(float(x)-atm)))]
    opt=node.get(side)
    if not opt or not opt.get("security_id"): return {"symbol":symbol,"status":"WAIT","reason":"ATM option unavailable"}
    conf=option_confirmation(opt["security_id"],direction)
    if not conf["ok"]:
        return {"symbol":symbol,"status":"RETEST","reason":"retest present; option confirmation absent","direction":direction,"level":level,"option":side.upper(),"strike":atm,"confirmation":conf}
    sl_dist=0.75*a
    entry=p1m
    sl=entry-sl_dist if direction=="LONG" else entry+sl_dist
    target=entry+sl_dist if direction=="LONG" else entry-sl_dist
    return {"symbol":symbol,"status":"SIGNAL","direction":direction,"entry":round(entry,2),"stop_loss":round(sl,2),"target":round(target,2),"risk_points":round(sl_dist,2),"max_hold_minutes":30,"level":round(level,2),"atr14_5m":round(a,2),"option_side":side.upper(),"strike":atm,"option_security_id":opt["security_id"],"option_ltp":opt.get("last_price"),"expiry":expiries[0],"confirmation":conf}


def run_once():
    if not CLIENT_ID or not TOKEN: raise RuntimeError("DHAN_CLIENT_ID / DHAN_ACCESS_TOKEN missing")
    now=datetime.now(IST)
    # Outside NSE cash/index session: publish CLOSED rather than stale signals.
    market_open=now.weekday()<5 and ((now.hour,now.minute)>=(9,15) and (now.hour,now.minute)<(15,30))
    if not market_open:
        with lock: state.update({"updated_at":now.isoformat(),"market":"CLOSED","signals":[],"errors":[],"engine":"RUNNING"})
        return
    signals=[]; errors=[]
    for symbol,cfg in UNDERLYINGS.items():
        try:
            r1=fetch_intraday(cfg["sid"],1,1)
            r5=fetch_intraday(cfg["sid"],5,1)
            r1h=fetch_intraday(cfg["sid"],60,30)
            signals.append(build_signal(symbol,cfg,r1,r5,r1h))
        except Exception as e:
            errors.append(f"{symbol}: {e}")
    with lock: state.update({"updated_at":now.isoformat(),"market":"OPEN","signals":signals,"errors":errors,"engine":"RUNNING"})


def loop():
    while True:
        started=time.time()
        try: run_once()
        except Exception as e:
            with lock: state.update({"updated_at":datetime.now(IST).isoformat(),"errors":[str(e)],"engine":"ERROR"})
        time.sleep(max(1,REFRESH_SECONDS-(time.time()-started)))


def html():
    with lock: s=json.loads(json.dumps(state))
    cards=[]
    for x in s["signals"]:
        status=x.get("status")
        cls="signal" if status=="SIGNAL" else "wait"
        cards.append(f"<div class='{cls}'><h2>{x.get('symbol')} — {status}</h2><pre>{json.dumps(x,indent=2)}</pre></div>")
    return f"""<!doctype html><html><head><meta charset='utf-8'><meta http-equiv='refresh' content='60'><meta name='viewport' content='width=device-width,initial-scale=1'><title>PSYCHO Intraday Signal Engine</title><style>body{{font-family:Arial;background:#0d1117;color:#e6edf3;padding:24px;max-width:1100px;margin:auto}}.signal,.wait{{padding:18px;margin:14px 0;border-radius:10px;background:#161b22;border:1px solid #30363d}}.signal{{border-color:#3fb950}}pre{{white-space:pre-wrap;overflow:auto}}small{{color:#8b949e}}</style></head><body><h1>PSYCHO INTRADAY SIGNAL ENGINE</h1><small>Independent engine • refresh: 60 seconds • no order placement</small><h3>Market: {s.get('market')} | Updated: {s.get('updated_at')}</h3>{''.join(cards)}<h3>Errors</h3><pre>{json.dumps(s.get('errors',[]),indent=2)}</pre></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/api/signal"):
            with lock: body=json.dumps(state,indent=2).encode()
            self.send_response(200); self.send_header("Content-Type","application/json"); self.end_headers(); self.wfile.write(body); return
        body=html().encode(); self.send_response(200); self.send_header("Content-Type","text/html; charset=utf-8"); self.end_headers(); self.wfile.write(body)
    def log_message(self,*args): pass


if __name__=="__main__":
    threading.Thread(target=loop,daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0",PORT),Handler).serve_forever()
