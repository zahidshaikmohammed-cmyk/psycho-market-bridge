import json, os, time, urllib.request
from datetime import datetime, time as dt_time
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
BRIDGE_BASE_URL = os.environ.get("BRIDGE_BASE_URL", "http://127.0.0.1:10000").rstrip("/")
POLL_SECONDS = int(os.environ.get("SIGNAL_POLL_SECONDS", "5"))
RULES_FILE = os.environ.get("SIGNAL_RULES_FILE", "strategy_rules.json")
OUTPUT_FILE = os.environ.get("SIGNAL_OUTPUT_FILE", "signal-live.json")


def now():
    return datetime.now(IST)


def get_json(path):
    req = urllib.request.Request(BRIDGE_BASE_URL + path, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def write_atomic(data):
    tmp = OUTPUT_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.flush(); os.fsync(f.fileno())
    os.replace(tmp, OUTPUT_FILE)


def candles(state, tf):
    return (((state or {}).get("timeframes") or {}).get(tf) or [])


def vwap(candles_):
    pv = vol = 0.0
    for c in candles_:
        try:
            h, l, cl, v = float(c["high"]), float(c["low"]), float(c["close"]), float(c.get("volume") or 0)
            if v > 0:
                pv += ((h + l + cl) / 3.0) * v
                vol += v
        except (TypeError, ValueError, KeyError):
            pass
    return pv / vol if vol else None


def opening_range(candles_, start="09:15", end="09:45"):
    s = dt_time.fromisoformat(start); e = dt_time.fromisoformat(end)
    selected=[]
    for c in candles_:
        try:
            t=datetime.fromtimestamp(int(c["timestamp"]), IST).time()
            if s <= t < e: selected.append(c)
        except Exception: pass
    if not selected: return None
    return {"high": max(float(c["high"]) for c in selected), "low": min(float(c["low"]) for c in selected), "candles": len(selected)}


def load_rules():
    with open(RULES_FILE, "r", encoding="utf-8") as f: return json.load(f)


def evaluate(symbol, market, peer_market, rules):
    session = market.get("current_session") or {}
    px = session.get("last_price")
    if px is None: return {"status":"NO_DATA"}
    c5 = candles(market, "5M")
    c1 = candles(market, "1M")
    orb = opening_range(c5, rules["opening_range"]["start"], rules["opening_range"]["end"])
    if not orb: return {"status":"WAITING_ORB"}
    if now().time() < dt_time.fromisoformat(rules["entry_window"]["start"]): return {"status":"WAITING_ENTRY_WINDOW","orb":orb}
    if now().time() > dt_time.fromisoformat(rules["entry_window"]["end"]): return {"status":"NO_SIGNAL_TIME_WINDOW","orb":orb}

    px=float(px); direction = "LONG" if px > orb["high"] else "SHORT" if px < orb["low"] else None
    if not direction: return {"status":"NO_BREAKOUT","orb":orb,"price":px}

    vw = vwap(c1)
    if rules["filters"]["vwap_required"]:
        if vw is None: return {"status":"WAITING_VWAP","orb":orb,"price":px}
        if direction == "LONG" and px <= vw: return {"status":"FILTER_FAIL_VWAP","direction":direction,"vwap":vw}
        if direction == "SHORT" and px >= vw: return {"status":"FILTER_FAIL_VWAP","direction":direction,"vwap":vw}

    peer_px=(peer_market.get("current_session") or {}).get("last_price")
    if rules["filters"]["relative_strength_required"] and peer_px is None:
        return {"status":"WAITING_RELATIVE_STRENGTH"}

    return {"status":"CANDIDATE","instrument":symbol,"direction":direction,"price":px,"orb":orb,"vwap":vw,"peer_price":peer_px}


def run_once():
    rules=load_rules()
    if not rules.get("enabled", False):
        out={"status":"DISABLED","generated_at":now().isoformat(),"reason":"Strategy rulebook is not locked/enabled"}
        write_atomic(out); return out
    n=get_json("/nifty-live"); b=get_json("/banknifty-live")
    results={"NIFTY":evaluate("NIFTY",n,b,rules),"BANKNIFTY":evaluate("BANKNIFTY",b,n,rules)}
    out={"status":"LIVE","source":"PSYCHO SIGNAL ENGINE","generated_at":now().isoformat(),"strategy":rules.get("name"),"results":results}
    write_atomic(out); return out


if __name__ == "__main__":
    print("PSYCHO SIGNAL ENGINE STARTING", flush=True)
    while True:
        try: print(json.dumps(run_once(), ensure_ascii=False), flush=True)
        except Exception as e:
            out={"status":"ERROR","source":"PSYCHO SIGNAL ENGINE","generated_at":now().isoformat(),"error":f"{type(e).__name__}: {e}"}
            try: write_atomic(out)
            except Exception: pass
            print(json.dumps(out), flush=True)
        time.sleep(POLL_SECONDS)
