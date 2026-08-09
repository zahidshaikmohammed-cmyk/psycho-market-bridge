"""PSYCHO Batch 2 — Futures Participation Acquisition

Acquires historical 1-minute NIFTY/BANK NIFTY futures OHLCV + OI from Dhan's
v2 historical intraday endpoint in <=90-day chunks. Security IDs are resolved
from Dhan instrument master using configured futures contracts where possible.
No live bridge/Hunter logic is modified.
"""
import os, io, csv, json, time
from pathlib import Path
from datetime import datetime, timedelta
import requests

ROOT = Path("research_data/batch2_futures")
ROOT.mkdir(parents=True, exist_ok=True)
BASE = "https://api.dhan.co/v2"
FROM = datetime.fromisoformat(os.getenv("BATCH2_FROM", "2025-08-11 09:15:00"))
TO = datetime.fromisoformat(os.getenv("BATCH2_TO", "2026-08-08 15:40:00"))
CHUNK_DAYS = 89
CLIENT_ID = os.environ["DHAN_CLIENT_ID"]
TOKEN = os.environ["DHAN_ACCESS_TOKEN"]
HEADERS = {"access-token": TOKEN, "client-id": CLIENT_ID, "Content-Type": "application/json"}


def request_json(path, payload):
    r = requests.post(BASE + path, headers=HEADERS, json=payload, timeout=45)
    r.raise_for_status()
    return r.json()


def validate(rows):
    required = {"timestamp", "open", "high", "low", "close", "volume", "oi"}
    if not rows:
        return {"ok": False, "reason": "zero rows"}
    missing = required - set(rows[0])
    if missing:
        return {"ok": False, "reason": f"missing fields: {sorted(missing)}"}
    ts = [r["timestamp"] for r in rows]
    parsed = [datetime.fromisoformat(str(x).replace("Z", "+00:00")) if "T" in str(x) else str(x) for x in ts]
    duplicates = len(parsed) - len(set(map(str, parsed)))
    ordered = all(str(parsed[i]) <= str(parsed[i+1]) for i in range(len(parsed)-1))
    bad_ohlc = 0
    for r in rows:
        try:
            o,h,l,c = map(float, (r["open"],r["high"],r["low"],r["close"]))
            if h < max(o,c) or l > min(o,c) or h < l: bad_ohlc += 1
        except Exception: bad_ohlc += 1
    return {"ok": duplicates == 0 and ordered and bad_ohlc == 0, "rows": len(rows), "duplicates": duplicates, "ordered": ordered, "bad_ohlc": bad_ohlc, "first": ts[0], "last": ts[-1], "fields": sorted(rows[0])}


def download(security_id, label, out):
    all_rows=[]
    cur=FROM
    while cur < TO:
        end=min(cur+timedelta(days=CHUNK_DAYS), TO)
        payload={"securityId": str(security_id), "exchangeSegment":"NSE_FNO", "instrument":"FUTIDX", "interval":"1", "fromDate":cur.strftime("%Y-%m-%d"), "toDate":end.strftime("%Y-%m-%d")}
        data=request_json("/charts/intraday", payload)
        if isinstance(data, dict) and "timestamp" in data:
            keys=[k for k in ("timestamp","open","high","low","close","volume","oi") if k in data]
            n=len(data["timestamp"])
            for i in range(n): all_rows.append({k:data[k][i] for k in keys})
        cur=end
        time.sleep(0.2)
    # de-duplicate by timestamp across chunks
    uniq={str(r["timestamp"]):r for r in all_rows}
    rows=[uniq[k] for k in sorted(uniq)]
    result=validate(rows)
    (out).write_text(json.dumps({"instrument":label,"security_id":security_id,"validation":result,"rows":rows},indent=2))
    return result


def main():
    # The exact front-month security IDs vary by expiry. This first acquisition
    # intentionally resolves them from Dhan's instrument master before download.
    master_url="https://images.dhan.co/api-data/api-scrip-master.csv"
    r=requests.get(master_url,timeout=60)
    r.raise_for_status()
    text=r.text
    # Keep the raw master locally for auditable resolution.
    (ROOT/"dhan_scrip_master.csv").write_text(text)
    import pandas as pd
    df=pd.read_csv(io.StringIO(text), low_memory=False)
    # Normalize names seen in Dhan master; filter NSE index futures.
    cols={c.lower():c for c in df.columns}
    seg=cols.get("exchange_segment"); it=cols.get("instrument_type"); sym=cols.get("symbol")
    sid=cols.get("security_id"); exp=cols.get("expiry_date")
    if not all([seg,it,sym,sid,exp]): raise RuntimeError(f"Required master columns not found: {list(df.columns)}")
    x=df[(df[seg].astype(str)=="NSE_FNO") & (df[it].astype(str).str.upper().isin(["FUTIDX","FUTIDX_NSE"]))]
    x=x[x[sym].astype(str).str.upper().isin(["NIFTY","BANKNIFTY"])].copy()
    x[exp]=pd.to_datetime(x[exp],errors="coerce")
    x=x[(x[exp]>=pd.Timestamp(FROM.date())) & (x[exp]<=pd.Timestamp(TO.date()))].sort_values([sym,exp])
    manifest={"status":"STARTED","from":str(FROM),"to":str(TO),"datasets":{}}
    for symbol in ["NIFTY","BANKNIFTY"]:
        sub=x[x[sym].astype(str).str.upper()==symbol]
        # One contract per expiry; download each expiry separately to avoid mixing contracts.
        files=[]
        for _,row in sub.iterrows():
            label=f"{symbol}_{pd.Timestamp(row[exp]).date()}"
            out=ROOT/(label+".json")
            try:
                res=download(str(row[sid]),label,out)
                files.append({"label":label,"security_id":str(row[sid]),"file":out.name,"validation":res})
            except Exception as e:
                files.append({"label":label,"security_id":str(row[sid]),"error":str(e)})
        manifest["datasets"][symbol]=files
    manifest["status"]="COMPLETED"
    (ROOT/"batch2_manifest.json").write_text(json.dumps(manifest,indent=2,default=str))
    print(json.dumps(manifest,indent=2,default=str))

if __name__=="__main__": main()
