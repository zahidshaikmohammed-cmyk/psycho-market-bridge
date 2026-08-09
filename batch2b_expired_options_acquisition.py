"""PSYCHO Batch 2B — Expired Options Microstructure Acquisition

Research-only acquisition using Dhan /charts/rollingoption.
"""
import os, json, gzip, time
from pathlib import Path
from datetime import date, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

ROOT = Path("research_data/batch2b_expired_options")
ROOT.mkdir(parents=True, exist_ok=True)
BASE = "https://api.dhan.co/v2/charts/rollingoption"
FROM = date.fromisoformat(os.getenv("BATCH2B_FROM", "2025-08-11"))
TO = date.fromisoformat(os.getenv("BATCH2B_TO", "2026-08-09"))
MODE = os.getenv("BATCH2B_MODE", "PILOT").upper()
CLIENT_ID = os.environ["DHAN_CLIENT_ID"]
TOKEN = os.environ["DHAN_ACCESS_TOKEN"]
HEADERS = {"access-token": TOKEN, "client-id": CLIENT_ID, "Content-Type": "application/json", "Accept": "application/json"}
CONFIGS=[]
for symbol,sid in (("NIFTY","13"),("BANKNIFTY","25")):
    CONFIGS.append((symbol,sid,"WEEK",1,list(range(-10,11))))
    for expiry_code in (1,2,3): CONFIGS.append((symbol,sid,"MONTH",expiry_code,list(range(-3,4))))

def strike_name(offset):
    return "ATM" if offset==0 else (f"ATM+{offset}" if offset>0 else f"ATM{offset}")

def request_once(payload):
    for attempt in range(4):
        try:
            r=requests.post(BASE,headers=HEADERS,json=payload,timeout=90)
            if r.status_code==429: time.sleep(2*(attempt+1)); continue
            r.raise_for_status(); obj=r.json()
            if isinstance(obj,dict) and str(obj.get("status","")).lower()=="failure":
                raise RuntimeError(obj.get("remarks") or obj.get("errorMessage") or str(obj))
            return obj
        except Exception:
            if attempt==3: raise
            time.sleep(1.5*(attempt+1))

def normalize_volume(side):
    """Preserve raw volume and recover unsigned-32-bit wraparound when evident.
    Values that are negative are not silently accepted: each correction is recorded.
    """
    if not side or "volume" not in side: return side, []
    raw=side["volume"]; normalized=[]; corrections=[]
    for i,v in enumerate(raw):
        try: x=int(float(v))
        except Exception: normalized.append(v); continue
        if x < 0:
            recovered=x + 2**32
            if recovered >= 0:
                normalized.append(recovered)
                corrections.append({"index":i,"raw":x,"normalized":recovered,"method":"uint32_wraparound"})
            else: normalized.append(x)
        else: normalized.append(x)
    out=dict(side); out["volume_raw"]=raw; out["volume"]=normalized
    return out,corrections

def validate_side(side):
    if not side: return {"present":False,"ok":True,"rows":0}
    side,corrections=normalize_volume(side)
    required={"timestamp","open","high","low","close","volume"}
    missing=sorted(required-set(side))
    if missing: return {"present":True,"ok":False,"reason":f"missing fields: {missing}","volume_corrections":corrections}
    n=len(side["timestamp"]); lengths={k:len(v) for k,v in side.items() if isinstance(v,list)}
    bad_lengths={k:v for k,v in lengths.items() if v!=n}
    if bad_lengths: return {"present":True,"ok":False,"reason":"array length mismatch","lengths":lengths,"volume_corrections":corrections}
    ts=[int(float(x)) for x in side["timestamp"]]; duplicates=n-len(set(ts)); ordered=all(ts[i]<ts[i+1] for i in range(n-1))
    bad_ohlc=0; bad_volume=0
    for i in range(n):
        try:
            o,h,l,c=map(float,(side["open"][i],side["high"][i],side["low"][i],side["close"][i]))
            if h<max(o,c) or l>min(o,c) or h<l: bad_ohlc+=1
            if float(side["volume"][i])<0: bad_volume+=1
        except Exception: bad_ohlc+=1
    return {"present":True,"ok":duplicates==0 and ordered and bad_ohlc==0 and bad_volume==0,
            "rows":n,"duplicates":duplicates,"ordered":ordered,"bad_ohlc":bad_ohlc,"bad_volume":bad_volume,
            "volume_corrections":corrections,"fields":sorted(side.keys()),"first_epoch":ts[0] if n else None,"last_epoch":ts[-1] if n else None}, side

def validate_response(obj):
    data=obj.get("data",obj) if isinstance(obj,dict) else obj
    ce=data.get("ce") if isinstance(data,dict) else None; pe=data.get("pe") if isinstance(data,dict) else None
    vc=validate_side(ce); vp=validate_side(pe)
    if ce: data["ce"]=vc[1]; vc=vc[0]
    if pe: data["pe"]=vp[1]; vp=vp[0]
    return {"ok":vc.get("ok",False) and vp.get("ok",False),"rows":vc.get("rows",0)+vp.get("rows",0),"ce":vc,"pe":vp},data

def chunks(start,end,days=30):
    cur=start
    while cur<end:
        nxt=min(cur+timedelta(days=days),end); yield cur,nxt; cur=nxt

def key_for(symbol,sid,flag,expiry_code,strike,option_type,start,end): return f"{symbol}|{sid}|{flag}|{expiry_code}|{strike}|{option_type}|{start}|{end}"
def out_path(symbol,flag,expiry_code,strike,option_type,start,end): return ROOT/f"{symbol}_{flag}_{expiry_code}_{strike}_{option_type}_{start}_{end}.json.gz"
def load_manifest():
    p=ROOT/"batch2b_manifest.json"; return json.loads(p.read_text()) if p.exists() else {"version":1,"status":"STARTED","requests":{}}
def save_manifest(m): (ROOT/"batch2b_manifest.json").write_text(json.dumps(m,indent=2,sort_keys=True))

def task(spec):
    symbol,sid,flag,expiry_code,offset,option_type,start,end=spec; strike=strike_name(offset); key=key_for(symbol,sid,flag,expiry_code,strike,option_type,start,end); path=out_path(symbol,flag,expiry_code,strike,option_type,start,end)
    if path.exists(): return key,{"status":"EXISTS","file":path.name}
    payload={"exchangeSegment":"NSE_FNO","interval":"1","securityId":sid,"instrument":"OPTIDX","expiryFlag":flag,"expiryCode":expiry_code,"strike":strike,"drvOptionType":option_type,"requiredData":["open","high","low","close","iv","volume","strike","oi","spot"],"fromDate":start.isoformat(),"toDate":end.isoformat()}
    obj=request_once(payload); validation,data=validate_response(obj)
    with gzip.open(path,"wt",encoding="utf-8") as f: json.dump({"request":payload,"validation":validation,"data":data},f,separators=(",",":"))
    if not validation["ok"]: raise RuntimeError(f"validation failed for {key}: {validation}")
    return key,{"status":"VALIDATED","file":path.name,"validation":validation}

def build_specs():
    all_chunks=list(chunks(FROM,TO,30))
    if MODE=="PILOT":
        first=all_chunks[0]
        return [(symbol,sid,"WEEK",1,0,opt,first[0],first[1]) for symbol,sid in (("NIFTY","13"),("BANKNIFTY","25")) for opt in ("CALL","PUT")]
    return [(symbol,sid,flag,expiry_code,offset,opt,start,end) for symbol,sid,flag,expiry_code,offsets in CONFIGS for offset in offsets for opt in ("CALL","PUT") for start,end in all_chunks]

def main():
    if MODE not in ("PILOT","FULL"): raise RuntimeError("BATCH2B_MODE must be PILOT or FULL")
    manifest=load_manifest(); specs=[]
    for s in build_specs():
        symbol,sid,flag,expiry_code,offset,opt,start,end=s; key=key_for(symbol,sid,flag,expiry_code,strike_name(offset),opt,start,end)
        if manifest["requests"].get(key,{}).get("status")=="VALIDATED": continue
        specs.append(s)
    manifest.update({"status":"RUNNING","mode":MODE,"planned_requests":len(specs)}); save_manifest(manifest); failures=[]
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures={pool.submit(task,s):s for s in specs}
        for fut in as_completed(futures):
            s=futures[fut]
            try: key,result=fut.result(); manifest["requests"][key]=result
            except Exception as exc:
                symbol,sid,flag,expiry_code,offset,opt,start,end=s; key=key_for(symbol,sid,flag,expiry_code,strike_name(offset),opt,start,end); manifest["requests"][key]={"status":"FAILED","error":str(exc)}; failures.append(key)
            if len(manifest["requests"])%10==0: save_manifest(manifest)
    manifest["failed_requests"]=failures; manifest["validated_requests"]=sum(1 for x in manifest["requests"].values() if x.get("status")=="VALIDATED"); manifest["status"]="VALIDATED" if not failures else "FAILED"; save_manifest(manifest)
    if failures: raise RuntimeError(f"Batch 2B failed requests: {len(failures)}; see batch2b_manifest.json")
    print(json.dumps({"status":manifest["status"],"mode":MODE,"validated_requests":manifest["validated_requests"]},indent=2))
if __name__=="__main__": main()
