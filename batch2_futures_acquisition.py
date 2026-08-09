"""PSYCHO Batch 2 — Futures Participation Acquisition

Acquires historical 1-minute NIFTY/BANK NIFTY futures OHLCV + OI from Dhan.
Security IDs are resolved from Dhan's scrip master. The script refuses to claim
historical coverage when the master does not expose contracts for the requested
research window.
"""
import os, io, json, time
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
            if h < max(o,c) or l > min(o,c) or h < l:
                bad_ohlc += 1
        except Exception:
            bad_ohlc += 1
    return {"ok": duplicates == 0 and ordered and bad_ohlc == 0, "rows": len(rows), "duplicates": duplicates, "ordered": ordered, "bad_ohlc": bad_ohlc, "first": ts[0], "last": ts[-1], "fields": sorted(rows[0])}


def download(security_id, label, out):
    all_rows=[]
    cur=FROM
    while cur < TO:
        end=min(cur+timedelta(days=CHUNK_DAYS), TO)
        payload={"securityId": str(security_id), "exchangeSegment":"NSE_FNO", "instrument":"FUTIDX", "interval":"1", "oi":True, "fromDate":cur.strftime("%Y-%m-%d"), "toDate":end.strftime("%Y-%m-%d")}
        data=request_json("/charts/intraday", payload)
        if isinstance(data, dict) and "timestamp" in data:
            keys=[k for k in ("timestamp","open","high","low","close","volume","oi") if k in data]
            n=len(data["timestamp"])
            for i in range(n):
                all_rows.append({k:data[k][i] for k in keys})
        cur=end
        time.sleep(0.2)
    uniq={str(r["timestamp"]):r for r in all_rows}
    rows=[uniq[k] for k in sorted(uniq)]
    result=validate(rows)
    out.write_text(json.dumps({"instrument":label,"security_id":security_id,"validation":result,"rows":rows},indent=2))
    return result


def main():
    master_url="https://images.dhan.co/api-data/api-scrip-master.csv"
    r=requests.get(master_url,timeout=60)
    r.raise_for_status()
    text=r.text
    (ROOT/"dhan_scrip_master.csv").write_text(text)
    import pandas as pd
    df=pd.read_csv(io.StringIO(text), low_memory=False)

    needed=["SEM_EXM_EXCH_ID","SEM_SEGMENT","SEM_SMST_SECURITY_ID","SEM_INSTRUMENT_NAME","SEM_EXPIRY_DATE","SEM_TRADING_SYMBOL","SEM_EXCH_INSTRUMENT_TYPE","SM_SYMBOL_NAME"]
    missing=[c for c in needed if c not in df.columns]
    if missing:
        raise RuntimeError(f"Required master columns missing: {missing}; available={list(df.columns)}")

    # Dhan compact master: NSE is EXM_EXCH_ID and derivatives are SEGMENT=D.
    x=df[(df["SEM_EXM_EXCH_ID"].astype(str).str.upper()=="NSE") &
         (df["SEM_SEGMENT"].astype(str).str.upper()=="D") &
         (df["SEM_INSTRUMENT_NAME"].astype(str).str.upper()=="FUTIDX")].copy()
    x["SYMBOL_NORM"]=x["SM_SYMBOL_NAME"].astype(str).str.upper().str.strip()
    x["TRADING_NORM"]=x["SEM_TRADING_SYMBOL"].astype(str).str.upper().str.strip()
    x=x[x["SYMBOL_NORM"].isin(["NIFTY","BANKNIFTY"]) | x["TRADING_NORM"].str.startswith(("NIFTY-","BANKNIFTY-"))]
    x["SEM_EXPIRY_DATE"]=pd.to_datetime(x["SEM_EXPIRY_DATE"],errors="coerce")
    x=x[(x["SEM_EXPIRY_DATE"]>=pd.Timestamp(FROM.date())) & (x["SEM_EXPIRY_DATE"]<=pd.Timestamp(TO.date()))].sort_values(["SYMBOL_NORM","SEM_EXPIRY_DATE"])

    if x.empty:
        fut_all=df[(df["SEM_EXM_EXCH_ID"].astype(str).str.upper()=="NSE") & (df["SEM_SEGMENT"].astype(str).str.upper()=="D") & (df["SEM_INSTRUMENT_NAME"].astype(str).str.upper()=="FUTIDX")]
        raise RuntimeError(f"No NIFTY/BANKNIFTY FUTIDX contracts found in requested window. NSE-D FUTIDX rows={len(fut_all)}; expiry range={fut_all['SEM_EXPIRY_DATE'].min()}..{fut_all['SEM_EXPIRY_DATE'].max()}")

    manifest={"status":"STARTED","from":str(FROM),"to":str(TO),"master_rows":len(df),"matched_contracts":len(x),"datasets":{}}
    for symbol in ["NIFTY","BANKNIFTY"]:
        sub=x[x["SYMBOL_NORM"]==symbol]
        if sub.empty:
            sub=x[x["TRADING_NORM"].str.startswith(symbol+"-")]
        files=[]
        for _,row in sub.iterrows():
            label=f"{symbol}_{pd.Timestamp(row['SEM_EXPIRY_DATE']).date()}"
            out=ROOT/(label+".json")
            try:
                res=download(str(row["SEM_SMST_SECURITY_ID"]),label,out)
                files.append({"label":label,"security_id":str(row["SEM_SMST_SECURITY_ID"]),"file":out.name,"validation":res})
            except Exception as e:
                files.append({"label":label,"security_id":str(row["SEM_SMST_SECURITY_ID"]),"error":str(e)})
        manifest["datasets"][symbol]=files

    failed=[f for files in manifest["datasets"].values() for f in files if "error" in f or not f.get("validation",{}).get("ok",False)]
    manifest["status"]="VALIDATED" if not failed else "COMPLETED_WITH_ERRORS"
    manifest["failed_contracts"]=len(failed)
    (ROOT/"batch2_manifest.json").write_text(json.dumps(manifest,indent=2,default=str))
    print(json.dumps(manifest,indent=2,default=str))
    if failed:
        raise RuntimeError(f"Batch 2 validation failed for {len(failed)} contract(s)")

if __name__=="__main__": main()
