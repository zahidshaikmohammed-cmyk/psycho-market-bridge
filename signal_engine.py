import json, os, time, urllib.request
from datetime import datetime, time as dt_time, timedelta
from zoneinfo import ZoneInfo

IST=ZoneInfo('Asia/Kolkata')
BRIDGE_BASE_URL=os.environ.get('BRIDGE_BASE_URL','https://psycho-market-bridge.onrender.com').rstrip('/')
POLL_SECONDS=int(os.environ.get('SIGNAL_POLL_SECONDS','5'))
RULES_FILE=os.environ.get('SIGNAL_RULES_FILE','strategy_rules.json')
OUTPUT_FILE=os.environ.get('SIGNAL_OUTPUT_FILE','signal-live.json')
STATE_FILE=os.environ.get('SIGNAL_STATE_FILE','signal-engine-state.json')

def now(): return datetime.now(IST)
def get_json(path):
    req=urllib.request.Request(BRIDGE_BASE_URL+path,headers={'Accept':'application/json'})
    with urllib.request.urlopen(req,timeout=15) as r: return json.loads(r.read().decode())
def write_atomic(filename,data):
    tmp=filename+'.tmp'
    with open(tmp,'w',encoding='utf-8') as f: json.dump(data,f,indent=2,ensure_ascii=False); f.flush(); os.fsync(f.fileno())
    os.replace(tmp,filename)
def read_json(filename,default):
    try:
        with open(filename,'r',encoding='utf-8') as f: return json.load(f)
    except Exception: return default
def candles(state,tf): return (((state or {}).get('timeframes') or {}).get(tf) or [])
def candle_dt(c):
    try: return datetime.fromtimestamp(int(c['timestamp']),IST)
    except Exception: return None
def closed_5m(cs):
    cutoff=now()-timedelta(minutes=5); return [c for c in cs if candle_dt(c) and candle_dt(c)<=cutoff]
def vwap(cs):
    pv=vol=0.0
    for c in cs:
        try:
            h,l,cl,v=map(float,(c['high'],c['low'],c['close'],c.get('volume') or 0))
            if v>0: pv+=((h+l+cl)/3)*v; vol+=v
        except Exception: pass
    return pv/vol if vol else None
def opening_range(cs,start,end):
    s,e=dt_time.fromisoformat(start),dt_time.fromisoformat(end); x=[]
    for c in cs:
        d=candle_dt(c)
        if d and s<=d.time()<e: x.append(c)
    if not x:return None
    return {'high':max(float(c['high']) for c in x),'low':min(float(c['low']) for c in x),'candles':len(x)}
def session_return(m):
    cur=m.get('current_session') or {}; op=cur.get('open'); px=cur.get('last_price')
    try:return (float(px)-float(op))/float(op)*100 if op else None
    except Exception:return None
def volume_confirm(c5,r):
    closed=closed_5m(c5); n=r['filters']['volume_lookback_bars']
    if len(closed)<n+1:return False,None
    try:
        cur=float(closed[-1].get('volume') or 0); hist=[float(x.get('volume') or 0) for x in closed[-n-1:-1]]; avg=sum(hist)/len(hist)
        return avg>0 and cur>=avg*r['filters']['volume_multiplier'],(cur/avg if avg else None)
    except Exception:return False,None
def breakout(c5,orb,r):
    for c in reversed(closed_5m(c5)):
        d=candle_dt(c)
        if not d or d.time()<dt_time.fromisoformat(r['entry_window']['start']):continue
        op,cl=float(c['open']),float(c['close'])
        if cl>orb['high'] and op<=orb['high']:return {'side':'LONG','timestamp':c['timestamp'],'level':orb['high']}
        if cl<orb['low'] and op>=orb['low']:return {'side':'SHORT','timestamp':c['timestamp'],'level':orb['low']}
    return None
def retest(c5,b,r):
    cs=closed_5m(c5)
    try:i=next(i for i,c in enumerate(cs) if c['timestamp']==b['timestamp'])
    except StopIteration:return False,None
    tol=r['breakout']['retest_tolerance_points']; level=b['level']
    for c in cs[i+1:i+1+r['breakout']['retest_max_bars']]:
        lo,hi,cl=map(float,(c['low'],c['high'],c['close']))
        if b['side']=='LONG' and lo<=level+tol and cl>level:return True,c
        if b['side']=='SHORT' and hi>=level-tol and cl<level:return True,c
    return False,None
def oi_wall(oc,side,px,maxdist):
    out=[]
    for k,row in (oc.get('strikes') or {}).items():
        try:
            strike=float(row.get('strike',k)); leg=row.get('CE' if side=='LONG' else 'PE') or {}; oi=float(leg.get('oi') or 0)
            if oi<=0:continue
            if side=='LONG' and strike>px:out.append((strike,oi))
            if side=='SHORT' and strike<px:out.append((strike,oi))
        except Exception:pass
    if not out:return None
    strike,oi=max(out,key=lambda x:x[1]); return {'strike':strike,'oi':oi,'distance_points':abs(strike-px),'blocked':abs(strike-px)<=maxdist}
def evaluate(symbol,m,peer,oc,r):
    px=(m.get('current_session') or {}).get('last_price')
    if px is None:return {'status':'NO_DATA'}
    t=now().time(); start=dt_time.fromisoformat(r['entry_window']['start']); end=dt_time.fromisoformat(r['entry_window']['end'])
    if t<start:return {'status':'WAITING_ENTRY_WINDOW'}
    if t>end:return {'status':'NO_SIGNAL_TIME_WINDOW'}
    c5=candles(m,'5M'); orb=opening_range(c5,r['opening_range']['start'],r['opening_range']['end'])
    if not orb:return {'status':'WAITING_ORB'}
    px=float(px); b=breakout(c5,orb,r)
    if not b:return {'status':'NO_VALID_BREAKOUT','orb':orb}
    ok,rt=retest(c5,b,r)
    if not ok:return {'status':'WAITING_RETEST','direction':b['side'],'orb':orb}
    vw=vwap(candles(m,'1M'))
    if vw is None:return {'status':'WAITING_VWAP'}
    if (b['side']=='LONG' and px<=vw) or (b['side']=='SHORT' and px>=vw):return {'status':'FILTER_FAIL_VWAP','vwap':vw}
    vok,vr=volume_confirm(c5,r)
    if not vok:return {'status':'FILTER_FAIL_VOLUME','volume_ratio':vr}
    own,pr=session_return(m),session_return(peer)
    if own is None or pr is None:return {'status':'WAITING_RELATIVE_STRENGTH'}
    rs=own-pr; th=r['filters']['relative_strength_min_pct_points']
    if (b['side']=='LONG' and rs<th) or (b['side']=='SHORT' and rs>-th):return {'status':'FILTER_FAIL_RELATIVE_STRENGTH','relative_strength_pct_points':rs}
    wall=oi_wall(oc,b['side'],px,r['filters']['oi_wall_max_distance_points'])
    if not wall:return {'status':'WAITING_OI_STRUCTURE'}
    if wall['blocked']:return {'status':'FILTER_FAIL_OI_WALL','oi_wall':wall}
    atm=oc.get('atm_strike'); row=(oc.get('strikes') or {}).get(str(atm)) or {}; typ='CE' if b['side']=='LONG' else 'PE'; leg=row.get(typ) or {}
    if atm is None or leg.get('last_price') is None:return {'status':'WAITING_OPTION_PREMIUM'}
    return {'status':'SIGNAL','instrument':symbol,'direction':b['side'],'option_type':typ,'option_strike':atm,'option_ltp':leg.get('last_price'),'underlying_ltp':px,'orb':orb,'breakout':b,'retest_confirmed':True,'vwap':vw,'volume_ratio':vr,'relative_strength_pct_points':rs,'oi_wall':wall,'stop_underlying':orb['low'] if b['side']=='LONG' else orb['high'],'target_underlying':wall['strike']}
def run_once():
    r=read_json(RULES_FILE,{})
    if not r.get('enabled',False):out={'status':'DISABLED','source':'PSYCHO SIGNAL ENGINE','generated_at':now().isoformat()};write_atomic(OUTPUT_FILE,out);return out
    n,b=no_bo=get_json('/nifty-live'),None
    b=get_json('/banknifty-live'); no=get_json('/nifty-option-chain'); bo=get_json('/banknifty-option-chain')
    state=read_json(STATE_FILE,{}); today=now().date().isoformat()
    if state.get('session_date')!=today:state={'session_date':today,'signalled':{}}
    results={'NIFTY':evaluate('NIFTY',n,b,no,r),'BANKNIFTY':evaluate('BANKNIFTY',b,n,bo,r)}
    for s,x in list(results.items()):
        if x.get('status')=='SIGNAL' and state['signalled'].get(s):results[s]={'status':'NO_SIGNAL_ALREADY_TAKEN_TODAY','previous_signal':state['signalled'][s]}
        elif x.get('status')=='SIGNAL':state['signalled'][s]=x
    write_atomic(STATE_FILE,state);out={'status':'LIVE','source':'PSYCHO SIGNAL ENGINE','strategy':r['name'],'generated_at':now().isoformat(),'results':results};write_atomic(OUTPUT_FILE,out);return out
def run_loop():
    while True:
        try: run_once()
        except Exception as e: write_atomic(OUTPUT_FILE,{'status':'ERROR','source':'PSYCHO SIGNAL ENGINE','generated_at':now().isoformat(),'error':f'{type(e).__name__}: {e}'})
        time.sleep(POLL_SECONDS)
if __name__=='__main__':run_loop()
