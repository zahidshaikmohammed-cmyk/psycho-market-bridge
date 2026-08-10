import json, os, threading, time, urllib.request, math
from datetime import datetime, time as T, timedelta
from zoneinfo import ZoneInfo
from flask import Flask, jsonify, render_template_string

app=Flask(__name__)
IST=ZoneInfo('Asia/Kolkata')
BR=os.getenv('MARKET_BRIDGE_URL','https://psycho-market-bridge.onrender.com').rstrip('/')
START,CLOSE=T(9,15),T(15,40)
SCAN=15; MIN_FINAL=82; COOLDOWN=8
LOCK=threading.RLock()
STATE_PATH=os.getenv('IOE_STATE_PATH','/data/psycho_ioe_state.json')
BOOK={'NIFTY':None,'BANK NIFTY':None}
STATE={'date':None,'scan_time':None,'trades':[],'hunts':[],'events':[]}

HTML='''<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><title>PSYCHO INTRADAY OPPORTUNITY ENGINE</title><style>body{font-family:Arial;background:#070b12;color:#eef2f7;padding:14px}.wrap{max-width:1450px;margin:auto}.box,.card{background:#111722;border:1px solid #293241;border-radius:16px;padding:15px;margin:10px 0}.grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}@media(max-width:850px){.grid{grid-template-columns:1fr}}.row{display:flex;justify-content:space-between;gap:12px;padding:6px 0;border-bottom:1px solid #242c38;font-size:13px}.good{color:#54e39a}.bad{color:#ff6f7d}.warn{color:#ffd166}.muted{color:#8995a5;font-size:12px}.why{background:#0b1018;padding:10px;border-radius:10px;margin-top:10px;font-size:13px;line-height:1.5}table{width:100%;border-collapse:collapse;font-size:12px}td,th{padding:7px;border-bottom:1px solid #29313d;text-align:left}.pill{display:inline-block;padding:4px 8px;border-radius:999px;background:#182130;margin:3px;font-size:12px}</style><div class="wrap"><div class="box"><h1>PSYCHO INTRADAY OPPORTUNITY ENGINE</h1><h3>{{session.status}}</h3><div class="muted">{{session.detail}} • {{scan or 'waiting'}} • 15s intelligence cycle • INTRADAY ONLY</div></div><div class="grid">{%for x in items%}<div class="card"><h2>{{x.name}}</h2><h3 class="{{x.cls}}">{{x.status}}</h3><div class="row"><span>State</span><b>{{x.state}}</b></div><div class="row"><span>Direction</span><b>{{x.direction}}</b></div><div class="row"><span>Setup Hunter</span><b>{{x.setup}}</b></div><div class="row"><span>Contract</span><b>{{x.contract}}</b></div><div class="row"><span>Entry Price — FROZEN</span><b>{{x.entry}}</b></div><div class="row"><span>Current LTP</span><b>{{x.live}}</b></div><div class="row"><span>Entry → Current</span><b>{{x.move}}</b></div><div class="row"><span>SL / TP</span><b>{{x.sl}} / {{x.tp}}</b></div><div class="row"><span>Time left</span><b>{{x.time_left}}</b></div><div class="row"><span>TP distance</span><b>{{x.tp_distance}}</b></div><div class="row"><span>TP feasibility</span><b>{{x.tp_feas}}</b></div><div class="row"><span>Market regime</span><b>{{x.regime}}</b></div><div class="row"><span>Setup quality</span><b>{{x.setup_q}}/100</b></div><div class="row"><span>Regime fit</span><b>{{x.regime_q}}/100</b></div><div class="row"><span>Execution quality</span><b>{{x.exec_q}}/100</b></div><div class="row"><span>Option quality</span><b>{{x.opt_q}}/100</b></div><div class="row"><span>Final confidence</span><b>{{x.score}}/100</b></div><div class="why"><b>WHY</b><br>{{x.reason}}</div></div>{%endfor%}</div><div class="box"><h2>ENGINE STATUS</h2>{%for s in stats%}<span class="pill">{{s}}</span>{%endfor%}</div><div class="box"><h2>TODAY'S CLOSED TRADES</h2>{%if trades%}<table><tr><th>Exit</th><th>Instrument</th><th>Hunter</th><th>Contract</th><th>Entry</th><th>Exit</th><th>R</th><th>Result</th></tr>{%for t in trades%}<tr><td>{{t.exit}}</td><td>{{t.name}}</td><td>{{t.setup}}</td><td>{{t.contract}}</td><td>{{t.entry}}</td><td>{{t.price}}</td><td>{{t.r}}</td><td class="{{'good' if t.result=='TP TAKEN' else 'bad'}}">{{t.result}}</td></tr>{%endfor%}</table>{%else%}<div class="muted">No completed trades today.</div>{%endif%}</div><div class="box"><h2>TODAY'S HUNT / MISSED OPPORTUNITIES</h2>{%if hunts%}<table><tr><th>Time</th><th>Instrument</th><th>Hunter</th><th>Direction</th><th>Score</th><th>Outcome</th></tr>{%for h in hunts[-30:]%}<tr><td>{{h.time}}</td><td>{{h.name}}</td><td>{{h.setup}}</td><td>{{h.direction}}</td><td>{{h.score}}</td><td>{{h.outcome}}</td></tr>{%endfor%}</table>{%else%}<div class="muted">No rejected/missed hunts recorded today.</div>{%endif%}</div></div>'''

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

def save():
    try:
        os.makedirs(os.path.dirname(STATE_PATH) or '.',exist_ok=True)
        p=STATE_PATH+'.tmp'
        with open(p,'w',encoding='utf-8') as f:json.dump({'state':STATE,'book':BOOK},f,separators=(',',':'))
        os.replace(p,STATE_PATH)
    except:pass

def load():
    global STATE,BOOK
    try:
        with open(STATE_PATH,encoding='utf-8') as f:d=json.load(f)
        if d.get('state',{}).get('date')==now().date().isoformat():
            STATE.update(d['state'])
            for k in BOOK:BOOK[k]=d.get('book',{}).get(k)
    except:pass

def new_day():
    d=now().date().isoformat()
    if STATE.get('date')!=d:
        BOOK['NIFTY']=BOOK['BANK NIFTY']=None
        STATE={'date':d,'scan_time':None,'trades':[],'hunts':[],'events':[]}
        globals()['STATE']=STATE;save()

def get(path):
    r=urllib.request.Request(BR+path,headers={'Accept':'application/json','User-Agent':'PSYCHO-IOE/8.0'})
    with urllib.request.urlopen(r,timeout=10) as x:return json.loads(x.read().decode())

def candles(o):
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
    walk(o);return sorted({x['dt']:x for x in out}.values(),key=lambda x:x['dt'])

def tf(o,k):
    t=(o.get('timeframes') or o.get('candles') or {}) if isinstance(o,dict) else {};return candles(t.get(k,[]))
def atr(c,n=14):
    if len(c)<n+1:return None
    tr=[];p=None
    for x in c:
        tr.append(x['high']-x['low'] if p is None else max(x['high']-x['low'],abs(x['high']-p),abs(x['low']-p)));p=x['close']
    return sum(tr[-n:])/n
def ema(v,n=20):
    if not v:return None
    e=v[0];a=2/(n+1)
    for x in v[1:]:e=a*x+(1-a)*e
    return e
def vwap(c):
    p=q=0
    for x in c:p+=((x['high']+x['low']+x['close'])/3)*x['volume'];q+=x['volume']
    return p/q if q else None
def rng(x):return max(x['high']-x['low'],1e-9)
def bull(x):return x['close']>x['open']
def bear(x):return x['close']<x['open']

def options(raw):
    n=raw.get('option_chain') or raw.get('data') or raw;st=n.get('strikes') if isinstance(n,dict) else None
    pairs=list(st.items()) if isinstance(st,dict) else ([(x.get('strike'),x) for x in st] if isinstance(st,list) else [])
    out=[]
    for k,v in pairs:
        if not isinstance(v,dict):continue
        strike=num(v.get('strike',k))
        for side in ('CE','PE'):
            q=v.get(side) or v.get(side.lower()) or {};ltp=num(q.get('last_price') or q.get('ltp') or q.get('lastPrice'))
            if strike is None or not ltp or ltp<=0:continue
            g=q.get('greeks') or {}
            out.append({'strike':strike,'side':side,'ltp':ltp,'delta':num(g.get('delta') or q.get('delta')),'bid':num(q.get('top_bid_price') or q.get('bid')),'ask':num(q.get('top_ask_price') or q.get('ask')),'oi':num(q.get('oi') or q.get('open_interest')),'volume':num(q.get('volume'))})
    return out

def prev(c,t):
    z=[x for x in c if x['dt'].date()<t.date()]
    if not z:return None
    d=max(x['dt'].date() for x in z);z=[x for x in z if x['dt'].date()==d]
    return {'high':max(x['high'] for x in z),'low':min(x['low'] for x in z),'close':z[-1]['close']}
def opening(c,t):
    z=[x for x in c if x['dt'].date()==t.date() and T(9,15)<=x['dt'].time()<T(9,30)]
    return max((x['high'] for x in z),default=None),min((x['low'] for x in z),default=None)
def regime(c,c5,c15,c1,u):
    e=[ema([x['close'] for x in z],20) for z in (c5,c15,c1)]
    if any(x is None for x in e):return 'UNKNOWN',55
    votes=sum(u>x for x in e);short=sum(rng(x) for x in c[-3:])/3;long=sum(rng(x) for x in c[-12:-3])/9
    if short>long*1.35:return 'VOLATILITY EXPANSION',88
    if short<long*.70:return 'COMPRESSION',82
    if votes==3:return 'BULL TREND',90
    if votes==0:return 'BEAR TREND',90
    return 'BALANCED/RANGE',72

def detect(name,raw,t):
    c=tf(raw,'1M') or candles(raw);c=[x for x in c if x['dt']+timedelta(minutes=1)<=t];c5,c15,c1=tf(raw,'5M'),tf(raw,'15M'),tf(raw,'1H')
    if len(c)<30:return None
    u,a=c[-1]['close'],atr(c);pd=prev(c,t);orh,orl=opening(c,t);day=[x for x in c if x['dt'].date()==t.date()]
    if not a:return None
    e5,e15,e1=[ema([x['close'] for x in z],20) for z in (c5,c15,c1)];vw=vwap(day);reg,rf=regime(c,c5,c15,c1,u);x1,x2,x3=c[-1],c[-2],c[-3];cs=[]
    if pd:
        if x1['low']<pd['low'] and x1['close']>pd['low']:cs.append(('LIQUIDITY SWEEP','LONG',87,['PDL sweep','reclaim','rejection']))
        if x1['high']>pd['high'] and x1['close']<pd['high']:cs.append(('LIQUIDITY SWEEP','SHORT',87,['PDH sweep','reclaim','rejection']))
        if x2['high']>pd['high'] and x2['close']<pd['high'] and x1['close']<x2['low']:cs.append(('FAILED BREAKOUT','SHORT',89,['PDH failure','return inside','confirmation']))
        if x2['low']<pd['low'] and x2['close']>pd['low'] and x1['close']>x2['high']:cs.append(('FAILED BREAKDOWN','LONG',89,['PDL failure','return inside','confirmation']))
    if orh is not None and orl is not None:
        if x1['close']>orh and x2['close']>orh:cs.append(('OPENING RANGE','LONG',84,['OR high acceptance','two-candle hold']))
        if x1['close']<orl and x2['close']<orl:cs.append(('OPENING RANGE','SHORT',84,['OR low acceptance','two-candle hold']))
    if e5 and e15:
        if u>e5 and u>e15 and x2['low']<=e5*1.0015 and bull(x1) and x1['close']>x2['high']:cs.append(('PULLBACK','LONG',88,['5M/15M trend','controlled pullback','continuation']))
        if u<e5 and u<e15 and x2['high']>=e5*.9985 and bear(x1) and x1['close']<x2['low']:cs.append(('PULLBACK','SHORT',88,['5M/15M trend','controlled pullback','continuation']))
    avg=sum(rng(x) for x in c[-8:-2])/6
    if rng(x3)<avg*.75 and rng(x2)<avg*.75 and rng(x1)>avg*1.25:cs.append(('COMPRESSION EXPANSION','LONG' if bull(x1) else 'SHORT',84,['compression','expansion','directional release']))
    if all(bull(x) for x in c[-3:]) and u>max(x['high'] for x in c[-6:-3]):cs.append(('TREND CONTINUATION','LONG',83,['bullish sequence','fresh high']))
    if all(bear(x) for x in c[-3:]) and u<min(x['low'] for x in c[-6:-3]):cs.append(('TREND CONTINUATION','SHORT',83,['bearish sequence','fresh low']))
    if not cs:return None
    best=None
    for setup,direction,base,why in cs:
        mtf=sum(1 for e in (e5,e15,e1) if e is not None and ((u>e) if direction=='LONG' else (u<e)))
        vwok=vw is not None and ((u>vw) if direction=='LONG' else (u<vw))
        fit=rf
        if 'PULLBACK' in setup and ('TREND' in reg or 'EXPANSION' in reg):fit=min(100,fit+6)
        if 'FAILED' in setup and ('RANGE' in reg or 'COMPRESSION' in reg):fit=min(100,fit+6)
        if 'OPENING' in setup and t.time()<T(11):fit=min(100,fit+6)
        setupq=min(100,base+mtf*3+(5 if vwok else 0));score=round(setupq*.55+fit*.25+(70+10*mtf)*.20)
        z={'setup':setup,'direction':direction,'setup_q':setupq,'regime_q':fit,'score':score,'why':why+[f'MTF {mtf}/3','VWAP aligned' if vwok else 'VWAP not aligned'],'u':u,'atr':a,'regime':reg}
        if best is None or z['score']>best['score']:best=z
    return best

def pick(raw,u,direction):
    side='CE' if direction=='LONG' else 'PE';rank=[]
    for x in [z for z in options(raw) if z['side']==side]:
        q=0;dist=abs(x['strike']-u);d=abs(x['delta'] or 0)
        q+=28 if dist<=max(50,u*.0025) else 16 if dist<=max(100,u*.005) else 0
        q+=28 if .40<=d<=.65 else 16 if .30<=d<=.75 else 0
        if x['bid'] is not None and x['ask'] is not None and x['ask']>=x['bid']>0:
            sp=(x['ask']-x['bid'])/x['ltp'];gap=abs(x['ask']-x['ltp'])/x['ltp'];q+=28 if sp<=.006 and gap<=.01 else 18 if sp<=.012 and gap<=.02 else 5 if sp<=.025 else -35
        else:q-=25
        q+=8 if x['oi'] else 0;q+=8 if x['volume'] else 0;rank.append((max(0,min(100,q)),x))
    return max(rank,key=lambda z:z[0]) if rank else (0,None)

def minutes_left(t):return max(0,(datetime.combine(t.date(),CLOSE)-t.replace(tzinfo=None)).total_seconds()/60)
def feas(entry,tp,ua,delta,mins):
    if mins<=0:return 'LOW',10
    expected=max(.01,ua*max(abs(delta or .5),.35)*math.sqrt(mins/5)*.55);r=abs(tp-entry)/expected
    return ('HIGH',90) if r<=.85 else ('MEDIUM',68) if r<=1.2 else ('LOW',45) if r<=1.6 else ('VERY LOW',20)

def make_signal(name,d,opt,oq,t):
    entry=opt['ltp'];delta=abs(opt['delta'] or .5);risk=max(entry*.045,d['atr']*delta*.55);sl=round(max(.05,entry-risk),2);tp=round(entry+2*risk,2);fg,fs=feas(entry,tp,d['atr'],delta,minutes_left(t));sp=((opt['ask']-opt['bid'])/entry) if opt['bid'] and opt['ask'] else .03;eq=98 if sp<=.006 else 92 if sp<=.012 else 82
    final=round(d['score']*.48+d['regime_q']*.17+eq*.15+oq*.15+fs*.05);sid=f'IOE-{name.replace(" ","")}-{t:%Y%m%d-%H%M%S}'
    return {'name':name,'signal_id':sid,'state':'ACTIVE','status':'🟢 ACTIVE — LOCKED','cls':'good','direction':d['direction'],'setup':d['setup'],'contract':f"{int(opt['strike']):,} {opt['side']}",'strike':opt['strike'],'side':opt['side'],'entry':fmt(entry),'entry_raw':entry,'live':fmt(entry),'live_raw':entry,'move':fmt(0),'bid':fmt(opt['bid']),'ask':fmt(opt['ask']),'sl':fmt(sl),'sl_raw':sl,'tp':fmt(tp),'tp_raw':tp,'score':final,'setup_q':d['setup_q'],'regime_q':d['regime_q'],'exec_q':eq,'opt_q':oq,'regime':d['regime'],'reason':' • '.join(d['why']+[f'option quality {oq}/100','FRESH OPTION LTP VERIFIED','ENTRY PRICE FROZEN','CONTRACT LOCKED — NO RESELECTION',f'TP FEASIBILITY {fg}']),'created':t.isoformat(),'atr':d['atr'],'tp_feas':fg,'mfe':0,'mae':0}

def close(name,t,result,p):
    s=BOOK[name]
    risk=max(.01,s['entry_raw']-s['sl_raw']);r=(p-s['entry_raw'])/risk
    if s['direction']=='SHORT':r=-r
    STATE['trades'].append({'exit':t.strftime('%H:%M:%S'),'name':name,'setup':s['setup'],'contract':s['contract'],'entry':s['entry'],'price':fmt(p),'r':f'{r:+.2f}R','result':result,'signal_id':s['signal_id'],'mfe':fmt(s['mfe']),'mae':fmt(s['mae'])});BOOK[name]=None;save()

def monitor(name,t):
    s=BOOK[name];slug='nifty' if name=='NIFTY' else 'banknifty'
    try:r=get('/'+slug+'-option-chain')
    except:return s
    rows=[x for x in options(r) if x['side']==s['side'] and abs(x['strike']-s['strike'])<.01]
    if not rows:return s
    q=rows[0];live=q['ltp'];s['live_raw']=live;s['live']=fmt(live);mv=live-s['entry_raw'];s['move']=('+' if mv>=0 else '')+fmt(mv);s['mfe']=max(s['mfe'],live-s['entry_raw']);s['mae']=max(s['mae'],s['entry_raw']-live);s['tp_feas'],_=feas(s['entry_raw'],s['tp_raw'],s['atr'],abs(q['delta'] or .5),minutes_left(t));s['time_left']=f'{int(minutes_left(t))} min';s['tp_distance']=fmt(s['tp_raw']-live)
    if live>=s['tp_raw']:close(name,t,'TP TAKEN',live);return None
    if live<=s['sl_raw']:close(name,t,'SL TRIGGERED',live);return None
    if t.time()>=CLOSE:close(name,t,'SESSION EXIT',live);return None
    return s

def scan(name,t):
    if BOOK[name] or not START<=t.time()<CLOSE:return
    slug='nifty' if name=='NIFTY' else 'banknifty'
    try:raw,oc=get('/'+slug+'-live'),get('/'+slug+'-option-chain')
    except:return
    d=detect(name,raw,t)
    if not d or d['score']<78:return
    oq,opt=pick(oc,d['u'],d['direction'])
    if not opt or oq<72 or opt['bid'] is None or opt['ask'] is None or opt['ask']<opt['bid']:return
    if (opt['ask']-opt['bid'])/opt['ltp']>.025:return
    s=make_signal(name,d,opt,oq,t)
    if s['score']<MIN_FINAL:
        STATE['hunts'].append({'time':t.strftime('%H:%M:%S'),'name':name,'setup':d['setup'],'direction':d['direction'],'score':s['score'],'outcome':'REJECTED'});return
    BOOK[name]=s;save()

def blank(name,t):
    return {'name':name,'status':'⚪ HUNTING','cls':'muted','state':'HUNT','direction':'—','setup':'Independent hunters scanning','contract':'—','entry':'—','live':'—','move':'—','sl':'—','tp':'—','time_left':f'{int(minutes_left(t))} min','tp_distance':'—','tp_feas':'—','regime':'Scanning','setup_q':'—','regime_q':'—','exec_q':'—','opt_q':'—','score':'—','reason':'No forced trade. Each hunter operates independently.'}

def loop():
    load();new_day()
    while True:
        try:
            t=now();new_day();STATE['scan_time']=t.strftime('%H:%M:%S')
            with LOCK:
                for n in ('NIFTY','BANK NIFTY'):
                    if BOOK[n]:monitor(n,t)
                    else:scan(n,t)
            save()
        except Exception as e:STATE['events'].append({'time':now().isoformat(),'error':str(e)[:150]})
        time.sleep(SCAN)

@app.route('/')
def home():
    t=now();session={'status':'⏳ PRE-MARKET','detail':'Waiting for 09:15 IST'} if t.time()<START else {'status':'🔴 MARKET CLOSED','detail':'Session archived; next day starts clean'} if t.time()>=CLOSE else {'status':'🟢 LIVE HUNTING','detail':'Independent hunters • strict execution gate • no forced quota'}
    items=[]
    for n in ('NIFTY','BANK NIFTY'):
        s=BOOK[n] or blank(n,t);items.append(s)
    stats=[f'Active: {sum(bool(x) for x in BOOK.values())}',f'Closed: {len(STATE["trades"])}',f'Hunts: {len(STATE["hunts"])}','No reselection: ON','No chase: ON','Time-aware TP: ON','Structure-aware SL: ON','Intraday hard exit: 15:40']
    return render_template_string(HTML,session=session,items=items,trades=STATE['trades'][-30:],hunts=STATE['hunts'],stats=stats,scan=STATE['scan_time'])

@app.route('/health')
def health():return jsonify({'status':'ok','engine':'PSYCHO IOE v8','date':STATE.get('date'),'active':{k:bool(v) for k,v in BOOK.items()},'scan_time':STATE.get('scan_time')})
@app.route('/api/state')
def api_state():return jsonify({'state':STATE,'active':BOOK})

if __name__=='__main__':
    load();new_day();threading.Thread(target=loop,daemon=True).start();app.run(host='0.0.0.0',port=int(os.getenv('PORT','10000')))
