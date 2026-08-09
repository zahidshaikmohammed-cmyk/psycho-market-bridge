import json, threading, time, urllib.request
from datetime import datetime, time as dt_time
from zoneinfo import ZoneInfo
from flask import Flask, jsonify, render_template_string

app=Flask(__name__)
IST=ZoneInfo('Asia/Kolkata')
BRIDGE='https://psycho-market-bridge.onrender.com'
OPEN=dt_time(9,15); WINDOW_START=dt_time(10,30); RANGE_END=dt_time(10,40); WINDOW_END=dt_time(11,30); CLOSE=dt_time(15,40)
SCAN_SECONDS=60
TRIGGERED={'NIFTY':False,'BANK NIFTY':False}
LOCK=threading.Lock(); STATE={'scan_time':None,'session':{},'items':[]}

HTML='''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="refresh" content="60"><title>PSYCHO 10301130</title><style>body{font-family:Arial;background:#090b0f;color:#f4f6f8;margin:0;padding:18px}.wrap{max-width:1050px;margin:auto}h1{margin:0}.sub{color:#9aa3ad;margin:6px 0 18px}.banner,.card{background:#14181e;border:1px solid #29303a;border-radius:14px;padding:18px;margin-bottom:16px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}@media(max-width:800px){.grid{grid-template-columns:1fr}}.title{font-size:22px;font-weight:800}.status{padding:13px;border-radius:10px;background:#202631;font-weight:800;font-size:18px;margin:12px 0}.green{color:#63e6a7}.yellow{color:#ffd166}.blue{color:#73b7ff}.row{display:flex;justify-content:space-between;border-bottom:1px solid #232a33;padding:7px 0}.label{color:#aab3bf}.value{font-weight:700;text-align:right}.section{margin-top:16px;font-weight:800;font-size:12px;color:#8ea0b4;letter-spacing:.08em}.hero{font-size:30px;font-weight:900}.small{font-size:12px;color:#8d96a2;line-height:1.5;margin-top:12px}.footer{color:#707985;font-size:11px;margin-top:14px}</style></head><body><div class="wrap"><h1>PSYCHO 10301130 MOMENTUM ENGINE</h1><div class="sub">Secondary engine • 1-minute scan • 5M authority • local-range breakout → acceptance → confluence → option execution</div><div class="banner"><div class="hero">{{session.status}}</div><div class="sub">{{session.detail}}</div><div class="small">Last scan: {{scan_time}} • Automatic scan every 60 seconds • Signal authority: completed 5M candles only</div></div><div class="grid">{% for x in items %}<div class="card"><div class="title">{{x.name}}</div><div class="status {{x.cls}}">{{x.status}}</div><div class="row"><span class="label">Research Score</span><span class="value">{{x.score}}/100</span></div><div class="row"><span class="label">Setup Detected</span><span class="value">{{x.detected}}</span></div><div class="row"><span class="label">Direction</span><span class="value">{{x.direction}}</span></div><div class="section">10301130 PATTERN</div><div class="row"><span class="label">Local Range</span><span class="value">{{x.range}}</span></div><div class="row"><span class="label">Range High / Low</span><span class="value">{{x.rh}} / {{x.rl}}</span></div><div class="row"><span class="label">Breakout</span><span class="value">{{x.breakout}}</span></div><div class="row"><span class="label">Acceptance</span><span class="value">{{x.acceptance}}</span></div><div class="row"><span class="label">Trigger</span><span class="value">{{x.trigger}}</span></div><div class="section">CONFIRMATION</div><div class="row"><span class="label">VWAP</span><span class="value">{{x.vwap}}</span></div><div class="row"><span class="label">5M Trend</span><span class="value">{{x.t5}}</span></div><div class="row"><span class="label">15M Trend</span><span class="value">{{x.t15}}</span></div><div class="row"><span class="label">Option Quality</span><span class="value">{{x.opt_score}}/100</span></div><div class="section">EXECUTION</div><div class="row"><span class="label">Contract</span><span class="value">{{x.contract}}</span></div><div class="row"><span class="label">Entry</span><span class="value">{{x.entry}}</span></div><div class="row"><span class="label">Thesis Failure / SL</span><span class="value">{{x.thesis_failure}} / {{x.sl}}</span></div><div class="row"><span class="label">TP1</span><span class="value">{{x.tp1}}</span></div><div class="row"><span class="label">TP2</span><span class="value">{{x.tp2}}</span></div><div class="row"><span class="label">Underlying R</span><span class="value">{{x.risk}}</span></div><div class="row"><span class="label">Delta</span><span class="value">{{x.delta}}</span></div><div class="small">{{x.reason}}</div><div class="footer">Research score is not a calibrated win probability. No orders are placed. 10301130 is secondary to 9301030.</div></div>{% endfor %}</div></div></body></html>'''

def now(): return datetime.now(IST)
def fmt(v,d=2): return '—' if v is None else f'{v:,.{d}f}'
def session_state(t):
    if t.weekday()>=5:return {'status':'MARKET CLOSED — WEEKEND','detail':'No scanning.'}
    if t.time()<OPEN:return {'status':'MARKET CLOSED — PRE-OPEN','detail':'Waiting for 09:15 IST.'}
    if t.time()<WINDOW_START:return {'status':'MARKET OPEN — WAITING FOR 10:30','detail':'Building context; 10301130 is not eligible yet.'}
    if t.time()<=WINDOW_END:return {'status':'MARKET OPEN — 10301130 SCANNING','detail':'Only the completed 5M pattern can trigger.'}
    if t.time()<=CLOSE:return {'status':'WINDOW CLOSED — NO NEW 10301130 SIGNAL','detail':'The 10:30–11:30 window has ended.'}
    return {'status':'MARKET CLOSED — SESSION COMPLETE','detail':'Scanning ended at 15:40 IST.'}

def get_json(path):
    req=urllib.request.Request(BRIDGE+path,headers={'Accept':'application/json'})
    with urllib.request.urlopen(req,timeout=20) as r:return json.loads(r.read().decode())

def candles(obj):
    out=[]
    def walk(v):
        if isinstance(v,dict):
            if all(k in v for k in ('open','high','low','close')):
                try:
                    ts=v.get('timestamp') or v.get('time') or v.get('datetime')
                    if ts is not None:
                        d=datetime.fromisoformat(ts.replace('Z','+00:00')).astimezone(IST) if isinstance(ts,str) and not ts.isdigit() else datetime.fromtimestamp(int(ts),IST)
                        out.append({'dt':d,'open':float(v['open']),'high':float(v['high']),'low':float(v['low']),'close':float(v['close']),'volume':float(v.get('volume') or 0)})
                except Exception: pass
            for z in v.values(): walk(z)
        elif isinstance(v,list):
            for z in v: walk(z)
    walk(obj);return sorted({x['dt']:x for x in out}.values(),key=lambda x:x['dt'])

def ema(vals,n=9):
    if not vals:return None
    e=vals[0];a=2/(n+1)
    for x in vals[1:]:e=a*x+(1-a)*e
    return e

def vwap(c):
    pv=vol=0
    for x in c:
        q=(x['high']+x['low']+x['close'])/3;pv+=q*x['volume'];vol+=x['volume']
    return pv/vol if vol else None

def idle(name,reason=None):
    return {'name':name,'score':0,'detected':False,'direction':'—','status':'⚪ WAITING','cls':'blue','range':'10:30–10:40','rh':'—','rl':'—','breakout':'—','acceptance':'—','trigger':'—','vwap':'—','t5':'—','t15':'—','opt_score':0,'contract':'—','entry':'—','thesis_failure':'—','sl':'—','tp1':'—','tp2':'—','risk':'—','delta':'—','reason':reason or 'Waiting for the completed 10:30–11:30 historical sequence.'}

def option_rows(raw):
    strikes=((raw.get('option_chain') or {}).get('strikes') or {});out=[]
    for k,s in strikes.items():
        try:st=float(s.get('strike',k))
        except:continue
        for side in ('CE','PE'):
            l=s.get(side) or {};p=l.get('last_price')
            if p is None:continue
            try:p=float(p)
            except:continue
            g=l.get('greeks') or {}
            def n(key):
                try:return float(l[key]) if l.get(key) is not None else None
                except:return None
            try:delta=float(g.get('delta')) if g.get('delta') is not None else None
            except:delta=None
            out.append({'strike':st,'side':side,'ltp':p,'oi':n('oi'),'oi_change':n('oi_change'),'volume':n('volume'),'bid':n('top_bid_price'),'ask':n('top_ask_price'),'delta':delta})
    return out

def choose_option(raw,under,direction,risk):
    side='CE' if direction=='LONG' else 'PE';rows=[o for o in option_rows(raw) if o['side']==side];rank=[]
    for o in rows:
        s=0;d=abs(o['strike']-under)
        if d<=risk*0.75:s+=25
        elif d<=risk*1.5:s+=15
        else:s+=5
        if o['bid'] is not None and o['ask'] is not None and o['ltp']>0:
            sp=(o['ask']-o['bid'])/o['ltp'];s+=25 if sp<=.01 else 18 if sp<=.02 else 8 if sp<=.04 else 0
        if o['delta'] is not None:
            ad=abs(o['delta']);s+=25 if .40<=ad<=.65 else 15 if .30<=ad<=.75 else 5
        if o['volume'] and o['volume']>0:s+=10
        if o['oi'] and o['oi']>0:s+=10
        rank.append((min(100,s),o))
    return sorted(rank,key=lambda x:x[0],reverse=True)[0] if rank else (0,None)

def detect(c5,t):
    cs=[c for c in c5 if c['dt'].date()==t.date() and c['dt'].time()<=t.time()]
    local=[c for c in cs if dt_time(10,30)<=c['dt'].time()<RANGE_END]
    post=[c for c in cs if c['dt'].time()>=RANGE_END and c['dt'].time()<=WINDOW_END]
    if len(local)<2:return None
    rh=max(c['high'] for c in local);rl=min(c['low'] for c in local)
    if len(post)<2:return {'rh':rh,'rl':rl}
    for i in range(len(post)-1):
        b=post[i];a=post[i+1]
        if b['close']>rh and a['close']>rh:
            return {'detected':True,'direction':'LONG','rh':rh,'rl':rl,'breakout':b,'acceptance':a}
        if b['close']<rl and a['close']<rl:
            return {'detected':True,'direction':'SHORT','rh':rh,'rl':rl,'breakout':b,'acceptance':a}
    return {'rh':rh,'rl':rl}

def analyze(name,path):
    t=now();raw=get_json(path);market=raw.get('market') or raw;c5=candles((market.get('timeframes') or {}).get('5M') or market);c15=candles((market.get('timeframes') or {}).get('15M') or [])
    r=idle(name);d=detect(c5,t)
    if not d:return r
    r['rh']=fmt(d.get('rh'));r['rl']=fmt(d.get('rl'))
    if not d.get('detected'):
        r['reason']='No completed breakout + next-5M acceptance yet.';return r
    direction=d['direction'];b=d['breakout'];a=d['acceptance'];under=a['close'];r['direction']=direction;r['breakout']=(('ABOVE RANGE HIGH @ '+fmt(b['close'])) if direction=='LONG' else ('BELOW RANGE LOW @ '+fmt(b['close'])));r['acceptance']='CONFIRMED — next 5M closed outside range';r['trigger']=a['dt'].strftime('%H:%M IST')
    invalid=d['rh'] if direction=='LONG' else d['rl'];risk=under-invalid if direction=='LONG' else invalid-under
    if risk<=0:return r
    r['thesis_failure']=fmt(invalid);r['risk']=fmt(risk)
    closes5=[x['close'] for x in c5 if x['dt']<=a['dt']];closes15=[x['close'] for x in c15 if x['dt']<=a['dt']]
    e5=ema(closes5);e15=ema(closes15);r['t5']='BULLISH' if under>e5 else 'BEARISH' if e5 else '—';r['t15']='BULLISH' if under>e15 else 'BEARISH' if e15 else '—';vw=vwap([x for x in c5 if x['dt'].date()==t.date() and x['dt']<=a['dt']]);r['vwap']=fmt(vw)
    score=40
    if (direction=='LONG' and under>e5) or (direction=='SHORT' and under<e5):score+=15
    if (direction=='LONG' and e15 and under>e15) or (direction=='SHORT' and e15 and under<e15):score+=15
    if (direction=='LONG' and vw and under>vw) or (direction=='SHORT' and vw and under<vw):score+=15
    opt_score,opt=choose_option(raw,under,direction,risk);r['opt_score']=opt_score;score+=round(opt_score*.15);r['score']=min(99,score)
    # Research-derived targets: 10:30–11:30 did not support 2R reliably. Use 0.5R primary and 1R secondary.
    tp1=under+(0.5*risk if direction=='LONG' else -0.5*risk);tp2=under+(risk if direction=='LONG' else -risk);r['tp1']=fmt(tp1);r['tp2']=fmt(tp2)
    if not opt or opt_score<75:
        r['status']='🟡 PATTERN DETECTED — OPTION FILTER FAILED';r['cls']='yellow';r['reason']='Historical pattern matched, but option execution quality is below the secondary-engine gate. No trade.';return r
    # Secondary engine requires VWAP and 5M alignment; unlike 9301030, the raw pattern is not enough.
    aligned=(direction=='LONG' and under>e5 and (vw is None or under>vw)) or (direction=='SHORT' and under<e5 and (vw is None or under<vw))
    if not aligned:
        r['status']='🟡 PATTERN DETECTED — CONFLUENCE FAILED';r['cls']='yellow';r['reason']='Frequent pattern detected, but directional confirmation is insufficient. No trade.';return r
    entry=opt['ask'] if opt['ask'] and opt['ask']>0 else opt['ltp'];delta=abs(opt['delta']) if opt['delta'] not in (None,0) else .50
    r['contract']=f"{int(opt['strike']):,} {opt['side']}";r['entry']=fmt(entry);r['delta']=fmt(opt['delta'],3);r['sl']=fmt(max(.05,entry-risk*delta));r['detected']=True;r['status']='🟢 10301130 TRADE DETECTED';r['cls']='green';r['reason']='Secondary pattern + 5M/VWAP alignment + liquid option confirmation. Structural SL = local-range thesis failure. Research target model: TP1 0.5R, TP2 1R; no 2R assumption.'
    return r

def scan():
    t=now()
    if t.weekday()>=5 or t.time()<WINDOW_START or t.time()>WINDOW_END:
        with LOCK:STATE['scan_time']=t.strftime('%d %b %Y %H:%M:%S IST');STATE['session']=session_state(t);STATE['items']=[idle('NIFTY'),idle('BANK NIFTY')]
        return
    items=[]
    for name,path in [('NIFTY','/nifty-live'),('BANK NIFTY','/banknifty-live')]:
        try:
            x=analyze(name,path)
            if TRIGGERED[name]:x['status']='🔒 SIGNAL ALREADY FIRED TODAY';x['cls']='green';x['reason']='One-signal-per-instrument lock is active.'
            elif x['detected']:TRIGGERED[name]=True
            items.append(x)
        except Exception as e:items.append(idle(name,f'Bridge unavailable: {type(e).__name__}'))
    with LOCK:STATE['scan_time']=t.strftime('%d %b %Y %H:%M:%S IST');STATE['session']=session_state(t);STATE['items']=items

def worker():
    global TRIGGERED
    day=None
    while True:
        t=now()
        if day!=t.date():TRIGGERED={'NIFTY':False,'BANK NIFTY':False};day=t.date()
        try:scan()
        except Exception:pass
        time.sleep(SCAN_SECONDS)

@app.route('/')
def home():
    with LOCK:s=dict(STATE)
    return render_template_string(HTML,session=s.get('session') or session_state(now()),scan_time=s.get('scan_time') or '—',items=s.get('items') or [idle('NIFTY'),idle('BANK NIFTY')])
@app.route('/api/state')
def api_state():
    with LOCK:return jsonify(STATE)
@app.route('/health')
def health():return jsonify({'ok':True,'engine':'10301130','time':now().isoformat()})
if __name__=='__main__':
    threading.Thread(target=worker,daemon=True).start();app.run(host='0.0.0.0',port=10000)
