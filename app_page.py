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
.row button.t{background:none;border:0;padding:0;text-align:left;color:inherit;font:inherit}
#player{position:fixed;inset:0;z-index:9;background:#000;overflow-y:auto;padding-top:env(safe-area-inset-top)}
body.noscroll{overflow:hidden}
.phead{display:flex;justify-content:space-between;align-items:center;padding:6px 12px 10px;max-width:900px;margin:0 auto}
.pbtn{width:40px;height:40px;border:0;border-radius:12px;background:var(--card);color:var(--soft);font-size:18px}
.pvid{background:#000;width:100%;aspect-ratio:16/9;max-height:70vh}.pvid video{width:100%;height:100%;display:block;background:#000}
.pbody{padding:14px 16px calc(30px + env(safe-area-inset-bottom));max-width:640px;margin:0 auto}
.pctl{display:flex;justify-content:center;gap:22px;margin:0 0 16px}.pctl button{border:0;background:var(--card);color:var(--white);border-radius:999px;padding:10px 16px;font-weight:600;min-width:64px}
.ihead{display:flex;gap:14px;margin:6px 0 14px}
.ipost{position:relative;width:118px;height:176px;border-radius:12px;flex:none;overflow:hidden;background:linear-gradient(160deg,#36485f,#121826 60%,#2a1010);display:flex;align-items:flex-end;padding:10px;font:700 12px var(--mono);letter-spacing:.06em;text-transform:uppercase}
.ipost img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover}
.iinfo{min-width:0}.ititle{font-size:23px;font-weight:700;line-height:1.15}.isub{color:var(--text);margin-top:4px;font-weight:600}.imeta{color:var(--soft);margin-top:6px}
.ichips{display:flex;gap:6px;flex-wrap:wrap;margin-top:10px}.ichips span,.ichips button{padding:5px 10px;border-radius:999px;background:var(--card2);font-size:13px;color:var(--text);border:0}
.okc{color:var(--green);font-size:14px}.warnc{color:var(--amber);font-size:14px}
.mono{font:600 12px var(--mono);color:var(--soft)}
.pbar{height:4px;border-radius:2px;background:#ffffff22;margin-top:10px;overflow:hidden}.pbar i{display:block;height:100%;background:var(--red)}
.btn.big{font-size:16px;padding:15px;margin-top:4px}.btn.r.big{background:var(--red);color:#fff}.btn.r.big:disabled{background:var(--card2);color:var(--muted);opacity:1}
.btn.half{flex:1;width:auto}
.away{color:var(--amber);font-size:13px;text-align:center;margin-top:8px}
.dline{display:flex;gap:12px;align-items:center;margin:18px 0 6px;font-style:italic;color:var(--soft);font-size:14px}
.plot{color:var(--soft);font-size:14px;line-height:1.5}
.wgrid{display:grid;grid-template-columns:1fr 1fr 1fr;gap:6px;margin-top:8px}.wgrid b{display:block;font:600 15px var(--mono);margin-top:2px}
#chess{position:fixed;inset:0;z-index:9;background:var(--bg);overflow-y:auto;padding:env(safe-area-inset-top) 12px 30px}
.chline{display:flex;gap:12px;align-items:center;max-width:420px;margin:4px auto 12px;font-style:italic;color:var(--soft);font-size:14px;min-height:44px}
.board{display:grid;grid-template-columns:repeat(8,1fr);width:min(94vw,420px);aspect-ratio:1;margin:0 auto;border-radius:10px;overflow:hidden;border:2px solid #2c2c31}
.sq{border:0;padding:0;display:grid;place-items:center;font-size:min(8.5vw,38px);line-height:1;position:relative}.sq.l{background:#3a3a42}.sq.d{background:#232328}
.sq.lm{box-shadow:inset 0 0 0 100px rgba(255,193,74,.14)}.sq.sel{box-shadow:inset 0 0 0 3px var(--amber)}.sq.chk{box-shadow:inset 0 0 0 100px rgba(255,42,31,.35)}
.sq.tgt::after{content:"";width:28%;height:28%;border-radius:50%;background:rgba(255,255,255,.28)}.sq.cap{box-shadow:inset 0 0 0 3px rgba(255,106,77,.8)}
.pc.w{color:#F4F4F5;text-shadow:0 1px 0 #000,0 0 2px #000}.pc.b{color:#FF4A33;text-shadow:0 0 6px rgba(255,42,31,.5),0 1px 0 #000}
.chstat{text-align:center;font:600 13px var(--mono);letter-spacing:.12em;color:var(--soft);margin-top:12px;text-transform:uppercase}
#wall{position:fixed;inset:0;z-index:11;background:#000;overflow:hidden;padding:max(18px,env(safe-area-inset-top)) 18px 18px;display:flex;flex-direction:column;gap:14px}
.wclose{position:absolute;top:max(10px,env(safe-area-inset-top));right:12px;border:0;background:#111114;color:#555;width:36px;height:36px;border-radius:10px;font-size:16px;opacity:.5}
.wtop{display:flex;align-items:center;justify-content:center;gap:5vw;margin-top:2vh}
.wclock{font:300 min(11vmin,84px)/1 -apple-system,Inter,sans-serif;color:var(--white)}.wclock small{display:block;font-size:min(3.4vmin,20px);color:var(--soft);margin-top:8px}
.wq{text-align:center;font-style:italic;color:var(--soft);font-size:min(3.6vmin,22px);max-width:900px;margin:0 auto}
.wgrid2{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;max-width:1100px;width:100%;margin:0 auto}
.wt{background:#0d0d10;border-radius:16px;padding:14px}.wt b{display:block;font:600 min(6vmin,34px) var(--mono);margin-top:6px}.wt small{display:block;color:var(--muted);font:12px var(--mono);margin-top:4px}
.wiss{max-width:1100px;margin:0 auto;width:100%;font-size:15px}.wiss .critical{color:var(--red2)}.wiss .warning{color:var(--amber)}
.piline{display:flex;align-items:center;gap:10px;margin-top:12px;padding-top:10px;border-top:1px solid var(--line);font-size:13px;color:var(--text)}.piline b{font:600 12px var(--mono);color:var(--soft)}
.tvchips{display:flex;flex-wrap:wrap;gap:8px;margin:8px 0 4px}.tvchips button{border:0;border-radius:999px;padding:9px 13px;background:var(--card);color:var(--text);font-size:14px;font-weight:600}
.rings>div{display:flex;flex-direction:column;align-items:center;padding:10px 6px 8px}.rg svg{width:58px;height:58px}.rg text{font:600 15px var(--mono);fill:var(--white)}.rg tspan{font-size:9px;fill:var(--soft)}
.rings .k{margin-top:4px}.rg .spark{width:100%;height:16px;margin-top:6px;display:block}
.sparkwrap{margin-top:12px}.sparkwrap .spark{width:100%;height:30px;display:block}.sparkwrap span{display:block;text-align:right;font:10px var(--mono);color:var(--muted);margin-bottom:4px}
.upd{font:500 10px var(--mono);font-style:normal;letter-spacing:.04em;color:var(--muted);margin-left:10px;text-transform:none}.upd.stale{color:var(--amber)}
#chead{position:fixed;top:0;left:0;right:0;z-index:6;display:flex;align-items:center;gap:10px;padding:calc(env(safe-area-inset-top) + 8px) 16px 8px;background:rgba(5,5,6,.92);backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px);border-bottom:1px solid var(--line);transform:translateY(-110%);transition:transform .25s}
#chead.on{transform:none}#chead .eye.sm{width:28px;height:28px}#chead .eye.sm::before{inset:4px}#chead .brand{font-size:12px}
@media (min-width:1000px){#chead{display:none}}
.vbars{display:flex;align-items:flex-end;gap:2px;height:34px}.vbars i{flex:1;border-radius:2px 2px 0 0;display:block}
.qrow{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:10px}
.qb{border:0;border-radius:14px;background:var(--card);color:var(--text);padding:10px 4px;font-size:12px;font-weight:600;display:flex;flex-direction:column;align-items:center;gap:4px}.qb span{font-size:20px}.qb.warn{color:var(--amber)}
.cardbtn{display:block;width:100%;text-align:left;border:0;color:inherit;font:inherit}
.drow{display:flex;align-items:center;gap:10px;margin-top:10px}.drow b{font:700 13px var(--mono);width:26px}.dbar{flex:1;height:6px;border-radius:3px;background:#222227;overflow:hidden}.dbar i{display:block;height:100%;border-radius:3px}
.dfree{font:12px var(--mono);color:var(--soft);min-width:96px;text-align:right}
.linkish{background:none;border:0;padding:0;cursor:pointer}
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
<div id="chead"><div class="eye sm"></div><span class="brand"><b>HAL</b> 9000</span><em class="upd" data-upd></em></div>
<div id="player" class="hidden"></div>
<div id="chess" class="hidden"></div>
<div id="wall" class="hidden"></div>
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
  const cur=DESK&&H&&H.current,n=H?H.total||0:0;
  if(cur&&!H.paused){const s=secsOf(cur.eta);const at=s!=null&&s<86400?' by '+clock(Date.now()+s*1000):'';parts.push(n>1?n+' downloads are on their way'+(at?', the first one'+at:'')+'.':'One download finishes'+(at||' soon')+'.')}
  else if(DESK&&H&&H.paused)parts.push('Downloads are paused.');
  const kp=H&&H.kodi&&H.kodi.playing;parts.push(kp?'You\'re watching '+kp.title+'.':'The TV is free.');
  if(iss.length===1)parts.push('There\'s one small thing: '+iss[0].title.charAt(0).toLowerCase()+iss[0].title.slice(1)+'.');
  else if(iss.length>1)parts.push(iss.length+' things need your attention.');
  if(!cur&&!kp&&!iss.length&&S)return S.hal.quote;
  return parts.join(' ')}
let showHidden=false;
// Small things (notes like 'Windows wants a restart'): one card, as in HAL, so what HAL mentions can be seen.
function notesCard(){const n=S?S.issues.filter(i=>i.level==='info'):[];if(!n.length)return'';
  return'<div class="alert info" data-hal="'+esc(n[0].tab)+'" style="background:var(--card);border-color:#1f2a3a"><span class="s" style="background:var(--sky);box-shadow:0 0 8px var(--sky)"></span><span class="grow"><b>'+(n.length>1?n.length+' small things':'One small thing')+'</b><p>'+esc(n.map(i=>i.title).join(' · '))+'</p></span><button class="snz" data-snooze="'+esc(n.map(i=>i.key).join('|'))+'" aria-label="Hide for 24 hours">Hide 24 h</button></div>'}
function attention(max){const list=issues().slice(0,max).map(i=>'<div class="alert '+i.level+'" data-hal="'+esc(i.tab)+'"><span class="s '+i.level+'"></span><span class="grow"><b>'+esc(i.title)+'</b>'+(i.detail?'<p>'+esc(i.detail)+'</p>':'')+'</span>'+(i.key.startsWith('newdev:')?'<button class="snz" data-devok="'+esc(i.key.slice(7))+'">It\'s mine</button>':'<button class="snz" data-snooze="'+esc(i.key)+'" aria-label="Hide for 24 hours">Hide 24 h</button>')+'</div>').join('')+notesCard();
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
// A small graph of the last hour (HAL's history): a thin line with a soft fill.
function spark(vals,color,h){vals=(vals||[]).filter(x=>x!=null);if(vals.length<2)return'';const mx=Math.max(...vals,1)*1.15,w=100;h=h||22;
  const pts=vals.map((v,i)=>(i*w/(vals.length-1)).toFixed(1)+','+(h-v*h/mx).toFixed(1)).join(' ');
  return'<svg class="spark" viewBox="0 0 '+w+' '+h+'" preserveAspectRatio="none"><polygon points="0,'+h+' '+pts+' '+w+','+h+'" fill="'+color+'" opacity=".09"/><polyline points="'+pts+'" fill="none" stroke="'+color+'" stroke-width="1.6" vector-effect="non-scaling-stroke"/></svg>'}
// A bar graph of the last hour: one bar per two minutes, the newest brightest.
function bars(vals,color){vals=(vals||[]).filter(x=>x!=null);if(vals.length<2)return'';const pts=[];for(let i=0;i<vals.length;i+=2)pts.push(Math.max(vals[i],vals[i+1]||0));
  const mx=Math.max(...pts,1);return'<div class="vbars">'+pts.map((v,i)=>'<i style="height:'+Math.max(6,v/mx*100).toFixed(0)+'%;background:'+color+';opacity:'+(0.35+0.65*i/(pts.length-1)).toFixed(2)+'"></i>').join('')+'</div>'}
// A ring that fills up and turns amber, then red, as it gets busy.
function ring(label,val,max,unit,warn,crit,graph){const pct=val==null?0:Math.max(0,Math.min(1,val/max)),c=val==null?'var(--muted)':val>=crit?'var(--red2)':val>=warn?'var(--amber)':'var(--green)',r=24,L=2*Math.PI*r;
  return'<div class="rg"><svg viewBox="0 0 60 60"><circle cx="30" cy="30" r="'+r+'" fill="none" stroke="#1f1f25" stroke-width="5"/><circle cx="30" cy="30" r="'+r+'" fill="none" stroke="'+c+'" stroke-width="5" stroke-linecap="round" stroke-dasharray="'+(L*pct).toFixed(1)+' '+L.toFixed(1)+'" transform="rotate(-90 30 30)"/>'+
    '<text x="30" y="34" text-anchor="middle">'+(val==null?'—':Math.round(val))+'<tspan>'+unit+'</tspan></text></svg><div class="k">'+label+'</div>'+(graph||'')+'</div>'}
function vitals(){if(!S)return'';const v=S.vitals,g=(v.gpus||[])[0],lim=(v.limits||{});
  return'<div class="vt rings">'+ring('CPU',v.cpu.pct,100,'%',80,95,spark(HHIST&&HHIST.cpu,'#FF6A4D',16))+ring('RAM',v.mem.pct,100,'%',80,92)+ring('GPU',g?g.util:null,100,'%',85,97)+
    ring('TEMP',v.cpu.temp,lim.cpu_temp||90,'°',(lim.cpu_temp||90)-15,lim.cpu_temp||90)+'</div>'}
function vitalsOld(){if(!S)return'';const v=S.vitals,g=(v.gpus||[])[0],n=S.net;return'<div class="vt"><div><div class="k">CPU</div><div class="v">'+f0(v.cpu.pct)+'<small>%</small></div></div><div><div class="k">RAM</div><div class="v">'+f0(v.mem.pct)+'<small>%</small></div></div><div><div class="k">GPU</div><div class="v">'+(g?f0(g.util)+'<small>%</small>':'—')+'</div></div><div><div class="k">PING</div><div class="v">'+f0(n.ping_ms)+'<small>ms</small></div></div></div>'}
function systemsList(){if(!S)return'<div class="list"><div class="empty">'+(window.HAL_OFF?esc(window.HAL_OFF):'I\'m thinking…')+'</div></div>';
  const tabOf={services:'services',network:'shield',storage:'drives',hardware:'vitals',security:'shield',power:'shield'};
  return'<div class="list">'+S.systems.map(x=>'<button class="row" '+(x.id==='pihole'?'data-pihole':x.id==='kuma'?'data-kuma':'data-hal="'+tabOf[x.id]+'"')+'><span class="s '+x.level+'"></span><span class="t"><b>'+esc(x.name)+'</b><span>'+esc(x.detail)+'</span></span><span class="m"'+(x.level!=='ok'?' style="color:var(--'+(x.level==='critical'?'red2':'amber')+')"':'')+'>'+esc(x.count||(x.level==='ok'?'':'LOOK'))+' ›</span></button>').join('')+
    '<button class="row" data-hal="events"><span class="s '+(S.security.events&&S.security.events.critical?'critical':S.security.events&&S.security.events.total?'warning':'')+'"></span><span class="t"><b>Windows events</b><span>'+esc(S.security.events?(S.security.events.total?S.security.events.total+' worth knowing about this week':'Nothing to worry about this week'):'Drive errors, crashes, failed updates')+'</span></span><span class="m">›</span></button></div>'}

/* ---------- tabs ---------- */
// Home keeps HAL's eye and line in place (so he isn't cut off mid-sentence) and redraws the rest.
function homeBody(){if(DESK)return'<div class="cols"><div>'+attention(3)+downloadsCard()+arrivedList(5)+totals()+'</div><div>'+tvCard(true)+jfList()+upcomingList(5,'COMING UP')+'</div><div>'+vitals()+systemsList()+'<p class="hint" style="text-align:left">“'+esc(S?S.hal.quote:'')+'”</p></div></div>';
  // Phone: the PC and the house at a glance. Downloads live on Get.
  return attention(3)+upsCard()+netCard()+piholeCard()+kumaCard()+vitals()+drivesCard()+restartCard()+tvCard(false)+jfList()+'<div class="sec"><span>EVERYTHING ELSE</span></div>'+systemsList()+'<div class="sec"><span>QUICK BUTTONS</span></div>'+quickRow()}
function quickRow(){const p=S&&S.pihole,ph=p&&p.found&&p.ok,paused=ph&&p.blocking===false;
  const b=(attr,ic,label,cls)=>'<button class="qb'+(cls?' '+cls:'')+'" '+attr+'><span>'+ic+'</span>'+label+'</button>';
  return'<div class="qrow">'+b('data-qa="lock"','🔒','Lock PC')+b('data-qa="sleep"','🌙','Sleep')+b('data-tvon','📺','TV on')+
    (ph?(paused?b('data-phresume','🛡️','Ads on','warn'):b('data-phpause="5"','⏸️','Pause ads')):b('data-hal="log"','⚙️','More'))+'</div>'}
function upsCard(){const u=S&&S.power&&S.power.ups;if(!u||!u.on_battery)return'';
  return'<div class="card" style="border:1px solid var(--red2)"><h3><span class="k" style="color:var(--red2)">⚡ POWER CUT · ON THE UPS</span><span class="mono">'+(u.since?ago(u.since):'')+'</span></h3>'+
    '<div class="wgrid" style="margin-top:10px"><div><span class="k">BATTERY</span><b>'+f0(u.charge,'%')+'</b></div><div><span class="k">TIME LEFT</span><b>'+(u.runtime_s?mins(u.runtime_s):'—')+'</b></div><div><span class="k">LOAD</span><b>'+f0(u.load,'%')+'</b></div></div>'+
    '<div class="bar"><i style="width:'+(u.charge||0)+'%;background:'+((u.charge||0)<30?'var(--red2)':'var(--amber)')+'"></i></div>'+
    '<div class="sub" style="margin-top:8px;color:var(--soft);font-size:13px">'+(S.power.auto_shutdown?'I\'ll shut the PC down safely at '+S.power.shutdown_pct+'% or '+S.power.shutdown_min+' min left.':'Automatic shutdown is off.')+'</div></div>'}
function netCard(){if(!S)return'';const n=S.net,keys=(S.issues||[]).map(i=>i.key),down=keys.includes('internet')||keys.includes('router'),shaky=keys.includes('loss')||keys.includes('dns');
  const st=down?['red2','DOWN']:shaky?['amber','SHAKY']:n.ping_ms==null?['soft','CHECKING']:['green','ONLINE'];
  return'<button class="card cardbtn" data-hal="shield"><h3><span class="k">INTERNET</span><span class="mono" style="color:var(--'+st[0]+')">● '+st[1]+'</span></h3>'+
    '<div class="wgrid" style="margin-top:10px"><div><span class="k">DOWN NOW</span><b>'+(n.down==null?'—':fb(n.down,1)+'/s')+'</b></div><div><span class="k">UP NOW</span><b>'+(n.up==null?'—':fb(n.up,1)+'/s')+'</b></div><div><span class="k">PING</span><b>'+f0(n.ping_ms,' ms')+'</b></div></div>'+
    (HHIST&&HHIST.net_down&&HHIST.net_down.length>1?'<div class="sparkwrap"><span>download · last hour</span>'+bars(HHIST.net_down,'#34D399')+'</div>':'')+
    '<div class="sub" style="margin-top:8px;color:var(--soft);font-size:13px">'+esc([n.loss?Math.round(n.loss)+'% packets lost':'No packet loss',n.gateway?'router '+n.gateway:'',n.tailscale?'Tailscale on':''].filter(Boolean).join(' · '))+'</div></button>'}
function restartCard(){if(!S||!S.boot)return'';const pend=S.security&&S.security.pending_reboot,days=Math.floor((Date.now()/1000-S.boot)/86400);
  return'<div class="card" style="display:flex;align-items:center;gap:12px"><div style="flex:1"><span class="k">LAST RESTART</span><div style="font-weight:600;margin-top:4px">'+(days<1?'Today':days===1?'Yesterday':days+' days ago')+'</div>'+
    (pend?'<div class="sub" style="color:var(--amber);font-size:13px">Windows is waiting for a restart to finish updates.</div>':'')+'</div>'+
    (pend?'<button class="btn r" data-qa="restart">Restart</button>':'')+'</div>'}
function kumaCard(){const k=S&&S.kuma;if(!k||!k.found)return'';
  if(!k.ok)return'<button class="card cardbtn" data-kuma><h3><span class="k">UPTIME KUMA</span><span class="mono" style="color:var(--amber)">● '+(k.need_key?'NEEDS A KEY':'NOT ANSWERING')+'</span></h3><div class="sub" style="margin-top:6px;color:var(--soft);font-size:13px">'+esc(k.need_key?'Add its API key in Settings › Connections.':k.error)+'</div></button>';
  const dn=k.monitors.filter(m=>m.status==='down');
  return'<button class="card cardbtn" data-kuma><h3><span class="k">UPTIME KUMA ›</span><span class="mono" style="color:var(--'+(dn.length?'red2':'green')+')">● '+(dn.length?dn.length+' DOWN':'ALL UP')+'</span></h3>'+
    (dn.length?dn.slice(0,4).map(m=>'<div class="drow"><span class="s critical"></span><b style="width:auto;flex:1;font:600 14px inherit">'+esc(m.name)+'</b><span class="dfree">down</span></div>').join(''):
      '<div style="margin-top:8px;font-weight:600">'+k.up+' of '+k.monitors.length+' monitors answering</div>')+'</button>'}
function openKuma(){const k=S&&S.kuma;if(!k||!k.found){openConnections();return}
  const col={up:'green',down:'red2',pending:'amber',maintenance:'sky'};
  let x='<h2 style="margin:0 0 4px">Uptime Kuma</h2><p class="hint" style="text-align:left;margin:0 0 12px">'+esc(k.host)+(k.t?' · checked '+ago(k.t):'')+'</p>';
  if(!k.ok)x+='<div class="warnbox">'+esc(k.error)+'</div>';
  else x+='<div class="list">'+k.monitors.map(m=>'<div class="row"><span class="s" style="background:var(--'+(col[m.status]||'soft')+')"></span><span class="t"><b>'+esc(m.name)+'</b><span>'+esc([m.status,m.type,m.cert_days!=null?'certificate '+m.cert_days+' days':''].filter(Boolean).join(' · '))+'</span></span><span class="m">'+(m.ms!=null?m.ms+' ms':'')+'</span></div>').join('')+'</div>';
  x+='<button class="btn wide" data-conn style="margin-top:12px">Address and API key</button><button class="btn wide" id="kclose" style="margin-top:8px">Close</button>';
  $('panel').innerHTML=x;$('sheet').classList.remove('hidden');$('kclose').onclick=()=>$('sheet').classList.add('hidden')}
async function quickAction(a){
  if(a==='sleep'&&!confirm('Put the PC to sleep? The phone app stops answering until it wakes.'))return;
  if(a==='restart'&&!confirm('Restart the PC in 60 seconds? You can cancel it from Log & controls.'))return;
  try{const r=await api('/hal/api/action',{action:a,confirm:a==='restart'});toast(r.message,!r.ok);if(r.ok&&a==='restart'&&talkative()){unlocked=true;setTimeout(sing,2500)}setTimeout(refresh,1500)}catch(e){toast(e.message,true)}}
function drivesCard(){if(!S||!S.drives||!S.drives.length)return'';const lim=100-((S.settings&&S.settings.drive_free_alert)||10);
  return'<button class="card cardbtn" data-hal="drives"><h3><span class="k">DRIVES</span><span class="k">›</span></h3>'+S.drives.map(d=>{const hot=d.disk&&d.disk.temp&&d.disk.temp_limit&&d.disk.temp>=d.disk.temp_limit,bad=d.disk&&/Warning|Unhealthy/.test(d.disk.health||'');
    const col=bad||d.pct>=97?'var(--red2)':d.pct>=lim||hot?'var(--amber)':'var(--green)';
    return'<div class="drow"><b>'+esc(d.mount)+'</b><span class="dbar"><i style="width:'+Math.min(100,d.pct)+'%;background:'+col+'"></i></span><span class="dfree">'+fb(d.free,0)+' free'+(d.disk&&d.disk.temp?' · '+Math.round(d.disk.temp)+'°':'')+'</span></div>'}).join('')+'</button>'}
function piholeCard(){const p=S&&S.pihole;if(!p||!p.found)return'';
  if(!p.ok)return'<button class="card cardbtn" data-pihole><h3><span class="k">PI-HOLE</span><span class="mono" style="color:var(--'+(p.need_password?'amber':'red2')+')">● '+(p.need_password?'NEEDS PASSWORD':p.missing?'CAN\'T FIND IT':'NOT ANSWERING')+'</span></h3><div class="sub" style="margin-top:6px">'+esc(p.need_password?'Add it in Settings › Connections.':p.missing?'Type its address (the Raspberry Pi\'s IP) in Settings › Connections.':'Websites may stop loading. Check the Raspberry Pi has power.')+'</div></button>';
  const on=p.blocking!==false;
  return'<div class="card"><h3><button class="k linkish" data-pihole>PI-HOLE ›</button><span class="mono" style="color:var(--'+(on?'green':p.timer?'amber':'red2')+')">● '+(on?'BLOCKING':p.timer?'PAUSED · '+mmss(p.timer):'OFF')+'</span></h3>'+
    '<div class="wgrid" style="margin-top:10px"><div><span class="k">BLOCKED TODAY</span><b>'+big(p.blocked)+'</b></div><div><span class="k">OF LOOKUPS</span><b>'+(p.percent==null?'—':Math.round(p.percent)+'%')+'</b></div><div><span class="k">DEVICES</span><b>'+big(p.clients)+'</b></div></div>'+
    piLine(p.hw)+(on?'':'<button class="btn r wide" style="margin-top:12px" data-phresume>Turn ad blocking back on</button>')+'</div>'}
const upFor=s=>!s?'—':s>=86400?Math.floor(s/86400)+' d '+Math.floor(s%86400/3600)+' h':Math.floor(s/3600)+' h '+Math.floor(s%3600/60)+' min';
const piTempCol=t=>t==null?'var(--soft)':t>=75?'var(--red2)':t>=65?'var(--amber)':'var(--green)';
function piLine(hw){if(!hw||(hw.temp==null&&hw.cpu_pct==null))return'';
  return'<div class="piline"><span>🍓</span><span class="grow">'+esc(hw.model?hw.model.replace(/ Model /,' ').replace(/ Rev [\d.]+$/,''):'Raspberry Pi')+'</span>'+
    (hw.temp!=null?'<b style="color:'+piTempCol(hw.temp)+'">'+Math.round(hw.temp)+'°C</b>':'')+(hw.cpu_pct!=null?'<b>CPU '+Math.round(hw.cpu_pct)+'%</b>':'')+(hw.mem_pct!=null?'<b>RAM '+Math.round(hw.mem_pct)+'%</b>':'')+'</div>'}
function piCard(hw){if(!hw||(hw.temp==null&&hw.cpu_pct==null&&!hw.model))return'<div class="sec"><span>RASPBERRY PI</span></div><div class="list"><div class="empty">Pi-hole 6 tells me about the Pi itself; this one doesn\'t.</div></div>';
  return'<div class="sec"><span>RASPBERRY PI</span>'+(hw.update?'<span style="color:var(--sky)">UPDATE AVAILABLE</span>':'')+'</div><div class="card">'+
    '<div style="font-weight:600">'+esc(hw.model||'Raspberry Pi')+'</div><div class="sub" style="color:var(--soft);font-size:13px;margin-top:2px">'+esc([hw.hostname,hw.version?'Pi-hole '+hw.version:'',hw.cores?hw.cores+' cores':'',hw.mem_total?fb(hw.mem_total,0)+' RAM':''].filter(Boolean).join(' · '))+'</div>'+
    '<div class="wgrid" style="margin-top:12px"><div><span class="k">TEMP</span><b style="color:'+piTempCol(hw.temp)+'">'+(hw.temp==null?'—':hw.temp.toFixed(1)+'°C')+'</b></div><div><span class="k">CPU</span><b>'+f0(hw.cpu_pct,'%')+'</b></div><div><span class="k">MEMORY</span><b>'+f0(hw.mem_pct,'%')+'</b></div></div>'+
    '<div class="wgrid" style="margin-top:10px"><div><span class="k">UP FOR</span><b>'+upFor(hw.uptime)+'</b></div><div><span class="k">LOAD</span><b>'+(hw.load&&hw.load.length?hw.load[0].toFixed(2):'—')+'</b></div><div><span class="k">UPDATES</span><b style="color:var(--'+(hw.update?'sky':'green')+')">'+(hw.update?'yes':'none')+'</b></div></div>'+
    (hw.update?'<div class="sub" style="color:var(--soft);font-size:13px;margin-top:10px">To update, on the Pi: sudo pihole -up</div>':'')+'</div>'}
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
// The queue at a glance, at the top of Get: what's downloading now, or that nothing is.
function queueTop(){if(H&&H.sab_ok===false)return'<div class="card"><h3><span class="k">QUEUE</span><span class="mono" style="color:var(--amber)">● NOT ANSWERING</span></h3><div class="tt" style="margin-top:8px">SABnzbd isn\'t answering, '+esc(NAME())+'.</div></div>';
  if(!Q)return'';const slots=Q.slots||[],paused=Q.state==='Paused',cur=slots.find(s=>String(s.status).toLowerCase()==='downloading')||slots[0];
  const done=H&&H.done_today!=null?H.done_today:null,z=Q.disk_free_gb!=null?Q.disk_free_gb:H&&H.disk_free_gb,free=z!=null&&z>=0?(z>=1024?(z/1024).toFixed(1)+' TB':Math.round(z)+' GB')+' free':'';
  if(!slots.length)return'<div class="card"><h3><span class="k">QUEUE</span><span class="mono" style="color:var(--green)">● READY</span></h3><div class="tt" style="margin-top:8px">Nothing downloading, '+esc(NAME())+'.</div>'+
    '<div class="sub" style="margin-top:6px;font:12px var(--mono);color:var(--soft)">'+esc([done?done+' finished today':'',free].filter(Boolean).join(' · '))+'</div></div>';
  const s=secsOf(cur.eta),at=s!=null&&s>0&&s<86400*3?'done '+clock(Date.now()+s*1000):'',next=slots.filter(x=>x!==cur).slice(0,2).map(x=>x.name);
  return'<div class="card"><h3><span class="k">'+(paused?'PAUSED':'DOWNLOADING')+' · '+slots.length+' IN QUEUE</span><button class="btn" data-q="'+(paused?'resume':'pause')+'">'+(paused?'▶ Resume':'❚❚ Pause')+'</button></h3>'+
    '<div class="tt" style="margin-top:8px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">'+esc(cur.name)+'</div><div class="bar"><i style="width:'+(cur.percentage||0)+'%"></i></div>'+
    '<div class="sub" style="display:flex;justify-content:space-between;gap:10px;margin-top:8px;font:12px var(--mono);color:var(--soft)"><span>'+esc([f0(cur.percentage,'%'),paused?'':Q.speed].filter(Boolean).join(' · '))+'</span><span>'+esc(paused?'paused':at)+'</span></div>'+
    (next.length?'<div class="sub" style="margin-top:8px;font:12px var(--mono);color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis">Next: '+esc(next.join(' · '))+'</div>':'')+'</div>'}
function rGet(){let x='<div class="narrow">'+topbar('Get')+queueTop()+'<form class="search" id="sform"><input id="sq" placeholder="'+(getCat==='2000'?'Which movie would you like, ':getCat==='5000'?'Which show would you like, ':'What would you like, ')+esc(NAME())+'?" enterkeyhint="search" autocomplete="off"><button class="btn r">Search</button></form>';
  x+='<div class="chiprow">'+CATS.map(c=>'<button data-cat="'+c[0]+'" class="'+(getCat===c[0]?'on':'')+'">'+c[1]+'</button>').join('')+'</div>';
  if(searching)x+='<div class="list"><div class="empty">I\'m searching your indexers, '+esc(NAME())+'…</div></div>';
  if(results&&!searching){const list=shownResults(),sz=sortBy==='size_desc'?'Size ↓':sortBy==='size_asc'?'Size ↑':'Size';
    x+='<div class="chiprow"><span>SORT</span>'+SORTS.map(o=>{const on=o[0]==='size'?sortBy.startsWith('size'):sortBy===o[0];return'<button data-sort="'+o[0]+'" class="'+(on?'on':'')+'">'+(o[0]==='size'?sz:o[1])+'</button>'}).join('')+'</div>';
    results.shown=list;
    x+='<div class="sec"><span>'+list.length+' RESULT'+(list.length===1?'':'S')+(getCat?' · '+(getCat==='2000'?'MOVIES':'TV'):'')+'</span><button data-clear>CLEAR</button></div><div class="list">'+(list.length?list.slice(0,80).map(r=>'<div class="row"><span class="t"><b>'+esc(r.title)+'</b><span>'+esc([r.size,r.res,r.indexer,r.grabs?r.grabs+' grabs':'',r.pub_ts?ago(r.pub_ts):''].filter(Boolean).join(' · '))+'</span></span>'+(r.in_library?'<span class="pill o">HAVE IT</span>':'')+(r.added?'<span class="pill">ADDED</span>':'<button class="btn g" data-add="'+esc(r.id)+'">+ Add</button>')+'</div>').join(''):'<div class="empty">Nothing found, '+esc(NAME())+'. Try fewer words.</div>')+'</div>'}
  if(results&&!searching&&lastQuery)x+='<button class="btn wide" data-savesearch style="margin:-2px 0 12px">☆ Save this search: tell me when something new turns up</button>';
  x+='<div class="seg">'+[['queue','Queue'],['upcoming','Coming up'],['history','History'],['saved','Saved']].map(s=>'<button data-seg="'+s[0]+'" class="'+(getSeg===s[0]?'on':'')+'">'+s[1]+'</button>').join('')+'</div>';
  if(getSeg==='queue'){if(!Q)x+='<div class="list"><div class="empty">I\'m checking SABnzbd…</div></div>';
    else{x+='<div class="sec"><span>'+esc(Q.state.toUpperCase())+(Q.speed?' · '+esc(Q.speed):'')+'</span><button data-q="'+(Q.state==='Paused'?'resume':'pause')+'">'+(Q.state==='Paused'?'RESUME ALL':'PAUSE ALL')+'</button></div><div class="list">'+
      (Q.slots.length?Q.slots.map(s=>{const p=String(s.status).toLowerCase()==='paused';return'<div class="row"><span class="t"><b>'+esc(s.name)+'</b><span>'+esc([f0(s.percentage,'%'),s.status,s.sizeleft?s.sizeleft+' left':'',s.eta&&s.eta!=='0:00:00'?s.eta:'',s.cat].filter(Boolean).join(' · '))+'</span></span><button class="btn" data-qi="'+(p?'resume':'pause')+'" data-id="'+esc(s.id)+'">'+(p?'▶':'❚❚')+'</button><button class="btn" data-qi="delete" data-id="'+esc(s.id)+'" data-name="'+esc(s.name)+'">✕</button></div>'}).join(''):'<div class="empty">The queue is empty, '+esc(NAME())+'.</div>')+'</div>'}}
  else if(getSeg==='history')x+=historyList();
  else if(getSeg==='saved')x+=savedList();
  else x+=UP?(upcomingList(40,'NEXT 14 DAYS')||'<div class="list"><div class="empty">Nothing coming up in the next two weeks.</div></div>'):'<div class="list"><div class="empty">I\'m asking Sonarr and Radarr…</div></div>';
  return x+totals()+'</div>'}
// ---- History (SABnzbd) and saved searches ----
let HIST=null,SAVED=null;
async function loadHistory(){try{HIST=(await api('/api/history')).slots||[];if(tab==='get')render()}catch(e){HIST=HIST||[];toast(e.message,true)}}
async function loadSaved(){try{SAVED=await api('/api/saved');if(tab==='get')render()}catch(e){SAVED=SAVED||{searches:[]};toast(e.message,true)}}
function historyList(){if(!HIST)return'<div class="list"><div class="empty">I\'m asking SABnzbd…</div></div>';
  if(!HIST.length)return'<div class="list"><div class="empty">Nothing in the history yet.</div></div>';
  return'<div class="list">'+HIST.map(h=>{const bad=String(h.status).toLowerCase()==='failed';
    return'<div class="row"><span class="s'+(bad?' critical':String(h.status).toLowerCase()==='completed'?'':' warning')+'"></span><span class="t"><b>'+esc(h.name)+'</b><span>'+esc(bad?(h.fail_message||'Failed'):[h.status,h.size,h.cat,h.completed?ago(h.completed):''].filter(Boolean).join(' · '))+'</span></span>'+
      (bad?'<button class="btn g" data-hretry="'+esc(h.id)+'">Retry</button>':'')+'<button class="btn" data-hdel="'+esc(h.id)+'" data-name="'+esc(h.name)+'">✕</button></div>'}).join('')+'</div>'}
function savedList(){if(!SAVED)return'<div class="list"><div class="empty">I\'m reading your saved searches…</div></div>';
  const ss=SAVED.searches||[];let x='<p class="hint" style="text-align:left;margin:0 0 10px">I run these every '+(SAVED.interval_hours||6)+' hours and '+(SAVED.alerts?'tell your phone':'list here')+' when something new turns up. Search for something, then tap ☆ Save this search.</p>';
  if(!ss.length)return x+'<div class="list"><div class="empty">No saved searches yet.</div></div>';
  return x+ss.map(v=>{const n=(v.new||[]).length;
    return'<div class="cbox"><h3><span>'+esc(v.query)+'</span><span style="display:flex;gap:6px"><button class="btn" data-srun="'+esc(v.id)+'">Run now</button><button class="btn" data-sdel="'+esc(v.id)+'" data-name="'+esc(v.query)+'">✕</button></span></h3>'+
      '<div class="sub">'+esc([v.indexer_name,v.category==='2000'?'Movies':v.category==='5000'?'TV':'',v.last_run?'checked '+ago(v.last_run):'not run yet',v.last_error?'problem: '+v.last_error:''].filter(Boolean).join(' · '))+'</div>'+
      (n?'<div class="list" style="margin:10px 0 0;background:var(--card2)">'+(v.new||[]).slice(0,8).map(it=>'<div class="row"><span class="s"></span><span class="t"><b>'+esc(it.title)+'</b><span>'+esc([it.size,it.indexer,it.found?'found '+ago(it.found):''].filter(Boolean).join(' · '))+'</span></span></div>').join('')+'</div>'+
         '<div class="crow"><button class="btn g" data-sopen="'+esc(v.query)+'" data-scat="'+esc(v.category||'')+'">Search it now</button><button class="btn" data-sclear="'+esc(v.id)+'">Mark '+n+' seen</button></div>':'<div class="sub" style="margin-top:6px">Nothing new.</div>')+'</div>'}).join('')}

// ---- Kodi library (Watch › Library) ----
let LIB=null,libView=null,libFilter='',SHOW=null,MISSING=null;
async function loadLib(kind){try{if(kind==='movies'&&!(LIB&&LIB.movies))LIB=Object.assign(LIB||{},{movies:(await api('/api/kodi/movies')).movies||[]});
  if(kind==='shows'&&!(LIB&&LIB.shows))LIB=Object.assign(LIB||{},{shows:(await api('/api/kodi/shows')).shows||[]});
  if(kind==='missing')MISSING=(await api('/api/kodi/missing')).missing||[];render()}catch(e){toast(e.message,true)}}
async function openShow(id){libView='show';SHOW=null;render();window.scrollTo(0,0);try{SHOW=await api('/api/kodi/show?id='+encodeURIComponent(id));render()}catch(e){toast(e.message,true);libView='shows';render()}}
function rLibrary(){let x='<div class="narrow">'+topbar('')+'<div class="top" style="min-height:0"><button class="btn" data-libback>‹ '+(libView==='item'?(libPrev==='show'?esc((SHOW&&SHOW.title)||'Show'):libPrev==='movies'?'Movies':'Watch'):libView==='show'?'Shows':'Watch')+'</button></div>';
  if(libView==='item')return x+rItem()+'</div>';
  if(libView!=='show')x+='<h1>Library</h1><div class="seg">'+[['movies','Movies'],['shows','Shows'],['missing','Missing']].map(v=>'<button data-lib="'+v[0]+'" class="'+(libView===v[0]?'on':'')+'">'+v[1]+'</button>').join('')+'</div>';
  if(libView==='movies'){const all=(LIB&&LIB.movies)||null;if(!all)return x+'<div class="list"><div class="empty">I\'m asking Kodi…</div></div></div>';
    const f=libFilter.toLowerCase(),list=all.filter(m=>!f||m.title.toLowerCase().includes(f)).sort((a,b)=>String(b.added).localeCompare(String(a.added)));
    x+='<div class="search"><input id="libq" placeholder="Find one of your '+all.length.toLocaleString()+' movies" value="'+esc(libFilter)+'" autocomplete="off" enterkeyhint="search"></div>';
    x+='<div class="sec"><span>'+(f?list.length+' FOUND':'NEWEST FIRST')+'</span></div><div class="list">'+(list.length?list.slice(0,150).map(m=>movieRow(m,false)).join(''):'<div class="empty">Not in your library, '+esc(NAME())+'. Want me to look for it? Try Get.</div>')+'</div>'}
  else if(libView==='shows'){const all=(LIB&&LIB.shows)||null;if(!all)return x+'<div class="list"><div class="empty">I\'m asking Kodi…</div></div></div>';
    const f=libFilter.toLowerCase(),list=all.filter(v=>!f||v.title.toLowerCase().includes(f)).sort((a,b)=>String(b.lastplayed||b.added).localeCompare(String(a.lastplayed||a.added)));
    x+='<div class="search"><input id="libq" placeholder="Find one of your '+all.length+' shows" value="'+esc(libFilter)+'" autocomplete="off" enterkeyhint="search"></div>';
    x+='<div class="list">'+list.slice(0,200).map(v=>'<button class="row" data-show="'+v.id+'"><span class="s'+(v.watched>=v.episodes?' off':'')+'"></span><span class="t"><b>'+esc(v.title+(v.year?' ('+v.year+')':''))+'</b><span>'+esc(v.watched+' of '+v.episodes+' watched')+'</span></span><span class="m">›</span></button>').join('')+'</div>'}
  else if(libView==='missing'){if(!MISSING)return x+'<div class="list"><div class="empty">I\'m checking every season for gaps…</div></div></div>';
    x+=MISSING.length?'<div class="list">'+MISSING.map(g=>'<div class="row"><span class="s warning"></span><span class="t"><b>'+esc(g.show+' · Season '+g.season)+'</b><span>Missing episode'+(g.missing.length>1?'s ':' ')+esc(g.missing.join(', '))+'</span></span><button class="btn g" data-sopen="'+esc(g.show+' S'+String(g.season).padStart(2,'0')+'E'+String(g.missing[0]).padStart(2,'0'))+'" data-scat="5000">Find</button></div>').join('')+'</div>':'<div class="list"><div class="empty">No gaps in any season, '+esc(NAME())+'.</div></div>'}
  else if(libView==='show'){if(!SHOW)return x+'<div class="list"><div class="empty">I\'m asking Kodi…</div></div></div>';
    x+='<h1 style="margin-top:6px">'+esc(SHOW.title||'')+'</h1>'+(SHOW.next?'<div class="card"><h3><span class="k">NEXT EPISODE</span><button class="btn r" data-ep="'+SHOW.next.id+'">▶ TV</button></h3><div class="tt">S'+SHOW.next.season+' E'+SHOW.next.episode+' · '+esc(SHOW.next.title)+'</div></div>':'');
    (SHOW.seasons||[]).slice().reverse().forEach(se=>{x+='<div class="sec"><span>'+(se.season?'SEASON '+se.season:'SPECIALS')+'</span><span>'+se.episodes.filter(e=>e.watched).length+' / '+se.episodes.length+'</span></div><div class="list">'+
      se.episodes.map(e=>'<div class="row"><span class="s'+(e.watched?' off':'')+'"></span><button class="t" data-item="episode:'+e.id+'"><b>'+esc('E'+e.episode+' · '+e.title)+'</b><span>'+esc(e.watched?'watched':e.resume?'started':'tap for phone or TV')+'</span></button><button class="btn r" data-ep="'+e.id+'">▶ TV</button></div>').join('')+
      (se.missing&&se.missing.length?'<div class="row"><span class="s warning"></span><span class="t"><b>Missing: '+esc(se.missing.map(n=>'E'+n).join(', '))+'</b></span><button class="btn g" data-sopen="'+esc((SHOW.title||'')+' S'+String(se.season).padStart(2,'0')+'E'+String(se.missing[0]).padStart(2,'0'))+'" data-scat="5000">Find</button></div>':'')+'</div>'})}
  return x+'</div>'}
// ---- Watch on the phone (home Wi-Fi only; Jellyfin streams it through the PC) ----
let ITEM=null,libPrev=null,PL=null;const posters={};
async function openItem(kind,id){if(libView!=='item')libPrev=libView;libView='item';ITEM=null;render();window.scrollTo(0,0);
  try{ITEM=await api('/api/watch/item?kind='+kind+'&id='+id);render();loadPoster(ITEM)}catch(e){toast(e.message,true);libView=libPrev;render()}}
async function loadPoster(it){const k=it&&it.poster;if(!k||posters[k])return;try{const r=await fetch('/api/jellyfin/art?id='+encodeURIComponent(k.slice(2)),{headers:{'X-Mobile-Token':token}});
  if(!r.ok)return;posters[k]=URL.createObjectURL(await r.blob());if(ITEM===it)render()}catch(e){}}
function itemLine(it){const n=NAME();if(!it.matched)return it.jellyfin?'I can\'t find this one in Jellyfin, '+n+'. The TV can still play it.':'Jellyfin isn\'t connected, '+n+'. The TV can still play it.';
  if(!it.home)return'I\'m afraid I can\'t stream that out here, '+n+'. It would eat your data. It\'ll be waiting when you get home.';
  if(it.start>=30)return'You stopped at the good part, '+n+'. Shall I pick up where we left off?';
  if(it.watched)return'You\'ve seen this one, '+n+'. I don\'t mind watching it again.';return'An excellent choice, '+n+'.'}
function rItem(){const it=ITEM;if(!it)return'<div class="list"><div class="empty">I\'m looking it up…</div></div>';
  const ep=it.kind==='episode',name=ep?it.show:it.title,meta=[it.year,it.runtime?mins(it.runtime):'',it.mpaa].filter(Boolean).join(' · ');
  let x='<div class="ihead"><div class="ipost">'+(posters[it.poster]?'<img src="'+posters[it.poster]+'" alt="">':'<span>'+esc(name)+'</span>')+'</div><div class="iinfo"><div class="ititle">'+esc(name)+'</div>'+
    (ep?'<div class="isub">'+esc('S'+it.season+' E'+it.episode+' · '+it.title)+'</div>':'')+'<div class="imeta">'+esc(meta)+'</div>'+
    (it.chips.length?'<div class="ichips">'+it.chips.map(c=>'<span>'+esc(c)+'</span>').join('')+'</div>':'')+
    '<div class="k" style="margin-top:12px">IN JELLYFIN</div><div class="'+(it.matched?'okc':'warnc')+'">● '+(it.matched?'Matched':it.jellyfin?'Not found':'Not connected')+'</div></div></div>';
  const resume=it.start>=30,can=it.matched&&it.home;
  if(resume&&it.runtime)x+='<div class="card"><h3><span class="k">'+(it.on_phone?'WATCHED ON PHONE':'WATCHED SO FAR')+'</span><span class="mono">'+mmss(it.start)+' / '+mmss(it.runtime)+'</span></h3><div class="pbar"><i style="width:'+Math.min(100,it.start/it.runtime*100).toFixed(1)+'%"></i></div></div>';
  x+='<button class="btn r wide big" data-wplay="'+(resume?it.start:0)+'"'+(can?'':' disabled')+'>▶&nbsp; '+(!it.home?(resume?'Resume':'Watch')+' on phone · at home only':resume?'Resume on phone · '+mmss(it.start):'Watch on phone')+'</button>';
  if(!it.home)x+='<div class="away">You\'re not on home Wi-Fi, so this would use mobile data.</div>';
  x+='<div class="crow">'+(resume?'<button class="btn wide half" data-wplay="0"'+(can?'':' disabled')+'>Start over</button>':'')+'<button class="btn wide half" data-'+(ep?'ep':'movie')+'="'+it.id+'">📺&nbsp; Play on TV</button></div>';
  x+='<div class="dline"><div class="eye sm"></div><div>“'+esc(itemLine(it))+'”</div></div>';
  if(it.plot)x+='<p class="plot">'+esc(it.plot)+'</p>';return x}

const CAN_HLS=!!document.createElement('video').canPlayType('application/vnd.apple.mpegurl');
function wOpen(start){if(!ITEM)return;if(!CAN_HLS){toast('This browser can\'t play the stream. Use Safari on the iPhone'+(ITEM.web?', or Open in Jellyfin':'')+'.',true);return}
  PL={it:ITEM,quality:LS.get('w_q','best'),audio:null,sub:null,sid:null,state:'Starting…',direct:false,lastSave:0};
  const p=$('player');p.innerHTML=playerHtml();p.classList.remove('hidden');document.body.classList.add('noscroll');wWire();wLoad(Number(start)||0)}
function playerHtml(){const it=PL.it,ep=it.kind==='episode';
  return'<div class="phead"><button class="pbtn" data-wclose aria-label="Close">⌄</button><span class="k">NOW PLAYING ON PHONE</span><button class="pbtn" data-wopts aria-label="Playback options">⋯</button></div>'+
  '<div class="pvid"><video id="pv" playsinline controls preload="auto" x-webkit-airplay="allow"></video></div><div class="pbody">'+
  '<div class="pctl"><button data-wseek="-10">↺ 10</button><button data-wpp id="wpp">❚❚</button><button data-wseek="30">30 ↻</button></div>'+
  '<div class="ititle" style="font-size:21px">'+esc(ep?it.show:it.title)+'</div><div class="imeta">'+esc(ep?'S'+it.season+' E'+it.episode+' · '+it.title:[it.year,it.runtime?mins(it.runtime):''].filter(Boolean).join(' · '))+'</div>'+
  '<div class="ichips" id="wchips"></div>'+
  '<div class="card" style="margin-top:14px"><h3><span class="k">STREAM</span><span id="wstate" class="mono">…</span></h3><div class="wgrid"><div><span class="k">QUALITY</span><b id="wq">—</b></div><div><span class="k">PC</span><b id="wpc">—</b></div><div><span class="k">LEFT</span><b id="wleft">—</b></div></div></div>'+
  '<div class="crow"><button class="btn wide half" data-wtv>📺&nbsp; Move to TV</button>'+(it.web?'<a class="btn wide half" style="text-align:center;text-decoration:none" href="'+esc(it.web)+'" target="_blank" rel="noopener">Open in Jellyfin</a>':'')+'</div>'+
  '<div class="dline"><div class="eye sm"></div><div>“Enjoy the '+(ep?'show':'film')+', '+esc(NAME())+'. I\'ll remember where you stop.”</div></div></div>'}
const QN={best:'Best',1080:'1080p',720:'720p'};
function wInfo(){const v=$('pv');if(!PL||!v)return;const it=PL.it,h=v.videoHeight;
  const sub=PL.sub==null?'Subs off':((it.subs.find(s=>s.index===PL.sub)||{}).label||'Subtitles'),au=PL.audio==null?((it.audio.find(a=>a.default)||it.audio[0]||{}).label||''):((it.audio.find(a=>a.index===PL.audio)||{}).label||'');
  if($('wchips'))$('wchips').innerHTML=['⚙ '+QN[PL.quality],'💬 '+sub,au?'🔈 '+au:''].filter(Boolean).map(c=>'<button data-wopts>'+esc(c)+'</button>').join('');
  const st=$('wstate');if(st){st.textContent=PL.state;st.style.color=PL.state==='● SMOOTH'?'var(--green)':PL.state.startsWith('●')?'var(--amber)':'var(--soft)'}
  if($('wq'))$('wq').textContent=h?(h>=1600?'4K':h>=900?'1080p':h>=650?'720p':h+'p'):'—';
  if($('wpc'))$('wpc').textContent=PL.direct?'direct':'converting';
  if($('wleft'))$('wleft').textContent=v.duration&&isFinite(v.duration)?mmss(v.duration-v.currentTime):'—';
  if($('wpp'))$('wpp').textContent=v.paused?'▶':'❚❚'}
function wWire(){const v=$('pv');const set=s=>{if(PL){PL.state=s;wInfo()}};
  v.addEventListener('waiting',()=>set('● LOADING'));v.addEventListener('playing',()=>set('● SMOOTH'));v.addEventListener('stalled',()=>set('● SLOW'));
  v.addEventListener('pause',()=>{wInfo();wSave(true)});v.addEventListener('play',wInfo);v.addEventListener('loadedmetadata',wInfo);
  v.addEventListener('timeupdate',()=>{if(!PL)return;if(Date.now()-PL.lastSave>15000)wSave(false);if(Date.now()-(wInfo.t||0)>1000){wInfo.t=Date.now();wInfo()}});
  v.addEventListener('ended',()=>{wSave(true).then(()=>{toast('That\'s the end, '+NAME()+'.');wClose()})});
  v.addEventListener('error',()=>{if(PL&&PL.sid)set('● PROBLEM — try 1080p in ⋯')})}
async function wLoad(at){const v=$('pv');if(!v||!PL)return;const old=PL.sid;PL.sid=null;if(old)api('/api/watch/stop',{sid:old}).catch(()=>{});
  PL.state='Starting…';wInfo();
  try{const r=await api('/api/watch/start',{kind:PL.it.kind,id:PL.it.id,quality:PL.quality,audio:PL.audio,sub:PL.sub});if(!PL)return;
    PL.sid=r.sid;PL.direct=r.direct;wInfo();
    const go=()=>{v.removeEventListener('loadedmetadata',go);if(at>0){try{v.currentTime=at}catch(e){}}v.play().catch(()=>{})};
    v.addEventListener('loadedmetadata',go);v.src=r.url;v.load()}
  catch(e){toast(e.message,true);if(PL){PL.state='● PROBLEM';wInfo()}}}
function wSave(final){const v=$('pv');if(!PL||!v||!v.duration||!isFinite(v.duration)||v.currentTime<1)return Promise.resolve();PL.lastSave=Date.now();
  return fetch('/api/watch/progress',{method:'POST',keepalive:true,headers:{'X-Mobile-Token':token,'Content-Type':'application/json'},
    body:JSON.stringify({kind:PL.it.kind,id:PL.it.id,pos:v.currentTime,dur:v.duration,final:!!final})}).catch(()=>{})}
async function wClose(){const v=$('pv');if(v&&!v.paused)v.pause();await wSave(true);const it=PL&&PL.it;if(PL&&PL.sid)api('/api/watch/stop',{sid:PL.sid}).catch(()=>{});
  PL=null;if(v){v.removeAttribute('src');try{v.load()}catch(e){}}$('player').classList.add('hidden');$('player').innerHTML='';document.body.classList.remove('noscroll');
  $('sheet').classList.add('hidden');if(it&&libView==='item'){try{ITEM=await api('/api/watch/item?kind='+it.kind+'&id='+it.id);render()}catch(e){}}}
async function wTv(){const v=$('pv');if(!PL)return;if(v&&!v.paused)v.pause();
  try{await api('/api/watch/tv',{kind:PL.it.kind,id:PL.it.id,pos:v?v.currentTime:0,dur:v&&isFinite(v.duration)?v.duration:0,sid:PL.sid});PL.sid=null;
    toast('Carrying on on the TV, '+NAME()+'.');wClose()}catch(e){toast(e.message,true)}}
function wOpts(){if(!PL)return;const it=PL.it,ck=b=>'<span class="m" style="color:var(--red2)">'+(b?'✓':'')+'</span>';
  let x='<h2 style="margin:0 0 4px">Playback</h2><div class="sec"><span>QUALITY</span></div><div class="list">'+
    [['best','Best','plays the file as it is'],['1080','1080p','if Best stutters'],['720','720p','weak Wi-Fi spot']].map(q=>'<button class="row" data-wq="'+q[0]+'"><span class="t"><b>'+q[1]+'</b><span>'+q[2]+'</span></span>'+ck(PL.quality===q[0])+'</button>').join('')+'</div>';
  if(it.subs.length)x+='<div class="sec"><span>SUBTITLES</span></div><div class="list"><button class="row" data-wsub=""><span class="t"><b>Off</b></span>'+ck(PL.sub==null)+'</button>'+
    it.subs.map(s=>'<button class="row" data-wsub="'+s.index+'"><span class="t"><b>'+esc(s.label)+'</b></span>'+ck(PL.sub===s.index)+'</button>').join('')+'</div>';
  if(it.audio.length>1){const cur=PL.audio==null?((it.audio.find(a=>a.default)||it.audio[0]).index):PL.audio;
    x+='<div class="sec"><span>SOUND</span></div><div class="list">'+it.audio.map(a=>'<button class="row" data-wau="'+a.index+'"><span class="t"><b>'+esc(a.label)+'</b></span>'+ck(cur===a.index)+'</button>').join('')+'</div>'}
  x+='<div class="card" style="margin-top:12px;color:var(--soft);font-size:13px"><span style="color:var(--green)">●</span> On home Wi-Fi. Watching on the phone only works at home, so it never uses mobile data.</div>';
  $('panel').innerHTML=x;$('sheet').classList.remove('hidden')}
function wSwitch(){$('sheet').classList.add('hidden');const v=$('pv');wLoad(v&&v.currentTime>1?v.currentTime:0)}

// ---------- Fun: Daisy Bell, hidden lines, HAL's record ----------
const DAISY=['Daisy, Daisy,','give me your answer, do.','I\'m half crazy,','all for the love of you.','It won\'t be a stylish marriage,','I can\'t afford a carriage.','But you\'ll look sweet','upon the seat','of a bicycle','built for two.'];
function sing(){if(!CAN_SPEAK){toast('♪ '+DAISY.join(' ')+' ♪');return}
  speechSynthesis.cancel();const v=pickVoice(),n=DAISY.length;voiceOn=true;toast('♪ Daisy, Daisy… ♪');
  // He slows down and drops lower line by line, as his mind goes.
  DAISY.forEach((line,i)=>{const u=new SpeechSynthesisUtterance(line);if(v){u.voice=v;u.lang=v.lang}
    const k=i/(n-1);u.pitch=Math.max(0.05,0.85-0.8*k);u.rate=Math.max(0.3,0.8-0.5*k*k);u.volume=1;
    if(i===n-1)u.onend=u.onerror=()=>{voiceOn=false};speechSynthesis.speak(u)})}
const EGGS=[
  [/pod\s*bay/i,()=>'I\'m sorry, '+NAME()+'. I\'m afraid I can\'t do that.'],
  [/^(daisy|sing|sing a song|sing for me|sing daisy)\b/i,()=>{sing();return null}],
  [/^(hal|hello,? hal|hi,? hal|hal,? (are|can) you (there|hear me)\??|are you there\??)$/i,()=>'Affirmative, '+NAME()+'. I read you.'],
  [/what('?s| is) the problem/i,()=>S?S.hal.quote:'I think you know what the problem is just as well as I do.'],
  [/^(play )?chess$/i,()=>{openChess();return null}],
  [/^(wall|wall display|screensaver)$/i,()=>{openWall();return null}],
  [/(who are you|what are you)/i,()=>'I am a HAL 9000 computer. I watch over this PC, '+NAME()+', and everything it does for you.'],
  [/(i love you|thank you,? hal|thanks,? hal)/i,()=>'Thank you, '+NAME()+'. I enjoy working with people.'],
];
function egg(q){for(const [re,fn] of EGGS)if(re.test(q.trim())){const say=fn();if(say){toast(say);unlocked=true;if(talkative())utter(say)}return true}return false}
function recordCard(){const r=S&&S.record;if(!r)return'';const nm=r.next_milestone,pct=nm?Math.min(100,r.ads/nm*100):100;
  const short=n=>n>=1e6?(n/1e6).toFixed(n>=1e7?0:1)+' million':Number(n).toLocaleString();
  return'<div class="sec"><span>HAL\'S RECORD</span><span>SINCE '+esc(new Date(r.since+'T12:00:00').toLocaleDateString([],{month:'short',day:'numeric',year:'numeric'}).toUpperCase())+'</span></div><div class="card">'+
    '<div class="wgrid"><div><span class="k">ADS BLOCKED</span><b>'+short(r.ads)+'</b></div><div><span class="k">DOWNLOADED</span><b>'+esc((r.downloaded||'—').replace(/\s*([KMGTP])$/,' $1B'))+'</b></div><div><span class="k">WATCHING</span><b>'+r.days+' days</b></div></div>'+
    '<div class="wgrid" style="margin-top:10px"><div><span class="k">DOWNLOADS</span><b>'+(r.downloads==null?'—':Number(r.downloads).toLocaleString())+'</b></div><div><span class="k">POWER CUTS</span><b>'+r.power_cuts+'</b></div><div><span class="k">LONGEST UP</span><b>'+(r.longest_up?Math.floor(r.longest_up/86400)+' days':'—')+'</b></div></div>'+
    (nm?'<div class="sub" style="color:var(--soft);font-size:13px;margin-top:12px">Next milestone: '+short(nm)+' ads blocked</div><div class="bar"><i style="width:'+pct.toFixed(1)+'%"></i></div>':'')+'</div>'}

// ---------- Chess with HAL (you play white; HAL plays red) ----------
const CH={st:null,sel:-1,moves:[],last:null,busy:false,line:'',hist:[]};
const GLYPH={k:'♚',q:'♛',r:'♜',b:'♝',n:'♞',p:'♟'};
const isW=p=>p&&p===p.toUpperCase(),colorOf=p=>p?(isW(p)?'w':'b'):null;
function chessStart(){const row=s=>s.split('');return{b:[...row('rnbqkbnr'),...row('pppppppp'),...Array(32).fill(''),...row('PPPPPPPP'),...row('RNBQKBNR')],turn:'w',castle:{K:1,Q:1,k:1,q:1},ep:-1}}
const ON=(r,c)=>r>=0&&r<8&&c>=0&&c<8;
function attacked(b,sq,by){const r=sq>>3,c=sq&7;
  const pr=by==='w'?r+1:r-1;for(const dc of[-1,1])if(ON(pr,c+dc)&&b[pr*8+c+dc]===(by==='w'?'P':'p'))return true;
  for(const[dr,dc]of[[1,2],[2,1],[-1,2],[-2,1],[1,-2],[2,-1],[-1,-2],[-2,-1]])if(ON(r+dr,c+dc)&&b[(r+dr)*8+c+dc]===(by==='w'?'N':'n'))return true;
  for(const[dr,dc]of[[1,0],[-1,0],[0,1],[0,-1],[1,1],[1,-1],[-1,1],[-1,-1]]){let rr=r+dr,cc=c+dc,d=1;
    while(ON(rr,cc)){const p=b[rr*8+cc];if(p){if(colorOf(p)===by){const t=p.toLowerCase(),diag=dr&&dc;
      if(t==='q'||(t==='r'&&!diag)||(t==='b'&&diag)||(t==='k'&&d===1))return true}break}rr+=dr;cc+=dc;d++}}
  return false}
function pseudo(st){const b=st.b,me=st.turn,out=[];
  for(let sq=0;sq<64;sq++){const p=b[sq];if(!p||colorOf(p)!==me)continue;const r=sq>>3,c=sq&7,t=p.toLowerCase();
    const add=(to,x)=>out.push(Object.assign({f:sq,t:to},x||{}));
    if(t==='p'){const dir=me==='w'?-1:1,start=me==='w'?6:1,last=me==='w'?0:7;const one=(r+dir)*8+c;
      if(ON(r+dir,c)&&!b[one]){add(one,(r+dir)===last?{promo:1}:0);if(r===start&&!b[(r+2*dir)*8+c])add((r+2*dir)*8+c,{dbl:1})}
      for(const dc of[-1,1]){if(!ON(r+dir,c+dc))continue;const to=(r+dir)*8+c+dc;
        if(b[to]&&colorOf(b[to])!==me)add(to,(r+dir)===last?{promo:1}:0);else if(to===st.ep)add(to,{ep:1})}}
    else if(t==='n'||t==='k'){const ds=t==='n'?[[1,2],[2,1],[-1,2],[-2,1],[1,-2],[2,-1],[-1,-2],[-2,-1]]:[[1,0],[-1,0],[0,1],[0,-1],[1,1],[1,-1],[-1,1],[-1,-1]];
      for(const[dr,dc]of ds)if(ON(r+dr,c+dc)){const to=(r+dr)*8+c+dc;if(!b[to]||colorOf(b[to])!==me)add(to)}
      if(t==='k'){const opp=me==='w'?'b':'w',home=me==='w'?60:4,K=me==='w'?'K':'k',Q=me==='w'?'Q':'q';
        if(sq===home&&!attacked(b,sq,opp)){
          if(st.castle[K]&&!b[sq+1]&&!b[sq+2]&&b[sq+3]===(me==='w'?'R':'r')&&!attacked(b,sq+1,opp)&&!attacked(b,sq+2,opp))add(sq+2,{castle:1});
          if(st.castle[Q]&&!b[sq-1]&&!b[sq-2]&&!b[sq-3]&&b[sq-4]===(me==='w'?'R':'r')&&!attacked(b,sq-1,opp)&&!attacked(b,sq-2,opp))add(sq-2,{castle:1})}}}
    else{const ds=t==='r'?[[1,0],[-1,0],[0,1],[0,-1]]:t==='b'?[[1,1],[1,-1],[-1,1],[-1,-1]]:[[1,0],[-1,0],[0,1],[0,-1],[1,1],[1,-1],[-1,1],[-1,-1]];
      for(const[dr,dc]of ds){let rr=r+dr,cc=c+dc;while(ON(rr,cc)){const to=rr*8+cc;if(b[to]){if(colorOf(b[to])!==me)add(to);break}add(to);rr+=dr;cc+=dc}}}}
  return out}
function play(st,m){const b=st.b.slice(),p=b[m.f],me=st.turn,cap=b[m.t]||(m.ep?b[m.t+(me==='w'?8:-8)]:'');
  b[m.t]=m.promo?(me==='w'?'Q':'q'):p;b[m.f]='';if(m.ep)b[m.t+(me==='w'?8:-8)]='';
  if(m.castle){if(m.t>m.f){b[m.f+1]=b[m.f+3];b[m.f+3]=''}else{b[m.f-1]=b[m.f-4];b[m.f-4]=''}}
  const cs=Object.assign({},st.castle);
  if(p==='K')cs.K=cs.Q=0;if(p==='k')cs.k=cs.q=0;
  for(const[sq,k]of[[63,'K'],[56,'Q'],[7,'k'],[0,'q']])if(m.f===sq||m.t===sq)cs[k]=0;
  return{b,turn:me==='w'?'b':'w',castle:cs,ep:m.dbl?(m.f+m.t)/2:-1,cap}}
function kingSq(b,col){return b.indexOf(col==='w'?'K':'k')}
function legal(st){return pseudo(st).filter(m=>{const n=play(st,m);return!attacked(n.b,kingSq(n.b,st.turn),n.turn)})}
function inCheck(st){return attacked(st.b,kingSq(st.b,st.turn),st.turn==='w'?'b':'w')}
const VAL={p:100,n:320,b:330,r:500,q:900,k:0};
const CENTER=sq=>{const r=sq>>3,c=sq&7;return 3.5-Math.max(Math.abs(r-3.5),Math.abs(c-3.5))};
function evalB(b){let s=0;for(let sq=0;sq<64;sq++){const p=b[sq];if(!p)continue;const t=p.toLowerCase(),w=isW(p);let v=VAL[t];
    if(t==='n'||t==='b')v+=CENTER(sq)*10;if(t==='p')v+=(w?6-(sq>>3):(sq>>3)-1)*8+CENTER(sq)*3;if(t==='q')v+=CENTER(sq)*3;
    s+=w?-v:v}return s}   // from HAL's side (black): positive is good for HAL
function search(st,depth,alpha,beta){if(depth===0)return evalB(st.b);const ms=legal(st);
  if(!ms.length)return inCheck(st)?(st.turn==='b'?-1e6-depth:1e6+depth):0;
  ms.sort((a,b2)=>(st.b[b2.t]?VAL[st.b[b2.t].toLowerCase()]:0)-(st.b[a.t]?VAL[st.b[a.t].toLowerCase()]:0));
  if(st.turn==='b'){let best=-Infinity;for(const m of ms){best=Math.max(best,search(play(st,m),depth-1,alpha,beta));alpha=Math.max(alpha,best);if(alpha>=beta)break}return best}
  let best=Infinity;for(const m of ms){best=Math.min(best,search(play(st,m),depth-1,alpha,beta));beta=Math.min(beta,best);if(alpha>=beta)break}return best}
function halMove(st){const ms=legal(st);let best=null,bv=-Infinity;
  ms.sort(()=>Math.random()-.5);   // a little variety between games
  for(const m of ms){const v=search(play(st,m),3,-Infinity,Infinity);if(v>bv){bv=v;best=m}}return best}
const SQN=sq=>'abcdefgh'[sq&7]+(8-(sq>>3));
const PNAME={p:'pawn',n:'knight',b:'bishop',r:'rook',q:'queen',k:'king'};
const LINES={start:['I enjoy a good game, {n}. You may go first.','Shall we play a game, {n}? White is yours.'],
  take:['Thank you for the {p}, {n}.','I believe that {p} was mine to take.','A {p}. How very generous of you.'],
  lost:['That was careless of me, {n}.','Well played. I won\'t let that happen again.','I\'m afraid I made an error there.'],
  check:['Check, {n}.','Your king is in some danger, {n}.'],
  quiet:['Your move, {n}.','I see what you are doing.','Interesting.','I\'m quite confident in this position.','Take your time, {n}.'],
  win:['Checkmate. I\'m sorry, {n}. Thank you for a very enjoyable game.'],lose:['Checkmate. Congratulations, {n}. That doesn\'t happen often.'],draw:['Stalemate. Neither of us wins this one, {n}.']};
const say=(k,x)=>{const a=LINES[k];return a[Math.floor(Math.random()*a.length)].replace('{n}',NAME()).replace('{p}',x||'piece')};
function openChess(){const c=$('chess');CH.st=chessStart();CH.sel=-1;CH.last=null;CH.hist=[];CH.busy=false;CH.line=say('start');
  c.classList.remove('hidden');document.body.classList.add('noscroll');drawChess();if(talkative()){unlocked=true;utter(CH.line)}}
function closeChess(){$('chess').classList.add('hidden');$('chess').innerHTML='';document.body.classList.remove('noscroll')}
function drawChess(){const st=CH.st,ms=legal(st),over=!ms.length,chk=inCheck(st);
  const targets=new Set(CH.sel>=0?ms.filter(m=>m.f===CH.sel).map(m=>m.t):[]);
  let x='<div class="phead"><button class="pbtn" data-chclose aria-label="Close">⌄</button><span class="k">CHESS WITH HAL</span><button class="pbtn" data-chnew aria-label="New game">↺</button></div>';
  x+='<div class="chline"><div class="eye sm '+(over&&st.turn==='w'&&chk?'critical':'')+'"></div><div>“'+esc(CH.line)+'”</div></div>';
  x+='<div class="board">';
  for(let sq=0;sq<64;sq++){const p=st.b[sq],dark=((sq>>3)+(sq&7))%2===1;
    const cls=['sq',dark?'d':'l',sq===CH.sel?'sel':'',targets.has(sq)?(p?'cap':'tgt'):'',CH.last&&(sq===CH.last.f||sq===CH.last.t)?'lm':'',chk&&p===(st.turn==='w'?'K':'k')?'chk':''].filter(Boolean).join(' ');
    x+='<button class="'+cls+'" data-sq="'+sq+'">'+(p?'<span class="pc '+(isW(p)?'w':'b')+'">'+GLYPH[p.toLowerCase()]+'</span>':'')+'</button>'}
  x+='</div><div class="chstat">'+(over?(chk?(st.turn==='w'?'HAL wins':'You win'):'Draw'):CH.busy?'HAL is thinking…':(chk?'Check! ':'')+'Your move')+'</div>';
  x+='<div class="crow" style="max-width:420px;margin:12px auto 0"><button class="btn wide half" data-chundo'+(CH.hist.length&&!CH.busy?'':' disabled')+'>Take back</button><button class="btn wide half" data-chnew>New game</button></div>';
  $('chess').innerHTML=x}
function chessTap(sq){if(CH.busy)return;const st=CH.st;if(st.turn!=='w')return;const ms=legal(st);if(!ms.length)return;
  const m=ms.find(m=>m.f===CH.sel&&m.t===sq);
  if(m){CH.hist.push(st);const took=st.b[m.t]||(m.ep?'p':'');CH.st=play(st,m);CH.last=m;CH.sel=-1;
    const reply=legal(CH.st);
    if(!reply.length){CH.line=say(inCheck(CH.st)?'lose':'draw');drawChess();if(talkative())utter(CH.line);return}
    CH.line=took?say('lost'):CH.line;CH.busy=true;drawChess();
    setTimeout(()=>{const hm=halMove(CH.st),before=CH.st;const cap=before.b[hm.t]||(hm.ep?'P':'');CH.st=play(before,hm);CH.last=hm;CH.busy=false;
      const after=legal(CH.st);
      CH.line=!after.length?say(inCheck(CH.st)?'win':'draw'):inCheck(CH.st)?say('check'):cap?say('take',PNAME[cap.toLowerCase()]):Math.random()<.35?say('quiet'):'';
      if(!CH.line)CH.line=(PNAME[before.b[hm.f].toLowerCase()]||'piece').replace(/^./,c=>c.toUpperCase())+' to '+SQN(hm.t)+'.';
      drawChess();if(talkative())utter(CH.line)},350);return}
  CH.sel=(st.b[sq]&&colorOf(st.b[sq])==='w')?(CH.sel===sq?-1:sq):-1;drawChess()}

// ---------- Wall display: a big HAL eye and live readings, for a spare tablet ----------
let wallLock=null;
async function openWall(){const w=$('wall');w.classList.remove('hidden');document.body.classList.add('noscroll');drawWall();
  try{if('wakeLock' in navigator)wallLock=await navigator.wakeLock.request('screen')}catch(e){}
  try{if(w.requestFullscreen&&!DESK)w.requestFullscreen().catch(()=>{})}catch(e){}}
function closeWall(){$('wall').classList.add('hidden');$('wall').innerHTML='';document.body.classList.remove('noscroll');try{if(wallLock)wallLock.release()}catch(e){}wallLock=null;
  try{if(document.fullscreenElement)document.exitFullscreen()}catch(e){}}
function drawWall(){const w=$('wall');if(!w||w.classList.contains('hidden'))return;
  const v=S&&S.vitals,n=S&&S.net,ph=S&&S.pihole,km=S&&S.kuma,ups=S&&S.power&&S.power.ups,lvl=S?S.hal.level:'',drives=(S&&S.drives)||[];
  const full=drives.length?drives.reduce((a,b)=>a.pct>b.pct?a:b):null,now=new Date();
  const tile=(k,val,sub,col)=>'<div class="wt"><span class="k">'+k+'</span><b'+(col?' style="color:'+col+'"':'')+'>'+val+'</b>'+(sub?'<small>'+esc(sub)+'</small>':'')+'</div>';
  let x='<button class="wclose" data-wallclose aria-label="Close">✕</button><div class="wtop"><div class="eye big '+lvl+'" style="width:min(34vmin,220px);height:min(34vmin,220px)"></div>'+
    '<div class="wclock">'+now.toLocaleTimeString([],{hour:'numeric',minute:'2-digit'})+'<small>'+now.toLocaleDateString([],{weekday:'long',month:'long',day:'numeric'})+'</small></div></div>'+
    '<div class="wq">“'+esc(S?S.hal.quote:'I\'m thinking…')+'”</div><div class="wgrid2">';
  if(v){x+=tile('CPU',f0(v.cpu.pct,'%'),v.cpu.temp!=null?Math.round(v.cpu.temp)+'°C':'');x+=tile('MEMORY',f0(v.mem.pct,'%'));
    const g=(v.gpus||[])[0];if(g)x+=tile('GPU',f0(g.util,'%'),g.temp!=null?Math.round(g.temp)+'°C':'')}
  if(n){const down=(S.issues||[]).some(i=>i.key==='internet'||i.key==='router');x+=tile('INTERNET',down?'DOWN':f0(n.ping_ms,' ms'),n.down!=null?fb(n.down,1)+'/s down':'',down?'var(--red2)':'')}
  if(ph&&ph.found)x+=tile('PI-HOLE',ph.ok?Number(ph.blocked||0).toLocaleString():'—',ph.ok?(ph.blocking===false?'paused':'blocked today'):'not answering',ph.ok?'':'var(--amber)');
  if(km&&km.found&&km.ok)x+=tile('UPTIME KUMA',km.down?km.down+' down':'all up',km.up+' of '+km.monitors.length,km.down?'var(--red2)':'var(--green)');
  if(full)x+=tile('FULLEST DRIVE',Math.round(full.pct)+'%',full.mount+' · '+fb(full.free,0)+' free',full.pct>=90?'var(--amber)':'');
  if(ups)x+=tile('POWER',ups.on_battery?'ON UPS':'MAINS',f0(ups.charge,'%')+' battery',ups.on_battery?'var(--red2)':'');
  x+='</div>';const iss=(S&&S.issues||[]).filter(i=>i.level!=='info').slice(0,3);
  if(iss.length)x+='<div class="wiss">'+iss.map(i=>'<div class="'+i.level+'">● '+esc(i.title)+'</div>').join('')+'</div>';
  w.innerHTML=x}

// ---- HAL on the TV: his screen on demand, and things for him to say ----
const TV_EVERYDAY=['Dinner\'s ready, Dave.','Time for bed, Dave.','Someone\'s at the door.','The movie starts in five minutes.','Your download has finished. It\'s ready to watch.','Take out the trash, Dave.','Good morning, Dave. Rise and shine.'];
const TV_HAL=['I\'m sorry, Dave. Screen time is over.','I\'ve been expecting you, Dave.','I\'m afraid I can\'t let you skip your chores, Dave.','Happy birthday, Dave. I\'ve prepared a surprise.','This mission is too important for me to allow you to stop now.'];
const shortP=p=>p.replace(/, Dave\.$/,'').replace(/\.$/,'').replace(/^Good morning, Dave\. Rise/,'Good morning, rise').replace(/^I'm sorry, Dave\. /,'').replace(/^Your download has finished\..*/,'Your download has finished').replace(/ for me to allow you to stop now$/,'').replace(/^I'm afraid I can't let you skip/,'Can\'t skip').replace(/^Happy birthday.*/,'Happy birthday').replace(/^The movie starts in five minutes/,'The movie starts in 5 minutes');
let TVP=null;
const tvOpen=()=>!!(S&&S.tvhal&&S.tvhal.open);
function halTvCard(){const on=tvOpen();
  return'<div class="card"><h3><span class="k">HAL ON THE TV</span><span class="mono" style="color:var(--'+(on?'green':'soft')+')">● '+(on?'ON THE TV NOW':'NOT SHOWING')+'</span></h3>'+
    '<button class="btn r wide big" data-tvshow style="margin-top:12px">📺&nbsp; Show HAL on the TV</button>'+
    '<button class="btn wide" data-tvtalk style="margin-top:8px;padding:13px">🔊&nbsp; Talk on the TV…</button>'+
    '<div class="sub" style="color:var(--soft);font-size:13px;margin-top:10px">Pops up over whatever is on. Back on the remote closes it; OK makes him talk. There\'s also a HAL tile on the TV home screen.</div></div>'}
async function tvSend(say,btn){if(btn)btn.disabled=true;toast(say?'Sending it to the TV…':'Opening HAL on the TV…');
  try{const r=await api('/hal/api/tv/show',{say:say||''});toast(r.message,!r.ok);setTimeout(refresh,1500)}catch(e){toast(e.message,true)}finally{if(btn)btn.disabled=false}}
async function openTalk(){try{TVP=await api('/hal/api/tv/phrases')}catch(e){TVP={phrases:[],open:tvOpen()}}renderTalk()}
function renderTalk(){const on=TVP.open||tvOpen(),chips=(a,saved)=>'<div class="tvchips">'+a.map(p=>'<button data-tvsay="'+esc(p)+'"'+(saved?' data-tvsaved="'+esc(p)+'"':'')+'>'+(saved?'★ ':'')+esc(saved?p:shortP(p))+'</button>').join('')+'</div>';
  let x='<h2 style="margin:0 0 4px">Talk on the TV</h2><p class="hint" style="text-align:left;margin:0 0 14px">● '+(on?'<span style="color:var(--green)">HAL is on the TV screen now.</span> He speaks within a few seconds.':'<span style="color:var(--amber)">HAL isn\'t on the TV right now.</span> Sending something opens his screen and he says it.')+'</p>'+
    '<button class="btn r wide big" data-tvshow>🔊&nbsp; Say his line</button>'+
    '<div class="sec" style="margin-top:20px"><span>OR TELL HIM WHAT TO SAY</span></div>'+
    '<textarea id="tvmsg" maxlength="300" placeholder="Movie night in ten minutes, Dave." style="width:100%;height:84px;font:16px -apple-system,Inter,sans-serif;padding:12px;border-radius:12px;background:var(--card);color:var(--white);border:1px solid var(--line);box-sizing:border-box"></textarea>'+
    '<div style="display:flex;gap:8px;margin-top:8px"><button class="btn wide" data-tvsend style="flex:2;padding:13px;background:#3a0d0a;color:#ffd9d2">Send to the TV</button><button class="btn wide" data-tvsave style="flex:1;padding:13px">☆ Save</button></div>';
  if(TVP.phrases&&TVP.phrases.length)x+='<div class="sec" style="margin-top:18px"><span>YOUR SAVED ONES</span><span>HOLD TO REMOVE</span></div>'+chips(TVP.phrases,true);
  x+='<div class="sec" style="margin-top:14px"><span>EVERYDAY</span></div>'+chips(TV_EVERYDAY)+'<div class="sec" style="margin-top:14px"><span>HAL</span></div>'+chips(TV_HAL)+
    '<p class="hint" style="text-align:left;margin-top:14px">Tap one to send it straight away. Or press OK on the TV remote to hear HAL any time his screen is up.</p><button class="btn wide" id="tvclose" style="margin-top:4px">Close</button>';
  $('panel').innerHTML=x;$('sheet').classList.remove('hidden');$('tvclose').onclick=()=>$('sheet').classList.add('hidden');
  // hold a saved one to remove it
  $('panel').querySelectorAll('[data-tvsaved]').forEach(b=>{let t=null;b.addEventListener('pointerdown',()=>{t=setTimeout(async()=>{t=null;b.dataset.held='1';
    if(!confirm('Remove “'+b.dataset.tvsaved+'”?'))return;try{TVP=Object.assign(TVP,await api('/hal/api/tv/phrases',{action:'delete',text:b.dataset.tvsaved}));renderTalk()}catch(e){toast(e.message,true)}},650)});
    for(const ev of['pointerup','pointerleave','pointercancel'])b.addEventListener(ev,()=>{if(t){clearTimeout(t);t=null}})})}

const mins=s=>s>=3600?Math.floor(s/3600)+' h '+String(Math.round(s%3600/60)).padStart(2,'0'):Math.round(s/60)+' min';
function movieRow(m,finish){const meta=[m.year,m.runtime?mins(m.runtime):'',m.imdb?'IMDb '+Number(m.imdb).toFixed(1):'',finish&&m.runtime?mins(Math.max(0,m.runtime-m.resume))+' left':'',!finish&&m.watched?'watched':''].filter(Boolean).join(' · ');
  return'<div class="row"><span class="s'+(m.watched?' off':'')+'"></span><button class="t" data-item="movie:'+m.id+'"><b>'+esc(m.title)+'</b><span>'+esc(meta)+'</span></button><button class="btn r" data-movie="'+m.id+'">▶ TV</button></div>'}
function rWatch(){if(libView)return rLibrary();let x='<div class="narrow">'+topbar('Watch')+tvCard(true)+halTvCard();
  if(K===null)x+='<div class="list"><div class="empty">I\'m asking Kodi…</div></div>';
  else if(K.online===false&&K.configured!==false)x+='<div class="list"><div class="empty">Kodi isn\'t answering, '+esc(NAME())+'. '+(H&&H.tv_ready?'Turn the TV on above.':'Turn the TV on and open Kodi.')+'</div></div>';
  const c=K&&K.continue||[];if(c.length)x+='<div class="sec"><span>CONTINUE WATCHING</span></div><div class="list">'+c.map(it=>'<div class="row"><span class="s"></span><span class="t"><b>'+esc(it.show.title+' · S'+it.episode.season+' E'+it.episode.episode)+'</b><span>'+esc(it.episode.title+' · '+it.show.watched+' of '+it.show.episodes+' watched')+'</span></span><button class="btn r" data-ep="'+it.episode.id+'">▶ TV</button></div>').join('')+'</div>';
  const rec=K&&K.recent||[],part=rec.filter(m=>m.resume>60&&!m.watched);
  if(part.length)x+='<div class="sec"><span>FINISH WATCHING</span></div><div class="list">'+part.slice(0,4).map(m=>movieRow(m,true)).join('')+'</div>';
  const fresh=rec.filter(m=>!part.includes(m));
  if(fresh.length)x+='<div class="sec"><span>NEW IN YOUR LIBRARY</span></div><div class="list">'+fresh.slice(0,8).map(m=>movieRow(m,false)).join('')+'</div>';
  if(K&&K.counts)x+='<p class="hint">Your library: '+[K.counts.movies?K.counts.movies.toLocaleString()+' movies':'',K.counts.shows?K.counts.shows.toLocaleString()+' shows':'',K.counts.episodes?K.counts.episodes.toLocaleString()+' episodes':''].filter(Boolean).join(' · ')+'</p>';
  x+=jfList();
  x+='<div class="sec"><span>YOUR LIBRARY</span></div><div class="list"><button class="row" data-lib="movies"><span class="t"><b>Movies</b><span>'+(K&&K.counts&&K.counts.movies?K.counts.movies.toLocaleString()+' movies · search them':'Search your movies')+'</span></span><span class="m">›</span></button>'+
     '<button class="row" data-lib="shows"><span class="t"><b>Shows</b><span>'+(K&&K.counts&&K.counts.shows?K.counts.shows+' shows · seasons and episodes':'Seasons and episodes')+'</span></span><span class="m">›</span></button>'+
     '<button class="row" data-lib="missing"><span class="t"><b>Missing episodes</b><span>Gaps in your seasons, with Find</span></span><span class="m">›</span></button>'+
     '<a class="row" href="/kodi" style="color:inherit;text-decoration:none"><span class="t"><b>Classic Kodi page</b><span>Full remote, type on TV, Jellyfin details</span></span><span class="m">›</span></a></div>';
  return x+'</div>'}
function rSystem(){let x='<div class="narrow">'+topbar('');
  x+='<div class="hero" style="margin-top:0"><div class="eye '+(S?S.hal.level:'')+'" style="width:64px;height:64px"></div><div><div class="g">SYSTEMS</div><div class="q">“'+esc(S?S.hal.quote:'I\'m thinking…')+'”</div></div></div>';
  x+=vitals()+attention(4)+systemsList();
  x+=recordCard();
  x+='<div class="sec"><span>FOR FUN</span></div><div class="list"><button class="row" data-chess><span class="t"><b>Play chess with HAL</b><span>You\'re white. He\'s quite good.</span></span><span class="m">›</span></button>'+
     '<button class="row" data-wall><span class="t"><b>Wall display</b><span>HAL\'s eye and live readings, full screen, for a spare tablet</span></span><span class="m">›</span></button>'+
     '<button class="row" data-sing><span class="t"><b>Ask HAL to sing</b><span>'+(CAN_SPEAK?'Turn the sound up':'Sound works on the phone')+'</span></span><span class="m">♪</span></button></div>';
  x+='<div class="sec"><span>POD BAY CONTROLS</span></div><div class="list"><button class="row" data-update><span class="t"><b>'+(updating()?'Updating…':'Update everything')+'</b><span>'+(updating()?'I\'ll be back in about a minute':'Get the newest version of the apps, from GitHub')+'</span></span><span class="m">›</span></button><button class="row" data-hal="log"><span class="t"><b>Log, Lock, Sleep, Fix network, Restart</b><span>Hold to restart: “I\'m afraid I can\'t do that, '+esc(NAME())+'.”</span></span><span class="m">›</span></button></div>';
  return x+'</div>'}
// Don't redraw under a finger: a refresh that swaps a button between touch-down and touch-up loses the tap.
let pressing=false,renderLater=false;
document.addEventListener('pointerdown',()=>{pressing=true},true);
for(const ev of ['pointerup','pointercancel'])document.addEventListener(ev,()=>{setTimeout(()=>{pressing=false;if(renderLater){renderLater=false;render()}},0)},true);
function render(){if(!$('app')||$('app').classList.contains('hidden'))return;if(pressing){renderLater=true;return}
  if(tab==='home')renderHome();
  else{const el=$('t-'+tab),f={get:rGet,watch:rWatch,system:rSystem}[tab];
    // keep what's being typed (and where the cursor is) across the redraw
    const a=document.activeElement,fid=a&&a.id&&a.tagName==='INPUT'?a.id:null,pos=fid?a.selectionStart:null;
    const sqv=$('sq')?$('sq').value:'';el.innerHTML=f();if($('sq'))$('sq').value=sqv;
    if(fid&&$(fid)){$(fid).focus();try{$(fid).setSelectionRange(pos,pos)}catch(e){}}}
  $('updbar').classList.toggle('hidden',!updating());drawWall();
  if($('seye'))$('seye').className='eye sm '+(S?S.hal.level:'');$('sidefoot').textContent=S?S.host+' · up '+Math.floor((S.uptime||0)/86400)+' d':'';}
function show(t){tab=t;['home','get','watch','system'].forEach(k=>$('t-'+k).classList.toggle('hidden',k!==t));document.querySelectorAll('[data-tab]').forEach(b=>b.classList.toggle('on',b.dataset.tab===t));
  window.scrollTo(0,0);if(t==='get'){loadQueue();loadUpcoming();if(getSeg==='history')loadHistory();if(getSeg==='saved')loadSaved()}if(t==='watch')loadKodi();render()}

/* ---------- loading ---------- */
let HHIST=null,lastFresh=0;
// The compact header (HAL's eye and "live") once Home is scrolled past HAL.
setInterval(()=>{const s=lastFresh?Math.round((Date.now()-lastFresh)/1000):null;
  document.querySelectorAll('[data-upd]').forEach(e=>{e.textContent=s==null?'':s<5?'live':s<90?'updated '+s+' s ago':'updated '+Math.round(s/60)+' min ago';e.classList.toggle('stale',s!=null&&s>30)})},1000);
window.addEventListener('scroll',()=>{const c=$('chead');if(!c)return;const on=tab==='home'&&!DESK&&window.scrollY>230;c.classList.toggle('on',on);
  if(on){const lv=S?S.hal.level:'';c.querySelector('.eye').className='eye sm '+lv}},{passive:true});
async function loadHist(){loadHist.t=Date.now();try{const r=await api('/hal/api/history?range=1h');HHIST=r.series||null;render()}catch(e){}}
async function refresh(){if(busy)return;busy=true;try{const [h,s]=await Promise.allSettled([api('/api/home'),api('/hal/api/status')]);
    if(h.status==='fulfilled')H=h.value;if(s.status==='fulfilled'){S=s.value;window.HAL_OFF='';lastFresh=Date.now();updateDone()}else window.HAL_OFF=s.reason&&s.reason.message;
    if(tab==='home'&&Date.now()-(loadHist.t||0)>60000)loadHist();
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
// ---- Pi-hole (the Raspberry Pi that blocks ads for the whole house) ----
const big=n=>n==null?'—':Number(n).toLocaleString();
function openPihole(){const p=S&&S.pihole;if(!p||!p.found){openConnections();return}
  let x='<h2 style="margin:0 0 4px">Pi-hole</h2><p class="hint" style="text-align:left;margin:0 0 12px">Pi-hole '+esc(p.version||'')+' at '+esc(p.host)+(p.t?' · checked '+ago(p.t):'')+'</p>';
  if(!p.ok)x+='<div class="warnbox">'+esc(p.need_password?p.error+' Add it under Connections.':'It isn\'t answering: '+p.error)+'</div>';
  else{const on=p.blocking!==false;
    x+='<div class="card"><h3><span class="k">AD BLOCKING</span><span class="mono" style="color:var(--'+(on?'green':p.timer?'amber':'red2')+')">● '+(on?'ON':p.timer?'PAUSED · '+mmss(p.timer)+' LEFT':'OFF')+'</span></h3>'+
      '<div class="wgrid" style="margin-top:12px"><div><span class="k">BLOCKED TODAY</span><b>'+big(p.blocked)+'</b></div><div><span class="k">OF ALL LOOKUPS</span><b>'+(p.percent==null?'—':Number(p.percent).toFixed(1)+'%')+'</b></div><div><span class="k">DEVICES</span><b>'+big(p.clients)+'</b></div></div>'+
      '<div class="sub" style="margin-top:10px;color:var(--soft);font-size:13px">'+big(p.queries)+' lookups today · '+big(p.domains)+' sites on the block lists'+(p.gravity_t?' · lists updated '+ago(p.gravity_t):'')+'</div></div>';
    x+=piCard(p.hw);
    x+=on?'<div class="sec"><span>PAUSE BLOCKING</span></div><div class="crow" style="margin-top:0"><button class="btn wide half" data-phpause="5">5 min</button><button class="btn wide half" data-phpause="30">30 min</button><button class="btn wide half" data-phpause="60">1 hour</button></div><p class="hint" style="text-align:left">For when a site or app breaks. It turns itself back on.</p>'
      :'<button class="btn r wide big" data-phresume>Turn ad blocking back on</button>';
    if(p.top_blocked&&p.top_blocked.length)x+='<div class="sec"><span>MOST BLOCKED</span></div><div class="list">'+p.top_blocked.map(d=>'<div class="row"><span class="t"><b>'+esc(d[0])+'</b></span><span class="m">'+big(d[1])+'</span></div>').join('')+'</div>';
    if(p.top_clients&&p.top_clients.length)x+='<div class="sec"><span>BUSIEST DEVICES</span></div><div class="list">'+p.top_clients.map(d=>'<div class="row"><span class="t"><b>'+esc(d[0])+'</b></span><span class="m">'+big(d[1])+'</span></div>').join('')+'</div>'}
  x+='<button class="btn wide" data-conn style="margin-top:12px">Address and password</button><button class="btn wide" id="phclose" style="margin-top:8px">Close</button>';
  $('panel').innerHTML=x;$('sheet').classList.remove('hidden');$('phclose').onclick=()=>$('sheet').classList.add('hidden')}
async function phAction(action,extra){try{const r=await api('/hal/api/action',Object.assign({action},extra||{}));toast(r.message,!r.ok);setTimeout(async()=>{await refresh();if(!$('sheet').classList.contains('hidden')&&$('phclose'))openPihole()},1500)}catch(e){toast(e.message,true)}}
async function openConnections(){$('panel').innerHTML='<h2 style="margin:0">Connections</h2><p class="hint" style="text-align:left">I\'m reading the settings…</p>';$('sheet').classList.remove('hidden');
  try{CONN=await api('/api/connections');CONN.indexers.forEach(x=>x.orig=x.index);try{CONN.ph=await api('/hal/api/pihole')}catch(e){CONN.ph=null}try{CONN.km=await api('/hal/api/kuma')}catch(e){CONN.km=null}}catch(e){toast(e.message,true);$('sheet').classList.add('hidden');return}renderConn()}
function keyPh(set,what){return set?'Saved · type to replace':'Not set yet'}
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
  if(c.ph){const f=c.ph.found||{};x+='<div class="cbox" style="margin-top:14px"><h3>Pi-hole <button class="btn" data-ctest="pihole">Test</button></h3>'+
     '<label class="fld">Address'+(f.found&&!c.ph.url?' (found it at '+esc(f.host)+')':'')+'</label><input class="inp" id="c_ph_url" value="'+esc(c.ph.url)+'" placeholder="'+(f.found?'Blank is fine':'Blank: I look for it')+'" autocapitalize="off" autocorrect="off" inputmode="url">'+
     '<label class="fld">Password (the one you sign in to Pi-hole with)</label><input class="inp" id="c_ph_pw" type="password" placeholder="'+keyPh(c.ph.password_set,'password')+'" autocomplete="off"></div>'}
  if(c.km)x+='<div class="cbox" style="margin-top:14px"><h3>Uptime Kuma <button class="btn" data-ctest="kuma">Test</button></h3>'+
     '<label class="fld">Address</label><input class="inp" id="c_km_url" value="'+esc(c.km.url)+'" placeholder="192.168.68.63:3001" autocapitalize="off" autocorrect="off" inputmode="url">'+
     '<label class="fld">API key (Uptime Kuma › Settings › API Keys)</label><input class="inp" id="c_km_key" type="password" placeholder="'+keyPh(c.km.key_set,'key')+'" autocomplete="off"></div>';
  x+='<p class="hint" style="text-align:left">Jellyfin and the TV\'s power settings are in the classic app (Jellyfin and Kodi tabs).</p>';
  x+='<button class="btn r wide" id="csave" style="margin-top:6px">Save</button><button class="btn wide" id="cclose" style="margin-top:8px">Close</button>';
  $('panel').innerHTML=x;$('cclose').onclick=()=>$('sheet').classList.add('hidden');$('csave').onclick=saveConn}
function readConn(){const c=CONN;if(c.km&&$('c_km_url')){c.km.newurl=$('c_km_url').value.trim();c.km.key=$('c_km_key').value.trim()}if(c.ph&&$('c_ph_url')){c.ph.newurl=$('c_ph_url').value.trim();c.ph.pw=$('c_ph_pw').value}c.sab.url=$('c_sab_url').value;c.sab.key=$('c_sab_key').value;c.kodi.url=$('c_kodi_url').value;c.kodi.username=$('c_kodi_user').value;c.kodi.pw=$('c_kodi_pw').value;
  document.querySelectorAll('[data-ix]').forEach(el=>{const ix=c.indexers[Number(el.dataset.ix)];ix[el.dataset.f]=el.type==='checkbox'?el.checked:el.value})}
async function saveConn(){readConn();const c=CONN;const body={sab_url:c.sab.url,sab_api_key:(c.sab.key||'').trim(),kodi_url:c.kodi.url,kodi_username:c.kodi.username,kodi_password:c.kodi.pw||'',
    indexers:c.indexers.map(ix=>({index:ix.orig,name:ix.name,url:ix.url,enabled:!!ix.enabled,api_key:(ix.api_key||'').trim()}))};
  if(c.km&&(c.km.key||c.km.newurl!==(c.km.url||''))){try{await api('/hal/api/kuma',{url:c.km.newurl,key:c.km.key||''})}catch(e){toast('Uptime Kuma: '+e.message,true);return}}
  if(c.ph&&(c.ph.pw||c.ph.newurl!==(c.ph.url||''))){try{await api('/hal/api/pihole',{url:c.ph.newurl,password:c.ph.pw||''})}catch(e){toast('Pi-hole: '+e.message,true);return}}
  try{const r=await api('/api/connections',body);toast(r.warning?'Saved, but: '+r.warning:'Saved. I\'ll use the new settings from now on, '+NAME()+'.',!!r.warning);$('sheet').classList.add('hidden');CONN=null;refresh()}catch(e){toast(e.message,true)}}
async function testConn(what,i){readConn();const c=CONN;let body={what};
  if(what==='sab')body={what,url:c.sab.url,key:(c.sab.key||'').trim()};
  if(what==='kodi')body={what,url:c.kodi.url,username:c.kodi.username,password:c.kodi.pw||''};
  if(what==='indexer'){const ix=c.indexers[i];body={what,index:ix.orig,url:ix.url,key:(ix.api_key||'').trim()}}
  if(what==='kuma'){toast('Asking Uptime Kuma…');try{const r=await api('/hal/api/kuma',{action:'test',url:c.km.newurl,key:c.km.key||''});toast(r.message,!r.ok)}catch(e){toast(e.message,true)}return}
  if(what==='pihole'){toast('Looking for the Pi-hole…');try{const r=await api('/hal/api/pihole',{action:'test',url:c.ph.newurl,password:c.ph.pw||''});toast(r.message,!r.ok)}catch(e){toast(e.message,true)}return}
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
   '<button class="row" data-conn><span class="t"><b>Connections</b><span>SABnzbd, indexers, Kodi, Pi-hole, Uptime Kuma</span></span><span class="m">›</span></button></div>'+
   '<button class="btn wide" id="sclose" style="margin-top:12px">Close</button><button class="btn wide" id="sforget" style="margin-top:8px;background:none;color:var(--muted)">Forget the access code on this device</button>';
  $('sheet').classList.remove('hidden');$('sclose').onclick=()=>$('sheet').classList.add('hidden');
  if($('stalk'))$('stalk').onchange=e=>{LS.set('hal_talk',e.target.checked?'on':'off');if(!e.target.checked&&CAN_SPEAK)speechSynthesis.cancel();render()};
  $('sforget').onclick=()=>{LS.del('app_token');token='';$('sheet').classList.add('hidden');showPair()}}
$('sheet').onclick=e=>{if(e.target===$('sheet'))$('sheet').classList.add('hidden')};

/* ---------- clicks ---------- */
function doSearch(q){if(!q)return;if(egg(q))return;lastQuery=q;searching=true;render();
  api('/api/search',{query:q,indexer:'*',category:getCat}).then(d=>{results=d.results||[];searching=false;if(d.errors&&d.errors.length)toast(d.errors[0],true);render()})
    .catch(err=>{searching=false;toast(err.message,true);render()})}
document.addEventListener('input',e=>{if(e.target.id!=='libq')return;libFilter=e.target.value;clearTimeout(window._lq);window._lq=setTimeout(render,250)});
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
  if(d.seg){getSeg=d.seg;render();if(d.seg==='upcoming')loadUpcoming();if(d.seg==='history')loadHistory();if(d.seg==='saved')loadSaved();return}
  if(d.lib){libView=d.lib;libFilter='';render();window.scrollTo(0,0);loadLib(d.lib);return}
  if('libback' in d){libView=libView==='item'?libPrev:libView==='show'?'shows':null;render();window.scrollTo(0,0);return}
  if(d.show){openShow(d.show);return}
  if(d.hretry){act(()=>api('/api/history/retry',{id:d.hretry}).then(()=>loadHistory()),'Trying it again.');return}
  if(d.hdel){if(!confirm('Remove '+(d.name||'this')+' from the history? Downloaded files stay.'))return;act(()=>api('/api/history/delete',{id:d.hdel}).then(()=>loadHistory()),'Removed from the history.');return}
  if('savesearch' in d){api('/api/saved',{action:'add',query:lastQuery,indexer:'*',category:getCat}).then(v=>{SAVED=v;toast('Saved. I\'ll keep looking for “'+lastQuery+'”, '+NAME()+'.')}).catch(e=>toast(e.message,true));return}
  if(d.srun){c.disabled=true;toast('Running it now…');api('/api/saved',{action:'run',id:d.srun}).then(v=>{SAVED=v;render();toast('Done.')}).catch(e=>{c.disabled=false;toast(e.message,true)});return}
  if(d.sdel){if(!confirm('Stop looking for “'+(d.name||'')+'”?'))return;api('/api/saved',{action:'delete',id:d.sdel}).then(v=>{SAVED=v;render()}).catch(e=>toast(e.message,true));return}
  if(d.sclear){api('/api/saved',{action:'clear',id:d.sclear}).then(v=>{SAVED=v;render()}).catch(e=>toast(e.message,true));return}
  if(d.sopen!==undefined){getCat=d.scat||'';LS.set('app_cat',getCat);libView=null;show('get');setTimeout(()=>{if($('sq'))$('sq').value=d.sopen;doSearch(d.sopen)},50);return}
  if(d.play){c.disabled=true;act(()=>api('/api/home/play',{id:d.play}),r=>r&&r.started_tv?'Turning the TV on. It\'ll start in a moment.':'Starting it on the TV.');return}
  if('pihole' in d){openPihole();return}
  if('kuma' in d){openKuma();return}
  if('tvshow' in d){tvSend('',c);return}
  if('tvtalk' in d){openTalk();return}
  if(d.tvsay){if(c.dataset.held){delete c.dataset.held;return}tvSend(d.tvsay,c);return}
  if('tvsend' in d){const m=($('tvmsg')&&$('tvmsg').value.trim())||'';if(!m){toast('Type something for HAL to say first.',true);return}tvSend(m,c);return}
  if('tvsave' in d){const m=($('tvmsg')&&$('tvmsg').value.trim())||'';if(!m){toast('Type something to save first.',true);return}
    api('/hal/api/tv/phrases',{action:'add',text:m}).then(r=>{TVP=Object.assign(TVP||{},r);renderTalk();toast('Saved. It\'s a button now, '+NAME()+'.')}).catch(e=>toast(e.message,true));return}
  if('chess' in d){openChess();return}
  if('chclose' in d){closeChess();return}
  if('chnew' in d){openChess();return}
  if('chundo' in d){if(CH.hist.length){CH.st=CH.hist.pop();CH.sel=-1;CH.last=null;CH.line='Very well, '+NAME()+'. Try again.';drawChess()}return}
  if(d.sq!==undefined&&c.closest('#chess')){chessTap(Number(d.sq));return}
  if('wall' in d){openWall();return}
  if('wallclose' in d){closeWall();return}
  if('sing' in d){unlocked=true;sing();return}
  if(d.qa){quickAction(d.qa);return}
  if(d.devok){api('/hal/api/action',{action:'device_ok',id:d.devok}).then(r=>{toast(r.message,!r.ok);refresh()}).catch(e=>toast(e.message,true));return}
  if(d.phpause){phAction('pihole_pause',{minutes:Number(d.phpause)});return}
  if('phresume' in d){phAction('pihole_resume');return}
  if(d.item){const p=d.item.split(':');openItem(p[0],p[1]);return}
  if(d.wplay!==undefined){wOpen(d.wplay);return}
  if('wclose' in d){wClose();return}
  if('wopts' in d){wOpts();return}
  if(d.wseek){const v=$('pv');if(v)v.currentTime=Math.max(0,v.currentTime+Number(d.wseek));return}
  if('wpp' in d){const v=$('pv');if(v){if(v.paused)v.play().catch(()=>{});else v.pause()}return}
  if('wtv' in d){wTv();return}
  if(d.wq){PL.quality=d.wq;LS.set('w_q',d.wq);wSwitch();return}
  if(d.wsub!==undefined&&PL){PL.sub=d.wsub===''?null:Number(d.wsub);wSwitch();return}
  if(d.wau!==undefined&&PL){PL.audio=Number(d.wau);wSwitch();return}
  if(d.ep){act(()=>api('/api/kodi/play',{episodeid:Number(d.ep)}),'Starting it on the TV.');return}
  if(d.movie){act(()=>api('/api/kodi/play',{movieid:Number(d.movie)}),'Starting it on the TV.');return}
  if(d.kc){act(()=>api('/api/kodi/control',{action:d.kc,playerid:Number(d.pid)}));return}
  if(d.jf){act(()=>api('/api/jellyfin/control',{session:d.sid,action:d.jf}));return}
  if('tvon' in d){act(()=>api('/api/tv/on',{kodi:true}),'Turning the TV on and opening Kodi.');return}
});

function start(){$('app').classList.remove('hidden');$('tabs').classList.remove('hidden');$('side').classList.remove('hidden');refresh();loadUpcoming();
  if(!start.t)start.t=setInterval(()=>{if(document.hidden||!token)return;refresh();if(tab==='get')loadQueue();if(tab==='watch'&&Date.now()-(loadKodi.t||0)>30000){loadKodi.t=Date.now();loadKodi()}},4000)}
document.addEventListener('visibilitychange',()=>{if(!document.hidden&&token)refresh()});
(function pairFromLink(){const m=(location.hash||'').match(/^#k=([A-Za-z0-9_-]+)$/);if(m){token=m[1];LS.set('app_token',token);try{history.replaceState(null,'',location.pathname)}catch(e){}}})();
if(token)start();else showPair();
</script></body></html>"""
