"""The run page: one research run for a viewer, live or finished.

watch.py serves it live and export-run writes it as one self-contained file. It is organised as views with
their own addresses rather than one long scroll:
  #overview      project overview with company findings, research activity and other leads
  #activity      activity chart and playback controls on the overview
  #case/ID       one finding in full: the event, the outcomes, the figures behind them with their provenance,
                 the evidence, who to call and the opening message
  #research      a continuous, time-stamped transcript with replay and links to saved findings
  #also          follow-up leads, and conclusions that concern no case, on the overview
  #found/ID      the step where a finding was saved; #step/N a step by number; #play replays from the start
"""

FAVICON = ("data:image/svg+xml," + "%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' "
           "rx='8' fill='%23111827'/%3E%3Cpath d='M7 20l5-6 4 4 9-10' stroke='%2334d399' stroke-width='3' fill='none' "
           "stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E")

FOUND_JS = r"""function groupByCompany(cases, getCase = x => x) {
  const groups = new Map();
  for (const item of cases) {
    const c = getCase(item), key = c.company_id || c.company || c.oid || item;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(item);
  }
  return Array.from(groups.values());
}
function money(amount, currency) {
  if (amount == null) return "";
  const value = Math.abs(amount).toLocaleString("en-US", {notation:"compact", maximumFractionDigits:Math.abs(amount) >= 1e9 ? 2 : 1});
  return (amount < 0 ? "−" : "") + (currency && currency !== "USD" ? currency + " " : "$") + value;
}
function leadSources(urls) {
  const links = (urls || []).filter(url => /^https?:\/\//i.test(url)).map(url => {
    try { return `<a href="${esc(url)}" target="_blank" rel="noopener">${esc(new URL(url).hostname.replace(/^www\./, ""))} ↗</a>`; }
    catch { return ""; }
  }).filter(Boolean);
  return links.length ? `<small class="lead-sources">${links.join(" · ")}</small>` : "";
}
function periodLabel(c) {
  const start = c.period_start, end = c.period_end;
  if (!start || !end) return c.period || "";
  const y = start.slice(0, 4);
  if (start === y + "-01-01" && end === y + "-12-31") return y;
  if (start === y + "-07-01" && end === y + "-12-31") return "Jul–Dec " + y;
  const fmt = d => new Date(d + "T12:00:00").toLocaleDateString("en-US", {month:"short", day:"numeric", year:"numeric"});
  return start === end ? fmt(start) : fmt(start) + " – " + fmt(end);
}"""

# The minute-by-minute activity chart, shared by the run page and the guide. bins: [{search, read, calc}] per
# minute; flags: [{t: seconds, n}] for each exposure found; upto: seconds drawn so far (Infinity for all).
CHART_JS = r"""function drawChart(svg, bins, flags, upto, H, visibleBins) {
  const W = Math.round(svg.getBoundingClientRect().width || 1000), LEFT = 26; svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  const N = Math.max(1, bins.length), TOP = 30, BASE = H - 22, BW = (W - LEFT) / N;
  svg.dataset.plotLeft = LEFT;
  const maxv = Math.max(1, ...bins.map(b => b.search + b.read + b.calc)), y = v => (BASE - TOP) * v / maxv;
  const normal = N <= 40 ? 5 : N <= 90 ? 10 : N <= 150 ? 15 : 30;
  const every = normal * Math.max(1, Math.ceil(50 / (BW * normal)));
  let s = "";
  for (const count of new Set([0, Math.round(maxv / 2), maxv])) {
    const cy = BASE - y(count);
    s += `<line x1="${LEFT}" x2="${W}" y1="${cy}" y2="${cy}" stroke="#eef0f3"/><text x="${LEFT - 5}" y="${cy + 3}" fill="#52647a" font-size="11" text-anchor="end">${count}</text>`;
  }
  for (let m = 0; m < N; m += every) { const x = LEFT + m * BW; if (m && x > W - 40) break; s += `<line x1="${x}" x2="${x}" y1="${TOP - 6}" y2="${BASE}" stroke="#eef0f3"/>`
    + `<text x="${x + 3}" y="${H - 6}" font-size="12" fill="#52647a">${m} min</text>`; }
  (visibleBins || bins).forEach((b, i) => { if (i * 60 > upto) return; const f = visibleBins ? 1 : Math.min(1, (upto - i * 60) / 60), x = LEFT + i * BW + BW * .14, w = Math.max(1, BW * .72);
    let h0 = 0; [["search", "#2563eb"], ["read", "#94a3b8"], ["calc", "#7c3aed"]].forEach(([k, col]) => { const h = y(b[k]) * f; if (h > 0)
      s += `<rect x="${x.toFixed(1)}" y="${(BASE - h0 - h).toFixed(1)}" width="${w.toFixed(1)}" height="${h.toFixed(1)}" rx="2" fill="${col}"/>`; h0 += h; }); });
  let lastCx = -99;
  flags.forEach(fl => { if (fl.t > upto) return; const x = Math.min(W - 9, Math.max(9, LEFT + fl.t / 60 * BW)), cx = fl.n == null ? x : Math.min(W - 9, Math.max(x, lastCx + 19)), cy = TOP - 10; lastCx = cx;
    s += `<g>${fl.label ? "<title>" + esc(fl.label) + "</title>" : ""}<line x1="${x}" x2="${x}" y1="${TOP - 2}" y2="${BASE}" stroke="#16a34a" stroke-width="2" stroke-dasharray="3 3"/>`
      + `<circle cx="${cx}" cy="${cy}" r="${fl.n == null ? 4.5 : 8.5}" fill="#15803d" stroke="#fff" stroke-width="1.5"/>`
      + (fl.n == null ? "" : `<text x="${cx}" y="${cy + 3.5}" font-size="10" fill="#fff" text-anchor="middle" font-weight="700">${fl.n}</text>`) + "</g>"; });
  s += `<line x1="${LEFT}" x2="${W}" y1="${BASE}" y2="${BASE}" stroke="#e2e8f0"/>`;
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
:root{--bg:#f7f8fa;--card:#fff;--line:#e7e9ee;--line2:#eff1f4;--ink:#0f172a;--soft:#334155;--mute:#64748b;--faint:#52647a;
--blue:#2563eb;--bluebg:#eef3ff;--green:#16a34a;--greenink:#15803d;--greenbg:#ecfdf3;--greenline:#bbf7d0;--violet:#7c3aed;
--amber:#b45309;--amberbg:#fff7ed;--red:#dc2626}
*{box-sizing:border-box}[hidden]{display:none!important}
html,body{margin:0;background:var(--bg);color:var(--ink);font:15.5px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Inter,system-ui,sans-serif;
-webkit-font-smoothing:antialiased}a{color:var(--blue);text-decoration:none}a:hover{text-decoration:underline}button{font-family:inherit}
:focus-visible{outline:3px solid var(--blue);outline-offset:3px}.tabs a:focus-visible{outline-offset:-3px}
.top{position:sticky;top:0;z-index:20;background:rgba(247,248,250,.92);backdrop-filter:saturate(1.6) blur(12px);border-bottom:1px solid var(--line)}
.top .in{max-width:1040px;margin:0 auto;padding:0 28px;height:58px;display:flex;align-items:center;gap:16px}
.brand{font-weight:700;font-size:14px;color:var(--ink);display:flex;align-items:center;gap:9px;white-space:nowrap}
.brand img{width:22px;height:22px}.brand:hover{text-decoration:none}.divider{width:1px;height:22px;background:var(--line);flex:0 0 1px}
.tabs{display:flex;gap:2px;overflow-x:auto;scrollbar-width:none;min-width:0}.tabs::-webkit-scrollbar{display:none}
.tabs a{padding:7px 12px;border-radius:9px;color:var(--mute);font-weight:600;font-size:14px;white-space:nowrap}
.tabs a:hover{background:#eceff3;color:var(--ink);text-decoration:none}.tabs a.on{background:var(--ink);color:#fff}
.spacer{flex:1}
.pill{display:inline-flex;align-items:center;gap:6px;font-size:12px;font-weight:650;padding:3px 10px;border-radius:99px;white-space:nowrap}
.pill.running{background:#dbeafe;color:#1d4ed8}.pill.failed{background:#fee2e2;color:#b91c1c}.pill.finished{background:#dcfce7;color:var(--greenink)}
.pulse{display:inline-block;width:7px;height:7px;border-radius:50%;background:#2563eb;animation:pulse 1.4s infinite}
@keyframes pulse{0%{box-shadow:0 0 0 0 rgba(37,99,235,.55)}100%{box-shadow:0 0 0 9px rgba(37,99,235,0)}}
.btn{display:inline-flex;align-items:center;gap:8px;border:1px solid var(--line);background:var(--card);color:var(--ink);border-radius:99px;
padding:10px 17px;font-weight:600;font-size:14px;line-height:1;cursor:pointer;white-space:nowrap}
.btn:hover{border-color:#cbd5e1;text-decoration:none}.btn.dark{background:var(--ink);border-color:var(--ink);color:#fff}
.btn.sm{padding:7px 13px;font-size:13px}.btn svg{width:13px;height:13px}
.wrap{max-width:1040px;margin:0 auto;padding:38px 28px 70px}
.view h1[tabindex="-1"]:focus{outline:none}.view{animation:fade .25s ease}@keyframes fade{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:none}}
h1{font-size:36px;line-height:1.12;letter-spacing:-.03em;margin:0 0 12px;font-weight:790}
h2{font-size:24px;letter-spacing:-.02em;line-height:1.25;margin:0 0 6px;font-weight:760}
.lede{font-size:17.5px;color:var(--soft);max-width:760px;margin:0 0 20px}.sub{color:var(--mute);margin:0 0 20px;font-size:15px;max-width:800px}
.sub b{color:var(--ink)}.mute{color:var(--mute)}
.num{display:inline-grid;place-items:center;flex:0 0 auto;width:24px;height:24px;border-radius:50%;background:var(--greenink);color:#fff;font-size:12px;font-weight:760;font-style:normal}

.event-label{color:var(--soft);font-size:14px}.event-label span{color:var(--mute)}
.case-actions{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin-top:18px}
.case-actions a{font-size:14px}.case-actions form{display:flex;align-items:center;gap:6px;margin:0}
.case-actions label,.case-actions select{font:inherit;font-size:13px;color:var(--soft)}
.case-actions select{max-width:100%;background:white;border:1px solid var(--line);border-radius:6px;padding:5px}
.case-actions .memo-actions{display:inline-flex;gap:8px;margin-left:auto}.case-actions .memo-actions .btn{font-size:13px}
.case-actions .memo-actions .btn.dark{color:#fff}.case-actions .memo-actions .btn:hover{text-decoration:none}
.crumbs{display:flex;align-items:center;gap:10px;margin-bottom:24px;font-size:14px;color:var(--mute)}
.crumbs a.back{font-weight:650}
.casehead{display:flex;gap:24px;align-items:flex-start;justify-content:space-between}
.casehead .eyebrow{display:flex;align-items:center;gap:9px;font-weight:650;color:var(--soft);font-size:15.5px;margin-bottom:8px;flex-wrap:wrap}
.casehead h1{font-size:32px;margin:0}
.stake b{display:block;font-size:34px;font-weight:800;letter-spacing:-.03em;line-height:1.1}.stake span{color:var(--mute);font-size:13px;display:block;max-width:380px}
.doc2{display:grid;grid-template-columns:190px minmax(0,1fr);gap:0 28px;margin-top:18px}.doc2>h3{scroll-margin-top:80px}
.doc2>h3{margin:0;padding:18px 0;border-top:1px solid var(--line);font-size:12px;text-transform:uppercase;letter-spacing:.08em;color:var(--mute);font-weight:750}
.doc2>div{padding:16px 0 22px;border-top:1px solid var(--line);color:var(--soft);font-size:15px;min-width:0}
.doc2>div p{margin:0 0 10px}.doc2>div p:last-child{margin-bottom:0}.doc2 summary{cursor:pointer;color:var(--blue);font-weight:600}.doc2 details[open] summary{margin-bottom:8px}.doc2 .big{color:var(--ink);font-weight:600;font-size:16px}
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
.quote{display:block;margin:6px 0 0;padding:8px 12px;background:#f8fafc;
border-left:3px solid #cbd5e1;border-radius:0 8px 8px 0;font-size:13.5px;color:var(--soft);overflow-wrap:anywhere}
.src{display:inline-block;font-size:12.5px;color:var(--mute);margin-top:5px}
.ev{padding:12px 0;border-top:1px solid var(--line2)}.ev:first-child{border-top:0;padding-top:2px}.ev b{color:var(--ink);font-size:14px;font-weight:650}
blockquote.opening{margin:0;padding:14px 18px;background:#f8fafc;border-left:3px solid var(--blue);border-radius:0 12px 12px 0;color:var(--ink);font-size:15px;white-space:pre-wrap}
.player{position:sticky;top:calc(var(--shell-height,56px) + 6px);z-index:15;display:flex;align-items:center;gap:16px;background:var(--card);border:1px solid var(--line);
border-radius:16px;padding:10px 16px 10px 10px;box-shadow:0 6px 22px rgba(15,23,42,.07)}
.pp{width:44px;height:44px;flex:0 0 44px;border-radius:50%;border:0;background:var(--ink);color:#fff;display:grid;place-items:center;cursor:pointer}
.pp svg{width:17px;height:17px}.pp:hover{background:#1e293b}
.pmeta{min-width:160px}.pmeta b{font-size:14px}.pmeta span{display:block}.pmeta button{border:0;background:none;color:var(--blue);font-size:12px;font-weight:600;padding:0;margin-left:8px;cursor:pointer}.pmeta span{font-size:12.5px;color:var(--mute);font-variant-numeric:tabular-nums}
.scrub{flex:1;position:relative;height:44px;cursor:pointer;min-width:120px}
.track{position:absolute;inset:0;display:flex;align-items:center;border-radius:5px;outline-offset:2px}
.rail{width:100%;height:6px;border-radius:99px;background:#e8ebf0;overflow:hidden}.fill{height:100%;background:var(--ink);width:0}
.knob{position:absolute;top:50%;width:14px;height:14px;border-radius:50%;background:#fff;border:3px solid var(--ink);transform:translate(-50%,-50%);pointer-events:none}
.speed{display:flex;background:#f1f3f6;border-radius:99px;padding:3px}
.speed button{border:0;background:none;padding:5px 10px;border-radius:99px;font-weight:650;font-size:12.5px;color:var(--mute);cursor:pointer}
.speed button.on{background:var(--card);color:var(--ink);box-shadow:0 1px 3px rgba(15,23,42,.12)}
.livebar{padding:12px 18px;color:var(--soft);font-size:14px}.livebar b{font-variant-numeric:tabular-nums}
.loghead{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-bottom:14px}
.loghead h1{margin:0}.loghead select{width:9.5rem;max-width:100%;font:inherit;font-size:13px;color:var(--soft);border:1px solid var(--line);border-radius:8px;padding:6px 8px;background:var(--card)}
#transcript,#log-empty{margin-top:16px;overflow-anchor:none}
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
.case-actions .btn{white-space:normal}
.empty{color:var(--mute);background:var(--card);border:1px dashed #d5d9e0;border-radius:14px;padding:22px;text-align:center}
.alist{display:flex;flex-direction:column;gap:10px;margin:10px 0 0}
.alist div{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:13px 17px;font-size:14.5px;color:var(--soft)}.alist b{color:var(--ink)}.lead-sources{display:block;margin-top:6px}
.lead,.rest,.say,.act,.alist div,.doc2>div{overflow-wrap:anywhere}
#latest{position:fixed;bottom:24px;left:50%;transform:translateX(-50%);background:var(--ink);color:#fff;border:0;border-radius:99px;z-index:30;
padding:10px 18px;font-weight:600;font-size:13px;line-height:1;cursor:pointer;box-shadow:0 8px 24px rgba(15,23,42,.22)}
.runs{max-width:920px;margin:0 auto;padding:28px 28px 80px}.ttl{font-weight:700}.top .sub{margin:0;font-size:13px}
.run{display:flex;align-items:center;gap:18px;background:var(--card);border:1px solid var(--line);border-radius:15px;padding:15px 20px;margin-bottom:10px;color:var(--ink)}
.run:hover{border-color:#cbd5e1;text-decoration:none}.run .what{flex:1;min-width:0}.run .what b{font-weight:650}
.run .what div{color:var(--mute);font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.run .n{font-size:13px;color:var(--mute);text-align:right;min-width:150px;font-variant-numeric:tabular-nums}
@media(max-width:820px){
.doc2{grid-template-columns:1fr}.doc2>h3{padding:18px 0 4px}.doc2>div{border-top:0;padding-top:4px}.casehead{flex-direction:column;gap:14px}
.sc{grid-template-columns:1fr;gap:6px}.sc .sv{text-align:left}.inp{grid-template-columns:1fr;gap:4px}}
@media(max-width:640px){.top .in,.wrap{padding-left:16px;padding-right:16px}.brand span,.top .about{display:none}h1{font-size:28px}
.top{position:static}.top .in{flex-wrap:wrap;height:auto;padding-top:8px;padding-bottom:8px;gap:6px 12px}
.top .brand span{display:inline}.top .divider,.top .spacer{display:none}.top .pill{margin-left:auto}
.top .tabs{order:1;flex:0 0 100%;flex-wrap:wrap;gap:3px}.tabs a{padding:5px 6px;font-size:13px}
.case-actions{gap:10px}
.crumbs{flex-wrap:wrap}
.found{flex-wrap:wrap}.found .go{margin-left:0}.found>span:nth-child(2){flex:1;min-width:0}
.step{grid-template-columns:1fr;gap:2px;padding:14px 16px}.player{flex-wrap:wrap;gap:10px}.scrub{order:3;flex-basis:100%}.pmeta{flex:1;min-width:0}.doc2>h3{scroll-margin-top:10px}}
@media(prefers-reduced-motion:reduce){.view,.step,.act,.found,.pulse{animation:none!important}}
"""

SHELL_STYLE = r"""
.run-nav{background:var(--bg)}
.run-nav .in{max-width:1116px;height:54px;gap:19px}
.run-nav .brand{margin-right:auto}.run-nav .tabs{align-self:stretch;align-items:stretch;gap:18px;overflow:visible}
.run-nav .tabs a{display:inline-flex;align-items:center;position:relative;padding:0 1px;color:var(--mute);background:none;border-radius:0}
.run-nav .tabs a:hover,.run-nav .tabs .on{color:var(--ink)}
.run-nav .tabs .on:after{content:"";position:absolute;bottom:-1px;left:0;right:0;height:2px;background:var(--blue)}
.run-nav .project-link{font-size:13px;font-weight:600;color:var(--mute);white-space:nowrap}
.run-nav .project-link:hover{color:var(--ink)}
.run-nav .project-credit{border-left:1px solid var(--line);padding-left:18px;color:var(--mute);font-size:12px;line-height:1.4;white-space:nowrap}
.project-credit strong{font-weight:600;color:var(--soft)}
.project-credit:hover strong{text-decoration:underline;text-underline-offset:3px}
.run-nav .github-icon{display:none;width:20px;height:20px;fill:currentColor}
.run-nav + .wrap{max-width:1116px;padding-top:28px}
.run-nav + .wrap .view{animation:none}
.demo-header{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px 12px;padding:13px 18px;background:var(--card);border:1px solid var(--line);border-bottom:0;border-radius:12px 12px 0 0}
#section-findings .demo-header h2{margin:0;font-size:19px;line-height:1.3;letter-spacing:-.025em}
#section-findings:has(.demo-header) .prospect-sheet{border-radius:0 0 12px 12px}
.demo-play{display:inline-flex;align-items:center;gap:7px;min-height:34px;padding:0 12px;border:0;border-radius:7px;background:var(--ink);color:#fff;font:inherit;font-size:13px;font-weight:600;cursor:pointer}
.demo-play:hover{background:var(--soft);text-decoration:none}
.demo-play svg{width:12px;height:12px;fill:currentColor}
@media(max-width:360px){.demo-play{gap:5px;padding:0 8px;font-size:12px}}
@media(max-width:600px){
  .demo-header{padding:10px 14px}
}
#overview,#section-findings,#activity,#also,#hubspot,.doc2>h3{scroll-margin-top:calc(var(--shell-height,54px) + 16px)}
#also{margin-top:30px}
#also>h2{margin:0 0 12px;font-size:21px}
.lead-groups{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:26px}
.lead-group{min-width:0}.lead-group h3{margin:0 0 6px;color:var(--soft);font-size:14px;font-weight:650}
.lead-group ul{margin:0;padding:0;list-style:none;border-top:1px solid var(--line)}
.lead-group li{border-bottom:1px solid var(--line);overflow-wrap:anywhere}
.lead-group summary{cursor:pointer;color:var(--soft);font-size:14px;line-height:1.45;padding:8px 0}
.lead-group summary::marker{color:var(--faint);font-size:11px}
.lead-group summary:hover,.lead-group details[open] summary{color:var(--ink)}
.lead-group p{margin:0;padding:0 0 10px;color:var(--soft);font-size:14px}
.lead-group .lead-note{padding:8px 0}.lead-group .lead-sources{display:block;margin-top:5px}
@media(max-width:700px){
  .run-nav{position:sticky}.run-nav .in{height:auto;min-height:47px;padding:0 16px;flex-wrap:wrap;gap:0 14px}
  .run-nav .brand span{display:inline}.run-nav .tabs{order:0;flex:0 0 auto;flex-wrap:nowrap;gap:12px}
  .run-nav .tabs a{padding:12px 0}.run-nav .pill{margin-left:0}
  .run-nav + .wrap{padding-top:22px}
  .lead-groups{grid-template-columns:1fr;gap:18px}
}
@media(max-width:560px){
  .run-nav .github-icon{display:block}.run-nav .github-label{display:none}
  .run-nav .source-link{display:grid;place-items:center;min-width:25px;min-height:40px}
  .run-nav:not(:has(.operator-home,.pill:not([hidden]))) .in{display:grid;grid-template-columns:minmax(0,1fr) auto auto;column-gap:12px}
  .run-nav:has(.operator-home) .tabs{order:1;flex-basis:100%}
  .run-nav .in:has(.project-credit):not(:has(.operator-home,.pill:not([hidden]))){grid-template-rows:auto auto;min-height:54px;row-gap:2px}
  .run-nav .in:has(.project-credit) .brand{grid-area:1/1;align-self:end;line-height:1.25}
  .run-nav .project-credit{grid-area:2/1;align-self:start;justify-self:start;padding:0;border:0;font-size:11px;line-height:1.25}
  .run-nav .in:has(.project-credit) .tabs{grid-area:1/2/3/3}
  .run-nav .in:has(.project-credit) .source-link{grid-area:1/3/3/4}
}
@media(max-width:375px){
  .run-nav .in{gap:0 10px;padding:0 12px}.run-nav .brand{gap:6px;font-size:12px}
  .run-nav .brand img{width:17px;height:17px}.run-nav .tabs{gap:10px}
  .run-nav .tabs a,.run-nav .project-link{font-size:12px}
}
@media(max-width:360px){
  .run-nav .in:has(.project-credit) .brand{align-self:center}
  .run-nav .in:has(.project-credit) .project-credit{grid-area:1/2;align-self:center}
  .run-nav .in:has(.project-credit) .source-link{grid-area:1/3;min-height:34px}
  .run-nav .in:has(.project-credit) .tabs{grid-area:2/1/3/-1;gap:15px}
  .run-nav .in:has(.project-credit) .tabs a{padding:1px 0 7px}
}
@media(max-width:340px){.run-nav .brand img{display:none}}
"""

LEADS_HTML = """<section id="also" aria-labelledby="leads-title" hidden>
  <h2 id="leads-title">Other leads</h2><div class="lead-groups" id="a-body"></div>
</section>"""

PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__ · Distress Signals</title>__SHAREMETA__<link rel="icon" href="__FAVICON__"><style>__STYLE__</style></head><body>
<header class="top run-nav"><div class="in"><a class="brand" href="#overview" aria-label="Distress Signals overview"><img src="__FAVICON__" alt=""><span>Distress Signals</span></a>
  <nav class="tabs" id="tabs" aria-label="Run navigation">
    <a href="#overview">Overview</a><a href="#research">Research log</a>
  </nav><span class="pill" id="pill" hidden></span>
  __RUNINDEX____CREDIT__<a class="project-link source-link" href="__REPO__" aria-label="Source on GitHub" title="Source on GitHub">
    <svg class="github-icon" viewBox="0 0 16 16" aria-hidden="true"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82A7.45 7.45 0 018 3.86c.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0016 8c0-4.42-3.58-8-8-8Z"/></svg><span class="github-label">GitHub</span></a></div></header>
<main class="wrap">
__FINDINGSHTML__
<section class="view" id="v-case" hidden></section>
<section class="view" id="v-research" hidden>
  <div class="loghead"><h1>Research log</h1><select id="saved-jump" aria-label="Jump to a saved case" hidden></select></div>
  <div class="player" id="player"><button class="pp" id="pp" aria-label="Play replay"></button>
    <div class="pmeta"><b id="ptitle"></b><button id="full-log" hidden>Show full log</button><span id="ptime"></span></div>
    <div class="scrub" id="scrub"><div class="track" role="slider" tabindex="0" aria-label="Research step" aria-valuemin="1">
      <div class="rail"><div class="fill" id="fill"></div></div><div class="knob" id="knob"></div></div></div>
    <div class="speed" id="speed" role="group" aria-label="Replay speed"><button data-x="1">1×</button><button data-x="2">2×</button><button data-x="4">4×</button></div></div>
  <div class="player livebar" id="livebar" hidden></div>
  <div id="transcript" class="steps" aria-label="Transcript"></div><div id="log-empty" class="empty" hidden></div>
</section>
</main>
<button id="latest" hidden>Follow replay ↓</button>
<script>
__FOUNDJS__
__CHARTJS__
const RUN = "__RUN__", ICONS = __ICONS__, PAGE_STEPS = __PAGESTEPS__, PACE = __PACE__, DATA = __DATA__;
const $ = (s, r = document) => r.querySelector(s), $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const inline = s => s.replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>").replace(/`([^`]+)`/g, "$1");
const paras = s => esc(s).split(/\n{2,}/).filter(p => p.trim()).map(p => "<p>" + inline(p).replace(/^\s*[-•*] /gm, "• ").replace(/\n/g, "<br>") + "</p>").join("");
const pct = x => x == null ? "" : x.toLocaleString("en-US", {style:"percent", maximumFractionDigits:1});
const basis = c => c.share != null && c.of ? `${pct(c.share)} of ${c.of}` : "";
const shortfallLabel = c => ["Largest modeled shortfall", periodLabel(c)].filter(Boolean).join(" · ");
const emptyFindings = () => live() ? "No findings recorded yet." : "No active findings recorded for this run.";
const day = d => new Date(d + "T12:00:00");
const longDate = d => d ? day(d).toLocaleDateString("en-US", {month: "long", day: "numeric", year: "numeric"}) : "";
const clock = s => `${Math.floor((s || 0) / 60)}:${String(Math.floor((s || 0) % 60)).padStart(2, "0")}`;
const words = s => (String(s || "").match(/\S+/g) || []).length;
const clampN = (x, a, b) => Math.max(a, Math.min(b, x));
const host = u => { try { return new URL(u).hostname.replace(/^www\./, ""); } catch (e) { return "source"; } };
const NOUN = {search:["search","searches"],read:["document","documents"],web:["web page","web pages"],calc:["query or calculation","queries and calculations"],
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
  const steps = [], base = {}, seen = new Set(), finds = [], aside = [], leads = []; let cur = null;
  const step = (t, el) => { const [lead, rest] = split(t); cur = {i: steps.length, el: el ?? (cur ? cur.el : 0) ?? 0, lead, rest, says: [], acts: [], finds: [], events: []}; steps.push(cur); };
  for (const it of items) {
    if (it.k === "think") step(it.t, it.el);
    else if (it.k === "say" || it.k === "note") { if (!cur) step("", it.el); cur.events.push({k: "say", j: cur.says.length, el: it.el}); cur.says.push(it.t); }
    else if (it.k === "act") { if (!cur) step("", it.el); cur.events.push({k: "act", j: cur.acts.length, el: it.el}); cur.acts.push(it);
      if (it.case) { base[it.case.oid] = {...(base[it.case.oid] || {}), ...it.case};
        if (!seen.has(it.case.oid)) { seen.add(it.case.oid); const f = {oid: it.case.oid, step: cur.i, el: it.el ?? cur.el}; cur.finds.push(f); finds.push(f); cur.events.push({k: "find", f, el: f.el}); } }
      if (it.tool === "record_finding" && it.finding === "conclusion" && it.no_case && it.status === "ok" && it.text) aside.push(it.text); }
    else if (it.k === "final") leads.push(...(it.leads || []));
  }
  const caseOf = oid => ({...(base[oid] || {}), ...(details[oid] || {}), oid});
  finds.forEach(f => f.c = caseOf(f.oid));
  const found = finds.filter(f => f.c.status !== "withdrawn"); found.forEach((f, i) => f.n = i + 1);

  const beats = [], first = []; let acc = 0;
  const beat = (s, k, d, x = {}) => { beats.push({s: s.i, ...x, el: x.el ?? s.el, k, d, at: acc}); acc += d; };
  steps.forEach(s => {
    first[s.i] = beats.length;
    beat(s, "think", clampN(PACE.think[0] + PACE.think[1] * words(s.lead), PACE.think[2], PACE.think[3]));
    for (const e of s.events) {
      if (e.k === "say") beat(s, e.k, clampN(PACE.say[0] + PACE.say[1] * words(s.says[e.j]), PACE.say[2], PACE.say[3]), e);
      else if (e.k === "act") beat(s, e.k, PACE.act, e);
      else if (e.f.n) beat(s, e.k, PACE.find, e);
    }
  });
  const secs = status.duration_ms ? status.duration_ms / 1000 : Math.max(60, ...items.map(i => i.el || 0));
  const minutes = Math.max(1, Math.ceil(secs / 60)), bins = Array.from({length: minutes}, () => ({search: 0, read: 0, calc: 0}));
  items.forEach(it => {
    const k = activityKind(it);
    if (k) bins[Math.min(minutes - 1, Math.floor((it.el || 0) / 60))][k]++; });
  return {steps, finds, found, beats, first, total: acc, aside: DATA?.lead_review?.aside ?? aside,
          leads: DATA?.lead_review?.leads ?? leads, bins, secs};
}
const live = () => status.state === "running" && !DATA;
const elapsed = () => status.duration_ms ? status.duration_ms / 1000 : live() && status.t0 ? (Date.now() - Date.parse(status.t0)) / 1000 : M.secs;
// Translate bookmarks made by earlier exports that split the log at saves and every twelve entries.
function bookmarkedStep(section, page) {
  const ends = [...new Set(M.found.map(f => f.step))];
  const last = M.steps.length - 1;
  if (!ends.length || ends[ends.length - 1] !== last) ends.push(last);
  const i = clampN(section, 0, ends.length - 1), start = i ? ends[i - 1] + 1 : 0;
  const length = ends[i] - start + 1, pages = Math.max(1, Math.ceil(length / PAGE_STEPS));
  return start + clampN(page, 0, pages - 1) * Math.ceil(length / pages);
}

// ---- views ----
let route = {v: "findings"};
const opened = new Set(), actsOpen = new Set(), actsClosed = new Set();
function parse() {
  const [v, encoded, b] = location.hash.slice(1).split("/");
  let a;
  try { a = decodeURIComponent(encoded || ""); }
  catch { return {v:"findings"}; }
  if (v === "case" && a) return {v: "case", oid: a};
  if (v === "found" && a) return {v: "research", find: a};
  if (v === "step" && /^\d+$/.test(a || "")) return {v: "research", step: +a};
  if (v === "play") return {v: "research", play: true};
  if (v === "research") return {v, step: /^\d+$/.test(a || "") ? bookmarkedStep(+a - 1, /^\d+$/.test(b || "") ? +b - 1 : 0) : null};
  const sections = new Map([
    ["activity", "activity"], ["run", "activity"], ["also", "also"], ["hubspot", "hubspot"],
    ["found", "section-findings"], ["section-findings", "section-findings"]
  ]);
  return {v:"findings", section:sections.get(v)};
}
function go(r, savedReplay) {
  if (P.playing) pause();
  route = r;
  $("#latest").hidden = true;
  $("#transcript .flash")?.classList.remove("flash");
  if (r.v !== "research") { render(); return; }

  if (savedReplay && !live() && M.beats.length) {
    const {read, expanded, collapsed, ...transport} = savedReplay;
    for (const [set, values] of [[opened, read], [actsOpen, expanded], [actsClosed, collapsed]]) {
      set.clear(); for (const value of values || []) set.add(value);
    }
    logModel = null;
    Object.assign(P, transport, {pos: clampN(transport.pos, 0, M.beats.length - 1)});
    render(); followLatest(P.follow); return;
  }
  P.pos = 0; P.finished = false; P.remaining = null; P.reveal = false; P.follow = true;
  const finding = r.find && M.finds.find(f => f.oid === r.find);
  const requested = finding ? finding.step : r.step;
  const step = requested != null && M.steps.length ? clampN(requested, 0, M.steps.length - 1) : null;
  if (step != null) {
    const savedBeat = finding ? M.beats.findIndex(b => b.f === finding) : -1;
    P.pos = savedBeat >= 0 ? savedBeat : M.first[step]; P.follow = false;
    if (finding) { actsClosed.delete(step); actsOpen.add(step); }
  }
  if (r.play && !live() && M.beats.length) {
    P.reveal = true;
    render(); window.scrollTo({top: 0}); toggle(); return;
  }
  render();
  if (step != null) {
    paintRow(step, null);
    const row = document.getElementById("s" + step); row.classList.add("flash");
    const save = finding && Array.from(row.querySelectorAll(".found")).find(a => a.hash === "#case/" + finding.oid);
    scrollEntry(save || row);
  } else if (live()) scrollCurrent();
  else window.scrollTo({top: 0});
}
let overviewModel = null;
function render() {
  const current = route.v === "research" ? "research" : "overview";
  for (const a of $$("#tabs a")) {
    const active = a.hash === "#" + current;
    a.classList.toggle("on", active);
    if (active) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  }
  if (route.v !== "findings") activityLeave();
  pill();
  for (const v of $$(".view")) v.hidden = v.id !== "v-" + route.v;
  if (route.v === "findings") {
    if (M !== overviewModel) {
      findings(); otherLeads(); overviewModel = M;
    }
    activityView();
  } else if (route.v === "case") caseView();
  else research();
  const title = $(".view:not([hidden]) h1");
  document.title = `${title?.textContent || "Findings"} · Distress Signals`;
}
function pill() { const p = $("#pill"); p.hidden = !(live() || status.state === "failed"); p.className = "pill " + status.state;
  p.innerHTML = live() ? `<span class="pulse"></span>Live · ${clock(elapsed())}` : "Failed"; }

function eventLabel(c) {
  const date = c.event_date ? [c.date_label || "Event reference date", longDate(c.event_date)].join(" · ") : "";
  return [esc(c.event || ""), date ? `<span>${esc(date)}</span>` : ""].filter(Boolean).join(" · ");
}
__FINDINGSJS__

__ACTIVITYJS__

function fmtVal(x) {
  const unit = String(x.unit || ""), currency = unit.match(/^([A-Z]{3})\b[\s,]*(.*)$/);
  if (x.value == null) return ["", unit];
  if (!currency && /\bshare of\b/i.test(unit)) return [x.value.toLocaleString("en-US", {style:"percent", maximumSignificantDigits:15}), unit];
  const number = x.value.toLocaleString("en-US", {maximumSignificantDigits:15});
  return currency ? [`${currency[1] === "USD" ? "$" : currency[1] + " "}${number}`, currency[2]] : [number, unit];
}
function scenarios(c) {
  const b = c.baseline || {}, list = c.scenarios || [], cur = c.currency; if (b.revenue == null || !list.length) return "";
  const max = Math.max(b.revenue, ...list.map(s => s.revenue || 0), 1);
  const one = (cls, name, desc, rev, short, share) => {
    const width = v => 100 * Math.max(0, v || 0) / max;
    const difference = short == null ? "" : short === 0 ? "At baseline" : `${money(Math.abs(short), cur)} ${short > 0 ? "below" : "above"} baseline`;
    return `<div class="sc ${cls}"><div class="sl"><b>${esc(name)}</b><span>${esc(desc || "")}</span></div>`
      + `<div class="bar" aria-hidden="true"><i style="width:${width(rev)}%"></i>${short > 0 ? `<s style="width:${width(short)}%"></s>` : ""}</div>`
      + `<div class="sv"><b>${money(rev, cur)}</b><span>Stream revenue</span>${difference ? `<div class="${short > 0 ? "neg" : ""}">${difference}${share != null && c.of ? ` · ${pct(share)}` : ""}</div>` : ""}</div></div>`;
  };
  return (c.of ? `<p>Percentage benchmark · ${esc(c.of)}${c.denominator_value != null ? ` (${money(c.denominator_value, cur)})` : ""}</p>` : "")
    + one("base", "Baseline", b.description, b.revenue, null)
    + list.map(s => one("", s.label, s.description, s.revenue, s.shortfall, s.share)).join("");
}
function quote(text, url) { return (text ? `<blockquote class="quote">${esc(text)}</blockquote>` : "")
  + (url ? `<a class="src" href="${esc(url)}" target="_blank" rel="noopener">${esc(host(url))} ↗</a>` : ""); }
function sourceNote(cite) {
  return cite.checked === false ? '<small class="note">Source excerpt only.</small>' : "";
}
const DOC_ICON = '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round" aria-hidden="true"><path d="M4 1.75h5.25L12.5 5v9.25h-8.5z"/><path d="M9.25 1.75V5h3.25"/><path d="M6.25 8.25h4M6.25 10.75h4"/></svg>';
function caseView() {
  const box = $("#v-case"), i = M.found.findIndex(f => f.oid === route.oid);
  const listLabel = DATA?.example_deal ? "Prospects" : "Findings";
  if (i < 0) { box.innerHTML = `<div class="crumbs"><a class="back" href="#section-findings">‹ ${listLabel}</a></div><div class="empty">No case with this ID is available in this run.</div>`; return; }
  const f = M.found[i], c = f.c, prev = M.found[i - 1], next = M.found[i + 1];
  const secs = [], p = t => t ? `<p>${esc(t)}</p>` : "";
  const sec = (h, body) => { if (!body) return ""; secs.push(h); return `<h3 id="sec${secs.length}">${h}</h3><div>${body}</div>`; };
  box.innerHTML = `<div class="crumbs"><a class="back" href="#section-findings">‹ ${listLabel}</a><span class="spacer"></span>`
    + (prev ? `<a class="btn sm" href="#case/${esc(prev.oid)}">‹ Previous case</a>` : "")
    + (next ? `<a class="btn sm" href="#case/${esc(next.oid)}">Next case ›</a>` : "") + `</div>`
    + `<div class="casehead"><div><div class="eyebrow">${esc(c.company)}</div><h1>${esc(c.stream || "Revenue exposure")}</h1></div>`
    + (c.stake != null ? `<div class="stake"><b>${esc(money(c.stake, c.currency))}</b><span>${esc(shortfallLabel(c))}</span>${basis(c) ? `<span>${esc(basis(c))}</span>` : ""}</div>` : "") + `</div>`
    + `<div class="case-actions"><form id="jump"><label for="case-section">Section</label><select id="case-section"></select><button class="btn sm">Go</button></form>`
    + `<a href="#found/${esc(f.oid)}">Research entry · ${clock(f.el)}</a>`
    + (c.deal ? `<a href="${esc(c.deal)}" target="_blank" rel="noopener">Open in HubSpot</a>` : "")
    + (c.memo ? `<span class="memo-actions"><a class="btn sm dark" href="${esc(c.memo.page)}" target="_blank" rel="noopener">${DOC_ICON}Memo</a>`
        + `<a class="btn sm" href="${esc(c.memo.pdf)}" download="${esc(c.memo.name)}">PDF</a></span>` : "") + `</div><div class="doc2">`
    + sec("Exposure", p(c.why_matters) + p(c.dependency))
    + sec("Event and timing", (eventLabel(c) ? `<p class="event-label">${eventLabel(c)}</p>` : "") + p(c.event_text) + p(c.event_description) + p(c.event_timing) + p(c.resolution_source ? "Shown by: " + c.resolution_source : ""))
    + sec("Revenue scenarios", p(c.condition) + scenarios(c) + p(c.timing))
    + sec("Model inputs", (c.inputs || []).map(x => { const [v, u] = fmtVal(x);
        return `<div class="inp"><div class="iv"><b>${esc(v)}</b><span>${esc(x.name)}</span></div><div><span class="badge ${x.kind}">${KIND[x.kind] || x.kind}</span>`
          + `<span class="unit">${esc(u)}</span>${x.how ? `<div>${esc(x.how)}</div>` : ""}${x.passage || x.url ? quote(x.passage, x.url) + sourceNote(x) : ""}`
          + `${x.note ? `<small class="note">${esc(x.note)}</small>` : ""}</div></div>`; }).join(""))
    + sec("Evidence", (c.evidence || []).map(e => `<div class="ev"><b>${esc(e.supports || "")}</b>${quote(e.passage, e.url)}${sourceNote(e)}</div>`).join(""))
    + sec(DATA?.example_deal ? "Suggested outreach" : "Person responsible", (c.who ? `<b>${esc(c.who)}</b>` : "") + p(c.role) + p(c.why_now) + p(DATA?.example_deal ? c.contact_route : "")
        + (c.opening ? `<details><summary>Draft message</summary><blockquote class="opening">${esc(c.opening)}</blockquote></details>` : ""))
    + "</div>";
  $("#jump").hidden = !secs.length;
  $("#case-section").replaceChildren(...secs.map((label, i) => new Option(label, "sec" + (i + 1))));
}
function otherLeads() {
  const section = $("#also"), list = $("#a-body");
  section.hidden = !(M.leads.length || M.aside.length);
  const expanded = new Set($$("details[open]", list).map(el => el.dataset.lead));
  function group(title, records, kind) {
    if (!records.length) return "";
    const rows = records.map((record, index) => {
      if (typeof record === "string") return `<li><p class="lead-note">${esc(record)}</p></li>`;
      const key = kind + index;
      const name = record.company_or_event || record.title;
      const body = esc(record.reason) + leadSources(record.source_urls);
      if (!name) return `<li><p class="lead-note">${body}</p></li>`;
      return `<li><details data-lead="${key}"${expanded.has(key) ? " open" : ""}><summary>${esc(name)}</summary><p>${body}</p></details></li>`;
    }).join("");
    return `<section class="lead-group"><h3>${title}</h3><ul>${rows}</ul></section>`;
  }
  list.innerHTML = group("For follow-up", M.leads, "follow") + group(DATA?.lead_review ? "Set aside" : "Concluded, with no case", M.aside, "aside");
}

// ---- the research, and its replay ----
const P = {pos: 0, playing: false, reveal: false, finished: false, follow: true, speed: 1, remaining: null, tick: 0, timer: 0};
function partOf(si) {
  const part = {says: 0, acts: 0, finds: 0};
  for (let i = M.first[si]; i <= P.pos; i++) {
    const k = M.beats[i].k;
    if (k === "say") part.says++; else if (k === "act") part.acts++; else if (k === "find") part.finds++;
  }
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
function stepBody(s, part, beat) {
  const says = part ? s.says.slice(0, part.says) : s.says, acts = part ? s.acts.slice(0, part.acts) : s.acts;
  const saved = s.finds.filter(f => f.n), finds = part ? saved.slice(0, part.finds) : saved;
  const showActs = !actsClosed.has(s.i) && (actsOpen.has(s.i) || (live() && s.i === M.steps.length - 1));
  const rest = !s.rest ? "" : `<button class="link" data-rest="${s.i}" aria-expanded="${opened.has(s.i)}">${opened.has(s.i) ? "Show less" : "Read full entry"}</button>`;
  return `<div class="t">${clock(s.el)}</div><div>`
    + `<p class="lead">${s.lead ? inline(esc(s.lead)) : '<span class="mute">Research activity</span>'}</p>${opened.has(s.i) && s.rest ? `<div class="rest">${paras(s.rest)}</div>` : ""}`
    + says.map(t => `<div class="say">${paras(t)}</div>`).join("")
    + (rest || acts.length ? `<div class="srow">${acts.length ? `<button class="chips" data-acts="${s.i}" aria-expanded="${showActs}">${chipsHTML(acts)}<span class="chev">${showActs ? "Hide" : "Show"}</span></button>` : ""}${rest}</div>` : "")
    + (acts.length && showActs ? `<ul class="al">${acts.map((a, j) => actHTML(a, part && beat.k === "act" && j === acts.length - 1)).join("")}</ul>` : "")
    + finds.map(f => `<a class="found${part && beat.f === f ? " new" : ""}" href="#case/${esc(f.oid)}"><span class="num">${f.n}</span><span>${clock(f.el)} · Case saved · <b>${esc(f.c.company)}</b> · `
      + `${esc(f.c.stream || "")}${f.c.stake != null ? ` · ${money(f.c.stake, f.c.currency)} modeled shortfall` : ""}</span><span class="go">View case →</span></a>`).join("")
    + `</div>`;
}
// Rows keep their DOM identity as playback appends entries. Live updates can amend earlier actions.
let logModel = null, previousActive = null, lastScroll = 0;
const rowBodies = [];
function paintRow(i, beat) {
  const rows = $("#transcript");
  let row = rows.children[i];
  if (!row) { row = document.createElement("article"); row.id = "s" + i; row.className = "step"; rows.append(row); }
  const active = beat?.s === i, part = active ? partOf(i) : null;
  if (part?.acts && !actsClosed.has(i)) actsOpen.add(i);
  row.classList.toggle("cur", active); row.classList.toggle("new", active && beat.k === "think");
  const body = stepBody(M.steps[i], part, beat);
  if (body !== rowBodies[i]) {
    const focused = row.contains(document.activeElement) && document.activeElement;
    const key = focused && ["rest", "acts"].find(k => focused.dataset[k] != null);
    const href = focused && focused.getAttribute("href");
    row.innerHTML = body; rowBodies[i] = body;
    if (key) row.querySelector(`[data-${key}]`)?.focus({preventScroll:true});
    else if (href) Array.from(row.querySelectorAll("a")).find(a => a.getAttribute("href") === href)?.focus({preventScroll:true});
  }
}
function research() {
  const rows = $("#transcript"), beat = P.reveal && M.beats[P.pos], count = beat ? beat.s + 1 : M.steps.length;
  $("#player").hidden = live() || !M.beats.length; $("#livebar").hidden = !live();
  rows.hidden = !count; $("#log-empty").hidden = !!count;
  if (!count) $("#log-empty").textContent = live() ? "Waiting for the first entry…" : "No research entries recorded.";
  while (rows.childElementCount > count) { rows.lastElementChild.remove(); rowBodies.pop(); }
  if (logModel !== M) {
    const jump = $("#saved-jump"); jump.hidden = !M.found.length;
    const options = M.found.map(f => new Option([clock(f.el), f.c.company, f.c.stream].filter(Boolean).join(" · "), f.oid));
    jump.replaceChildren(new Option("Jump to case", ""), ...options);
    for (let i = 0; i < count; i++) paintRow(i, beat);
    logModel = M;
  } else {
    if (previousActive != null && previousActive < count && previousActive !== beat?.s) paintRow(previousActive, beat);
    for (let i = rows.childElementCount; i < count; i++) paintRow(i, beat);
    if (beat) paintRow(beat.s, beat);
  }
  previousActive = beat ? beat.s : null;
  player();
}
function player() {
  if (live()) {
    $("#livebar").innerHTML = `<span class="pill running"><span class="pulse"></span>Live</span><span class="spacer"></span><b>${clock(elapsed())}</b>`;
    return;
  }
  if (!M.beats.length) return;
  const beat = M.beats[P.pos], t = P.finished ? M.secs : beat.el, fr = clampN(100 * t / (M.secs || 1), 0, 100);
  $("#full-log").hidden = !P.reveal || P.finished;
  $("#pp").innerHTML = P.playing ? ICONS.pause : ICONS.play;
  $("#pp").title = P.playing ? "Pause replay" : P.finished ? "Replay from start" : "Play replay";
  $("#pp").setAttribute("aria-label", $("#pp").title);
  const track = $("#scrub .track");
  track.setAttribute("aria-valuemax", M.steps.length); track.setAttribute("aria-valuenow", beat.s + 1);
  track.setAttribute("aria-valuetext", `Step ${beat.s + 1} of ${M.steps.length}, ${clock(t)} into the run`);
  $("#fill").style.width = fr + "%"; $("#knob").style.left = fr + "%";
  $("#ptitle").textContent = P.playing ? "Playing" : P.finished ? "Replay complete" : P.reveal || P.pos ? "Paused" : "Replay";
  $("#ptime").textContent = `${clock(t)} / ${clock(M.secs)}`;
  $$("#speed button").forEach(x => {
    const active = +x.dataset.x === P.speed; x.classList.toggle("on", active); x.setAttribute("aria-pressed", active);
  });
}
function scrollEntry(el) {
  if (!el) return;
  const bar = live() ? $("#livebar") : $("#player"), r = el.getBoundingClientRect();
  const top = (parseFloat(getComputedStyle(bar).top) || 0) + bar.offsetHeight + 12, bottom = innerHeight - 20;
  const delta = r.top < top || r.height > bottom - top ? r.top - top : Math.max(0, r.bottom - bottom);
  if (Math.abs(delta) > 1) window.scrollBy({top: delta, behavior: "instant"});
  lastScroll = window.scrollY;
}
function scrollCurrent() {
  const row = live() ? $("#transcript").lastElementChild : document.getElementById("s" + M.beats[P.pos]?.s);
  scrollEntry(row?.querySelector(".act.new,.found.new") || row);
}
function followLatest(follow) {
  P.follow = follow;
  $("#latest").hidden = follow || route.v !== "research" || !(live() || P.playing || P.reveal);
  $("#latest").textContent = live() ? "Latest entry ↓" : "Follow replay ↓";
}
function schedule() {
  clearTimeout(P.timer);
  if (P.remaining == null) P.remaining = M.beats[P.pos].d * 1000;
  P.tick = performance.now();
  P.timer = setTimeout(() => {
    P.remaining = null;
    if (P.pos === M.beats.length - 1) { P.playing = false; P.finished = true; P.reveal = false; followLatest(true); research(); return; }
    P.pos++; research(); if (P.follow) scrollCurrent(); schedule();
  }, P.remaining / P.speed);
}
function remainingTime() { return P.playing ? Math.max(0, P.remaining - (performance.now() - P.tick) * P.speed) : P.remaining; }
function pause() {
  if (P.playing) P.remaining = remainingTime();
  clearTimeout(P.timer); P.playing = false; if (route.v === "research") player();
}
function toggle() {
  if (live() || !M.beats.length) return;
  if (P.playing) {
    pause(); history.replaceState({...history.state, runScroll:window.scrollY, runReplay:replayPosition()}, ""); return;
  }
  if (P.finished) { P.pos = 0; P.remaining = null; P.finished = false; }
  P.reveal = true; P.playing = true; followLatest(true); research(); scrollCurrent(); schedule();
}
function seek(k) {
  P.pos = k; P.finished = false; P.remaining = null; P.reveal = true;
  followLatest(true); research(); scrollCurrent(); if (P.playing) schedule();
}

// ---- wiring ----
$("#pp").onclick = toggle;
$("#full-log").onclick = () => {
  pause(); P.reveal = false; followLatest(true); research();
  $("#pp").focus({preventScroll:true});
  history.replaceState({...history.state, runScroll:window.scrollY, runReplay:replayPosition()}, "");
};
$("#speed").onclick = e => {
  const x = +e.target.dataset.x; if (!x || x === P.speed) return;
  const playing = P.playing;
  if (playing) pause(); P.speed = x; P.playing = playing; player(); if (playing) schedule();
};
$("#scrub .track").onkeydown = e => {
  if (live() || !M.beats.length) return;
  const step = M.beats[P.pos].s;
  const next = {Home: 0, End: M.steps.length - 1, ArrowLeft: step - 1, ArrowDown: step - 1, ArrowRight: step + 1, ArrowUp: step + 1};
  if (!(e.key in next)) return;
  e.preventDefault(); seek(M.first[clampN(next[e.key], 0, M.steps.length - 1)]);
};
$("#scrub").onclick = e => {
  if (live() || !M.beats.length) return;
  const r = e.currentTarget.getBoundingClientRect(), t = clampN((e.clientX - r.left) / r.width, 0, 1) * M.secs;
  const after = M.beats.findIndex(b => b.el > t);
  seek(t === 0 ? 0 : after < 0 ? M.beats.length - 1 : Math.max(0, after - 1));
};
$("#saved-jump").onchange = e => { const oid = e.target.value; e.target.value = "";
  if (!oid) return;
  const hash = "#found/" + encodeURIComponent(oid);
  if (hash === location.hash) go(parse()); else location.hash = hash;
};
$("#v-research").addEventListener("click", e => {
  const t = e.target.closest("[data-rest],[data-acts]"); if (!t) return;
  if (P.playing) pause();
  const i = +(t.dataset.rest ?? t.dataset.acts);
  if (t.hasAttribute("data-rest")) { if (opened.has(i)) opened.delete(i); else opened.add(i); }
  else if (t.getAttribute("aria-expanded") === "true") { actsOpen.delete(i); actsClosed.add(i); }
  else { actsClosed.delete(i); actsOpen.add(i); }
  paintRow(i, P.reveal ? M.beats[P.pos] : null);
});
$("#v-case").addEventListener("submit", e => {
  if (e.target.id !== "jump") return;
  e.preventDefault();
  const section = document.getElementById($("#case-section").value);
  if (section) {
    section.tabIndex = -1; section.focus({preventScroll:true});
    section.scrollIntoView({behavior:scrollBehavior(), block:"start"});
  }
});
document.addEventListener("keydown", e => { if (route.v !== "research" || e.metaKey || e.ctrlKey || e.altKey || e.target.closest("button,a,input,textarea,select,summary,[contenteditable=true],[role=slider]")) return;
  if (e.code === "Space") { e.preventDefault(); toggle(); } });
// Hash links are real history entries, including the reader's position in each view.
let displayedURL, currentVisit;
const positions = new Map(), replayVisits = new Map();
function replayPosition() {
  if (route.v !== "research" || live() || !M?.beats.length) return null;
  const {pos, reveal, finished, follow, speed} = P;
  return {pos, reveal, finished, follow, remaining:remainingTime(), speed,
          read:[...opened], expanded:[...actsOpen], collapsed:[...actsClosed]};
}
let visitCount = 0;
history.scrollRestoration = "manual";
function rememberPosition() {
  if (currentVisit && displayedURL === location.href) positions.set(currentVisit, window.scrollY);
}
window.addEventListener("scroll", rememberPosition, {passive:true});
document.addEventListener("click", e => {
  if (e.target.closest('a[href^="#"]')) rememberPosition();
}, {capture:true});
window.addEventListener("pagehide", () => {
  rememberPosition();
  if (currentVisit && displayedURL === location.href)
    history.replaceState({...history.state, runScroll:positions.get(currentVisit), runReplay:replayPosition()}, "");
});
function openRoute(moveFocus = false) {
  const next = parse(), visit = history.state?.runVisit || `${performance.timeOrigin}:${++visitCount}`;
  if (moveFocus && visit === currentVisit && displayedURL === location.href) return;
  const saved = positions.get(visit) ?? history.state?.runScroll;
  if (currentVisit && currentVisit !== visit && route.v === "research") replayVisits.set(currentVisit, replayPosition());
  currentVisit = visit;
  const savedReplay = replayVisits.get(visit) || history.state?.runReplay;
  history.replaceState({...history.state, runVisit:visit, runScroll:saved, runReplay:savedReplay}, "");
  go(next, savedReplay);
  displayedURL = location.href;
  const anchor = next.section && document.getElementById(next.section);
  if (anchor && next.section === "hubspot") {
    setExampleDealOpen(true);
  }
  if (Number.isFinite(saved)) window.scrollTo({top:saved, behavior:"instant"});
  else if (anchor && anchor.getClientRects().length) anchor.scrollIntoView({block:"start", behavior:"instant"});
  else if (next.v !== "research") window.scrollTo({top:0, behavior:"instant"});
  if (!moveFocus) return;
  const target = (anchor && anchor.getClientRects().length && (anchor.querySelector("h2,summary") || anchor)) || (savedReplay && next.v === "research" && $("#pp")) || ((next.step != null || next.find) && $(".step.flash")) || (next.play && $("#pp")) || $(".view:not([hidden]) h1");
  if (target) { if (!target.matches("button")) target.tabIndex = -1; target.focus({preventScroll:true}); }
}
window.addEventListener("hashchange", () => { if (M) openRoute(true); });
function measureShell() {
  document.documentElement.style.setProperty("--shell-height", $(".run-nav").offsetHeight + "px");
}
measureShell();
new ResizeObserver(measureShell).observe($(".run-nav"));
window.addEventListener("resize", () => { if (route.v === "findings") activityResize(); });
const scrollBehavior = () => matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth";
window.addEventListener("scroll", () => {
  if (route.v === "research" && (P.playing || live()) && window.scrollY < lastScroll - 2) followLatest(false);
  lastScroll = window.scrollY;
}, {passive: true});
window.addEventListener("wheel", e => {
  if (e.deltaY < 0 && route.v === "research" && (P.playing || live())) followLatest(false);
}, {passive: true});
$("#latest").onclick = () => { followLatest(true); scrollCurrent(); };
setInterval(() => { if (!live()) return; pill(); if (route.v === "research") player(); }, 1000);
let received = false;
async function poll() {
  try {
    const initial = !received, j = await (await fetch(`/runs/${RUN}/ops?after=${after}`)).json();
    const oldState = status.state, cases = j.cases || {};
    const amended = Object.entries(cases).some(([oid, c]) => JSON.stringify(details[oid]) !== JSON.stringify(c));
    ingest(j.ops); after = j.next; status = j.status || {}; Object.assign(details, cases); received = true;
    if (initial || j.ops.length || status.state !== oldState || amended) {
      M = build();
      if (initial) openRoute();
      else if (route.v === "research") { research(); pill(); followLatest(P.follow); if (P.follow && live()) scrollCurrent(); }
      else render();
    } else pill();
    if (status.state === "running" || status.state === "unknown") setTimeout(poll, 2500);
  } catch (e) { setTimeout(poll, 5000); }
}
if (DATA) { ingest(DATA.ops);
  for (const [i, display] of Object.entries(DATA.action_display || {})) Object.assign(items[i], display);
  status = DATA.status || {}; details = DATA.cases || {}; M = build(); openRoute(); }
else { M = build(); openRoute(); poll(); }
</script></body></html>"""

PAGE_STEPS = 12  # page size used to resolve bookmarks from older exports

# How long the replay holds each thing at 1x, in seconds: a thought's headline gets base + per-word time
# within [min, max]; a message from the researcher likewise; each search, read or calculation; each finding.
PACE = {"think": [0.8, 0.075, 1.8, 3.4], "say": [1.0, 0.03, 2.2, 4.5], "act": 0.35, "find": 3.0}
