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
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<title>PSYCHO // BANKNIFTY SIGNAL TERMINAL</title>
<style>
:root{--bg:#05080d;--panel:#0b1119;--panel2:#0e1621;--line:#202c39;--text:#edf2f7;--muted:#7e8b99;--green:#35e38f;--red:#ff5268;--amber:#ffc34d;--blue:#63b3ff}
*{box-sizing:border-box}html,body{margin:0;width:100%;min-height:100%;background:var(--bg);color:var(--text);font-family:Inter,Segoe UI,Arial,sans-serif}body{overflow-x:hidden}.wrap{width:min(1400px,100%);margin:auto;padding:16px}.top{display:flex;align-items:center;justify-content:space-between;gap:16px;border-bottom:1px solid var(--line);padding:4px 2px 16px;margin-bottom:14px}.brand{font-size:20px;font-weight:950;letter-spacing:1.8px}.sub{margin-top:5px;color:var(--muted);font-size:10px;letter-spacing:1.5px}.live{white-space:nowrap;font-size:11px;font-weight:900;letter-spacing:1px}.dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--green);box-shadow:0 0 12px var(--green);margin-right:7px}.grid{display:grid;grid-template-columns:minmax(0,1.35fr) minmax(0,1fr);gap:12px}.card{min-width:0;background:linear-gradient(180deg,var(--panel2),var(--panel));border:1px solid var(--line);border-radius:10px;padding:15px;overflow:hidden}.hero{min-height:250px;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;border-color:#303c4a}.eyebrow,.title{font-size:10px;color:#9ba9b8;font-weight:900;letter-spacing:1.8px}.signal{font-family:Consolas,Monaco,monospace;font-size:clamp(38px,6vw,68px);font-weight:950;letter-spacing:2px;margin:15px 0 8px;line-height:1}.signal.wait{color:var(--amber)}.signal.buy{color:var(--green)}.signal.sell{color:var(--red)}.signal.error{color:var(--amber)}.reason{font-size:13px;color:#b7c1cc;max-width:90%}.meta{display:flex;flex-wrap:wrap;justify-content:center;gap:7px;margin-top:18px}.pill{max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;border:1px solid var(--line);background:#080d13;padding:6px 9px;border-radius:999px;color:#9eabb9;font:10px Consolas,monospace}.title{margin-bottom:11px}.rows{width:100%}.row{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.7fr);gap:12px;padding:9px 0;border-bottom:1px solid #19232e;font:12px Consolas,Monaco,monospace}.row:last-child{border-bottom:0}.key{color:var(--muted);text-transform:uppercase;overflow:hidden;text-overflow:ellipsis}.value{text-align:right;font-weight:800;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.ok{color:var(--green)}.bad{color:var(--red)}.warn{color:var(--amber)}.wide{grid-column:1/-1}.statusbar{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:9px}.status{min-width:0;background:#080d13;border:1px solid var(--line);border-radius:8px;padding:11px}.status b{display:block;font:11px Consolas,monospace;margin-bottom:5px}.status span{color:var(--muted);font-size:10px}.footer{text-align:center;color:#566271;font:9px Consolas,monospace;letter-spacing:1px;padding:15px 0 2px}.pulse{animation:pulse 1.4s infinite}@keyframes pulse{50%{opacity:.55}}
@media(max-width:850px){.wrap{padding:10px}.top{align-items:flex-start}.brand{font-size:16px}.sub{font-size:8px}.grid{grid-template-columns:1fr}.wide{grid-column:auto}.statusbar{grid-template-columns:1fr}.hero{min-height:220px}.signal{font-size:46px}.row{grid-template-columns:minmax(0,1fr) minmax(0,1.25fr)}}
@media(max-width:480px){.top{display:block}.live{margin-top:10px}.card{padding:12px}.signal{font-size:38px}.row{font-size:11px}.meta{justify-content:flex-start}}
</style>
</head>
<body><main class="wrap">
<header class="top"><div><div class="brand">PSYCHO // TRADING TERMINAL</div><div class="sub">BANKNIFTY • V6R1 • FILTERED ORB • SIGNAL ONLY</div></div><div class="live"><span class="dot" id="dot"></span><span id="liveText">CONNECTING</span></div></header>
<section class="grid">
<div class="card hero"><div class="eyebrow">BANKNIFTY PRIMARY DECISION</div><div id="signal" class="signal wait pulse">CONNECTING</div><div id="reason" class="reason">Reading live signal state…</div><div class="meta"><span class="pill" id="strategy">STRATEGY —</span><span class="pill" id="updated">UPDATED —</span></div></div>
<div class="card"><div class="title">MARKET STATE</div><div class="rows" id="market"><div class="row"><span class="key">STATUS</span><b class="value">CONNECTING</b></div></div></div>
<div class="card"><div class="title">SIGNAL ENGINE</div><div class="rows" id="engine"></div></div>
<div class="card"><div class="title">NIFTY CONTEXT</div><div class="rows" id="nifty"></div></div>
<div class="card wide"><div class="title">SYSTEM STATUS</div><div class="statusbar"><div class="status"><b class="ok">● DHAN LIVE</b><span>Market data source</span></div><div class="status"><b class="ok">● V6R1 LIVE</b><span>Signal computation</span></div><div class="status"><b class="ok">● NEMOTRON REMOVED</b><span>Critical path is deterministic</span></div></div></div>
</section><div class="footer">PSYCHO SIGNAL ENGINE • AUTO REFRESH 5s • NO SIGNAL IS A VALID STATE</div>
</main>
<script>
const esc=v=>String(v??'—').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const label=k=>String(k).replaceAll('_',' ').toUpperCase();
function rows(obj){return Object.entries(obj||{}).map(([k,v])=>`<div class="row"><span class="key">${esc(label(k))}</span><b class="value">${esc(typeof v==='object'?JSON.stringify(v):v)}</b></div>`).join('')}
function stateClass(s){s=String(s||'');if(/CALL|BUY|LONG/i.test(s))return'buy';if(/PUT|SELL|SHORT/i.test(s))return'sell';if(/ERROR/i.test(s))return'error';return'wait'}
async function refresh(){
 try{
  const r=await fetch('/signal.json?ts='+Date.now(),{cache:'no-store'});if(!r.ok)throw new Error('HTTP '+r.status);
  const d=await r.json(),b=d.results?.BANKNIFTY||{},n=d.results?.NIFTY||{};
  const s=String(b.status||d.status||'WAITING');
  const el=document.getElementById('signal');el.textContent=s;el.className='signal '+stateClass(s);
  let reason=b.reason||b.detail||'';if(!reason&&s==='NO_SIGNAL_TIME_WINDOW')reason='ENTRY WINDOW CLOSED — NO TRADE';if(!reason)reason='Live engine state';
  document.getElementById('reason').textContent=reason;
  document.getElementById('strategy').textContent='STRATEGY '+(d.strategy||'FILTERED_ORB');
  document.getElementById('updated').textContent='UPDATED '+(d.generated_at||'—');
  document.getElementById('market').innerHTML=rows(b)||'<div class="row"><span class="key">STATUS</span><b class="value">NO DATA</b></div>';
  document.getElementById('nifty').innerHTML=rows(n)||'<div class="row"><span class="key">STATUS</span><b class="value">NO DATA</b></div>';
  document.getElementById('engine').innerHTML=rows({STATUS:d.status||'LIVE',SOURCE:d.source||'PSYCHO SIGNAL ENGINE',BANKNIFTY:b.status||'—',NIFTY:n.status||'—',UPDATED:d.generated_at||'—'});
  document.getElementById('liveText').textContent=d.status==='LIVE'?'LIVE':'WAITING';document.getElementById('dot').style.background='var(--green)';
 }catch(e){
  const el=document.getElementById('signal');el.textContent='DATA ERROR';el.className='signal error';document.getElementById('reason').textContent='Live signal feed temporarily unavailable';document.getElementById('liveText').textContent='DATA ERROR';document.getElementById('dot').style.background='var(--red)';
 }
}
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
