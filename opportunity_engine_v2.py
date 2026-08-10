import json, threading, time, urllib.request
from datetime import datetime, time as dt_time, timedelta
from zoneinfo import ZoneInfo
from flask import Flask, jsonify, render_template_string

app=Flask(__name__); IST=ZoneInfo('Asia/Kolkata'); BRIDGE='https://psycho-market-bridge.onrender.com'
OPEN,START,CLOSE=dt_time(9,15),dt_time(9,30),dt_time(15,40); SCAN=15; QUALIFY=85
LOCK=threading.Lock(); STATE={'scan_time':None,'session':{},'items':[]}; SIGNALS={}

HTML='''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="refresh" content="15"><title>PSYCHO Intraday Opportunity Engine</title><style>body{font-family:Arial;background:#090b0f;color:#f4f6f8;margin:0;padding:18px}.wrap{max-width:1100px;margin:auto}h1{margin:0}.sub{color:#9aa3ad;margin:6px 0 18px}.banner,.card{background:#14181e;border:1px solid #29303a;border-radius:14px;padding:18px;margin-bottom:16px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}@media(max-width:800px){.grid{grid-template-columns:1fr}}.title{font-size:22px;font-weight:800}.status{padding:13px;border-radius:10px;background:#202631;font-weight:800;font-size:18px;margin:12px 0}.green{color:#63e6a7}.yellow{color:#ffd166}.blue{color:#73b7ff}.red{color:#ff7777}.row{display:flex;justify-content:space-between;border-bottom:1px solid #232a33;padding:7px 0}.label{color:#aab3bf}.value{font-weight:700;text-align:right}.section{margin-top:16px;font-weight:800;font-size:12px;color:#8ea0b4;letter-spacing:.08em}.small{font-size:12px;color:#8d96a2;line-height:1.5;margin-top:12px}.hero{font-size:28px;font-weight:900}.footer{color:#707985;font-size:11px;margin-top:14px}</style></head><body><div class="wrap"><h1>PSYCHO INTRADAY OPPORTUNITY ENGINE</h1><div class="sub">1M trigger → 5M/15M/1H context → PDH/PDL liquidity break → acceptance → option quality → LOCKED SIGNAL</div><div class="banner"><div class="hero">{{session.status}}</div><div class="sub">{{session.detail}}</div><div class="small">Last scan: {{scan_time}} • scanner refresh: 15s</div></div><div class="grid">{% for x in items %}<div class="card"><div class="title">{{x.name}}</div><div class="status {{x.cls}}">{{x.status}}</div><div class="row"><span class="label">Signal ID</span><span class="value">{{x.signal_id}}</span></div><div class="row"><span class="label">Score</span><span class="value">{{x.score}}/100</span></div><div class="row"><span class="label">Direction</span><span class="value">{{x.direction}}</span></div><div class="row"><span class="label">Underlying</span><span class="value">{{x.underlying}}</span></div><div class="section">STRUCTURE</div><div class="row"><span class="label">PDH / PDL</span><span class="value">{{x.pdh}} / {{x.pdl}}</span></div><div class="row"><span class="label">VWAP</span><span class="value">{{x.vwap}}</span></div><div class="row"><span class="label">ATR(14)</span><span class="value">{{x.atr}}</span></div><div class="row"><span class="label">Break</span><span class="value">{{x.break_event}}</span></div><div class="row"><span class="label">Acceptance</span><span class="value">{{x.acceptance}}</span></div><div class="row"><span class="label">MTF</span><span class="value">{{x.mtf}}</span></div><div class="section">LOCKED OPTION</div><div class="row"><span class="label">Contract</span><span class="value">{{x.contract}}</span></div><div class="row"><span class="label">Entry</span><span class="value">{{x.entry}}</span></div><div class="row"><span class="label">Stop</span><span class="value">{{x.sl}}</span></div><div class="row"><span class="label">Target</span><span class="value">{{x.tp}}</span></div><div class="row"><span class="label">Delta</span><span class="value">{{x.delta}}</span></div><div class="row"><span class="label">OI / Volume</span><span class="value">{{x.oi}} / {{x.volume}}</span></div><div class="row"><span class="label">Option Quality</span><span class="value">{{x.opt_score}}/100</span></div><div class="small">{{x.reason}}</div><div class="footer">Signal values are frozen after lock. No order is placed. Research/paper-trading system.</div></div>{% endfor %}</div></div></body></html>'''

def now(): return datetime.now(IST)
def session(t):
    if t.weekday()>=5:return {'status':'MARKET CLOSED — WEEKEND','detail':'No scanning.'}
    if t.time()<OPEN:return {'status':'MARKET CLOSED — PRE-OPEN','detail':'Waiting for 09:15 IST.'}
    if t.time()<START:return {'status':'MARKET OPEN — BUILDING CONTEXT','detail':'Signal eligibility begins at 09:30 IST.'}
    if t.time()>CLOSE:return {'status':'MARKET CLOSED — SESSION COMPLETE','detail':'Signal engine stopped at 15:40 IST.'}
    return {'status':'MARKET OPEN — OPPORTUNITY SCANNER','detail':'Only fully confirmed setups may become signals.'}

def get(path):
    r=urllib.request.urlopen(urllib.request.Request(BRIDGE+path,headers={'Accept':'application/json'}),timeout=15); return json.loads(r.read().decode())
def num(v):
    try:return float(v)
    except:return None
def fmt(v,d=2):return '—' if v is None else f'{v:,.{d}f}'

def candles(obj):
    out=[]
    def walk(v):
        if isinstance(v,dict):
            if all(k in v for k in ('open','high','low','close')):
                try:
                    ts=v.get('timestamp') or v.get('time') or v.get('datetime');
                    if ts is not None:
                        d=datetime.fromisoformat(ts.replace('Z','+00:00')).astimezone(IST) if isinstance(ts,str) and not ts.isdigit() else datetime.fromtimestamp(int(ts),IST)
                        out.append({'dt':d,'open':float(v['open']),'high':float(v['high']),'low':float(v['low']),'close':float(v['close']),'volume':num(v.get('volume')) or 0})
                except Exception:pass
            for z in v.values():walk(z)
        elif isinstance(v,list):
            for z in v:walk(z)
    walk(obj); return sorted({x['dt']:x for x in out}.values(),key=lambda x:x['dt'])

def atr(c,n=14):
    if len(c)<n+1:return None
    tr=[];p=None
    for x in c:
        tr.append(x['high']-x['low'] if p is None else max(x['high']-x['low'],abs(x['high']-p),abs(x['low']-p)));p=x['close']
    return sum(tr[-n:])/n

def ema(c,n=20):
    if not c:return None
    e=c[0];a=2/(n+1)
    for x in c[1:]:e=a*x+(1-a)*e
    return e

def vwap(c):
    pv=vv=0
    for x in c:
        pv+=((x['high']+x['low']+x['close'])/3)*x['volume'];vv+=x['volume']
    return pv/vv if vv else None

def completed(c,minutes,t): return [x for x in c if x['dt']+timedelta(minutes=minutes)<=t]

def option_rows(raw):
    strikes=((raw.get('option_chain') or {}).get('strikes') or {});out=[]
    for k,s in strikes.items():
        st=num(s.get('strike',k));
        if st is None:continue
        for side in ('CE','PE'):
            l=s.get(side) or {};p=num(l.get('last_price'))
            if p is None or p<=0:continue
            g=l.get('greeks') or {};d=num(g.get('delta'));bid=num(l.get('top_bid_price'));ask=num(l.get('top_ask_price'))
            out.append({'strike':st,'side':side,'ltp':p,'delta':d,'bid':bid,'ask':ask,'oi':num(l.get('oi')),'volume':num(l.get('volume')),'oi_change':num(l.get('oi_change'))})
    return out

def opt_quality(o,under,a):
    s=0;dist=abs(o['strike']-under)
    s+=25 if a and dist<=.75*a else 18 if a and dist<=1.25*a else 5
    d=abs(o['delta']) if o['delta'] is not None else 0;s+=25 if .40<=d<=.65 else 15 if .30<=d<=.75 else 0
    if o['bid'] is not None and o['ask'] is not None and o['ask']>=o['bid']:
        sp=(o['ask']-o['bid'])/o['ltp'];s+=25 if sp<=.01 else 18 if sp<=.02 else 8 if sp<=.04 else 0
    s+=10 if (o['oi'] or 0)>0 else 0;s+=10 if (o['volume'] or 0)>0 else 0;s+=5 if o['ltp']>=50 else 0
    return min(s,100)

def choose(raw,under,direction,a):
    side='CE' if direction=='LONG' else 'PE';rows=[o for o in option_rows(raw) if o['side']==side]
    ranked=sorted(((opt_quality(o,under,a),o) for o in rows),key=lambda z:z[0],reverse=True)
    return ranked[0] if ranked else (0,None)

def blank(name,status='WAITING'):
    return {'name':name,'signal_id':'—','score':0,'direction':'—','status':status,'cls':'blue','underlying':'—','pdh':'—','pdl':'—','vwap':'—','atr':'—','break_event':'NONE','acceptance':'WAITING','mtf':'NOT ALIGNED','contract':'—','entry':'—','sl':'—','tp':'—','delta':'—','oi':'—','volume':'—','opt_score':0,'reason':'No locked signal.'}

def build(name,path,t):
    raw=get(path);m=raw.get('market') or raw;allc=candles(m);today=[x for x in allc if x['dt'].date()==t.date()];prior=[x for x in allc if x['dt'].date()<t.date()];dates=sorted({x['dt'].date() for x in prior});prev=[x for x in prior if dates and x['dt'].date()==dates[-1]]
    pdh=max((x['high'] for x in prev),default=None);pdl=min((x['low'] for x in prev),default=None);under=today[-1]['close'] if today else None;a=atr(allc);vw=vwap(today)
    tf=m.get('timeframes') or {};c1=completed(candles(tf.get('1M') or []),1,t);c5=completed(candles(tf.get('5M') or []),5,t);c15=completed(candles(tf.get('15M') or []),15,t);c1h=completed(candles(tf.get('1H') or []),60,t)
    if not under or not pdh or not pdl or not a or len(c1)<3:return blank(name,'WAITING — DATA/CONTEXT')
    # Use only completed 1M candles. Break must be followed by a separate completed acceptance candle.
    br=c1[-2];ac=c1[-1];direction=None;event='NONE';accepted=False
    if br['close']>pdh and br['close']-pdh>=.15*a and ac['close']>pdh and ac['low']>pdh:direction='LONG';event='PDH LIQUIDITY BREAK';accepted=True
    elif br['close']<pdl and pdl-br['close']>=.15*a and ac['close']<pdl and ac['high']<pdl:direction='SHORT';event='PDL LIQUIDITY BREAK';accepted=True
    if not direction:return {**blank(name,'WATCH — NO CONFIRMED BREAK'),'underlying':fmt(under),'pdh':fmt(pdh),'pdl':fmt(pdl),'vwap':fmt(vw),'atr':fmt(a),'break_event':'NONE'}
    score=30
    if accepted:score+=25
    if direction=='LONG' and vw and under>vw:score+=15
    if direction=='SHORT' and vw and under<vw:score+=15
    def aligned(c):
        return bool(c and ((direction=='LONG' and under>ema([x['close'] for x in c])) or (direction=='SHORT' and under<ema([x['close'] for x in c]))))
    votes=sum(aligned(c) for c in (c5,c15,c1h));mtf='ALIGNED' if votes==3 else 'PARTIAL' if votes>=2 else 'NOT ALIGNED';score+=20 if votes==3 else 10 if votes==2 else 0
    regime='BULLISH' if direction=='LONG' else 'BEARISH'
    oq,opt=choose(raw,under,direction,a)
    if opt:score+=20 if oq>=75 else 10 if oq>=60 else 0
    score=min(score,100)
    if not accepted or votes<2 or oq<75 or score<QUALIFY:return {**blank(name,'WATCH — CONFIRMATION REQUIRED'),'score':score,'direction':direction,'underlying':fmt(under),'pdh':fmt(pdh),'pdl':fmt(pdl),'vwap':fmt(vw),'atr':fmt(a),'break_event':event,'acceptance':'CONFIRMED','mtf':mtf,'opt_score':oq,'reason':'Confluence not strong enough to lock.'}
    entry=opt['ask'] if opt['ask'] and opt['ask']>0 else opt['ltp'];d=abs(opt['delta']) if opt['delta'] else .50;invalid=pdl if direction=='LONG' else pdh;urisk=max(abs(under-invalid),.15*a);prisk=max(urisk*d,entry*.10);sl=round(max(.05,entry-prisk),2);tp=round(entry+2*(entry-sl),2);sid=f"IOE-{name.replace(' ','')}-{t:%Y%m%d}-{ac['dt']:%H%M}"
    return {'name':name,'signal_id':sid,'score':score,'direction':direction,'status':'🟢 SIGNAL LOCKED','cls':'green','underlying':fmt(under),'pdh':fmt(pdh),'pdl':fmt(pdl),'vwap':fmt(vw),'atr':fmt(a),'break_event':event,'acceptance':'CONFIRMED','mtf':mtf,'contract':f"{int(opt['strike']):,} {opt['side']}",'entry':fmt(entry),'sl':fmt(sl),'tp':fmt(tp),'delta':fmt(opt['delta']),'oi':fmt(opt['oi'],0),'volume':fmt(opt['volume'],0),'opt_score':oq,'reason':'Locked after completed 1M break + acceptance + MTF + option-quality confluence.'}

def scan():
    t=now();ss=session(t)
    with LOCK:
        if t.weekday()>=5 or t.time()<OPEN or t.time()>CLOSE:
            STATE.update(scan_time=t.strftime('%d %b %Y %H:%M:%S IST'),session=ss,items=[blank('NIFTY'),blank('BANK NIFTY')]);return
        for name,path in [('NIFTY','/nifty-live'),('BANK NIFTY','/banknifty-live')]:
            st=SIGNALS.get(name)
            if st and st.get('date')==t.date():continue
            try:
                candidate=build(name,path,t)
                if candidate['status']=='🟢 SIGNAL LOCKED':SIGNALS[name]={'date':t.date(),'data':candidate}
                else: SIGNALS.setdefault(name,{'date':t.date(),'data':candidate})['data']=candidate
            except Exception as e: SIGNALS[name]={'date':t.date(),'data':{**blank(name,'BRIDGE UNAVAILABLE'),'reason':str(e)}}
        items=[]
        for name in ('NIFTY','BANK NIFTY'):items.append(SIGNALS.get(name,{'data':blank(name)})['data'])
        STATE.update(scan_time=t.strftime('%d %b %Y %H:%M:%S IST'),session=ss,items=items)

def worker():
    while True:
        try:scan()
        except Exception as e:print('ENGINE ERROR',e,flush=True)
        time.sleep(SCAN)
threading.Thread(target=worker,daemon=True).start()

@app.route('/')
def home():
    with LOCK:return render_template_string(HTML,**json.loads(json.dumps(STATE)))
@app.route('/health')
def health():return jsonify({'status':'ok','service':'psycho-opportunity-engine-v2','last_scan':STATE['scan_time'],'session':session(now()),'locked':{k:bool(v.get('data',{}).get('status')=='🟢 SIGNAL LOCKED') for k,v in SIGNALS.items()}})
@app.route('/api/state')
def api_state():
    with LOCK:return jsonify({'state':STATE,'signals':SIGNALS})
if __name__=='__main__':app.run(host='0.0.0.0',port=10000)
