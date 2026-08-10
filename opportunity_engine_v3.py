import json, threading, time, urllib.request
from datetime import datetime, time as T, timedelta
from zoneinfo import ZoneInfo
from flask import Flask, jsonify, render_template_string

app=Flask(__name__)
IST=ZoneInfo('Asia/Kolkata')
BR='https://psycho-market-bridge.onrender.com'
OPEN,START,CLOSE=T(9,15),T(9,20),T(15,40)
SCAN=15
MIN_SCORE=78
LOCK=threading.RLock()
BOOK={}
STATE={'scan_time':None,'session':{},'items':[],'trades':[]}

HTML='''<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="refresh" content="15"><style>body{font-family:Arial;background:#080b12;color:#eef2f7;padding:18px}.wrap{max-width:1250px;margin:auto}.banner,.card,.trades{background:#111722;border:1px solid #293241;border-radius:16px;padding:16px;margin:12px 0}.grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}@media(max-width:850px){.grid{grid-template-columns:1fr}}.row{display:flex;justify-content:space-between;padding:7px 0;border-bottom:1px solid #242c38}.good{color:#54e39a}.bad{color:#ff6f7d}.warn{color:#ffd166}.blue{color:#6db8ff}.muted{color:#8995a5;font-size:12px}.reason{background:#0b1018;padding:10px;border-radius:10px;margin-top:10px;line-height:1.5;font-size:13px}table{width:100%;border-collapse:collapse;font-size:12px}td,th{padding:8px;border-bottom:1px solid #29313d;text-align:left}</style><div class="wrap"><div class="banner"><h1>PSYCHO INTRADAY OPPORTUNITY ENGINE</h1><h3>{{session.status}}</h3><div class="muted">{{session.detail}} • {{scan_time or 'waiting'}} • 15s scan</div></div><div class="grid">{%for x in items%}<div class="card"><h2>{{x.name}}</h2><h3 class="{{x.cls}}">{{x.status}}</h3><div class="row"><span>State</span><b>{{x.state}}</b></div><div class="row"><span>Direction</span><b>{{x.direction}}</b></div><div class="row"><span>Setup</span><b>{{x.setup}}</b></div><div class="row"><span>Signal</span><b>{{x.signal_id}}</b></div><div class="row"><span>Contract</span><b>{{x.contract}}</b></div><div class="row"><span>Entry</span><b>{{x.entry}}</b></div><div class="row"><span>SL</span><b>{{x.sl}}</b></div><div class="row"><span>TP</span><b>{{x.tp}}</b></div><div class="row"><span>Live premium</span><b>{{x.live}}</b></div><div class="row"><span>Score</span><b>{{x.score}}/100</b></div><div class="reason"><b>WHY</b><br>{{x.reason}}</div></div>{%endfor%}</div><div class="trades"><h2>TODAY'S CLOSED TRADES</h2>{%if trades%}<table><tr><th>Exit</th><th>Instrument</th><th>Signal</th><th>Setup</th><th>Contract</th><th>Entry</th><th>Exit</th><th>Result</th></tr>{%for t in trades%}<tr><td>{{t.exit_time}}</td><td>{{t.name}}</td><td>{{t.signal_id}}</td><td>{{t.setup}}</td><td>{{t.contract}}</td><td>{{t.entry}}</td><td>{{t.exit}}</td><td class="{{'good' if t.result=='TP TAKEN' else 'bad'}}">{{t.result}}</td></tr>{%endfor%}</table>{%else%}<div class="muted">No completed trades today.</div>{%endif%}</div></div>'''

def now(): return datetime.now(IST)
def num(v):
    try:return float(v)
    except:return None
def fmt(v):
    if v is None:return '—'
    try:return f'{float(v):,.2f}'
    except:return str(v)
def dt(v):
    try:
        s=str(v)
        if s.isdigit():
            n=int(s);return datetime.fromtimestamp(n/1000 if n>10**11 else n,IST)
        return datetime.fromisoformat(s.replace('Z','+00:00')).astimezone(IST)
    except:return None

def get(path):
    r=urllib.request.Request(BR+path,headers={'Accept':'application/json','User-Agent':'PSYCHO-IOE/4.0'})
    with urllib.request.urlopen(r,timeout=12) as x:return json.loads(x.read().decode())

def candles(obj):
    out=[]
    def walk(v):
        if isinstance(v,dict):
            if all(k in v for k in ('open','high','low','close')):
                d=dt(v.get('timestamp') or v.get('time') or v.get('datetime') or v.get('date'))
                if d:
                    try:out.append({'dt':d,'open':float(v['open']),'high':float(v['high']),'low':float(v['low']),'close':float(v['close']),'volume':num(v.get('volume')) or 0})
                    except:pass
            for z in v.values():walk(z)
        elif isinstance(v,list):
            for z in v:walk(z)
    walk(obj);return sorted({x['dt']:x for x in out}.values(),key=lambda x:x['dt'])

def tf(obj,k):
    t=(obj.get('timeframes') or obj.get('candles') or {}) if isinstance(obj,dict) else {}
    return candles(t.get(k,[]))
def atr(c,n=14):
    if len(c)<n+1:return None
    a=[];p=None
    for x in c:
        a.append(x['high']-x['low'] if p is None else max(x['high']-x['low'],abs(x['high']-p),abs(x['low']-p)));p=x['close']
    return sum(a[-n:])/n
def ema(c,n=20):
    if not c:return None
    e=c[0];a=2/(n+1)
    for x in c[1:]:e=a*x+(1-a)*e
    return e
def vwap(c):
    pv=vol=0
    for x in c:
        q=x['volume'];pv+=((x['high']+x['low']+x['close'])/3)*q;vol+=q
    return pv/vol if vol else None
def rng(x):return max(x['high']-x['low'],0.00001)
def body(x):return abs(x['close']-x['open'])
def bull(x):return x['close']>x['open']
def bear(x):return x['close']<x['open']

def option_rows(raw):
    if not isinstance(raw,dict):return []
    n=raw.get('option_chain') or raw.get('data') or raw
    s=n.get('strikes') if isinstance(n,dict) else None
    pairs=list(s.items()) if isinstance(s,dict) else [(x.get('strike'),x) for x in s] if isinstance(s,list) else []
    out=[]
    for k,v in pairs:
        if not isinstance(v,dict):continue
        st=num(v.get('strike',k))
        for side in ('CE','PE'):
            q=v.get(side) or v.get(side.lower()) or {}
            l=num(q.get('last_price') or q.get('ltp') or q.get('lastPrice'))
            if st is None or l is None or l<=0:continue
            g=q.get('greeks') or {}
            out.append({'strike':st,'side':side,'ltp':l,'delta':num(g.get('delta') or q.get('delta')),'bid':num(q.get('top_bid_price') or q.get('bid')),'ask':num(q.get('top_ask_price') or q.get('ask')),'oi':num(q.get('oi') or q.get('open_interest')),'volume':num(q.get('volume'))})
    return out

def bundle(name):
    slug='nifty' if name=='NIFTY' else 'banknifty'
    m=get('/'+slug+'-live')
    o=get('/'+slug+'-option-chain')
    return m,o

def pick(o,u,d):
    rows=[x for x in option_rows(o) if x['side']==('CE' if d=='LONG' else 'PE')]
    rank=[]
    for x in rows:
        s=0;de=abs(x['delta'] or 0);dist=abs(x['strike']-u)
        s+=25 if dist<=max(50,u*.0025) else 12 if dist<=max(100,u*.005) else 0
        s+=25 if .40<=de<=.65 else 15 if .30<=de<=.75 else 0
        if x['bid'] is not None and x['ask'] is not None and x['ask']>=x['bid']:
            sp=(x['ask']-x['bid'])/max(x['ltp'],.01);s+=25 if sp<=.01 else 18 if sp<=.02 else 5 if sp<=.04 else 0
        s+=15 if (x['oi'] or 0)>0 else 0;s+=10 if (x['volume'] or 0)>0 else 0
        rank.append((min(100,s),x))
    return max(rank,key=lambda z:z[0]) if rank else (0,None)

def prev(c,t):
    p=[x for x in c if x['dt'].date()<t.date()]
    if not p:return None
    d=max(x['dt'].date() for x in p);z=[x for x in p if x['dt'].date()==d]
    return {'high':max(x['high'] for x in z),'low':min(x['low'] for x in z),'close':z[-1]['close']}
def opening(c,t):
    z=[x for x in c if x['dt'].date()==t.date() and T(9,15)<=x['dt'].time()<T(9,30)]
    return (max((x['high'] for x in z),default=None),min((x['low'] for x in z),default=None))

def detect(name,raw,t):
    c=tf(raw,'1M') or candles(raw);c=[x for x in c if x['dt']+timedelta(minutes=1)<=t]
    c5=tf(raw,'5M');c15=tf(raw,'15M');c1h=tf(raw,'1H')
    if len(c)<25:return None
    u=c[-1]['close'];a=atr(c);pd=prev(c,t);orh,orl=opening(c,t);vw=vwap([x for x in c if x['dt'].date()==t.date()]);e5=ema([x['close'] for x in c5],20);e15=ema([x['close'] for x in c15],20);e1h=ema([x['close'] for x in c1h],20)
    x1,x2,x3=c[-1],c[-2],c[-3];cs=[]
    if pd:
        if x1['low']<pd['low'] and x1['close']>pd['low']:cs.append((82,'LONG','LIQUIDITY SWEEP + RECLAIM',['swept previous-day low','reclaimed the level','rejection close']))
        if x1['high']>pd['high'] and x1['close']<pd['high']:cs.append((82,'SHORT','LIQUIDITY SWEEP + RECLAIM',['swept previous-day high','reclaimed the level','rejection close']))
        if x2['high']>pd['high'] and x2['close']<pd['high'] and x1['close']<x2['low']:cs.append((84,'SHORT','FAILED BREAKOUT',['PDH breakout failed','returned inside structure','confirmation candle']))
        if x2['low']<pd['low'] and x2['close']>pd['low'] and x1['close']>x2['high']:cs.append((84,'LONG','FAILED BREAKDOWN',['PDL breakdown failed','returned inside structure','confirmation candle']))
    if orh and orl:
        if x1['close']>orh and x2['close']>orh:cs.append((80,'LONG','OPENING RANGE CONTINUATION',['accepted above opening-range high','two-candle acceptance']))
        if x1['close']<orl and x2['close']<orl:cs.append((80,'SHORT','OPENING RANGE CONTINUATION',['accepted below opening-range low','two-candle acceptance']))
    if e5 and e15:
        if u>e5 and u>e15 and x2['low']<=e5*1.001 and bull(x1) and x1['close']>x2['high']:cs.append((83,'LONG','PULLBACK CONTINUATION',['5M/15M trend aligned','pullback into mean','bullish rejection/continuation']))
        if u<e5 and u<e15 and x2['high']>=e5*.999 and bear(x1) and x1['close']<x2['low']:cs.append((83,'SHORT','PULLBACK CONTINUATION',['5M/15M trend aligned','pullback into mean','bearish rejection/continuation']))
    r=c[-8:];av=sum(rng(z) for z in r[:-2])/6
    if rng(x3)<av*.75 and rng(x2)<av*.75 and rng(x1)>av*1.25:
        d='LONG' if bull(x1) else 'SHORT' if bear(x1) else None
        if d:cs.append((82,d,'VOLATILITY CONTRACTION → EXPANSION',['range compression','expansion candle','directional release']))
    if len(c)>=6:
        if all(bull(z) for z in c[-3:]) and u>max(z['high'] for z in c[-6:-3]):cs.append((80,'LONG','TREND CONTINUATION',['bullish candle sequence','fresh continuation high']))
        if all(bear(z) for z in c[-3:]) and u<min(z['low'] for z in c[-6:-3]):cs.append((80,'SHORT','TREND CONTINUATION',['bearish candle sequence','fresh continuation low']))
    avv=sum(z['volume'] for z in c[-21:-1])/20 if len(c)>=21 else 0
    if avv and x1['volume']>=avv*1.5 and body(x1)>=.45*a and body(x1)/rng(x1)>=.6:
        d='LONG' if bull(x1) else 'SHORT' if bear(x1) else None
        if d:cs.append((79,d,'PRICE-FLOW EXPANSION PROXY',['relative volume expansion','wide body','strong close location']))
    if not cs:return None
    cs.sort(key=lambda z:z[0],reverse=True);base,d,setup,why=cs[0]
    mtf=sum(v is not None and ((u>v) if d=='LONG' else (u<v)) for v in (e5,e15,e1h));vwok=vw is not None and ((u>vw) if d=='LONG' else (u<vw));score=min(100,base+mtf*4+(4 if vwok else 0))
    why+= [f'MTF alignment {mtf}/3','VWAP aligned' if vwok else 'VWAP not aligned']
    return {'direction':d,'setup':setup,'score':score,'why':why,'u':u,'atr':a}

def blank(n,status='SCANNING',reason='No qualified setup.'):
    return {'name':n,'signal_id':'—','state':'SCANNING','status':status,'cls':'blue','direction':'—','setup':'NONE','contract':'—','entry':'—','sl':'—','tp':'—','live':'—','score':0,'reason':reason}

def signal(n,d,o,q,t,det):
    e=o['ask'] if o.get('ask') and o['ask']>0 else o['ltp'];risk=max(e*.10,det['atr']*max(abs(o.get('delta') or .5),.35)*.20);sl=round(max(.05,e-risk),2);tp=round(e+2*(e-sl),2);sid=f'IOE-{n.replace(" ","")}-{t:%Y%m%d-%H%M%S}'
    return {'name':n,'signal_id':sid,'state':'ACTIVE','status':'🟢 SIGNAL ACTIVE — LOCKED','cls':'good','direction':d,'setup':det['setup'],'contract':f"{int(o['strike']):,} {o['side']}",'strike':o['strike'],'side':o['side'],'entry':fmt(e),'entry_raw':e,'sl':fmt(sl),'sl_raw':sl,'tp':fmt(tp),'tp_raw':tp,'live':fmt(o['ltp']),'score':det['score'],'reason':' • '.join(det['why']+[f'option quality {q}/100','option locked; no reselection']),'created':t.isoformat()}

def monitor(n,t):
    s=BOOK[n]['active'];slug='nifty' if n=='NIFTY' else 'banknifty'
    try:o=get('/'+slug+'-option-chain')
    except:return s
    rows=[x for x in option_rows(o) if x['side']==s['side'] and abs(x['strike']-s['strike'])<.01]
    if not rows:return s
    p=rows[0]['ltp'];s['live']=fmt(p);res='TP TAKEN' if p>=s['tp_raw'] else 'SL TRIGGERED' if p<=s['sl_raw'] else None
    if not res:return s
    s['state']='CLOSED';s['status']=('🟢 ' if res=='TP TAKEN' else '🔴 ')+res;s['cls']='good' if res=='TP TAKEN' else 'bad'
    BOOK[n]['trades'].append({'exit_time':t.strftime('%H:%M:%S IST'),'name':n,'signal_id':s['signal_id'],'setup':s['setup'],'contract':s['contract'],'direction':s['direction'],'entry':fmt(s['entry_raw']),'exit':fmt(p),'result':res})
    BOOK[n]['last']=dict(s);BOOK[n]['active']=None;BOOK[n]['mode']='PULLBACK_ONLY';return s

def scan():
    t=now();
    if t.weekday()>=5 or t.time()<OPEN or t.time()>CLOSE:sess={'status':'MARKET CLOSED','detail':'No live hunting outside 09:15–15:40 IST.'}
    elif t.time()<START:sess={'status':'MARKET OPEN — BUILDING CONTEXT','detail':'Building opening-range and liquidity context.'}
    else:sess={'status':'MARKET OPEN — PRECISION HUNTING','detail':'Independent setup hunters ranked by confluence. One active trade per instrument.'}
    with LOCK:
        for n in ('NIFTY','BANK NIFTY'):
            if BOOK.get(n,{}).get('date')!=t.date():BOOK[n]={'date':t.date(),'active':None,'last':None,'mode':'INITIAL','trades':[]}
        items=[]
        for n in ('NIFTY','BANK NIFTY'):
            b=BOOK[n]
            try:
                if t.time()<START:item=blank(n,'BUILDING CONTEXT','Waiting for the first opening-range structure.')
                elif t.time()>CLOSE:item=blank(n,'MARKET CLOSED','Session ended; closed trades remain in today\'s ledger.')
                else:
                    raw,opt=bundle(n)
                    if b['active']:
                        item=monitor(n,t)
                        if not b['active']:item=blank(n,'🟡 PULLBACK HUNTING','Previous trade closed. New entries are pullback-only.')
                    else:
                        det=detect(n,raw,t)
                        if b['mode']=='PULLBACK_ONLY' and (not det or det['setup'] not in ('PULLBACK CONTINUATION','LIQUIDITY SWEEP + RECLAIM','FAILED BREAKOUT','FAILED BREAKDOWN')):item=blank(n,'🟡 PULLBACK HUNTING','Previous trade closed. Waiting for a pullback/rejection structure; generic re-entry is disabled.')
                        elif not det:item=blank(n,'SCANNING','No qualifying setup detected.')
                        elif det['score']<MIN_SCORE:item={**blank(n,'WATCH — CONFLUENCE BELOW THRESHOLD','Setup detected but the combined confirmation score is below the entry threshold.'),'direction':det['direction'],'setup':det['setup'],'score':det['score'],'reason':' • '.join(det['why'])}
                        else:
                            q,o=pick(opt,det['u'],det['direction'])
                            if not o or q<75:item={**blank(n,'WATCH — OPTION QUALITY INSUFFICIENT','Underlying setup qualifies, but no sufficiently liquid option contract is available.'),'direction':det['direction'],'setup':det['setup'],'score':det['score'],'reason':' • '.join(det['why'])}
                            else:b['active']=signal(n,det['direction'],o,q,t,det);b['mode']='ACTIVE';item=b['active']
            except Exception as e:item={**blank(n,'DATA ERROR','No signal fabricated from incomplete/broken data.'),'cls':'warn','reason':str(e)}
            items.append(item)
        STATE.update(scan_time=t.strftime('%d %b %Y %H:%M:%S IST'),session=sess,items=items,trades=sum((b['trades'] for b in BOOK.values()),[]))

def worker():
    while True:
        try:scan()
        except Exception as e:print('IOE ERROR',e,flush=True)
        time.sleep(SCAN)
threading.Thread(target=worker,daemon=True).start()

@app.route('/')
def home():
    with LOCK:d=json.loads(json.dumps(STATE,default=str))
    return render_template_string(HTML,**d)
@app.route('/health')
def health():return jsonify({'status':'ok','service':'psycho-opportunity-engine-v4','last_scan':STATE['scan_time']})
@app.route('/api/state')
def api_state():return jsonify(STATE)
@app.route('/api/trades')
def api_trades():return jsonify({'date':now().date().isoformat(),'trades':STATE['trades']})
if __name__=='__main__':app.run(host='0.0.0.0',port=10000)
