import json, os, threading
from flask import Flask, jsonify, Response
import signal_engine

app = Flask(__name__)


def worker():
    signal_engine.run_loop()


TERMINAL_HTML = r'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>PSYCHO // BANKNIFTY SIGNAL TERMINAL</title>
<style>
:root{--bg:#070a0f;--panel:#0d121a;--panel2:#111823;--line:#26313e;--text:#e8edf3;--muted:#7f8b99;--green:#42e39a;--red:#ff5c70;--amber:#ffc857;--blue:#66b3ff;--shadow:0 14px 40px rgba(0,0,0,.32)}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 50% -20%,#172231 0,#070a0f 42%);color:var(--text);font-family:Inter,Segoe UI,Arial,sans-serif;min-height:100vh}.wrap{max-width:1320px;margin:auto;padding:22px}.top{display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid var(--line);padding:4px 0 18px;margin-bottom:18px}.brand{font-weight:900;letter-spacing:2px;font-size:20px}.sub{font-size:11px;color:var(--muted);letter-spacing:1.5px;margin-top:4px}.live{display:flex;align-items:center;gap:8px;font-size:12px;font-weight:800;letter-spacing:1px}.dot{width:9px;height:9px;border-radius:50%;background:var(--green);box-shadow:0 0 14px var(--green)}.grid{display:grid;grid-template-columns:1.45fr 1fr;gap:16px}.card{background:linear-gradient(180deg,rgba(17,24,35,.96),rgba(11,16,24,.96));border:1px solid var(--line);border-radius:12px;box-shadow:var(--shadow);padding:18px}.hero{min-height:300px;display:flex;flex-direction:column;justify-content:center;align-items:center;text-align:center}.eyebrow{font-size:11px;color:var(--muted);letter-spacing:2px;font-weight:800}.signal{font-size:clamp(38px,7vw,76px);font-weight:950;letter-spacing:3px;margin:18px 0 10px}.signal.wait{color:var(--amber)}.signal.buy{color:var(--green)}.signal.sell{color:var(--red)}.reason{color:var(--muted);font-size:13px}.meta{display:flex;gap:10px;flex-wrap:wrap;justify-content:center;margin-top:22px}.pill{border:1px solid var(--line);background:#0a0f16;padding:7px 11px;border-radius:999px;font-size:11px;color:#b8c2ce}.title{font-size:12px;letter-spacing:1.5px;color:#9ba7b5;font-weight:900;margin-bottom:14px}.rows{display:grid;gap:0}.row{display:flex;justify-content:space-between;gap:18px;padding:12px 0;border-bottom:1px solid #1e2732;font-size:13px}.row:last-child{border-bottom:0}.value{font-weight:800;text-align:right}.ok{color:var(--green)}.bad{color:var(--red)}.warn{color:var(--amber)}.muted{color:var(--muted)}.wide{grid-column:1/-1}.statusbar{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}.status{background:#0a0f16;border:1px solid var(--line);border-radius:9px;padding:12px}.status b{display:block;font-size:12px;margin-bottom:5px}.status span{font-size:11px;color:var(--muted)}.footer{color:#667281;font-size:10px;letter-spacing:1px;text-align:center;padding:18px 0 4px}@media(max-width:800px){.grid{grid-template-columns:1fr}.wide{grid-column:auto}.statusbar{grid-template-columns:1fr}.wrap{padding:12px}.top{align-items:flex-start}.signal{font-size:46px}}
</style>
</head>
<body><main class="wrap">
<header class="top"><div><div class="brand">PSYCHO // TRADING TERMINAL</div><div class="sub">BANKNIFTY • V6R1 • FILTERED ORB • SIGNAL ONLY</div></div><div class="live"><span class="dot"></span><span id="liveText">LIVE</span></div></header>
<section class="grid">
<div class="card hero"><div class="eyebrow">BANKNIFTY PRIMARY DECISION</div><div id="signal" class="signal wait">LOADING</div><div id="reason" class="reason">Reading live signal state…</div><div class="meta"><span class="pill" id="strategy">STRATEGY —</span><span class="pill" id="updated">UPDATED —</span></div></div>
<div class="card"><div class="title">MARKET STATE</div><div class="rows" id="market"><div class="row"><span class="muted">BANKNIFTY</span><b class="value">—</b></div><div class="row"><span class="muted">STATUS</span><b class="value">LOADING</b></div></div></div>
<div class="card"><div class="title">SIGNAL ENGINE</div><div class="rows" id="engine"></div></div>
<div class="card"><div class="title">NIFTY CONTEXT</div><div class="rows" id="nifty"></div></div>
<div class="card wide"><div class="title">SYSTEM STATUS</div><div class="statusbar"><div class="status"><b class="ok">● DHAN LIVE</b><span>Market data source</span></div><div class="status"><b class="ok">● V6R1 LIVE</b><span>Signal computation</span></div><div class="status"><b class="ok">● NEMOTRON REMOVED</b><span>Critical path is deterministic</span></div></div></div>
</section><div class="footer">PSYCHO SIGNAL ENGINE • AUTO REFRESH 5s • NO SIGNAL IS A VALID STATE</div>
</main>
<script>
const esc=v=>String(v??'—').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function rows(obj){return Object.entries(obj||{}).map(([k,v])=>`<div class="row"><span class="muted">${esc(k.replaceAll('_',' ').toUpperCase())}</span><b class="value">${esc(typeof v==='object'?JSON.stringify(v):v)}</b></div>`).join('')}
async function refresh(){try{const r=await fetch('/signal.json?ts='+Date.now(),{cache:'no-store'});const d=await r.json();const b=d.results?.BANKNIFTY||{};const n=d.results?.NIFTY||{};const s=b.status||d.status||'WAITING';const el=document.getElementById('signal');el.textContent=s;el.className='signal '+(s.includes('CALL')||s.includes('BUY')?'buy':s.includes('PUT')||s.includes('SELL')?'sell':'wait');document.getElementById('reason').textContent=b.reason||b.detail||s==='NO_SIGNAL_TIME_WINDOW'?'ENTRY WINDOW CLOSED — NO TRADE':'Live engine state';document.getElementById('strategy').textContent='STRATEGY '+(d.strategy||'FILTERED_ORB');document.getElementById('updated').textContent='UPDATED '+(d.generated_at||'—');document.getElementById('market').innerHTML=rows(b);document.getElementById('nifty').innerHTML=rows(n);document.getElementById('engine').innerHTML=rows({STATUS:d.status||'LIVE',SOURCE:d.source||'PSYCHO SIGNAL ENGINE',BANKNIFTY:b.status||'—',NIFTY:n.status||'—'});document.getElementById('liveText').textContent=d.status==='LIVE'?'LIVE':'WAITING'}catch(e){document.getElementById('liveText').textContent='DATA ERROR';document.getElementById('reason').textContent='Signal feed temporarily unavailable'}}
refresh();setInterval(refresh,5000);
</script></body></html>'''


@app.get('/')
def root():
    return jsonify({'status':'ONLINE','service':'PSYCHO SIGNAL ENGINE','endpoints':['/health','/signal','/signal.json'],'mode':'SIGNAL_ONLY','nemotron':'REMOVED'})


@app.get('/health')
def health():
    return jsonify({'status':'ONLINE','service':'PSYCHO SIGNAL ENGINE','engine':'RUNNING','nemotron_wiring':'REMOVED'})


@app.get('/signal.json')
def signal_json():
    path = signal_engine.OUTPUT_FILE
    try:
        with open(path,'r',encoding='utf-8') as f:
            return jsonify(json.load(f))
    except Exception:
        return jsonify({'status':'WAITING','service':'PSYCHO SIGNAL ENGINE'}),503


@app.get('/signal')
def signal():
    return Response(TERMINAL_HTML,mimetype='text/html')


if __name__ == '__main__':
    threading.Thread(target=worker, daemon=True).start()
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT','10000')))
