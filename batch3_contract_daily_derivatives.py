"""PSYCHO Batch 3 — Contract-Level Derivative Daily Context

Acquires daily OHLCV + OI for individual NIFTY/BANKNIFTY index-futures
contracts using Dhan's /charts/historical endpoint. This complements Batch 2A
continuous futures by preserving contract/expiry identity and participation.

The Dhan detailed instrument master is downloaded at runtime; no credentials
are stored in source. Expired-futures intraday is intentionally NOT requested.
"""
import csv, json, os, time, urllib.request
from datetime import date
from pathlib import Path

MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master-detailed.csv"
API = "https://api.dhan.co/v2/charts/historical"
ROOT = Path(os.getenv("PSYCHO_RESEARCH_ROOT", "research_data")) / "batch3_contract_daily_derivatives"
ROOT.mkdir(parents=True, exist_ok=True)
START = os.getenv("BATCH3_FROM", "2025-08-11")
END = os.getenv("BATCH3_TO", "2026-08-09")


def norm(row, *names):
    for n in names:
        if n in row and row[n] not in (None, ""):
            return str(row[n]).strip()
    return ""


def get_master():
    req = urllib.request.Request(MASTER_URL, headers={"User-Agent": "PSYCHO-Batch3/1.0"})
    with urllib.request.urlopen(req, timeout=120) as r:
        text = r.read().decode("utf-8-sig")
    return list(csv.DictReader(text.splitlines()))


def request(payload):
    token = os.environ["DHAN_ACCESS_TOKEN"]
    req = urllib.request.Request(API, data=json.dumps(payload).encode(), method="POST", headers={
        "Accept":"application/json", "Content-Type":"application/json", "access-token":token,
    })
    with urllib.request.urlopen(req, timeout=90) as r:
        obj = json.loads(r.read().decode())
    if isinstance(obj, dict) and str(obj.get("status", "")).lower() == "failure":
        raise RuntimeError(obj.get("remarks") or obj.get("errorMessage") or str(obj))
    return obj


def rows_from(obj):
    ts=obj.get("timestamp") or []
    keys=["open","high","low","close","volume"]
    if any(len(obj.get(k) or []) != len(ts) for k in keys):
        raise ValueError("Dhan column length mismatch")
    oi=obj.get("oi") or []
    out=[]
    for i,t in enumerate(ts):
        r={"timestamp":int(float(t)), **{k:obj[k][i] for k in keys}}
        if oi: r["oi"]=oi[i]
        out.append(r)
    return out


def validate(rows):
    seen=set(); errors=[]; prev=None
    for i,r in enumerate(rows):
        if prev is not None and r["timestamp"] <= prev: errors.append(f"row {i}: timestamp order")
        if r["timestamp"] in seen: errors.append(f"row {i}: duplicate timestamp")
        seen.add(r["timestamp"]); prev=r["timestamp"]
        try:
            o,h,l,c=map(float,(r["open"],r["high"],r["low"],r["close"]))
            if h < max(o,c) or l > min(o,c) or h < l: errors.append(f"row {i}: OHLC")
            if float(r["volume"]) < 0: errors.append(f"row {i}: negative volume")
            if "oi" in r and float(r["oi"]) < 0: errors.append(f"row {i}: negative OI")
        except Exception: errors.append(f"row {i}: numeric field")
    return {"rows":len(rows),"unique_timestamps":len(seen),"valid":not errors,"errors":errors[:50],"first_epoch":rows[0]["timestamp"] if rows else None,"last_epoch":rows[-1]["timestamp"] if rows else None}


def main():
    if not os.environ.get("DHAN_ACCESS_TOKEN"): raise RuntimeError("Missing DHAN_ACCESS_TOKEN")
    master=get_master()
    selected=[]
    for r in master:
        exch=norm(r,"EXCH_ID","SEM_EXM_EXCH_ID").upper()
        seg=norm(r,"SEGMENT","SEM_SEGMENT").upper()
        inst=norm(r,"INSTRUMENT","SEM_INSTRUMENT_NAME").upper()
        underlying=norm(r,"UNDERLYING_SYMBOL","SM_SYMBOL_NAME","SYMBOL_NAME").upper()
        sid=norm(r,"SECURITY_ID","SEM_SMST_SECURITY_ID")
        expiry=norm(r,"SM_EXPIRY_DATE","SEM_EXPIRY_DATE")[:10]
        if exch != "NSE" or seg not in ("D","NSE_FNO"): continue
        if inst not in ("FUTIDX","FUTIDX") or underlying not in ("NIFTY","BANKNIFTY"): continue
        if not sid or not expiry or not (START <= expiry <= END): continue
        selected.append({"symbol":underlying,"security_id":sid,"expiry":expiry,"trading_symbol":norm(r,"SEM_TRADING_SYMBOL","DISPLAY_NAME","SEM_CUSTOM_SYMBOL"),"lot_size":norm(r,"LOT_SIZE","SEM_LOT_UNITS"),"tick_size":norm(r,"TICK_SIZE","SEM_TICK_SIZE")})
    # Deduplicate security IDs while retaining contract identity.
    uniq={x["security_id"]:x for x in selected}; selected=list(uniq.values())
    manifest={"batch":"BATCH_3_CONTRACT_DAILY_DERIVATIVES","from":START,"to":END,"master_rows":len(master),"contracts_selected":len(selected),"status":"RUNNING","datasets":[]}
    (ROOT/"batch3_manifest.json").write_text(json.dumps(manifest,indent=2))
    for c in selected:
        payload={"securityId":c["security_id"],"exchangeSegment":"NSE_FNO","instrument":"FUTIDX","expiryCode":0,"oi":True,"fromDate":START,"toDate":END}
        try:
            data=request(payload); rows=rows_from(data); meta=validate(rows)
            name=f"{c['symbol']}_{c['expiry']}_{c['security_id']}"
            with (ROOT/f"{name}.csv").open("w",newline="") as f:
                fields=["timestamp","open","high","low","close","volume"] + (["oi"] if rows and "oi" in rows[0] else [])
                w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(rows)
            meta.update(c); meta["status"]="VALIDATED" if meta["valid"] else "INVALID"; manifest["datasets"].append(meta)
            if not meta["valid"]: raise RuntimeError(f"validation failed: {name}")
        except Exception as e:
            meta={**c,"status":"FAILED","error":str(e)}; manifest["datasets"].append(meta)
        if len(manifest["datasets"]) % 10 == 0: (ROOT/"batch3_manifest.json").write_text(json.dumps(manifest,indent=2))
        time.sleep(0.15)
    manifest["validated_contracts"]=sum(1 for x in manifest["datasets"] if x.get("status")=="VALIDATED")
    manifest["failed_contracts"]=sum(1 for x in manifest["datasets"] if x.get("status")=="FAILED")
    manifest["status"]="VALIDATED" if manifest["failed_contracts"]==0 else "PARTIAL"
    (ROOT/"batch3_manifest.json").write_text(json.dumps(manifest,indent=2))
    print(json.dumps({k:manifest[k] for k in ("status","contracts_selected","validated_contracts","failed_contracts")},indent=2))

if __name__=="__main__": main()
