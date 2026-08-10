import json, urllib.request
from datetime import datetime, time as T
from flask import request, jsonify, render_template_string

REPLAY_HTML = '''<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><style>body{font-family:Arial;background:#080b12;color:#eef2f7;padding:18px}.wrap{max-width:1300px;margin:auto}.card{background:#111722;border:1px solid #293241;border-radius:16px;padding:16px;margin:12px 0}.row{display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid #242c38;gap:12px}.good{color:#54e39a}.warn{color:#ffd166}.muted{color:#8995a5;font-size:12px}table{width:100%;border-collapse:collapse;font-size:12px}td,th{padding:8px;border-bottom:1px solid #29313d;text-align:left}</style><div class=wrap><div class=card><h1>PSYCHO ENGINE 2 — REPLAY LAB</h1><div class=muted>Walk-forward replay • no future candles • underlying-structure validation</div><p><a href="/" style="color:#6db8ff">← Live Engine</a></p><form><label>Date <input name=date value="{{date}}"></label> <button>Replay</button></form></div>{%if result%}<div class=card><h2>{{result.status}}</h2><div class=muted>{{result.detail}}</div>{%for k,v in [('Session',result.session_date),('Scanned minutes',result.scanned),('Raw setups',result.raw_setups),('Qualified',result.qualified),('Unique episodes',result.episodes),('Direction changes',result.direction_changes),('Lookahead guard',result.lookahead),('Options validation',result.options_validation)]%}<div class=row><span>{{k}}</span><b>{{v}}</b></div>{%endfor%}</div><div class=card><h2>Signal Episodes</h2>{%if result.episodes_rows%}<table><tr><th>Time</th><th>Instrument</th><th>Direction</th><th>Setup</th><th>Score</th><th>Underlying</th><th>Reasons</th></tr>{%for x in result.episodes_rows%}<tr><td>{{x.time}}</td><td>{{x.name}}</td><td>{{x.direction}}</td><td>{{x.setup}}</td><td>{{x.score}}</td><td>{{x.u}}</td><td>{{x.reason}}</td></tr>{%endfor%}</table>{%else%}<div class=muted>No qualifying episodes found.</div>{%endif%}</div><div class=card><h2>Replay Verdict</h2><p>{{result.verdict}}</p></div>{%endif%}</div>'''

def _dt(v):
    try:
        if isinstance(v,(int,float)): return datetime.fromtimestamp(float(v)/1000 if float(v)>1e11 else float(v),IST)
        return datetime.fromisoformat(str(v).replace('Z','+00:00')).astimezone(IST)
    except: return None

def _candles(raw):
    out=[]
    def walk(v):
        if isinstance(v,dict):
            if all(k in v for k in ('open','high','low','close')):
                d=_dt(v.get('timestamp') or v.get('time') or v.get('datetime') or v.get('date'))
                if d:
                    try: out.append({'dt':d,'open':float(v['open']),'high':float(v['high']),'low':float(v['low']),'close':float(v['close']),'volume':float(v.get('volume') or 0)})
                    except: pass
            for z in v.values(): walk(z)
        elif isinstance(v,list):
            for z in v: walk(z)
    walk(raw); return sorted({x['dt']:x for x in out}.values(),key=lambda x:x['dt'])

def _bridge(path):
    req=urllib.request.Request(BR+path,headers={'Accept':'application/json','User-Agent':'PSYCHO-REPLAY-LAB/1.0'})
    with urllib.request.urlopen(req,timeout=20) as r: return json.loads(r.read().decode())

def _prepare(raw,day):
    tfs=raw.get('timeframes') or {}; out={'timeframes':{}}
    for key,val in tfs.items():
        cs=_candles(val)
        if key in ('1M','5M','15M','1H'): cs=[x for x in cs if x['dt'].date()<=day]
        out['timeframes'][key]=cs
    return out

def _one(name,raw,day):
    raw=_prepare(raw,day); c=_candles((raw.get('timeframes') or {}).get('1M',[])); times=[x['dt'] for x in c if x['dt'].date()==day and T(9,15)<=x['dt'].time()<=T(15,40)]
    episodes=[]; raw_n=qual=changes=0; last=None
    for tm in sorted(set(times)):
        det=detect(name,raw,tm)
        if not det: continue
        raw_n+=1
        if det['score']<MIN_SCORE: continue
        qual+=1; key=(det['direction'],det['setup'])
        if key!=last:
            if last and key[0]!=last[0]: changes+=1
            episodes.append({'time':tm.strftime('%H:%M'),'name':name,'direction':det['direction'],'setup':det['setup'],'score':det['score'],'u':fmt(det['u']),'reason':' • '.join(det['why'])}); last=key
    return len(set(times)),raw_n,qual,changes,episodes

def run_replay(day):
    rows=[]; problems=[]
    for name,slug in (('NIFTY','nifty'),('BANK NIFTY','banknifty')):
        try:
            raw=_bridge('/'+slug+'-live'); md=str(raw.get('session_date') or '')[:10]
            if md and md!=day.isoformat(): problems.append(f'{name}: bridge snapshot is {md}, requested {day.isoformat()}'); continue
            rows.append((name,*_one(name,raw,day)))
        except Exception as e: problems.append(f'{name}: {type(e).__name__}: {e}')
    scanned=sum(x[1] for x in rows); raw_n=sum(x[2] for x in rows); qual=sum(x[3] for x in rows); changes=sum(x[4] for x in rows); episodes=[e for x in rows for e in x[5]]
    if not rows: return {'status':'REPLAY UNAVAILABLE','detail':' | '.join(problems) or 'No replay data available.','session_date':day.isoformat(),'scanned':0,'raw_setups':0,'qualified':0,'episodes':0,'direction_changes':0,'lookahead':'GUARDED','options_validation':'NOT RUN — historical option candles required','episodes_rows':[],'verdict':'No result was fabricated. Use the current bridge snapshot date or supply historical candles.'}
    return {'status':'REPLAY COMPLETE','detail':'Each minute was evaluated only with candles available up to that simulated minute.'+(' Partial: '+' | '.join(problems) if problems else ''),'session_date':day.isoformat(),'scanned':scanned,'raw_setups':raw_n,'qualified':qual,'episodes':len(episodes),'direction_changes':changes,'lookahead':'PASS — future intraday candles excluded','options_validation':'NOT RUN — historical option-premium candles are required for honest P&L','episodes_rows':episodes[-100:],'verdict':'This proves the structure detector is replayable without lookahead. It does NOT claim option TP/SL performance until historical option candles are connected.'}

@app.route('/replay')
def replay_page():
    s=request.args.get('date') or now().date().isoformat()
    try: day=datetime.strptime(s,'%Y-%m-%d').date()
    except: day=now().date(); s=day.isoformat()
    return render_template_string(REPLAY_HTML,date=s,result=run_replay(day))

@app.route('/api/replay')
def replay_api():
    s=request.args.get('date') or now().date().isoformat()
    try: day=datetime.strptime(s,'%Y-%m-%d').date()
    except: return jsonify({'error':'date must be YYYY-MM-DD'}),400
    return jsonify(run_replay(day))
