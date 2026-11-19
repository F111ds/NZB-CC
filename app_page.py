"""The all-in-one HAL 9000 app (Home, Get, Watch, System), served by mobile_server.py at /app.

It uses the NZB phone server's API (/api/...) and HAL's (/hal/api/...) on the same address, so it signs in
with the access code the phone already saved for the NZB app (or HAL). Keep this file next to mobile_server.py.
"""

APP_PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>HAL 9000</title>
<meta name="apple-mobile-web-app-capable" content="yes"><meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent"><meta name="apple-mobile-web-app-title" content="HAL 9000">
<meta name="theme-color" content="#050506">
<link rel="manifest" href="/app/manifest.webmanifest"><link rel="icon" type="image/png" href="/app/icon.png"><link rel="apple-touch-icon" href="/app/icon.png">
<style>
:root{color-scheme:dark;--bg:#050506;--card:#111114;--card2:#18181C;--line:#1E1E23;--red:#FF2A1F;--red2:#FF6A4D;--white:#F4F4F5;--text:#D4D4D8;--soft:#A1A1AA;--muted:#71717A;--amber:#FBBF24;--green:#34D399;--sky:#60A5FA;--mono:ui-monospace,"SF Mono","JetBrains Mono","Cascadia Mono",Consolas,monospace}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
html,body{margin:0;background:var(--bg);color:var(--white);font:15px/1.4 -apple-system,BlinkMacSystemFont,"Segoe UI",Inter,Roboto,sans-serif;-webkit-text-size-adjust:100%}
button,input{font:inherit;color:inherit}button{cursor:pointer}
.hidden{display:none!important}
main{max-width:640px;margin:0 auto;padding:calc(env(safe-area-inset-top) + 10px) 16px calc(100px + env(safe-area-inset-bottom))}
.top{display:flex;align-items:center;justify-content:space-between;min-height:46px;margin-bottom:4px}
.brand{font:700 13px var(--mono);letter-spacing:.32em;color:var(--soft)}.brand b{color:var(--white)}
.icons{display:flex;gap:8px}
.ib{position:relative;width:40px;height:40px;border-radius:12px;background:var(--card);border:0;display:grid;place-items:center}
.ib i{position:absolute;top:7px;right:8px;width:9px;height:9px;border-radius:50%;background:var(--amber);border:2px solid var(--card)}
h1{font-size:26px;margin:2px 0 10px}
/* HAL's eye */
.eye{width:92px;height:92px;border-radius:50%;position:relative;flex:none;border:0;padding:0;
 background:radial-gradient(circle,#2a2a2e 0 62%,#0b0b0c 63% 66%,#3a3a40 67% 69%,#0c0c0e 70% 100%);
 box-shadow:0 0 0 2px #2c2c31,0 0 0 5px #0d0d0f,0 0 0 6px #3b3b42,0 14px 40px rgba(255,30,20,.16)}
.eye::before{content:"";position:absolute;inset:14px;border-radius:50%;background:radial-gradient(circle,#FFF6C8 0 5%,#FFC14A 9%,#FF3B1F 20%,#C80E08 38%,#5A0402 62%,#140000 80%,#000 100%);
 box-shadow:0 0 30px 5px rgba(255,42,31,.35);animation:breathe 6s ease-in-out infinite}
.eye.warning{box-shadow:0 0 0 2px #2c2c31,0 0 0 5px #0d0d0f,0 0 0 6px #3b3b42,0 0 0 9px rgba(251,191,36,.55)}
.eye.critical{box-shadow:0 0 0 2px #2c2c31,0 0 0 5px #0d0d0f,0 0 0 6px #3b3b42,0 0 0 9px rgba(255,42,31,.6),0 10px 60px rgba(255,30,20,.4)}
.eye.critical::before{animation:alarm 1.1s ease-in-out infinite}
.eye.talking::before{animation:none!important;filter:brightness(var(--b,1));box-shadow:0 0 calc(30px*var(--b,1)) calc(5px*var(--b,1)) rgba(255,42,31,calc(.22*var(--b,1)))}
.eye.sm{width:40px;height:40px;box-shadow:0 0 0 1px #2c2c31,0 0 0 3px #0d0d0f,0 0 0 4px #3b3b42}.eye.sm::before{inset:6px;box-shadow:0 0 10px 2px rgba(255,42,31,.35)}
.eye.big{width:120px;height:120px}.eye.big::before{inset:19px}
@keyframes breathe{0%,100%{filter:brightness(1)}50%{filter:brightness(1.18)}}
@keyframes alarm{0%,100%{filter:brightness(.75)}50%{filter:brightness(1.5)}}
@media (prefers-reduced-motion:reduce){.eye::before{animation:none!important}}
.hero{display:flex;gap:18px;align-items:center;margin:6px 0 14px;padding-left:9px}
.hero .g{font:600 10px var(--mono);letter-spacing:.14em;color:var(--muted);text-transform:uppercase}
.hero .q{font-size:15px;font-style:italic;color:var(--text);margin-top:4px;line-height:1.35;min-height:40px}
/* phone Home: HAL's eye in the middle, his words underneath (as in the first HAL mock-up) */
.hero.center{flex-direction:column;gap:0;padding-left:0;text-align:center;margin:8px 0 16px}
.hero.center .eye{width:124px;height:124px;box-shadow:0 0 0 2px #2c2c31,0 0 0 6px #0d0d0f,0 0 0 7px #3b3b42,0 18px 50px rgba(255,30,20,.16)}
.hero.center .eye.warning{box-shadow:0 0 0 2px #2c2c31,0 0 0 6px #0d0d0f,0 0 0 7px #3b3b42,0 0 0 10px rgba(251,191,36,.55)}
.hero.center .eye.critical{box-shadow:0 0 0 2px #2c2c31,0 0 0 6px #0d0d0f,0 0 0 7px #3b3b42,0 0 0 10px rgba(255,42,31,.6),0 10px 60px rgba(255,30,20,.4)}
.hero.center .eye::before{inset:19px}
.hero.center .g{margin-top:18px}
.hero.center .q{font-size:16px;margin:6px 4px 0;min-height:66px}
.caret{display:inline-block;width:2px;height:1em;background:var(--red2);vertical-align:-2px;margin-left:2px;animation:blink .8s steps(1) infinite}
@keyframes blink{50%{opacity:0}}
/* blocks */
.card{background:var(--card);border-radius:14px;padding:13px 14px;margin-bottom:10px}
.card h3{margin:0;font-size:14px;display:flex;justify-content:space-between;align-items:center;gap:8px}
.k{font:700 10px var(--mono);letter-spacing:.14em;color:var(--muted)}
.sub{font:12px var(--mono);color:var(--soft);margin-top:4px}
.tt{font-weight:600;font-size:14px;margin-top:8px}
.bar{height:5px;border-radius:3px;background:#222227;margin-top:9px;overflow:hidden}.bar i{display:block;height:100%;border-radius:3px;background:var(--red2)}
.bar i.g{background:var(--green)}.bar i.s{background:var(--sky)}.bar i.a{background:var(--amber)}.bar i.c{background:var(--red)}
.alert{display:flex;gap:11px;width:100%;text-align:left;background:linear-gradient(90deg,#2a1d05,#17140b);border:1px solid #4a3609;border-radius:14px;padding:11px 13px;margin-bottom:10px}
.alert.critical{background:linear-gradient(90deg,#300908,#170b0b);border-color:#5a1410}
.alert b{font-size:14px;display:block}.alert p{margin:2px 0 0;font-size:12.5px;color:var(--text)}
div.alert{cursor:pointer}.alert .grow{flex:1;min-width:0}
.snz{flex:none;align-self:center;border:0;border-radius:9px;background:rgba(255,255,255,.08);color:var(--soft);font:600 11px var(--mono);padding:7px 9px;margin-left:6px}
.hidline{display:flex;align-items:center;justify-content:space-between;gap:10px;font:12px var(--mono);color:var(--muted);margin:-2px 2px 10px}
.hidline button{background:none;border:0;color:var(--red2);font:700 11px var(--mono);letter-spacing:.06em;padding:4px 0}
.s{width:9px;height:9px;border-radius:50%;flex:none;background:var(--green);box-shadow:0 0 8px var(--green)}
.s.warning{background:var(--amber);box-shadow:0 0 8px var(--amber)}.s.critical,.s.down{background:var(--red);box-shadow:0 0 10px var(--red)}
.s.off,.s.info{background:#3f3f46;box-shadow:none}
.alert .s{margin-top:5px}
.sec{font:700 11px var(--mono);letter-spacing:.16em;color:var(--muted);margin:14px 2px 8px;display:flex;justify-content:space-between;align-items:center;gap:8px}
.sec button{background:none;border:0;color:var(--red2);font:700 11px var(--mono);letter-spacing:.06em;padding:0}
.list{background:var(--card);border-radius:14px;overflow:hidden;margin-bottom:10px}
.row{display:flex;align-items:center;gap:12px;padding:11px 14px;border-top:1px solid var(--line);width:100%;background:none;border-left:0;border-right:0;border-bottom:0;text-align:left}
.list>.row:first-child{border-top:0}
.row .t{flex:1;min-width:0}.row .t b{display:block;font-weight:600;font-size:14px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.row .t span{display:block;font:12px var(--mono);color:var(--soft);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.row .m{font:600 12px var(--mono);color:var(--soft);text-align:right;flex:none}
.empty{color:var(--muted);font-size:13px;padding:14px;text-align:center;font-style:italic}
.pill{display:inline-flex;gap:5px;align-items:center;font:600 10.5px var(--mono);padding:3px 8px;border-radius:999px;background:#0f2a20;color:var(--green);white-space:nowrap;flex:none}
.pill.w{background:#2e2408;color:var(--amber)}.pill.o{background:#222227;color:var(--soft)}.pill.s{background:#0c1d33;color:var(--sky)}
.btn{border:0;border-radius:10px;background:var(--card2);color:var(--white);font-weight:600;font-size:12.5px;padding:8px 12px;flex:none}
.btn.r{background:#3a0d0a;color:#ffb4a8}.btn.g{background:#0f2a20;color:var(--green)}.btn.wide{width:100%;padding:12px;font-size:14px}.btn:disabled{opacity:.5}
.strip{display:flex;justify-content:space-between;gap:8px}.strip .v{font:600 18px var(--mono)}.strip .v small{font-size:11px;color:var(--soft)}
.tvcard{background:linear-gradient(120deg,#2a1210,#111114 65%)}.tvcard .k{color:#e8a598}
.remote{display:grid;grid-template-columns:repeat(5,1fr);gap:8px;margin-top:12px}
.remote button{height:44px;border-radius:11px;background:rgba(255,255,255,.07);border:0;font-size:15px}
.remote button.on{background:#3a0d0a;color:#ffb4a8}
.search{display:flex;gap:8px;margin-bottom:10px}
.search input{flex:1;min-width:0;background:var(--card);border:1px solid #26262b;border-radius:12px;padding:12px 14px;font-size:16px}
.seg{display:flex;background:var(--card);border-radius:12px;padding:4px;margin:0 0 12px}
.seg button{flex:1;min-height:38px;border:0;border-radius:9px;background:transparent;color:var(--muted);font-weight:600;font-size:13px}.seg button.on{background:#26262b;color:var(--white)}
.chiprow{display:flex;gap:6px;overflow-x:auto;padding-bottom:4px;margin:-2px 0 10px;scrollbar-width:none;align-items:center}
.chiprow span{font:700 10px var(--mono);letter-spacing:.14em;color:var(--muted);flex:none;margin-right:2px}
.chiprow button{flex:none;border:0;border-radius:999px;padding:7px 12px;background:var(--card);color:var(--soft);font-weight:600;font-size:13px}
.chiprow button.on{background:#3a0d0a;color:#ffd9d2}
.vt{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:10px}
.vt div{background:var(--card);border-radius:12px;padding:10px}.vt .v{font:600 17px var(--mono);margin-top:3px}.vt .v small{font-size:11px;color:var(--soft)}
.hint{font-size:12px;color:var(--muted);text-align:center;font-style:italic;margin:8px 0 0}
/* tabs, sidebar */
nav.tabs{position:fixed;left:0;right:0;bottom:0;background:rgba(10,10,12,.96);backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px);border-top:1px solid #1d1d22;display:flex;padding:8px 6px calc(10px + env(safe-area-inset-bottom));z-index:5}
nav.tabs button{flex:1;display:flex;flex-direction:column;align-items:center;gap:3px;font-size:10px;font-weight:600;color:var(--muted);background:none;border:0;padding:2px 0}
nav.tabs button.on{color:var(--red2)}nav.tabs svg{width:23px;height:23px}
aside.side{display:none}
.cols{display:block}
@media (min-width:1000px){
  body{display:flex}
  aside.side{display:flex;flex-direction:column;gap:4px;width:232px;flex:none;height:100vh;position:sticky;top:0;background:#0a0a0c;border-right:1px solid #1a1a1f;padding:22px 14px}
  aside.side button{display:flex;align-items:center;gap:12px;padding:11px 12px;border-radius:10px;color:var(--muted);font-weight:600;font-size:14px;background:none;border:0;text-align:left}
  aside.side button.on{background:#1a0b0a;color:var(--red2)}aside.side svg{width:20px;height:20px}
  nav.tabs{display:none}
  main{max-width:none;flex:1;min-width:0;padding:26px 30px 40px}
  .cols>div{min-width:0}
  .cols .vt{grid-template-columns:repeat(2,1fr)}
  .hero>div{min-width:0}
  main .narrow{max-width:900px}
  .cols{display:grid;grid-template-columns:1.25fr 1fr 1fr;gap:16px;align-items:start}
  .hero .q{font-size:20px}
  .mobile-only{display:none!important}
}
@media (max-width:999px){.desk-only{display:none!important}}
/* sheet, toast, pairing */
.sheet{position:fixed;inset:0;background:rgba(0,0,0,.6);display:flex;align-items:flex-end;justify-content:center;z-index:10}
.sheet .panel{background:var(--bg);width:100%;max-width:640px;max-height:92vh;overflow-y:auto;border-radius:20px 20px 0 0;padding:18px 16px calc(24px + env(safe-area-inset-bottom));border-top:1px solid #222}
@media (min-width:1000px){.sheet{align-items:center}.sheet .panel{border-radius:20px}}
.sw{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:12px 0;border-top:1px solid var(--line)}
.sw small{display:block;color:var(--muted);font-size:12px}
.fld{display:block;font-size:12px;color:var(--soft);margin:10px 0 5px}
.inp{width:100%;background:var(--card2);border:1px solid #26262b;border-radius:10px;padding:11px 12px;font:16px var(--mono);color:var(--white)}
.cbox{background:var(--card);border-radius:14px;padding:12px 14px;margin-bottom:10px}
.cbox h3{margin:0 0 2px;font-size:15px;display:flex;justify-content:space-between;align-items:center;gap:8px}
.crow{display:flex;gap:8px;margin-top:10px;flex-wrap:wrap}
.warnbox{background:#2a1d05;border:1px solid #4a3609;border-radius:12px;padding:10px 12px;font-size:13px;color:#fde68a;margin-bottom:10px}.sw input{width:22px;height:22px;accent-color:#FF3B1F;flex:none}
#updbar{position:fixed;left:0;right:0;top:0;z-index:15;background:#1a1407;border-bottom:1px solid #4a3609;color:#fde68a;text-align:center;font-weight:600;font-size:13px;padding:calc(env(safe-area-inset-top) + 8px) 12px 8px}
#toast{position:fixed;left:16px;right:16px;bottom:calc(96px + env(safe-area-inset-bottom));max-width:600px;margin:0 auto;background:#1c1c21;border:1px solid #2c2c31;border-radius:12px;padding:12px 14px;font-size:14px;z-index:20;opacity:0;transform:translateY(10px);transition:all .25s;pointer-events:none}
#toast.on{opacity:1;transform:none}#toast.bad{border-color:#5a1410}
#pair{max-width:420px;margin:0 auto;padding:calc(env(safe-area-inset-top) + 50px) 22px 40px;text-align:center}
#pair input{width:100%;background:var(--card);border:1px solid #26262b;border-radius:10px;padding:12px;font:16px var(--mono);margin:14px 0 10px}
/* (16px: an iPhone zooms into smaller text boxes, and a home-screen app then stays zoomed in) */
</style></head><body>

<aside class="side" id="side">
  <div style="display:flex;align-items:center;gap:12px;padding:4px 8px 22px"><div class="eye sm" id="seye"></div><span class="brand"><b>HAL</b> 9000</span></div>
  <button data-tab="home" class="on"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="3.5" fill="currentColor"/></svg>Home</button>
  <button data-tab="get"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 3v12M7 10l5 5 5-5M4 19h16"/></svg>Get</button>
  <button data-tab="watch"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="5" width="18" height="12" rx="2"/><path d="M8 21h8M10 9l5 2-5 2z"/></svg>Watch</button>
  <button data-tab="system"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 12h4l3-8 4 16 3-8h4"/></svg>System</button>
  <div style="flex:1"></div>
  <button data-settings><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M4.9 19.1L7 17M17 7l2.1-2.1"/></svg>Settings</button>
  <div style="font:11px var(--mono);color:var(--muted);padding:8px 12px" id="sidefoot"></div>
</aside>

<div id="pair" class="hidden">
  <div style="display:flex;justify-content:center"><div class="eye big" id="peye"></div></div>
  <p class="hero" style="display:block;padding:0;margin-top:26px"><span class="q" id="pquote">“I'm sorry, Dave. I can't let you in without the access code.”</span></p>
  <p style="font-size:14px;color:var(--soft)">On the PC, open <b id="pairurl">http://localhost:8765/hal/pair</b> and type the <b>6-digit PIN</b>. Or paste the access code (the same as the NZB app's).</p>
  <input id="code" placeholder="6-digit PIN or access code" autocomplete="off" autocapitalize="off" autocorrect="off" spellcheck="false">
  <button id="connect" class="btn r wide">Open the pod bay doors</button>
</div>

<main id="app" class="hidden">
  <section id="t-home"></section>
  <section id="t-get" class="hidden"></section>
  <section id="t-watch" class="hidden"></section>
  <section id="t-system" class="hidden"></section>
</main>

<nav class="tabs hidden" id="tabs">
  <button data-tab="home" class="on"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="3.5" fill="currentColor"/></svg>Home</button>
  <button data-tab="get"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 3v12M7 10l5 5 5-5M4 19h16"/></svg>Get</button>
  <button data-tab="watch"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="5" width="18" height="12" rx="2"/><path d="M8 21h8M10 9l5 2-5 2z"/></svg>Watch</button>
  <button data-tab="system"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 12h4l3-8 4 16 3-8h4"/></svg>System</button>
</nav>
<div id="sheet" class="sheet hidden"><div class="panel" id="panel"></div></div>
<div id="updbar" class="hidden">Updating… I'll be back in about a minute.</div>
<div id="toast"></div>

<script>
'use strict';
const $=id=>document.getElementById(id);
const esc=s=>String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const LS={get(k,d){try{const v=localStorage.getItem(k);return v==null?d:v}catch(e){return d}},set(k,v){try{localStorage.setItem(k,v)}catch(e){}},del(k){try{localStorage.removeItem(k)}catch(e){}}};
const LV={ok:0,info:1,warning:2,critical:3};
// Same address as the NZB app and HAL: use whichever code this phone already saved.
let token=LS.get('app_token','')||LS.get('nzb_mobile_token','')||LS.get('hal_token','');
let tab='home',H=null,S=null,Q=null,UP=null,K=null,results=null,getSeg='queue',lastQuote='',busy=false;
// Search: what to look for (Newznab categories: 2000 movies, 5000 TV) and how to sort; remembered on this device.
let getCat=LS.get('app_cat',''),sortBy=LS.get('app_sort','best'),lastQuery='',searching=false;
const CATS=[['','All'],['2000','Movies'],['5000','TV']];
const SORTS=[['best','Best'],['size','Size'],['new','Newest'],['grabs','Grabs']];
const DESK=matchMedia('(min-width:1000px)').matches&&!('ontouchstart' in window);
const NAME=()=>(S&&S.hal&&S.hal.name)||'Dave';
const fb=(n,d=1)=>{if(n==null||isNaN(n))return'—';const u=['B','KB','MB','GB','TB'];let i=0;n=Number(n);while(Math.abs(n)>=1024&&i<u.length-1){n/=1024;i++}return(i?n.toFixed(n>=100?0:d):n.toFixed(0))+' '+u[i]};
const f0=(n,s='')=>n==null||isNaN(n)?'—':Math.round(n)+s;
const mmss=s=>{s=Math.max(0,Math.round(s||0));const h=Math.floor(s/3600),m=Math.floor(s%3600/60),x=s%60;return(h?h+':'+String(m).padStart(2,'0'):m)+':'+String(x).padStart(2,'0')};
function secsOf(eta){const p=String(eta||'').split(':').map(Number);if(p.some(isNaN)||!p.length)return null;return p.reduce((a,b)=>a*60+b,0)}
const clock=t=>new Date(t).toLocaleTimeString([],{hour:'numeric',minute:'2-digit'});
function whenText(iso){if(!iso)return'';const d=new Date(iso.length<=10?iso+'T12:00:00':iso),now=new Date();const days=Math.round((new Date(d.toDateString())-new Date(now.toDateString()))/864e5);
  const day=days===0?'Today':days===1?'Tomorrow':days<7&&days>0?d.toLocaleDateString([],{weekday:'short'}):d.toLocaleDateString([],{month:'short',day:'numeric'});return iso.length>10?day+' '+clock(d):day}
function ago(t){if(!t)return'';const ts=typeof t==='number'?t*1000:Date.parse(t);if(isNaN(ts))return'';const d=(Date.now()-ts)/1000;if(d<3600)return Math.max(1,Math.round(d/60))+' min ago';if(d<86400)return clock(ts);return new Date(ts).toLocaleDateString([],{weekday:'short'})}
async function api(path,body){const o={headers:{'X-Mobile-Token':token}};if(body!==undefined){o.method='POST';o.headers['Content-Type']='application/json';o.body=JSON.stringify(body)}
  const r=await fetch(path,o);let d={};try{d=await r.json()}catch(e){}
  if(r.status===401&&!path.startsWith('/hal/api/pair')){showPair(d.error);throw new Error(d.error||'Not signed in')}
  if(!r.ok)throw new Error(d.error||('Error '+r.status));return d}

/* ---------- HAL's voice: words type out while the eye flickers; spoken on the phone after a tap ---------- */
const REDUCE=matchMedia('(prefers-reduced-motion: reduce)').matches;
const CAN_SPEAK=!DESK&&'speechSynthesis' in window&&'SpeechSynthesisUtterance' in window;
let talkRun=null,voiceOn=false,unlocked=false;
const talkative=()=>CAN_SPEAK&&LS.get('hal_talk','on')==='on';
function pickVoice(){const vs=CAN_SPEAK?speechSynthesis.getVoices().filter(v=>/^en/i.test(v.lang)):[];const want=LS.get('hal_voice_name','');
  const score=v=>(/daniel|arthur|aaron|oliver|malcolm|gordon|reed|evan|tom|alex|fred|george|ryan|male/i.test(v.name)?10:0)+(/premium|enhanced|neural|natural/i.test(v.name)?6:0)+(/en-GB/i.test(v.lang)?3:0);
  return vs.find(v=>v.name===want)||vs.sort((a,b)=>score(b)-score(a))[0]||null}
const speakable=t=>String(t).replace(/\b([A-Z]):/g,'$1').replace(/°C/g,' degrees').replace(/(\d)\s*GB\b/g,'$1 gigabytes').replace(/(\d)\s*TB\b/g,'$1 terabytes').replace(/[—·]/g,', ');
function utter(text){if(!CAN_SPEAK)return;const u=new SpeechSynthesisUtterance(speakable(text));u.rate=Number(LS.get('hal_rate','0.82'));u.pitch=Number(LS.get('hal_pitch','0.7'));const v=pickVoice();if(v){u.voice=v;u.lang=v.lang}
  u.onend=u.onerror=()=>{voiceOn=false};if(speechSynthesis.speaking||speechSynthesis.pending)speechSynthesis.cancel();voiceOn=true;speechSynthesis.speak(u)}
function talk(eye,el,text,speak){if(talkRun)talkRun.stop();const wrap=s=>'“'+esc(s)+'”';if(!eye||!el)return;
  if(REDUCE&&!speak){el.innerHTML=wrap(text);return}
  if(speak)utter(text);
  let raf=0,stopped=false,cur=1,target=1,next=0;const t0=performance.now(),cps=speak?13:30;eye.classList.add('talking');
  const run={stop(){stopped=true;cancelAnimationFrame(raf);eye.classList.remove('talking');eye.style.removeProperty('--b');el.innerHTML=wrap(text)}};talkRun=run;
  (function f(now){if(stopped)return;const shown=Math.min(text.length,Math.floor((now-t0)/1000*cps));const busy=speak?(voiceOn||shown<text.length):shown<text.length;
    el.innerHTML='“'+esc(text.slice(0,shown))+(shown<text.length?'<span class="caret"></span>':'”');
    if(busy){if(now>=next){const ch=text[Math.max(0,shown-1)]||' ';target=/[aeiouy]/i.test(ch)?1.5+Math.random()*.45:/[\s,.;:!?]/.test(ch)?.72:1.12+Math.random()*.25;next=now+55+Math.random()*85}}else target=1;
    cur+=(target-cur)*.32;eye.style.setProperty('--b',cur.toFixed(3));if(!busy&&Math.abs(cur-1)<.015){run.stop();if(talkRun===run)talkRun=null;return}raf=requestAnimationFrame(f)})(t0)}
document.addEventListener('pointerdown',e=>{if(unlocked||!CAN_SPEAK)return;unlocked=true;if(!talkative()||!H||e.target.closest('#heye,[data-talk]'))return;
  setTimeout(()=>{const eye=$('heye');if(eye&&!voiceOn)talk(eye,$('hquote'),greeting()+' '+lastQuote,true)},60)},true);
function toast(msg,bad){const t=$('toast');t.textContent=msg;t.className='on'+(bad?' bad':'');clearTimeout(toast.h);toast.h=setTimeout(()=>t.className='',3600);if(talkative()&&unlocked&&!talkRun&&!bad)utter(msg)}

/* ---------- pairing ---------- */
function showPair(msg){$('app').classList.add('hidden');$('tabs').classList.add('hidden');$('side').classList.add('hidden');$('pair').classList.remove('hidden');
  $('pairurl').textContent='http://localhost:'+(location.port||80)+'/hal/pair';if(msg&&token)talk($('peye'),$('pquote'),msg,false)}
async function connect(){let v=String($('code').value||'').replace(/\\[nrt]/g,'').replace(/[^A-Za-z0-9_\-]/g,'');if(!v)return;
  if(/^\d{6}$/.test(v)){try{const r=await fetch('/hal/api/pair',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({pin:v})});const d=await r.json().catch(()=>({}));
      if(!r.ok||!d.token){talk($('peye'),$('pquote'),d.error||'That PIN isn\'t right.',false);return}v=d.token}catch(e){talk($('peye'),$('pquote'),'I can\'t reach the PC.',false);return}}
  token=v;try{await api('/api/home');LS.set('app_token',v);$('pair').classList.add('hidden');start()}catch(e){talk($('peye'),$('pquote'),e.message,false)}}
$('connect').onclick=connect;$('code').onkeydown=e=>{if(e.key==='Enter')connect()};

/* ---------- small pieces ---------- */
const SVG={spk:'<path d="M11 5L6 9H3v6h3l5 4zM15.5 8.5a5 5 0 010 7M18.5 5.5a9 9 0 010 13"/>',mute:'<path d="M11 5L6 9H3v6h3l5 4zM22 9l-6 6M16 9l6 6"/>',bell:'<path d="M6 8a6 6 0 1112 0c0 7 3 9 3 9H3s3-2 3-9M10 21h4"/>',gear:'<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M4.9 19.1L7 17M17 7l2.1-2.1"/>'};
const svg=(k,c)=>'<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="'+(c||'#D4D4D8')+'" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'+SVG[k]+'</svg>';
function topbar(title,withSpk){const lvl=S?S.hal.level:'';
  return'<div class="top mobile-only"><span class="brand"><b>HAL</b> 9000</span><span class="icons">'+(withSpk&&CAN_SPEAK?'<button class="ib" data-talk aria-label="Voice">'+svg(talkative()?'spk':'mute',talkative()?'#FF6A4D':'#71717A')+'</button>':'')+
    '<button class="ib" data-alerts aria-label="Alerts">'+svg('bell')+(S&&S.unread?'<i></i>':'')+'</button><button class="ib" data-settings aria-label="Settings">'+svg('gear')+'</button></span></div>'+
    (title?'<h1>'+esc(title)+'</h1>':'')}
function greeting(){const h=new Date().getHours();return'Good '+(h>=5&&h<12?'morning':h<18?'afternoon':'evening')+', '+NAME()+'.'}
function issues(){return S?S.issues.filter(i=>LV[i.level]>=2):[]}
// What HAL says on Home: downloads, the TV, and anything that needs you, in one or two sentences.
function homeLine(){if(!H&&!S)return'I\'m thinking…';const parts=[],iss=issues(),crit=iss.find(i=>i.level==='critical');
  if(crit)return'I\'m sorry, '+NAME()+'. '+crit.title+'.';
  const cur=H&&H.current,n=H?H.total||0:0;
  if(cur&&!H.paused){const s=secsOf(cur.eta);const at=s!=null&&s<86400?' by '+clock(Date.now()+s*1000):'';parts.push(n>1?n+' downloads are on their way'+(at?', the first one'+at:'')+'.':'One download finishes'+(at||' soon')+'.')}
  else if(H&&H.paused)parts.push('Downloads are paused.');
  const kp=H&&H.kodi&&H.kodi.playing;parts.push(kp?'You\'re watching '+kp.title+'.':'The TV is free.');
  if(iss.length===1)parts.push('There\'s one small thing: '+iss[0].title.charAt(0).toLowerCase()+iss[0].title.slice(1)+'.');
  else if(iss.length>1)parts.push(iss.length+' things need your attention.');
  if(!cur&&!kp&&!iss.length&&S)return S.hal.quote;
  return parts.join(' ')}
let showHidden=false;
// Small things (notes like 'Windows wants a restart'): one card, as in HAL, so what HAL mentions can be seen.
function notesCard(){const n=S?S.issues.filter(i=>i.level==='info'):[];if(!n.length)return'';
  return'<div class="alert info" data-hal="'+esc(n[0].tab)+'" style="background:var(--card);border-color:#1f2a3a"><span class="s" style="background:var(--sky);box-shadow:0 0 8px var(--sky)"></span><span class="grow"><b>'+(n.length>1?n.length+' small things':'One small thing')+'</b><p>'+esc(n.map(i=>i.title).join(' · '))+'</p></span><button class="snz" data-snooze="'+esc(n.map(i=>i.key).join('|'))+'" aria-label="Hide for 24 hours">Hide 24 h</button></div>'}
function attention(max){const list=issues().slice(0,max).map(i=>'<div class="alert '+i.level+'" data-hal="'+esc(i.tab)+'"><span class="s '+i.level+'"></span><span class="grow"><b>'+esc(i.title)+'</b>'+(i.detail?'<p>'+esc(i.detail)+'</p>':'')+'</span><button class="snz" data-snooze="'+esc(i.key)+'" aria-label="Hide for 24 hours">Hide 24 h</button></div>').join('')+notesCard();
  const h=(S&&S.snoozed)||[];if(!h.length)return list;
  const until=Math.min(...h.map(i=>i.until));
  let x=list+'<div class="hidline"><span>'+h.length+' alert'+(h.length>1?'s':'')+' hidden until '+esc((new Date(until*1000).toDateString()===new Date().toDateString()?'':new Date(until*1000).toDateString()===new Date(Date.now()+864e5).toDateString()?'tomorrow ':new Date(until*1000).toLocaleDateString([],{weekday:'short'})+' ')+clock(until*1000))+'</span><button data-showhidden>'+(showHidden?'CLOSE':'SHOW')+'</button></div>';
  if(showHidden)x+='<div class="list">'+h.map(i=>'<div class="row"><span class="s '+i.level+'"></span><span class="t"><b>'+esc(i.title)+'</b><span>Hidden until '+esc(new Date(i.until*1000).toLocaleString([],{weekday:'short',hour:'numeric',minute:'2-digit'}))+'</span></span><button class="btn" data-unsnooze="'+esc(i.key)+'">Show again</button></div>').join('')+'</div>';
  return x}
function downloadsCard(){if(!H)return'';if(H.sab_ok===false)return'<div class="card"><h3><span class="k">DOWNLOADS</span><span class="pill w">OFFLINE</span></h3><div class="sub">SABnzbd isn\'t answering: '+esc(H.sab_error||'')+'</div></div>';
  const c=H.current;if(!c)return'<div class="card"><h3><span class="k">DOWNLOADS</span><span class="pill o">IDLE</span></h3><div class="sub">Nothing downloading. What would you like, '+esc(NAME())+'?</div></div>';
  const s=secsOf(c.eta);return'<div class="card"><h3><span class="k">DOWNLOADING'+(H.total>1?' · 1 OF '+H.total:'')+'</span><span style="display:flex;gap:6px;align-items:center"><span class="pill'+(H.paused?' w':'')+'">'+esc(H.paused?'PAUSED':H.speed)+'</span>'+
    '<button class="btn" data-q="'+(H.paused?'resume':'pause')+'">'+(H.paused?'▶':'❚❚')+'</button></span></h3><div class="tt">'+esc(c.name)+'</div>'+
    '<div class="sub">'+f0(c.pct,'%')+' · '+esc(c.sizeleft||'')+' left'+(s!=null&&!H.paused?' · ready ~'+clock(Date.now()+s*1000):'')+'</div><div class="bar"><i style="width:'+(c.pct||0)+'%"></i></div></div>'}
function tvCard(full){const k=H&&H.kodi;const p=k&&k.playing;
  if(!p){if(!full)return'';return'<div class="card tvcard"><h3><span class="k">THE TV</span></h3><div class="tt">Nothing\'s playing, '+esc(NAME())+'.</div><div class="sub">'+(k&&k.configured===false?'Kodi isn\'t set up yet.':k&&k.online===false?'Kodi isn\'t answering (the TV may be off).':'Kodi is ready when you are.')+'</div>'+
    (H&&H.tv_ready?'<button class="btn r wide" style="margin-top:12px" data-tvon>Turn the TV on and open Kodi</button>':'')+'</div>'}
  return'<div class="card tvcard"><h3><span class="k">NOW ON THE TV</span>'+(full?'':'<button class="btn" data-kc="playpause" data-pid="'+p.playerid+'">'+(p.paused?'▶ Play':'❚❚ Pause')+'</button>')+'</h3>'+
    '<div class="tt" style="font-size:'+(full?17:14)+'px">'+esc(p.title)+'</div><div class="sub">'+esc((p.sub?p.sub+' · ':'')+mmss(p.time)+' / '+mmss(p.total))+'</div><div class="bar"><i style="width:'+(p.pct||0)+'%;background:#ffb4a8"></i></div>'+
    (full?'<div class="remote"><button data-kc="seek_back" data-pid="'+p.playerid+'">⏪</button><button class="on" data-kc="playpause" data-pid="'+p.playerid+'">'+(p.paused?'▶':'❚❚')+'</button><button data-kc="seek_fwd" data-pid="'+p.playerid+'">⏩</button><button data-kc="stop" data-pid="'+p.playerid+'">■</button><button data-kc="next" data-pid="'+p.playerid+'">⏭</button></div>'+
      '<div class="remote" style="grid-template-columns:repeat(3,1fr)"><button data-kc="vol_down" data-pid="'+p.playerid+'">🔉</button><button data-kc="mute" data-pid="'+p.playerid+'">🔇</button><button data-kc="vol_up" data-pid="'+p.playerid+'">🔊</button></div>':'')+'</div>'}
function arrivedList(n){const a=(H&&H.arrived)||[];if(!a.length)return'';
  return'<div class="sec"><span>JUST ARRIVED</span></div><div class="list">'+a.slice(0,n).map(x=>'<div class="row"><span class="s'+(x.watched?' off':'')+'"></span><span class="t"><b>'+esc(x.title+(x.year?' ('+x.year+')':''))+'</b><span>'+esc([x.quality,ago(x.added),x.watched?'watched':x.in_kodi?'in the library':''].filter(Boolean).join(' · '))+'</span></span><button class="btn r" data-play="'+esc(x.id)+'">▶ '+(DESK?'Play on TV':'TV')+'</button></div>').join('')+'</div>'}
function upcomingList(n,title){const u=(UP||(H&&H.upcoming)||[]).filter(x=>!x.has_file).slice(0,n);if(!u.length)return'';
  return'<div class="sec"><span>'+(title||'COMING UP')+'</span></div><div class="list">'+u.map(x=>'<div class="row"><span class="s"></span><span class="t"><b>'+esc(x.kind==='episode'?x.title+' S'+String(x.season).padStart(2,'0')+'E'+String(x.episode).padStart(2,'0'):x.title+(x.year?' ('+x.year+')':''))+'</b><span>'+esc(whenText(x.when)+(x.release?' · '+x.release:'')+(x.kind==='episode'?' · I\'ll fetch it when it airs':''))+'</span></span><span class="pill">'+(x.app==='sonarr'?'SONARR':'RADARR')+'</span></div>').join('')+'</div>'}
function totals(){if(!H)return'';const z=H.disk_free_gb;return'<div class="card strip"><div><div class="k">DONE</div><div class="v">'+f0(H.done_today)+'</div></div><div><div class="k">FAILED</div><div class="v"'+(H.failed_today?' style="color:var(--red2)"':'')+'>'+f0(H.failed_today)+'</div></div><div><div class="k">IN QUEUE</div><div class="v">'+f0(H.total)+'</div></div><div><div class="k">FREE</div><div class="v">'+(z!=null&&z>=0?(z>=1024?(z/1024).toFixed(1)+'<small> TB</small>':Math.round(z)+'<small> GB</small>'):'—')+'</div></div></div>'}
function jfList(){const j=(H&&H.jellyfin)||[];if(!j.length)return'';return'<div class="sec"><span>JELLYFIN</span></div><div class="list">'+j.map(x=>'<div class="row"><span class="s'+(x.paused?' warning':'')+'"></span><span class="t"><b>'+esc(x.device+' · '+x.title)+'</b><span>'+esc([x.method,x.quality,f0(x.pct,'%')].filter(Boolean).join(' · '))+'</span></span><button class="btn" data-jf="'+(x.paused?'unpause':'pause')+'" data-sid="'+esc(x.session)+'">'+(x.paused?'▶':'❚❚')+'</button><button class="btn" data-jf="stop" data-sid="'+esc(x.session)+'">■</button></div>').join('')+'</div>'}
function vitals(){if(!S)return'';const v=S.vitals,g=(v.gpus||[])[0],n=S.net;return'<div class="vt"><div><div class="k">CPU</div><div class="v">'+f0(v.cpu.pct)+'<small>%</small></div></div><div><div class="k">RAM</div><div class="v">'+f0(v.mem.pct)+'<small>%</small></div></div><div><div class="k">GPU</div><div class="v">'+(g?f0(g.util)+'<small>%</small>':'—')+'</div></div><div><div class="k">PING</div><div class="v">'+f0(n.ping_ms)+'<small>ms</small></div></div></div>'}
function systemsList(){if(!S)return'<div class="list"><div class="empty">'+(window.HAL_OFF?esc(window.HAL_OFF):'I\'m thinking…')+'</div></div>';
  const tabOf={services:'services',network:'shield',storage:'drives',hardware:'vitals',security:'shield',power:'shield'};
  return'<div class="list">'+S.systems.map(x=>'<button class="row" data-hal="'+tabOf[x.id]+'"><span class="s '+x.level+'"></span><span class="t"><b>'+esc(x.name)+'</b><span>'+esc(x.detail)+'</span></span><span class="m"'+(x.level!=='ok'?' style="color:var(--'+(x.level==='critical'?'red2':'amber')+')"':'')+'>'+esc(x.count||(x.level==='ok'?'':'LOOK'))+' ›</span></button>').join('')+
    '<button class="row" data-hal="events"><span class="s '+(S.security.events&&S.security.events.critical?'critical':S.security.events&&S.security.events.total?'warning':'')+'"></span><span class="t"><b>Windows events</b><span>'+esc(S.security.events?(S.security.events.total?S.security.events.total+' worth knowing about this week':'Nothing to worry about this week'):'Drive errors, crashes, failed updates')+'</span></span><span class="m">›</span></button></div>'}

/* ---------- tabs ---------- */
// Home keeps HAL's eye and line in place (so he isn't cut off mid-sentence) and redraws the rest.
function homeBody(){if(DESK)return'<div class="cols"><div>'+attention(3)+downloadsCard()+arrivedList(5)+totals()+'</div><div>'+tvCard(true)+jfList()+upcomingList(5,'COMING UP')+'</div><div>'+vitals()+systemsList()+'<p class="hint" style="text-align:left">“'+esc(S?S.hal.quote:'')+'”</p></div></div>';
  return attention(2)+downloadsCard()+tvCard(false)+jfList()+arrivedList(4)+upcomingList(3,'TONIGHT & THIS WEEK')+totals()}
function renderHome(){const el=$('t-home');
  if(!$('heye'))el.innerHTML='<div id="hhead"></div><div class="hero'+(DESK?'':' center')+'"><button class="eye'+(DESK?' big':'')+'" id="heye" aria-label="Hear HAL"></button><div><div class="g" id="hgreet"></div><div class="q" id="hquote"></div></div></div><div id="hbody"></div>';
  $('hhead').innerHTML=topbar('',true);$('hbody').innerHTML=homeBody();$('hgreet').textContent=greeting();
  const eye=$('heye');eye.className='eye'+(DESK?' big':'')+' '+(S?S.hal.level:'')+(eye.classList.contains('talking')?' talking':'');
  const line=homeLine();if(line!==lastQuote){const first=!lastQuote;lastQuote=line;talk(eye,$('hquote'),line,!first&&talkative()&&unlocked)}}
function shownResults(){let r=(results||[]).slice();
  if(getCat==='2000')r=r.filter(x=>x.kind!=='tv');            // an indexer's mis-filed episode stays out of Movies
  const by={'size_desc':(a,b)=>(b.size_bytes||0)-(a.size_bytes||0),'size_asc':(a,b)=>(a.size_bytes||0)-(b.size_bytes||0),
    'new':(a,b)=>(b.pub_ts||0)-(a.pub_ts||0),'grabs':(a,b)=>(b.grabs||0)-(a.grabs||0)}[sortBy];
  return by?r.sort(by):r}
function rGet(){let x='<div class="narrow">'+topbar('Get')+'<form class="search" id="sform"><input id="sq" placeholder="'+(getCat==='2000'?'Which movie would you like, ':getCat==='5000'?'Which show would you like, ':'What would you like, ')+esc(NAME())+'?" enterkeyhint="search" autocomplete="off"><button class="btn r">Search</button></form>';
  x+='<div class="chiprow">'+CATS.map(c=>'<button data-cat="'+c[0]+'" class="'+(getCat===c[0]?'on':'')+'">'+c[1]+'</button>').join('')+'</div>';
  if(searching)x+='<div class="list"><div class="empty">I\'m searching your indexers, '+esc(NAME())+'…</div></div>';
  if(results&&!searching){const list=shownResults(),sz=sortBy==='size_desc'?'Size ↓':sortBy==='size_asc'?'Size ↑':'Size';
    x+='<div class="chiprow"><span>SORT</span>'+SORTS.map(o=>{const on=o[0]==='size'?sortBy.startsWith('size'):sortBy===o[0];return'<button data-sort="'+o[0]+'" class="'+(on?'on':'')+'">'+(o[0]==='size'?sz:o[1])+'</button>'}).join('')+'</div>';
    results.shown=list;
    x+='<div class="sec"><span>'+list.length+' RESULT'+(list.length===1?'':'S')+(getCat?' · '+(getCat==='2000'?'MOVIES':'TV'):'')+'</span><button data-clear>CLEAR</button></div><div class="list">'+(list.length?list.slice(0,80).map(r=>'<div class="row"><span class="t"><b>'+esc(r.title)+'</b><span>'+esc([r.size,r.res,r.indexer,r.grabs?r.grabs+' grabs':'',r.pub_ts?ago(r.pub_ts):''].filter(Boolean).join(' · '))+'</span></span>'+(r.in_library?'<span class="pill o">HAVE IT</span>':'')+(r.added?'<span class="pill">ADDED</span>':'<button class="btn g" data-add="'+esc(r.id)+'">+ Add</button>')+'</div>').join(''):'<div class="empty">Nothing found, '+esc(NAME())+'. Try fewer words.</div>')+'</div>'}
  x+='<div class="seg">'+[['queue','Queue'],['upcoming','Coming up']].map(s=>'<button data-seg="'+s[0]+'" class="'+(getSeg===s[0]?'on':'')+'">'+s[1]+'</button>').join('')+'</div>';
  if(getSeg==='queue'){if(!Q)x+='<div class="list"><div class="empty">I\'m checking SABnzbd…</div></div>';
    else{x+='<div class="sec"><span>'+esc(Q.state.toUpperCase())+(Q.speed?' · '+esc(Q.speed):'')+'</span><button data-q="'+(Q.state==='Paused'?'resume':'pause')+'">'+(Q.state==='Paused'?'RESUME ALL':'PAUSE ALL')+'</button></div><div class="list">'+
      (Q.slots.length?Q.slots.map(s=>{const p=String(s.status).toLowerCase()==='paused';return'<div class="row"><span class="t"><b>'+esc(s.name)+'</b><span>'+esc([f0(s.percentage,'%'),s.status,s.sizeleft?s.sizeleft+' left':'',s.eta&&s.eta!=='0:00:00'?s.eta:'',s.cat].filter(Boolean).join(' · '))+'</span></span><button class="btn" data-qi="'+(p?'resume':'pause')+'" data-id="'+esc(s.id)+'">'+(p?'▶':'❚❚')+'</button><button class="btn" data-qi="delete" data-id="'+esc(s.id)+'" data-name="'+esc(s.name)+'">✕</button></div>'}).join(''):'<div class="empty">The queue is empty, '+esc(NAME())+'.</div>')+'</div>'}}
  else x+=UP?(upcomingList(40,'NEXT 14 DAYS')||'<div class="list"><div class="empty">Nothing coming up in the next two weeks.</div></div>'):'<div class="list"><div class="empty">I\'m asking Sonarr and Radarr…</div></div>';
  return x+totals()+'</div>'}
const mins=s=>s>=3600?Math.floor(s/3600)+' h '+String(Math.round(s%3600/60)).padStart(2,'0'):Math.round(s/60)+' min';
function movieRow(m,finish){const meta=[m.year,m.runtime?mins(m.runtime):'',m.imdb?'IMDb '+Number(m.imdb).toFixed(1):'',finish&&m.runtime?mins(Math.max(0,m.runtime-m.resume))+' left':'',!finish&&m.watched?'watched':''].filter(Boolean).join(' · ');
  return'<div class="row"><span class="s'+(m.watched?' off':'')+'"></span><span class="t"><b>'+esc(m.title)+'</b><span>'+esc(meta)+'</span></span><button class="btn r" data-movie="'+m.id+'">▶ TV</button></div>'}
function rWatch(){let x='<div class="narrow">'+topbar('Watch')+tvCard(true);
  if(K===null)x+='<div class="list"><div class="empty">I\'m asking Kodi…</div></div>';
  else if(K.online===false&&K.configured!==false)x+='<div class="list"><div class="empty">Kodi isn\'t answering, '+esc(NAME())+'. '+(H&&H.tv_ready?'Turn the TV on above.':'Turn the TV on and open Kodi.')+'</div></div>';
  const c=K&&K.continue||[];if(c.length)x+='<div class="sec"><span>CONTINUE WATCHING</span></div><div class="list">'+c.map(it=>'<div class="row"><span class="s"></span><span class="t"><b>'+esc(it.show.title+' · S'+it.episode.season+' E'+it.episode.episode)+'</b><span>'+esc(it.episode.title+' · '+it.show.watched+' of '+it.show.episodes+' watched')+'</span></span><button class="btn r" data-ep="'+it.episode.id+'">▶ TV</button></div>').join('')+'</div>';
  const rec=K&&K.recent||[],part=rec.filter(m=>m.resume>60&&!m.watched);
  if(part.length)x+='<div class="sec"><span>FINISH WATCHING</span></div><div class="list">'+part.slice(0,4).map(m=>movieRow(m,true)).join('')+'</div>';
  const fresh=rec.filter(m=>!part.includes(m));
  if(fresh.length)x+='<div class="sec"><span>NEW IN YOUR LIBRARY</span></div><div class="list">'+fresh.slice(0,8).map(m=>movieRow(m,false)).join('')+'</div>';
  if(K&&K.counts)x+='<p class="hint">Your library: '+[K.counts.movies?K.counts.movies.toLocaleString()+' movies':'',K.counts.shows?K.counts.shows.toLocaleString()+' shows':'',K.counts.episodes?K.counts.episodes.toLocaleString()+' episodes':''].filter(Boolean).join(' · ')+'</p>';
  x+=jfList();
  x+='<div class="sec"><span>MORE</span></div><div class="list"><a class="row" href="/kodi" style="color:inherit;text-decoration:none"><span class="t"><b>Library, remote and search</b><span>The full Kodi and Jellyfin pages (classic app)</span></span><span class="m">›</span></a></div>';
  return x+'</div>'}
function rSystem(){let x='<div class="narrow">'+topbar('');
  x+='<div class="hero" style="margin-top:0"><div class="eye '+(S?S.hal.level:'')+'" style="width:64px;height:64px"></div><div><div class="g">SYSTEMS</div><div class="q">“'+esc(S?S.hal.quote:'I\'m thinking…')+'”</div></div></div>';
  x+=vitals()+attention(4)+systemsList();
  x+='<div class="sec"><span>POD BAY CONTROLS</span></div><div class="list"><button class="row" data-update><span class="t"><b>'+(updating()?'Updating…':'Update everything')+'</b><span>'+(updating()?'I\'ll be back in about a minute':'Get the newest version of the apps, from GitHub')+'</span></span><span class="m">›</span></button><button class="row" data-hal="log"><span class="t"><b>Log, Lock, Sleep, Fix network, Restart</b><span>Hold to restart: “I\'m afraid I can\'t do that, '+esc(NAME())+'.”</span></span><span class="m">›</span></button></div>';
  return x+'</div>'}
// Don't redraw under a finger: a refresh that swaps a button between touch-down and touch-up loses the tap.
let pressing=false,renderLater=false;
document.addEventListener('pointerdown',()=>{pressing=true},true);
for(const ev of ['pointerup','pointercancel'])document.addEventListener(ev,()=>{setTimeout(()=>{pressing=false;if(renderLater){renderLater=false;render()}},0)},true);
function render(){if(!$('app')||$('app').classList.contains('hidden'))return;if(pressing){renderLater=true;return}
  if(tab==='home')renderHome();
  else{const el=$('t-'+tab),f={get:rGet,watch:rWatch,system:rSystem}[tab];const focused=document.activeElement&&document.activeElement.id==='sq';
    const sqv=$('sq')?$('sq').value:'';el.innerHTML=f();if($('sq')){$('sq').value=sqv;if(focused)$('sq').focus()}}
  $('updbar').classList.toggle('hidden',!updating());
  if($('seye'))$('seye').className='eye sm '+(S?S.hal.level:'');$('sidefoot').textContent=S?S.host+' · up '+Math.floor((S.uptime||0)/86400)+' d':'';}
function show(t){tab=t;['home','get','watch','system'].forEach(k=>$('t-'+k).classList.toggle('hidden',k!==t));document.querySelectorAll('[data-tab]').forEach(b=>b.classList.toggle('on',b.dataset.tab===t));
  window.scrollTo(0,0);if(t==='get'){loadQueue();loadUpcoming()}if(t==='watch')loadKodi();render()}

/* ---------- loading ---------- */
async function refresh(){if(busy)return;busy=true;try{const [h,s]=await Promise.allSettled([api('/api/home'),api('/hal/api/status')]);
    if(h.status==='fulfilled')H=h.value;if(s.status==='fulfilled'){S=s.value;window.HAL_OFF='';updateDone()}else window.HAL_OFF=s.reason&&s.reason.message;
    render()}finally{busy=false}}
// Update button: the PC restarts the phone server in between; wait for its report, then reload with the new version.
let updWatch=false;
function updating(){return updWatch||!!(S&&S.update&&S.update.running)}
function updateDone(){const u=S&&S.update;if(!updWatch||!u||u.running||!u.result||!u.started||u.result.t<u.started)return;
  updWatch=false;const r=u.result;toast(r.state==='updated'?'Updated to version '+r.version+'. I feel much better now, '+NAME()+'.':r.message,r.state==='failed');
  if(r.state==='updated')setTimeout(()=>location.reload(),3000)}
function startUpdate(){if(updating()){toast('I\'m already updating, '+NAME()+'.');return}
  if(!confirm('Update everything now? The app goes quiet for about a minute while the PC restarts the phone server.'))return;
  api('/hal/api/action',{action:'update',confirm:true}).then(r=>{toast(r.message,!r.ok);if(r.updating){updWatch=true;render()}}).catch(e=>toast(e.message,true))}
async function loadQueue(){try{Q=await api('/api/queue');if(tab==='get')render()}catch(e){}}
async function loadUpcoming(){if(UP&&Date.now()-loadUpcoming.t<600000)return;try{UP=(await api('/api/arr/upcoming')).items||[];loadUpcoming.t=Date.now();if(tab==='get'||tab==='home')render()}catch(e){UP=UP||[]}}
async function loadKodi(){try{K=await api('/api/kodi');if(tab==='watch')render()}catch(e){K={continue:[]};if(tab==='watch')render()}}
async function act(fn,ok){try{const r=await fn();if(ok)toast(typeof ok==='function'?ok(r):ok);setTimeout(()=>{refresh();if(tab==='get')loadQueue()},600)}catch(e){toast(e.message,true)}}

/* ---------- settings ---------- */
/* ---------- Connections: SABnzbd, indexers, Kodi (the desktop app's settings, edited here) ---------- */
let CONN=null;
async function openConnections(){$('panel').innerHTML='<h2 style="margin:0">Connections</h2><p class="hint" style="text-align:left">I\'m reading the settings…</p>';$('sheet').classList.remove('hidden');
  try{CONN=await api('/api/connections');CONN.indexers.forEach(x=>x.orig=x.index)}catch(e){toast(e.message,true);$('sheet').classList.add('hidden');return}renderConn()}
function keyPh(set,what){return set?'Saved · type a new one to replace':'Not set yet'}
function renderConn(){const c=CONN;
  let x='<h2 style="margin:0 0 4px">Connections</h2><p class="hint" style="text-align:left;margin:0 0 10px">“I can give you my complete assurance that everything is connected, '+esc(NAME())+'.” Keys are encrypted on the PC and never shown here.</p>';
  if(c.desktop_open)x+='<div class="warnbox">The NZB Command Center desktop window is open on the PC. Close it before saving here, or its next save puts the old settings back.</div>';
  x+='<div class="cbox"><h3>SABnzbd <button class="btn" data-ctest="sab">Test</button></h3><label class="fld">Address</label><input class="inp" id="c_sab_url" value="'+esc(c.sab.url)+'" autocapitalize="off" autocorrect="off" inputmode="url">'+
     '<label class="fld">API key (SABnzbd › Config › General)</label><input class="inp" id="c_sab_key" placeholder="'+keyPh(c.sab.key_set,'key')+'" autocapitalize="off" autocorrect="off" autocomplete="off"></div>';
  x+='<div class="sec" style="margin-top:6px"><span>INDEXERS</span><button data-cadd>+ ADD</button></div>';
  c.indexers.forEach((ix,i)=>{x+='<div class="cbox"><h3><span>'+esc(ix.name||'New indexer')+'</span><span style="display:flex;gap:6px"><button class="btn" data-ctest="indexer" data-i="'+i+'">Test</button><button class="btn" data-cdel="'+i+'">Remove</button></span></h3>'+
     '<label class="fld">Name</label><input class="inp" data-ix="'+i+'" data-f="name" value="'+esc(ix.name)+'">'+
     '<label class="fld">Address</label><input class="inp" data-ix="'+i+'" data-f="url" value="'+esc(ix.url)+'" placeholder="https://api.example.com" autocapitalize="off" autocorrect="off" inputmode="url">'+
     '<label class="fld">API key</label><input class="inp" data-ix="'+i+'" data-f="api_key" placeholder="'+keyPh(ix.key_set,'key')+'" autocapitalize="off" autocorrect="off" autocomplete="off">'+
     '<label class="sw" style="border:0;padding-bottom:0"><span>Search this indexer</span><input type="checkbox" data-ix="'+i+'" data-f="enabled"'+(ix.enabled?' checked':'')+'></label></div>'});
  if(!c.indexers.length)x+='<div class="list"><div class="empty">No indexers yet. Tap + ADD.</div></div>';
  x+='<div class="cbox" style="margin-top:14px"><h3>Kodi on the TV <button class="btn" data-ctest="kodi">Test</button></h3><label class="fld">Address</label><input class="inp" id="c_kodi_url" value="'+esc(c.kodi.url)+'" placeholder="http://192.168.68.62:8080" autocapitalize="off" autocorrect="off" inputmode="url">'+
     '<label class="fld">User name</label><input class="inp" id="c_kodi_user" value="'+esc(c.kodi.username)+'" autocapitalize="off" autocorrect="off">'+
     '<label class="fld">Password</label><input class="inp" id="c_kodi_pw" type="password" placeholder="'+keyPh(c.kodi.password_set,'password')+'" autocomplete="off"></div>';
  x+='<p class="hint" style="text-align:left">Jellyfin and the TV\'s power settings are in the classic app (Jellyfin and Kodi tabs).</p>';
  x+='<button class="btn r wide" id="csave" style="margin-top:6px">Save</button><button class="btn wide" id="cclose" style="margin-top:8px">Close</button>';
  $('panel').innerHTML=x;$('cclose').onclick=()=>$('sheet').classList.add('hidden');$('csave').onclick=saveConn}
function readConn(){const c=CONN;c.sab.url=$('c_sab_url').value;c.sab.key=$('c_sab_key').value;c.kodi.url=$('c_kodi_url').value;c.kodi.username=$('c_kodi_user').value;c.kodi.pw=$('c_kodi_pw').value;
  document.querySelectorAll('[data-ix]').forEach(el=>{const ix=c.indexers[Number(el.dataset.ix)];ix[el.dataset.f]=el.type==='checkbox'?el.checked:el.value})}
async function saveConn(){readConn();const c=CONN;const body={sab_url:c.sab.url,sab_api_key:(c.sab.key||'').trim(),kodi_url:c.kodi.url,kodi_username:c.kodi.username,kodi_password:c.kodi.pw||'',
    indexers:c.indexers.map(ix=>({index:ix.orig,name:ix.name,url:ix.url,enabled:!!ix.enabled,api_key:(ix.api_key||'').trim()}))};
  try{const r=await api('/api/connections',body);toast(r.warning?'Saved, but: '+r.warning:'Saved. I\'ll use the new settings from now on, '+NAME()+'.',!!r.warning);$('sheet').classList.add('hidden');CONN=null;refresh()}catch(e){toast(e.message,true)}}
async function testConn(what,i){readConn();const c=CONN;let body={what};
  if(what==='sab')body={what,url:c.sab.url,key:(c.sab.key||'').trim()};
  if(what==='kodi')body={what,url:c.kodi.url,username:c.kodi.username,password:c.kodi.pw||''};
  if(what==='indexer'){const ix=c.indexers[i];body={what,index:ix.orig,url:ix.url,key:(ix.api_key||'').trim()}}
  toast('Testing…');try{const r=await api('/api/connections/test',body);toast(r.message,!r.ok)}catch(e){toast(e.message,true)}}
document.addEventListener('click',e=>{const b=e.target.closest('[data-ctest],[data-cadd],[data-cdel],[data-conn]');if(!b)return;const d=b.dataset;
  if('conn' in d){openConnections();return}
  if(!CONN)return;
  if(d.ctest){testConn(d.ctest,Number(d.i));return}
  if('cadd' in d){readConn();CONN.indexers.push({index:null,orig:null,name:'',url:'',enabled:true,key_set:false});renderConn();return}
  if(d.cdel!==undefined){readConn();const ix=CONN.indexers[Number(d.cdel)];if(!confirm('Remove '+(ix.name||'this indexer')+'? It\'s only removed when you tap Save.'))return;CONN.indexers.splice(Number(d.cdel),1);renderConn();return}
});

function openSettings(){const sw=(id,t,sub,on)=>'<label class="sw"><span>'+t+(sub?'<small>'+sub+'</small>':'')+'</span><input type="checkbox" id="'+id+'"'+(on?' checked':'')+'></label>';
  $('panel').innerHTML='<h2 style="margin:0 0 4px">Settings</h2><p class="hint" style="text-align:left;margin:0 0 10px">“I can give you my complete assurance that everything is connected, '+esc(NAME())+'.”</p>'+
   (CAN_SPEAK?sw('stalk','Talk to me','HAL speaks on this phone after your first tap',talkative()):'')+
   '<div class="list" style="margin-top:12px"><a class="row" href="/hal/?tab=log&settings=1&from=app" style="color:inherit;text-decoration:none"><span class="t"><b>HAL: name, alerts, UPS, voice</b><span>HAL\'s own settings</span></span><span class="m">›</span></a>'+
   '<button class="row" data-update><span class="t"><b>Update everything</b><span>Newest version of the apps, from GitHub'+(S&&S.update&&S.update.result?' · last: '+esc(S.update.result.state==='updated'?'v'+S.update.result.version:S.update.result.state==='uptodate'?'up to date':'failed')+' '+esc(ago(S.update.result.t)):'')+'</span></span><span class="m">›</span></button>'+
   '<a class="row" href="/" style="color:inherit;text-decoration:none"><span class="t"><b>Classic NZB app</b><span>Indexers, saved searches, phone alerts, drives</span></span><span class="m">›</span></a>'+
   '<button class="row" data-conn><span class="t"><b>Connections</b><span>SABnzbd, your indexers and Kodi</span></span><span class="m">›</span></button></div>'+
   '<button class="btn wide" id="sclose" style="margin-top:12px">Close</button><button class="btn wide" id="sforget" style="margin-top:8px;background:none;color:var(--muted)">Forget the access code on this device</button>';
  $('sheet').classList.remove('hidden');$('sclose').onclick=()=>$('sheet').classList.add('hidden');
  if($('stalk'))$('stalk').onchange=e=>{LS.set('hal_talk',e.target.checked?'on':'off');if(!e.target.checked&&CAN_SPEAK)speechSynthesis.cancel();render()};
  $('sforget').onclick=()=>{LS.del('app_token');token='';$('sheet').classList.add('hidden');showPair()}}
$('sheet').onclick=e=>{if(e.target===$('sheet'))$('sheet').classList.add('hidden')};

/* ---------- clicks ---------- */
function doSearch(q){if(!q)return;lastQuery=q;searching=true;render();
  api('/api/search',{query:q,indexer:'*',category:getCat}).then(d=>{results=d.results||[];searching=false;if(d.errors&&d.errors.length)toast(d.errors[0],true);render()})
    .catch(err=>{searching=false;toast(err.message,true);render()})}
document.addEventListener('submit',e=>{if(e.target.id!=='sform')return;e.preventDefault();doSearch($('sq').value.trim())});
document.addEventListener('click',e=>{const c=e.target.closest('button,a,[data-hal]');if(!c)return;const d=c.dataset;
  if(d.snooze!==undefined||d.unsnooze!==undefined){const off=d.unsnooze!==undefined;api('/hal/api/snooze',{keys:(off?d.unsnooze:d.snooze).split('|'),hours:24,off})
    .then(r=>{toast(r.message,!r.ok);refresh()}).catch(err=>toast(err.message,true));return}
  if('showhidden' in d){showHidden=!showHidden;render();return}
  if('update' in d){$('sheet').classList.add('hidden');startUpdate();return}
  if(d.tab){show(d.tab);return}
  if('settings' in d){openSettings();return}
  if('alerts' in d){location.href='/hal/?tab=log&from=app';return}
  if(d.hal){location.href='/hal/?tab='+encodeURIComponent(d.hal)+'&from=app';return}
  if('talk' in d){LS.set('hal_talk',talkative()?'off':'on');if(!talkative()&&CAN_SPEAK)speechSynthesis.cancel();else{unlocked=true;talk($('heye'),$('hquote'),'Thank you, '+NAME()+'. I\'ll talk to you from now on.',true)}render();return}
  if(c.id==='heye'){if(voiceOn){speechSynthesis.cancel();voiceOn=false;return}unlocked=true;talk(c,$('hquote'),greeting()+' '+lastQuote,CAN_SPEAK);return}
  if(d.q){act(()=>api('/api/queue/'+d.q,{}),d.q==='pause'?'Downloads paused.':'Downloads resumed.');return}
  if(d.qi){if(d.qi==='delete'&&!confirm('Remove '+(d.name||'this download')+' from the queue?'))return;act(()=>api('/api/queue/item',{id:d.id,action:d.qi}),d.qi==='delete'?'Removed.':null);return}
  if(d.add){c.disabled=true;c.textContent='Adding…';api('/api/add',{id:d.add,category:'',priority:''}).then(r=>{const it=(results||[]).find(x=>x.id===d.add);if(it)it.added=true;toast('Added'+(r.category?' to '+r.category:'')+'. I\'ll let you know when it\'s ready, '+NAME()+'.');render();loadQueue()}).catch(err=>{c.disabled=false;c.textContent='+ Add';toast(err.message,true)});return}
  if('clear' in d){results=null;lastQuery='';render();return}
  if(d.cat!==undefined){getCat=d.cat;LS.set('app_cat',getCat);const q=($('sq')&&$('sq').value.trim())||lastQuery;if(q&&(results||searching))doSearch(q);else render();return}
  if(d.sort){sortBy=d.sort==='size'?(sortBy==='size_desc'?'size_asc':'size_desc'):d.sort;LS.set('app_sort',sortBy);render();return}
  if(d.seg){getSeg=d.seg;render();if(d.seg==='upcoming')loadUpcoming();return}
  if(d.play){c.disabled=true;act(()=>api('/api/home/play',{id:d.play}),r=>r&&r.started_tv?'Turning the TV on. It\'ll start in a moment.':'Starting it on the TV.');return}
  if(d.ep){act(()=>api('/api/kodi/play',{episodeid:Number(d.ep)}),'Starting it on the TV.');return}
  if(d.movie){act(()=>api('/api/kodi/play',{movieid:Number(d.movie)}),'Starting it on the TV.');return}
  if(d.kc){act(()=>api('/api/kodi/control',{action:d.kc,playerid:Number(d.pid)}));return}
  if(d.jf){act(()=>api('/api/jellyfin/control',{session:d.sid,action:d.jf}));return}
  if('tvon' in d){act(()=>api('/api/tv/on',{kodi:true}),'Turning the TV on and opening Kodi.');return}
});

function start(){$('app').classList.remove('hidden');$('tabs').classList.remove('hidden');$('side').classList.remove('hidden');refresh();loadUpcoming();
  if(!start.t)start.t=setInterval(()=>{if(document.hidden||!token)return;refresh();if(tab==='get'&&getSeg==='queue')loadQueue();if(tab==='watch'&&Date.now()-(loadKodi.t||0)>30000){loadKodi.t=Date.now();loadKodi()}},4000)}
document.addEventListener('visibilitychange',()=>{if(!document.hidden&&token)refresh()});
(function pairFromLink(){const m=(location.hash||'').match(/^#k=([A-Za-z0-9_-]+)$/);if(m){token=m[1];LS.set('app_token',token);try{history.replaceState(null,'',location.pathname)}catch(e){}}})();
if(token)start();else showPair();
</script></body></html>"""
