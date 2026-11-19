"""The run page: one research run for a viewer, live or finished.

watch.py serves it live and export-run writes it as one self-contained file. It is organised as views with
their own addresses rather than one long scroll:
  #overview      what was asked, the run's numbers, a minute-by-minute chart, and the findings in brief
  #findings      a card for each revenue exposure found
  #case/ID       one finding in full: the event, the outcomes, the figures behind them with their provenance,
                 the evidence, who to call and the opening message
  #research/C/P  the reasoning as chapters, one per finding, each ending where that finding was saved, paged
                 at most PAGE_STEPS steps to a page; a player replays it at a pace set by each step's content
  #also          what was set aside, and the leads it suggests
  #found/ID      the step where a finding was saved; #step/N a step by number; #play replays from the start
"""

FAVICON = ("data:image/svg+xml," + "%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' "
           "rx='8' fill='%23111827'/%3E%3Cpath d='M7 20l5-6 4 4 9-10' stroke='%2334d399' stroke-width='3' fill='none' "
           "stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E")

# util.found_line for the pages: cases are [{company_id, company}], one per active case
FOUND_JS = r"""function tally(cases) { return {n: cases.length, k: new Set(cases.map(c => c.company_id || c.company)).size}; }
function foundLine(cases) { const {n, k} = tally(cases); if (!n) return "no revenue exposures";
  return `${n} revenue exposure${n === 1 ? "" : "s"} ${k === 1 ? "at" : "across"} ${k} compan${k === 1 ? "y" : "ies"}`; }"""

# The minute-by-minute activity chart, shared by the run page and the guide. bins: [{search, read, calc}] per
# minute; flags: [{t: seconds, n}] for each exposure found; upto: seconds drawn so far (Infinity for all).
CHART_JS = r"""function drawChart(svg, bins, flags, upto, H) {
  const W = Math.max(280, Math.round(svg.getBoundingClientRect().width || 1000)); svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  const N = Math.max(1, bins.length), TOP = 30, BASE = H - 22, BW = W / N;
  const maxv = Math.max(1, ...bins.map(b => b.search + b.read + b.calc)), y = v => (BASE - TOP) * v / maxv;
  const every = N <= 40 ? 5 : N <= 90 ? 10 : N <= 150 ? 15 : 30; let s = "";
  for (let m = 0; m < N; m += every) { const x = m * BW; s += `<line x1="${x}" x2="${x}" y1="${TOP - 6}" y2="${BASE}" stroke="#eef0f3"/>`
    + `<text x="${x + 3}" y="${H - 6}" font-size="12" fill="#94a3b8">${m} min</text>`; }
  bins.forEach((b, i) => { if (i * 60 > upto) return; const f = Math.min(1, (upto - i * 60) / 60), x = i * BW + BW * .14, w = Math.max(1, BW * .72);
    let h0 = 0; [["search", "#2563eb"], ["read", "#94a3b8"], ["calc", "#7c3aed"]].forEach(([k, col]) => { const h = y(b[k]) * f; if (h > 0)
      s += `<rect x="${x.toFixed(1)}" y="${(BASE - h0 - h).toFixed(1)}" width="${w.toFixed(1)}" height="${h.toFixed(1)}" rx="2" fill="${col}"/>`; h0 += h; }); });
  let lastCx = -99;
  flags.forEach(fl => { if (fl.t > upto) return; const x = Math.min(W - 9, Math.max(9, fl.t / 60 * BW)), cx = Math.max(x, lastCx + 19), cy = TOP - 10; lastCx = cx;
    s += `<line x1="${x}" x2="${x}" y1="${TOP - 2}" y2="${BASE}" stroke="#16a34a" stroke-width="2" stroke-dasharray="3 3"/>`
      + `<circle cx="${cx}" cy="${cy}" r="8.5" fill="#16a34a" stroke="#fff" stroke-width="1.5"/><text x="${cx}" y="${cy + 3.5}" font-size="10" fill="#fff" text-anchor="middle" font-weight="700">${fl.n}</text>`; });
  s += `<line x1="0" x2="${W}" y1="${BASE}" y2="${BASE}" stroke="#e2e8f0"/>`;
  svg.innerHTML = s;
}"""

ICONS = {
    "search": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>',
    "read": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5M9 13h6M9 17h4"/></svg>',
    "web": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/></svg>',
    "calc": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><rect x="5" y="3" width="14" height="18" rx="2"/><path d="M8 7h8M8 12h2M14 12h2M8 16h2M14 16h2"/></svg>',
    "lookup": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><path d="M6 3h12v18l-6-4-6 4z"/></svg>',
    "finding": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><path d="M5 21V4h11l-1.5 4L16 12H5"/></svg>',
    "save": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/></svg>',
    "answer": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><path d="M4 5h16v11H8l-4 4z"/></svg>',
    "play": '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M7 4.5v15l12.5-7.5z"/></svg>',
    "pause": '<svg viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="4.5" width="4.2" height="15" rx="1"/><rect x="13.8" y="4.5" width="4.2" height="15" rx="1"/></svg>',
}

STYLE = r"""
:root{--bg:#f7f8fa;--card:#fff;--line:#e7e9ee;--line2:#eff1f4;--ink:#0f172a;--soft:#334155;--mute:#64748b;--faint:#94a3b8;
--blue:#2563eb;--bluebg:#eef3ff;--green:#16a34a;--greenink:#15803d;--greenbg:#ecfdf3;--greenline:#bbf7d0;--violet:#7c3aed;
--amber:#b45309;--amberbg:#fff7ed;--red:#dc2626}
*{box-sizing:border-box}[hidden]{display:none!important}
html,body{margin:0;background:var(--bg);color:var(--ink);font:15.5px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Inter,system-ui,sans-serif;
-webkit-font-smoothing:antialiased}a{color:var(--blue);text-decoration:none}a:hover{text-decoration:underline}button{font-family:inherit}
.top{position:sticky;top:0;z-index:20;background:rgba(247,248,250,.92);backdrop-filter:saturate(1.6) blur(12px);border-bottom:1px solid var(--line)}
.top .in{max-width:1040px;margin:0 auto;padding:0 28px;height:58px;display:flex;align-items:center;gap:16px}
.brand{font-weight:700;font-size:14px;color:var(--ink);display:flex;align-items:center;gap:9px;white-space:nowrap}
.brand img{width:22px;height:22px}.brand:hover{text-decoration:none}.divider{width:1px;height:22px;background:var(--line);flex:0 0 1px}
.tabs{display:flex;gap:2px;overflow-x:auto;scrollbar-width:none;min-width:0}.tabs::-webkit-scrollbar{display:none}
.tabs a{padding:7px 12px;border-radius:9px;color:var(--mute);font-weight:600;font-size:14px;white-space:nowrap}
.tabs a:hover{background:#eceff3;color:var(--ink);text-decoration:none}.tabs a.on{background:var(--ink);color:#fff}
.tabs .cnt{display:inline-block;margin-left:6px;font-size:11.5px;font-weight:700;padding:0 6px;border-radius:99px;background:rgba(100,116,139,.15);line-height:17px}
.tabs a.on .cnt{background:rgba(255,255,255,.22)}.spacer{flex:1}
.pill{display:inline-flex;align-items:center;gap:6px;font-size:12px;font-weight:650;padding:3px 10px;border-radius:99px;white-space:nowrap}
.pill.running{background:#dbeafe;color:#1d4ed8}.pill.failed{background:#fee2e2;color:#b91c1c}.pill.finished{background:#dcfce7;color:var(--greenink)}
.pulse{display:inline-block;width:7px;height:7px;border-radius:50%;background:#2563eb;animation:pulse 1.4s infinite}
@keyframes pulse{0%{box-shadow:0 0 0 0 rgba(37,99,235,.55)}100%{box-shadow:0 0 0 9px rgba(37,99,235,0)}}
.btn{display:inline-flex;align-items:center;gap:8px;border:1px solid var(--line);background:var(--card);color:var(--ink);border-radius:99px;
padding:10px 17px;font-weight:600;font-size:14px;line-height:1;cursor:pointer;white-space:nowrap}
.btn:hover{border-color:#cbd5e1;text-decoration:none}.btn.dark{background:var(--ink);border-color:var(--ink);color:#fff}
.btn.sm{padding:7px 13px;font-size:13px}.btn svg{width:13px;height:13px}
.wrap{max-width:1040px;margin:0 auto;padding:38px 28px 70px}
.view{animation:fade .25s ease}@keyframes fade{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:none}}
h1{font-size:36px;line-height:1.12;letter-spacing:-.03em;margin:0 0 12px;font-weight:790}
h2{font-size:24px;letter-spacing:-.02em;line-height:1.25;margin:0 0 6px;font-weight:760}
.lede{font-size:17.5px;color:var(--soft);max-width:760px;margin:0 0 20px}.sub{color:var(--mute);margin:0 0 20px;font-size:15px;max-width:800px}
.sub b{color:var(--ink)}.mute{color:var(--mute)}
.k{font-size:11.5px;text-transform:uppercase;letter-spacing:.08em;color:var(--faint);font-weight:700}
.meta{color:var(--mute);font-size:14px;margin:-8px 0 20px}.assign{padding:14px 18px;background:var(--card);border:1px solid var(--line);border-radius:14px;color:var(--soft);max-width:880px}
.assign .k{display:block;margin-bottom:2px}.req{color:var(--mute);font-size:14px;margin:10px 0 0}
.tiles{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:12px;margin:22px 0 14px}
.tile{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px 16px 12px}
.tile span{white-space:nowrap}.tile b{display:block;font-size:28px;letter-spacing:-.03em;line-height:1.1;font-variant-numeric:tabular-nums}.tile span{color:var(--mute);font-size:13px}
.panel{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:18px 20px}
.ph{display:flex;align-items:center;gap:14px;flex-wrap:wrap;margin-bottom:6px}.ph h3{margin:0;font-size:15px;font-weight:700}
.legend{display:flex;gap:14px;font-size:12.5px;color:var(--mute);flex-wrap:wrap}
.legend i{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:5px;vertical-align:-1px}
.chart{display:block;width:100%;height:190px;cursor:pointer}.hint{color:var(--faint);font-size:13px;margin:6px 0 0}
.ctas{display:flex;gap:10px;flex-wrap:wrap;margin:22px 0 0}.sect{margin-top:48px}
.num{display:inline-grid;place-items:center;flex:0 0 auto;width:24px;height:24px;border-radius:50%;background:var(--green);color:#fff;font-size:12px;font-weight:760;font-style:normal}
.multi{display:inline-block;font-style:normal;font-size:11.5px;font-weight:650;color:var(--greenink);background:var(--greenbg);border-radius:99px;
padding:0 8px;line-height:19px;margin-left:8px;vertical-align:1px;white-space:nowrap}
.row{display:grid;grid-template-columns:auto minmax(0,1fr) auto auto 14px;gap:18px;align-items:center;background:var(--card);border:1px solid var(--line);
border-radius:14px;padding:14px 18px;margin-bottom:10px;color:var(--ink)}
.row:hover{border-color:#cbd5e1;text-decoration:none;box-shadow:0 4px 14px rgba(15,23,42,.06)}
.row .who b{display:block;font-size:16px}.row .who span{color:var(--mute);font-size:13.5px}
.row .date,.row .amt{text-align:right;white-space:nowrap}.row .date b{display:block;font-size:14.5px}.row .amt b{display:block;font-size:19px;letter-spacing:-.02em}
.row .date span,.row .amt span{font-size:12.5px;color:var(--mute)}.go{color:var(--faint);font-size:20px;line-height:1}
.also2{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:16px}
.also2 a{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px 18px;color:var(--ink);font-weight:650}
.also2 a:hover{border-color:#cbd5e1;text-decoration:none}.also2 a span{display:block;color:var(--mute);font-weight:400;font-size:13.5px}
.cards{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.c{display:block;background:var(--card);border:1px solid var(--line);border-radius:18px;padding:20px 22px;color:var(--ink)}
.c:hover{border-color:#cbd5e1;text-decoration:none;box-shadow:0 8px 24px rgba(15,23,42,.07)}
.c .hd{display:flex;gap:14px;align-items:flex-start;justify-content:space-between}
.c .co{font-size:18px;font-weight:740;letter-spacing:-.01em;display:flex;align-items:center;gap:9px}.c .st{color:var(--mute);font-size:13.5px;margin-top:2px}
.ring{position:relative;width:62px;height:62px;flex:0 0 62px}.ring svg{width:62px;height:62px;transform:rotate(-90deg)}
.ring b{position:absolute;inset:0;display:grid;place-items:center;font-size:13.5px;font-weight:750}
.c .amt{font-size:30px;font-weight:800;letter-spacing:-.03em;margin:14px 0 8px;line-height:1.1}
.c .amt small{font-size:14px;font-weight:600;color:var(--mute);letter-spacing:0;margin-left:6px}
.chip{display:flex;gap:12px;align-items:center;margin-top:10px;font-size:14px;color:var(--soft);min-width:0}
.chip small{display:block;color:var(--faint);font-size:12.5px}.chip .tx{min-width:0}
.clamp2{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.cal{width:44px;flex:0 0 44px;border:1px solid var(--line);border-radius:9px;overflow:hidden;text-align:center;background:#fff}
.cal i{display:block;background:#ef4444;color:#fff;font-size:10px;font-weight:750;font-style:normal;letter-spacing:.06em;line-height:16px}
.cal b{display:block;font-size:18px;line-height:24px;color:var(--ink)}
.av{width:40px;height:40px;flex:0 0 40px;border-radius:50%;background:linear-gradient(135deg,#6366f1,#2563eb);color:#fff;display:grid;place-items:center;font-weight:700;font-size:14px}
.ic{width:40px;height:40px;flex:0 0 40px;border-radius:12px;background:var(--bluebg);color:var(--blue);display:grid;place-items:center}.ic svg{width:19px;height:19px}
.c .open{display:block;margin-top:14px;color:var(--blue);font-weight:650;font-size:14px}
.crumbs{display:flex;align-items:center;gap:10px;margin-bottom:24px;font-size:14px;color:var(--mute)}
.crumbs a.back{font-weight:650}
.casehead{display:flex;gap:24px;align-items:flex-start;justify-content:space-between}
.casehead .eyebrow{display:flex;align-items:center;gap:9px;font-weight:650;color:var(--soft);font-size:15.5px;margin-bottom:8px;flex-wrap:wrap}
.casehead h1{font-size:32px;margin:0}.stakebox{display:flex;gap:14px;align-items:center}
.stake b{display:block;font-size:34px;font-weight:800;letter-spacing:-.03em;line-height:1.1}.stake span{color:var(--mute);font-size:13px;display:block;max-width:250px}
.keyfacts{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin:24px 0 6px}
.keyfacts .chip{margin:0;background:var(--card);border:1px solid var(--line);border-radius:14px;padding:12px 14px;color:var(--ink)}
.keyfacts a.chip:hover{border-color:#cbd5e1;text-decoration:none}
.jump{display:flex;flex-wrap:wrap;align-items:center;gap:6px;margin:22px 0 0}.jump .k{margin-right:6px}
.jump button{border:1px solid var(--line);background:var(--card);border-radius:99px;padding:5px 11px;font-size:12.5px;font-weight:600;color:var(--soft);cursor:pointer}
.jump button:hover{border-color:#cbd5e1;color:var(--ink)}
.doc2{display:grid;grid-template-columns:190px minmax(0,1fr);gap:0 28px;margin-top:18px}.doc2>h3{scroll-margin-top:80px}
.doc2>h3{margin:0;padding:18px 0;border-top:1px solid var(--line);font-size:12px;text-transform:uppercase;letter-spacing:.08em;color:var(--mute);font-weight:750}
.doc2>div{padding:16px 0 22px;border-top:1px solid var(--line);color:var(--soft);font-size:15px;min-width:0}
.doc2>div p{margin:0 0 10px}.doc2>div p:last-child{margin-bottom:0}.doc2 .big{color:var(--ink);font-weight:600;font-size:16px}
.sc{display:grid;grid-template-columns:minmax(0,1.15fr) minmax(0,1fr) 160px;gap:16px;align-items:center;padding:12px 0;border-top:1px solid var(--line2)}
.sc.base{border-top:0}.sc .sl b{display:block;color:var(--ink);font-size:14px}.sc .sl span{font-size:13px;color:var(--mute)}
.bar{height:14px;border-radius:99px;background:#f1f3f6;display:flex;overflow:hidden}.bar i{background:#94a3b8}.bar s{background:#f87171;text-decoration:none}
.sc.base .bar i{background:var(--green)}.sc .sv{text-align:right;font-size:13px;color:var(--mute)}.sc .sv b{display:block;font-size:15px;color:var(--ink)}
.sc .sv .neg{color:var(--red);font-weight:650}
.inp{display:grid;grid-template-columns:170px minmax(0,1fr);gap:18px;padding:14px 0;border-top:1px solid var(--line2)}.inp:first-child{border-top:0;padding-top:2px}
.inp .iv b{display:block;font-size:18px;color:var(--ink);letter-spacing:-.01em;line-height:1.3}.inp .iv span{font-size:12.5px;color:var(--mute)}
.inp .unit{font-size:12.5px;color:var(--mute)}.inp .note{display:block;font-size:12.5px;color:var(--faint);margin-top:4px}
.badge{display:inline-block;font-size:10.5px;font-weight:760;letter-spacing:.05em;text-transform:uppercase;border-radius:6px;padding:1px 7px;margin-right:8px;vertical-align:1px}
.badge.reported{background:var(--greenbg);color:var(--greenink)}.badge.derived{background:var(--bluebg);color:var(--blue)}.badge.assumed{background:var(--amberbg);color:var(--amber)}
.quote{display:-webkit-box;-webkit-line-clamp:4;-webkit-box-orient:vertical;overflow:hidden;margin-top:6px;padding:8px 12px;background:#f8fafc;
border-left:3px solid #cbd5e1;border-radius:0 8px 8px 0;font-size:13.5px;color:var(--soft);cursor:pointer;overflow-wrap:anywhere}
.quote.full{display:block}.src{display:inline-block;font-size:12.5px;color:var(--mute);margin-top:5px}
.ev{padding:12px 0;border-top:1px solid var(--line2)}.ev:first-child{border-top:0;padding-top:2px}.ev b{color:var(--ink);font-size:14px;font-weight:650}
blockquote.opening{margin:0;padding:14px 18px;background:#f8fafc;border-left:3px solid var(--blue);border-radius:0 12px 12px 0;color:var(--ink);font-size:15px;white-space:pre-wrap}
.casefoot{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-top:30px;padding-top:22px;border-top:1px solid var(--line)}
.player{position:sticky;top:68px;z-index:15;display:flex;align-items:center;gap:16px;background:var(--card);border:1px solid var(--line);
border-radius:16px;padding:10px 16px 10px 10px;box-shadow:0 6px 22px rgba(15,23,42,.07)}
.pp{width:44px;height:44px;flex:0 0 44px;border-radius:50%;border:0;background:var(--ink);color:#fff;display:grid;place-items:center;cursor:pointer}
.pp svg{width:17px;height:17px}.pp:hover{background:#1e293b}
.pmeta{min-width:160px}.pmeta b{display:block;font-size:14px}.pmeta span{font-size:12.5px;color:var(--mute);font-variant-numeric:tabular-nums}
.scrub{flex:1;position:relative;height:44px;cursor:pointer;min-width:120px}
.track{position:absolute;left:0;right:0;top:27px;height:6px;border-radius:99px;background:#e8ebf0;overflow:hidden}.fill{height:100%;background:var(--ink);width:0}
.flag{position:absolute;top:0;transform:translateX(-50%);width:20px;height:20px;border-radius:50%;background:var(--green);color:#fff;font-size:10.5px;
font-weight:760;display:grid;place-items:center;border:2px solid #fff;padding:0;cursor:pointer;box-shadow:0 1px 3px rgba(15,23,42,.2)}
.cut{position:absolute;top:26px;width:2px;height:8px;background:#fff;transform:translateX(-50%)}
.knob{position:absolute;top:23px;width:14px;height:14px;border-radius:50%;background:#fff;border:3px solid var(--ink);transform:translateX(-50%);pointer-events:none}
.speed{display:flex;background:#f1f3f6;border-radius:99px;padding:3px}
.speed button{border:0;background:none;padding:5px 10px;border-radius:99px;font-weight:650;font-size:12.5px;color:var(--mute);cursor:pointer}
.speed button.on{background:var(--card);color:var(--ink);box-shadow:0 1px 3px rgba(15,23,42,.12)}
.livebar{padding:12px 18px;color:var(--soft);font-size:14px}.livebar b{font-variant-numeric:tabular-nums}
.chaps{position:relative;display:flex;gap:8px;overflow-x:auto;margin:18px 0 26px;padding:2px 2px 6px;scrollbar-width:thin}
.chap{flex:0 0 auto;min-width:138px;max-width:230px;background:var(--card);border:1px solid var(--line);border-radius:12px;padding:9px 13px;color:var(--ink)}
.chap:hover{border-color:#cbd5e1;text-decoration:none}.chap.on{border-color:var(--ink);box-shadow:0 0 0 1px var(--ink)}
.chap b{display:flex;align-items:center;gap:7px;font-size:13.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.chap b .num{width:20px;height:20px;font-size:11px}
.chap small{display:block;color:var(--faint);font-size:12px;margin-top:1px}
.chead .k{display:block;margin-bottom:4px}.chead h2{margin:0 0 4px}.chead .sub{margin:0 0 16px}
.steps{background:var(--card);border:1px solid var(--line);border-radius:16px;overflow:hidden}
.step{display:grid;grid-template-columns:58px minmax(0,1fr);gap:14px;padding:16px 22px 16px 20px;border-top:1px solid var(--line2);position:relative;scroll-margin:170px}
.step:first-child{border-top:0}.step .t{font-size:12.5px;color:var(--faint);font-weight:650;font-variant-numeric:tabular-nums;padding-top:3px}
.step .lead{margin:0;font-size:15.5px;color:var(--ink);line-height:1.55}.step .rest{color:var(--soft);font-size:14.5px;margin-top:8px}.step .rest p{margin:0 0 8px}
.step.cur{background:#f8faff}.step.cur:before{content:"";position:absolute;left:0;top:0;bottom:0;width:3px;background:var(--blue)}
.step.new{animation:fade .45s ease}.step.flash{animation:flash 2.2s ease}@keyframes flash{0%,40%{background:#fef9c3}100%{background:transparent}}
.link{display:block;border:0;background:none;color:var(--blue);font-weight:600;font-size:13px;padding:0;margin:6px 0 0;cursor:pointer;line-height:1.4}
.say{margin:10px 0 0;padding:10px 14px;background:#f8fafc;border:1px solid var(--line);border-radius:10px;font-size:14.5px;color:var(--soft)}
.say p{margin:0 0 6px}.say p:last-child{margin:0}
.srow{display:flex;flex-wrap:wrap;align-items:center;gap:8px 16px;margin-top:10px}.srow .link{margin:0}
.chips{display:inline-flex;flex-wrap:wrap;align-items:center;gap:8px;border:1px solid var(--line);background:var(--card);border-radius:99px;padding:4px 12px;
font-size:12.5px;color:var(--mute);cursor:pointer;line-height:1.6;text-align:left}.chips:hover{border-color:#cbd5e1}
.chips svg{width:13px;height:13px;vertical-align:-2px;margin-right:3px}.chips .chev{color:var(--blue);font-weight:650}.sep{color:#cbd5e1}.warn{color:var(--amber)}
ul.al{list-style:none;margin:10px 0 0;padding:0 0 0 2px;border-left:2px solid var(--line2)}
.act{display:flex;gap:10px;padding:4px 8px 4px 12px;font-size:13.5px;color:var(--soft);border-radius:0 8px 8px 0}
.act.new{animation:fade .35s ease;background:linear-gradient(90deg,#eef3ff,rgba(238,243,255,0))}
.act a{color:var(--soft)}.act a:hover{color:var(--blue)}.act svg{width:15px;height:15px;flex:0 0 15px;margin-top:3px;color:var(--faint)}
.act .meta{color:var(--faint);margin-left:6px;font-size:12.5px}.act.blocked,.act.blocked a{color:var(--faint)}.act.blocked .meta{color:var(--amber)}
.act.refused .meta{color:#b91c1c}.act .atext{color:var(--mute);font-size:13px;margin-top:1px}
.det{margin-top:4px;display:flex;flex-direction:column;gap:2px}.det a{font-size:12.5px;color:var(--mute)}
.det pre{margin:4px 0 0;padding:8px 10px;background:#f8fafc;border:1px solid var(--line);border-radius:7px;font:11.5px/1.5 ui-monospace,Menlo,monospace;
white-space:pre-wrap;color:var(--mute);max-height:150px;overflow:auto}
.found{display:flex;align-items:center;gap:10px;margin-top:12px;padding:10px 14px;background:var(--greenbg);border:1px solid var(--greenline);border-radius:12px;
color:var(--greenink);font-size:14px}.found:hover{text-decoration:none;border-color:#86efac}.found b{color:var(--ink)}
.found .go{margin-left:auto;font-weight:650;font-size:13.5px;color:var(--greenink);white-space:nowrap}
.found.new{animation:pop .7s ease}@keyframes pop{0%{transform:scale(.96);opacity:0}60%{transform:scale(1.015);opacity:1}100%{transform:none}}
.pager{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-top:18px}.pager .pg{color:var(--mute);font-size:13.5px}
.endcard{margin-top:18px;padding:20px 22px;background:var(--greenbg);border:1px solid var(--greenline);border-radius:16px;color:var(--soft)}
.endcard b{color:var(--ink)}.endcard .ctas{margin-top:14px}
.empty{color:var(--mute);background:var(--card);border:1px dashed #d5d9e0;border-radius:14px;padding:22px;text-align:center}
.alist{display:flex;flex-direction:column;gap:10px;margin:10px 0 0}
.alist div{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:13px 17px;font-size:14.5px;color:var(--soft)}.alist b{color:var(--ink)}
.lead,.rest,.say,.act,.alist div,.doc2>div{overflow-wrap:anywhere}
#latest{position:fixed;bottom:24px;left:50%;transform:translateX(-50%);background:var(--ink);color:#fff;border:0;border-radius:99px;z-index:30;
padding:10px 18px;font-weight:600;font-size:13px;line-height:1;cursor:pointer;box-shadow:0 8px 24px rgba(15,23,42,.22)}
footer{max-width:1040px;margin:0 auto;padding:0 28px 56px;color:var(--faint);font-size:13px}footer a{color:var(--mute)}
.runs{max-width:920px;margin:0 auto;padding:28px 28px 80px}.ttl{font-weight:700}.top .sub{margin:0;font-size:13px}
.run{display:flex;align-items:center;gap:18px;background:var(--card);border:1px solid var(--line);border-radius:15px;padding:15px 20px;margin-bottom:10px;color:var(--ink)}
.run:hover{border-color:#cbd5e1;text-decoration:none}.run .what{flex:1;min-width:0}.run .what b{font-weight:650}
.run .what div{color:var(--mute);font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.run .n{font-size:13px;color:var(--mute);text-align:right;min-width:150px;font-variant-numeric:tabular-nums}
@media(max-width:820px){.cards,.also2,.keyfacts{grid-template-columns:1fr}.tiles{grid-template-columns:repeat(3,minmax(0,1fr))}.row{grid-template-columns:auto minmax(0,1fr) auto}.row .date,.row .go{display:none}
.doc2{grid-template-columns:1fr}.doc2>h3{padding:18px 0 4px}.doc2>div{border-top:0;padding-top:4px}.casehead{flex-direction:column;gap:14px}
.sc{grid-template-columns:1fr;gap:6px}.sc .sv{text-align:left}.inp{grid-template-columns:1fr;gap:4px}}
@media(max-width:640px){.top .in,.wrap,footer{padding-left:16px;padding-right:16px}.brand span,.top .about{display:none}h1{font-size:28px}
.step{grid-template-columns:1fr;gap:2px;padding:14px 16px}.player{flex-wrap:wrap;top:64px;gap:10px}.scrub{order:3;flex-basis:100%}.pmeta{flex:1;min-width:0}}
"""

PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__ · distress-signals</title><link rel="icon" href="__FAVICON__"><style>__STYLE__</style></head><body>
<header class="top"><div class="in"><a class="brand" href="__HOME__"><img src="__FAVICON__" alt=""><span>distress-signals</span></a><span class="divider"></span>
  <nav class="tabs" id="tabs"></nav><span class="spacer"></span><span class="pill" id="pill" hidden></span>
  <a class="btn sm about" href="__REPO__" target="_blank" rel="noopener">About the project</a></div></header>
<main class="wrap">
<section class="view" id="v-overview" hidden>
  <h1>__TITLE__</h1><p class="lede">__LEDE__</p><p class="meta" id="o-meta"></p>
  <div class="assign" __ASSIGNHIDDEN__><span class="k">The assignment</span>__ASSIGNMENT__</div><p class="req" id="o-req" hidden></p>
  <div class="tiles" id="o-tiles"></div>
  <div class="panel"><div class="ph"><h3>What it did, minute by minute</h3><span class="spacer"></span>
    <div class="legend"><span><i style="background:#2563eb"></i>searches</span><span><i style="background:#94a3b8"></i>reading</span>
      <span><i style="background:#7c3aed"></i>calculations</span><span><i style="background:#16a34a"></i>exposure found</span></div></div>
    <svg class="chart" id="o-chart" role="img" aria-label="Activity by minute"></svg>
    <p class="hint">Click the chart to see what it was doing at that minute.</p></div>
  <div class="ctas"><a class="btn dark" id="o-watch" href="#play"></a><a class="btn" href="#findings">See the findings</a></div>
  <div class="sect"><h2>What it found</h2><p class="sub" id="o-lead"></p><div id="o-rows"></div><div class="also2" id="o-also"></div></div>
</section>
<section class="view" id="v-findings" hidden><h1>What it found</h1><p class="sub" id="f-lead"></p><div class="cards" id="f-cards"></div></section>
<section class="view" id="v-case" hidden></section>
<section class="view" id="v-research" hidden>
  <h1>How it got there</h1><p class="sub">The researcher's own account of its thinking, step by step, with the searches, documents and calculations
  under each step. There is one chapter for each finding. Press play to watch it unfold, or pick a chapter.</p>
  <div class="player" id="player"><button class="pp" id="pp" aria-label="Play or pause"></button>
    <div class="pmeta"><b id="ptitle"></b><span id="ptime"></span></div>
    <div class="scrub" id="scrub" title="Jump to a point in the run"><div class="track"><div class="fill" id="fill"></div></div><div id="marks"></div><div class="knob" id="knob"></div></div>
    <div class="speed" id="speed"><button data-x="1">1×</button><button data-x="2">2×</button><button data-x="4">4×</button></div></div>
  <div class="player livebar" id="livebar" hidden></div>
  <nav class="chaps" id="chaps"></nav><div id="chapter"></div>
</section>
<section class="view" id="v-also" hidden><h1>Also considered</h1>
  <p class="sub">What the researcher checked and ruled out, and the leads it suggests for a follow-up run.</p><div id="a-body"></div></section>
</main>
<footer>Made with <a href="__REPO__" target="_blank" rel="noopener">distress-signals</a>, which finds companies whose revenue depends on an upcoming
event they don't control, works out what's at stake, and names the person to talk to.</footer>
<button id="latest" hidden>Jump to the latest step ↓</button>
<script>
__FOUNDJS__
__CHARTJS__
const RUN = "__RUN__", ICONS = __ICONS__, PAGE_STEPS = __PAGESTEPS__, PACE = __PACE__, DATA = __DATA__;
const $ = (s, r = document) => r.querySelector(s), $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const inline = s => s.replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>").replace(/`([^`]+)`/g, "$1");
const paras = s => esc(s).split(/\n{2,}/).filter(p => p.trim()).map(p => "<p>" + inline(p).replace(/^\s*[-•*] /gm, "• ").replace(/\n/g, "<br>") + "</p>").join("");
const money = (x, c) => x == null ? "" : ((c && c !== "USD") ? c + " " : "$") + (Math.abs(x) >= 1e9 ? (x/1e9).toFixed(2) + "B" :
  Math.abs(x) >= 1e6 ? (x/1e6).toFixed(1) + "M" : Math.abs(x) >= 1e3 ? Math.round(x/1e3) + "K" : Math.round(x));
const pct = x => x == null ? "" : `${Math.round(100 * x)}%`;
const MON = ["JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"];
const day = d => new Date(d + "T12:00:00");
const longDate = d => d ? day(d).toLocaleDateString("en-US", {month: "long", day: "numeric", year: "numeric"}) : "";
const clock = s => `${Math.floor((s || 0) / 60)}:${String(Math.floor((s || 0) % 60)).padStart(2, "0")}`;
const dur = s => s < 90 ? `${Math.max(1, Math.round(s))} sec` : `${Math.round(s / 60)} min`;
const words = s => (String(s || "").match(/\S+/g) || []).length;
const clampN = (x, a, b) => Math.max(a, Math.min(b, x));
const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;
const cap = s => s.replace(/^./, c => c.toUpperCase());
const host = u => { try { return new URL(u).hostname.replace(/^www\./, ""); } catch (e) { return "source"; } };
const initials = n => (String(n || "").split(",")[0].split(/\s+/).filter(w => /^[A-Za-z]/.test(w)).slice(0, 2).map(w => w[0]).join("") || "?").toUpperCase();
const NOUN = {search:["search","searches"],read:["document","documents"],web:["web page","web pages"],calc:["calculation","calculations"],
  lookup:["check","checks"],finding:["note","notes"],save:["write-up","write-ups"],answer:["summary","summaries"]};
const KIND = {reported: "Reported", derived: "Calculated", assumed: "Assumed"};

// A thought's first sentence is its headline; the rest is a click away
function split(t) {
  t = String(t || "").trim(); if (!t) return ["", ""];
  const ps = t.split(/\n{2,}/), p0 = ps[0], m = p0.match(/^([\s\S]{20,}?[a-z0-9)\]"'”%][.!?])\s+(?=[A-Z"“(])/);
  return [(m ? m[1] : p0).replace(/\*\*/g, ""), [m ? p0.slice(m[0].length) : "", ...ps.slice(1)].filter(x => x.trim()).join("\n\n")];
}

// ---- the run, as the page sees it ----
let items = [], status = {}, details = {}, after = 0, M = null;
function ingest(ops) { for (const op of ops) { if (op.op === "add") items.push({...op.item}); else if (items[op.i]) Object.assign(items[op.i], op.fields); } }
function build() {
  const steps = [], base = {}, seen = new Set(), finds = [], aside = [], leads = []; let cur = null, end = null;
  const step = (t, el) => { const [lead, rest] = split(t); cur = {i: steps.length, el: el ?? (cur ? cur.el : 0) ?? 0, lead, rest, says: [], acts: [], finds: []}; steps.push(cur); };
  for (const it of items) {
    if (it.k === "think") step(it.t, it.el);
    else if (it.k === "say" || it.k === "note") { if (!cur) step("", it.el); cur.says.push(it.t); }
    else if (it.k === "act") { if (!cur) step("", it.el); cur.acts.push(it);
      if (it.case) { base[it.case.oid] = {...(base[it.case.oid] || {}), ...it.case};
        if (!seen.has(it.case.oid)) { seen.add(it.case.oid); const f = {oid: it.case.oid, step: cur.i, el: it.el ?? cur.el}; cur.finds.push(f); finds.push(f); } }
      if (it.tool === "record_finding" && it.finding === "dropped" && it.status === "ok" && it.text) aside.push(it.text); }
    else if (it.k === "final") leads.push(...(it.leads || []));
    else if (it.k === "end") end = it;
  }
  const caseOf = oid => ({...(base[oid] || {}), ...(details[oid] || {}), oid});
  finds.forEach(f => f.c = caseOf(f.oid));
  const found = finds.filter(f => f.c.status !== "withdrawn"); found.forEach((f, i) => f.n = i + 1);
  const per = {}; found.forEach(f => (per[f.c.company_id || f.c.company] ??= []).push(f));
  found.forEach(f => { const g = per[f.c.company_id || f.c.company]; f.of = g.length; f.nth = g.indexOf(f) + 1; });
  // chapters end where each finding was saved; long ones are paged
  const chapters = []; let from = 0;
  steps.forEach(s => { const f = s.finds.find(x => x.n); if (f) { chapters.push({from, to: s.i, find: f}); from = s.i + 1; } });
  if (from < steps.length || !chapters.length) chapters.push({from, to: steps.length - 1, find: null});
  chapters.forEach((ch, ci) => { ch.i = ci; const n = Math.max(0, ch.to - ch.from + 1), np = Math.max(1, Math.ceil(n / PAGE_STEPS)), size = Math.max(1, Math.ceil(n / np));
    ch.pages = Array.from({length: np}, (_, p) => [ch.from + p * size, Math.min(ch.to, ch.from + (p + 1) * size - 1)]);
    ch.t0 = steps[ch.from] ? steps[ch.from].el : 0; ch.t1 = steps[ch.to] ? steps[ch.to].el : ch.t0; });
  // the replay: one beat per thing to take in, each held long enough to read it
  const beats = [], first = []; let acc = 0;
  const beat = (s, k, d, x) => { beats.push({s, k, d, at: acc, ...x}); acc += d; };
  steps.forEach(s => { first[s.i] = beats.length;
    beat(s.i, "think", clampN(PACE.think[0] + PACE.think[1] * words(s.lead), PACE.think[2], PACE.think[3]));
    s.says.forEach((t, j) => beat(s.i, "say", clampN(PACE.say[0] + PACE.say[1] * words(t), PACE.say[2], PACE.say[3]), {j}));
    s.acts.forEach((a, j) => beat(s.i, "act", PACE.act, {j}));
    s.finds.filter(f => f.n).forEach(f => beat(s.i, "find", PACE.find, {f})); });
  const secs = status.duration_ms ? status.duration_ms / 1000 : Math.max(60, ...items.map(i => i.el || 0));
  const minutes = Math.max(1, Math.ceil(secs / 60)), bins = Array.from({length: minutes}, () => ({search: 0, read: 0, calc: 0}));
  items.forEach(it => { if (it.k !== "act" || it.status === "blocked") return;
    const k = it.icon === "search" ? "search" : (it.icon === "read" || it.icon === "web") ? "read" : it.icon === "calc" ? "calc" : null;
    if (k) bins[Math.min(minutes - 1, Math.floor((it.el || 0) / 60))][k]++; });
  const count = k => bins.reduce((a, b) => a + b[k], 0);
  return {steps, finds, found, chapters, beats, first, total: acc, aside, leads, end, bins, secs,
          stats: {searches: count("search"), documents: count("read"), calculations: count("calc")}};
}
const live = () => status.state === "running" && !DATA;
const elapsed = () => status.duration_ms ? status.duration_ms / 1000 : live() && status.t0 ? (Date.now() - Date.parse(status.t0)) / 1000 : M.secs;
const locate = si => { for (const ch of M.chapters) for (let p = 0; p < ch.pages.length; p++) { const [a, b] = ch.pages[p]; if (si >= a && si <= b) return {ci: ch.i, pi: p}; }
  const ch = M.chapters[M.chapters.length - 1]; return {ci: ch.i, pi: ch.pages.length - 1}; };
const isLastPage = () => route.ci === M.chapters.length - 1 && route.pi === M.chapters[route.ci].pages.length - 1;
const pageFirstBeat = () => M.first[M.chapters[route.ci].pages[route.pi][0]] ?? 0;
const runDay = () => status.t0 ? status.t0.slice(0, 10) : null;
const daysOut = d => d && runDay() ? Math.round((day(d) - day(runDay())) / 864e5) : null;
const daysLabel = n => n == null ? "" : n > 0 ? `${n} days out` : n === 0 ? "the day of the run" : `${-n} days before the run`;

// ---- views ----
let route = {v: "overview"};
const opened = new Set(), actsOpen = new Set(), actsClosed = new Set();
function parse() {
  const [v, a, b] = decodeURIComponent(location.hash.slice(1)).split("/");
  if (v === "case" && a) return {v: "case", oid: a};
  if (v === "found" && a) return {v: "research", find: a};
  if (v === "step" && a) return {v: "research", step: +a};
  if (v === "play") return {v: "research", play: true};
  if (v === "research") return {v: "research", ci: a ? +a - 1 : null, pi: b ? +b - 1 : 0};
  if (v === "findings" || v === "also") return {v};
  return {v: "overview"};
}
function go(r) {
  if (r.v === "research") {
    if (r.play && !live() && M.beats.length) { route = {v: "research", ci: 0, pi: 0}; render(); window.scrollTo({top: 0}); startAt(0); return; }
    if (P.playing) pause();
    let si = r.step != null ? r.step : null;
    if (r.find) { const f = M.finds.find(x => x.oid === r.find); if (f) si = f.step; }
    if (si != null && M.steps.length) { si = clampN(si, 0, M.steps.length - 1); P.reveal = false; route = {v: "research", ...locate(si), flash: si};
      history.replaceState(null, "", `#research/${route.ci + 1}/${route.pi + 1}`); }
    else if (r.ci == null) {
      if (P.reveal && M.beats.length) route = {v: "research", ...locate(M.beats[P.pos].s)};
      else { const last = M.chapters.length - 1; route = live() ? {v: "research", ci: last, pi: M.chapters[last].pages.length - 1} : {v: "research", ci: 0, pi: 0}; }
    } else { P.reveal = false; const ci = clampN(r.ci || 0, 0, M.chapters.length - 1); route = {v: "research", ci, pi: clampN(r.pi || 0, 0, M.chapters[ci].pages.length - 1)}; }
    if (!P.reveal) P.pos = pageFirstBeat();
  } else { if (P.playing) pause(); route = r; }
  render(); window.scrollTo({top: 0});
  if (route.flash != null) { const si = route.flash; route.flash = null; actsOpen.add(si); research();
    const el = $("#s" + si); if (el) { el.scrollIntoView({block: "center"}); el.classList.add("flash"); } }
  if (live() && route.v === "research" && isLastPage()) window.scrollTo({top: document.body.scrollHeight});
}
function render() {
  const nA = M.aside.length + M.leads.length;
  $("#tabs").innerHTML = [["overview", "Overview"], ["findings", `Findings<span class="cnt">${M.found.length}</span>`], ["research", "The research"],
    ...(nA ? [["also", `Also considered<span class="cnt">${nA}</span>`]] : [])].map(([k, l]) =>
    `<a href="#${k}" class="${route.v === k || (k === "findings" && route.v === "case") ? "on" : ""}">${l}</a>`).join("");
  pill();
  for (const v of $$(".view")) v.hidden = v.id !== "v-" + route.v;
  ({overview, findings, case: caseView, research, also})[route.v]();
}
function pill() { const p = $("#pill"); p.hidden = !(live() || status.state === "failed"); p.className = "pill " + status.state;
  p.innerHTML = live() ? `<span class="pulse"></span>Live · ${clock(elapsed())}` : "Failed"; }

function overview() {
  const st = M.stats, cos = tally(M.found.map(f => f.c)).k;
  const tiles = [[Math.max(1, Math.round(elapsed() / 60)), "minutes"], [st.searches, "searches"], [st.documents, "documents read"],
    [st.calculations, "calculations"], [M.found.length, M.found.length === 1 ? "revenue exposure" : "revenue exposures"], [cos, cos === 1 ? "company" : "companies"]];
  const when = status.t0 ? new Date(status.t0).toLocaleString("en-US", {month: "long", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit"}) : "";
  $("#o-meta").innerHTML = [when, live() ? "running now" : `${plural(Math.max(1, Math.round(elapsed() / 60)), "minute", "minutes")}`,
    status.cost != null ? `$${status.cost.toFixed(2)} in AI usage` : ""].filter(Boolean).join(" · ");
  $("#o-tiles").innerHTML = tiles.map(([b, s]) => `<div class="tile"><b>${b}</b><span>${s}</span></div>`).join("");
  drawChart($("#o-chart"), M.bins, M.found.map(f => ({t: f.el, n: f.n})), Infinity, 190);
  const w = $("#o-watch"); w.hidden = !M.steps.length;
  if (live()) { w.innerHTML = `${ICONS.play} Follow it live`; w.href = "#research"; } else { w.innerHTML = `${ICONS.play} Watch the research · about ${dur(M.total)}`; w.href = "#play"; }
  const rq = status.requester; $("#o-req").hidden = !rq;
  if (rq) $("#o-req").innerHTML = `Requested from <a href="${esc(rq.url)}" target="_blank" rel="noopener">${esc(rq.name)}</a> in HubSpot.`;
  $("#o-lead").innerHTML = M.found.length ? `<b>${cap(foundLine(M.found.map(f => f.c)))}.</b> Each is one revenue stream riding on a dated event. Open one for the full case.`
    : live() ? "Nothing yet. Findings appear here as the researcher saves them." : "Nothing met the bar in this run.";
  $("#o-rows").innerHTML = M.found.map(f => { const c = f.c, n = daysOut(c.event_date);
    return `<a class="row" href="#case/${esc(f.oid)}"><span class="num">${f.n}</span><div class="who"><b>${esc(c.company)}</b><span>${esc(c.stream || "")}`
      + `${f.of > 1 ? `<i class="multi">${f.nth} of ${f.of} at this company</i>` : ""}</span></div>`
      + `<div class="date">${c.event_date ? `<b>${day(c.event_date).toLocaleDateString("en-US", {month: "short", day: "numeric", year: "numeric"})}</b><span>${daysLabel(n)}</span>` : ""}</div>`
      + `<div class="amt"><b>${money(c.stake, c.currency)}</b><span>${c.share != null ? pct(c.share) + " of revenue" : "at stake"}</span></div><span class="go">›</span></a>`; }).join("");
  const also = [];
  if (M.aside.length) also.push(`<a href="#also">${M.aside.length} looked at and set aside<span>What it checked and ruled out, and why</span></a>`);
  if (M.leads.length) also.push(`<a href="#also">${plural(M.leads.length, "lead", "leads")} worth a look next<span>What it suggests for a follow-up run</span></a>`);
  $("#o-also").innerHTML = also.join(""); $("#o-also").hidden = !also.length;
}
function ring(share) { if (share == null) return ""; const r = 26, c = 2 * Math.PI * r, p = clampN(share, 0, 1);
  return `<div class="ring"><svg viewBox="0 0 62 62"><circle cx="31" cy="31" r="${r}" fill="none" stroke="#eef0f3" stroke-width="7"/>`
    + `<circle cx="31" cy="31" r="${r}" fill="none" stroke="#16a34a" stroke-width="7" stroke-linecap="round" stroke-dasharray="${c * p} ${c}"/></svg><b>${pct(p)}</b></div>`; }
function calChip(c, small) { if (!c.event_date) return ""; const d = day(c.event_date), n = daysOut(c.event_date);
  return `<div class="chip"><div class="cal"><i>${MON[d.getMonth()]}</i><b>${d.getDate()}</b></div><div class="tx"><span class="clamp2">${esc(c.event || "")}</span>`
    + `<small>${small ? daysLabel(n) : longDate(c.event_date) + (n != null ? " · " + daysLabel(n) : "")}</small></div></div>`; }
function findings() {
  $("#f-lead").innerHTML = M.found.length ? `<b>${cap(foundLine(M.found.map(f => f.c)))}.</b> Each card is one revenue stream riding on a dated event. `
    + `Open one for the full case: the numbers and where they come from, the evidence, and who to call.` : live() ? "Nothing yet. Findings appear here as the researcher saves them." : "Nothing met the bar in this run.";
  $("#f-cards").innerHTML = M.found.map(f => { const c = f.c;
    return `<a class="c" href="#case/${esc(f.oid)}"><div class="hd"><div><div class="co"><span class="num">${f.n}</span>${esc(c.company)}</div>`
      + `<div class="st">${esc(c.stream || "")}${f.of > 1 ? `<i class="multi">${f.nth} of ${f.of} at this company</i>` : ""}</div></div>${ring(c.share)}</div>`
      + `<div class="amt">${money(c.stake, c.currency)}<small>${c.share != null ? "of revenue at stake" : "at stake"}</small></div>` + calChip(c, true)
      + (c.who ? `<div class="chip"><span class="av">${esc(initials(c.who))}</span><div class="tx">${esc(c.who)}<small>who to call</small></div></div>` : "")
      + `<span class="open">Open the full case →</span></a>`; }).join("");
}
function fmtVal(x) { const u = String(x.unit || ""), v = x.value, m = u.match(/^([A-Z]{3})\b[\s,]*(.*)$/);
  if (v == null) return ["", u]; if (m) return [money(v, m[1]), m[2]];
  if (/share|fraction|proportion|percent|rate|margin|ratio/i.test(u) && Math.abs(v) <= 1.5) return [pct(v), u];
  return [Number(v).toLocaleString("en-US", {maximumFractionDigits: 2}), u]; }
function scenarios(c) {
  const b = c.baseline || {}, list = c.scenarios || [], cur = c.currency; if (b.revenue == null || !list.length) return "";
  const max = Math.max(b.revenue, ...list.map(s => (s.revenue || 0) + (s.shortfall || 0))) || 1, first = t => split(t)[0];
  const one = (cls, name, desc, rev, short, share) => `<div class="sc ${cls}"><div class="sl"><b>${esc(name)}</b><span>${esc(first(desc))}</span></div>`
    + `<div class="bar"><i style="width:${100 * Math.max(0, rev || 0) / max}%"></i>${short ? `<s style="width:${100 * short / max}%"></s>` : ""}</div>`
    + `<div class="sv"><b>${money(rev, cur)}</b>${short ? `<span class="neg">−${money(short, cur)}</span>${share != null ? ` · ${pct(share)} of revenue` : ""}` : "<span>revenue</span>"}</div></div>`;
  const per = c.period_start ? ` from ${longDate(c.period_start)} to ${longDate(c.period_end)}` : "";
  return `<p>This stream's revenue${per}, if the event goes the company's way and if it doesn't. Red is the revenue at risk.</p>`
    + one("base", "If it goes their way", b.description, b.revenue, 0) + list.map(s => one("", s.label, s.description, s.revenue, s.shortfall, s.share)).join("");
}
function quote(text, url) { return `<span class="quote" title="Click to read it all">${esc(text || "")}</span>`
  + (url ? `<a class="src" href="${esc(url)}" target="_blank" rel="noopener">${esc(host(url))} ↗</a>` : ""); }
function caseView() {
  const box = $("#v-case"), i = M.found.findIndex(f => f.oid === route.oid);
  if (i < 0) { box.innerHTML = `<div class="crumbs"><a class="back" href="#findings">‹ All findings</a></div><div class="empty">This finding isn't part of this run.</div>`; return; }
  const f = M.found[i], c = f.c, prev = M.found[i - 1], next = M.found[i + 1];
  const secs = [], p = t => t ? `<p>${esc(t)}</p>` : "";
  const sec = (h, body) => { if (!body) return ""; secs.push(h); return `<h3 id="sec${secs.length}">${h}</h3><div>${body}</div>`; };
  box.innerHTML = `<div class="crumbs"><a class="back" href="#findings">‹ All findings</a><span class="spacer"></span><span>Finding ${f.n} of ${M.found.length}</span>`
    + (prev ? `<a class="btn sm" href="#case/${esc(prev.oid)}">‹ Previous</a>` : "") + (next ? `<a class="btn sm" href="#case/${esc(next.oid)}">Next ›</a>` : "") + `</div>`
    + `<div class="casehead"><div><div class="eyebrow"><span class="num">${f.n}</span>${esc(c.company)}${f.of > 1 ? `<i class="multi">${f.nth} of ${f.of} at this company</i>` : ""}</div>`
    + `<h1>${esc(c.stream || "")}</h1></div><div class="stakebox">${ring(c.share)}<div class="stake"><b>${money(c.stake, c.currency)}</b>`
    + `<span>of revenue at stake${c.of ? `, out of ${esc(c.of)}` : ""}</span></div></div></div>`
    + `<div class="keyfacts">${calChip(c, false)}`
    + (c.who ? `<div class="chip"><span class="av">${esc(initials(c.who))}</span><div class="tx"><span class="clamp2">${esc(c.who)}</span><small>who to call</small></div></div>` : "")
    + `<a class="chip" href="#found/${esc(f.oid)}"><span class="ic">${ICONS.search}</span><div class="tx">How it found this<small>${Math.max(1, Math.round(f.el / 60))} minutes into the run</small></div></a></div>`
    + `<nav class="jump" id="jump"></nav><div class="doc2">`
    + sec("Why it matters", p(c.why_matters))
    + sec("What's at stake", scenarios(c) + (c.timing ? `<p style="margin-top:12px">${esc(c.timing)}</p>` : ""))
    + sec("Why now", p(c.why_now))
    + sec("Who to call", c.who ? `<div class="chip" style="margin:0"><span class="av">${esc(initials(c.who))}</span><div class="tx"><b>${esc(c.who)}</b>${c.role ? `<small>${esc(c.role)}</small>` : ""}</div></div>` : "")
    + sec("Opening message", c.opening ? `<blockquote class="opening">${esc(c.opening)}</blockquote>` : "")
    + sec("The event", `<p class="big">${esc(c.event || "")}</p>${p(c.event_description)}${p(c.event_timing)}`)
    + sec("What has to happen", p(c.condition))
    + sec("How its revenue depends on it", p(c.dependency))
    + sec("The figures behind it", (c.inputs || []).map(x => { const [v, u] = fmtVal(x);
        return `<div class="inp"><div class="iv"><b>${esc(v)}</b><span>${esc(x.name)}</span></div><div><span class="badge ${x.kind}">${KIND[x.kind] || x.kind}</span>`
          + `<span class="unit">${esc(u)}</span>${x.kind === "reported" ? quote(x.passage, x.url) : `<div>${esc(x.how || "")}</div>`}`
          + `${x.note ? `<small class="note">${esc(x.note)}</small>` : ""}</div></div>`; }).join(""))
    + sec("Evidence", (c.evidence || []).map(e => `<div class="ev"><b>${esc(e.supports || "")}</b>${quote(e.passage, e.url)}</div>`).join(""))
    + sec("Next step", p(c.next_action))
    + `</div><div class="casefoot"><a class="btn" href="#found/${esc(f.oid)}">How it found this</a>`
    + (c.deal ? `<a class="btn" href="${esc(c.deal)}" target="_blank" rel="noopener">Open in HubSpot</a>` : "") + `<span class="spacer"></span>`
    + (next ? `<a class="btn dark" href="#case/${esc(next.oid)}">Next: ${esc(next.c.company)} ›</a>` : `<a class="btn" href="#findings">All findings</a>`) + `</div>`;
  $("#jump").innerHTML = `<span class="k">Jump to</span>` + secs.map((h, k) => `<button data-jump="sec${k + 1}">${h}</button>`).join("");
}
function also() {
  $("#a-body").innerHTML = (M.aside.length ? `<h2>Looked at and set aside <span class="mute">· ${M.aside.length}</span></h2><div class="alist">${M.aside.map(t => `<div>${esc(t)}</div>`).join("")}</div>` : "")
    + (M.leads.length ? `<h2 style="margin-top:34px">Worth a look next <span class="mute">· ${M.leads.length}</span></h2><div class="alist">${M.leads.map(l =>
      `<div><b>${esc(l.company_or_event)}</b><br>${esc(l.reason)}</div>`).join("")}</div>` : "") || `<div class="empty">Nothing here for this run.</div>`;
}

// ---- the research, and its replay ----
const P = {pos: 0, playing: false, reveal: false, speed: 1, timer: 0};
const chapName = ch => ch.find ? ch.find.c.company : live() && ch.i === M.chapters.length - 1 ? "Still searching" : M.chapters.length === 1 ? "The whole run" : "Wrapping up";
const span = (a, b) => `${Math.floor(a / 60)}–${Math.max(Math.floor(a / 60) + 1, Math.ceil(b / 60))} min`;
function partOf(si) {
  const part = {says: 0, acts: 0, find: false};
  for (let k = M.first[si]; k <= P.pos; k++) { const b = M.beats[k]; if (b.k === "say") part.says++; else if (b.k === "act") part.acts++; else if (b.k === "find") part.find = true; }
  return part;
}
function actHTML(a, fresh) {
  const label = a.link ? `<a href="${esc(a.link)}" target="_blank" rel="noopener">${esc(a.label)}</a>` : esc(a.label);
  const det = (a.details || []).filter(Boolean).map(d => typeof d === "string" ? `<pre>${esc(d)}</pre>` :
    `<a href="${esc(d.url)}" target="_blank" rel="noopener">${esc(d.title)}</a>`).join("");
  return `<li class="act ${a.status || "pending"}${fresh ? " new" : ""}">${ICONS[a.icon] || ""}<div><div>${label}${a.meta ? `<span class="meta">${esc(a.meta)}</span>` : ""}</div>`
    + `${a.text ? `<div class="atext">${esc(a.text)}</div>` : ""}${det ? `<div class="det">${det}</div>` : ""}</div></li>`;
}
function chipsHTML(acts) {
  const counts = {}; acts.forEach(a => counts[a.icon] = (counts[a.icon] || 0) + 1); const blocked = acts.filter(a => a.status === "blocked").length;
  return Object.entries(counts).map(([k, n]) => `<span>${ICONS[k] || ""}${n} ${(NOUN[k] || [k, k])[n > 1 ? 1 : 0]}</span>`).join('<span class="sep">·</span>')
    + (blocked ? `<span class="sep">·</span><span class="warn">${blocked} blocked</span>` : "");
}
function stepHTML(s, part, b) {
  const says = part ? s.says.slice(0, part.says) : s.says, acts = part ? s.acts.slice(0, part.acts) : s.acts, finds = s.finds.filter(f => f.n && (!part || part.find));
  const auto = (part && acts.length > 0) || (live() && s.i === M.steps.length - 1), showActs = actsClosed.has(s.i) ? false : actsOpen.has(s.i) || auto;
  const rest = !s.rest ? "" : `<button class="link" data-rest="${s.i}">${opened.has(s.i) ? "Show less" : "Read the full thought"}</button>`;
  return `<article class="step${part ? " cur" : ""}${part && b.k === "think" ? " new" : ""}" id="s${s.i}"><div class="t">${clock(s.el)}</div><div>`
    + `<p class="lead">${s.lead ? inline(esc(s.lead)) : '<span class="mute">Getting started.</span>'}</p>${opened.has(s.i) && s.rest ? `<div class="rest">${paras(s.rest)}</div>` : ""}`
    + says.map(t => `<div class="say">${paras(t)}</div>`).join("")
    + (rest || acts.length ? `<div class="srow">${acts.length ? `<button class="chips" data-acts="${s.i}">${chipsHTML(acts)}<span class="chev">${showActs ? "Hide" : "Show"}</span></button>` : ""}${rest}</div>` : "")
    + (acts.length && showActs ? `<ul class="al">${acts.map((a, j) => actHTML(a, part && b.k === "act" && j === acts.length - 1)).join("")}</ul>` : "")
    + finds.map(f => `<a class="found${part && b.k === "find" ? " new" : ""}" href="#case/${esc(f.oid)}"><span class="num">${f.n}</span><span>Found <b>${esc(f.c.company)}</b> · `
      + `${esc(f.c.stream || "")}${f.c.stake != null ? ` · ${money(f.c.stake, f.c.currency)} at stake` : ""}</span><span class="go">Open the finding →</span></a>`).join("")
    + `</div></article>`;
}
function research() {
  $("#player").hidden = live() || !M.beats.length; $("#livebar").hidden = !live();
  if (!M.steps.length) { $("#chaps").innerHTML = ""; $("#chapter").innerHTML = `<div class="empty">${live() ? "Waiting for the researcher's first step…" : "This run has no steps to show."}</div>`; player(); return; }
  const ch = M.chapters[route.ci], [a, z] = ch.pages[route.pi], n = M.chapters.length, b = P.reveal ? M.beats[P.pos] : null;
  $("#chaps").innerHTML = M.chapters.map(c => `<a class="chap${c.i === route.ci ? " on" : ""}" href="#research/${c.i + 1}/1"><b>${c.find ? `<span class="num">${c.find.n}</span>` : ""}`
    + `${live() && !c.find && c.i === n - 1 ? '<span class="pulse"></span>' : ""}${esc(chapName(c))}</b><small>${span(c.t0, c.t1)} · ${plural(c.to - c.from + 1, "step", "steps")}</small></a>`).join("");
  const on = $("#chaps .on"), box = $("#chaps"); if (on && (on.offsetLeft < box.scrollLeft || on.offsetLeft + on.offsetWidth > box.scrollLeft + box.clientWidth)) box.scrollLeft = on.offsetLeft - 16;
  const f = ch.find, np = ch.pages.length;
  const sub = f ? `${esc(f.c.stream || "")}${f.c.stake != null ? ` · ${money(f.c.stake, f.c.currency)} at stake` : ""} · <a href="#case/${esc(f.oid)}">Open the finding</a>`
    : live() && ch.i === n - 1 ? "What it is working on now." : n === 1 ? "Everything it did, step by step." : "What it did after its last finding, through its final summary.";
  let html = ""; for (let s = a; s <= z; s++) { if (b && s > b.s) break; html += stepHTML(M.steps[s], b && s === b.s ? partOf(s) : null, b); }
  const prev = route.pi > 0 ? [route.ci, route.pi - 1] : route.ci > 0 ? [route.ci - 1, M.chapters[route.ci - 1].pages.length - 1] : null;
  const next = route.pi < np - 1 ? [route.ci, route.pi + 1] : route.ci < n - 1 ? [route.ci + 1, 0] : null, h = ([c, p]) => `#research/${c + 1}/${p + 1}`;
  $("#chapter").innerHTML = `<div class="chead"><span class="k">Chapter ${ch.i + 1} of ${n} · ${span(ch.t0, ch.t1)}${np > 1 ? ` · page ${route.pi + 1} of ${np}` : ""}</span>`
    + `<h2>${f ? `How it found ${esc(f.c.company)}` : esc(chapName(ch))}</h2><p class="sub">${sub}</p></div><div class="steps">${html}</div>`
    + (isLastPage() && !P.reveal && M.end && !live() ? `<div class="endcard"><b>End of the run.</b> In ${Math.max(1, Math.round(M.secs / 60))} minutes it found ${foundLine(M.found.map(x => x.c))}.`
      + `<div class="ctas"><a class="btn dark" href="#findings">See the findings</a><a class="btn" href="#play">${ICONS.play} Watch it again</a></div></div>` : "")
    + `<nav class="pager">${prev ? `<a class="btn" href="${h(prev)}">‹ Previous</a>` : "<span></span>"}<span class="pg">${np > 1 ? `Page ${route.pi + 1} of ${np} in this chapter` : ""}</span>`
    + (next ? `<a class="btn${P.reveal ? "" : " dark"}" href="${h(next)}">${next[0] !== route.ci ? `Next chapter: ${esc(chapName(M.chapters[next[0]]))} ›` : "Next page ›"}</a>` : "<span></span>") + `</nav>`;
  player();
}
function player() {
  if (live()) { $("#livebar").innerHTML = `<span class="pill running"><span class="pulse"></span>Live</span> New steps appear here as the researcher works. <span class="spacer"></span><b>${clock(elapsed())}</b>`; return; }
  if (!M.beats.length) return;
  const T = M.total || 1, at = P.reveal ? M.beats[P.pos].at : (M.beats[pageFirstBeat()] || {at: 0}).at, fr = 100 * at / T;
  $("#pp").innerHTML = P.playing ? ICONS.pause : ICONS.play; $("#pp").title = P.playing ? "Pause (space bar)" : "Play (space bar)";
  $("#fill").style.width = fr + "%"; $("#knob").style.left = fr + "%";
  $("#ptitle").textContent = P.playing ? "Playing" : P.reveal ? "Paused" : at > 0 ? "Play from here" : "Watch it unfold";
  const runT = P.reveal ? M.steps[M.beats[P.pos].s].el : (M.steps[M.chapters[route.ci].pages[route.pi][0]] || {el: 0}).el;
  $("#ptime").textContent = P.reveal || at > 0 ? `${clock(runT)} into the run · ${dur((T - at) / P.speed)} left` : `about ${dur(T / P.speed)}`;
  $$("#speed button").forEach(x => x.classList.toggle("on", +x.dataset.x === P.speed));
  $("#marks").innerHTML = M.chapters.slice(1).map(c => `<span class="cut" style="left:${100 * (M.beats[M.first[c.from]] || {at: T}).at / T}%"></span>`).join("")
    + M.beats.map((x, k) => x.k === "find" ? `<button class="flag" data-b="${k}" style="left:${100 * x.at / T}%" title="Found ${esc(x.f.c.company)}">${x.f.n}</button>` : "").join("");
}
function showBeat() {
  const b = M.beats[P.pos], loc = locate(b.s);
  if (route.v !== "research" || loc.ci !== route.ci || loc.pi !== route.pi) {
    route = {v: "research", ...loc}; history.replaceState(null, "", `#research/${loc.ci + 1}/${loc.pi + 1}`); render(); window.scrollTo({top: 0});
  } else research();
  const el = $("#s" + b.s); if (!el) return; const t = el.querySelector(".act.new,.found.new") || el, r = t.getBoundingClientRect();
  if (r.top < 170 || r.bottom > innerHeight - 30) t.scrollIntoView({block: "center", behavior: "smooth"});
}
function schedule(show) {
  clearTimeout(P.timer); if (show) showBeat(); if (!P.playing) { player(); return; }
  P.timer = setTimeout(() => { if (P.pos < M.beats.length - 1) { P.pos++; schedule(true); } else finish(); }, M.beats[P.pos].d * 1000 / P.speed);
}
function startAt(k) { P.pos = clampN(k, 0, M.beats.length - 1); P.reveal = true; P.playing = true; schedule(true); }
function pause() { P.playing = false; clearTimeout(P.timer); if (route.v === "research") player(); }
function toggle() {
  if (live() || !M.beats.length) return; if (P.playing) return pause();
  if (P.reveal) { P.playing = true; schedule(false); return; }
  startAt(route.v === "research" ? pageFirstBeat() : 0);
}
function finish() { P.playing = false; P.reveal = false; const last = M.chapters.length - 1; route = {v: "research", ci: last, pi: M.chapters[last].pages.length - 1};
  history.replaceState(null, "", `#research/${last + 1}/${route.pi + 1}`); render(); const e = $(".endcard"); if (e) e.scrollIntoView({block: "center", behavior: "smooth"}); }

// ---- wiring ----
$("#pp").onclick = toggle;
$("#speed").onclick = e => { const x = +(e.target.dataset || {}).x; if (!x) return; P.speed = x; if (P.playing) schedule(false); else player(); };
$("#scrub").onclick = e => { if (live() || !M.beats.length) return; const fl = e.target.closest(".flag"); let k;
  if (fl) k = +fl.dataset.b; else { const r = $("#scrub").getBoundingClientRect(), x = clampN((e.clientX - r.left) / r.width, 0, 1) * M.total;
    k = M.beats.findIndex(b => b.at + b.d > x); if (k < 0) k = M.beats.length - 1; k = M.first[M.beats[k].s]; }
  P.pos = k; P.reveal = true; schedule(true); };
$("#v-research").addEventListener("click", e => { const t = e.target.closest("[data-rest],[data-acts]"); if (!t) return; if (P.playing) pause();
  if (t.dataset.rest) { const i = +t.dataset.rest; opened.has(i) ? opened.delete(i) : opened.add(i); }
  else { const i = +t.dataset.acts, shown = t.querySelector(".chev").textContent === "Hide";
    if (shown) { actsOpen.delete(i); actsClosed.add(i); } else { actsClosed.delete(i); actsOpen.add(i); } }
  research(); });
$("#v-case").addEventListener("click", e => { const q = e.target.closest(".quote"); if (q) q.classList.toggle("full");
  const j = e.target.closest("[data-jump]"); if (j) document.getElementById(j.dataset.jump).scrollIntoView({behavior: "smooth", block: "start"}); });
$("#o-chart").onclick = e => { if (!M.steps.length) return; const r = e.currentTarget.getBoundingClientRect(), m = Math.floor((e.clientX - r.left) / r.width * M.bins.length);
  const s = M.steps.findIndex(x => x.el >= m * 60); location.hash = "#step/" + (s < 0 ? M.steps.length - 1 : s); };
document.addEventListener("keydown", e => { if (route.v !== "research" || e.metaKey || e.ctrlKey || e.altKey || /input|textarea|select/i.test(e.target.tagName)) return;
  if (e.code === "Space") { e.preventDefault(); toggle(); }
  else if (e.key === "ArrowRight" || e.key === "ArrowLeft") { const a = $(".pager a.btn:" + (e.key === "ArrowRight" ? "last-child" : "first-child")); if (a) location.hash = a.getAttribute("href"); } });
window.addEventListener("hashchange", () => go(parse()));
window.addEventListener("resize", () => { if (route.v === "overview") drawChart($("#o-chart"), M.bins, M.found.map(f => ({t: f.el, n: f.n})), Infinity, 190); });
const nearBottom = () => window.innerHeight + window.scrollY >= document.body.scrollHeight - 220;
window.addEventListener("scroll", () => { if (nearBottom()) $("#latest").hidden = true; });
$("#latest").onclick = () => { $("#latest").hidden = true; const last = M.chapters.length - 1; if (route.v !== "research" || !isLastPage()) location.hash = `#research/${last + 1}/${M.chapters[last].pages.length}`;
  else window.scrollTo({top: document.body.scrollHeight, behavior: "smooth"}); };
setInterval(() => { if (!live()) return; pill(); if (route.v === "research") player(); }, 1000);
async function poll() {
  try {
    const first = after === 0, j = await (await fetch(`/runs/${RUN}/ops?after=${after}`)).json();
    ingest(j.ops); after = j.next; status = j.status || {}; Object.assign(details, j.cases || {});
    if (j.ops.length || first) {
      const wasLast = route.v === "research" && M.steps.length && isLastPage(), follow = wasLast && nearBottom(); M = build();
      if (first) go(parse());
      else if (wasLast) { const last = M.chapters.length - 1; route = {v: "research", ci: last, pi: M.chapters[last].pages.length - 1}; render();
        if (follow) window.scrollTo({top: document.body.scrollHeight, behavior: "smooth"}); else $("#latest").hidden = false; }
      else render();
    } else pill();
    if (status.state === "running" || status.state === "unknown") setTimeout(poll, 2500); else { M = build(); render(); }
  } catch (e) { setTimeout(poll, 5000); }
}
if (DATA) { ingest(DATA.ops); status = DATA.status || {}; details = DATA.cases || {}; M = build(); go(parse()); }
else { M = build(); go(parse()); poll(); }
</script></body></html>"""

PAGE_STEPS = 12  # at most this many reasoning steps to a page of the research view

# How long the replay holds each thing at 1x, in seconds: a thought's headline gets base + per-word time
# within [min, max]; a message from the researcher likewise; each search, read or calculation; each finding.
PACE = {"think": [0.8, 0.075, 1.8, 3.4], "say": [1.0, 0.03, 2.2, 4.5], "act": 0.35, "find": 3.0}
