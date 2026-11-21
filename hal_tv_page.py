"""HAL on the TV, the animated "Ember" screen (served by HAL at <base>/tv, shown full screen by the TV app 3.5-nzbcc.44+).

The TV app lays this page over its own HAL screen and hands it the remote's keys (halKey) and HAL's voice
(halSay / halVoice, from the TV's text-to-speech). Without the app (a browser) the page reads HAL's lines itself,
silently. Data: <base>/api/brief?from=tv every 10 s, <base>/api/history?range=1h every minute, <base>/api/tv/details
every minute. The access code comes in the address's #k=... (never sent to the server in the URL)."""

TV_PAGE = r"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=960,height=540">
<title>HAL 9000</title>
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=JetBrains+Mono:wght@500;700&display=swap" rel="stylesheet">
<style>
*{box-sizing:border-box;margin:0;padding:0}
html,body{width:960px;height:540px;overflow:hidden;background:#0a0405;color:#fff;font-family:"Plus Jakarta Sans",system-ui,sans-serif;-webkit-font-smoothing:antialiased}
#stage{position:absolute;left:0;top:0;width:960px;height:540px;transform-origin:0 0}
canvas.full{position:absolute;inset:0;width:960px;height:540px}
.vig{position:absolute;inset:0;background:radial-gradient(ellipse at 50% 45%,transparent 55%,rgba(0,0,0,.55));pointer-events:none}
.glass{position:absolute;border-radius:20px;padding:15px 17px;overflow:hidden;
 background:linear-gradient(160deg,rgba(70,22,16,.78),rgba(28,8,8,.72) 60%);border:1px solid rgba(255,255,255,.14);
 box-shadow:0 18px 40px rgba(0,0,0,.45),inset 0 1px 0 rgba(255,255,255,.18)}
.glass.l{box-shadow:0 18px 40px rgba(0,0,0,.45),inset 0 1px 0 rgba(255,255,255,.18),inset -2px 0 0 rgba(255,110,60,.55),12px 0 40px -18px rgba(255,80,30,.7)}
.glass.r{box-shadow:0 18px 40px rgba(0,0,0,.45),inset 0 1px 0 rgba(255,255,255,.18),inset 2px 0 0 rgba(255,110,60,.55),-12px 0 40px -18px rgba(255,80,30,.7)}
.sheen{position:absolute;top:-20%;bottom:-20%;width:90px;left:0;pointer-events:none;background:linear-gradient(100deg,transparent,rgba(255,230,210,.16),transparent)}
.k{font:700 10px "JetBrains Mono",monospace;letter-spacing:.16em;color:rgba(255,220,200,.6);display:flex;justify-content:space-between;align-items:center;gap:8px;white-space:nowrap}
.k b{font-weight:700;letter-spacing:.12em}.ok{color:#7cf5b5}.warning{color:#ffc46b}.critical{color:#ff7a6b}.info{color:#9cc8ff}
.num{font-weight:800;letter-spacing:-.03em;line-height:1}.glow{text-shadow:0 0 18px rgba(255,120,60,.7)}
.mono{font-family:"JetBrains Mono",monospace;font-weight:500}
.row{display:flex;justify-content:space-between;align-items:center;font-size:12.5px;padding:5px 0;border-top:1px solid rgba(255,255,255,.08);gap:10px}
.row:first-child{border-top:0}.row em{font:500 12px "JetBrains Mono",monospace;font-style:normal;color:#ffe7dc;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.row span{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.drv{display:flex;gap:6px;margin-top:6px}.drv div{flex:1;min-width:0}.drv span{font:500 9.5px "JetBrains Mono",monospace;color:rgba(255,230,220,.7);display:flex;justify-content:space-between}
.drv i{display:block;height:5px;border-radius:3px;background:rgba(255,255,255,.12);margin-top:3px;overflow:hidden}.drv i b{display:block;height:100%;width:0;border-radius:3px;background:linear-gradient(90deg,#ff9a3c,#ff4b2b)}
.drv i b.w{background:linear-gradient(90deg,#ffc46b,#ffb020)}
.mon{display:flex;align-items:center;gap:9px;font-size:12.5px;padding:4.5px 0}
.mon .d{width:7px;height:7px;border-radius:50%;background:#7cf5b5;flex:none;box-shadow:0 0 6px rgba(124,245,181,.6)}.mon .d.down{background:#ff6a4d;box-shadow:0 0 6px rgba(255,106,77,.6)}
.mon span{flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.mon svg{width:84px;height:16px;flex:none}.mon em{font:500 11.5px "JetBrains Mono",monospace;font-style:normal;width:44px;text-align:right;flex:none}
.quote{position:absolute;left:332px;width:296px;top:310px;text-align:center;font-size:15px;font-weight:600;font-style:italic;line-height:1.3;color:#ffe9e0;min-height:42px;max-height:60px;overflow:hidden}
.quote.long{font-size:13px}.quote.vlong{font-size:11.5px}
.caret{display:inline-block;width:2px;height:1em;background:#ffb59a;vertical-align:-2px;margin-left:2px}
.wave{position:absolute;left:420px;width:120px;top:372px;height:12px;display:flex;align-items:center;gap:2px;justify-content:center}
.wave i{width:2px;height:2px;border-radius:1px;background:linear-gradient(#ffb347,#ff3a1e);box-shadow:0 0 4px rgba(255,90,40,.6)}
.alert{position:absolute;left:345px;width:270px;top:388px;padding:9px 14px;display:flex;gap:10px;align-items:center;border-radius:14px}
.alert .ic{width:24px;height:24px;border-radius:8px;background:linear-gradient(135deg,#ffc46b,#ff6a3a);display:grid;place-items:center;font-weight:800;font-size:13px;color:#2a0a00;flex:none}
.alert.critical .ic{background:linear-gradient(135deg,#ff8a6b,#e11d48);color:#fff}
.alert b{font-size:12.5px;display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:200px}.alert span{display:block;font-size:10.5px;opacity:.7;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:200px}
.strip{left:26px;right:26px;bottom:34px;height:54px;padding:0;display:flex;align-items:center}
.strip .art{width:150px;height:100%;position:relative;overflow:hidden;flex:none;background:radial-gradient(circle at 40% 50%,#ff7a3a,#7a1208 60%,#1a0504)}
.strip .art svg{position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);width:30px;height:30px}
.strip .art:after{content:"";position:absolute;inset:0;background:linear-gradient(90deg,transparent 40%,rgba(30,8,8,.95))}
.strip .t{flex:1;padding-left:6px;min-width:0}.strip .t b{font-size:14px;display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.strip .t span{display:block;font:500 10.5px "JetBrains Mono",monospace;opacity:.75;margin-top:3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.strip .p{width:260px;margin-right:22px;flex:none}.strip .p .bar{height:6px;border-radius:3px;background:rgba(255,255,255,.14);overflow:hidden}
.strip .p .bar i{display:block;height:100%;width:0;background:linear-gradient(90deg,#ff6a3a,#ffc46b);box-shadow:0 0 12px #ff7a3a}
.strip .p span{display:flex;justify-content:space-between;font:500 10px "JetBrains Mono",monospace;opacity:.7;margin-top:5px}
.hint{position:absolute;left:0;right:0;bottom:10px;text-align:center;font:500 10px "JetBrains Mono",monospace;letter-spacing:.06em;color:rgba(255,220,210,.55)}
.hint b{color:#fff;border:1px solid rgba(255,255,255,.25);border-radius:5px;padding:0 5px;margin:0 5px 0 16px;font-weight:700}
#clock{position:absolute;right:30px;bottom:12px;font:700 10px "JetBrains Mono",monospace;letter-spacing:.3em;color:rgba(255,225,210,.6)}
#boot{position:absolute;left:0;right:0;top:318px;text-align:center;font:700 13px "JetBrains Mono",monospace;letter-spacing:.5em;color:#ffd2c0;text-shadow:0 0 14px rgba(255,90,40,.9)}
#veil{position:absolute;inset:0;background:#000;pointer-events:none}
/* details pages */
#det{position:absolute;inset:0;padding:20px 26px 30px;display:none;flex-direction:column}
#det.on{display:flex;background:radial-gradient(ellipse at 50% 40%,rgba(10,4,5,.55),rgba(10,4,5,.25) 70%)}
#det .dh{display:flex;align-items:center;height:40px;gap:12px}
#det .dh .title{font-size:24px;font-weight:600;letter-spacing:-.01em}
#det .tabs{margin-left:auto;display:flex;gap:2px}#det .tabs .k{padding:6px 9px;border-radius:8px;display:block}#det .tabs .on{background:rgba(255,255,255,.14);color:#fff}
#det .gridw{flex:1;display:flex;flex-direction:column;gap:10px;margin-top:12px;min-height:0}
#det .gr{flex:1;display:flex;gap:10px;min-height:0}
#det .c{position:relative;flex:1 1 0;min-width:0;display:flex;flex-direction:column;padding:12px 14px}
#det .big{font-size:30px;font-weight:800;letter-spacing:-.03em;line-height:1;margin-top:6px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#det .big small{font:500 11.5px "JetBrains Mono",monospace;color:rgba(255,230,220,.65);margin-left:7px;letter-spacing:0}
#det .rows{margin-top:6px;display:flex;flex-direction:column;justify-content:space-around;min-height:0}
#det .rows.fill{flex:1}
#det .row .bar{width:56px;height:4px;border-radius:2px;background:rgba(255,255,255,.12);overflow:hidden;flex:none}#det .row .bar b{display:block;height:100%;border-radius:2px;background:#7cf5b5}
#det .chips{display:flex;flex-wrap:wrap;gap:5px;margin-top:7px}#det .chips span{font:500 9.5px "JetBrains Mono",monospace;color:#ffe7dc;background:rgba(255,255,255,.08);border-radius:6px;padding:3px 7px;white-space:nowrap}
#det .chips span b{color:#fff}
#det svg.g{display:block;width:100%;flex:1;min-height:24px;margin-top:6px}
#det .hb{display:flex;align-items:flex-end;gap:2px;margin-top:8px}#det .hb i{flex:1;border-radius:2px 2px 0 0;position:relative;display:flex;flex-direction:column;justify-content:flex-end;min-height:2px}
#det .hb i s{display:block}#det .hb em{position:absolute;left:-8px;right:-8px;bottom:100%;margin-bottom:2px;text-align:center;font:500 9px "JetBrains Mono",monospace;font-style:normal;color:#ffe7dc}
#det .hl{display:flex;margin-top:4px;font:500 9px "JetBrains Mono",monospace;color:rgba(255,230,220,.6)}
#det .dgrid{display:grid;grid-template-columns:1fr 1fr;gap:8px 14px;margin-top:8px}
#det .dd .top{display:flex;justify-content:space-between;font-size:11.5px;gap:6px}#det .dd .top em{font:500 10px "JetBrains Mono",monospace;font-style:normal;white-space:nowrap}
#det .dd .bar{height:5px;border-radius:3px;background:rgba(255,255,255,.12);margin-top:4px;overflow:hidden}#det .dd .bar b{display:block;height:100%;border-radius:3px}
#det .dd .inf{font:500 9px "JetBrains Mono",monospace;color:rgba(255,230,220,.6);margin-top:3px}
#det .cores{display:flex;gap:2px;height:14px;margin-top:6px}#det .cores i{flex:1;background:rgba(255,255,255,.08);position:relative;border-radius:1px}#det .cores i b{position:absolute;left:0;right:0;bottom:0;background:#7cf5b5;border-radius:1px}
#det .checks{display:flex;gap:2px;height:7px;margin-top:7px}#det .checks i{flex:1;border-radius:1px}
#det .q{margin-top:6px}#det .q .n{display:flex;justify-content:space-between;font-size:12px}#det .q .qb{height:5px;border-radius:3px;background:rgba(255,255,255,.12);margin:5px 0;overflow:hidden}#det .q .qb b{display:block;height:100%;background:linear-gradient(90deg,#ff6a3a,#ffc46b)}
#det .q .i{font:500 9.5px "JetBrains Mono",monospace;color:rgba(255,230,220,.6);margin-bottom:6px}
#det .dhint{position:absolute;left:0;right:0;bottom:9px;text-align:center;font:500 10px "JetBrains Mono",monospace;color:rgba(255,220,210,.55)}
#det .dhint b{color:#fff;border:1px solid rgba(255,255,255,.25);border-radius:5px;padding:0 5px;margin:0 5px 0 16px}
#main{position:absolute;inset:0;transition:opacity .35s}
#main.off{opacity:0}
#sweep{position:absolute;left:50%;top:50%;width:266px;height:266px;margin:-133px 0 0 -133px;pointer-events:none;will-change:transform;animation:spin 9s linear infinite;background:conic-gradient(from 0deg,rgba(255,176,122,0) 0deg,rgba(255,176,122,.08) 120deg,rgba(255,200,160,.35) 300deg,rgba(255,232,210,.95) 358deg,rgba(255,176,122,0) 360deg);border-radius:50%;-webkit-mask:radial-gradient(closest-side,transparent calc(100% - 8px),#000 calc(100% - 7px));mask:radial-gradient(closest-side,transparent calc(100% - 8px),#000 calc(100% - 7px))}@keyframes spin{to{transform:rotate(360deg)}}
</style></head><body><div id="stage">
<canvas id="gl" class="full" width="1920" height="1080"></canvas><canvas id="sp" class="full" width="960" height="540"></canvas><canvas id="fx" class="full" width="960" height="540"></canvas>

<div id="main">
<div id="eyewrap" style="position:absolute;left:345px;top:14px;width:270px;height:270px;transform-origin:135px 135px"><svg id="eye" style="position:absolute;left:0;top:0" width="270" height="270" viewBox="0 0 300 300">
 <defs>
  <radialGradient id="lens"><stop offset="0" stop-color="#fffbe0"/><stop offset=".06" stop-color="#ffd27a"/><stop offset=".16" stop-color="#ff5a1f"/><stop offset=".34" stop-color="#d4140a"/><stop offset=".6" stop-color="#5a0402"/><stop offset=".88" stop-color="#120000"/><stop offset="1" stop-color="#000"/></radialGradient>
  <linearGradient id="rim" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#9aa0a8"/><stop offset=".5" stop-color="#2b2e33"/><stop offset="1" stop-color="#6c7178"/></linearGradient>
  <radialGradient id="bloom"><stop offset="0" stop-color="#ff4a1a" stop-opacity=".8"/><stop offset=".5" stop-color="#ff2a10" stop-opacity=".25"/><stop offset="1" stop-color="#ff2a10" stop-opacity="0"/></radialGradient>
  <radialGradient id="coreg"><stop offset="0" stop-color="#fff3c4" stop-opacity=".95"/><stop offset=".35" stop-color="#ff8a3a" stop-opacity=".55"/><stop offset="1" stop-color="#ff3010" stop-opacity="0"/></radialGradient>
  <linearGradient id="ga" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#ffd27a"/><stop offset="1" stop-color="#ff4b2b"/></linearGradient>
  <linearGradient id="gb" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#ff9a3c"/><stop offset="1" stop-color="#e11d48"/></linearGradient>
  <linearGradient id="gc" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#9ff5d0"/><stop offset="1" stop-color="#2dd4bf"/></linearGradient>
  <linearGradient id="sw" gradientUnits="userSpaceOnUse" x1="150" y1="7" x2="251" y2="49"><stop offset="0" stop-color="#ffb07a" stop-opacity="0"/><stop offset="1" stop-color="#ffe0c8" stop-opacity=".95"/></linearGradient>
  <clipPath id="lensclip"><circle cx="150" cy="150" r="76"/></clipPath>
  <filter id="glf"><feGaussianBlur stdDeviation="2.4" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
 </defs>
 <circle cx="150" cy="150" r="150" fill="url(#bloom)"/>
 <g id="ticks"></g>
 
 <g fill="none" stroke-linecap="round" filter="url(#glf)">
  <circle id="r1" cx="150" cy="150" r="128" stroke="url(#ga)" stroke-width="6" stroke-dasharray="0 804" transform="rotate(-90 150 150)"/>
  <circle id="r2" cx="150" cy="150" r="116" stroke="url(#gb)" stroke-width="6" stroke-dasharray="0 729" transform="rotate(-90 150 150)"/>
  <circle id="r3" cx="150" cy="150" r="104" stroke="url(#gc)" stroke-width="6" stroke-dasharray="0 653" transform="rotate(-90 150 150)"/>
 </g>
 <circle cx="150" cy="150" r="88" fill="url(#rim)"/><circle cx="150" cy="150" r="82" fill="#0b0b0d"/>
 <g clip-path="url(#lensclip)"><g id="iris"><circle cx="150" cy="150" r="80" fill="url(#lens)"/><circle id="core" cx="150" cy="150" r="40" fill="url(#coreg)" style="mix-blend-mode:screen"/></g></g>
 <ellipse cx="128" cy="112" rx="34" ry="16" fill="rgba(255,255,255,.16)" transform="rotate(-28 128 112)"/><circle cx="182" cy="186" r="5" fill="rgba(255,255,255,.12)"/>
 <g font-family="JetBrains Mono,monospace" font-size="9.5" font-weight="700" fill="#ffe2d6"><text id="l1" x="142" y="26" text-anchor="end"></text><text id="l2" x="142" y="38" text-anchor="end" fill="#ffb59a"></text><text id="l3" x="142" y="50" text-anchor="end" fill="#9ff5d0"></text></g>
</svg><div id="sweep"></div></div>
<div class="card glass l" id="cpc" style="left:26px;top:22px;width:288px;height:186px">
 <div class="k">THE PC <b id="pcl" class="ok">● OK</b></div>
 <div style="display:flex;gap:18px;margin-top:9px;align-items:flex-end">
  <div><div class="num glow" style="font-size:34px"><span id="cpu">—</span><span style="font-size:16px">%</span></div><div class="mono" style="font-size:10px;opacity:.65;margin-top:3px">CPU</div></div>
  <div><div class="num" style="font-size:22px" id="gpu">—</div><div class="mono" style="font-size:10px;opacity:.65;margin-top:3px" id="gpus">GPU</div></div>
  <div><div class="num" style="font-size:22px" id="mem">—</div><div class="mono" style="font-size:10px;opacity:.65;margin-top:3px" id="mems">memory</div></div>
 </div>
 <svg width="254" height="34" style="display:block;margin-top:8px" viewBox="0 0 254 34" preserveAspectRatio="none"><defs><linearGradient id="pcg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#ff7a3a" stop-opacity=".6"/><stop offset="1" stop-color="#ff7a3a" stop-opacity="0"/></linearGradient></defs><path id="pcf" fill="url(#pcg)"/><path id="pcln" fill="none" stroke="#ff9a5c" stroke-width="1.6"/></svg>
 <div class="drv" id="drv"></div>
</div>
<div class="card glass l" id="cnet" style="left:26px;top:220px;width:288px;height:184px">
 <div class="k">NETWORK <b id="netl" class="ok">● ALL UP</b></div>
 <div style="display:flex;align-items:baseline;gap:10px;margin-top:8px"><div class="num glow" style="font-size:34px"><span id="ping">—</span><span style="font-size:15px;font-weight:600;opacity:.7"> ms</span></div><div class="mono" style="font-size:11px;opacity:.75;white-space:nowrap;overflow:hidden;text-overflow:ellipsis" id="rate"></div></div>
 <div style="margin-top:8px" id="netrows"></div>
 <svg width="288" height="44" style="position:absolute;left:0;bottom:0" viewBox="0 0 100 44" preserveAspectRatio="none"><defs><linearGradient id="ntg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#ffb347" stop-opacity=".5"/><stop offset="1" stop-color="#ffb347" stop-opacity="0"/></linearGradient></defs><path id="ntf" fill="url(#ntg)"/><path id="ntl" fill="none" stroke="#ffc46b" stroke-width="1.3" vector-effect="non-scaling-stroke"/></svg>
</div>
<div class="card glass r" id="ckuma" style="left:646px;top:22px;width:288px;height:186px">
 <div class="k">UPTIME KUMA <b id="kumal" class="ok"></b></div><div style="margin-top:7px" id="mons"></div>
</div>
<div class="card glass r" id="cpi" style="left:646px;top:220px;width:288px;height:184px">
 <div class="k">RASPBERRY PI <b id="pil" class="ok"></b></div>
 <div style="display:flex;align-items:baseline;gap:10px;margin-top:8px"><div class="num glow" style="font-size:34px" id="pit">—</div><div class="mono" style="font-size:11px;opacity:.75;white-space:nowrap;overflow:hidden;text-overflow:ellipsis" id="pis"></div></div>
 <div class="mono" style="font-size:9.5px;opacity:.6;margin-top:10px;letter-spacing:.08em" id="dnsk">DNS · LAST 24 H</div>
 <div id="dns" style="display:flex;align-items:flex-end;gap:2px;height:52px;margin-top:6px"></div>
</div>
<div class="quote" id="quote"></div><div class="wave" id="wave"></div>
<div class="card glass alert" id="alert"><div class="ic">!</div><div><b id="at"></b><span id="ad"></span></div></div>
<div class="card glass strip" id="strip"><div class="art"><svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 4v11M7 10l5 5 5-5M5 20h14"/></svg></div>
 <div class="t"><b id="dlt"></b><span id="dli"></span></div><div class="p"><div class="bar"><i id="pb"></i></div><span><b id="pct"></b><em id="dle" style="font-style:normal"></em></span></div></div>
<div id="boot"></div>
<div class="hint"><b>▼</b>details<b>OK</b>HAL talks<b>BACK</b>close</div><div id="clock"></div>
</div>
<div id="det"></div>
<div class="vig"></div><div id="veil"></div>
</div>
<script>
"use strict";
const $=id=>document.getElementById(id),BASE=location.pathname.replace(/\/tv\/?$/,''),KEY=(location.hash.match(/k=([^&]+)/)||[])[1]||'';
const esc=x=>String(x==null?'':x).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const ease=x=>1-Math.pow(1-Math.min(1,Math.max(0,x)),3),sm=x=>x<=0?0:x>=1?1:x*x*(3-2*x);
const nz=(x,s)=>Math.sin(x*1.3+s)*.5+Math.sin(x*2.9+s*1.7)*.3+Math.sin(x*6.1+s*2.3)*.2;
const APP=!!window.HAL;   // inside the TV app: it speaks, and hands us the keys
// fit 960x540 to whatever the screen is
let K=1,PX='';function fit(){const s=Math.min(innerWidth/960,innerHeight/540);K=Math.min(4,s*(window.devicePixelRatio||1));PX=Math.round(innerWidth*(window.devicePixelRatio||1))+'x'+Math.round(innerHeight*(window.devicePixelRatio||1));
 ['sp','fx'].forEach(id=>{const c=document.getElementById(id);if(c){c.width=Math.round(960*K);c.height=Math.round(540*K)}});$('stage').style.transform=`translate(${(innerWidth-960*s)/2}px,${(innerHeight-540*s)/2}px) scale(${s})`;
 document.body.style.width=innerWidth+'px';document.body.style.height=innerHeight+'px'}
addEventListener('resize',fit);fit();
// ---------- data ----------
let B=null,HIST=null,DET=null;
async function api(p){const r=await fetch(BASE+p,{headers:{'X-Mobile-Token':decodeURIComponent(KEY)},cache:'no-store'});if(!r.ok)throw new Error('HTTP '+r.status);return r.json()}
async function poll(){try{B=await api('/api/brief?from=tv&px='+PX);apply()}catch(e){}setTimeout(poll,10000)}
async function pollSlow(){try{HIST=(await api('/api/history?range=1h')).series}catch(e){}try{DET=await api('/api/tv/details');applyDet();if(detOn)drawDet()}catch(e){}setTimeout(pollSlow,60000)}
const lv=l=>l==='critical'?'critical':l==='warning'?'warning':'ok';
function setLevel(id,level,okText,txt){const e=$(id);e.className=lv(level);e.textContent='● '+(txt||(level==='ok'?okText:level==='critical'?'PROBLEM':'CHECK'))}
let lines=[{text:"I'm reading the house, Dave.",say:""}],mons=[],dnsBars=[],drives=[],alert0=null,dl=null,targets={};
function apply(){if(!B)return;const sc=B.screen||{};
 const pc=sc.pc||{},net=sc.net||{},k=sc.kuma||{},pi=sc.pi||{};
 setLevel('pcl',pc.level,'OK');setLevel('netl',net.level,'ALL UP');
 const row=(o,i)=>((o.rows||[])[i]||[]);
 const g=String(row(pc,1)[1]||'—'),m=String(row(pc,2)[1]||'—');
 $('gpu').textContent=g.split(' · ')[0];$('gpus').textContent='GPU'+(g.includes('·')?' · '+g.split(' · ')[1]:'');
 $('mem').textContent=m.split(' of ')[0];$('mems').textContent=m.includes(' of ')?'of '+m.split(' of ')[1]:'memory';
 drives=(pc.drives||[]).slice(0,5);$('drv').innerHTML=drives.map(d=>`<div><span>${esc(d.mount)}<b>${d.pct}%</b></span><i><b class="dv${d.warn?' w':''}" data-w="${d.pct}"></b></i></div>`).join('');
 const r0=String(row(net,0)[1]||'');$('rate').textContent=r0.includes('·')?r0.split('·').slice(1).join('·').trim():'';
 $('netrows').innerHTML=(net.rows||[]).slice(2,4).map(r=>`<div class="row"><span>${esc(r[0])}</span><em class="${r[2]==='ok'?'':lv(r[2])}">${esc(r[1])}</em></div>`).join('');
 if(k.ok){$('kumal').className=lv(k.level);$('kumal').textContent='● '+String(k.head||'').toUpperCase()}else{$('kumal').className='warning';$('kumal').textContent=k.found?'● NOT ANSWERING':'● NOT SET UP'}
 mons=(k.monitors||[]).slice(0,5);$('mons').innerHTML=mons.map((x,i)=>`<div class="mon"><i class="d${x.status==='up'?'':' down'}" id="md${i}"></i><span>${esc(x.name)}</span><svg viewBox="0 0 84 16" preserveAspectRatio="none"><polyline id="m${i}" fill="none" stroke="${x.status==='up'?'#7cf5b5':'#ff6a4d'}" stroke-width="1.3" opacity=".9"/></svg><em class="${x.status==='up'?'':'critical'}">${x.status==='up'?(x.ms==null?'up':Math.round(x.ms)+' ms'):esc(x.status)}</em></div>`).join('');
 setLevel('pil',pi.level,'OK');const pr=pi.rows||[];
 $('pit').textContent=String((pr[1]||[])[1]||'—').replace('°C','°');$('pis').textContent=[(pr[0]||[])[1],'CPU '+String((pr[2]||[])[1]||'—').split(' · ')[0],'mem '+String((pr[3]||[])[1]||'—').replace(' used','')].filter(Boolean).join(' · ');
 alert0=(sc.alerts||[])[0]||null;$('alert').style.display=alert0?'flex':'none';if(alert0){$('alert').className='card glass alert '+lv(alert0.level);$('at').textContent=alert0.title;$('ad').textContent=alert0.detail||''}
 dl=sc.downloads||null;const busy=dl&&dl.state==='downloading';
 $('dlt').textContent=dl?(busy?'Downloading · ':'')+(dl.title||''):'Downloads';$('dli').textContent=dl?String(dl.info||'').toUpperCase():'';
 $('pct').textContent=busy?dl.pct+'%':'';$('dle').textContent=busy?'':(dl&&dl.state==='off'?'':'idle');
 if(Array.isArray(B.lines)&&B.lines.length)lines=B.lines;
 targets={};
}
function applyDet(){try{const pi=(DET.pages||[]).find(p=>p.title==='Raspberry Pi');const card=pi&&pi.grid&&pi.grid[0]&&pi.grid[0][1];
 if(card&&card.hourly){dnsBars=card.hourly.map((v,i)=>[v,(card.hourly2||[])[i]||0]);$('dnsk').textContent='DNS · LAST 24 H · '+String(card.big||'')+' ASKED'}}catch(e){}}
// ---------- voice: what HAL is saying, typed as he says it ----------
let say={text:'',start:-1,frac:-1,speaking:false,target:null,look:null};
function setText(text){if(text!==say.text){say.text=text;say.start=now();say.target=whereFor(text);say.look=/are you there/i.test(text)?'room':null}}
// spoken by the TV: frac is how far the voice has got
window.halSay=function(text,frac){lastFromApp=now();setText(text);say.selfEnd=0;say.silent=false;say.frac=frac;say.speaking=frac<1};
// shown without speaking: typed out here
function typeLine(text,silent){say.text='';setText(text);say.frac=0;say.selfEnd=now()+.6+String(text).length/22;say.silent=silent}
window.halLine=function(text){lastFromApp=now();if(say.speaking&&!say.selfEnd)return;typeLine(text,true)};
window.halVoice=function(on){say.speaking=!!on;if(!on)say.frac=1};
function whereFor(t){t=String(t||'');if(/finished|download|queue|SABnzbd/i.test(t))return'strip';if(/drive|CPU|temperature|memory|graphics|restart|Windows|PC\b/i.test(t))return'pc';
 if(/Pi-hole|ads|internet|network|device|router|security/i.test(t))return'net';if(/Kuma|monitor|down\b|answering/i.test(t))return'kuma';if(/Raspberry|the Pi\b/i.test(t))return'pi';return null}
const where={pc:[-.95,-.35],net:[-.95,.45],kuma:[.95,-.35],pi:[.95,.45],strip:[-.1,.95]};
// without the app, HAL reads his own lines (silently); every few minutes he checks the room
let li=0,nextLineAt=4.2,roomAt=95;
let lastFromApp=-99;
function speakSelf(t){
 const idle=!say.speaking&&!say.selfEnd&&t-lastFromApp>20;
 if(idle&&t>nextLineAt){const L=lines[li++%lines.length];typeLine(L.text||L.say||'',APP);nextLineAt=t+15}
 if(!APP&&t>roomAt&&!say.speaking&&!say.selfEnd){typeLine('Dave… are you there?',false);roomAt=t+240}
 if(say.selfEnd&&t<say.selfEnd){say.frac=Math.min(.999,Math.max(0,(t-say.start-.6)/(say.selfEnd-say.start-.6)));say.speaking=!say.silent}
 else if(say.selfEnd){say.speaking=false;say.frac=1;say.selfEnd=0}}
// in the app, the room check goes through the TV's voice
setInterval(()=>{if(APP&&!say.speaking&&now()>60&&Math.random()<.5)try{HAL.say('Dave… are you there?')}catch(e){}},300000);
// ---------- scene ----------
const T0=performance.now(),now=()=>(performance.now()-T0)/1000;
let tk='';for(let i=0;i<72;i++){const a=i*5*Math.PI/180,r1=139,r2=i%6?143:147;tk+=`<line x1="${(150+r1*Math.sin(a)).toFixed(1)}" y1="${(150-r1*Math.cos(a)).toFixed(1)}" x2="${(150+r2*Math.sin(a)).toFixed(1)}" y2="${(150-r2*Math.cos(a)).toFixed(1)}" stroke="rgba(255,210,190,${i%6?.25:.55})" stroke-width="1.2"/>`}
$('ticks').innerHTML=tk;$('wave').innerHTML='<i></i>'.repeat(24);const waveEls=[...$('wave').children];
const tickEls=[...$('ticks').children];

const cards=[...document.querySelectorAll('#main .card')];
// WebGL smoke + god rays
const gl=null;let glOK=false,ut,W=$('gl').width,H=$('gl').height;
if(gl){try{
 const vs='attribute vec2 p;void main(){gl_Position=vec4(p,0,1);}';
 const fs=`precision highp float;uniform vec2 r;uniform float t;
float h(vec2 p){return fract(sin(dot(p,vec2(127.1,311.7)))*43758.5453);}
float n(vec2 p){vec2 i=floor(p),f=fract(p);f=f*f*(3.-2.*f);return mix(mix(h(i),h(i+vec2(1,0)),f.x),mix(h(i+vec2(0,1)),h(i+vec2(1,1)),f.x),f.y);}
float fbm(vec2 p){float v=0.,a=.5;for(int i=0;i<4;i++){v+=a*n(p);p=p*2.03+vec2(1.7,9.2);a*=.5;}return v;}
void main(){vec2 uv=gl_FragCoord.xy/r;vec2 p=uv*vec2(r.x/r.y,1.)*2.2;vec2 q=vec2(fbm(p+vec2(0.,t*.04)),fbm(p+vec2(5.2,1.3)-t*.03));float w=fbm(p+1.9*q+vec2(t*.02,-t*.06));
 vec2 c=(uv-vec2(.5,.71))*vec2(r.x/r.y,1.);float d=length(c);float pulse=1.;vec3 col=vec3(.035,.008,.01);
 col+=vec3(.42,.05,.02)*pow(w,2.2)*1.6;col+=vec3(.95,.38,.05)*smoothstep(.62,.95,w)*.35*(1.-uv.y*.6);col+=vec3(1.,.22,.05)*exp(-d*d*9.)*.62*pulse;col+=vec3(1.,.55,.2)*exp(-d*d*60.)*.25*pulse;
 float bt=clamp((t-40.)/2.6,0.,1.);col*=1.-.55*smoothstep(.45,1.1,length((uv-.5)*vec2(1.2,1.5)));col*=.15+.85*bt;col+=(h(gl_FragCoord.xy+fract(t*7.)*61.)-.5)*(3./255.);gl_FragColor=vec4(col,1.);}`;
 const sh=(ty,src)=>{const s=gl.createShader(ty);gl.shaderSource(s,src);gl.compileShader(s);return s};
 const pr=gl.createProgram();gl.attachShader(pr,sh(gl.VERTEX_SHADER,vs));gl.attachShader(pr,sh(gl.FRAGMENT_SHADER,fs));gl.linkProgram(pr);
 if(gl.getProgramParameter(pr,gl.LINK_STATUS)){gl.useProgram(pr);const bf=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,bf);gl.bufferData(gl.ARRAY_BUFFER,new Float32Array([-1,-1,1,-1,-1,1,1,1]),gl.STATIC_DRAW);
  const pl=gl.getAttribLocation(pr,'p');gl.enableVertexAttribArray(pl);gl.vertexAttribPointer(pl,2,gl.FLOAT,false,0,0);
  // the smoke is soft, so it is drawn at half size to spare the TV
  $('gl').width=960;$('gl').height=540;W=960;H=540;gl.viewport(0,0,W,H);gl.uniform2f(gl.getUniformLocation(pr,'r'),W,H);ut=gl.getUniformLocation(pr,'t');glOK=true}}catch(e){}}
$('gl').style.background='radial-gradient(ellipse 420px 300px at 50% 28%,rgba(255,60,20,.42),rgba(120,16,8,.18) 55%,transparent 75%),radial-gradient(ellipse 900px 500px at 50% 60%,#1a0706,#0a0405 70%)';
$('gl').style.opacity=0;
const DOT=document.createElement('canvas');DOT.width=DOT.height=128;{const d=DOT.getContext('2d'),g=d.createRadialGradient(64,64,0,64,64,64);g.addColorStop(0,'rgba(255,215,160,1)');g.addColorStop(1,'rgba(255,90,30,0)');d.fillStyle=g;d.fillRect(0,0,128,128)}
const sp=$('sp').getContext('2d'),fx=$('fx').getContext('2d'),FW=1920,FH=1080,EX=960,EY=298;
let seed=7;const rnd=()=>(seed=(seed*16807)%2147483647)/2147483647;
const sparks=Array.from({length:2},()=>({x:rnd()*FW,y:rnd()*FH,v:18+rnd()*40,r:1+rnd()*2.6,ph:rnd()*6.28,dr:(rnd()-.5)*20}));
const motes=Array.from({length:5},()=>({s:Math.floor(rnd()*4),o:rnd(),v:.12+rnd()*.18,w:(rnd()-.5)*120,r:1+rnd()*1.8}));
const srcPts=[[314*2,115*2],[314*2,312*2],[646*2,115*2],[646*2,312*2]];
function gaze(t){
 if(t<1.5)return[0,0];
 const lt=say.start>=0?now()-say.start:99,typed=.6+String(say.text).length/24;
 const path=k=>{for(let i=0;i<k.length-1;i++){const[a,ax,ay]=k[i],[b,bx,by]=k[i+1];if(lt>=a&&lt<=b){const e=sm((lt-a)/Math.min(.35,b-a));return[ax+(bx-ax)*e,ay+(by-ay)*e]}}return null};
 if(say.look==='room'){const p=path([[0,0,0],[typed,0,0],[typed+.4,-1,.05],[typed+1.3,-1,.05],[typed+1.6,1,.05],[typed+2.5,1,.05],[typed+2.8,-.6,0],[typed+3.3,-.6,0],[typed+3.7,0,0],[99,0,0]]);if(p)return p}
 if(say.target&&where[say.target]){const w=where[say.target];const p=path([[0,0,0],[.6,0,0],[1,w[0],w[1]],[Math.max(3,typed),w[0],w[1]],[Math.max(3.4,typed+.4),0,0],[99,0,0]]);if(p)return p}
 const k=Math.floor(t/2.3),f=t/2.3-k,r=i=>{const x=Math.sin(i*12.9898)*43758.5453;return x-Math.floor(x)},to=[(r(k)-.5)*.7,(r(k+.5)-.5)*.5],fr=[(r(k-1)-.5)*.7,(r(k-.5)-.5)*.5],e=sm(f/.25);
 return[fr[0]+(to[0]-fr[0])*e,fr[1]+(to[1]-fr[1])*e]}
function pathD(vals,w,h,lo,hi,step){const pts=vals.map((v,i)=>`${(i*w/Math.max(1,vals.length-1)).toFixed(1)} ${(h-(v-lo)/Math.max(1e-6,hi-lo)*h*.8-h*.1).toFixed(1)}`);return'M'+pts.join(' L')}
function render(t){
 const boot=ease(t/2.6);
 $('gl').style.opacity=Math.min(1,t/1.2);
 // sparks
 sp.setTransform(1,0,0,1,0,0);sp.clearRect(0,0,sp.canvas.width,sp.canvas.height);sp.setTransform(K/2,0,0,K/2,0,0);
 for(const s of sparks){const y=((s.y-t*s.v)%FH+FH)%FH,x=s.x+Math.sin(t*.8+s.ph)*s.dr,a=(.35+.35*Math.sin(t*3+s.ph))*Math.min(1,y/FH*2)*boot;
  if(a>.01){sp.globalAlpha=a;sp.drawImage(DOT,x-s.r*4,y-s.r*4,s.r*8,s.r*8)}}sp.globalAlpha=1;
 // boot: the veil lifts, the lens assembles, ticks light in turn, panels unfold from the eye
 $('veil').style.opacity=1-ease((t-.1)/.6);
 const eye=$('eyewrap'),px=Math.sin(t*.23),py=Math.cos(t*.17);
 eye.style.transform=`scale(${.55+.45*ease((t-.05)/.8)}) translate(${-px*2}px,${-py*2}px)`;eye.style.opacity=Math.min(1,t/.3);
 tickEls.forEach((l,i)=>l.style.opacity=t>.2+i*.008?1:0);
 const bs='HAL 9000 · ONLINE',bn=Math.floor(Math.max(0,t-.3)*30);$('boot').textContent=bs.slice(0,bn);$('boot').style.opacity=t<1.2?1:Math.max(0,1-(t-1.2)/.3);
 cards.forEach((c,i)=>{const e=ease((t-.7-i*.06)/.5),left=parseFloat(c.style.left)<480||c.id==='alert'||c.id==='strip';
  if(t<2){c.style.clipPath=i<4?`inset(0 ${left?(1-e)*100:0}% 0 ${left?0:(1-e)*100}% round 20px)`:`inset(0 ${(1-e)*50}% 0 ${(1-e)*50}% round 16px)`;c.style.opacity=Math.min(1,e*1.4)}else if(c.style.clipPath){c.style.clipPath='';c.style.opacity=1}
  const d=i<4?5:3;c.style.transform=`translate(${px*d}px,${py*d}px)`});
 document.querySelectorAll('.hint,#clock').forEach(e=>e.style.opacity=ease((t-1)/.5));
 // rings and numbers: the real values, filled on opening and breathing a little
 const cpu=(B&&B.cpu!=null?B.cpu:0)+1.2*nz(t*.5,1)*(B?1:0),tmp=B&&B.cpu_temp!=null?B.cpu_temp:0,mem=B&&B.ram!=null?B.ram:0,e=ease((t-.2)/.9);
 const ring=(id,len,f)=>$(id).setAttribute('stroke-dasharray',`${(len*Math.max(0,Math.min(1,f))*e).toFixed(1)} ${len}`);
 ring('r1',804,cpu/100);ring('r2',729,tmp/100);ring('r3',653,mem/100);
 $('l1').textContent=B?`CPU ${Math.max(0,Math.round(cpu*e))}%`:'CPU';$('l2').textContent=B&&B.cpu_temp!=null?`TEMP ${Math.round(tmp*e)}°`:'TEMP';$('l3').textContent=B?`MEM ${Math.round(mem*e)}%`:'MEM';
 $('cpu').textContent=B?Math.max(0,Math.round(cpu*e)):'—';$('ping').textContent=B&&B.ping!=null?Math.max(1,Math.round(B.ping*e)):'—';
 $('core').setAttribute('r',40);$('core').setAttribute('opacity',.85);
 // speech
 speakSelf(t);
 const txt=String(say.text||''),shown=t<1.5?0:(say.frac>=0?Math.round(txt.length*Math.min(1,say.frac<0?1:say.frac)):txt.length);
 $('quote').className='quote'+(txt.length>110?' vlong':txt.length>70?' long':'');$('quote').innerHTML=t<1.5||!txt?'':'“'+esc(txt.slice(0,shown))+(shown<txt.length?'<span class="caret"></span>':'”');
 const speaking=say.speaking&&t>=1.5;
 waveEls.forEach((el,i)=>{const v=Math.abs(nz(t*7+i*.45,i));el.style.height=(2+(speaking?9:1)*v).toFixed(1)+'px'});
 
 const g=gaze(t);$('iris').setAttribute('transform',`translate(${(g[0]*17).toFixed(2)} ${(g[1]*17).toFixed(2)})`);
 document.querySelectorAll('.dv').forEach((b,i)=>b.style.width=(+b.dataset.w*ease((t-.6-i*.12)/1.2))+'%');
 // PC graph from the last hour, flowing
 const ch=(HIST&&HIST.cpu||[]).filter(v=>v!=null).slice(-60);if(ch.length>1){const lo=0,hi=Math.max(20,...ch)*1.15,d=pathD(ch.map((v,i)=>v+1.5*nz((i+t*2)*.4,2)),254,34,lo,hi);$('pcln').setAttribute('d',d);$('pcf').setAttribute('d',d+' V34 H0Z')}
 const pg=(HIST&&HIST.ping||[]).filter(v=>v!=null).slice(-50),src=pg.length>1?pg:Array.from({length:50},()=>10);
 {const lo=Math.min(...src),hi=Math.max(...src)+1;let np=[];for(let i=0;i<src.length;i++){const x=i*100/(src.length-1),y=40-((src[i]-lo)/(hi-lo))*14-2*nz((i+t*5)*.3,8)-8;np.push(`${x.toFixed(2)} ${y.toFixed(2)}`)}$('ntl').setAttribute('d','M'+np.join(' L'));$('ntf').setAttribute('d','M'+np.join(' L')+' V44 H0Z')}
 // Kuma: a live line and a heartbeat per monitor
 mons.forEach((m,k)=>{const el=$('m'+k);if(!el)return;let p='';for(let i=0;i<25;i++){const v=8+(m.status==='up'?4.5:1)*nz((i+t*3)*.35,k*2.1);p+=`${(i*3.5).toFixed(1)},${v.toFixed(2)} `}el.setAttribute('points',p);
});
 // DNS bars grow in
 if(dnsBars.length){if($('dns').children.length!==dnsBars.length){const mx=Math.max(...dnsBars.map(x=>x[0]),1);$('dns').innerHTML=dnsBars.map(([v,b])=>`<i style="flex:1;height:${Math.max(3,v*100/mx)}%;border-radius:2px 2px 0 0;transform-origin:bottom;background:linear-gradient(#ffc46b ${100-(v?b*100/v:0)}%, #ff3a1e ${100-(v?b*100/v:0)}%)"></i>`).join('')}
  [...$('dns').children].forEach((el,i)=>el.style.transform=`scaleY(${ease((t-.8-i*.04)/.8)})`)}
 // alert breathes, download creeps and shimmers
 const ab=.5+.5*Math.sin(t*2.2),warm=alert0&&alert0.level==='critical'?'255,90,70':'255,170,80';$('alert').style.boxShadow=`0 18px 40px rgba(0,0,0,.45),0 0 ${(10+18*ab).toFixed(1)}px rgba(${warm},${(.25+.3*ab).toFixed(2)}),inset 0 1px 0 rgba(255,255,255,.18)`;
 if(dl&&dl.state==='downloading'){$('pb').style.width=dl.pct+'%';$('pb').style.background=`linear-gradient(90deg,#ff6a3a 0%,#ffc46b ${(30+((t*40)%100)).toFixed(0)}%,#ff6a3a 100%)`}else $('pb').style.width='0';
 // clock
 const d=new Date();$('clock').textContent=d.toLocaleDateString([], {weekday:'short',day:'numeric',month:'short'}).toUpperCase()+' · '+d.toLocaleTimeString([], {hour:'2-digit',minute:'2-digit',second:'2-digit'});
 // canvas effects: data streams into HAL, his voice round the lens, the scanning beam
 fx.setTransform(1,0,0,1,0,0);fx.clearRect(0,0,fx.canvas.width,fx.canvas.height);fx.setTransform(K/2,0,0,K/2,0,0);
 if(t>1.4&&!detOn){const a=Math.min(1,(t-1.4)/1);for(const m of motes){const[sx,sy]=srcPts[m.s],p=(m.o+t*m.v)%1,cx=(sx+EX)/2+m.w,cy=Math.min(sy,EY)-80+m.w*.3;
  const x=(1-p)*(1-p)*sx+2*(1-p)*p*cx+p*p*EX,y=(1-p)*(1-p)*sy+2*(1-p)*p*cy+p*p*EY,al=a*Math.sin(p*Math.PI)*.85*(p<.92?1:0);
  if(al>.01){fx.globalAlpha=al;fx.drawImage(DOT,x-m.r*5,y-m.r*5,m.r*10,m.r*10)}}fx.globalAlpha=1}


}
// ---------- details pages ----------
let detOn=false,detAt=0;
const TABS=['The PC','Network','Uptime Kuma','Raspberry Pi','Downloads'];
function graphSvg(v,col,fit,v2,col2){v=v.map(Number);const all=v.concat(v2||[]).map(Number);let lo=fit?Math.min(...all):0,hi=Math.max(...all,fit?lo+1:1);const pad=(hi-lo)*(fit?.25:.15);lo=fit?Math.max(0,lo-pad):0;hi+=pad;
 const P=a=>a.map((x,i)=>(i*100/(a.length-1)).toFixed(2)+','+(50-50*(x-lo)/(hi-lo)).toFixed(2)).join(' ');let o=`<svg class="g" viewBox="0 0 100 50" preserveAspectRatio="none"><polygon points="0,50 ${P(v)} 100,50" fill="${col}" opacity=".18"/><polyline points="${P(v)}" fill="none" stroke="${col}" stroke-width="1.6" vector-effect="non-scaling-stroke"/>`;
 if(v2&&v2.length>1)o+=`<polygon points="0,50 ${P(v2.map(Number))} 100,50" fill="${col2}" opacity=".14"/><polyline points="${P(v2.map(Number))}" fill="none" stroke="${col2}" stroke-width="1.6" vector-effect="non-scaling-stroke"/>`;return o+'</svg>'}
const lcol=l=>l==='critical'?'#ff7a6b':l==='warning'?'#ffc46b':l==='info'?'#9cc8ff':'#7cf5b5';
const bcol=b=>{const u=String(b||'').toUpperCase();return/^(CHECK|PAUSED|NOT |WARM)/.test(u)?'warning':/^(DOWN|HOT|PROBLEM)/.test(u)?'critical':/HEALTHY|^UP |^ALL|^ONLINE|^BLOCKING|^COOL|^OK/.test(u)?'ok':''};
function detCard(c){let h=`<div class="k">${esc(c.key)}${c.badge?`<b class="${bcol(c.badge)}">${esc(c.badge)}</b>`:''}</div>`;
 if(c.big)h+=`<div class="big">${esc(c.big)}${c.bigsub?`<small>${esc(c.bigsub)}</small>`:''}</div>`;
 const fg=c.graph&&c.graph.length>1,fh=!fg&&c.hourly&&c.hourly.length,fill=!fg&&!fh&&!(c.footer_graph&&c.footer_graph.length>1);
 if(fg)h+=graphSvg(c.graph,c.graph_color||'#ff9a5c',c.graph_fit,c.graph2,c.graph2_color);
 if(c.legend)h+=`<div class="hl">${c.legend.map(l=>`<span style="color:${esc(l[1])}">■&nbsp;</span>${esc(l[0])}&nbsp;&nbsp;&nbsp;`).join('')}</div>`;
 if(c.checks)h+=`<div class="checks">${c.checks.slice(-30).map(x=>`<i style="background:${x==='up'?'#7cf5b5':x==='down'?'#ff6a4d':x?'#ffc46b':'rgba(255,255,255,.08)'}"></i>`).join('')}</div>`;
 if(c.cores&&c.cores.length)h+=`<div class="cores">${c.cores.map(v=>`<i><b style="height:${Math.max(8,Math.min(100,v))}%"></b></i>`).join('')}</div>`;
 if(c.drives)h+=`<div class="dgrid">${c.drives.map(d=>`<div class="dd"><div class="top"><span>${esc(d.mount)} ${esc(d.name)}</span><em style="color:${d.warn?'#ffc46b':'#ffe7dc'}">${d.pct}% · ${esc(d.free)} free</em></div><div class="bar"><b style="width:${d.pct}%;background:${d.warn?'linear-gradient(90deg,#ffc46b,#ffb020)':'linear-gradient(90deg,#ff9a3c,#ff4b2b)'}"></b></div><div class="inf">${esc(d.info)}</div></div>`).join('')}</div>`;
 if(c.queue)h+=`<div class="q">${c.queue.map(q=>`<div class="n"><span>${esc(q.name)}</span><span class="mono">${q.pct}%</span></div><div class="qb"><b style="width:${q.pct}%;${q.paused?'background:#ffc46b':''}"></b></div><div class="i">${esc(q.info)}</div>`).join('')}</div>`;
 if(c.monitors)h+=`<div class="rows fill">${c.monitors.map(m=>`<div class="row"><span><span style="color:${m.status==='up'?'#7cf5b5':'#ff6a4d'}">●</span> ${esc(m.name)}</span><em style="color:${m.status==='up'?'#ffe7dc':'#ff7a6b'}">${m.status==='up'?(m.ms==null?'up':m.ms+' ms'):esc(m.status)}</em></div>`).join('')}</div>`;
 if(c.rows&&c.rows.length)h+=`<div class="rows${fill?' fill':''}">${c.rows.map(r=>{const l=r[2]||'ok';return`<div class="row"><span>${esc(r[0])}</span>${r.length>3?`<span class="bar"><b style="width:${Math.max(0,Math.min(100,r[3]))}%;background:${lcol(l)}"></b></span>`:''}<em style="color:${l==='ok'?'#ffe7dc':lcol(l)}">${esc(r[1])}</em></div>`}).join('')}</div>`;
 if(c.hourly&&c.hourly.length){const mx=Math.max(...c.hourly,1);h+=`<div class="hb" style="${fh?'flex:1':'height:46px'};${c.hourly_tops?'padding-top:14px':''}">${c.hourly.map((v,k)=>{const b=c.hourly2?c.hourly2[k]||0:0;return`<i style="height:${Math.max(3,v*100/mx)}%;background:${c.hourly_color||'#2a9d74'}"><s style="height:${v?b*100/v:0}%;background:${c.hourly2_color||'#ff6a4d'}"></s>${c.hourly_tops&&c.hourly_tops[k]?`<em>${esc(c.hourly_tops[k])}</em>`:''}</i>`}).join('')}</div>`;
  if(c.hourly_labels){const each=c.hourly_labels.length===c.hourly.length;h+=`<div class="hl" style="${each?'':'justify-content:space-between'}">${c.hourly_labels.map(l=>`<span style="${each?'flex:1;text-align:center':''}">${esc(l)}</span>`).join('')}</div>`}
  if(c.hourly_label)h+=`<div class="hl">${esc(c.hourly_label)}</div>`}
 if(c.footer_graph&&c.footer_graph.length>1)h+=`<div class="k" style="margin-top:10px">${esc(c.footer_key||'')}${c.footer_badge?`<b>${esc(c.footer_badge)}</b>`:''}</div>`+graphSvg(c.footer_graph,c.footer_color||'#7cf5b5');
 if(c.chips&&c.chips.length)h+=`<div class="chips">${c.chips.map(x=>`<span>${esc(x[0])}<b>${esc(x[1])}</b>${esc(x[2])}</span>`).join('')}</div>`;
 return`<div class="c glass" style="flex:${c.weight||1} 1 0">${h}</div>`}
function drawDet(){const pages=(DET&&DET.pages)||[];const pg=pages[detAt];const el=$('det');
 if(!pg){el.innerHTML='<div class="dh"><span class="title">Details</span></div><div class="dhint">I\'m reading it, Dave…</div>';return}
 const grid=pg.grid||[(pg.cols||[]).map(c=>c)];
 el.innerHTML=`<div class="dh"><svg width="34" height="34" viewBox="0 0 34 34"><circle cx="17" cy="17" r="16" fill="#2b2e33"/><circle cx="17" cy="17" r="12" fill="url(#lens)"/></svg><span class="title">${esc(pg.title)}</span><span class="k" style="margin-left:6px"><b class="${lv(pg.level)}">● ${esc(pg.head||'')}</b></span>
 <span class="tabs">${TABS.map((x,k)=>`<span class="k ${k===detAt?'on':''}">${x.toUpperCase()}</span>`).join('')}</span></div>
 <div class="gridw">${grid.map(r=>`<div class="gr">${r.map(detCard).join('')}</div>`).join('')}</div><div class="dhint"><b>◀ ▶</b>other pages<b>OK</b>HAL talks<b>BACK</b>return to HAL</div>`;
 el.querySelectorAll('.c').forEach((c,i)=>{c.animate([{opacity:0,transform:'translateY(14px)'},{opacity:1,transform:'none'}],{duration:450,delay:i*60,easing:'cubic-bezier(.2,.8,.2,1)',fill:'both'})})}
function openDet(){detOn=true;$('main').classList.add('off');$('det').classList.add('on');drawDet();if(!DET)pollSlowOnce()}
function closeDet(){detOn=false;$('main').classList.remove('off');$('det').classList.remove('on')}
async function pollSlowOnce(){try{DET=await api('/api/tv/details');applyDet();if(detOn)drawDet()}catch(e){}}
// the TV app hands us the remote: returns true if the page used the key
window.halKey=function(k){if(k==='down'){if(!detOn)openDet();return true}
 if(detOn){if(k==='back'||k==='up'){closeDet();return true}if(k==='right'){detAt=(detAt+1)%TABS.length;drawDet();return true}if(k==='left'){detAt=(detAt+TABS.length-1)%TABS.length;drawDet();return true}}
 return false};
addEventListener('keydown',e=>{const m={ArrowDown:'down',ArrowUp:'up',ArrowLeft:'left',ArrowRight:'right',Escape:'back',Backspace:'back'}[e.key];if(m&&halKey(m))e.preventDefault()});
// ---------- go ----------
fit();
function loop(){render(now());requestAnimationFrame(loop)}
window.render=render;
poll();pollSlow();requestAnimationFrame(loop);
setTimeout(()=>{try{if(APP)HAL.ready()}catch(e){}},300);
</script></body></html>"""
