from bridge import app, live_refresh_worker
from flask import Response
import threading
import os


PSYCHO_HTML = r'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>PSYCHO // PHASE 2</title>
<style>
:root{--bg:#050505;--panel:#0b0b0d;--line:#24242a;--text:#f2f2f4;--muted:#8b8b95;--red:#ff1744;--red2:#ff4d6d;--green:#00e676;--amber:#ffb300;--cyan:#00e5ff}
*{box-sizing:border-box}html,body{margin:0;background:radial-gradient(circle at 50% -10%,#26000b 0,#09090b 35%,#050505 72%);color:var(--text);font-family:Inter,ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif}body{min-height:100vh}.wrap{max-width:1280px;margin:auto;padding:24px}.top{display:flex;justify-content:space-between;align-items:center;gap:16px;border-bottom:1px solid var(--line);padding:8px 0 22px}.brand{letter-spacing:.18em;font-weight:900;font-size:20px}.brand span{color:var(--red)}.sub{font-size:11px;color:var(--muted);letter-spacing:.22em;margin-top:6px}.status{display:flex;align-items:center;gap:9px;border:1px solid var(--line);background:#0a0a0c;padding:10px 14px;border-radius:999px;font-size:11px;letter-spacing:.12em}.dot{width:8px;height:8px;border-radius:50%;background:var(--green);box-shadow:0 0 14px var(--green)}.hero{padding:42px 0 28px}.eyebrow{font-size:11px;letter-spacing:.3em;color:var(--red2);font-weight:800}.hero h1{font-size:clamp(38px,7vw,78px);line-height:.95;margin:12px 0;font-weight:950;letter-spacing:-.05em}.hero h1 em{font-style:normal;color:transparent;-webkit-text-stroke:1px #777}.hero p{max-width:720px;color:#a8a8b2;font-size:14px;line-height:1.7}.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}.card{background:linear-gradient(180deg,#0d0d10,#08080a);border:1px solid var(--line);border-radius:14px;padding:18px;box-shadow:0 14px 40px #0008}.label{font-size:10px;letter-spacing:.18em;color:var(--muted);text-transform:uppercase}.value{font-size:24px;font-weight:900;margin-top:9px}.small{font-size:11px;color:var(--muted);margin-top:6px}.market-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:14px}.market{position:relative;overflow:hidden}.market:before{content:"";position:absolute;left:0;top:0;bottom:0;width:3px;background:var(--red);box-shadow:0 0 18px var(--red)}.market-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:18px}.ticker{font-size:22px;font-weight:950;letter-spacing:.04em}.pill{font-size:9px;padding:6px 8px;border-radius:999px;background:#16161a;color:#aaa;letter-spacing:.12em}.price{font-size:40px;font-weight:950;letter-spacing:-.04em}.row{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-top:16px}.mini{background:#08080a;border:1px solid #1d1d22;border-radius:10px;padding:12px}.mini b{display:block;font-size:16px;margin-top:5px}.section{margin-top:28px}.section-title{font-size:11px;letter-spacing:.25em;color:#aaa;margin-bottom:12px}.layers{display:grid;grid-template-columns:repeat(6,1fr);gap:8px}.layer{padding:15px 10px;text-align:center;border:1px solid var(--line);border-radius:10px;background:#09090b}.layer strong{display:block;font-size:17px}.layer span{font-size:9px;color:var(--muted);letter-spacing:.12em}.footer{margin-top:30px;padding:20px 0;border-top:1px solid var(--line);display:flex;justify-content:space-between;color:#666;font-size:10px;letter-spacing:.12em}.refresh{color:var(--cyan)}@media(max-width:850px){.grid{grid-template-columns:repeat(2,1fr)}.market-grid{grid-template-columns:1fr}.layers{grid-template-columns:repeat(3,1fr)}}@media(max-width:520px){.wrap{padding:15px}.top{align-items:flex-start}.status{font-size:9px}.hero{padding:30px 0 20px}.grid{grid-template-columns:1fr 1fr}.value{font-size:20px}.price{font-size:31px}.layers{grid-template-columns:repeat(2,1fr)}.footer{display:block;line-height:2}}
</style>
</head>
<body>
<div class="wrap">
<header class="top"><div><div class="brand">PSYCHO <span>//</span> MARKET BRIDGE</div><div class="sub">PHASE 2 — MARKET STRUCTURE ENGINE</div></div><div class="status"><span class="dot"></span><span id="bridge">BRIDGE ONLINE</span></div></header>
<section class="hero"><div class="eyebrow">LIVE MARKET INFRASTRUCTURE</div><h1>READ THE<br><em>MARKET.</em></h1><p>A clean command surface for the PSYCHO TRADING Phase 2 data layer. DHAN feeds the bridge; the bridge isolates the current session and exposes structured market, futures and option-chain data for downstream analysis.</p></section>
<section class="grid">
<div class="card"><div class="label">Market state</div><div class="value" id="market">—</div><div class="small" id="reason">—</div></div>
<div class="card"><div class="label">Live window</div><div class="value">09:15—15:40</div><div class="small">IST • weekdays</div></div>
<div class="card"><div class="label">Refresh target</div><div class="value">60 SEC</div><div class="small">automatic during session</div></div>
<div class="card"><div class="label">Data source</div><div class="value">DHAN</div><div class="small">server-side acquisition</div></div>
</section>
<section class="section"><div class="section-title">UNDERLYING COMMAND BOARD</div><div class="market-grid"><div class="card market"><div class="market-head"><div class="ticker">NIFTY</div><div class="pill" id="nstatus">WAITING</div></div><div class="price" id="nprice">—</div><div class="small" id="ngap">GAP —</div><div class="row"><div class="mini"><div class="label">1M</div><b id="n1">0</b></div><div class="mini"><div class="label">5M</div><b id="n5">0</b></div><div class="mini"><div class="label">15M</div><b id="n15">0</b></div></div></div><div class="card market"><div class="market-head"><div class="ticker">BANK NIFTY</div><div class="pill" id="bstatus">WAITING</div></div><div class="price" id="bprice">—</div><div class="small" id="bgap">GAP —</div><div class="row"><div class="mini"><div class="label">1M</div><b id="b1">0</b></div><div class="mini"><div class="label">5M</div><b id="b5">0</b></div><div class="mini"><div class="label">15M</div><b id="b15">0</b></div></div></div></div></section>
<section class="section"><div class="section-title">STRUCTURE LAYERS</div><div class="layers"><div class="layer"><strong>1M</strong><span>MICRO</span></div><div class="layer"><strong>5M</strong><span>EXECUTION</span></div><div class="layer"><strong>15M</strong><span>STRUCTURE</span></div><div class="layer"><strong>1H</strong><span>CONTEXT</span></div><div class="layer"><strong>1D</strong><span>DAILY</span></div><div class="layer"><strong>1W</strong><span>MACRO</span></div></div></section>
<section class="section"><div class="grid"><div class="card"><div class="label">Derivatives</div><div class="value">FUTURES</div><div class="small">Nearest index future • 5M OHLCV/OI • live quote • 5-level depth</div></div><div class="card"><div class="label">Options</div><div class="value">ATM ± 10</div><div class="small">Nearest expiry • CE/PE • OI • volume • IV • Greeks • bid/ask</div></div><div class="card"><div class="label">Session policy</div><div class="value">ISOLATED</div><div class="small">Current-day intraday data never mixes with previous-day intraday data</div></div><div class="card"><div class="label">Last server check</div><div class="value refresh" id="checked">—</div><div class="small">Auto-refreshing dashboard</div></div></div></section>
<footer class="footer"><span>PSYCHO TRADING // PHASE 2</span><span>DHAN → BRIDGE → STRUCTURE ENGINE</span></footer>
</div>
<script>
const $=id=>document.getElementById(id);const fmt=v=>v==null?'—':Number(v).toLocaleString('en-IN',{maximumFractionDigits:2});
async function pull(){try{const s=await fetch('/bridge-status',{cache:'no-store'}).then(r=>r.json());$('bridge').textContent=s.server==='ONLINE'?'BRIDGE ONLINE':'BRIDGE CHECK';$('market').textContent=s.market?.status||'—';$('reason').textContent=s.market?.reason||'—';$('checked').textContent=new Date().toLocaleTimeString('en-IN',{hour12:false});const load=async(k,p)=>{try{const d=await fetch(p,{cache:'no-store'}).then(r=>r.json());const m=d.market||{};const c=m.current_session||{};const t=m.timeframes||{};$(k+'status').textContent=d.status||'WAITING';$(k+'price').textContent=fmt(c.last_price);$(k+'gap').textContent='GAP '+(c.gap?.type||'—')+' '+(c.gap?.points??'');$(k+'1').textContent=(t['1M']||[]).length;$(k+'5').textContent=(t['5M']||[]).length;$(k+'15').textContent=(t['15M']||[]).length}catch(e){}};await load('n','/nifty-live');await load('b','/banknifty-live')}catch(e){$('bridge').textContent='BRIDGE UNREACHABLE'}}pull();setInterval(pull,15000);
</script>
</body></html>'''


@app.route("/")
def psycho_home():
    return Response(PSYCHO_HTML, content_type="text/html; charset=utf-8")


if __name__ == "__main__":
    threading.Thread(target=live_refresh_worker, daemon=True, name="psycho-live-refresh").start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, threaded=True, use_reloader=False)
