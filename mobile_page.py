"""NZB Command Center phone app: the web page (HTML, CSS and script), served by mobile_server.py.

Kept out of mobile_server.py so the program itself stays readable. Keep this file next to it.
"""

PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes"><meta name="apple-mobile-web-app-status-bar-style" content="black-translucent"><meta name="theme-color" content="#060D10"><meta name="apple-mobile-web-app-title" content="NZB Center">
<title>NZB Command Center</title>
<link rel="manifest" href="/manifest.webmanifest"><link rel="icon" type="image/png" href="/icon.png"><link rel="apple-touch-icon" href="/icon.png">
<style>
:root{color-scheme:dark;--bg:#060D10;--side:#08141A;--card:#0E1C23;--hover:#13262F;--deep:#08141A;--line:#1A3440;--teal:#2DD4BF;--teal2:#5EEAD4;--ink:#041016;--white:#F0FDFA;--text:#C9DDE2;--soft:#A7C1C8;--muted:#7F9DA6;--red:#F87171;--amber:#FBBF24;--green:#34D399;--sky:#38BDF8;--purple:#C4B5FD;--mono:ui-monospace,"SF Mono","Cascadia Mono",Consolas,monospace}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
body{margin:0;background:var(--bg);color:var(--white);font:15px/1.4 -apple-system,BlinkMacSystemFont,"Segoe UI Variable","Segoe UI",Roboto,Inter,sans-serif;-webkit-text-size-adjust:100%}
button,input,select{font:inherit;color:inherit}
header{position:sticky;top:0;z-index:5;background:rgba(6,13,16,.92);backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px);padding:calc(14px + env(safe-area-inset-top)) 18px 12px}
.brand{display:flex;align-items:center;gap:10px;font-weight:700;font-size:17px;letter-spacing:-.01em}.brand img{width:30px;height:30px;border-radius:8px}
.statusbar{display:flex;gap:8px;margin-top:10px}.spill{display:inline-flex;align-items:center;gap:7px;background:var(--card);border-radius:20px;padding:6px 12px;font:600 12px var(--mono);color:var(--muted)}
.spill i{width:8px;height:8px;border-radius:4px;background:currentColor;display:inline-block}.spill.ok{color:var(--green)}.spill.warn{color:var(--amber)}.spill.bad{color:var(--red)}.spill.busy{color:var(--teal)}
main{max-width:720px;margin:auto;padding:4px 14px calc(150px + env(safe-area-inset-bottom))}
h1{font-size:26px;letter-spacing:-.02em;margin:10px 4px 2px}.lede{color:var(--soft);font-size:13px;margin:0 4px 14px}
.card{background:var(--card);border-radius:14px;padding:14px;margin-bottom:12px}
.label{display:block;font-size:12px;color:var(--soft);margin:0 0 6px}
.section{font-size:11px;font-weight:600;letter-spacing:.08em;color:var(--muted);margin:18px 4px 8px}
.field{width:100%;background:var(--deep);border:1px solid transparent;border-radius:10px;padding:12px 13px;font-size:16px;margin-bottom:10px;outline:none}.field:focus{border-color:var(--teal)}
.search{display:flex;align-items:center;gap:10px;background:var(--deep);border-radius:12px;padding:0 14px;min-height:50px;margin-bottom:10px;border:1px solid transparent}.search:focus-within{border-color:var(--teal)}
.search input{flex:1;background:transparent;border:0;outline:none;font-size:16px;min-width:0}
.row{display:flex;gap:10px;align-items:center}.row>*{flex:1;min-width:0}
.btn{border:0;border-radius:12px;background:var(--teal);color:var(--ink);font-weight:700;padding:13px 16px;font-size:15px;min-height:48px;cursor:pointer}
.btn.ghost{background:transparent;color:var(--white);border:1px solid var(--line);font-weight:600}
.btn.danger{background:transparent;color:var(--red);border:1px solid #5B2328;font-weight:600}
.btn.sm{min-height:40px;padding:8px 13px;font-size:13px;border-radius:10px}
.btn:disabled{opacity:.45}.btn:active:not(:disabled){transform:scale(.98)}.full{width:100%}
.icon-btn{width:44px;height:44px;border-radius:12px;border:0;background:var(--hover);color:var(--text);display:inline-flex;align-items:center;justify-content:center;flex:none}.icon-btn.red{color:var(--red)}
details.opts{background:var(--card);border-radius:14px;margin-bottom:12px}details.opts summary{list-style:none;cursor:pointer;padding:14px;display:flex;align-items:center;gap:10px;font-weight:600;font-size:14px}
details.opts summary::-webkit-details-marker{display:none}details.opts summary .chev{margin-left:auto;transition:transform .2s;color:var(--muted)}details.opts[open] summary .chev{transform:rotate(180deg)}
details.opts .body{padding:0 14px 14px}
.status{font-size:13px;color:var(--soft);margin:2px 4px 10px;white-space:pre-wrap}.status.error{color:var(--red)}
.result{background:var(--card);border-radius:14px;padding:14px;margin-bottom:10px;position:relative}
.result h3{font-size:15px;line-height:1.35;margin:0 40px 6px 0;overflow-wrap:anywhere;font-weight:600}
.details{color:var(--soft);font-size:13px;margin-bottom:10px;overflow-wrap:anywhere}
.badges{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:12px}.badge{font:600 11px var(--mono);padding:4px 8px;border-radius:6px;background:var(--deep);color:var(--text)}.badge.purple{background:#22183A;color:var(--purple)}
.check{position:absolute;top:10px;right:10px;width:40px;height:40px;display:flex;align-items:center;justify-content:center}.check input{width:22px;height:22px;accent-color:var(--teal)}
.result .btn.added{background:#0B2A22;color:var(--green)}
.tiles{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin-bottom:12px}
.tile{background:var(--card);border-radius:14px;padding:12px}.tile .k{font-size:11px;color:var(--soft)}.tile .v{font:600 19px var(--mono);margin-top:4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.tile .v small{font-size:12px;color:var(--soft)}
.hero{background:var(--card);border-radius:16px;padding:16px;margin-bottom:12px}
.tag{font:600 11px var(--mono);padding:4px 8px;border-radius:6px;letter-spacing:.03em}.tag.ok{background:#0B2A22;color:var(--green)}.tag.warn{background:#2E2410;color:var(--amber)}.tag.busy{background:#0B2230;color:var(--sky)}
.hero .name{font-weight:600;font-size:16px;margin:10px 0 4px;overflow-wrap:anywhere}.hero .sub{font:500 12px var(--mono);color:var(--soft)}
.bar{height:10px;background:var(--deep);border-radius:5px;overflow:hidden;margin:12px 0 10px}.bar span{display:block;height:100%;background:var(--teal);border-radius:5px;transition:width .6s ease}
.bar.thin{height:4px;margin:8px 0 0}.bar.thin span{background:var(--sky)}
.qrow{display:flex;align-items:center;gap:10px;background:var(--card);border-radius:12px;padding:12px;margin-bottom:8px}
.qrow .info{flex:1;min-width:0}.qrow .t{font-size:14px;font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.qrow .m{font:500 11px var(--mono);color:var(--soft);margin-top:3px}
.hrow{display:flex;align-items:flex-start;gap:12px;background:#0A171D;border-radius:12px;padding:12px;margin-bottom:8px}
.dot{width:9px;height:9px;border-radius:5px;margin-top:6px;flex:none}.hrow .info{flex:1;min-width:0}.hrow .t{font-size:14px;overflow-wrap:anywhere}.hrow .m{font-size:12px;color:var(--soft);margin-top:3px}.hrow .m.bad{color:var(--red)}
.empty{text-align:center;color:var(--soft);padding:34px 14px;font-size:13px}.empty svg{color:var(--teal);opacity:.7;margin-bottom:8px}.empty b{display:block;color:var(--white);font-size:16px;margin-bottom:4px}
.hidden{display:none!important}.hint{font-size:12px;color:var(--muted);line-height:1.5}
nav.tabs{position:fixed;left:0;right:0;bottom:0;z-index:6;display:flex;background:rgba(8,20,26,.96);backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px);border-top:1px solid #10242C;padding:6px 8px calc(6px + env(safe-area-inset-bottom))}
.tab{flex:1;min-width:0;background:transparent;border:0;color:var(--muted);display:flex;flex-direction:column;align-items:center;gap:3px;padding:6px 0;font-size:10.5px;font-weight:600;position:relative;min-height:52px}
.tab.active{color:var(--teal)}.tab .count{position:absolute;top:2px;left:calc(50% + 6px);background:var(--teal);color:var(--ink);font:700 10px var(--mono);padding:1px 6px;border-radius:9px}
.batch{position:fixed;left:12px;right:12px;bottom:calc(74px + env(safe-area-inset-bottom));z-index:7;max-width:696px;margin:auto;display:flex;align-items:center;gap:10px;background:var(--hover);border:1px solid var(--line);border-radius:16px;padding:10px 10px 10px 16px;box-shadow:0 10px 30px rgba(0,0,0,.45)}
.batch span{flex:1;font-weight:600;font-size:14px}
.toast{position:fixed;left:12px;right:12px;bottom:calc(82px + env(safe-area-inset-bottom));max-width:696px;margin:auto;background:var(--hover);border:1px solid var(--teal);border-radius:14px;padding:13px 15px;z-index:9;white-space:pre-wrap;font-size:14px;box-shadow:0 10px 30px rgba(0,0,0,.45)}.toast.err{border-color:var(--red)}
.pair{max-width:440px;margin:40px auto 0}.pair h1{margin-top:0}
.seg{display:flex;background:var(--card);border-radius:12px;padding:4px;margin:4px 0 14px}.seg button{flex:1;min-height:40px;border:0;border-radius:9px;background:transparent;color:var(--muted);font-weight:600;font-size:14px}.seg button.on{background:var(--hover);color:var(--white)}
.shortcut{width:100%;display:flex;align-items:center;gap:10px;background:var(--deep);border:1px solid var(--line);border-radius:14px;padding:0 14px;min-height:52px;color:var(--muted);font-size:16px;margin-bottom:12px;text-align:left}
.kode{font-size:11px;font-weight:700;letter-spacing:.08em;color:var(--muted)}
.ctrls{display:flex;align-items:center;gap:12px}.ctrls .tm{flex:1;font:500 12px var(--mono);color:var(--soft)}
.round{width:48px;height:48px;border-radius:24px;border:0;background:var(--hover);color:var(--white);display:inline-flex;align-items:center;justify-content:center;flex:none}.round.main{background:var(--teal);color:var(--ink)}
.kidle{display:flex;align-items:center;gap:12px;width:100%;border:0;text-align:left;color:var(--soft);font-size:14px}.kidle svg{color:var(--teal);flex:none}
.icon-btn.play{color:var(--teal2)}
.badge.lib{background:#0B2A22;color:var(--green)}
.recent{display:flex;flex-wrap:wrap;gap:8px;margin:-2px 0 12px}.recent button{border:1px solid var(--line);background:transparent;color:var(--text);border-radius:18px;padding:0 13px;min-height:36px;font-size:13px;max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.recent button.clear{color:var(--muted);border-color:transparent}
.np{position:relative;overflow:hidden}.np>*{position:relative;z-index:1}.np>.npbg{position:absolute;inset:0;z-index:0}.npbg img.art.loaded{opacity:.32}.npbg::after{content:"";position:absolute;inset:0;background:linear-gradient(90deg,var(--card) 25%,rgba(14,28,35,.55))}
.skel{background:var(--card);border-radius:14px;padding:14px;margin-bottom:10px}.skel i{display:block;height:12px;border-radius:6px;margin:8px 0;background:linear-gradient(90deg,#13262F 0%,#1B3540 50%,#13262F 100%);background-size:200% 100%;animation:sk 1.2s linear infinite}
@keyframes sk{from{background-position:200% 0}to{background-position:-200% 0}}
@media (prefers-reduced-motion:reduce){.skel i{animation:none}}
.rl{display:flex;flex-wrap:wrap;align-items:center;gap:5px;margin-top:5px;font-size:12px;color:var(--soft)}
.rc{font:700 11px var(--mono);padding:2px 6px;border-radius:5px;background:var(--hover);color:var(--text)}.rc.imdb{background:#F5C518;color:#000}.rc.tmdb{background:#0B2A3A;color:#7DD3FC}
.pthumb{width:46px;height:69px;border-radius:6px;border:0;padding:0;background:var(--hover);overflow:hidden;flex:none;cursor:pointer}
.qrow .info.tap,.nowrow .info.tap{cursor:pointer}
img.art{width:100%;height:100%;object-fit:cover;display:block;opacity:0;transition:opacity .25s}img.art.loaded{opacity:1}
.nowrow{display:flex;gap:12px;align-items:center}.nowrow .pthumb{width:52px;height:78px}.nowrow .info{flex:1;min-width:0}.nowrow .name{margin-top:4px}
.sheet{position:fixed;inset:0;z-index:20;background:rgba(0,0,0,.65);display:flex;align-items:flex-end;justify-content:center}
.sheet .panel{background:var(--bg);width:100%;max-width:720px;max-height:92vh;overflow-y:auto;border-radius:20px 20px 0 0;position:relative;padding-bottom:calc(22px + env(safe-area-inset-bottom))}
.fan{position:relative;aspect-ratio:16/9;background:var(--card);overflow:hidden;border:0;padding:0;width:100%;display:block}.fan::after{content:"";position:absolute;inset:0;background:linear-gradient(to bottom,rgba(6,13,16,0) 45%,var(--bg))}
.dhead{display:flex;gap:14px;padding:0 16px;margin-top:-64px;position:relative;z-index:1;align-items:flex-end}
.dposter{width:104px;height:156px;border-radius:10px;overflow:hidden;flex:none;background:var(--hover);border:0;padding:0;box-shadow:0 8px 24px rgba(0,0,0,.55);cursor:zoom-in}
.dtitle{min-width:0;padding-bottom:4px}.dtitle h2{margin:0;font-size:21px;line-height:1.2;letter-spacing:-.01em}.dtitle .m{font:500 12px var(--mono);color:var(--soft);margin-top:6px}
.chips{display:flex;flex-wrap:wrap;gap:6px;padding:14px 16px 0}.chip{font:600 12px var(--mono);padding:5px 9px;border-radius:7px;background:var(--card);color:var(--text);text-decoration:none}.chip.imdb{background:#F5C518;color:#000}
.dbody{padding:12px 16px 0}.tagline{font-style:italic;color:var(--soft);margin:0 0 8px}.plot{line-height:1.55;color:var(--text);margin:0 0 14px}
.facts{font-size:13px;color:var(--soft);line-height:1.6;margin-bottom:14px}.facts b{color:var(--white);font-weight:600}
.dclose{position:absolute;top:12px;right:12px;z-index:3;width:40px;height:40px;border-radius:20px;border:0;background:rgba(0,0,0,.6);color:#fff;display:flex;align-items:center;justify-content:center}
.viewer{position:fixed;inset:0;z-index:30;background:#000;display:flex;align-items:center;justify-content:center;border:0;padding:0;width:100%}.viewer img{max-width:100%;max-height:100%;object-fit:contain}
.rrow{display:flex;gap:8px;margin-top:8px}.rrow button,.remote button{flex:1;min-height:46px;border:0;border-radius:12px;background:var(--hover);color:var(--text);font-weight:600;font-size:13px;display:flex;align-items:center;justify-content:center}
.rrow button:active,.remote button:active{background:var(--line)}
.remote{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;max-width:270px;margin:12px auto 2px}.remote button{min-height:54px;font-size:18px}.remote .ok{background:var(--teal);color:var(--ink);font-size:14px}.remote .sm{font-size:12px;color:var(--soft)}
.rinfo{font:500 12px var(--mono);color:var(--soft);text-align:center;margin-top:10px;min-height:16px}
details.remotebox{background:var(--card);border-radius:14px;margin-bottom:12px}details.remotebox summary{list-style:none;cursor:pointer;padding:14px;display:flex;align-items:center;gap:10px;font-weight:600;font-size:14px}details.remotebox summary::-webkit-details-marker{display:none}details.remotebox .body{padding:0 14px 14px}details.remotebox summary .chev{margin-left:auto;color:var(--muted);transition:transform .2s}details.remotebox[open] summary .chev{transform:rotate(180deg)}
.cw{display:flex;gap:10px;overflow-x:auto;padding-bottom:6px;margin:0 -2px 8px;scroll-snap-type:x mandatory}
.citem{flex:none;width:136px;scroll-snap-align:start;background:var(--card);border-radius:12px;padding:8px;border:0;text-align:left;color:inherit;cursor:pointer}
.citem .ph{width:100%;aspect-ratio:2/3;border-radius:8px;overflow:hidden;background:var(--hover);margin-bottom:8px}
.citem .t{font-weight:600;font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.citem .m{font:500 11px var(--mono);color:var(--soft);margin-top:3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.citem .go{font:700 11px var(--mono);color:var(--teal);margin-top:6px}
details.season{border-top:1px solid #10242C}details.season summary{list-style:none;cursor:pointer;padding:12px 0;display:flex;align-items:center;gap:8px;font-weight:600;font-size:14px}details.season summary::-webkit-details-marker{display:none}details.season summary .m{margin-left:auto;font:500 11px var(--mono);color:var(--soft)}
.eprow{display:flex;align-items:center;gap:10px;padding:9px 0;border-top:1px solid #0E1F26}.eprow .n{font:600 12px var(--mono);color:var(--muted);width:30px;flex:none}.eprow .info{flex:1;min-width:0}.eprow .t{font-size:14px;overflow-wrap:anywhere}.eprow .m{font-size:12px;color:var(--soft);margin-top:2px}.eprow.w .t{color:var(--soft)}
.miss{background:#2E2410;color:var(--amber);border-radius:10px;padding:8px 10px 8px 12px;font-size:13px;margin:8px 0;display:flex;align-items:center;gap:10px}.miss span{flex:1;min-width:0;overflow-wrap:anywhere}.miss .btn{background:transparent;color:var(--amber);border:1px solid #5A4512;flex:none}
.badge.up{background:#0B2230;color:var(--sky)}
.rtop{display:flex;gap:12px;align-items:flex-start;margin-bottom:10px}.rtop .rbody{flex:1;min-width:0}.rtop .details{margin-bottom:0}
.rart{width:52px;height:78px;flex:none;border-radius:8px;position:relative;overflow:hidden;background:var(--deep);display:flex;flex-direction:column;align-items:center;justify-content:center;gap:5px;color:var(--teal)}
.rart small{font:700 9px var(--mono);letter-spacing:.04em;color:var(--soft)}.rart img.art{position:absolute;inset:0}
.rart.k-tv,.rart.k-app{color:var(--sky)}.rart.k-music{color:var(--purple)}.rart.k-book{color:var(--amber)}.rart.k-game{color:var(--red)}.rart.k-package{color:var(--muted)}
.tiles.two{grid-template-columns:repeat(2,minmax(0,1fr))}
.drow{display:block;width:100%;border:0;background:transparent;color:inherit;text-align:left;padding:12px 0;border-top:1px solid #10242C;cursor:pointer}.drow:first-of-type{border-top:0;padding-top:2px}
.dl{display:flex;align-items:center;gap:10px}.dl .lt{font:700 15px var(--mono);width:30px;flex:none}.dl .nm{flex:1;min-width:0;font-weight:600;font-size:14px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.dl .nm small{font-weight:400;color:var(--soft)}
.dsub{display:flex;justify-content:space-between;gap:8px;font-size:12px;color:var(--soft);padding-left:40px}.dsub b{color:var(--white);font-weight:600}
.dbar{height:6px;background:var(--deep);border-radius:3px;overflow:hidden;margin:8px 0 6px 40px}.dbar span{display:block;height:100%;border-radius:3px;background:var(--teal)}
.tag.bad{background:#2E1215;color:var(--red)}.tag.unk{background:var(--deep);color:var(--muted)}
.dcard{background:var(--card);border-radius:14px;padding:14px;margin-bottom:12px}
.dcard .top{display:flex;align-items:center;gap:12px}.dcard .lt{font:800 20px var(--mono);min-width:34px}.dcard .nm{flex:1;min-width:0}.dcard .nm b{display:block;font-size:16px}.dcard .nm span{font-size:12px;color:var(--soft)}
.dstats{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:6px;margin-top:10px}.dstats div{background:var(--deep);border-radius:9px;padding:8px 4px;text-align:center}.dstats b{display:block;font:700 14px var(--mono)}.dstats span{font-size:10px;color:var(--soft)}
.dnote{font-size:12px;color:var(--soft);margin-top:10px}.dnote.warn{color:#E9D9A8;background:#211B0B;border-radius:8px;padding:8px 10px}.dnote.bad{color:#FBC7C7;background:#2A1013;border-radius:8px;padding:8px 10px}
.banner{display:flex;gap:12px;align-items:flex-start;border-radius:14px;padding:14px;margin-bottom:12px}.banner.warn{background:#211B0B;border:1px solid #4A3B10}.banner.bad{background:#2A1013;border:1px solid #5B2328}.banner.ok{background:#0B2420;border:1px solid #14532D}
.banner b{display:block;font-size:15px}.banner.warn b{color:var(--amber)}.banner.bad b{color:var(--red)}.banner.ok b{color:var(--green)}.banner span{font-size:12px;color:var(--soft)}
.tgl{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:12px 0;border-top:1px solid #10242C;min-height:48px}.tgl:first-child{border-top:0}.tgl input{width:22px;height:22px;accent-color:var(--teal);flex:none}.tgl small{display:block;color:var(--soft);font-size:12px}
.legend{display:flex;flex-wrap:wrap;gap:12px;font-size:12px;color:var(--soft);margin-top:8px}.legend i{display:inline-block;width:9px;height:9px;border-radius:5px;margin-right:5px}
.dio{display:flex;gap:12px;font:500 12px var(--mono);color:var(--soft);margin-top:10px;flex-wrap:wrap}.dio b{color:var(--white);font-weight:600}.dio .busy{margin-left:auto}
.dcard.tap{cursor:pointer;border:0;width:100%;text-align:left;color:inherit;display:block}.dcard .more{font-size:12px;color:var(--teal);margin-top:10px;font-weight:600}
.kv{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:0;font-size:13px}.kv div{padding:9px 0;border-top:1px solid #10242C}.kv div:nth-child(-n+2){border-top:0}.kv div:nth-child(odd){color:var(--soft)}.kv div:nth-child(even){text-align:right;font-family:var(--mono);font-weight:600;color:var(--white)}.kv .bad{color:var(--red)!important}.kv .warn{color:var(--amber)!important}.kv .good{color:var(--green)!important}
.vrow{padding:10px 0;border-top:1px solid #10242C}.vrow:first-child{border-top:0}.vrow .h{display:flex;justify-content:space-between;gap:8px;font-size:13px}.vrow .h span{color:var(--soft);font-family:var(--mono);font-size:12px}
.bar.big{height:14px;border-radius:7px}.bar.big span{border-radius:7px}
.ready{display:inline-block;font:700 13px var(--mono);color:var(--teal);margin-top:6px}
.hdrbtn{position:absolute;right:14px;top:calc(12px + env(safe-area-inset-top));width:44px;height:44px;border-radius:12px;border:0;background:transparent;color:var(--soft);display:flex;align-items:center;justify-content:center}
.hdrbtn.on{color:var(--teal)}
.arr{flex:none;width:136px;scroll-snap-align:start;background:var(--card);border-radius:12px;padding:8px}
.arr .ph{width:100%;aspect-ratio:2/3;border-radius:8px;overflow:hidden;background:var(--hover);margin-bottom:8px;position:relative}
.arr .ph .newtag{position:absolute;top:6px;left:6px;font:800 10px var(--mono);background:var(--teal);color:var(--ink);padding:2px 6px;border-radius:5px}
.arr .t{font-weight:600;font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.arr .m{font:500 11px var(--mono);color:var(--soft);margin:3px 0 8px}
.arr .btn{width:100%;min-height:38px;padding:6px;font-size:12px}
.ptr{position:fixed;left:50%;top:calc(6px + env(safe-area-inset-top));z-index:8;transform:translate(-50%,-60px);background:var(--hover);border:1px solid var(--line);color:var(--text);border-radius:18px;padding:7px 14px;font-size:13px;font-weight:600;transition:transform .15s;pointer-events:none}
.ptr.show{transform:translate(-50%,var(--ptr,0px))}
.emptydl{display:flex;align-items:center;gap:14px}.emptydl svg{color:var(--teal);flex:none;opacity:.8}
.alfield{margin-bottom:12px}.altop{display:flex;gap:8px}.altop .field{margin:0}
.jgrid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin-bottom:12px}
.jq{background:var(--card);border:0;border-radius:14px;padding:12px;text-align:left;color:var(--white);display:flex;flex-direction:column;gap:5px;min-height:78px;cursor:pointer}
.jq b{font-size:14px}.jq span{font-size:12px;color:var(--soft)}.jq.main{background:var(--teal);color:var(--ink)}.jq.main span{color:#0B3B36}.jq.on{outline:2px solid var(--teal)}
.jq .ct{align-self:flex-start;font:700 11px var(--mono);background:var(--teal);color:var(--ink);padding:1px 7px;border-radius:9px}.jq .ct.warn{background:var(--amber)}
.qual{font:600 11px var(--mono);padding:3px 7px;border-radius:6px;background:var(--deep);color:var(--text)}.qual.lo{background:#2E2410;color:var(--amber)}
.jnow .meta{font:500 12px var(--mono);color:var(--soft);margin-top:3px}.tag.tx{background:#2E2410;color:var(--amber)}
.pills{display:flex;flex-wrap:wrap;gap:8px;margin:6px 0 4px}.pills button{border:1px solid var(--line);background:transparent;color:var(--text);border-radius:18px;padding:0 14px;min-height:38px;font-size:13px;font-weight:600}.pills button.on{background:var(--teal);color:var(--ink);border-color:var(--teal)}
.pov{font-size:12px;color:var(--muted);margin-top:5px;line-height:1.35;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
.upw{font-size:12px;color:var(--teal);font-weight:600;margin-top:4px}
.uptag{flex:none;font:800 10px var(--mono);padding:3px 7px;border-radius:6px;background:var(--hover);color:var(--soft)}.uptag.mv{color:#C4B5FD}.uptag.tv{color:var(--teal)}
.chk{display:flex;gap:10px;align-items:flex-start;margin:4px 2px 14px;font-size:14px;cursor:pointer}.chk input{width:20px;height:20px;margin:1px 0 0;accent-color:var(--teal);flex:none}.chk small{display:block;color:var(--soft);font-size:12px;margin-top:2px}
.pvrhead .name{font-weight:600;font-size:17px;margin-top:4px;overflow-wrap:anywhere}.pvrhead .sub{font:500 12px var(--mono);color:var(--soft);margin-top:3px}
</style></head><body>
<header><div class="brand"><img src="/icon.png" alt="">NZB Command Center</div><button id="alertsbtn" class="hdrbtn hidden" aria-label="Phone alerts"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 8a6 6 0 1 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/></svg></button><div id="statusbar" class="statusbar hidden"><span id="sabpill" class="spill"><i></i>SABnzbd</span></div></header>
<main>
<div id="pair" class="pair card"><h1>Connect</h1><p class="lede" style="margin-left:0">Mobile control for your PC · v__VERSION__</p><label class="label" for="token">Access code</label><input id="token" class="field" type="password" placeholder="Code shown on your PC" autocomplete="off" autocapitalize="off" autocorrect="off"><button class="btn full" id="pairbtn">Connect</button><div id="pairmsg" class="hint" style="margin-top:10px">The code is in the window running NZB Mobile Server. Opening its pairing link on this phone connects automatically.</div></div>
<div id="app" class="hidden">
<section id="home" class="hidden">
<h1 id="greet">Hello</h1><p class="lede" id="homestatus">Loading…</p>
<div id="homedl"></div>
<div class="tiles"><div class="tile"><div class="k">Done today</div><div class="v" id="hdone">—</div></div><div class="tile"><div class="k">Free space</div><div class="v" id="hfree">—</div></div><div class="tile"><div class="k">Failed today</div><div class="v" id="hfail">—</div></div></div>
<div class="tiles" id="homeusage"><div class="tile"><div class="k">Today</div><div class="v" id="husday">—</div></div><div class="tile"><div class="k">This week</div><div class="v" id="husweek">—</div></div><div class="tile"><div class="k">This month</div><div class="v" id="husmonth">—</div></div></div>
<button class="shortcut" data-tab="search"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/></svg>Search your indexers…</button>
<div id="homekodi"></div>
<div id="homejf"></div>
<div id="homearrived"></div>
<div id="homeup"></div>
<div id="homedrives"></div>
<p class="hint" id="homever" style="text-align:center;margin:22px 0 4px"></p>
</section>
<section id="jellyfin" class="hidden">
<div class="row" style="align-items:flex-end"><div><h1>Jellyfin</h1><p class="lede" id="jfstatus">Loading…</p></div><button class="btn ghost sm hidden" id="jfscan" style="flex:none;margin-bottom:14px">Scan libraries</button></div>
<div id="jfnow"></div>
<div id="jfbody"></div>
</section>
<section id="drives" class="hidden">
<div class="row" style="align-items:flex-end"><div><h1>Drives</h1><p class="lede" id="drivestatus">Loading…</p></div><button class="btn ghost sm" id="drivecheck" style="flex:none;margin-bottom:14px">Check now</button></div>
<div id="drivebody"></div>
</section>
<section id="search" class="hidden">
<h1>Search</h1><p class="lede">Your indexers, from anywhere at home.</p>
<div class="seg"><button class="on" data-tab="search">Indexers</button><button data-tab="pvr">Movies &amp; shows</button></div>
<div class="card">
<label class="search"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#7F9DA6" stroke-width="2.2" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/></svg><input id="query" placeholder="Movie, show or release" enterkeyhint="search" autocomplete="off" aria-label="Search"></label>
<div class="row"><select id="indexer" class="field" aria-label="Indexer"><option value="*">All indexers</option></select><select id="category" class="field" aria-label="Category"><option value="">All categories</option></select></div>
<div class="row"><button class="btn" id="searchbtn">Search</button></div>
<p class="hint" style="margin:10px 2px 0">Tip: “Show S02E04” or “Show S02” searches by episode or season.</p>
</div>
<div id="recent" class="recent"></div>
<div id="searchstatus" class="status"></div>
<details class="opts" id="resulttools" style="display:none"><summary><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M4 6h16M7 12h10M10 18h4"/></svg>Sort, filter &amp; send options<svg class="chev" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M6 9l6 6 6-6"/></svg></summary><div class="body">
<label class="label" for="sort">Sort</label><select id="sort" class="field"><option value="date_desc">Newest first</option><option value="size_desc">Largest first</option><option value="size_asc">Smallest first</option><option value="date_asc">Oldest first</option><option value="grabs_desc">Most grabs</option><option value="title_asc">Title A–Z</option><option value="title_desc">Title Z–A</option></select>
<div class="row"><div><label class="label" for="minsize">Min size (MB)</label><input id="minsize" class="field" type="number" min="0" placeholder="Any"></div><div><label class="label" for="maxsize">Max size (MB)</label><input id="maxsize" class="field" type="number" min="0" placeholder="Any"></div></div>
<label class="label" for="mingrabs">Min grabs</label><input id="mingrabs" class="field" type="number" min="0" placeholder="Any">
<div class="row"><div><label class="label" for="sabcat">SABnzbd category</label><select id="sabcat" class="field"><option value="">Auto (match)</option></select></div><div><label class="label" for="prio">Priority</label><select id="prio" class="field"><option value="">Default</option><option value="-1">Low</option><option value="0">Normal</option><option value="1">High</option><option value="2">Force</option></select></div></div>
<div class="row"><button class="btn ghost sm" id="selvisible">Select visible</button><button class="btn ghost sm" id="selnone">Clear selection</button></div>
<p class="hint" style="margin:10px 0 0">Up to 10 at a time. Releases found on several indexers are grouped, and a failed download falls back to the other indexer automatically.</p>
</div></details>
<div id="results"></div>
</section>
<section id="pvr" class="hidden">
<h1>Movies &amp; shows</h1><p class="lede" id="pvrstatus">Loading…</p>
<div class="seg"><button data-tab="search">Indexers</button><button class="on" data-tab="pvr">Movies &amp; shows</button></div>
<div id="pvrbody"></div>
</section>
<section id="queue" class="hidden">
<h1>Queue</h1><p class="lede" id="queuestatus">Loading…</p>
<div class="seg"><button class="on" data-tab="queue">Active</button><button data-tab="history">History</button></div>
<div id="queueitems"></div>
<div class="section">CONTROLS</div>
<div class="card"><div class="row"><button id="qresume" class="btn sm">Resume all</button><button id="qpause" class="btn ghost sm">Pause all</button></div>
<div class="row" style="margin-top:10px"><input id="speedmb" class="field" style="margin:0" type="number" min="0" step="0.5" placeholder="Limit in MB/s (0 = none)" aria-label="Speed limit in MB/s"><button id="speedset" class="btn ghost sm" style="flex:none">Set limit</button></div></div>
</section>
<section id="history" class="hidden">
<div class="row" style="align-items:flex-end"><div><h1>History</h1><p class="lede" id="histstatus">Recent downloads</p></div><button class="icon-btn" id="histrefresh" aria-label="Refresh history" style="flex:none;margin-bottom:14px"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" aria-hidden="true"><path d="M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7"/></svg></button></div>
<div class="seg" style="margin-top:0"><button data-tab="queue">Active</button><button class="on" data-tab="history">History</button></div>
<div id="historyitems"></div>
</section>
<section id="kodi" class="hidden">
<div class="row" style="align-items:flex-end"><div><h1>Kodi</h1><p class="lede" id="kodistatus">Loading…</p></div><button class="btn ghost sm" id="kodiscan" style="flex:none;margin-bottom:14px">Scan library</button></div>
<div id="tvbox" class="hidden">
<div class="row" id="tvbtns"><button class="btn" id="tvon"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" aria-hidden="true" style="vertical-align:-3px;margin-right:6px"><path d="M12 3v9M6.3 6.3a8 8 0 1 0 11.4 0"/></svg>TV on + Kodi</button><button class="btn ghost" id="tvkodi">Open Kodi</button></div>
<p class="hint" id="tvmsg" style="margin:6px 4px 10px"></p>
<details class="opts" id="tvsetup"><summary><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><rect x="2" y="5" width="20" height="13" rx="2"/><path d="M8 21h8"/></svg>TV setup<svg class="chev" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M6 9l6 6 6-6"/></svg></summary><div class="body">
<p class="hint" style="margin:0 0 10px">On the Sony TV: <b>Settings › Network › Remote start › On</b>, then <b>Settings › Network › Home network › IP control › Authentication › “Normal and Pre-Shared Key”</b> and set a <b>Pre-Shared Key</b>.</p>
<label class="label" for="tvhost">TV address</label><input id="tvhost" class="field" placeholder="192.168.1.40" autocomplete="off" autocapitalize="off" inputmode="url">
<label class="label" for="tvpsk">Pre-Shared Key</label><input id="tvpsk" class="field" type="password" placeholder="The key you set on the TV" autocomplete="off" autocapitalize="off">
<label class="label" for="tvmac">TV network (MAC) address <span style="color:var(--muted)">· optional, learned automatically</span></label><input id="tvmac" class="field" placeholder="AA:BB:CC:DD:EE:FF" autocomplete="off" autocapitalize="characters">
<button class="btn full" id="tvsave">Save &amp; test</button>
</div></details>
</div>
<div id="kodinow"></div>
<details class="remotebox hidden" id="kodiremote"><summary><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><rect x="7" y="2" width="10" height="20" rx="3"/><circle cx="12" cy="8" r="2"/><path d="M10 14h4M10 17h4"/></svg>Remote<svg class="chev" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M6 9l6 6 6-6"/></svg></summary><div class="body">
<div class="rrow"><button data-kact="seek_back" aria-label="Back 15 seconds">−15s</button><button data-kact="playpause" aria-label="Play or pause">Play / Pause</button><button data-kact="seek_fwd" aria-label="Forward 30 seconds">+30s</button></div>
<div class="rrow"><button data-kact="subs_next">Subtitles</button><button data-kact="subs_off">Subs off</button><button data-kact="audio_next">Audio track</button></div>
<div class="rrow"><button data-kact="vol_down" aria-label="Volume down">Vol −</button><button data-kact="mute">Mute</button><button data-kact="vol_up" aria-label="Volume up">Vol +</button></div>
<div class="remote"><button class="sm" data-kact="home">Home</button><button data-kact="up" aria-label="Up">▲</button><button class="sm" data-kact="info">Info</button><button data-kact="left" aria-label="Left">◀</button><button class="ok" data-kact="select">OK</button><button data-kact="right" aria-label="Right">▶</button><button class="sm" data-kact="back">Back</button><button data-kact="down" aria-label="Down">▼</button><button class="sm" data-kact="menu">Menu</button></div>
<div class="rinfo" id="kodirinfo"></div>
</div></details>
<div class="card hidden" id="kodytype"><label class="kode" for="kodytext" style="display:block;margin-bottom:8px">TYPE ON TV</label>
<input id="kodytext" class="field" style="margin-bottom:8px" placeholder="Text for the box open on the TV" autocomplete="off" autocapitalize="off" autocorrect="off" spellcheck="false" enterkeyhint="send" maxlength="500">
<div class="row"><button class="btn sm" id="kodysend">Send &amp; OK</button><button class="btn ghost sm" id="kodysendonly">Send only</button></div>
<p class="hint" style="margin:8px 2px 0">Select a text box on the TV first (a search, an address, a password). What you send replaces what's in it. “Send &amp; OK” also presses OK on Kodi's keyboard.</p></div>
<div class="seg hidden" id="kodiseg"><button class="on" data-kmode="movies">Movies</button><button data-kmode="tv">TV shows</button></div>
<div id="kodiTV" class="hidden">
<div class="section" id="cwtitle">CONTINUE WATCHING</div>
<div class="cw" id="kodicw"></div>
<label class="search"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#7F9DA6" stroke-width="2.2" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/></svg><input id="tvfilter" placeholder="Find a show" autocomplete="off" enterkeyhint="search" aria-label="Find a show"></label>
<div class="row" style="margin:4px 0 8px"><select id="tvview" class="field" style="margin:0" aria-label="Show"><option value="all">All shows (A–Z)</option><option value="recent">Recently watched</option><option value="unwatched">Unwatched episodes</option><option value="added">Newest added</option><option value="missing">Missing episodes</option></select></div>
<div class="section" id="tvlisttitle">SHOWS</div>
<div id="tvlist"></div>
<button class="btn ghost full hidden" id="tvmore" style="margin-top:4px">Show more</button>
</div>
<div id="kodilib" class="hidden">
<label class="search"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#7F9DA6" stroke-width="2.2" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/></svg><input id="kodifilter" placeholder="Find a movie or genre" autocomplete="off" enterkeyhint="search" aria-label="Find a movie"></label>
<div class="row" style="margin:4px 0 8px"><select id="kodiview" class="field" style="margin:0" aria-label="Show"><option value="recent">Recently added</option><option value="all">All movies (A–Z)</option><option value="newest">All, newest added first</option><option value="year">All, by release year</option><option value="rated">Highest rated</option><option value="unwatched">Unwatched</option><option value="progress">In progress</option></select></div>
<div class="section" id="kodilisttitle">RECENTLY ADDED</div>
<div id="kodilist"></div>
<button class="btn ghost full hidden" id="kodimore" style="margin-top:4px">Show more</button>
</div>
</section>
</div></main>
<div id="batchbar" class="batch hidden"><span id="batchcount">0 selected</span><button id="batchbtn" class="btn sm">Add to SABnzbd</button></div>
<nav class="tabs hidden" id="tabbar" aria-label="Sections">
<button id="tabhome" class="tab active" data-tab="home"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M3 11l9-7 9 7v9a1 1 0 0 1-1 1h-5v-6h-6v6H4a1 1 0 0 1-1-1z"/></svg>Home</button>
<button id="tabsearch" class="tab" data-tab="search"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/></svg>Search</button>
<button id="tabqueue" class="tab" data-tab="queue"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M12 3v12M7 10l5 5 5-5M5 20h14"/></svg>Queue<span id="queuecount" class="count hidden"></span></button>
<button id="tabkodi" class="tab" data-tab="kodi"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M10 9l5 3-5 3z"/></svg>Kodi</button>
<button id="tabjellyfin" class="tab" data-tab="jellyfin"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round" aria-hidden="true"><path d="M12 3l9.5 17h-19z"/><path d="M12 10l4 7H8z"/></svg>Jellyfin</button>
<button id="tabdrives" class="tab" data-tab="drives"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 14h18"/><circle cx="17" cy="17" r=".8" fill="currentColor"/><path d="M7 17h4"/></svg>Drives</button>
</nav>
<div id="toast" class="toast hidden" role="status"></div>
<div id="sheet" class="sheet hidden" role="dialog" aria-modal="true" aria-label="Movie details"><div class="panel" id="sheetpanel"></div></div>
<button id="viewer" class="viewer hidden" aria-label="Close picture"><img id="viewerimg" alt=""></button>
<script>
'use strict';
const LS={get:(k,d='')=>{try{const v=localStorage.getItem(k);return v===null?d:v}catch{return d}},set:(k,v)=>{try{localStorage.setItem(k,v)}catch{}}};
const $=id=>document.getElementById(id);
let token=LS.get('nzb_mobile_token');
let results=[],selected=new Set(),added=new Set(),activeTab='home',queueBusy=false,toastTimer=null,searchNote='',sabCats=[],lastQueue=[],kodiPid=-1;
const ICON={pause:'<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" aria-hidden="true"><path d="M8 5v14M16 5v14"/></svg>',play:'<svg width="17" height="17" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M7 5l12 7-12 7z"/></svg>',trash:'<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" aria-hidden="true"><path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/></svg>',retry:'<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" aria-hidden="true"><path d="M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7"/></svg>'};
ICON.top='<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" aria-hidden="true"><path d="M5 4h14M12 20V9M7 13l5-5 5 5"/></svg>';
ICON.more='<svg width="17" height="17" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><circle cx="5" cy="12" r="1.8"/><circle cx="12" cy="12" r="1.8"/><circle cx="19" cy="12" r="1.8"/></svg>';
ICON.x='<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>';
const EMPTY_ICON='<svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="M12 3v12M7 10l5 5 5-5M5 20h14"/></svg>';

(function pairFromLink(){const m=(location.hash||'').match(/^#k=([A-Za-z0-9_-]+)$/);if(m){token=m[1];LS.set('nzb_mobile_token',token);try{history.replaceState(null,'',location.pathname)}catch{}}})();

function esc(s){return String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function clampPct(v){return Math.max(0,Math.min(100,Number(v)||0))}
function toast(msg,isErr){const t=$('toast');t.textContent=msg;t.className='toast'+(isErr?' err':'');clearTimeout(toastTimer);toastTimer=setTimeout(()=>{t.className='toast hidden'},isErr?6000:3500)}
function skeleton(n,lines){let h='';for(let i=0;i<n;i++)h+='<div class="skel">'+'<i style="width:'+(85-i*7)+'%"></i><i style="width:55%"></i>'+(lines>2?'<i style="width:35%"></i>':'')+'</div>';return h}
function empty(title,hint){return '<div class="empty">'+EMPTY_ICON+'<b>'+esc(title)+'</b>'+esc(hint)+'</div>'}

const API_TIMEOUTS={'/api/home/play':150000,'/api/arr/add':90000,'/api/jellyfin/missing':90000,'/api/jellyfin/upgrades':60000,'/api/jellyfin/connect':30000,'/api/drives':70000,'/api/drives/check':70000,'/api/search':90000,'/api/add':120000,'/api/add-batch':300000};
async function api(path,body){
  const opt={method:body===undefined?'GET':'POST',headers:{'X-Mobile-Token':token}};
  if(body!==undefined){opt.headers['Content-Type']='application/json';opt.body=JSON.stringify(body)}
  // Without a timeout a dropped Wi-Fi connection left buttons stuck on "Searching…"/"Adding…" forever.
  const ctl=new AbortController(),timer=setTimeout(()=>ctl.abort(),API_TIMEOUTS[path]||30000);opt.signal=ctl.signal;
  let r;try{r=await fetch(path,opt)}catch(e){clearTimeout(timer);throw new Error(e&&e.name==='AbortError'?'The server took too long to answer. Check the queue before retrying an add.':'Cannot reach the server. Is the PC on and NZB Mobile Server running?')}
  clearTimeout(timer);
  let d={};try{d=await r.json()}catch{}
  if(r.status===401){showPair(d.error||'The access code was not accepted.');throw new Error(d.error||'Unauthorized')}
  if(!r.ok||d.error)throw new Error(d.error||('HTTP '+r.status));
  return d;
}
function showPair(msg){$('alertsbtn').classList.add('hidden');$('app').classList.add('hidden');$('tabbar').classList.add('hidden');$('batchbar').classList.add('hidden');$('pair').classList.remove('hidden');$('token').value='';if(msg)$('pairmsg').textContent=msg}
function pair(){const v=$('token').value.trim();if(!v)return;token=v;LS.set('nzb_mobile_token',token);connect()}

async function connect(){
  try{
    const d=await api('/api/status');
    $('pair').classList.add('hidden');$('app').classList.remove('hidden');$('tabbar').classList.remove('hidden');$('alertsbtn').classList.remove('hidden');
    const sel=$('indexer');sel.innerHTML='<option value="*">All indexers</option>'+d.indexers.map(x=>'<option value="'+esc(x.id)+'">'+esc(x.name)+'</option>').join('');
    catGroups=d.categories||{};
    loadPrefs();fillSearchCategories(JSON.parse(LS.get('nzb_mobile_prefs','{}')||'{}').category);
    api('/api/sab/info').then(i=>fillCategories(i.categories||[])).catch(()=>{});
    showTab(activeTab);pollStatus();
  }catch(e){if($('app').classList.contains('hidden'))$('pairmsg').textContent=e.message}
}
let catGroups={};
function fillSearchCategories(keep){
  const sel=$('category'),cur=keep!==undefined?keep:sel.value,groups=catGroups[$('indexer').value]||catGroups['*']||[];
  let h='<option value="">All categories</option>';
  for(const g of groups){
    if(!g.children||!g.children.length){h+='<option value="'+esc(g.id)+'">'+esc(g.name)+'</option>';continue}
    h+='<optgroup label="'+esc(g.name)+'"><option value="'+esc(g.id)+'">All '+esc(g.name)+'</option>'+g.children.map(c=>'<option value="'+esc(c.id)+'">'+esc(c.name)+'</option>').join('')+'</optgroup>';
  }
  sel.innerHTML=h;sel.value=[...sel.options].some(o=>o.value===cur)?cur:'';
}
function fillCategories(cats){sabCats=cats;const sel=$('sabcat'),keep=LS.get('nzb_mobile_cat');sel.innerHTML='<option value="">Auto (match)</option><option value="*">Default</option>'+cats.map(c=>'<option value="'+esc(c)+'">'+esc(c)+'</option>').join('');if(keep==='*'||cats.includes(keep))sel.value=keep}

function showTab(t){activeTab=t;for(const n of ['home','search','pvr','queue','history','kodi','drives','jellyfin'])$(n).classList.toggle('hidden',n!==t);const tab=t==='history'?'queue':(t==='pvr'?'search':t);document.querySelectorAll('.tab').forEach(b=>b.classList.toggle('active',b.dataset.tab===tab));updateBatchButton();window.scrollTo(0,0);if(t==='home')refreshHome();if(t==='queue')refreshQueue();if(t==='history')refreshHistory();if(t==='kodi')refreshKodi();if(t==='drives'){refreshDrives();refreshDriveIO();setTimeout(()=>{if(activeTab==='drives')refreshDriveIO()},3500)}if(t==='jellyfin')refreshJellyfin();if(t==='pvr')refreshPvr()}

function savePrefs(){LS.set('nzb_mobile_prefs',JSON.stringify({sort:$('sort').value,minsize:$('minsize').value,maxsize:$('maxsize').value,mingrabs:$('mingrabs').value,indexer:$('indexer').value,prio:$('prio').value,category:$('category').value}));LS.set('nzb_mobile_cat',$('sabcat').value)}
function loadPrefs(){try{const p=JSON.parse(LS.get('nzb_mobile_prefs','{}'));if(p.sort)$('sort').value=p.sort;if(p.prio!==undefined)$('prio').value=p.prio;if(p.indexer&&[...$('indexer').options].some(o=>o.value===p.indexer))$('indexer').value=p.indexer;['minsize','maxsize','mingrabs'].forEach(k=>{if(p[k]!==undefined)$(k).value=p[k]})}catch{}}

function filterSort(list,f){
  const min=(Number(f.min)||0)*1048576,max=(Number(f.max)||0)*1048576,grabs=Number(f.grabs)||0;
  const a=list.filter(x=>(!min||x.size_bytes>=min)&&(!max||x.size_bytes<=max)&&(!grabs||(Number(x.grabs)||0)>=grabs));
  const by={size_asc:(x,y)=>x.size_bytes-y.size_bytes,size_desc:(x,y)=>y.size_bytes-x.size_bytes,date_asc:(x,y)=>(x.pub_ts||0)-(y.pub_ts||0),date_desc:(x,y)=>(y.pub_ts||0)-(x.pub_ts||0),grabs_desc:(x,y)=>(Number(y.grabs)||0)-(Number(x.grabs)||0),title_asc:(x,y)=>x.title.localeCompare(y.title),title_desc:(x,y)=>y.title.localeCompare(x.title)};
  return a.sort(by[f.mode]||by.date_desc);
}
function currentFilter(){return {min:$('minsize').value,max:$('maxsize').value,grabs:$('mingrabs').value,mode:$('sort').value}}
function addOptions(){return {category:$('sabcat').value,priority:$('prio').value}}
function updateBatchButton(){const n=selected.size;$('batchcount').textContent=n+' selected'+(n>10?' (max 10)':'');$('batchbtn').disabled=n===0||n>10;$('batchbar').classList.toggle('hidden',n===0||activeTab!=='search')}

function relAge(ts){if(!ts)return '';const s=Date.now()/1000-ts;if(s<3600)return Math.max(1,Math.round(s/60))+'m ago';if(s<86400)return Math.round(s/3600)+'h ago';if(s<86400*30)return Math.round(s/86400)+'d ago';return new Date(ts*1000).toLocaleDateString()}
function qualityTags(title){const t=String(title),out=[];const q=t.match(/\b(2160p|1080p|720p)\b/i);if(q)out.push(q[1].toLowerCase());if(/\b(dv|dovi|dolby.?vision)\b/i.test(t))out.push('DV');else if(/\bhdr/i.test(t))out.push('HDR');if(/\b(x265|h\.?265|hevc)\b/i.test(t))out.push('HEVC');else if(/\b(x264|h\.?264)\b/i.test(t))out.push('x264');if(/\bremux\b/i.test(t))out.push('REMUX');else if(/\b(bluray|bdrip)\b/i.test(t))out.push('BluRay');else if(/\bweb/i.test(t))out.push('WEB');return out.slice(0,4)}
// Placeholder "clipart" per kind of release (shown until, or instead of, a poster) - same kinds as the desktop app.
const KI=p=>'<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'+p+'</svg>';
const KINDS={
  movie:[KI('<rect x="3" y="6" width="18" height="14" rx="2"/><path d="M3 10h18M7 6l2 4M12 6l2 4M17 6l2 4"/>'),'MOVIE'],
  tv:[KI('<rect x="3" y="7" width="18" height="12" rx="2"/><path d="M8 3l4 4 4-4"/>'),'TV'],
  music:[KI('<path d="M9 18V5l11-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="17" cy="16" r="3"/>'),'MUSIC'],
  book:[KI('<path d="M5 4h11a3 3 0 0 1 3 3v13H8a3 3 0 0 1-3-3z"/><path d="M5 17a3 3 0 0 1 3-3h11"/>'),'BOOK'],
  game:[KI('<rect x="2" y="7" width="20" height="11" rx="5"/><path d="M7 11v3M5.5 12.5h3"/><circle cx="16" cy="11.5" r=".8"/><circle cx="18" cy="13.5" r=".8"/>'),'GAME'],
  app:[KI('<rect x="3" y="4" width="18" height="13" rx="2"/><path d="M8 21h8M12 17v4M9 9l-2 2 2 2M15 9l2 2-2 2"/>'),'APP'],
  package:[KI('<path d="M3 7l9-4 9 4v10l-9 4-9-4z"/><path d="M3 7l9 4 9-4M12 11v10"/>'),'NZB']
};
function resultCard(x){
  const done=added.has(x.id);
  const details=[x.indexer,x.size,(x.grabs||0)+' grabs',relAge(x.pub_ts)||x.date].filter(Boolean).map(esc).join(' · ');
  let badges=qualityTags(x.title).map(t=>'<span class="badge">'+esc(t)+'</span>').join('');
  if(x.in_library)badges='<span class="badge lib">'+(x.in_library==='episode'?'Episode already in Kodi':'Already in Kodi')+'</span>'+badges;
  if(x.also_found&&x.also_found.length)badges+='<span class="badge purple">Also on '+esc(x.also_found.join(', '))+'</span>';
  if(upgradeFrom&&isUpgrade(x.title))badges='<span class="badge up">Better than your '+esc(upgradeFrom.q||'copy')+'</span>'+badges;
  const kind=KINDS[x.kind]?x.kind:'package';
  const art='<div class="rart k-'+kind+'" role="img" aria-label="'+esc(KINDS[kind][1]+(x.res?' '+x.res:''))+'">'+KINDS[kind][0]+'<small>'+esc(x.res||KINDS[kind][1])+'</small>'+(x.art?'<img class="art" data-art="r:'+esc(x.id)+'" alt="">':'')+'</div>';
  return '<article class="result" data-id="'+esc(x.id)+'"><label class="check" aria-label="Select"><input type="checkbox" data-act="sel"'+(selected.has(x.id)?' checked':'')+(done?' disabled':'')+'></label><div class="rtop">'+art+'<div class="rbody"><h3>'+esc(x.title)+'</h3><div class="details">'+details+'</div></div></div>'+(badges?'<div class="badges">'+badges+'</div>':'')+'<button class="btn full'+(done?' added':'')+'" data-act="add"'+(done?' disabled':'')+'>'+(done?'Added ✓':'Add to SABnzbd')+'</button></article>';
}
function renderResults(){
  $('resulttools').style.display=results.length?'block':'none';
  const a=filterSort(results,currentFilter());
  if(results.length)$('searchstatus').textContent='Showing '+a.length+' of '+results.length+' result(s)'+searchNote;
  $('results').innerHTML=a.map(resultCard).join('')||(results.length?empty('Nothing matches these filters','Clear the size or grabs limits to see all '+results.length+' result(s).'):'');
  hydrateArt($('results'));updateBatchButton();
}
function recentList(){try{const a=JSON.parse(LS.get('nzb_mobile_recent','[]'));return Array.isArray(a)?a:[]}catch{return []}}
function rememberSearch(){
  const q=$('query').value.trim();if(!q)return;
  const e={q,ix:$('indexer').value,cat:$('category').value};
  const a=[e].concat(recentList().filter(x=>x.q.toLowerCase()!==q.toLowerCase())).slice(0,8);
  LS.set('nzb_mobile_recent',JSON.stringify(a));renderRecent();
}
function renderRecent(){
  const a=recentList();
  $('recent').innerHTML=a.length?a.map((x,i)=>'<button data-recent="'+i+'" aria-label="Search again for '+esc(x.q)+'">'+esc(x.q)+'</button>').join('')+'<button class="clear" data-recent="clear">Clear</button>':'';
}
function useRecent(i){
  if(i==='clear'){LS.set('nzb_mobile_recent','[]');renderRecent();return}
  const x=recentList()[Number(i)];if(!x)return;
  $('query').value=x.q;
  if([...$('indexer').options].some(o=>o.value===x.ix)){$('indexer').value=x.ix;fillSearchCategories(x.cat)}
  search();
}
// ---------- "Find a better copy" ----------
let upgradeFrom=null;
const RES_RANK={'2160p':4,'1080p':3,'720p':2,'SD':1};
function resOf(title){const m=String(title).match(/\b(2160p|1080p|720p)\b/i);return m?m[1].toLowerCase():(/\b(4k|uhd)\b/i.test(title)?'2160p':'')}
function isUpgrade(title){
  if(!upgradeFrom||!RES_RANK[upgradeFrom.q])return false;  // unknown quality of your copy: don't guess
  const have=RES_RANK[upgradeFrom.q],res=RES_RANK[resOf(title)]||0;
  if(res>have)return true;
  return res===have&&res>0&&!upgradeFrom.hdr&&/\b(hdr|dv|dovi|dolby.?vision)\b/i.test(title);
}
function goSearch(q,opts){
  opts=opts||{};showTab('search');$('query').value=q;
  // Use the requested category; if this indexer doesn't offer it, search all categories rather
  // than silently keeping an unrelated one (e.g. a movie search inside "TV > HD").
  if(opts.category!==undefined)$('category').value=[...$('category').options].some(o=>o.value===opts.category)?opts.category:'';
  search(opts);
}
async function search(extra){
  extra=(extra&&!(extra instanceof Event))?extra:{};
  if(!extra.keepUpgrade)upgradeFrom=null;
  searchNote='';
  const st=$('searchstatus');st.className='status';st.textContent='Searching…';$('results').innerHTML=skeleton(3,3);rememberSearch();results=[];selected.clear();added.clear();updateBatchButton();
  $('searchbtn').disabled=true;$('query').blur();
  try{
    const d=await api('/api/search',{query:$('query').value.trim(),indexer:$('indexer').value,category:$('category').value,imdbid:extra.imdbid||''});
    results=d.results||[];
    searchNote=(d.mode==='tvsearch'?' · episode search':(d.mode==='movie'?' · matched by IMDb id':''));
    if(upgradeFrom)searchNote+=' · '+results.filter(x=>isUpgrade(x.title)).length+' better than your '+(upgradeFrom.q||'copy');
    if(d.errors&&d.errors.length)searchNote+=' · '+d.errors.length+' indexer error(s)\n'+d.errors.join('\n');
    let msg=results.length+' unique result(s)'+searchNote;
    st.textContent=msg;if(d.errors&&d.errors.length&&!results.length)st.className='status error';
    renderResults();
    if(!results.length&&!(d.errors&&d.errors.length))$('results').innerHTML=empty('No releases found','Try a shorter title or a different category.');
  }catch(e){st.textContent=e.message;st.className='status error'}
  finally{$('searchbtn').disabled=false}
}
async function addResult(id,btn){
  btn.disabled=true;btn.textContent='Adding…';
  try{const d=await api('/api/add',Object.assign({id},addOptions()));added.add(id);selected.delete(id);toast('Added to SABnzbd'+(d.category?' → '+d.category:'')+(d.indexer?' (from '+d.indexer+')':''));renderResults();refreshQueue(true)}
  catch(e){btn.disabled=false;btn.textContent='Add to SABnzbd';toast(e.message,true)}
}
async function addSelected(){
  const ids=[...selected];if(!ids.length||ids.length>10)return;
  const b=$('batchbtn');b.disabled=true;b.textContent='Adding…';
  try{
    const d=await api('/api/add-batch',Object.assign({ids},addOptions()));
    (d.added||[]).forEach(x=>{added.add(x.id);selected.delete(x.id)});
    let msg='Added '+(d.added||[]).length+' of '+ids.length+' NZB(s).';
    if(d.errors&&d.errors.length)msg+='\n'+d.errors.map(x=>(x.title||x.id)+': '+x.error).join('\n');
    toast(msg,!!(d.errors&&d.errors.length));renderResults();refreshQueue(true);
  }catch(e){toast(e.message,true)}
  finally{b.textContent='Add to SABnzbd';updateBatchButton()}
}

function stateTag(status){const s=String(status||'').toLowerCase();const cls=s==='downloading'?'ok':(s==='paused'?'warn':'busy');return '<span class="tag '+cls+'">'+esc(String(status||'Queued').toUpperCase())+'</span>'}
function heroCard(x){
  const p=clampPct(x.percentage),paused=String(x.status).toLowerCase()==='paused';
  return '<div class="hero queueitem" data-id="'+esc(x.id)+'">'+stateTag(x.status)+'<div class="name">'+esc(x.name)+'</div><div class="sub">'+esc(x.sizeleft)+' of '+esc(x.size)+' left · ETA '+esc(x.eta||'—')+(x.cat&&x.cat!=='*'?' · '+esc(x.cat):'')+'</div><div class="bar big"><span style="width:'+p+'%"></span></div>'+(!paused&&readyAt(x.eta)?'<div class="ready" style="margin:0 0 8px">'+esc(readyAt(x.eta))+'</div>':'')+'<div class="row"><span style="flex:1;font:600 14px var(--mono);color:var(--teal)">'+p.toFixed(0)+'%</span><button class="icon-btn" data-act="more" aria-label="Category and priority">'+ICON.more+'</button><button class="btn ghost sm" style="flex:none" data-act="'+(paused?'resume':'pause')+'">'+(paused?'Resume':'Pause')+'</button><button class="btn danger sm" style="flex:none" data-act="delete">Delete</button></div></div>';
}
function queueRow(x){
  const p=clampPct(x.percentage),paused=String(x.status).toLowerCase()==='paused';
  return '<div class="qrow queueitem" data-id="'+esc(x.id)+'"><div class="info"><div class="t">'+esc(x.name)+'</div><div class="m">'+esc(String(x.status||'').toUpperCase())+' · '+esc(x.size)+(x.eta?' · '+esc(x.eta):'')+(!paused&&readyAt(x.eta)?' · '+esc(readyAt(x.eta)):'')+(x.cat&&x.cat!=='*'?' · '+esc(x.cat):'')+'</div><div class="bar thin"><span style="width:'+p+'%"></span></div></div><button class="icon-btn" data-act="more" aria-label="More options: move to top, category, priority">'+ICON.more+'</button><button class="icon-btn" data-act="'+(paused?'resume':'pause')+'" aria-label="'+(paused?'Resume':'Pause')+'">'+(paused?ICON.play:ICON.pause)+'</button><button class="icon-btn red" data-act="delete" aria-label="Delete">'+ICON.trash+'</button></div>';
}
async function refreshQueue(silent){
  if(queueBusy)return;queueBusy=true;const st=$('queuestatus');
  try{
    const d=await api('/api/queue');
    st.className='lede';
    st.textContent=(d.total?d.total+' in queue':'Queue is empty')+(d.speed_limit?' · limit '+d.speed_limit:'');
    updateStatus(d);
    const slots=d.slots||[];
    const free=d.disk_free_gb!=null&&d.disk_free_gb>=0?Number(d.disk_free_gb).toFixed(0):'—';
    const sp=String(d.speed||'0 B/s').split(' ');
    let html='<div class="tiles"><div class="tile"><div class="k">Speed</div><div class="v" style="color:var(--teal)">'+esc(sp[0])+' <small>'+esc(sp.slice(1).join(' '))+'</small></div></div><div class="tile"><div class="k">Queued</div><div class="v">'+esc(d.total||0)+'</div></div><div class="tile"><div class="k">Free space</div><div class="v">'+esc(free)+' <small>GB</small></div></div></div>';
    if(!slots.length){html+=empty('Nothing downloading','Search for something and add it to SABnzbd.')}
    else{
      const hi=Math.max(0,slots.findIndex(s=>String(s.status).toLowerCase()==='downloading'));
      html+=heroCard(slots[hi]);
      const rest=slots.filter((_,i)=>i!==hi);
      if(rest.length)html+='<div class="section">UP NEXT</div>'+rest.map(queueRow).join('');
    }
    lastQueue=slots;$('queueitems').innerHTML=html;
  }catch(e){if(!silent||activeTab==='queue'){st.textContent=e.message;st.className='lede status error'}}
  finally{queueBusy=false}
}
function updateStatus(d){
  const pill=$('sabpill'),state=String(d.state||'Idle'),low=state.toLowerCase();
  pill.className='spill '+(low==='paused'?'warn':(low==='downloading'?'ok':(d.total?'busy':'')));
  pill.innerHTML='<i></i>'+esc(state)+' · '+esc(d.speed||'0 B/s')+(d.total?' · '+esc(d.total)+' queued':'');
  $('statusbar').classList.remove('hidden');
  const c=$('queuecount');c.textContent=d.total||'';c.classList.toggle('hidden',!d.total);
}
async function pollStatus(){if(document.hidden||$('app').classList.contains('hidden')||activeTab==='queue'||activeTab==='home')return;try{updateStatus(await api('/api/queue'))}catch{const p=$('sabpill');p.className='spill bad';p.innerHTML='<i></i>SABnzbd offline'}}
async function itemAction(id,action){
  if(action==='more')return openQueueItem(id);
  if(action==='top'){try{await api('/api/queue/item',{id,action});toast('Moved to the top of the queue');await refreshQueue(true)}catch(e){toast(e.message,true)}return}
  if(action==='delete'&&!confirm('Remove this item from the queue and delete its partial files?'))return;
  try{await api('/api/queue/item',{id,action});await refreshQueue(true)}catch(e){toast(e.message,true)}
}
async function queueAll(action){try{await api('/api/queue/'+action,{});await refreshQueue(true)}catch(e){toast(e.message,true)}}
async function setSpeed(){
  const v=$('speedmb').value.trim();if(v==='')return;
  try{await api('/api/speedlimit',{mbps:Number(v)});toast(Number(v)>0?'Speed limit set to '+v+' MB/s':'Speed limit removed');await refreshQueue(true)}catch(e){toast(e.message,true)}
}

function historyRow(x){
  const ok=x.status==='Completed',bad=x.status==='Failed';
  const color=ok?'var(--green)':(bad?'var(--red)':'var(--amber)');
  const when=x.completed?relAge(x.completed):'';
  const note=bad&&x.fail_message?x.fail_message:[x.status,x.size,x.cat&&x.cat!=='*'?x.cat:''].filter(Boolean).join(' · ');
  return '<div class="hrow queueitem" data-id="'+esc(x.id)+'"><span class="dot" style="background:'+color+'"></span><div class="info"><div class="t">'+esc(x.name)+'</div><div class="m'+(bad?' bad':'')+'">'+esc(note)+(when?' · '+esc(when):'')+'</div></div>'+(bad?'<button class="icon-btn" data-act="retry" aria-label="Retry">'+ICON.retry+'</button>':'')+'<button class="icon-btn red" data-act="hdel" aria-label="Remove from history">'+ICON.trash+'</button></div>';
}
async function refreshHistory(){
  const st=$('histstatus');
  try{const d=await api('/api/history');st.className='lede';st.textContent=d.slots.length+' recent download(s)';$('historyitems').innerHTML=d.slots.map(historyRow).join('')||empty('No history yet','Finished and failed downloads show up here.')}
  catch(e){st.textContent=e.message;st.className='lede status error'}
}
// ---------- Home ----------
let homeBusy=false;
function fmtTime(s){s=Math.max(0,Math.floor(Number(s)||0));const h=Math.floor(s/3600),m=Math.floor(s%3600/60),x=s%60;return (h?h+':'+String(m).padStart(2,'0'):m)+':'+String(x).padStart(2,'0')}
function nowCard(p,compact){
  if(!p)return compact?'<button class="card kidle" data-tab="kodi">'+ICON.play+'<span>Nothing playing on Kodi · open your library</span></button>':'<div class="card kidle">'+ICON.play+'<span>Nothing playing right now.</span></div>';
  const pp=p.paused?ICON.play:ICON.pause;
  const det=p.movieid?' data-detail="'+esc(p.movieid)+'"':'';
  return '<div class="hero np">'+(p.fanart?'<div class="npbg">'+artTag(p.fanart)+'</div>':'')+'<div class="nowrow">'+(p.poster?'<button class="pthumb"'+det+' aria-label="Details for '+esc(p.title)+'">'+artTag(p.poster)+'</button>':'')+'<div class="info'+(det?' tap':'')+'"'+det+'><div class="kode">'+(compact?'NOW ON KODI':(p.paused?'PAUSED':'NOW PLAYING'))+'</div><div class="name">'+esc(p.title)+'</div>'+(p.sub?'<div class="sub">'+esc(p.sub)+'</div>':'')+'</div></div>'+
    '<div class="bar"><span style="width:'+clampPct(p.pct)+'%"></span></div><div class="ctrls"><span class="tm">'+fmtTime(p.time)+' / '+fmtTime(p.total)+'</span>'+
    (compact?'':'<button class="round" data-kact="stop" data-pid="'+esc(p.playerid)+'" aria-label="Stop"><svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><rect x="6" y="6" width="12" height="12" rx="2"/></svg></button>')+
    '<button class="round main" data-kact="playpause" data-pid="'+esc(p.playerid)+'" aria-label="'+(p.paused?'Play':'Pause')+'">'+pp+'</button></div></div>';
}
// "0:02:44" (SABnzbd's time left) -> "ready ~9:41 PM" (tomorrow/day added when it's not today)
function etaSecs(eta){const p=String(eta||'').trim().split(':').map(Number);if(!p.length||p.some(isNaN))return 0;return p.reduce((a,b)=>a*60+b,0)}
function readyAt(eta){
  const s=etaSecs(eta);if(!s)return '';
  const t=new Date(Date.now()+s*1000),now=new Date(),hm=t.toLocaleTimeString([],{hour:'numeric',minute:'2-digit'});
  const day=t.toDateString()===now.toDateString()?'':(new Date(now.getTime()+86400000).toDateString()===t.toDateString()?' tomorrow':' '+t.toLocaleDateString([],{weekday:'short'}));
  return 'ready ~'+hm+day;
}
function homeDownload(d){
  const c=d.current;
  const btn=d.paused?'<button class="btn sm" data-hact="resume">Resume all</button>':'<button class="btn ghost sm" data-hact="pause">Pause all</button>';
  const qbtn='<button class="btn ghost sm" data-tab="queue">Queue'+(d.total?' ('+esc(d.total)+')':'')+'</button>';
  if(!c)return '<div class="hero"><div class="emptydl"><svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><path d="M12 3v12M7 10l5 5 5-5M5 20h14"/></svg><div><div class="kode">DOWNLOADS</div><div class="name" style="margin-top:4px">All caught up</div><div class="sub">Nothing downloading right now.</div></div></div><div class="row" style="margin-top:14px"><button class="btn sm" data-tab="search">Find something</button>'+(d.paused?btn:'')+qbtn+'</div></div>';
  const low=String(d.state||'').toLowerCase(),tag=low==='paused'?'warn':(low==='downloading'?'ok':'busy');
  return '<div class="hero"><div class="row" style="justify-content:space-between"><span class="tag '+tag+'" style="flex:none">'+esc(String(d.state||'').toUpperCase())+'</span><span style="flex:none;font:700 18px var(--mono);color:var(--teal)">'+esc(d.speed||'')+'</span></div>'+
    '<div class="name">'+esc(c.name)+'</div><div class="bar big"><span style="width:'+clampPct(c.pct)+'%"></span></div>'+
    '<div class="sub">'+esc(Math.round(c.pct||0))+'% · '+esc(c.sizeleft||'?')+' left of '+esc(c.size||'?')+(c.eta?' · '+esc(c.eta):'')+'</div>'+
    (low!=='paused'&&readyAt(c.eta)?'<div class="ready">'+esc(readyAt(c.eta))+'</div>':'')+'<div class="row" style="margin-top:12px">'+btn+qbtn+'</div></div>';
}
let arrivedKey='';
function renderArrived(items,tvReady){
  const box=$('homearrived'),key=JSON.stringify(items.map(x=>[x.id,x.watched]));
  if(key===arrivedKey)return;arrivedKey=key;   // Home refreshes every 5 s: don't redraw (and refetch posters) for nothing
  if(!items.length){box.innerHTML='';return}
  box.innerHTML='<div class="section">JUST ARRIVED</div><div class="cw">'+items.map(x=>'<div class="arr"><div class="ph">'+artTag(x.poster)+(x.new&&!x.watched?'<span class="newtag">NEW</span>':'')+'</div><div class="t">'+esc(x.title)+'</div><div class="m">'+esc([x.year,x.quality].filter(Boolean).join(' · ')||'\u00a0')+'</div><button class="btn sm" data-hplay="'+esc(x.id)+'">'+ICON.play+' Play on TV</button></div>').join('')+'</div>'+
    (tvReady?'':'<p class="hint" style="margin:2px 4px 0">Kodi has to be open on the TV. Set up “TV on” on the Kodi tab and Play on TV turns the TV on for you.</p>');
  hydrateArt(box);
}
async function homePlay(b){
  b.disabled=true;const old=b.innerHTML;b.textContent='Starting…';
  toast('Starting it on the TV… (turning the TV on first if needed, this can take a minute)');
  try{const r=await api('/api/home/play',{id:b.dataset.hplay});toast(r.started_tv?'The TV is on and the movie is starting.':'Playing on the TV.');setTimeout(refreshHome,2500)}
  catch(e){toast(e.message,true)}
  finally{b.disabled=false;b.innerHTML=old}
}
async function refreshHome(){
  if(homeBusy)return;homeBusy=true;
  const hr=new Date().getHours();$('greet').textContent=hr<12?'Good morning':(hr<18?'Good afternoon':'Good evening');
  try{
    const d=await api('/api/home');
    if(d.sab_ok){updateStatus(d);$('homestatus').className='lede';$('homestatus').textContent=d.total?d.total+' in queue':'Queue is empty';$('homedl').innerHTML=homeDownload(d)}
    else{const p=$('sabpill');p.className='spill bad';p.innerHTML='<i></i>SABnzbd offline';$('statusbar').classList.remove('hidden');$('homestatus').className='lede status error';$('homestatus').textContent=d.sab_error||'SABnzbd is offline';$('homedl').innerHTML=''}
    $('hdone').textContent=d.done_today!=null?d.done_today:'—';
    $('hfail').textContent=d.failed_today!=null?d.failed_today:'—';$('hfail').style.color=d.failed_today?'var(--red)':'';
    $('hfree').innerHTML=d.disk_free_gb!=null&&d.disk_free_gb>=0?esc(Number(d.disk_free_gb).toFixed(0))+' <small>GB</small>':'—';
    const k=d.kodi||{};
    $('homekodi').innerHTML=!k.configured?'':(k.online?nowCard(k.playing,true):'<button class="card kidle" data-tab="kodi">'+ICON.play+'<span>Kodi is offline</span></button>');hydrateArt($('homekodi'));
    const js=d.jellyfin||[];
    $('homejf').innerHTML=js.length?'<div class="section">JELLYFIN · '+js.length+' WATCHING</div>'+js.map(jfNowCard).join(''):'';hydrateArt($('homejf'));
    renderArrived(d.arrived||[],d.tv_ready);renderHomeUp(d.upcoming||[]);
    if(d.version)$('homever').textContent='NZB Command Center phone app · v'+d.version;
    loadHomeUsage();loadHomeDrives();
  }catch(e){$('homestatus').className='lede status error';$('homestatus').textContent=e.message}
  finally{homeBusy=false}
}

// ---------- Kodi artwork (fetched with the access code, shown as blob: URLs) ----------
const artCache=new Map(),artQueue=[];let artActive=0;
function artURL(ref){
  if(!artCache.has(ref))artCache.set(ref,new Promise((res,rej)=>{artQueue.push({ref,res,rej});pumpArt()}));
  return artCache.get(ref);
}
function pumpArt(){
  while(artActive<4&&artQueue.length){
    const j=artQueue.shift();artActive++;
    // "r:<id>" = a search result's poster (the server picks the source); anything else = Kodi artwork.
    const src=j.ref.startsWith('t:')?'/api/tv/poster?id='+encodeURIComponent(j.ref.slice(2)):j.ref.startsWith('r:')?'/api/search/art?id='+encodeURIComponent(j.ref.slice(2)):(j.ref.startsWith('j:')?'/api/jellyfin/art?id='+encodeURIComponent(j.ref.slice(2)):j.ref.startsWith('a:')?'/api/arr/art?ref='+encodeURIComponent(j.ref.slice(2)):'/api/kodi/art?u='+encodeURIComponent(j.ref));
    fetch(src,{headers:{'X-Mobile-Token':token}})
      .then(r=>{if(r.status===404)return null;if(!r.ok)throw new Error('HTTP '+r.status);return r.blob()})
      .then(b=>{if(!b){j.res(null);return}const u=URL.createObjectURL(b);artReady.set(j.ref,u);trimArt();j.res(u)})  // null = no poster: keep the placeholder, don't ask again
      .catch(e=>{artCache.delete(j.ref);j.rej(e)}).finally(()=>{artActive--;pumpArt()});
  }
}
const artReady=new Map(),ART_KEEP=150;
// Every poster is kept in memory as a blob: URL. Scrolling a big library would otherwise keep
// adding hundreds of MB until iOS closes the home-screen app. Keep the newest ART_KEEP and let go
// of older ones that aren't on screen (they're fetched again if they come back into view).
function trimArt(){
  if(artReady.size<=ART_KEEP)return;
  const shown=new Set([...document.querySelectorAll('img[data-art]')].map(i=>i.dataset.art));
  for(const [ref,u] of artReady){
    if(artReady.size<=ART_KEEP)break;
    if(shown.has(ref))continue;
    artReady.delete(ref);artCache.delete(ref);
    try{URL.revokeObjectURL(u)}catch{}
  }
}
function artTag(ref){if(!ref)return '';const u=artReady.get(ref);return u?'<img class="art loaded" data-art="'+esc(ref)+'" src="'+u+'" alt="">':'<img class="art" data-art="'+esc(ref)+'" alt="">'}
const artObserver='IntersectionObserver' in window?new IntersectionObserver(es=>{for(const e of es)if(e.isIntersecting){artObserver.unobserve(e.target);showArt(e.target)}},{rootMargin:'300px'}):null;
function showArt(img){artURL(img.dataset.art).then(u=>{if(!u)return;img.onload=()=>img.classList.add('loaded');img.src=u}).catch(()=>{})}
function hydrateArt(root){root.querySelectorAll('img[data-art]:not([src])').forEach(img=>artObserver?artObserver.observe(img):showArt(img))}
function openViewer(ref){if(!ref)return;artURL(ref).then(u=>{if(!u)throw new Error('none');$('viewerimg').src=u;$('viewer').classList.remove('hidden')}).catch(()=>toast('Couldn\'t load the picture',true))}

// ---------- Movie details sheet ----------
let sheetOpen=false,detailMovie=null;
function closeSheet(fromPop){if(!sheetOpen)return;sheetOpen=false;$('sheet').classList.add('hidden');document.body.style.overflow='';if(!fromPop&&history.state&&history.state.sheet)history.back()}
async function openDetail(id){
  const panel=$('sheetpanel');
  panel.innerHTML='<button class="dclose" data-close aria-label="Close">'+ICON.x+'</button><div class="empty"><b>Loading…</b></div>';
  $('sheet').classList.remove('hidden');document.body.style.overflow='hidden';
  if(!sheetOpen){sheetOpen=true;try{history.pushState({sheet:1},'')}catch{}}
  let d;try{d=await api('/api/kodi/movie?id='+encodeURIComponent(id))}catch(e){panel.innerHTML='<button class="dclose" data-close aria-label="Close">'+ICON.x+'</button>'+empty('Couldn\'t load this movie',e.message);return}
  const meta=[d.year,runtimeText(d.runtime),d.watched?'watched':''].filter(Boolean).join(' · ');
  const chips=[];
  if(d.quality)chips.push('<span class="chip">Your copy: '+esc(d.quality)+(d.hdr?' '+esc(d.hdr.toUpperCase()):'')+'</span>');
  if(d.imdb_rating)chips.push(d.imdb_url?'<a class="chip imdb" href="'+esc(d.imdb_url)+'" target="_blank" rel="noopener">IMDb '+esc(Number(d.imdb_rating).toFixed(1))+'</a>':'<span class="chip imdb">IMDb '+esc(Number(d.imdb_rating).toFixed(1))+'</span>');
  if(d.tmdb)chips.push('<span class="chip" style="background:#0B2A3A;color:#7DD3FC">TMDb '+esc(Number(d.tmdb).toFixed(1))+'</span>');
  else if(!d.imdb_rating&&d.rating)chips.push('<span class="chip">★ '+esc(d.rating)+(d.rating_source?' '+esc(d.rating_source.replace('themoviedb','TMDb')):'')+'</span>');
  if(!d.imdb_rating&&d.imdb_url)chips.push('<a class="chip imdb" href="'+esc(d.imdb_url)+'" target="_blank" rel="noopener">IMDb</a>');
  if(d.mpaa)chips.push('<span class="chip">'+esc(d.mpaa)+'</span>');
  d.genres.forEach(g=>chips.push('<span class="chip">'+esc(g)+'</span>'));
  const facts=[];
  if(d.directors.length)facts.push('<b>Director</b> '+esc(d.directors.join(', ')));
  if(d.cast.length)facts.push('<b>Cast</b> '+esc(d.cast.map(c=>c.name).join(', ')));
  if(d.studio.length)facts.push('<b>Studio</b> '+esc(d.studio.join(', ')));
  if(d.imdb_votes)facts.push('<b>IMDb votes</b> '+esc(Number(d.imdb_votes).toLocaleString()));
  const play=(d.resume?'<button class="btn" data-dplay="'+esc(d.id)+'" data-resume="1">Resume at '+fmtTime(d.resume)+'</button><button class="btn ghost" data-dplay="'+esc(d.id)+'">Start over</button>':'<button class="btn" data-dplay="'+esc(d.id)+'">Play on Kodi</button>');
  panel.innerHTML='<button class="dclose" data-close aria-label="Close">'+ICON.x+'</button>'+
    (d.fanart?'<button class="fan" data-view="'+esc(d.fanart)+'" aria-label="View backdrop full size">'+artTag(d.fanart)+'</button>':'<div class="fan" style="aspect-ratio:3/1"></div>')+
    '<div class="dhead"><button class="dposter" data-view="'+esc(d.poster)+'" aria-label="View poster full size">'+artTag(d.poster)+'</button><div class="dtitle"><h2>'+esc(d.title)+'</h2><div class="m">'+esc(meta)+'</div></div></div>'+
    (chips.length?'<div class="chips">'+chips.join('')+'</div>':'')+
    '<div class="dbody">'+(d.tagline?'<p class="tagline">'+esc(d.tagline)+'</p>':'')+'<p class="plot">'+(esc(d.plot)||'<span style="color:var(--muted)">No synopsis in Kodi for this one.</span>')+'</p>'+
    (facts.length?'<div class="facts">'+facts.join('<br>')+'</div>':'')+'<div class="row">'+play+'</div>'+
    '<button class="btn ghost full" style="margin-top:10px" data-better="1">Find a better copy</button></div>';
  panel.scrollTop=0;hydrateArt(panel);
  detailMovie=d;
}

// ---------- Kodi ----------
let kodiRecent=[],kodiMovies=null,kodiLoading=false;
function runtimeText(sec){if(!sec)return '';const m=Math.round(sec/60);return m>=60?Math.floor(m/60)+'h '+(m%60)+'m':m+'m'}
function movieRow(mv){
  const meta=[mv.year,runtimeText(mv.runtime),mv.mpaa,mv.resume?'resume at '+fmtTime(mv.resume):(mv.watched?'watched':'')].filter(Boolean).join(' · ');
  const rl=(mv.imdb?'<span class="rc imdb">IMDb '+Number(mv.imdb).toFixed(1)+'</span>':'')+(mv.tmdb?'<span class="rc tmdb">TMDb '+Number(mv.tmdb).toFixed(1)+'</span>':'')+
    ((mv.genres||[]).length?'<span>'+esc(mv.genres.slice(0,2).join(', '))+'</span>':'');
  return '<div class="qrow"><button class="pthumb" data-detail="'+esc(mv.id)+'" aria-label="Details for '+esc(mv.title)+'">'+artTag(mv.poster)+'</button><div class="info tap" data-detail="'+esc(mv.id)+'"><div class="t">'+esc(mv.title)+'</div><div class="m">'+esc(meta)+'</div>'+(rl?'<div class="rl">'+rl+'</div>':'')+'</div>'+
    (mv.resume?'<button class="btn ghost sm" style="flex:none" data-play="'+esc(mv.id)+'" data-resume="1">Resume</button>':'')+
    '<button class="icon-btn play" data-play="'+esc(mv.id)+'" aria-label="Play '+esc(mv.title)+(mv.resume?' from the start':'')+'">'+ICON.play+'</button></div>';
}
const KODI_PAGE=100;let kodiShown=KODI_PAGE;
const KODI_VIEWS={recent:'RECENTLY ADDED',all:'ALL MOVIES',newest:'NEWEST ADDED',year:'BY RELEASE YEAR',rated:'HIGHEST RATED',unwatched:'UNWATCHED',progress:'IN PROGRESS'};
async function renderKodiList(resetPage){
  if(resetPage)kodiShown=KODI_PAGE;
  const q=$('kodifilter').value.trim().toLowerCase(),view=$('kodiview').value,title=$('kodilisttitle'),list=$('kodilist'),more=$('kodimore');
  more.classList.add('hidden');
  if(!q&&view==='recent'){title.textContent='RECENTLY ADDED';list.innerHTML=kodiRecent.map(movieRow).join('')||empty('No movies yet','Run a library scan once something finishes downloading.');hydrateArt(list);return}
  if(kodiMovies===null){
    if(kodiLoading)return;kodiLoading=true;title.textContent='LOADING LIBRARY…';list.innerHTML=skeleton(5,2);
    try{kodiMovies=(await api('/api/kodi/movies')).movies||[]}catch(e){title.textContent='';list.innerHTML=empty('Couldn\'t load the library',e.message);return}finally{kodiLoading=false}
    return renderKodiList();
  }
  let a=kodiMovies.slice();
  if(view==='unwatched')a=a.filter(m=>!m.watched);
  if(view==='progress')a=a.filter(m=>m.resume>0);
  if(q)a=a.filter(m=>m.title.toLowerCase().includes(q)||(m.genres||[]).some(g=>g.toLowerCase().includes(q)));
  if(view==='newest'||view==='recent')a.sort((x,y)=>(y.added||'').localeCompare(x.added||''));
  if(view==='rated')a.sort((x,y)=>((y.imdb||y.tmdb||0)-(x.imdb||x.tmdb||0))||x.title.localeCompare(y.title));
  if(view==='year')a.sort((x,y)=>(y.year||0)-(x.year||0)||x.title.localeCompare(y.title));
  const label=q?'MATCH'+(a.length===1?'':'ES'):(view==='all'?'MOVIES':KODI_VIEWS[view]);
  title.textContent=a.length+' '+label+(q&&view!=='all'&&view!=='recent'?' · '+KODI_VIEWS[view]:'');
  list.innerHTML=a.slice(0,kodiShown).map(movieRow).join('')||empty(q?'No matches':'Nothing here',q?'Try part of the title.':'No movies fit this view.');hydrateArt(list);
  if(a.length>kodiShown){more.textContent='Show more ('+(a.length-kodiShown)+' left)';more.classList.remove('hidden')}
}

// ---------- Sony TV: power on + open Kodi ----------
let tvInfo=null,tvPolling=false;
async function loadTv(){try{tvInfo=await api('/api/tv')}catch{tvInfo=null}renderTv()}
function tvSay(t,bad){const m=$('tvmsg');m.textContent=t||'';m.style.color=bad?'var(--red, #ff6b6b)':''}
function renderTv(){
  const box=$('tvbox');if(!tvInfo){box.classList.add('hidden');return}
  box.classList.remove('hidden');
  $('tvbtns').classList.toggle('hidden',!tvInfo.configured);
  const ts=$('tvsetup');if(!tvInfo.configured&&!ts.dataset.seen){ts.open=true;ts.dataset.seen='1';ts.dataset.auto='1'}else if(tvInfo.configured&&ts.dataset.auto){ts.open=false;delete ts.dataset.auto}
  const h=$('tvhost');if(document.activeElement!==h)h.value=tvInfo.host||'';
  const mc=$('tvmac');if(document.activeElement!==mc)mc.value=tvInfo.mac||'';
  h.placeholder=tvInfo.auto_host?'Same as Kodi ('+tvInfo.auto_host+')':'192.168.1.40';
  $('tvpsk').placeholder=tvInfo.psk_set?'•••••• saved (leave blank to keep)':'The key you set on the TV';
  if(tvPolling)return;
  if(!tvInfo.configured){tvSay('Set up TV control below to turn the TV on from here.');return}
  if(tvInfo.problem){tvSay(tvInfo.problem,true);return}
  tvSay({on:'TV is on.',standby:'TV is in standby.',off:'TV is off or unreachable.'}[tvInfo.power]||'')
}
function tvBusy(b){$('tvon').disabled=b;$('tvkodi').disabled=b}
async function tvOn(){
  tvBusy(true);tvPolling=true;tvSay('Turning the TV on…');
  try{await api('/api/tv/on',{kodi:true})}catch(e){tvPolling=false;tvBusy(false);tvSay(e.message,true);return}
  const t0=Date.now();
  while(Date.now()-t0<150000){
    await new Promise(r=>setTimeout(r,2000));
    let j;try{j=(await api('/api/tv/job')).job}catch{continue}
    tvSay(j.message,j.state==='error');
    if(j.state==='done'){toast(j.message);break}
    if(j.state==='error'){toast(j.message,true);break}
  }
  tvPolling=false;tvBusy(false);
  if(activeTab==='kodi')refreshKodi()
}
async function tvKodi(){
  tvBusy(true);tvSay('Opening Kodi…');
  try{await api('/api/tv/kodi',{});toast('Opening Kodi on the TV…');setTimeout(()=>{if(activeTab==='kodi')refreshKodi()},4000)}
  catch(e){tvSay(e.message,true);toast(e.message,true)}
  finally{tvBusy(false)}
}
async function tvSave(){
  const b=$('tvsave');b.disabled=true;
  try{
    tvInfo=await api('/api/tv/settings',{host:$('tvhost').value.trim(),psk:$('tvpsk').value.trim(),mac:$('tvmac').value.trim()});
    $('tvpsk').value='';renderTv();
    if(tvInfo.configured&&!tvInfo.problem){toast('TV connected');$('tvsetup').open=false}
    else if(tvInfo.problem)toast(tvInfo.problem,true);
  }catch(e){toast(e.message,true)}finally{b.disabled=false}
}
async function refreshKodi(){
  const st=$('kodistatus');loadTv();
  try{
    const d=await api('/api/kodi');
    const off=()=>{$('kodinow').innerHTML='';['kodilib','kodiTV','kodiseg','kodiremote','kodytype'].forEach(id=>$(id).classList.add('hidden'));kodiOnline=false};
    if(!d.configured){st.className='lede';st.textContent='Kodi isn\'t set up yet — add it in the desktop app\'s Kodi tab.';off();$('kodiscan').classList.add('hidden');return}
    $('kodiscan').classList.remove('hidden');
    if(!d.online){st.className='lede status error';st.textContent=d.problem||'Kodi is offline.';off();return}
    const c=d.counts||{};st.className='lede';st.textContent=(c.movies||0)+' movies · '+(c.shows||0)+' shows · '+(c.episodes||0)+' episodes';
    kodiPid=d.playing?d.playing.playerid:-1;
    $('kodinow').innerHTML=nowCard(d.playing,false);hydrateArt($('kodinow'));
    kodiOnline=true;$('kodiremote').classList.remove('hidden');$('kodytype').classList.remove('hidden');$('kodiseg').classList.remove('hidden');
    kodiRecent=d.recent||[];kodiMovies=null;kodiShows=null;kodiMissing=null;kodiContinue=d.continue||[];
    setKodiMode(kodiMode);
  }catch(e){st.className='lede status error';st.textContent=e.message}
}
function remoteInfo(d){
  const p=d.playing,v=d.volume,bits=[];
  if(p&&p.subtitle)bits.push('Subtitles: '+p.subtitle);else if(p)bits.push('Subtitles off');
  if(p&&p.audio)bits.push('Audio: '+p.audio);
  if(v)bits.push(v.muted?'Muted':'Volume '+v.level+'%');
  $('kodirinfo').textContent=bits.join(' · ');
}
async function refreshKodiNow(){try{const d=await api('/api/kodi/now');kodiPid=d.playing?d.playing.playerid:-1;$('kodinow').innerHTML=nowCard(d.playing,false);hydrateArt($('kodinow'));remoteInfo(d)}catch{}}
async function kodiControl(action,pid,value){
  if(pid===undefined||pid==='')pid=kodiPid;
  try{await api('/api/kodi/control',{action,playerid:Number(pid),value});setTimeout(()=>{if(activeTab==='kodi')refreshKodiNow();else refreshHome()},400)}catch(e){toast(e.message,true)}
}
async function playMovie(id,resume){
  try{await api('/api/kodi/play',{movieid:Number(id),resume:!!resume});toast(resume?'Resuming on Kodi…':'Starting on Kodi…');setTimeout(refreshKodiNow,2000)}catch(e){toast(e.message,true)}
}
async function playEpisode(id,resume){
  try{await api('/api/kodi/play',{episodeid:Number(id),resume:!!resume});toast(resume?'Resuming on Kodi…':'Starting on Kodi…');setTimeout(()=>{if(activeTab==='kodi')refreshKodi()},2500)}catch(e){toast(e.message,true)}
}

// ---------- Kodi TV shows ----------
const TV_PAGE=100;
let kodiMode=LS.get('nzb_mobile_kodimode','movies')==='tv'?'tv':'movies',kodiOnline=false,kodiShows=null,kodiContinue=[],showsLoading=false,tvShown=TV_PAGE,kodiMissing=null;
function setKodiMode(m){
  kodiMode=m;LS.set('nzb_mobile_kodimode',m);
  document.querySelectorAll('#kodiseg button').forEach(b=>b.classList.toggle('on',b.dataset.kmode===m));
  $('kodilib').classList.toggle('hidden',!kodiOnline||m!=='movies');$('kodiTV').classList.toggle('hidden',!kodiOnline||m!=='tv');
  if(!kodiOnline)return;
  if(m==='movies')renderKodiList(true);else{renderContinue();renderShows(true)}
}
function epCode(e){return 'S'+String(e.season).padStart(2,'0')+'E'+String(e.episode).padStart(2,'0')}
function renderContinue(){
  const box=$('kodicw');$('cwtitle').classList.toggle('hidden',!kodiContinue.length);box.classList.toggle('hidden',!kodiContinue.length);
  box.innerHTML=kodiContinue.map(c=>'<button class="citem" data-ep="'+esc(c.episode.id)+'"'+(c.episode.resume?' data-resume="1"':'')+' aria-label="Play '+esc(c.show.title+' '+epCode(c.episode))+'">'+(c.show.poster?'<div class="ph">'+artTag(c.show.poster)+'</div>':'')+'<div class="t">'+esc(c.show.title)+'</div><div class="m">'+esc(epCode(c.episode)+' · '+c.episode.title)+'</div><div class="go">'+(c.episode.resume?'▶ RESUME '+fmtTime(c.episode.resume):'▶ PLAY NEXT')+'</div></button>').join('');
  hydrateArt(box);
}
function showRow(s){
  const pct=s.episodes?Math.round(s.watched/s.episodes*100):0,left=s.episodes-s.watched;
  const meta=[s.year,s.episodes+' episodes',left>0&&s.watched?left+' unwatched':(left<=0&&s.episodes?'all watched':'')].filter(Boolean).join(' · ');
  return '<div class="qrow"><button class="pthumb" data-show="'+esc(s.id)+'" aria-label="Details for '+esc(s.title)+'">'+artTag(s.poster)+'</button><div class="info tap" data-show="'+esc(s.id)+'"><div class="t">'+esc(s.title)+'</div><div class="m">'+esc(meta)+'</div>'+((s.genres||[]).length?'<div class="rl"><span>'+esc(s.genres.slice(0,2).join(', '))+'</span></div>':'')+'<div class="bar thin"><span style="width:'+pct+'%"></span></div></div></div>';
}
function missingRow(x){
  const eps=x.missing.map(n=>'E'+String(n).padStart(2,'0')),q=x.show+' S'+String(x.season).padStart(2,'0')+(x.missing.length===1?'E'+String(x.missing[0]).padStart(2,'0'):'');
  return '<div class="miss"><span><b>'+esc(x.show)+'</b> · Season '+esc(x.season)+'<br>Missing '+esc(eps.slice(0,12).join(', ')+(eps.length>12?' …':''))+'</span><button class="btn sm" data-tvsearch="'+esc(q)+'">Search</button></div>';
}
async function renderShows(reset){
  if(reset)tvShown=TV_PAGE;
  const view=$('tvview').value,q=$('tvfilter').value.trim().toLowerCase(),list=$('tvlist'),title=$('tvlisttitle'),more=$('tvmore');more.classList.add('hidden');
  if(view==='missing'){
    if(kodiMissing===null){title.textContent='CHECKING FOR GAPS…';list.innerHTML=skeleton(4,2);try{kodiMissing=(await api('/api/kodi/missing')).missing||[]}catch(e){title.textContent='';list.innerHTML=empty('Couldn\'t check',e.message);return}}
    const a=kodiMissing.filter(x=>!q||x.show.toLowerCase().includes(q));
    title.textContent=a.length+' SEASON'+(a.length===1?'':'S')+' WITH GAPS';
    list.innerHTML=a.slice(0,tvShown).map(missingRow).join('')||empty('No gaps found','Every season has all episodes up to its latest one. (Kodi can\'t know about episodes after the newest one you have.)');
    if(a.length>tvShown){more.textContent='Show more ('+(a.length-tvShown)+' left)';more.classList.remove('hidden')}
    return;
  }
  if(kodiShows===null){
    if(showsLoading)return;showsLoading=true;title.textContent='LOADING SHOWS…';list.innerHTML=skeleton(5,2);
    try{kodiShows=(await api('/api/kodi/shows')).shows||[]}catch(e){title.textContent='';list.innerHTML=empty('Couldn\'t load your shows',e.message);return}finally{showsLoading=false}
  }
  let a=kodiShows.filter(s=>!q||s.title.toLowerCase().includes(q));
  if(view==='unwatched')a=a.filter(s=>s.episodes>s.watched);
  if(view==='recent')a=a.filter(s=>s.lastplayed).sort((x,y)=>y.lastplayed.localeCompare(x.lastplayed));
  if(view==='added')a.sort((x,y)=>(y.added||'').localeCompare(x.added||''));
  title.textContent=a.length+' SHOW'+(a.length===1?'':'S');
  list.innerHTML=a.slice(0,tvShown).map(showRow).join('')||empty(q?'No matches':'No shows here',q?'Try part of the title.':'Nothing fits this view.');hydrateArt(list);
  if(a.length>tvShown){more.textContent='Show more ('+(a.length-tvShown)+' left)';more.classList.remove('hidden')}
}
function episodeRow(e){
  const meta=[e.aired,runtimeText(e.runtime),e.resume?'resume at '+fmtTime(e.resume):(e.watched?'watched':'')].filter(Boolean).join(' · ');
  return '<div class="eprow'+(e.watched?' w':'')+'"><span class="n">E'+String(e.episode).padStart(2,'0')+'</span><div class="info"><div class="t">'+esc(e.title)+'</div><div class="m">'+esc(meta)+'</div></div><button class="icon-btn play" data-ep="'+esc(e.id)+'"'+(e.resume?' data-resume="1"':'')+' aria-label="Play '+esc(epCode(e)+' '+e.title)+'">'+ICON.play+'</button></div>';
}
async function openShow(id){
  const panel=$('sheetpanel');
  panel.innerHTML='<button class="dclose" data-close aria-label="Close">'+ICON.x+'</button><div class="empty"><b>Loading…</b></div>';
  $('sheet').classList.remove('hidden');document.body.style.overflow='hidden';
  if(!sheetOpen){sheetOpen=true;try{history.pushState({sheet:1},'')}catch{}}
  let d;try{d=await api('/api/kodi/show?id='+encodeURIComponent(id))}catch(e){panel.innerHTML='<button class="dclose" data-close aria-label="Close">'+ICON.x+'</button>'+empty('Couldn\'t load this show',e.message);return}
  const n=d.next,openSeason=n?n.season:(d.seasons[0]||{}).season;
  const nextBtn=n?'<button class="btn full" data-ep="'+esc(n.id)+'"'+(n.resume?' data-resume="1"':'')+'>'+(n.resume?'Resume ':'Play next: ')+esc(epCode(n))+' · '+esc(n.title)+'</button>':'<p class="hint">You\'ve watched everything Kodi has for this show.</p>';
  const seasons=d.seasons.map(s=>{
    const w=s.episodes.filter(e=>e.watched).length,name=s.season===0?'Specials':'Season '+s.season;
    const miss=s.missing.length?'<div class="miss"><span>Missing '+esc(s.missing.map(x=>'E'+String(x).padStart(2,'0')).join(', '))+'</span><button class="btn sm" data-tvsearch="'+esc(d.title+' S'+String(s.season).padStart(2,'0')+(s.missing.length===1?'E'+String(s.missing[0]).padStart(2,'0'):''))+'">Search</button></div>':'';
    return '<details class="season"'+(s.season===openSeason?' open':'')+'><summary>'+esc(name)+'<span class="m">'+w+'/'+s.episodes.length+' watched'+(s.missing.length?' · '+s.missing.length+' missing':'')+'</span></summary>'+miss+s.episodes.map(episodeRow).join('')+'</details>';
  }).join('');
  panel.innerHTML='<button class="dclose" data-close aria-label="Close">'+ICON.x+'</button>'+
    (d.fanart?'<button class="fan" data-view="'+esc(d.fanart)+'" aria-label="View backdrop full size">'+artTag(d.fanart)+'</button>':'<div class="fan" style="aspect-ratio:3/1"></div>')+
    '<div class="dhead"><button class="dposter" data-view="'+esc(d.poster)+'" aria-label="View poster full size">'+artTag(d.poster)+'</button><div class="dtitle"><h2>'+esc(d.title)+'</h2><div class="m">'+esc([d.year,d.episodes+' episodes',d.watched+' watched'].filter(Boolean).join(' · '))+'</div></div></div>'+
    ((d.genres||[]).length?'<div class="chips">'+d.genres.map(g=>'<span class="chip">'+esc(g)+'</span>').join('')+'</div>':'')+
    '<div class="dbody">'+(d.plot?'<p class="plot">'+esc(d.plot)+'</p>':'')+nextBtn+'<div style="margin-top:12px">'+seasons+'</div></div>';
  panel.scrollTop=0;hydrateArt(panel);
}

// ---------- Queue item options ----------
const PRIOS=[['-1','Low'],['0','Normal'],['1','High'],['2','Force']];
function openQueueItem(id){
  const x=lastQueue.find(s=>s.id===id);if(!x)return;
  const panel=$('sheetpanel');panel.dataset.nzo=id;
  const cats=['*'].concat(sabCats.filter(c=>c!=='*'));
  const cur=(x.cat&&x.cat!=='*')?x.cat:'*';
  panel.innerHTML='<button class="dclose" data-close aria-label="Close">'+ICON.x+'</button><div class="dbody" style="padding-top:20px"><h2 style="margin:0 44px 6px 0;font-size:18px;overflow-wrap:anywhere">'+esc(x.name)+'</h2><div class="hint">'+esc([x.status,x.size,x.priority?'priority '+x.priority:''].filter(Boolean).join(' · '))+'</div>'+
    '<div class="section">PRIORITY</div><div class="pills">'+PRIOS.map(([v,n])=>'<button data-qprio="'+v+'"'+(String(x.priority).toLowerCase()===n.toLowerCase()?' class="on"':'')+'>'+n+'</button>').join('')+'</div>'+
    '<div class="section">CATEGORY</div><div class="row"><select id="qcat" class="field" style="margin:0">'+cats.map(c=>'<option value="'+esc(c)+'"'+(c===cur?' selected':'')+'>'+esc(c==='*'?'Default':c)+'</option>').join('')+'</select><button class="btn ghost sm" style="flex:none" data-qcat="1">Change</button></div>'+
    '<div class="section">ORDER</div><button class="btn ghost full" data-qtop="1">Download this next</button></div>';
  $('sheet').classList.remove('hidden');document.body.style.overflow='hidden';
  if(!sheetOpen){sheetOpen=true;try{history.pushState({sheet:1},'')}catch{}}
}
async function queueItemChange(action,value){
  const id=$('sheetpanel').dataset.nzo;
  try{await api('/api/queue/item',{id,action,value});toast(action==='top'?'Moved to the top of the queue':(action==='priority'?'Priority changed':'Category changed'));closeSheet();await refreshQueue(true)}catch(e){toast(e.message,true)}
}

// ---------- Home: data downloaded ----------
function fmtBytes(n){n=Number(n)||0;const u=['B','KB','MB','GB','TB'];let i=0;while(n>=1024&&i<u.length-1){n/=1024;i++}return (i?n.toFixed(n<10?1:0):n)+' '+u[i]}
let usageAt=0;
async function loadHomeUsage(){
  if(Date.now()-usageAt<60000)return;usageAt=Date.now();
  try{const t=(await api('/api/stats')).totals||{};$('husday').textContent=fmtBytes(t.day);$('husweek').textContent=fmtBytes(t.week);$('husmonth').textContent=fmtBytes(t.month)}catch{}
}
// ---------- Drive Health ----------
let drivesAt=0,drivesData=null,drivesBusy=false;
const DCOL=['#A78BFA','#38BDF8','#F59E0B','#F472B6','#34D399','#FB7185'];
function dLetters(d){return d.letters.filter(x=>x!=='/').map(x=>x+':').join(' ')}
function dTag(d){if(d.key&&d.key.startsWith('vol:')&&d.status==='ok')return '<span class="tag ok" style="flex:none">'+(d.media==='Network'?'CONNECTED':'OK')+'</span>';const t={ok:['ok','HEALTHY'],warn:['warn',d.temp&&d.temp>=d.temp_limit-4?'RUNNING WARM':'NEEDS A LOOK'],bad:['bad',d.health==='Unhealthy'||d.health==='Warning'?'PROBLEM':'TOO HOT'],unknown:['unk','UNKNOWN']}[d.status]||['unk','UNKNOWN'];return '<span class="tag '+t[0]+'" style="flex:none">'+t[1]+'</span>'}
function dKind(d){return [d.media,d.bus].filter(Boolean).join(' · ')+(d.size?(d.media||d.bus?' · ':'')+fmtBytes(d.size):'')}
function dBarColor(d){return d.used_pct>=90?'var(--red)':(d.used_pct>=75?'#F59E0B':'var(--teal)')}
function dYears(h){if(!h)return '—';const y=h/8766;return y>=1?y.toFixed(1)+' yr':(h>=48?Math.round(h/24)+' d':h+' h')}
function dTitle(d){return esc(d.label||d.name)}
async function loadHomeDrives(force){
  if(!force&&Date.now()-drivesAt<120000){if(drivesData)renderHomeDrives(drivesData);return}
  drivesAt=Date.now();
  try{drivesData=await api('/api/drives');renderHomeDrives(drivesData)}
  catch(e){$('homedrives').innerHTML='<div class="section" style="margin:18px 4px 8px">DRIVES</div><button class="card kidle" data-tab="drives"><span>Couldn\'t check your drives: '+esc(e.message)+' · tap to try again</span></button>'}
}
function renderHomeDrives(r){
  const box=$('homedrives');
  if(!r||!r.drives||!r.drives.length){box.innerHTML='<div class="section" style="margin:18px 4px 8px">DRIVES</div><button class="card kidle" data-tab="drives"><span>'+esc((r&&r.problem)||'No drives reported yet')+' · tap for Drive Health</span></button>';return}
  const n=r.totals.warn;
  box.innerHTML='<div class="row" style="justify-content:space-between;margin:18px 4px 8px"><div class="section" style="margin:0;flex:none">DRIVES</div>'+(n?'<span class="tag warn" style="flex:none">'+n+' NEED'+(n===1?'S':'')+' A LOOK</span>':'<span class="tag ok" style="flex:none">ALL HEALTHY</span>')+'</div><div class="card">'+
    r.drives.map(d=>'<button class="drow" data-tab="drives"><div class="dl"><span class="lt">'+esc(dLetters(d)||'—')+'</span><span class="nm">'+dTitle(d)+' <small>· '+esc(dKind(d))+'</small></span>'+dTag(d)+'</div>'+
      (d.total?'<div class="dbar"><span style="width:'+clampPct(d.used_pct)+'%;background:'+dBarColor(d)+'"></span></div>':'')+
      '<div class="dsub"><span><b>'+esc(fmtBytes(d.free))+' free</b>'+(d.days_left!=null?' · ~'+esc(d.days_left)+' days left':(d.wear!=null?' · SSD wear '+esc(d.wear)+'%':''))+'</span><span style="font-family:var(--mono)'+(d.temp&&d.temp>=d.temp_limit-4?';color:var(--amber)':'')+'">'+(d.temp?esc(d.temp)+'°C':'')+'</span></div></button>').join('')+'</div>';
}
function tempChart(drives){
  const series=drives.map((d,i)=>({d,c:DCOL[i%DCOL.length],pts:(d.history||[])})).filter(s=>s.pts.length>1);
  if(!series.length)return '';
  const now=Date.now()/1000,start=now-86400;let lo=Infinity,hi=-Infinity;
  series.forEach(s=>s.pts.forEach(p=>{lo=Math.min(lo,p[1]);hi=Math.max(hi,p[1])}));
  const limit=Math.min(...series.map(s=>s.d.temp_limit));hi=Math.max(hi,limit)+2;lo=Math.max(0,lo-5);
  const W=330,H=110,x=t=>((t-start)/86400*W).toFixed(1),y=v=>(H-(v-lo)/(hi-lo)*H).toFixed(1);
  const lines=series.map(s=>'<polyline fill="none" stroke="'+s.c+'" stroke-width="2" stroke-linejoin="round" points="'+s.pts.map(p=>x(p[0])+','+y(p[1])).join(' ')+'"/>').join('');
  return '<div class="section">TEMPERATURES · 24 H</div><div class="card"><svg width="100%" height="110" viewBox="0 0 '+W+' '+H+'" preserveAspectRatio="none" role="img" aria-label="Drive temperatures over the last 24 hours"><line x1="0" x2="'+W+'" y1="'+y(limit)+'" y2="'+y(limit)+'" stroke="#6B5414" stroke-dasharray="4 4"/>'+lines+'</svg>'+
    '<div class="legend">'+series.map(s=>'<span><i style="background:'+s.c+'"></i>'+esc((dLetters(s.d)?dLetters(s.d)+' ':'')+(s.d.label||s.d.name))+'</span>').join('')+'<span style="margin-left:auto;color:#D6C7A0">- - '+esc(Math.round(limit))+'°C alert</span></div></div>';
}
function driveCard(d){
  const other=d.key&&d.key.startsWith('vol:');  // not a physical disk Windows lists: no temperature/health
  const stats=other?[[fmtBytes(d.free),'Free'],[fmtBytes(d.total),'Capacity'],[d.used_pct!=null?d.used_pct+'%':'—','Used'],[d.media||'—','Type']]:[[d.temp?d.temp+'°C':'—','Temp'],[dYears(d.hours),'Powered on']];
  if(!other){
    if(d.wear!=null)stats.push([d.wear+'%','SSD wear']);else if(d.rpm)stats.push([d.rpm,'RPM']);else stats.push([d.used_pct!=null?d.used_pct+'%':'—','Used']);
    stats.push(d.days_left!=null?['~'+d.days_left,'Days left']:[fmtBytes(d.total),'Capacity']);
  }
  const notes=d.problems.map(p=>'<div class="dnote '+(d.status==='bad'?'bad':'warn')+'">'+esc(p)+'</div>').join('');
  const info=d.days_left!=null?'<div class="dnote warn">About <b>'+esc(d.days_left)+' days</b> of space left at your pace ('+esc(fmtBytes(d.daily_bytes))+'/day).</div>':'';
  return '<button class="dcard tap" data-ddrive="'+esc(d.key)+'"><div class="top"><span class="lt">'+esc(dLetters(d)||'—')+'</span><div class="nm"><b>'+dTitle(d)+'</b><span>'+esc(d.name)+(dKind(d)?' · '+esc(dKind(d)):'')+'</span></div>'+dTag(d)+'</div>'+
    (d.total?'<div class="row" style="justify-content:space-between;margin-top:12px;font-size:13px"><b style="flex:none">'+esc(fmtBytes(d.free))+' free of '+esc(fmtBytes(d.total))+'</b><span style="flex:none;font:500 12px var(--mono);color:var(--soft)">'+esc(d.used_pct)+'% used</span></div><div class="bar" style="height:8px;margin:6px 0 0"><span style="width:'+clampPct(d.used_pct)+'%;background:'+dBarColor(d)+'"></span></div>':'')+
    '<div class="dstats">'+stats.map(s=>'<div><b>'+esc(s[0])+'</b><span>'+esc(s[1])+'</span></div>').join('')+'</div>'+
    notes+(notes?'':info)+(d.uses.length?'<div class="dnote">'+esc(d.uses.join(' · '))+(d.temp?' · alert at '+esc(Math.round(d.temp_limit))+'°C':'')+'</div>':'')+
    '<div class="dio" data-io="'+esc(d.details&&d.details.id)+'">'+ioText(driveIO[d.details&&d.details.id])+'</div><div class="more">Details ›</div></button>';
}
let driveIO={},driveOpen=null;
function ioText(x){if(!x)return '<span>Activity: —</span>';return '<span>Read <b>'+esc(fmtBytes(x.read))+'/s</b></span><span>Write <b>'+esc(fmtBytes(x.write))+'/s</b></span><span class="busy">Busy <b>'+esc(Math.round(x.busy))+'%</b></span>'}
async function refreshDriveIO(){
  try{driveIO=(await api('/api/drives/io')).io||{}}catch{return}
  document.querySelectorAll('[data-io]').forEach(el=>{el.innerHTML=ioText(driveIO[el.dataset.io])});
}
function dNum(v,unit){return v==null?'—':Number(v).toLocaleString()+(unit||'')}
function oneTempChart(d){
  const pts=d.history||[];if(pts.length<2)return '<p class="hint" style="margin:0">The graph fills in over the first day (one reading every 10 minutes).</p>';
  const vals=pts.map(p=>p[1]),lo0=Math.min(...vals),hi0=Math.max(...vals),avg=vals.reduce((a,b)=>a+b,0)/vals.length;
  const now=Date.now()/1000,start=now-86400,hi=Math.max(hi0,d.temp_limit)+2,lo=Math.max(0,lo0-5),W=330,H=110;
  const x=t=>((t-start)/86400*W).toFixed(1),y=v=>(H-(v-lo)/(hi-lo)*H).toFixed(1);
  return '<svg width="100%" height="110" viewBox="0 0 '+W+' '+H+'" preserveAspectRatio="none" role="img" aria-label="Temperature over the last 24 hours"><line x1="0" x2="'+W+'" y1="'+y(d.temp_limit)+'" y2="'+y(d.temp_limit)+'" stroke="#6B5414" stroke-dasharray="4 4"/><polyline fill="none" stroke="var(--teal)" stroke-width="2" stroke-linejoin="round" points="'+pts.map(p=>x(p[0])+','+y(p[1])).join(' ')+'"/></svg>'+
    '<div class="legend"><span>Low <b style="color:var(--white)">'+Math.round(lo0)+'°C</b></span><span>Average <b style="color:var(--white)">'+Math.round(avg)+'°C</b></span><span>High <b style="color:var(--white)">'+Math.round(hi0)+'°C</b></span><span style="margin-left:auto;color:#D6C7A0">- - '+Math.round(d.temp_limit)+'°C alert</span></div>';
}
function kv(rows){return '<div class="kv">'+rows.filter(r=>r).map(r=>'<div>'+esc(r[0])+'</div><div'+(r[2]?' class="'+r[2]+'"':'')+'>'+esc(r[1])+'</div>').join('')+'</div>'}
function openDrive(key){
  const d=(drivesData&&drivesData.drives||[]).find(x=>x.key===key);if(!d)return;driveOpen=key;
  const x=d.details||{},io=driveIO[x.id];
  const other=d.key&&d.key.startsWith('vol:');
  const health=other?kv([['Type',d.media||'—'],['Health (Windows)',d.health]]):kv([
    ['Health (Windows)',d.health,d.health==='Healthy'?'good':(d.health==='Unknown'?'':'bad')],
    x.status?['Status',x.status]:null,
    ['Temperature now',d.temp?d.temp+'°C':'—',d.temp&&d.temp>=d.temp_limit?'bad':(d.temp&&d.temp>=d.temp_limit-4?'warn':'')],
    ['Highest recorded',x.temp_max?x.temp_max+'°C':'—'],
    ['Alert at',Math.round(d.temp_limit)+'°C'],
    ['Powered on',d.hours?dNum(d.hours,' h')+' ('+dYears(d.hours)+')':'—'],
    d.media==='SSD'?['SSD wear',d.wear!=null?d.wear+'% used':'—',d.wear>=80?'bad':(d.wear>=50?'warn':'')]:null,
    ['Uncorrected read errors',dNum(x.read_errors),x.read_errors?'bad':''],
    ['Uncorrected write errors',dNum(x.write_errors),x.write_errors?'bad':''],
    ['Corrected read / write',dNum(x.read_fixed)+' / '+dNum(x.write_fixed)],
    ['Start / stop cycles',dNum(x.starts)],
    d.media==='HDD'?['Head park cycles',dNum(x.loads)]:null,
    ['Slowest read / write',(x.read_lat!=null?dNum(x.read_lat,' ms'):'—')+' / '+(x.write_lat!=null?dNum(x.write_lat,' ms'):'—')]]);
  const ident=kv([['Model',x.model||d.name],x.serial?['Serial (end)',x.serial]:null,x.firmware?['Firmware',x.firmware]:null,['Type',[d.media,d.rpm?d.rpm+' rpm':''].filter(Boolean).join(' · ')||'—'],['Connection',d.bus||'—'],['Capacity',fmtBytes(d.size||d.total)],
    x.remote?['Shared from',x.remote]:null,x.style?['Partition style',x.style]:null,x.partitions!=null?['Partitions',String(x.partitions)]:null,(x.sector||x.lsector)?['Sector size',(x.lsector||'?')+' / '+(x.sector||'?')+' bytes']:null,
    ['Boot / system disk',(x.boot?'boot':'')+(x.boot&&x.system?' + ':'')+(x.system?'system':'')||'no'],['Disk number',x.id||'—']]);
  const vols=(d.volumes||[]).map(v=>'<div class="vrow"><div class="h"><b>'+esc((v.letter!=='/'?v.letter+': ':'')+(v.label||'Volume'))+'</b><span>'+esc([v.fs,v.health&&v.health!=='Healthy'?v.health:''].filter(Boolean).join(' · '))+'</span></div><div class="bar" style="height:6px;margin:7px 0 5px"><span style="width:'+clampPct(v.used_pct)+'%;background:'+dBarColor(v)+'"></span></div><div class="h"><span>'+esc(fmtBytes(v.free))+' free of '+esc(fmtBytes(v.total))+'</span><span>'+esc(v.used_pct)+'% used</span></div></div>').join('')||'<p class="hint" style="margin:0">No drive letters on this disk.</p>';
  const panel=$('sheetpanel');
  panel.innerHTML='<button class="dclose" data-close aria-label="Close">'+ICON.x+'</button><div class="dbody" style="padding-top:20px">'+
    '<div class="row" style="gap:12px;margin-right:48px"><span style="flex:none;font:800 22px var(--mono)">'+esc(dLetters(d)||'—')+'</span><div style="min-width:0"><h2 style="margin:0;font-size:19px">'+dTitle(d)+'</h2><div class="hint">'+esc(d.name)+'</div></div></div>'+
    '<div style="margin-top:10px">'+dTag(d)+'</div>'+d.problems.map(p=>'<div class="dnote '+(d.status==='bad'?'bad':'warn')+'">'+esc(p)+'</div>').join('')+
    (d.days_left!=null?'<div class="dnote warn">About <b>'+esc(d.days_left)+' days</b> of space left at your pace ('+esc(fmtBytes(d.daily_bytes))+'/day).</div>':'')+
    '<div class="section">SPACE</div><div class="card">'+vols+'</div>'+
    '<div class="section">ACTIVITY NOW</div><div class="card"><div class="dio" style="margin:0" data-io="'+esc(x.id)+'">'+ioText(io)+'</div></div>'+
    '<div class="section">HEALTH</div><div class="card">'+health+(x.smart?'':'<p class="hint" style="margin:8px 0 0">'+(x.kind==='network'?'A network drive: its health and temperature are on the computer or NAS that shares it.':(x.kind?'Windows doesn\'t report health counters for this kind of drive (for example Storage Spaces, a USB enclosure or a virtual disk).':'Windows didn\'t share this drive\'s health counters. Running NZB Mobile Server as administrator usually fixes that.'))+'</p>')+'</div>'+
    '<div class="section">TEMPERATURE · 24 H</div><div class="card">'+oneTempChart(d)+'</div>'+
    '<div class="section">THIS DRIVE</div><div class="card">'+ident+(d.uses.length?'<p class="hint" style="margin:8px 0 0">Used for: '+esc(d.uses.join(', '))+'</p>':'')+'</div></div>';
  $('sheet').classList.remove('hidden');document.body.style.overflow='hidden';
  if(!sheetOpen){sheetOpen=true;try{history.pushState({sheet:1},'')}catch{}}
  panel.scrollTop=0;
}
function renderDrives(r){
  drivesData=r;const t=r.totals,ds=r.drives;
  const ago=r.checked?Math.max(0,Math.round((Date.now()/1000-r.checked)/60)):null;
  $('drivestatus').textContent=ds.length+' drive'+(ds.length===1?'':'s')+(ago!=null?' · checked '+(ago<1?'just now':ago+' min ago'):'');
  if(!ds.length){$('drivebody').innerHTML=empty('No drives found',r.problem||'Windows did not report any drives.');return}
  const worst=ds.find(d=>d.status==='bad')||ds.find(d=>d.status==='warn');
  const banner=worst?'<div class="banner '+worst.status+'"><svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" style="color:'+(worst.status==='bad'?'var(--red)':'var(--amber)')+';flex:none" aria-hidden="true"><path d="M12 3l9 16H3z"/><path d="M12 10v4M12 17h.01"/></svg><div><b>'+t.warn+' drive'+(t.warn===1?' needs':'s need')+' a look</b><span>'+esc((dLetters(worst)?dLetters(worst)+' ':'')+(worst.label||worst.name)+': '+(worst.problems[0]||''))+'</span></div></div>'
    :'<div class="banner ok"><svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="var(--green)" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" style="flex:none" aria-hidden="true"><path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"/><path d="M8.5 12l2.5 2.5 4.5-5"/></svg><div><b>All drives healthy</b><span>Windows reports no problems.</span></div></div>';
  const tiles='<div class="tiles"><div class="tile"><div class="k">Healthy</div><div class="v" style="color:var(--green)">'+t.ok+'</div></div><div class="tile"><div class="k">Need a look</div><div class="v" style="color:'+(t.warn?'var(--amber)':'var(--soft)')+'">'+t.warn+'</div></div><div class="tile"><div class="k">Free of '+esc(fmtBytes(t.total))+'</div><div class="v">'+esc(fmtBytes(t.free))+'</div></div></div>';
  const tempHint=r.temps_available?'':'<p class="hint" style="margin:0 4px 12px">Temperatures and powered-on hours need NZB Mobile Server to run as administrator, or LibreHardwareMonitor running with its web server on (port 8085).</p>';
  const a=r.alerts||{};
  const alerts='<div class="section">ALERTS TO MY PHONE</div><div class="card" id="drivealerts">'+
    '<label class="tgl"><span>Health changes<small>Windows reports a drive problem</small></span><input type="checkbox" data-dalert="notify_drive_health"'+(a.health?' checked':'')+'></label>'+
    '<label class="tgl"><span>Too hot<small>Hard drives '+esc(r.temp_alert)+'°C · NVMe '+esc(r.nvme_temp_alert)+'°C</small></span><input type="checkbox" data-dalert="notify_drive_hot"'+(a.hot?' checked':'')+'></label>'+
    '<label class="tgl"><span>Less than '+esc(a.low_gb||75)+' GB free</span><input type="checkbox" data-dalert="notify_drive_low"'+(a.low?' checked':'')+'></label>'+
    (a.ready?'':'<p class="hint" style="margin:8px 0 0">Phone alerts aren\'t set up yet: they use ntfy, the same as download alerts.</p>')+'</div>';
  $('drivebody').innerHTML=banner+tiles+tempHint+'<div class="section">ALL DRIVES</div>'+ds.map(driveCard).join('')+tempChart(ds)+alerts+(r.problem?'<p class="hint">'+esc(r.problem)+'</p>':'');
  renderHomeDrives(r);
}
async function refreshDrives(force){
  if(drivesBusy)return;drivesBusy=true;
  if(!drivesData){$('drivebody').innerHTML=skeleton(3,3)}else renderDrives(drivesData);
  const b=$('drivecheck');if(force){b.disabled=true;b.textContent='Checking…'}
  try{renderDrives(await api(force?'/api/drives/check':'/api/drives',force?{}:undefined));drivesAt=Date.now();if(force)toast('Drives checked')}
  catch(e){if(!drivesData)$('drivebody').innerHTML=empty('Couldn\'t read your drives',e.message);else toast(e.message,true)}
  finally{drivesBusy=false;b.disabled=false;b.textContent='Check now'}
}
async function setDriveAlert(key,on){try{const body={};body[key]=on;renderDrives(await api('/api/drives/alerts',body));toast(on?'Alert turned on':'Alert turned off')}catch(e){toast(e.message,true)}}

// ---------- Jellyfin ----------
let jf=null,jfMode='',jfBusy=false,jfMissing=null,jfUp=null,jfResults=null,jfQuery='',jfConnectOpen=false;
const JF_ICON='<svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round" aria-hidden="true"><path d="M12 3l9.5 17h-19z"/><path d="M12 10l4 7H8z"/></svg>';
function jfNowCard(x){
  const pp=x.paused?ICON.play:ICON.pause;
  return '<div class="hero jnow"><div class="nowrow"><div class="pthumb">'+artTag(x.poster)+'</div><div class="info"><div class="row" style="justify-content:space-between"><div class="kode" style="flex:1">'+(x.paused?'PAUSED':'NOW PLAYING')+'</div>'+(x.method==='Transcoding'?'<span class="tag tx" style="flex:none">TRANSCODING</span>':'')+'</div><div class="name">'+esc(x.title)+'</div>'+(x.sub?'<div class="sub">'+esc(x.sub)+'</div>':'')+
    '<div class="meta">'+esc([x.user,x.device,x.method==='Transcoding'?'':x.method,x.quality].filter(Boolean).join(' · '))+'</div></div></div>'+
    '<div class="bar"><span style="width:'+clampPct(x.pct)+'%"></span></div><div class="ctrls"><span class="tm">'+fmtTime(x.time)+' / '+fmtTime(x.total)+'</span>'+
    '<button class="round" data-jact="stop" data-sid="'+esc(x.session)+'" aria-label="Stop"><svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><rect x="6" y="6" width="12" height="12" rx="2"/></svg></button>'+
    '<button class="round main" data-jact="playpause" data-sid="'+esc(x.session)+'" aria-label="'+(x.paused?'Play':'Pause')+'">'+pp+'</button></div></div>';
}
function renderJfNow(list){const box=$('jfnow');box.innerHTML=(list||[]).map(jfNowCard).join('');hydrateArt(box)}
async function refreshJfNow(){if(!jf||!jf.online)return;try{renderJfNow((await api('/api/jellyfin/now')).sessions)}catch{}}
function jfConnectForm(prefill){
  return '<div class="card"><div class="kode" style="margin-bottom:10px">CONNECT JELLYFIN</div>'+
    '<div id="jffound"><button class="btn ghost sm full" id="jffind">Find Jellyfin on my network</button></div>'+
    '<label class="label" for="jfurl" style="margin-top:12px">Server address</label><input id="jfurl" class="field" placeholder="http://192.168.68.60:8096" autocomplete="off" autocapitalize="off" autocorrect="off" inputmode="url" value="'+esc(prefill||'')+'">'+
    '<label class="label" for="jfkey">API key</label><input id="jfkey" class="field" type="password" placeholder="From Jellyfin: Dashboard → API Keys → +" autocomplete="off" autocapitalize="off" autocorrect="off">'+
    '<button class="btn full" id="jfsave">Test &amp; save</button><p class="hint" style="margin:10px 2px 0">In Jellyfin open Dashboard → API Keys, tap +, name it “NZB Command Center” and copy the key. You can also set it up in the desktop app (Media Server tab).</p></div>';
}
function jfQuick(){
  const c=jf.counts||{};
  const q=(mode,title,sub,ct,cls)=>'<button class="jq'+(jfMode===mode?' on':'')+'" data-jmode="'+mode+'">'+(ct!=null?'<span class="ct'+(cls?' '+cls:'')+'">'+esc(ct)+'</span>':'')+'<b>'+title+'</b><span>'+sub+'</span></button>';
  return '<div class="jgrid">'+q('missing','Missing episodes','Gaps in your shows',jfMissing?jfMissing.reduce((n,x)=>n+x.missing.length,0):null,'')+q('upgrades','Low quality','Movies below 1080p',jfUp?jfUp.length:null,'warn')+'</div>';
}
function jfItemRow(x){
  const tap=x.type==='Series'?' data-jshow="'+esc(x.id)+'"':' data-jmovie="'+esc(x.id)+'"';
  const meta=x.type==='Series'?[x.year,x.episodes?x.episodes+' episodes':'','TV show'].filter(Boolean).join(' · '):[x.year,x.size?fmtBytes(x.size):'',x.watched?'watched':(x.resume?'resume at '+fmtTime(x.resume):'')].filter(Boolean).join(' · ');
  return '<div class="qrow"><button class="pthumb"'+tap+' aria-label="Details for '+esc(x.title)+'">'+artTag(x.poster)+'</button><div class="info tap"'+tap+'><div class="t">'+esc(x.title)+'</div><div class="m">'+esc(meta)+'</div>'+(x.type==='Movie'?'<div class="rl"><span class="badge lib">✓ In Jellyfin</span>'+(x.quality?'<span class="qual'+(x.quality==='SD'||x.quality==='720p'?' lo':'')+'">'+esc(x.quality)+'</span>':'')+'</div>':'')+'</div>'+
    (x.type==='Movie'?'<button class="btn ghost sm" style="flex:none" data-jbetter="'+esc(x.title+(x.year?' '+x.year:''))+'">'+(x.quality==='2160p'?'Search':'Better copy')+'</button>':'')+'</div>';
}
function jfMissingRow(x){
  const eps=x.missing.map(n=>'E'+String(n).padStart(2,'0')),q=x.show+' S'+String(x.season).padStart(2,'0')+(x.missing.length===1?'E'+String(x.missing[0]).padStart(2,'0'):'');
  return '<div class="miss"><span data-jshow="'+esc(x.show_id)+'" style="cursor:pointer"><b>'+esc(x.show)+'</b> · Season '+esc(x.season)+'<br>Missing '+esc(eps.slice(0,12).join(', ')+(eps.length>12?' …':''))+'</span><button class="btn sm" data-tvsearch="'+esc(q)+'">Search</button></div>';
}
async function jfList(){
  const box=$('jflist');if(!box)return;
  if(jfMode==='search'){
    if(jfResults===null){box.innerHTML=skeleton(3,2);try{jfResults=(await api('/api/jellyfin/search?q='+encodeURIComponent(jfQuery))).items||[]}catch(e){box.innerHTML=empty('Search failed',e.message);return}}
    box.innerHTML='<div class="section">'+jfResults.length+' IN YOUR LIBRARY</div>'+(jfResults.map(jfItemRow).join('')||empty('Not in Jellyfin','You don\'t have “'+jfQuery+'” yet.'))+
      '<button class="btn ghost full" data-jbetter="'+esc(jfQuery)+'" data-jcat="" style="margin-top:4px">Search your indexers for “'+esc(jfQuery)+'”</button>';
    hydrateArt(box);return;
  }
  if(jfMode==='missing'){
    if(jfMissing===null){box.innerHTML='<div class="section">CHECKING YOUR SHOWS…</div>'+skeleton(4,2);try{jfMissing=(await api('/api/jellyfin/missing')).missing||[]}catch(e){box.innerHTML=empty('Couldn\'t check',e.message);return}renderJellyfin();return}
    box.innerHTML='<div class="section">'+jfMissing.length+' SEASON'+(jfMissing.length===1?'':'S')+' WITH GAPS</div>'+(jfMissing.slice(0,80).map(jfMissingRow).join('')||empty('No gaps found','Every season has all its episodes up to the newest one you have.'));return;
  }
  if(jfMode==='upgrades'){
    if(jfUp===null){box.innerHTML='<div class="section">CHECKING QUALITY…</div>'+skeleton(4,2);try{jfUp=(await api('/api/jellyfin/upgrades')).movies||[]}catch(e){box.innerHTML=empty('Couldn\'t check',e.message);return}renderJellyfin();return}
    box.innerHTML='<div class="section">'+jfUp.length+' MOVIE'+(jfUp.length===1?'':'S')+' BELOW 1080P</div>'+(jfUp.slice(0,100).map(jfItemRow).join('')||empty('All good','Every movie is 1080p or better.'));hydrateArt(box);return;
  }
  const r=jf.recent||[];
  box.innerHTML=r.length?'<div class="section">RECENTLY ADDED</div><div class="cw">'+r.map(x=>{const tap=x.type==='Episode'?' data-jshow="'+esc(x.show_id)+'"':' data-jmovie="'+esc(x.id)+'"';return '<button class="citem"'+tap+'><div class="ph">'+artTag(x.poster)+'</div><div class="t">'+esc(x.type==='Episode'?x.show:x.title)+'</div><div class="m">'+esc(x.type==='Episode'?'S'+String(x.season).padStart(2,'0')+'E'+String(x.episode).padStart(2,'0'):(x.year||''))+'</div></button>'}).join('')+'</div>':'';
  hydrateArt(box);
}
function renderJellyfin(){
  const st=$('jfstatus'),body=$('jfbody');$('jfscan').classList.toggle('hidden',!(jf&&jf.online));
  if(!jf||!jf.configured){st.className='lede';st.textContent='Not connected yet.';$('jfnow').innerHTML='';body.innerHTML=jfConnectForm('');return}
  if(!jf.online){st.className='lede status error';st.textContent=jf.problem||'Jellyfin is offline';$('jfnow').innerHTML='';
    const badKey=/HTTP 40[13]/.test(jf.problem||'');
    body.innerHTML='<div class="card kidle">'+JF_ICON+'<span>'+(badKey?'Jellyfin at '+esc(jf.url)+' refused the API key'+(jf.source==='desktop'?' saved in the desktop app':'')+'. Enter a current key below.':'Can\'t reach Jellyfin at '+esc(jf.url)+'. Is the server on?')+'</span></div>'+((jfConnectOpen||badKey)?jfConnectForm(jf.url):'<button class="btn ghost full" id="jfchange">Change connection</button>');return}
  st.className='lede';const c=jf.counts;
  st.textContent=[jf.server+(jf.version?' · v'+jf.version:''),jf.ms+' ms',c?c.movies+' movie'+(c.movies===1?'':'s')+' · '+c.shows+' show'+(c.shows===1?'':'s'):''].filter(Boolean).join(' · ');
  renderJfNow(jf.sessions);
  const nothing=(jf.sessions||[]).length?'':'<div class="card kidle">'+ICON.play+'<span>Nothing playing on Jellyfin right now.</span></div>';
  body.innerHTML=nothing+'<label class="search"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#7F9DA6" stroke-width="2.2" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/></svg><input id="jfq" placeholder="Do I already have it?" enterkeyhint="search" autocomplete="off" aria-label="Search your Jellyfin library" value="'+esc(jfQuery)+'"></label>'+
    jfQuick()+'<div id="jflist"></div>'+
    '<details class="opts" style="margin-top:14px"><summary>Connection<svg class="chev" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M6 9l6 6 6-6"/></svg></summary><div class="body"><p class="hint" style="margin:0 0 10px">'+esc(jf.url)+' · set up '+(jf.source==='phone'?'from this phone':'in the desktop app')+'</p>'+
    (jf.source==='phone'?'<button class="btn danger sm full" id="jfforget">Disconnect</button>':'')+(jfConnectOpen?jfConnectForm(''):'<button class="btn ghost sm full" id="jfchange" style="margin-top:8px">Connect a different server</button>')+'</div></details>';
  jfList();
}
async function refreshJellyfin(){
  if(jfBusy)return;jfBusy=true;
  if(!jf){$('jfbody').innerHTML=skeleton(3,2)}
  try{jf=await api('/api/jellyfin');renderJellyfin()}
  catch(e){$('jfstatus').className='lede status error';$('jfstatus').textContent=e.message;if(!jf)$('jfbody').innerHTML=''}
  finally{jfBusy=false}
}
async function jfAction(sid,action){try{await api('/api/jellyfin/control',{session:sid,action});setTimeout(refreshJfNow,500)}catch(e){toast(e.message,true)}}
async function jfScan(){const b=$('jfscan');b.disabled=true;try{await api('/api/jellyfin/scan',{});jfMissing=null;jfUp=null;toast('Library scan started on Jellyfin')}catch(e){toast(e.message,true)}finally{b.disabled=false}}
async function jfFind(){
  const box=$('jffound');box.innerHTML='<p class="hint" style="margin:0">Looking on your network…</p>';
  try{const d=await api('/api/jellyfin/discover');const sv=d.servers||[];
    box.innerHTML=sv.length?'<div class="kode" style="margin-bottom:6px">FOUND ON YOUR NETWORK</div>'+sv.map(x=>'<button class="shortcut" data-jpick="'+esc(x.url)+'" style="color:var(--white)"><span style="flex:1"><b>'+esc(x.name)+'</b><br><small style="font-family:var(--mono);color:var(--soft)">'+esc(x.url)+'</small></span><span style="color:var(--teal);font-weight:700;font-size:13px">Use this</span></button>').join(''):'<p class="hint" style="margin:0">None found. Type the address below (the PC running Jellyfin, port 8096).</p>'}
  catch(e){box.innerHTML='<p class="hint" style="margin:0">'+esc(e.message)+'</p>'}
}
async function jfSave(){
  const b=$('jfsave');b.disabled=true;b.textContent='Testing…';
  try{jf=await api('/api/jellyfin/connect',{url:$('jfurl').value.trim(),key:$('jfkey').value.trim()});jfConnectOpen=false;jfMissing=jfUp=jfResults=null;renderJellyfin();toast('Connected to Jellyfin')}
  catch(e){toast(e.message,true);b.disabled=false;b.textContent='Test & save'}
}
async function jfForget(){if(!confirm('Disconnect the Jellyfin server set up on this phone?'))return;try{jf=await api('/api/jellyfin/forget',{});jfMissing=jfUp=jfResults=null;renderJellyfin()}catch(e){toast(e.message,true)}}
async function openJfShow(id){
  const panel=$('sheetpanel');
  panel.innerHTML='<button class="dclose" data-close aria-label="Close">'+ICON.x+'</button><div class="empty"><b>Loading…</b></div>';
  $('sheet').classList.remove('hidden');document.body.style.overflow='hidden';
  if(!sheetOpen){sheetOpen=true;try{history.pushState({sheet:1},'')}catch{}}
  let d;try{d=await api('/api/jellyfin/show?id='+encodeURIComponent(id))}catch(e){panel.innerHTML='<button class="dclose" data-close aria-label="Close">'+ICON.x+'</button>'+empty('Couldn\'t load this show',e.message);return}
  const n=d.next,openSeason=n?n.season:((d.seasons.find(s=>s.missing.length)||d.seasons[d.seasons.length-1]||{}).season);
  const totalMiss=d.seasons.reduce((a,s)=>a+s.missing.length,0);
  const seasons=d.seasons.map(s=>{
    const w=s.episodes.filter(e=>e.watched).length,name=s.season===0?'Specials':'Season '+s.season;
    const miss=s.missing.length?'<div class="miss"><span>Missing '+esc(s.missing.map(x=>'E'+String(x).padStart(2,'0')).join(', '))+'</span><button class="btn sm" data-tvsearch="'+esc(d.title+' S'+String(s.season).padStart(2,'0')+(s.missing.length===1?'E'+String(s.missing[0]).padStart(2,'0'):''))+'">Search</button></div>':'';
    const rows=s.episodes.map(e=>'<div class="eprow'+(e.watched?' w':'')+'"><span class="n">E'+String(e.episode).padStart(2,'0')+'</span><div class="info"><div class="t">'+esc(e.title)+'</div><div class="m">'+esc([e.aired,runtimeText(e.runtime),e.watched?'watched':''].filter(Boolean).join(' · '))+'</div></div></div>').join('');
    return '<details class="season"'+(s.season===openSeason?' open':'')+'><summary>'+esc(name)+'<span class="m">'+w+'/'+s.episodes.length+' watched'+(s.missing.length?' · '+s.missing.length+' missing':'')+'</span></summary>'+miss+rows+'</details>';
  }).join('');
  panel.innerHTML='<button class="dclose" data-close aria-label="Close">'+ICON.x+'</button><div class="fan" style="aspect-ratio:3/1"></div>'+
    '<div class="dhead"><button class="dposter" data-view="'+esc(d.poster)+'" aria-label="View poster full size">'+artTag(d.poster)+'</button><div class="dtitle"><h2>'+esc(d.title)+'</h2><div class="m">'+esc([d.year,d.episodes+' episodes',d.watched+' watched',totalMiss?totalMiss+' missing':''].filter(Boolean).join(' · '))+'</div></div></div>'+
    ((d.genres||[]).length?'<div class="chips">'+d.genres.map(g=>'<span class="chip">'+esc(g)+'</span>').join('')+'</div>':'')+
    '<div class="dbody">'+(d.plot?'<p class="plot">'+esc(d.plot)+'</p>':'')+(n?'<p class="hint">Next up: <b style="color:var(--white)">'+esc(epCode(n))+' · '+esc(n.title)+'</b></p>':'')+
    (totalMiss>1?'<button class="btn full" data-tvsearch="'+esc(d.title)+'" style="margin-bottom:6px">Search for this show</button>':'')+'<div style="margin-top:8px">'+seasons+'</div></div>';
  panel.scrollTop=0;hydrateArt(panel);
}
function openJfMovie(id){
  const pool=[].concat(jf&&jf.recent||[],jfResults||[],jfUp||[]),x=pool.find(m=>m.id===id);if(!x)return;
  const panel=$('sheetpanel');
  panel.innerHTML='<button class="dclose" data-close aria-label="Close">'+ICON.x+'</button><div class="fan" style="aspect-ratio:3/1"></div><div class="dhead"><button class="dposter" data-view="'+esc(x.poster)+'" aria-label="View poster full size">'+artTag(x.poster)+'</button><div class="dtitle"><h2>'+esc(x.title)+'</h2><div class="m">'+esc([x.year,runtimeText(x.runtime)].filter(Boolean).join(' · '))+'</div></div></div>'+
    '<div class="dbody"><div class="rl" style="margin:14px 0"><span class="badge lib">✓ In Jellyfin</span>'+(x.quality?'<span class="qual'+(x.quality==='SD'||x.quality==='720p'?' lo':'')+'">'+esc(x.quality)+'</span>':'')+(x.size?'<span class="rc">'+esc(fmtBytes(x.size))+'</span>':'')+(x.watched?'<span class="rc">watched</span>':'')+'</div>'+
    '<button class="btn full" data-jbetter="'+esc(x.title+(x.year?' '+x.year:''))+'">Find a better copy</button></div>';
  $('sheet').classList.remove('hidden');document.body.style.overflow='hidden';
  if(!sheetOpen){sheetOpen=true;try{history.pushState({sheet:1},'')}catch{}}
  hydrateArt(panel);
}

async function kodiType(done){
  const box=$('kodytext'),text=box.value,b1=$('kodysend'),b2=$('kodysendonly');
  if(!text){box.focus();return}
  b1.disabled=b2.disabled=true;
  try{const r=await api('/api/kodi/text',{text,done});box.value='';
    toast(r.window==='field'?'Sent. If it didn\'t appear, select the text field on the TV and send again.':(done?'Typed on the TV and confirmed':'Typed on the TV'))}
  catch(e){toast(e.message,true)}
  finally{b1.disabled=b2.disabled=false}
}

// ---------- Phone alerts (ntfy) ----------
function ntfyTopic(){const a='abcdefghjkmnpqrstuvwxyz23456789';let t='nzbcc-';const r=new Uint8Array(12);(window.crypto||{}).getRandomValues?crypto.getRandomValues(r):r.forEach((_,i)=>r[i]=Math.random()*256);r.forEach(x=>t+=a[x%a.length]);return 'https://ntfy.sh/'+t}
async function openAlerts(){
  const panel=$('sheetpanel');
  panel.innerHTML='<button class="dclose" data-close aria-label="Close">'+ICON.x+'</button><div class="empty"><b>Loading…</b></div>';
  $('sheet').classList.remove('hidden');document.body.style.overflow='hidden';
  if(!sheetOpen){sheetOpen=true;try{history.pushState({sheet:1},'')}catch{}}
  let st,dr;try{[st,dr]=await Promise.all([api('/api/settings'),api('/api/drives').catch(()=>null)])}catch(e){panel.innerHTML='<button class="dclose" data-close aria-label="Close">'+ICON.x+'</button>'+empty('Couldn\'t load the alert settings',e.message);return}
  const da=(dr&&dr.alerts)||{},on=!!st.ntfy_url;
  const tg=(id,label,sub,checked)=>'<label class="tgl"><span>'+label+(sub?'<small>'+sub+'</small>':'')+'</span><input type="checkbox" id="'+id+'"'+(checked?' checked':'')+'></label>';
  panel.innerHTML='<button class="dclose" data-close aria-label="Close">'+ICON.x+'</button><div class="dbody" style="padding-top:20px">'+
    '<h2 style="margin:0 44px 6px 0;font-size:20px">Phone alerts</h2><p class="hint" style="margin:0 0 14px">'+(on?'Alerts are <b style="color:var(--green)">on</b>.':'Get a notification on this iPhone when a download finishes or a drive needs a look. Free, using the <b>ntfy</b> app.')+'</p>'+
    (on?'':'<div class="card"><div class="kode" style="margin-bottom:8px">SET UP (ONE TIME)</div><ol class="hint" style="margin:0;padding-left:18px;line-height:1.7"><li>Install <b>ntfy</b> from the App Store.</li><li>Tap <b>Use a new private topic</b> below, then <b>Copy</b>.</li><li>In ntfy tap <b>+</b>, paste the topic name, <b>Subscribe</b>, and allow notifications.</li><li>Tap <b>Save</b>, then <b>Send a test</b>.</li></ol></div>')+
    '<div class="alfield"><label class="label" for="alurl">ntfy topic address</label><div class="altop"><input id="alurl" class="field" placeholder="https://ntfy.sh/your-private-topic" autocomplete="off" autocapitalize="off" autocorrect="off" inputmode="url" value="'+esc(st.ntfy_url||'')+'"><button class="btn ghost sm" id="alcopy" style="flex:none">Copy</button></div>'+
    '<button class="btn ghost sm full" id="alnew" style="margin-top:8px">Use a new private topic</button><p class="hint" style="margin:6px 2px 0">Anyone who knows the topic name can read your alerts, so keep it long and random.</p></div>'+
    '<details class="opts"><summary>Access token (only for a private ntfy server)<svg class="chev" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M6 9l6 6 6-6"/></svg></summary><div class="body"><input id="altoken" class="field" type="password" placeholder="'+(st.ntfy_token_set?'Saved (leave empty to keep it)':'Not needed for ntfy.sh')+'" autocomplete="off"></div></details>'+
    '<div class="section">TELL ME WHEN</div><div class="card">'+
    tg('alcomplete','A download finishes','',st.notify_complete)+tg('alfailed','A download fails','',st.notify_failed)+
    tg('aldhealth','A drive has a problem','Windows reports it unhealthy',da.health!==false)+tg('aldhot','A drive is too hot','',da.hot!==false)+tg('aldlow','A drive is almost full','Less than '+esc(da.low_gb||75)+' GB free',da.low!==false)+'</div>'+
    '<div class="row" style="margin-top:14px"><button class="btn" id="alsave">Save</button><button class="btn ghost" id="altest">Send a test</button></div>'+
    (on?'<button class="btn danger sm full" id="aloff" style="margin-top:12px">Turn alerts off</button>':'')+'</div>';
  panel.scrollTop=0;
  const urlEl=$('alurl');
  $('alnew').onclick=()=>{urlEl.value=ntfyTopic();toast('New private topic ready. Tap Copy, then subscribe to it in ntfy.')};
  $('alcopy').onclick=async()=>{const v=urlEl.value.trim();if(!v){toast('Make a topic first',true);return}const name=v.split('/').pop();try{await navigator.clipboard.writeText(name);toast('Copied “'+name+'”. In ntfy tap +, paste, Subscribe.')}catch{urlEl.select();toast('Select the text and copy it: '+name)}};
  const save=async off=>{
    const body={ntfy_url:off?'':urlEl.value.trim(),saved_interval_hours:st.saved_interval_hours||6,notify_complete:$('alcomplete').checked,notify_failed:$('alfailed').checked};
    const t=$('altoken').value.trim();if(t)body.ntfy_token=t;if(off)body.clear_token=true;
    await api('/api/settings',body);
    await api('/api/drives/alerts',{notify_drive_health:$('aldhealth').checked,notify_drive_hot:$('aldhot').checked,notify_drive_low:$('aldlow').checked}).catch(()=>{});
  };
  $('alsave').onclick=async()=>{try{await save(false);toast(urlEl.value.trim()?'Alerts saved. Tap “Send a test” to check.':'Saved (alerts are off without a topic).');openAlerts()}catch(e){toast(e.message,true)}};
  $('altest').onclick=async()=>{try{await save(false);await api('/api/settings/test',{});toast('Test sent. It should pop up on this iPhone in a few seconds.')}catch(e){toast(e.message,true)}};
  if($('aloff'))$('aloff').onclick=async()=>{if(!confirm('Turn phone alerts off?'))return;try{await save(true);toast('Alerts turned off');openAlerts()}catch(e){toast(e.message,true)}};
}

// ---------- Sonarr & Radarr ("Movies & shows") ----------
let pvr=null,pvrKind='radarr',pvrQuery='',pvrResults=null,pvrUp=null,pvrUpErr='',pvrBusy=false,pvrOpts={},pvrAdding=null,homeUpKey='';
const SEARCH_SVG='<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#7F9DA6" stroke-width="2.2" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/></svg>';
function pvrPrefs(){try{return JSON.parse(LS.get('nzb_mobile_pvr','{}'))||{}}catch{return {}}}
function pvrApp(a){return ((pvr&&pvr.apps)||[]).find(x=>x.app===a)||{name:a==='radarr'?'Radarr':'Sonarr'}}
const pad2=n=>String(n).padStart(2,'0');
function dayLabel(d){const t=new Date();t.setHours(0,0,0,0);const x=new Date(d);x.setHours(0,0,0,0);const diff=Math.round((x-t)/864e5);
  if(diff===0)return 'Today';if(diff===1)return 'Tomorrow';if(diff===-1)return 'Yesterday';return x.toLocaleDateString([],{weekday:'short',day:'numeric',month:'short'})}
function upWhen(x){
  if(!x.when)return '';
  if(x.kind==='movie')return dayLabel(new Date(x.when.slice(0,10)+'T12:00:00'));
  const d=new Date(x.when);if(isNaN(d))return x.when.slice(0,10);
  const day=dayLabel(d),tm=d.toLocaleTimeString([],{hour:'numeric',minute:'2-digit'});
  return (day==='Today'&&d.getHours()>=17?'Tonight':day)+' · '+tm;
}
function upSub(x){return x.kind==='episode'?'S'+pad2(x.season)+'E'+pad2(x.episode)+(x.episode_title&&!/^TBA$/i.test(x.episode_title)?' · '+x.episode_title:''):(x.release||'Release')+(x.year?' · '+x.year:'')}
function upRow(x){
  return '<div class="qrow"><div class="pthumb" style="cursor:default">'+artTag(x.poster)+'</div><div class="info"><div class="t">'+esc(x.title)+'</div><div class="m">'+esc(upSub(x))+'</div><div class="upw">'+esc(upWhen(x))+(x.kind==='movie'?'':(x.network?' · '+esc(x.network):''))+'</div></div>'+
    (x.has_file?'<span class="badge lib" style="flex:none">✓ Got it</span>':'<span class="uptag '+(x.kind==='movie'?'mv':'tv')+'">'+(x.kind==='movie'?'MOVIE':'TV')+'</span>')+'</div>';
}
function renderHomeUp(items){
  const box=$('homeup'),key=JSON.stringify(items.map(x=>[x.title,x.when,x.season,x.episode]));
  if(key===homeUpKey)return;homeUpKey=key;
  box.innerHTML=items.length?'<div class="section">COMING UP</div>'+items.map(upRow).join('')+'<button class="btn ghost full sm" data-tab="pvr" style="margin-top:2px">Movies &amp; shows</button>':'';
  hydrateArt(box);
}
function pvrRow(x){
  const right=x.in_library?'<span class="badge lib" style="flex:none">✓ '+(x.has_file?'Have it':'Added')+'</span>':'<button class="btn sm" style="flex:none" data-pvradd="'+esc(x.ext_id)+'">Add</button>';
  return '<div class="qrow" style="align-items:flex-start"><div class="pthumb" style="cursor:default">'+artTag(x.poster)+'</div><div class="info"><div class="t">'+esc(x.title)+'</div><div class="m">'+esc([x.year,x.extra].filter(Boolean).join(' · '))+'</div>'+(x.overview?'<div class="pov">'+esc(x.overview)+'</div>':'')+'</div>'+right+'</div>';
}
async function pvrList(){
  const box=$('pvrlist');if(!box)return;
  if(!pvrQuery){box.innerHTML='';return}
  const a=pvrApp(pvrKind);
  if(!a.online){box.innerHTML=empty(a.name+(a.configured?' isn\'t answering':' wasn\'t found'),a.problem||('Start '+a.name+' on the PC, then pull down to refresh.'));return}
  if(pvrResults===null){box.innerHTML=skeleton(3,2);
    try{pvrResults=(await api('/api/arr/lookup?app='+pvrKind+'&q='+encodeURIComponent(pvrQuery))).items||[]}
    catch(e){box.innerHTML=empty('Couldn\'t look it up',e.message);return}}
  box.innerHTML='<div class="section">'+pvrResults.length+' FOUND</div>'+(pvrResults.map(pvrRow).join('')||empty('Nothing found','Try another spelling, or add the year.'));
  hydrateArt(box);
}
function renderPvrUp(){
  const box=$('pvrup');if(!box)return;
  if(pvrUp===null){box.innerHTML='<div class="section">COMING UP · NEXT 14 DAYS</div>'+skeleton(3,2);return}
  box.innerHTML='<div class="section">COMING UP · NEXT 14 DAYS</div>'+(pvrUp.length?pvrUp.map(upRow).join(''):(pvrUpErr?empty('Couldn\'t load the calendar',pvrUpErr):empty('Nothing coming up','New episodes of your shows in Sonarr and movies you add to Radarr show up here.')));
  hydrateArt(box);
}
function renderPvr(){
  const st=$('pvrstatus'),body=$('pvrbody'),apps=(pvr&&pvr.apps)||[];
  st.className='lede';
  st.textContent=apps.map(a=>a.name+' '+(a.online?'✓'+(a.queue?' · '+a.queue+' downloading':''):(a.configured?'offline':'not found'))).join(' · ');
  if(!apps.some(a=>a.configured)){body.innerHTML='<div class="card kidle"><span>Radarr and Sonarr weren\'t found on this PC. The phone server looks in MediaAutomation on each drive (for example Z:\\MediaAutomation) and in C:\\ProgramData. Start them on the PC, then pull down to refresh.</span></div>';return}
  const kinds=['radarr','sonarr'].map(a=>'<button data-pvrkind="'+a+'"'+(pvrKind===a?' class="on"':'')+'>'+(a==='radarr'?'Movie':'TV show')+'</button>').join('');
  body.innerHTML='<div class="card"><div class="seg" style="margin:0 0 10px">'+kinds+'</div><label class="search" style="margin:0">'+SEARCH_SVG+'<input id="pvrq" placeholder="'+(pvrKind==='radarr'?'Find a movie to add':'Find a TV show to add')+'" enterkeyhint="search" autocomplete="off" aria-label="Find something to add" value="'+esc(pvrQuery)+'"></label>'+
    '<p class="hint" style="margin:8px 2px 0">'+(pvrKind==='radarr'?'Radarr':'Sonarr')+' grabs it from your indexers and files it in your library. Looking up costs no indexer hits.</p></div><div id="pvrlist"></div><div id="pvrup"></div>';
  pvrList();renderPvrUp();
}
async function loadPvrUp(force){try{pvrUp=(await api('/api/arr/upcoming'+(force?'?fresh=1':''))).items||[];pvrUpErr=''}catch(e){pvrUp=[];pvrUpErr=e.message}renderPvrUp()}
async function refreshPvr(force){
  if(pvrBusy)return;pvrBusy=true;
  if(!pvr)$('pvrbody').innerHTML=skeleton(3,2);
  try{pvr=await api('/api/arr'+(force?'?fresh=1':''));if(force){pvrUp=null;pvrOpts={}}renderPvr();if(pvr.apps.some(a=>a.online)&&(pvrUp===null||force))loadPvrUp(force)}
  catch(e){$('pvrstatus').className='lede status error';$('pvrstatus').textContent=e.message;if(!pvr)$('pvrbody').innerHTML=''}
  finally{pvrBusy=false}
}
async function openPvrAdd(id){
  const x=(pvrResults||[]).find(r=>String(r.ext_id)===String(id));if(!x)return;
  const app=x.app,name=app==='radarr'?'Radarr':'Sonarr',panel=$('sheetpanel');
  panel.innerHTML='<button class="dclose" data-close aria-label="Close">'+ICON.x+'</button><div class="empty"><b>Loading…</b></div>';
  $('sheet').classList.remove('hidden');document.body.style.overflow='hidden';
  if(!sheetOpen){sheetOpen=true;try{history.pushState({sheet:1},'')}catch{}}
  let o;try{o=pvrOpts[app]||(pvrOpts[app]=await api('/api/arr/options?app='+app))}catch(e){panel.innerHTML='<button class="dclose" data-close aria-label="Close">'+ICON.x+'</button>'+empty('Couldn\'t reach '+name,e.message);return}
  if(!o.roots.length){panel.innerHTML='<button class="dclose" data-close aria-label="Close">'+ICON.x+'</button>'+empty(name+' needs a media folder','In '+name+' on the PC open Settings → Media Management → Add Root Folder, then try again.');delete pvrOpts[app];return}
  const p=pvrPrefs()[app]||{},sel=(v,cur)=>String(v)===String(cur)?' selected':'';
  const mons=(pvr&&pvr.monitor)||{future:'New episodes only',all:'Every episode'};
  pvrAdding=x;
  panel.innerHTML='<button class="dclose" data-close aria-label="Close">'+ICON.x+'</button>'+
    '<div class="dbody" style="padding-bottom:18px"><div class="nowrow pvrhead" style="margin:6px 48px 12px 0"><div class="pthumb" style="cursor:default">'+artTag(x.poster)+'</div><div class="info"><div class="kode">ADD TO '+name.toUpperCase()+'</div><div class="name">'+esc(x.title)+'</div><div class="sub">'+esc([x.year,x.extra].filter(Boolean).join(' · '))+'</div></div></div>'+
    (x.overview?'<p class="hint" style="margin:0 2px 12px">'+esc(x.overview)+'</p>':'')+
    '<label class="label" for="pvrprof">Quality</label><select id="pvrprof" class="field">'+o.profiles.map(q=>'<option value="'+esc(q.id)+'"'+sel(q.id,p.profile)+'>'+esc(q.name)+'</option>').join('')+'</select>'+
    (o.roots.length>1?'<label class="label" for="pvrroot">Save to</label><select id="pvrroot" class="field">'+o.roots.map(r=>'<option value="'+esc(r.path)+'"'+sel(r.path,p.root)+'>'+esc(r.path)+(r.free?' ('+fmtBytes(r.free)+' free)':'')+'</option>').join('')+'</select>':'<p class="hint" style="margin:0 2px 10px">Saves to '+esc(o.roots[0].path)+'</p>')+
    (app==='sonarr'?'<label class="label" for="pvrmon">Get</label><select id="pvrmon" class="field">'+Object.entries(mons).map(([k,v])=>'<option value="'+esc(k)+'"'+sel(k,p.monitor||'future')+'>'+esc(v)+'</option>').join('')+'</select>':'')+
    '<label class="chk"><input type="checkbox" id="pvrsearch"'+(p.search===false?'':' checked')+'> <span>'+(app==='radarr'?'Look for it now':'Look for the episodes now')+'<small>Checks your indexers once. Otherwise '+name+' waits for new releases.</small></span></label>'+
    '<button class="btn full" id="pvrgo">Add to '+name+'</button></div>';
  hydrateArt(panel);
}
async function pvrAdd(){
  const x=pvrAdding;if(!x)return;const b=$('pvrgo');b.disabled=true;b.textContent='Adding…';
  const body={app:x.app,id:x.ext_id,profile:+$('pvrprof').value,root:$('pvrroot')?$('pvrroot').value:'',monitor:$('pvrmon')?$('pvrmon').value:'future',search:$('pvrsearch').checked};
  const all=pvrPrefs();all[x.app]={profile:body.profile,root:body.root,monitor:body.monitor,search:body.search};LS.set('nzb_mobile_pvr',JSON.stringify(all));
  try{const r=await api('/api/arr/add',body);r.title=r.title||x.title;closeSheet();x.in_library=true;
    toast(r.already?r.title+' is already in '+(x.app==='radarr'?'Radarr':'Sonarr')+'.':'Added '+r.title+(r.searching?'. Looking for it on your indexers now.':'.'));
    pvrUp=null;homeUpKey='';renderPvr();loadPvrUp(true)}
  catch(e){toast(e.message,true);b.disabled=false;b.textContent='Try again'}
}

// ---------- Pull down to refresh ----------
function refreshActive(){
  const f={home:()=>{drivesAt=0;usageAt=0;arrivedKey='';homeUpKey='';refreshHome()},queue:()=>refreshQueue(),history:()=>refreshHistory(),kodi:()=>refreshKodi(),
    jellyfin:()=>{jfMissing=jfUp=jfResults=null;refreshJellyfin()},drives:()=>refreshDrives(true),pvr:()=>{pvrResults=null;refreshPvr(true)}}[activeTab];
  if(f){f();return true}return false;
}
function setupPullToRefresh(){
  const ind=document.createElement('div');ind.className='ptr';ind.setAttribute('aria-hidden','true');ind.textContent='Pull to refresh';document.body.appendChild(ind);
  let y0=null,ready=false;
  window.addEventListener('touchstart',e=>{y0=(window.scrollY<=0&&!sheetOpen&&!$('app').classList.contains('hidden')&&e.touches.length===1)?e.touches[0].clientY:null;ready=false},{passive:true});
  window.addEventListener('touchmove',e=>{if(y0==null)return;const dy=e.touches[0].clientY-y0;if(dy<=12){ind.classList.remove('show');return}
    ready=dy>90;ind.textContent=ready?'Release to refresh':'Pull to refresh';ind.style.setProperty('--ptr',Math.min(dy/2,56)+'px');ind.classList.add('show')},{passive:true});
  window.addEventListener('touchend',()=>{if(y0==null)return;y0=null;ind.classList.remove('show');if(ready){ready=false;if(refreshActive())toast('Refreshing…')}},{passive:true});
}

async function kodiScan(){
  const b=$('kodiscan');b.disabled=true;
  try{const r=await api('/api/kodi/scan',{});kodiMovies=null;
    if(r.jellyfin)toast('Scanning Jellyfin and Kodi. New movies show up in Kodi in a few minutes, once Jellyfin has found them and the Jellyfin add-on has synced.'+(r.kodi===false?' (Kodi itself didn\'t answer.)':''));
    else if(r.jellyfin===false)toast('Kodi is scanning, but Jellyfin didn\'t answer ('+(r.jellyfin_problem||'offline')+'), so new movies may not appear yet.',true);
    else toast('Library scan started on Kodi')}
  catch(e){toast(e.message,true)}finally{b.disabled=false}
}

async function retryItem(id){try{await api('/api/history/retry',{id});toast('Retry requested');await refreshHistory()}catch(e){toast(e.message,true)}}
async function deleteHistory(id){if(!confirm('Remove this entry from the history? Downloaded files are kept.'))return;try{await api('/api/history/delete',{id});await refreshHistory()}catch(e){toast(e.message,true)}}

let seenHistory=null;
async function watchFinished(){
  if(document.hidden||$('app').classList.contains('hidden'))return;
  let d;try{d=await api('/api/history')}catch{return}
  const slots=d.slots||[];
  if(seenHistory===null){seenHistory=new Set(slots.map(x=>x.id));return}
  const fresh=slots.filter(x=>!seenHistory.has(x.id)&&/^(completed|failed)$/i.test(x.status||''));
  slots.forEach(x=>seenHistory.add(x.id));
  if(!fresh.length)return;
  const bad=fresh.filter(x=>/failed/i.test(x.status));
  const names=fresh.slice(0,3).map(x=>(/failed/i.test(x.status)?'Failed: ':'Finished: ')+x.name).join('\n')+(fresh.length>3?'\n…and '+(fresh.length-3)+' more':'');
  toast(names,bad.length>0);
  if(fresh.some(x=>/completed/i.test(x.status)))kodiMovies=null;
  if(activeTab==='history')refreshHistory();
}
function bind(){
  $('pairbtn').addEventListener('click',pair);
  $('drivecheck').addEventListener('click',()=>refreshDrives(true));
  $('homejf').addEventListener('click',e=>{const a=e.target.closest('[data-jact]');if(a)jfAction(a.dataset.sid,a.dataset.jact).then(()=>setTimeout(refreshHome,600))});
  $('homearrived').addEventListener('click',e=>{const b=e.target.closest('[data-hplay]');if(b)homePlay(b)});
  $('alertsbtn').addEventListener('click',openAlerts);
  setupPullToRefresh();
  $('kodysend').addEventListener('click',()=>kodiType(true));
  $('kodysendonly').addEventListener('click',()=>kodiType(false));
  $('kodytext').addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();kodiType(true)}});
  $('jfscan').addEventListener('click',jfScan);
  $('pvr').addEventListener('click',e=>{
    const k=e.target.closest('[data-pvrkind]');if(k&&k.dataset.pvrkind!==pvrKind){pvrKind=k.dataset.pvrkind;pvrResults=null;return renderPvr()}
    const a=e.target.closest('[data-pvradd]');if(a)return openPvrAdd(a.dataset.pvradd);
  });
  $('pvr').addEventListener('keydown',e=>{if(e.target.id==='pvrq'&&e.key==='Enter'){e.preventDefault();pvrQuery=e.target.value.trim();pvrResults=null;e.target.blur();pvrList()}});
  $('jellyfin').addEventListener('click',e=>{
    const a=e.target.closest('[data-jact]');if(a)return jfAction(a.dataset.sid,a.dataset.jact);
    const m=e.target.closest('[data-jmode]');if(m){jfMode=jfMode===m.dataset.jmode?'':m.dataset.jmode;return renderJellyfin()}
    const pk=e.target.closest('[data-jpick]');if(pk){$('jfurl').value=pk.dataset.jpick;$('jfkey').focus();return}
    if(e.target.closest('#jffind'))return jfFind();
    if(e.target.closest('#jfsave'))return jfSave();
    if(e.target.closest('#jfforget'))return jfForget();
    if(e.target.closest('#jfchange')){jfConnectOpen=true;return renderJellyfin()}
  });
  $('jellyfin').addEventListener('keydown',e=>{if(e.target.id==='jfq'&&e.key==='Enter'){e.preventDefault();jfQuery=e.target.value.trim();jfMode=jfQuery?'search':'';jfResults=null;e.target.blur();renderJellyfin()}});
  document.addEventListener('click',e=>{
    const sh=e.target.closest('[data-jshow]');if(sh){if(e.target.closest('#sheet'))closeSheet();return openJfShow(sh.dataset.jshow)}
    const mv=e.target.closest('[data-jmovie]');if(mv&&!e.target.closest('#sheet'))return openJfMovie(mv.dataset.jmovie);
    const bt=e.target.closest('[data-jbetter]');if(bt){if(e.target.closest('#sheet'))closeSheet();goSearch(bt.dataset.jbetter,{category:bt.dataset.jcat!==undefined?bt.dataset.jcat:'2000'})}
  });
  $('drivebody').addEventListener('click',e=>{const c=e.target.closest('[data-ddrive]');if(c)openDrive(c.dataset.ddrive)});
  $('drivebody').addEventListener('change',e=>{const c=e.target.closest('[data-dalert]');if(c)setDriveAlert(c.dataset.dalert,c.checked)});
  $('token').addEventListener('keydown',e=>{if(e.key==='Enter')pair()});
  document.addEventListener('click',e=>{const b=e.target.closest('[data-tab]');if(b)showTab(b.dataset.tab)});
  document.addEventListener('click',e=>{const b=e.target.closest('[data-kact]');if(b)kodiControl(b.dataset.kact,b.dataset.pid)});
  $('homedl').addEventListener('click',async e=>{const b=e.target.closest('[data-hact]');if(!b)return;b.disabled=true;await queueAll(b.dataset.hact);refreshHome()});
  $('kodilist').addEventListener('click',e=>{const b=e.target.closest('[data-play]');if(b)playMovie(b.dataset.play,b.dataset.resume==='1')});
  document.addEventListener('click',e=>{const b=e.target.closest('[data-detail]');if(b&&!e.target.closest('#sheet'))openDetail(b.dataset.detail)});
  document.addEventListener('click',e=>{const b=e.target.closest('[data-show]');if(b&&!e.target.closest('#sheet'))openShow(b.dataset.show)});
  document.addEventListener('click',e=>{const b=e.target.closest('[data-ep]');if(!b)return;playEpisode(b.dataset.ep,b.dataset.resume==='1');if(e.target.closest('#sheet'))closeSheet()});
  document.addEventListener('click',e=>{const b=e.target.closest('[data-tvsearch]');if(!b)return;if(e.target.closest('#sheet'))closeSheet();goSearch(b.dataset.tvsearch,{category:'5000'})});
  $('sheet').addEventListener('click',e=>{
    if(e.target===$('sheet')||e.target.closest('[data-close]'))return closeSheet();
    if(e.target.closest('#pvrgo'))return pvrAdd();
    const v=e.target.closest('[data-view]');if(v&&v.dataset.view)return openViewer(v.dataset.view);
    const p=e.target.closest('[data-dplay]');if(p){playMovie(p.dataset.dplay,p.dataset.resume==='1');closeSheet();return}
    if(e.target.closest('[data-better]')&&detailMovie){
      const d=detailMovie;upgradeFrom=RES_RANK[d.quality]?{q:d.quality,hdr:d.hdr}:null;closeSheet();
      goSearch(d.title+(d.year?' '+d.year:''),{imdbid:d.imdb_id||'',category:'2000',keepUpgrade:true});return;
    }
    const qp=e.target.closest('[data-qprio]');if(qp)return queueItemChange('priority',qp.dataset.qprio);
    if(e.target.closest('[data-qcat]'))return queueItemChange('category',$('qcat').value);
    if(e.target.closest('[data-qtop]'))return queueItemChange('top');
  });
  $('kodiseg').addEventListener('click',e=>{const b=e.target.closest('[data-kmode]');if(b)setKodiMode(b.dataset.kmode)});
  let tvTimer=null;$('tvfilter').addEventListener('input',()=>{clearTimeout(tvTimer);tvTimer=setTimeout(()=>renderShows(true),200)});
  $('tvview').value=LS.get('nzb_mobile_tvview','all');if(!$('tvview').value)$('tvview').value='all';
  $('tvview').addEventListener('change',()=>{LS.set('nzb_mobile_tvview',$('tvview').value);window.scrollTo(0,0);renderShows(true)});
  $('tvmore').addEventListener('click',()=>{tvShown+=TV_PAGE;renderShows()});
  $('viewer').addEventListener('click',()=>$('viewer').classList.add('hidden'));
  window.addEventListener('popstate',()=>{if(!$('viewer').classList.contains('hidden'))$('viewer').classList.add('hidden');closeSheet(true)});
  document.addEventListener('keydown',e=>{if(e.key==='Escape'){if(!$('viewer').classList.contains('hidden'))$('viewer').classList.add('hidden');else closeSheet()}});
  let kodiTimer=null;$('kodifilter').addEventListener('input',()=>{clearTimeout(kodiTimer);kodiTimer=setTimeout(()=>renderKodiList(true),200)});
  $('kodiview').value=LS.get('nzb_mobile_kodiview','recent');if(!$('kodiview').value)$('kodiview').value='recent';
  $('kodiview').addEventListener('change',()=>{LS.set('nzb_mobile_kodiview',$('kodiview').value);window.scrollTo(0,0);renderKodiList(true)});
  $('kodimore').addEventListener('click',()=>{kodiShown+=KODI_PAGE;renderKodiList()});
  $('kodiscan').addEventListener('click',kodiScan);
  $('tvon').addEventListener('click',tvOn);$('tvkodi').addEventListener('click',tvKodi);$('tvsave').addEventListener('click',tvSave);
  $('searchbtn').addEventListener('click',search);
  $('query').addEventListener('keydown',e=>{if(e.key==='Enter')search()});
  ['sort','minsize','maxsize','mingrabs'].forEach(id=>$(id).addEventListener('input',()=>{savePrefs();renderResults()}));
  $('indexer').addEventListener('change',()=>fillSearchCategories());
  ['sabcat','prio','indexer','category'].forEach(id=>$(id).addEventListener('change',savePrefs));
  $('selvisible').addEventListener('click',()=>{filterSort(results,currentFilter()).forEach(x=>{if(!added.has(x.id)&&selected.size<10)selected.add(x.id)});renderResults()});
  $('selnone').addEventListener('click',()=>{selected.clear();renderResults()});
  $('batchbtn').addEventListener('click',addSelected);
  $('results').addEventListener('click',e=>{const card=e.target.closest('.result'),b=e.target.closest('[data-act="add"]');if(card&&b)addResult(card.dataset.id,b)});
  $('results').addEventListener('change',e=>{const card=e.target.closest('.result');if(!card||e.target.dataset.act!=='sel')return;const id=card.dataset.id;if(e.target.checked){if(selected.size>=10){e.target.checked=false;toast('Select no more than 10 NZBs at a time.',true);return}selected.add(id)}else selected.delete(id);updateBatchButton()});
  $('queueitems').addEventListener('click',e=>{const row=e.target.closest('.queueitem'),b=e.target.closest('[data-act]');if(row&&b)itemAction(row.dataset.id,b.dataset.act)});
  $('historyitems').addEventListener('click',e=>{const row=e.target.closest('.queueitem'),b=e.target.closest('[data-act]');if(!row||!b)return;if(b.dataset.act==='retry')retryItem(row.dataset.id);else if(b.dataset.act==='hdel')deleteHistory(row.dataset.id)});
  $('qresume').addEventListener('click',()=>queueAll('resume'));
  $('qpause').addEventListener('click',()=>queueAll('pause'));
  $('speedset').addEventListener('click',setSpeed);
  $('histrefresh').addEventListener('click',refreshHistory);
  setInterval(()=>{if(!document.hidden&&activeTab==='queue'&&!$('app').classList.contains('hidden'))refreshQueue(true)},4000);
  setInterval(pollStatus,10000);
  setInterval(watchFinished,15000);
  $('recent').addEventListener('click',e=>{const b=e.target.closest('[data-recent]');if(b)useRecent(b.dataset.recent)});
  renderRecent();
  setInterval(()=>{if(document.hidden||$('app').classList.contains('hidden'))return;if(activeTab==='home')refreshHome();else if(activeTab==='kodi')refreshKodiNow();else if(activeTab==='jellyfin')refreshJfNow();else if(activeTab==='drives')refreshDriveIO()},5000);
}
bind();
if(token)connect();
</script></body></html>
"""
