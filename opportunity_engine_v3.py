import json,threading,time,urllib.request
from datetime import datetime,time as T,timedelta
from zoneinfo import ZoneInfo
from flask import Flask,jsonify,render_template_string
app=Flask(__name__); IST=ZoneInfo('Asia/Kolkata'); BR='https://psycho-market-bridge.onrender.com'; OPEN,START,CLOSE=T(9,15),T(9,30),T(15,40); LOCK=threading.Lock(); BOOK={}; STATE={'scan_time':None,'session':{},'items':[],'trades':[]}
HTML='''<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="refresh" content="15"><style>body{font-family:Arial;background:#080a0e;color:#eee;padding:16px}.card,.trades,.banner{background:#14181e;border:1px solid #303640;border-radius:14px;padding:16px;margin:12px auto;max-width:1100px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:12px;max-width:1100px;margin:auto}@media(max-width:800px){.grid{grid-template-columns:1fr}}.g{color:#63e6a7}.r{color:#ff7777}.y{color:#ffd166}.b{color:#73b7ff}.row{display:flex;justify-content:space-between;padding:6px;border-bottom:1px solid #252b33}.muted{color:#8993a0;font-size:12px}table{width:100%;font-size:12px;border-collapse:collapse}td,th{padding:7px;border-bottom:1px solid #292f37;text-align:left}</style><div class="banner"><h1>PSYCHO INTRADAY OPPORTUNITY ENGINE</h1><b>{{session.status}}</b><div class="muted">{{session.detail}} • scans every 15 seconds</div></div><div class="grid">{%for x in items%}<div class="card"><h2>{{x.name}}</h2><h3 class="{{x.cls}}">{{x.status}}</h3><div class="row"><span>Signal</span><b>{{x.signal_id}}</b></div><div class="row"><span>State</span><b>{{x.state}}</b></div><div class="row"><span>Direction</span><b>{{x.direction}}</b></div><div class="row"><span>Setup</span><b>{{x.setup}}</b></div><div class="row"><span>Contract</span><b>{{x.contract}}</b></div><div class="row"><span>Entry</span><b>{{x.entry}}</b></div><div class="row"><span>SL</span><b>{{x.sl}}</b></div><div class="row"><span>TP</span><b>{{x.tp}}</b></div><div class="row"><span>Live premium</span><b>{{x.live}}</b></div><div class="row"><span>Score</span><b>{{x.score}}/100</b></div><p class="muted">{{x.reason}}</p></div>{%endfor%}</div><div class="trades"><h2>TODAY'S CLOSED TRADES</h2>{%if trades%}<table><tr><th>Exit</th><th>Instrument</th><th>Signal</th><th>Direction</th><th>Contract</th><th>Entry</th><th>Exit</th><th>Result</th></tr>{%for t in trades%}<tr><td>{{t.exit_time}}</td><td>{{t.name}}</td><td>{{t.signal_id}}</td><td>{{t.direction}}</td><td>{{t.contract}}</td><td>{{t.entry}}</td><td>{{t.exit}}</td><td class="{{'g' if t.result=='TP TAKEN' else 'r'}}">{{t.result}}</td></tr>{%endfor%}</table>{%else%}<div class="muted">No completed trades today.</div>{%endif%}</div>'''
def now():return datetime.now(IST)
def f(v):return '—' if v is None else f'{v:,.2f}'
def get(p):
 with urllib.request.urlopen(urllib.request.Request(BR+p,headers={'Accept':'application/json'}),timeout=15) as r:return json.loads(r.read().decode())
def num(v):
 try:return float(v)
 except:return None
def candles(v):
 o=[]
 def w(z):
  if isinstance(z,dict):
   if all(k in z for k in ('open','high','low','close')):
    try:
     ts=z.get('timestamp') or z.get('time') or z.get('datetime');d=datetime.fromisoformat(ts.replace('Z','+00:00')).astimezone(IST) if isinstance(ts,str) and not ts.isdigit() else datetime.fromtimestamp(int(ts),IST);o.append({'dt':d,'open':float(z['open']),'high':float(z['high']),'low':float(z['low']),'close':float(z['close']),'volume':num(z.get('volume')) or 0})
    except:pass
   for x in z.values():w(x)
  elif isinstance(z,list):
   for x in z:w(x)
 w(v);return sorted({x['dt']:x for x in o}.values(),key=lambda x:x['dt'])
def atr(c,n=14):
 if len(c)<n+1:return None
 tr=[];p=None
 for x in c:tr.append(x['high']-x['low'] if p is None else max(x['high']-x['low'],abs(x['high']-p),abs(x['low']-p)));p=x['close']
 return sum(tr[-n:])/n
def ema(c,n=20):
 if not c:return None
 e=c[0];a=2/(n+1)
 for x in c[1:]:e=a*x+(1-a)*e
 return e
def opts(raw):
 s=(raw.get('option_chain') or {}).get('strikes') or {};o=[]
 for k,v in s.items():
  st=num(v.get('strike',k))
  for side in ('CE','PE'):
   q=v.get(side) or {};p=num(q.get('last_price'))
   if st is not None and p and p>0:o.append({'strike':st,'side':side,'ltp':p,'delta':num((q.get('greeks') or {}).get('delta')),'bid':num(q.get('top_bid_price')),'ask':num(q.get('top_ask_price')),'oi':num(q.get('oi')),'volume':num(q.get('volume'))})
 return o
def pick(raw,u,d,a,minq=75):
 side='CE' if d=='LONG' else 'PE';rank=[]
 for o in opts(raw):
  if o['side']!=side:continue
  q=25 if a and abs(o['strike']-u)<=.75*a else 15;dd=abs(o['delta'] or 0);q+=25 if .4<=dd<=.65 else 12 if .3<=dd<=.75 else 0
  if o['bid'] is not None and o['ask'] is not None and o['ask']>=o['bid']:
   sp=(o['ask']-o['bid'])/o['ltp'];q+=25 if sp<=.01 else 15 if sp<=.02 else 5
  q+=10 if (o['oi'] or 0)>0 else 0;q+=10 if (o['volume'] or 0)>0 else 0;rank.append((min(q,100),o))
 return max(rank,key=lambda x:x[0]) if rank else (0,None)
def blank(n,s='SCANNING'):return {'name':n,'signal_id':'—','state':'SCANNING','status':s,'cls':'b','direction':'—','setup':'NONE','contract':'—','entry':'—','sl':'—','tp':'—','live':'—','score':0,'reason':'No active signal.'}
def make(n,d,o,q,u,invalid,t,setup):
 e=o['ask'] if o.get('ask') and o['ask']>0 else o['ltp'];risk=max(abs(u-invalid),.15);pr=max(risk*abs(o.get('delta') or .5),e*.1);sl=round(max(.05,e-pr),2);tp=round(e+2*(e-sl),2);sid=f'IOE-{n.replace(" ","")}-{t:%Y%m%d-%H%M%S}'
 return {'name':n,'signal_id':sid,'state':'ACTIVE','status':'🟢 SIGNAL ACTIVE — LOCKED','cls':'g','direction':d,'setup':setup,'contract':f"{int(o['strike']):,} {o['side']}",'strike':o['strike'],'side':o['side'],'entry':f(e),'entry_raw':e,'sl':f(sl),'sl_raw':sl,'tp':f(tp),'tp_raw':tp,'live':f(o['ltp']),'score':q,'reason':f'{setup}: locked. No option reselection until this trade is closed.','underlying_raw':u}
def trend(c,d,u):
 e=ema([x['close'] for x in c],20);return bool(e and ((d=='LONG' and u>e)or(d=='SHORT' and u<e)))
def initial(n,raw,t):
 m=raw.get('market') or raw;allc=candles(m);today=[x for x in allc if x['dt'].date()==t.date()];prior=[x for x in allc if x['dt'].date()<t.date()];ds=sorted({x['dt'].date() for x in prior});pd=[x for x in prior if ds and x['dt'].date()==ds[-1]]
 if not today or not pd:return blank(n,'WAITING — CONTEXT')
 u=today[-1]['close'];pdh=max(x['high'] for x in pd);pdl=min(x['low'] for x in pd);a=atr(allc);tf=m.get('timeframes') or {};c1=candles(tf.get('1M') or []);c5=candles(tf.get('5M') or []);c15=candles(tf.get('15M') or []);c1h=candles(tf.get('1H') or [])
 if not a or len(c1)<3:return blank(n,'WAITING — DATA')
 c1=[x for x in c1 if x['dt']+timedelta(minutes=1)<=t];br,ac=c1[-2],c1[-1];d='LONG' if br['close']>pdh and br['close']-pdh>=.15*a and ac['close']>pdh and ac['low']>pdh else 'SHORT' if br['close']<pdl and pdl-br['close']>=.15*a and ac['close']<pdl and ac['high']<pdl else None
 if not d:return {**blank(n,'WATCH — NO CONFIRMED BREAK'),'direction':'—','setup':'NONE'}
 votes=sum(trend(c,d,u) for c in (c5,c15,c1h) if c);q=60+(20 if votes==3 else 10 if votes==2 else 0);oq,o=pick(raw,u,d,a);q=min(100,q+(20 if oq>=75 else 10 if oq>=60 else 0))
 if votes<2 or oq<75 or q<QUALIFY:return {**blank(n,'WATCH — CONFIRMATION REQUIRED'),'direction':d,'setup':'BREAKOUT + ACCEPTANCE','score':q,'reason':'Structure found but full confluence is below threshold.'}
 return make(n,d,o,q,u,pdl if d=='LONG' else pdh,t,'BREAKOUT + ACCEPTANCE')
def pullback(n,raw,t,last):
 m=raw.get('market') or raw;tf=m.get('timeframes') or {};c=candles(tf.get('1M') or []);c=[x for x in c if x['dt']+timedelta(minutes=1)<=t]
 if len(c)<21:return None
 d=last['direction'];u=c[-1]['close'];e=ema([x['close'] for x in c],20);a=atr(c);x=c[-1];touch=(x['low']<=e*1.001 if d=='LONG' else x['high']>=e*.999);reject=(x['close']>x['open'] and x['close']>e) if d=='LONG' else (x['close']<x['open'] and x['close']<e);c5=candles(tf.get('5M') or []);c15=candles(tf.get('15M') or [])
 if not(touch and reject and trend(c5,d,u) and trend(c15,d,u)):return None
 oq,o=pick(raw,u,d,a,80)
 if not o or oq<80:return None
 return make(n,d,o,oq,u,last['underlying_raw'],t,'PULLBACK REJECTION')
def close(n,t):
 s=BOOK[n]['active'];raw=get('/nifty-option-chain' if n=='NIFTY' else '/banknifty-option-chain');r=[o for o in opts(raw) if o['side']==s['side'] and abs(o['strike']-s['strike'])<.01]
 if not r:return
 p=r[0]['ltp'];s['live']=f(p);res='TP TAKEN' if p>=s['tp_raw'] else 'SL TRIGGERED' if p<=s['sl_raw'] else None
 if not res:return
 s['state']='CLOSED';s['status']='🟢 '+res if res=='TP TAKEN' else '🔴 '+res;s['cls']='g' if res=='TP TAKEN' else 'r';BOOK[n]['trades'].append({'exit_time':t.strftime('%H:%M:%S IST'),'name':n,'signal_id':s['signal_id'],'direction':s['direction'],'contract':s['contract'],'entry':f(s['entry_raw']),'exit':f(p),'result':res});BOOK[n]['last']=s;BOOK[n]['active']=None;BOOK[n]['mode']='PULLBACK_ONLY'
def scan():
 t=now();ss={'status':'MARKET CLOSED','detail':'Waiting for session.' if t.time()<OPEN else 'Session complete.'} if t.time()<OPEN or t.time()>CLOSE or t.weekday()>=5 else {'status':'MARKET OPEN — PRECISION SCANNING','detail':'One locked trade at a time. After SL/TP: pullback-only reentry.'}
 with LOCK:
  for n in ('NIFTY','BANK NIFTY'):
   if BOOK.get(n,{}).get('date')!=t.date():BOOK[n]={'date':t.date(),'active':None,'mode':'INITIAL','last':None,'trades':[]}
  items=[]
  for n,path in [('NIFTY','/nifty-live'),('BANK NIFTY','/banknifty-live')]:
   b=BOOK[n]
   try:
    if t.time()<START or t.time()>CLOSE: item=blank(n,'BUILDING CONTEXT' if t.time()<START else 'MARKET CLOSED')
    else:
     raw=get(path)
     if b['active']:close(n,t)
     if b['active']:item=b['active']
     elif b['mode']=='PULLBACK_ONLY':
      z=pullback(n,raw,t,b['last']);
      if z:b['active']=z;b['mode']='ACTIVE';item=z
      else:item={**blank(n,'🟡 PULLBACK HUNTING'),'state':'PULLBACK_ONLY','reason':'Previous trade closed. Breakout entries disabled; waiting for pullback + rejection + MTF confirmation.'}
     else:
      z=initial(n,raw,t)
      if z.get('state')=='ACTIVE':b['active']=z;b['mode']='ACTIVE'
      item=z
   except Exception as e:item={**blank(n,'DATA UNAVAILABLE'),'cls':'y','reason':str(e)}
   items.append(item)
  STATE.update(scan_time=t.strftime('%d %b %Y %H:%M:%S IST'),session=ss,items=items,trades=sum((b['trades'] for b in BOOK.values()),[]))
def worker():
 while True:
  try:scan()
  except Exception as e:print('IOE ERROR',e,flush=True)
  time.sleep(15)
threading.Thread(target=worker,daemon=True).start()
@app.route('/')
def home():
 with LOCK:d=json.loads(json.dumps(STATE,default=str))
 return render_template_string(HTML,**d)
@app.route('/health')
def health():return jsonify({'status':'ok','service':'psycho-opportunity-engine-v3','last_scan':STATE['scan_time']})
@app.route('/api/state')
def state():return jsonify(STATE)
@app.route('/api/trades')
def trades():return jsonify({'date':now().date().isoformat(),'trades':STATE['trades']})
if __name__=='__main__':app.run(host='0.0.0.0',port=10000)
