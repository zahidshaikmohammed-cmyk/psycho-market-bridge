import os, csv, io, json, time, threading, urllib.request, urllib.error
import base64, hashlib, hmac, struct, urllib.parse
from datetime import datetime, timedelta, time as dt_time
from zoneinfo import ZoneInfo
from flask import Flask, Response, jsonify

IST = ZoneInfo("Asia/Kolkata")
CLIENT_ID = os.environ.get("DHAN_CLIENT_ID", "").strip()
TOKEN = os.environ.get("DHAN_ACCESS_TOKEN", "").strip()
TOTP_SECRET = os.environ.get("DHAN_TOTP_SECRET", "").strip()
DHAN_PIN = os.environ.get("DHAN_PIN", "").strip()

INTRADAY_URL = "https://api.dhan.co/v2/charts/intraday"
HISTORICAL_URL = "https://api.dhan.co/v2/charts/historical"
EXPIRY_LIST_URL = "https://api.dhan.co/v2/optionchain/expirylist"
OPTION_CHAIN_URL = "https://api.dhan.co/v2/optionchain"
MARKET_QUOTE_URL = "https://api.dhan.co/v2/marketfeed/quote"
PROFILE_URL = "https://api.dhan.co/v2/profile"
AUTH_URL = "https://auth.dhan.co/app/generateAccessToken"
INSTRUMENT_MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master-detailed.csv"

MARKET_OPEN = dt_time(9, 15)
MARKET_CLOSE = dt_time(15, 40)
REFRESH_INTERVAL_SECONDS = 60
OPTION_CHAIN_DELAY_SECONDS = 3.2
TOKEN_REGEN_GUARD_SECONDS = 120
LIMITS = {"1M": 400, "5M": 200, "15M": 120, "1H": 100, "1D": 120, "1W": 80}
OPTION_STRIKES_EACH_SIDE = 10
refresh_lock = threading.Lock()
auth_lock = threading.Lock()
last_token_generation = 0.0
auth_state = {"status": "NOT_CHECKED", "last_error": None, "last_success": None, "expiry": None}

INSTRUMENTS = {
    "NIFTY": {
        "display_name": "NIFTY", "security_id": "13", "underlying_symbol": "NIFTY",
        "market_file": "nifty-live.json", "option_file": "nifty-option-chain.json",
        "snapshot_file": "nifty-session-snapshot.json", "futures_file": "nifty-futures-live.json"
    },
    "BANKNIFTY": {
        "display_name": "BANK NIFTY", "security_id": "25", "underlying_symbol": "BANKNIFTY",
        "market_file": "banknifty-live.json", "option_file": "banknifty-option-chain.json",
        "snapshot_file": "banknifty-session-snapshot.json", "futures_file": "banknifty-futures-live.json"
    }
}

def now_ist(): return datetime.now(IST)
def iso_now(): return now_ist().isoformat()
def is_weekday(v=None): return (v or now_ist()).weekday() < 5
def is_market_window(v=None):
    v = v or now_ist()
    return is_weekday(v) and MARKET_OPEN <= v.time() <= MARKET_CLOSE

def market_status():
    cur = now_ist()
    if not is_weekday(cur): reason = "WEEKEND"
    elif cur.time() < MARKET_OPEN: reason = "PRE_MARKET"
    elif cur.time() > MARKET_CLOSE: reason = "SESSION_FINISHED"
    else:
        return {"status":"OPEN","reason":"LIVE_MARKET_WINDOW","current_time":cur.isoformat(),"market_open":"09:15 IST","collection_end":"15:40 IST"}
    return {"status":"CLOSED","reason":reason,"current_time":cur.isoformat(),"market_open":"09:15 IST","collection_end":"15:40 IST"}

def write_json_atomic(filename, data):
    tmp = filename + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.flush(); os.fsync(f.fileno())
    os.replace(tmp, filename)

def read_json_file(filename):
    if not os.path.exists(filename): return None
    try:
        with open(filename, "r", encoding="utf-8") as f: return json.load(f)
    except Exception as e:
        print(f"JSON READ ERROR {filename}: {e}", flush=True); return None

def totp(secret, at=None):
    secret = "".join(secret.split()).replace("-", "").upper()
    key = base64.b32decode(secret + "=" * ((8-len(secret)%8)%8), casefold=True)
    counter = int((at if at is not None else time.time()) // 30)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 15
    value = struct.unpack(">I", digest[offset:offset+4])[0] & 0x7fffffff
    return f"{value % 1000000:06d}"

def generate_access_token(force=False):
    global TOKEN, last_token_generation
    if not (CLIENT_ID and TOTP_SECRET and DHAN_PIN):
        return None
    with auth_lock:
        now = time.time()
        if not force and TOKEN:
            return TOKEN
        if now - last_token_generation < TOKEN_REGEN_GUARD_SECONDS:
            return TOKEN or None
        try:
            code = totp(TOTP_SECRET, now)
            query = urllib.parse.urlencode({"dhanClientId": CLIENT_ID, "pin": DHAN_PIN, "totp": code})
            req = urllib.request.Request(f"{AUTH_URL}?{query}", data=b"", headers={"Accept":"application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=20) as r:
                payload = json.loads(r.read().decode("utf-8"))
            token = (payload.get("accessToken") or "").strip()
            if not token:
                raise RuntimeError(payload.get("errorMessage") or payload.get("message") or "No accessToken in Dhan response")
            TOKEN = token
            os.environ["DHAN_ACCESS_TOKEN"] = token
            last_token_generation = now
            auth_state.update({"status":"AUTHENTICATED","last_error":None,"last_success":iso_now(),"expiry":payload.get("expiryTime")})
            print(f"DHAN AUTH: token generated; expiry={payload.get('expiryTime')}", flush=True)
            return token
        except Exception as e:
            last_token_generation = now
            auth_state.update({"status":"AUTH_FAILED","last_error":f"{type(e).__name__}: {e}"})
            print(f"DHAN AUTH GENERATION FAILED: {type(e).__name__}: {e}", flush=True)
            return None

def _http_error_detail(exc):
    try:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw)
            code = payload.get("errorCode") or payload.get("errorType")
            message = payload.get("errorMessage") or payload.get("message") or raw
            return f"{code}: {message}" if code else message
        except Exception:
            return raw or str(exc)
    except Exception:
        return str(exc)

def dhan_request(url, payload=None, client_id_required=False, _retry=True):
    global TOKEN
    if not TOKEN and TOTP_SECRET and DHAN_PIN:
        generate_access_token(force=True)
    if not TOKEN:
        raise RuntimeError("DHAN_AUTH_MISSING: set DHAN_ACCESS_TOKEN or configure DHAN_PIN + DHAN_TOTP_SECRET")
    body = json.dumps(payload or {}).encode("utf-8")
    headers = {"Accept":"application/json","Content-Type":"application/json","access-token":TOKEN}
    if client_id_required: headers["client-id"] = CLIENT_ID
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = _http_error_detail(e)
        if e.code == 401 and _retry and TOTP_SECRET and DHAN_PIN:
            print("DHAN AUTH: 401 received; attempting automatic token regeneration", flush=True)
            new_token = generate_access_token(force=True)
            if new_token and new_token != headers["access-token"]:
                return dhan_request(url, payload, client_id_required, _retry=False)
        if e.code == 401:
            auth_state.update({"status":"AUTH_FAILED","last_error":detail})
        raise RuntimeError(f"DHAN_HTTP_{e.code}: {detail}") from None

def validate_auth():
    global TOKEN
    if not TOKEN and TOTP_SECRET and DHAN_PIN:
        generate_access_token(force=True)
    if not TOKEN:
        auth_state.update({"status":"AUTH_FAILED","last_error":"No access token configured"})
        return False
    try:
        req = urllib.request.Request(PROFILE_URL, headers={"Accept":"application/json","access-token":TOKEN}, method="GET")
        with urllib.request.urlopen(req, timeout=20) as r:
            json.loads(r.read().decode("utf-8"))
        auth_state.update({"status":"AUTHENTICATED","last_error":None,"last_success":iso_now()})
        return True
    except urllib.error.HTTPError as e:
        detail = _http_error_detail(e)
        if e.code == 401 and TOTP_SECRET and DHAN_PIN:
            if generate_access_token(force=True):
                return validate_auth()
        auth_state.update({"status":"AUTH_FAILED","last_error":detail})
        print(f"DHAN AUTH VALIDATION FAILED: {detail}", flush=True)
        return False
    except Exception as e:
        auth_state.update({"status":"AUTH_ERROR","last_error":f"{type(e).__name__}: {e}"})
        return False

def safe_fetch(label, fn):
    try:
        result = fn(); print(f"SUCCESS: {label}", flush=True); return result
    except Exception as e:
        print(f"ERROR: {label}: {e}", flush=True)
        return {"error": str(e), "label": label, "generated_at": iso_now()}

def normalize_candles(raw):
    if not isinstance(raw, dict): return []
    keys = ["timestamp","open","high","low","close","volume"]
    arrays = {k: raw.get(k,[]) or [] for k in keys}
    count = min((len(arrays[k]) for k in keys), default=0)
    out=[]
    for i in range(count):
        try: ts=int(arrays["timestamp"][i])
        except (TypeError,ValueError): continue
        out.append({"timestamp":ts,"open":arrays["open"][i],"high":arrays["high"][i],"low":arrays["low"][i],"close":arrays["close"][i],"volume":arrays["volume"][i]})
    return sorted(out,key=lambda x:x["timestamp"])

def candle_datetime(c):
    try: return datetime.fromtimestamp(int(c["timestamp"]), IST)
    except Exception: return None

def filter_session_candles(candles, session_date):
    out=[]
    for c in candles or []:
        dt=candle_datetime(c)
        if dt and dt.date()==session_date and MARKET_OPEN <= dt.time() <= MARKET_CLOSE: out.append(c)
    return sorted(out,key=lambda x:x["timestamp"])

def trim(candles,tf): return (candles or [])[-LIMITS.get(tf,len(candles or [])):]

def fetch_intraday(sid, interval, session_date=None, exchange_segment="IDX_I", instrument="INDEX", oi=False):
    current=now_ist(); session_date=session_date or current.date()
    payload={"securityId":str(sid),"exchangeSegment":exchange_segment,"instrument":instrument,"interval":str(interval),"oi":bool(oi),"fromDate":(current-timedelta(days=10)).strftime("%Y-%m-%d 09:15:00"),"toDate":current.strftime("%Y-%m-%d %H:%M:%S")}
    return filter_session_candles(normalize_candles(dhan_request(INTRADAY_URL,payload)),session_date)

def fetch_daily(sid):
    current=now_ist(); payload={"securityId":str(sid),"exchangeSegment":"IDX_I","instrument":"INDEX","expiryCode":0,"oi":False,"fromDate":(current-timedelta(days=730)).strftime("%Y-%m-%d"),"toDate":(current+timedelta(days=1)).strftime("%Y-%m-%d")}
    return normalize_candles(dhan_request(HISTORICAL_URL,payload))

def candle_date(c):
    dt=candle_datetime(c); return dt.date() if dt else None

def previous_daily(daily,session_date):
    candidates=[(candle_date(c),c) for c in daily if candle_date(c) and candle_date(c)<session_date]
    if not candidates: return None
    _,c=max(candidates,key=lambda x:x[0])
    return {"date":candle_date(c).isoformat(),"open":c.get("open"),"high":c.get("high"),"low":c.get("low"),"close":c.get("close"),"volume":c.get("volume"),"timestamp":c.get("timestamp")}

def build_current_daily(candles,session_date):
    session=filter_session_candles(candles,session_date)
    if not session: return None
    highs=[c.get("high") for c in session if c.get("high") is not None]; lows=[c.get("low") for c in session if c.get("low") is not None]
    return {"timestamp":session[0]["timestamp"],"open":session[0].get("open"),"high":max(highs) if highs else None,"low":min(lows) if lows else None,"close":session[-1].get("close"),"volume":sum(c.get("volume") or 0 for c in session),"session_date":session_date.isoformat(),"developing":is_market_window()}

def merge_daily(history,current,session_date):
    out=[c for c in history if candle_date(c)!=session_date]
    if current: out.append(current)
    return sorted(out,key=lambda x:x.get("timestamp",0))

def aggregate_weekly(daily):
    weeks={}
    for c in daily:
        dt=candle_datetime(c)
        if not dt: continue
        iso=dt.isocalendar(); key=f"{iso.year}-{iso.week:02d}"
        if key not in weeks:
            weeks[key]={"timestamp":c.get("timestamp"),"week":key,"open":c.get("open"),"high":c.get("high"),"low":c.get("low"),"close":c.get("close"),"volume":c.get("volume") or 0}
        else:
            w=weeks[key]; w["high"]=max(w["high"],c.get("high")); w["low"]=min(w["low"],c.get("low")); w["close"]=c.get("close"); w["volume"]+=c.get("volume") or 0
    return sorted(weeks.values(),key=lambda x:x["timestamp"])

def gap(previous,current_open):
    if not previous or previous.get("close") is None or current_open is None: return {"available":False,"type":"UNAVAILABLE","points":None,"percent":None}
    prev=float(previous["close"]); op=float(current_open); pts=op-prev
    return {"available":True,"type":"GAP_UP" if pts>0 else "GAP_DOWN" if pts<0 else "FLAT","previous_close":prev,"current_open":op,"points":round(pts,2),"percent":round(pts/prev*100,4) if prev else None}

def discover_nearest_futures(symbol):
    req=urllib.request.Request(INSTRUMENT_MASTER_URL,headers={"User-Agent":"PSYCHO-MARKET-BRIDGE"})
    with urllib.request.urlopen(req,timeout=30) as r: text=r.read().decode("utf-8",errors="replace")
    today=now_ist().date(); matches=[]
    for row in csv.DictReader(io.StringIO(text)):
        try:
            if row.get("EXCH_ID")!="NSE" or row.get("SEGMENT")!="D" or row.get("INSTRUMENT")!="FUTIDX": continue
            if (row.get("UNDERLYING_SYMBOL") or "").upper()!=symbol.upper(): continue
            expiry=datetime.strptime((row.get("SM_EXPIRY_DATE") or "")[:10],"%Y-%m-%d").date()
            if expiry>=today: matches.append((expiry,row))
        except Exception: continue
    if not matches: raise RuntimeError(f"No active FUTIDX contract found for {symbol}")
    expiry,row=min(matches,key=lambda x:x[0])
    return {"security_id":str(row.get("SECURITY_ID") or row.get("SEM_SMST_SECURITY_ID")),"expiry":expiry.isoformat(),"trading_symbol":row.get("SEM_TRADING_SYMBOL") or row.get("DISPLAY_NAME") or row.get("SYMBOL_NAME"),"underlying_symbol":symbol}

def fetch_futures(contract,session_date):
    candles=fetch_intraday(contract["security_id"],5,session_date,"NSE_FNO","FUTIDX",True); latest=candles[-1] if candles else None
    return {"status":"LIVE" if latest else "UNAVAILABLE","source":"DHAN","generated_at":iso_now(),**contract,"timeframe":"5M","latest":latest,"candles_5m":trim(candles,"5M"),"oi_enabled":True}

def fetch_market_quote(sid): return dhan_request(MARKET_QUOTE_URL,{"NSE_FNO":[int(sid)]},client_id_required=True)

def clean_futures_quote(raw,sid):
    data=(((raw or {}).get("data") or {}).get("NSE_FNO") or {}).get(str(sid)) or {}; depth=data.get("depth") or {}
    return {"security_id":sid,"last_price":data.get("last_price"),"last_quantity":data.get("last_quantity"),"last_trade_time":data.get("last_trade_time"),"average_price":data.get("average_price"),"volume":data.get("volume"),"oi":data.get("oi"),"oi_day_high":data.get("oi_day_high"),"oi_day_low":data.get("oi_day_low"),"buy_quantity":data.get("buy_quantity"),"sell_quantity":data.get("sell_quantity"),"ohlc":data.get("ohlc"),"depth":{"buy":depth.get("buy",[]),"sell":depth.get("sell",[])}}

def fetch_expiries(sid):
    raw=dhan_request(EXPIRY_LIST_URL,{"UnderlyingScrip":int(sid),"UnderlyingSeg":"IDX_I"},client_id_required=True); expiries=raw.get("data",[])
    if not expiries: raise RuntimeError("No active option expiry returned by DHAN")
    return expiries

def clean_option_leg(leg):
    if not isinstance(leg,dict): return None
    oi=leg.get("oi"); prev=leg.get("previous_oi")
    try: change=float(oi)-float(prev) if oi is not None and prev is not None else None
    except (TypeError,ValueError): change=None
    return {"security_id":leg.get("security_id"),"last_price":leg.get("last_price"),"average_price":leg.get("average_price"),"oi":oi,"previous_oi":prev,"oi_change":change,"volume":leg.get("volume"),"previous_volume":leg.get("previous_volume"),"implied_volatility":leg.get("implied_volatility"),"top_bid_price":leg.get("top_bid_price"),"top_bid_quantity":leg.get("top_bid_quantity"),"top_ask_price":leg.get("top_ask_price"),"top_ask_quantity":leg.get("top_ask_quantity"),"greeks":leg.get("greeks") or {}}

def build_option_chain(sid,session_date):
    expiry=fetch_expiries(sid)[0]; time.sleep(OPTION_CHAIN_DELAY_SECONDS)
    raw=dhan_request(OPTION_CHAIN_URL,{"UnderlyingScrip":int(sid),"UnderlyingSeg":"IDX_I","Expiry":expiry},client_id_required=True); data=raw.get("data") or {}; ltp=data.get("last_price"); oc=data.get("oc") or {}; rows=[]
    for strike,sd in oc.items():
        try: rows.append((float(strike),sd if isinstance(sd,dict) else {}))
        except (TypeError,ValueError): pass
    rows.sort(key=lambda x:x[0])
    if not rows: raise RuntimeError("No option strikes returned")
    try: atm=min(range(len(rows)),key=lambda i:abs(rows[i][0]-float(ltp))) if ltp is not None else len(rows)//2
    except (TypeError,ValueError): atm=len(rows)//2
    selected=rows[max(0,atm-OPTION_STRIKES_EACH_SIDE):min(len(rows),atm+OPTION_STRIKES_EACH_SIDE+1)]
    strikes={str(s):{"strike":s,"CE":clean_option_leg(sd.get("ce")),"PE":clean_option_leg(sd.get("pe"))} for s,sd in selected}
    return {"status":"LIVE","source":"DHAN","instrument_security_id":str(sid),"session_date":session_date.isoformat(),"generated_at":iso_now(),"expiry":expiry,"underlying_ltp":ltp,"atm_strike":rows[atm][0],"strike_range":{"below_atm":OPTION_STRIKES_EACH_SIDE,"above_atm":OPTION_STRIKES_EACH_SIDE,"total_returned":len(selected)},"strikes":strikes}

def valid_market(market):
    if not isinstance(market,dict): return False
    cur=market.get("current_session") or {}
    return cur.get("last_price") is not None or any((market.get("timeframes") or {}).get(tf) for tf in ("1M","5M","15M","1H","1D","1W"))

def build_instrument(key,config,session_date):
    name=config["display_name"]; sid=config["security_id"]; print(f"BUILDING {name} {session_date}",flush=True)
    c1=safe_fetch(f"{name} 1M",lambda:fetch_intraday(sid,1,session_date)); c5=safe_fetch(f"{name} 5M",lambda:fetch_intraday(sid,5,session_date)); c15=safe_fetch(f"{name} 15M",lambda:fetch_intraday(sid,15,session_date)); c1h=safe_fetch(f"{name} 1H",lambda:fetch_intraday(sid,60,session_date)); dh=safe_fetch(f"{name} 1D",lambda:fetch_daily(sid))
    c1,c5,c15,c1h,dh=[x if isinstance(x,list) else [] for x in (c1,c5,c15,c1h,dh)]
    prev=previous_daily(dh,session_date); curdaily=build_current_daily(c1,session_date); daily=merge_daily(dh,curdaily,session_date); weekly=aggregate_weekly(daily)
    market={"status":"LIVE" if c1 else "DEGRADED","source":"DHAN","instrument":name,"security_id":sid,"session_date":session_date.isoformat(),"generated_at":iso_now(),"session_isolation":True,"current_session":{"available":bool(c1),"open":c1[0].get("open") if c1 else None,"high":max([c.get("high") for c in c1 if c.get("high") is not None],default=None),"low":min([c.get("low") for c in c1 if c.get("low") is not None],default=None),"last_price":c1[-1].get("close") if c1 else None,"latest_candle_time":candle_datetime(c1[-1]).isoformat() if c1 and candle_datetime(c1[-1]) else None,"gap":gap(prev,c1[0].get("open") if c1 else None)},"previous_session":prev,"timeframes":{"1M":trim(c1,"1M"),"5M":trim(c5,"5M"),"15M":trim(c15,"15M"),"1H":trim(c1h,"1H"),"1D":trim(daily,"1D"),"1W":trim(weekly,"1W")},"volume_note":"Underlying index volume is preserved exactly as returned by DHAN; futures volume/OI are captured separately."}
    if not valid_market(market):
        print(f"DATA HEALTH FAILED: {name}; no valid market candles returned",flush=True)
        return {"status":"DATA_FAILED","source":"DHAN","instrument":name,"session_date":session_date.isoformat(),"generated_at":iso_now(),"error":"NO_VALID_MARKET_DATA","market":market}
    write_json_atomic(config["market_file"],market)
    futures={"status":"UNAVAILABLE","source":"DHAN","generated_at":iso_now()}
    try:
        contract=discover_nearest_futures(config["underlying_symbol"]); futures=fetch_futures(contract,session_date); q=safe_fetch(f"{name} FUTURES QUOTE/DEPTH",lambda:fetch_market_quote(contract["security_id"])); futures["live_quote"]=clean_futures_quote(q,contract["security_id"]) if isinstance(q,dict) and "error" not in q else {"status":"ERROR","details":q}
    except Exception as e:
        futures={"status":"ERROR","source":"DHAN","generated_at":iso_now(),"message":str(e)}; print(f"FUTURES ERROR {name}: {e}",flush=True)
    write_json_atomic(config["futures_file"],futures)
    option=safe_fetch(f"{name} OPTION CHAIN",lambda:build_option_chain(sid,session_date))
    if not isinstance(option,dict) or "error" in option: option={"status":"ERROR","source":"DHAN","session_date":session_date.isoformat(),"generated_at":iso_now(),"details":option}
    write_json_atomic(config["option_file"],option)
    snapshot={"status":"LIVE","source":"DHAN","instrument":name,"session_date":session_date.isoformat(),"snapshot_generated_at":iso_now(),"market":market,"futures":futures,"option_chain":option}
    write_json_atomic(config["snapshot_file"],snapshot); return snapshot

def refresh_all():
    if not refresh_lock.acquire(blocking=False): print("REFRESH SKIPPED: previous cycle still running",flush=True); return False
    try:
        date=now_ist().date(); ready=0
        for key,config in INSTRUMENTS.items():
            try:
                result=build_instrument(key,config,date)
                if isinstance(result,dict) and result.get("status")=="LIVE": ready+=1
            except Exception as e: print(f"INSTRUMENT BUILD ERROR {key}: {e}",flush=True)
        print(f"REFRESH COMPLETE: VALID DATA {ready}/{len(INSTRUMENTS)}",flush=True); return ready==len(INSTRUMENTS)
    finally: refresh_lock.release()

def live_refresh_worker():
    print("PSYCHO LIVE REFRESH WORKER STARTED",flush=True); last_state=None
    while True:
        try:
            cur=now_ist()
            if is_market_window(cur):
                if last_state!="OPEN": print(f"MARKET WINDOW OPEN {cur.isoformat()}",flush=True); last_state="OPEN"
                started=time.monotonic(); refresh_all(); time.sleep(max(1,REFRESH_INTERVAL_SECONDS-(time.monotonic()-started)))
            else:
                if last_state!="CLOSED": print(f"MARKET WINDOW CLOSED {cur.isoformat()}",flush=True); last_state="CLOSED"
                time.sleep(30)
        except Exception as e: print(f"BACKGROUND WORKER ERROR: {e}",flush=True); time.sleep(10)

def get_state(key):
    cfg=INSTRUMENTS[key]; snap=read_json_file(cfg["snapshot_file"])
    if snap: return snap
    return {"status":"WAITING","source":"DHAN","instrument":cfg["display_name"],"market":read_json_file(cfg["market_file"]),"futures":read_json_file(cfg["futures_file"]),"option_chain":read_json_file(cfg["option_file"])}

def build_phase2_text():
    lines=["PSYCHO MARKET BRIDGE — PHASE 2 LIVE","="*78,"BRIDGE STATUS: ONLINE",f"AUTH STATUS: {auth_state['status']}","DATA SOURCE: DHAN",f"SERVER TIME: {now_ist().strftime('%Y-%m-%d %H:%M:%S IST')}",f"MARKET STATUS: {market_status()['status']}","LIVE COLLECTION WINDOW: 09:15-15:40 IST",f"REFRESH TARGET: ~{REFRESH_INTERVAL_SECONDS} SECONDS","TIMEFRAMES: 1M + 5M + 15M + 1H + 1D + 1W","DERIVATIVES: NEAREST INDEX FUTURE + 5M OHLCV/OI + LIVE QUOTE/DEPTH","OPTIONS: NEAREST EXPIRY + ATM +/- 10 STRIKES"]
    for key,cfg in INSTRUMENTS.items():
        state=get_state(key); market=state.get("market") or {}; cur=market.get("current_session") or {}
        lines += ["","#"*78,cfg["display_name"],"#"*78,f"STATUS: {state.get('status')}",f"SESSION DATE: {market.get('session_date',state.get('session_date'))}",f"GENERATED AT: {market.get('generated_at')}",f"LAST PRICE: {cur.get('last_price') or 'N/A'}",f"GAP: {(cur.get('gap') or {}).get('type','N/A')} {(cur.get('gap') or {}).get('points','N/A')}" ]
        tfs=market.get("timeframes") or {}
        for tf in ("1M","5M","15M","1H","1D","1W"):
            candles=tfs.get(tf) or []; dt=candle_datetime(candles[-1]) if candles else None; latest=dt.strftime("%Y-%m-%d %H:%M:%S IST") if dt else "N/A"; lines.append(f"{tf}: {len(candles)} candles | latest={latest}")
        fut=state.get("futures") or {}; latest=fut.get("latest") or {}; quote=fut.get("live_quote") or {}; opt=state.get("option_chain") or {}
        lines += ["","FUTURES",f"STATUS: {fut.get('status')}",f"SYMBOL: {fut.get('trading_symbol')}",f"EXPIRY: {fut.get('expiry')}",f"SECURITY ID: {fut.get('security_id')}",f"5M FUTURES: {latest.get('close','N/A')} | V={latest.get('volume','N/A')} | OI={latest.get('oi','N/A')}",f"QUOTE: LTP={quote.get('last_price','N/A')} | VOL={quote.get('volume','N/A')} | OI={quote.get('oi','N/A')}","","OPTION CHAIN",f"STATUS: {opt.get('status')}",f"EXPIRY: {opt.get('expiry')}",f"UNDERLYING LTP: {opt.get('underlying_ltp')}",f"ATM: {opt.get('atm_strike')}",f"STRIKES: {len(opt.get('strikes') or {})}"]
    return "\n".join(lines+["","="*78,"END — PSYCHO MARKET BRIDGE PHASE 2 LIVE","="*78])

app=Flask(__name__)
def text_response(text,status=200): return Response(text,status=status,content_type="text/plain; charset=utf-8")

@app.route("/")
def home():
    return Response("""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>PSYCHO TRADING // PHASE 2</title></head><body style='font-family:system-ui;background:#05050b;color:#eee;padding:30px'><h1>PSYCHO TRADING // PHASE 2</h1><p>DHAN live market-data bridge.</p><p><a href='/bridge-status' style='color:#6ff'>Bridge Status</a> · <a href='/auth-status' style='color:#6ff'>Auth Status</a> · <a href='/phase2-live' style='color:#6ff'>Phase 2 Live</a></p></body></html>""",content_type="text/html; charset=utf-8")

@app.route("/phase2-live")
def phase2_live(): return text_response(build_phase2_text())

@app.route("/auth-status")
def auth_status():
    return jsonify({"service":"PSYCHO MARKET BRIDGE","authentication":{"status":auth_state["status"],"client_id_present":bool(CLIENT_ID),"access_token_present":bool(TOKEN),"totp_configured":bool(TOTP_SECRET),"pin_configured":bool(DHAN_PIN),"last_success":auth_state["last_success"],"last_error":auth_state["last_error"],"token_expiry":auth_state["expiry"]}})

@app.route("/bridge-status")
def bridge_status():
    instruments={}
    for key,cfg in INSTRUMENTS.items():
        snap=read_json_file(cfg["snapshot_file"]); market=(snap or {}).get("market") if isinstance(snap,dict) else {}
        instruments[key]={"data_ready":bool(snap and snap.get("status")=="LIVE" and valid_market(market)),"market_file_exists":os.path.exists(cfg["market_file"]),"option_file_exists":os.path.exists(cfg["option_file"]),"futures_file_exists":os.path.exists(cfg["futures_file"]),"snapshot_file_exists":os.path.exists(cfg["snapshot_file"]),"session_date":snap.get("session_date") if isinstance(snap,dict) else None,"snapshot_generated_at":snap.get("snapshot_generated_at") if isinstance(snap,dict) else None,"last_price":((market or {}).get("current_session") or {}).get("last_price")}
    return jsonify({"service":"PSYCHO MARKET BRIDGE","server":"ONLINE","source":"DHAN","server_time":iso_now(),"market":market_status(),"authentication":auth_state,"data_health":"READY" if all(v["data_ready"] for v in instruments.values()) else "FAILED","refresh_target_seconds":REFRESH_INTERVAL_SECONDS,"instruments":instruments})

def file_json(filename,waiting):
    data=read_json_file(filename)
    return (jsonify(data),200) if data is not None else (jsonify({"status":"WAITING","source":"DHAN","message":waiting}),503)

@app.route("/nifty-live")
def nifty_live(): return file_json(INSTRUMENTS["NIFTY"]["market_file"],"NIFTY market data unavailable")
@app.route("/nifty-option-chain")
def nifty_option_chain(): return file_json(INSTRUMENTS["NIFTY"]["option_file"],"NIFTY option chain unavailable")
@app.route("/nifty-futures-live")
def nifty_futures_live(): return file_json(INSTRUMENTS["NIFTY"]["futures_file"],"NIFTY futures data unavailable")
@app.route("/banknifty-live")
def banknifty_live(): return file_json(INSTRUMENTS["BANKNIFTY"]["market_file"],"BANK NIFTY market data unavailable")
@app.route("/banknifty-option-chain")
def banknifty_option_chain(): return file_json(INSTRUMENTS["BANKNIFTY"]["option_file"],"BANK NIFTY option chain unavailable")
@app.route("/banknifty-futures-live")
def banknifty_futures_live(): return file_json(INSTRUMENTS["BANKNIFTY"]["futures_file"],"BANK NIFTY futures data unavailable")

if __name__ == "__main__":
    print("PSYCHO MARKET BRIDGE STARTING",flush=True); print("SOURCE: DHAN",flush=True); print("DATA: 1M/5M/15M/1H/1D/1W + FUTURES OI + OPTION CHAIN + DEPTH",flush=True); print("LIVE WINDOW: 09:15-15:40 IST",flush=True)
    validate_auth()
    if not TOKEN and not (TOTP_SECRET and DHAN_PIN): print("AUTH CONFIGURATION REQUIRED: set DHAN_ACCESS_TOKEN or DHAN_PIN + DHAN_TOTP_SECRET",flush=True)
    threading.Thread(target=live_refresh_worker,daemon=True,name="psycho-live-refresh").start(); port=int(os.environ.get("PORT",10000)); app.run(host="0.0.0.0",port=port,threaded=True,use_reloader=False)
