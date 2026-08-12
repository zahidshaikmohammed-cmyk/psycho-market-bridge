import json, os, time, urllib.request
from datetime import datetime, time as dt_time, timedelta
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
BRIDGE_BASE_URL = os.environ.get("BRIDGE_BASE_URL", "http://127.0.0.1:10000").rstrip("/")
POLL_SECONDS = int(os.environ.get("SIGNAL_POLL_SECONDS", "5"))
RULES_FILE = os.environ.get("SIGNAL_RULES_FILE", "strategy_rules.json")
OUTPUT_FILE = os.environ.get("SIGNAL_OUTPUT_FILE", "signal-live.json")
STATE_FILE = os.environ.get("SIGNAL_STATE_FILE", "signal-engine-state.json")


def now(): return datetime.now(IST)


def get_json(path):
    req = urllib.request.Request(BRIDGE_BASE_URL + path, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def write_atomic(filename, data):
    tmp = filename + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.flush(); os.fsync(f.fileno())
    os.replace(tmp, filename)


def read_json(filename, default):
    try:
        with open(filename, "r", encoding="utf-8") as f: return json.load(f)
    except Exception: return default


def candles(state, tf): return (((state or {}).get("timeframes") or {}).get(tf) or [])


def candle_dt(c):
    try: return datetime.fromtimestamp(int(c["timestamp"]), IST)
    except Exception: return None


def closed_5m(candles_):
    cutoff = now() - timedelta(minutes=5)
    return [c for c in candles_ if candle_dt(c) and candle_dt(c) <= cutoff]


def vwap(candles_):
    pv = vol = 0.0
    for c in candles_:
        try:
            h,l,cl,v=map(float,(c["high"],c["low"],c["close"],c.get("volume") or 0))
            if v > 0: pv += ((h+l+cl)/3.0)*v; vol += v
        except Exception: pass
    return pv/vol if vol else None


def opening_range(candles_, start, end):
    s,e=dt_time.fromisoformat(start),dt_time.fromisoformat(end); selected=[]
    for c in candles_:
        d=candle_dt(c)
        if d and s <= d.time() < e: selected.append(c)
    if not selected: return None
    return {"high":max(float(c["high"]) for c in selected),"low":min(float(c["low"]) for c in selected),"candles":len(selected)}


def session_return(market):
    cur=market.get("current_session") or {}; op=cur.get("open"); px=cur.get("last_price")
    try: return (float(px)-float(op))/float(op)*100.0 if op else None
    except Exception: return None


def volume_confirm(c5, rules):
    closed=closed_5m(c5)
    if len(closed) < rules["filters"]["volume_lookback_bars"] + 1: return False, None
    last=closed[-1]
    try:
        current=float(last.get("volume") or 0)
        hist=[float(x.get("volume") or 0) for x in closed[-(rules["filters"]["volume_lookback_bars"]+1):-1]]
        avg=sum(hist)/len(hist) if hist else 0
        return avg > 0 and current >= avg*rules["filters"]["volume_multiplier"], (current/avg if avg else None)
    except Exception: return False, None


def latest_breakout(c5, orb, rules):
    closed=closed_5m(c5); maxbars=rules["breakout"]["retest_max_bars"]
    for c in reversed(closed):
        d=candle_dt(c)
        if not d or d.time() < dt_time.fromisoformat(rules["entry_window"]["start"]): continue
        cl=float(c["close"]); op=float(c["open"])
        if cl > orb["high"] and op <= orb["high"]:
            return {"side":"LONG","timestamp":c["timestamp"],"level":orb["high"],"bars_since":0}
        if cl < orb["low"] and op >= orb["low"]:
            return {"side":"SHORT","timestamp":c["timestamp"],"level":orb["low"],"bars_since":0}
    return None


def retest_confirm(c5, breakout, rules):
    closed=closed_5m(c5)
    try: idx=next(i for i,c in enumerate(closed) if c["timestamp"]==breakout["timestamp"])
    except StopIteration: return False, None
    after=closed[idx+1:idx+1+rules["breakout"]["retest_max_bars"]]
    tol=rules["breakout"]["retest_tolerance_points"]; level=breakout["level"]
    for c in after:
        lo,hi,cl=float(c["low"]),float(c["high"]),float(c["close"])
        if breakout["side"]=="LONG" and lo <= level+tol and cl > level: return True,c
        if breakout["side"]=="SHORT" and hi >= level-tol and cl < level: return True,c
    return False,None


def oi_wall(option_chain, side, px, maxdist):
    strikes=(option_chain or {}).get("strikes") or {}; candidates=[]
    for key,row in strikes.items():
        try:
            strike=float(row.get("strike",key)); leg=row.get("CE" if side=="LONG" else "PE") or {}; oi=float(leg.get("oi") or 0)
            if oi<=0: continue
            if side=="LONG" and strike>px: candidates.append((strike,oi))
            if side=="SHORT" and strike<px: candidates.append((strike,oi))
        except Exception: pass
    if not candidates: return None
    wall=max(candidates,key=lambda x:x[1])
    distance=abs(wall[0]-px)
    return {"strike":wall[0],"oi":wall[1],"distance_points":distance,"blocked":distance<=maxdist}


def evaluate(symbol, market, peer_market, option_chain, state, rules):
    session=market.get("current_session") or {}; px=session.get("last_price")
    if px is None: return {"status":"NO_DATA"}
    t=now().time()
    if t < dt_time.fromisoformat(rules["entry_window"]["start"]): return {"status":"WAITING_ENTRY_WINDOW"}
    if t > dt_time.fromisoformat(rules["entry_window"]["end"]): return {"status":"NO_SIGNAL_TIME_WINDOW"}
    c5=candles(market,"5M"); orb=opening_range(c5,rules["opening_range"]["start"],rules["opening_range"]["end"])
    if not orb: return {"status":"WAITING_ORB"}
    px=float(px); closed=closed_5m(c5)
    breakout=latest_breakout(c5,orb,rules)
    if not breakout: return {"status":"NO_VALID_BREAKOUT","orb":orb}
    ok_retest,retest=retest_confirm(c5,breakout,rules)
    if not ok_retest: return {"status":"WAITING_RETEST","direction":breakout["side"],"orb":orb}
    vw=vwap(candles(market,"1M"))
    if vw is None: return {"status":"WAITING_VWAP"}
    if breakout["side"]=="LONG" and px <= vw: return {"status":"FILTER_FAIL_VWAP","direction":"LONG","vwap":vw}
    if breakout["side"]=="SHORT" and px >= vw: return {"status":"FILTER_FAIL_VWAP","direction":"SHORT","vwap":vw}
    vol_ok,vol_ratio=volume_confirm(c5,rules)
    if not vol_ok: return {"status":"FILTER_FAIL_VOLUME","volume_ratio":vol_ratio}
    own=session_return(market); peer=session_return(peer_market)
    if own is None or peer is None: return {"status":"WAITING_RELATIVE_STRENGTH"}
    rs=own-peer; threshold=rules["filters"]["relative_strength_min_pct_points"]
    if breakout["side"]=="LONG" and rs < threshold: return {"status":"FILTER_FAIL_RELATIVE_STRENGTH","relative_strength_pct_points":rs}
    if breakout["side"]=="SHORT" and rs > -threshold: return {"status":"FILTER_FAIL_RELATIVE_STRENGTH","relative_strength_pct_points":rs}
    wall=oi_wall(option_chain,breakout["side"],px,rules["filters"]["oi_wall_max_distance_points"])
    if not wall: return {"status":"WAITING_OI_STRUCTURE"}
    if wall["blocked"]: return {"status":"FILTER_FAIL_OI_WALL","oi_wall":wall}
    atm=(option_chain or {}).get("atm_strike")
    if atm is None: return {"status":"WAITING_OPTION_CHAIN"}
    strikes=(option_chain or {}).get("strikes") or {}; row=strikes.get(str(atm)) or {}
    leg=row.get("CE" if breakout["side"]=="LONG" else "PE") or {}
    option_price=leg.get("last_price")
    signal={
        "status":"SIGNAL","instrument":symbol,"direction":breakout["side"],
        "option_type":"CE" if breakout["side"]=="LONG" else "PE","option_strike":atm,
        "option_ltp":option_price,"underlying_ltp":px,"orb":orb,"breakout":breakout,
        "retest":{"confirmed":True,"timestamp":retest.get("timestamp") if retest else None},
        "vwap":vw,"volume_ratio":vol_ratio,"relative_strength_pct_points":rs,
        "oi_wall":wall,"stop_underlying":orb["low"] if breakout["side"]=="LONG" else orb["high"],
        "target_underlying":wall["strike"]
    }
    return signal


def run_once():
    rules=read_json(RULES_FILE,{})
    if not rules.get("enabled",False):
        out={"status":"DISABLED","source":"PSYCHO SIGNAL ENGINE","generated_at":now().isoformat()}; write_atomic(OUTPUT_FILE,out); return out
    n=get_json("/nifty-live"); b=get_json("/banknifty-live")
    no=get_json("/nifty-option-chain"); bo=get_json("/banknifty-option-chain")
    state=read_json(STATE_FILE,{})
    today=now().date().isoformat()
    if state.get("session_date") != today: state={"session_date":today,"signalled":{}}
    results={"NIFTY":evaluate("NIFTY",n,b,no,state,rules),"BANKNIFTY":evaluate("BANKNIFTY",b,n,bo,state,rules)}
    for symbol,result in results.items():
        if result.get("status")=="SIGNAL" and rules["risk"]["one_trade_per_instrument_per_day"] and state["signalled"].get(symbol):
            result={"status":"NO_SIGNAL_ALREADY_TAKEN_TODAY","previous_signal":state["signalled"][symbol]}
            results[symbol]=result
        elif result.get("status")=="SIGNAL":
            state["signalled"][symbol]=result
    write_atomic(STATE_FILE,state)
    out={"status":"LIVE","source":"PSYCHO SIGNAL ENGINE","strategy":rules["name"],"generated_at":now().isoformat(),"results":results}
    write_atomic(OUTPUT_FILE,out); return out


if __name__=="__main__":
    print("PSYCHO SIGNAL ENGINE STARTING",flush=True)
    while True:
        try: print(json.dumps(run_once(),ensure_ascii=False),flush=True)
        except Exception as e:
            out={"status":"ERROR","source":"PSYCHO SIGNAL ENGINE","generated_at":now().isoformat(),"error":f"{type(e).__name__}: {e}"}
            try: write_atomic(OUTPUT_FILE,out)
            except Exception: pass
            print(json.dumps(out),flush=True)
        time.sleep(POLL_SECONDS)
