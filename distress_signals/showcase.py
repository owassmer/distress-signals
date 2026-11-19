"""A visual guide to one run, for sharing: what the tool does, what this run found, and where it shows up in
HubSpot, as graphics with few words. It is a guide to the product, not a copy of the operating surface.

export_guide(run_id, folder) writes index.html (the guide) and run.html (the run page's public export: the
findings, each case in full, and the research as chapters with a replay), both self-contained, ready for
GitHub Pages. The guide's cards link into run.html.

  python -m distress_signals export-guide RUN_ID [--out FOLDER]
"""

import datetime as dt
import json
import math
from pathlib import Path

from . import runpage, watch

# The chart replay's length follows the run's: per_minute seconds for each minute of the run, kept within
# [min, max] seconds, plus a hold at each finding; each line of the ticker stays up at least `label` seconds.
PACE = {"per_minute": 2.0, "min": 40, "max": 100, "hold": 1.8, "label": 1.5}


def _guide_case(detail, found_at):
    """Keep the scenario and comparison period with the headline shortfall."""
    fields = ("oid", "company", "company_id", "stream", "stake", "share", "of", "currency",
              "period_start", "period_end", "event", "event_date", "event_timing", "date_label",
              "who", "opening", "denominator_value")
    case = {key: detail.get(key) for key in fields}
    scenarios = [s for s in detail.get("scenarios") or [] if s.get("shortfall") is not None]
    worst = max(scenarios, key=lambda s: s["shortfall"], default={})
    case.update(found_at=found_at, scenario=worst.get("description") or worst.get("label"),
                source=next((e["url"] for e in detail.get("evidence") or [] if e.get("url")), None))
    return case


def build(run_id):
    """The guide's data for a run: the numbers, the minute-by-minute activity, the revenue exposures found and when,
    and what was set aside or suggested."""
    v = watch.RunView(run_id)
    v.update()
    run = watch._run_row(run_id)
    status = watch._status(run_id, run, v)
    items = watch._items(v.ops)
    acts = [it for it in items if it["k"] == "act"]
    found, seen = [], set()
    for it in acts:
        c = it.get("case")
        if c and c["oid"] not in seen:
            seen.add(c["oid"])
            found.append((it.get("el") or 0, c["oid"]))
    details = watch.public_cases(run_id, {oid for _, oid in found})
    t0 = dt.datetime.fromisoformat(status["t0"]) if status.get("t0") else None
    cases = []
    for t, oid in found:
        d = details.get(oid)
        if not d or d["status"] != "active":
            continue
        cases.append(_guide_case(d, t))
    seconds = (status.get("duration_ms") or 0) / 1000 or max([it.get("el") or 0 for it in acts] or [60])
    minutes = max(1, math.ceil(seconds / 60))
    bins = [{"search": 0, "read": 0, "calc": 0} for _ in range(minutes)]
    ticker = []
    categories = {"search": "search", "read": "read", "web": "read", "calc": "calc"}
    for it in acts:
        entry = {"t": it.get("el") or 0, "label": it.get("label") or ""}
        kind = categories.get(it["icon"])
        if kind and it.get("status") == "ok":
            entry["kind"] = kind
            bins[min(minutes - 1, int(entry["t"] // 60))][kind] += 1
        ticker.append(entry)
    count = lambda k: sum(b[k] for b in bins)
    other = watch.public_leads(run_id, items)
    return {
        "title": watch._title(run), "assignment": watch._assignment(run), "seed_kind": (run or {}).get("seed_kind"),
        "date": t0.strftime("%B %-d, %Y") if t0 else "", "minutes": round(seconds / 60),
        "duration_seconds": seconds, "state": status.get("state"),
        "stats": {"searches": count("search"), "documents": count("read"), "calculations": count("calc"),
                  "exposures": len(cases), "companies": len({c["company_id"] for c in cases})},
        "bins": bins, "cases": cases,
        "ticker": ticker,
        "aside": other["aside"],
        "leads": [{"what": x.get("company_or_event"), "why": x.get("reason"), "sources": x.get("source_urls") or []}
                  for x in other["leads"]],
        "steps": sum(1 for it in items if it["k"] == "think"),
    }


STYLE = r"""
:root{--bg:#f7f8fa;--panel:#fff;--ink:#162237;--body:#34445b;--muted:#52647a;--line:#dce2e9;--blue:#2456ad;--green:#14763a}
*{box-sizing:border-box}[hidden]{display:none!important}
html{scroll-behavior:smooth}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Inter,system-ui,sans-serif;-webkit-font-smoothing:antialiased}
.wrap{width:min(100%,1096px);margin:auto;padding:0 32px 42px}
a{color:var(--blue);text-decoration:none}a:hover{text-decoration:underline}button,summary{cursor:pointer;font:inherit}button{color:inherit}
:focus-visible{outline:3px solid #3b82f6;outline-offset:4px}
nav{display:flex;align-items:center;gap:22px;padding:18px 0;border-bottom:1px solid var(--line);font-size:14px;font-weight:600}
nav .brand{display:flex;align-items:center;gap:8px;margin-right:auto;color:var(--ink)}nav img{width:23px;height:23px}
.hero{padding:34px 0 23px}.hero h1{margin:0;font-size:clamp(30px,4.2vw,44px);letter-spacing:-.042em;line-height:1.1}
.hero p{margin:9px 0 0;max-width:820px;color:var(--body);font-size:17px}
h2,h3,h4,p{margin:0}h2,h3,h4{line-height:1.23}h2{font-size:24px;letter-spacing:-.025em}
.cards{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px}
.company,.chart,.crm{min-width:0;border:1px solid var(--line);border-radius:14px;background:var(--panel)}
.company{padding:20px 22px;display:flex;flex-direction:column;gap:13px}
.company.wide{grid-column:1/-1}.company h2{font-size:22px}
.event{padding-top:12px;border-top:1px solid var(--line);display:flex;flex-direction:column;flex:1}
.event-meta{color:var(--body);font-size:15px}
.event-meta strong{display:block;font-weight:650}.event-meta small{display:block;margin-top:2px;font-size:13px;color:var(--muted)}
.streams{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:24px;flex:1;margin-top:14px}
.streams.single{grid-template-columns:1fr}.stream{min-width:0;display:flex;flex-direction:column;gap:8px}
.stream + .stream{border-left:1px solid var(--line);padding-left:24px}.stream h3{font-size:16px}
.measure{display:flex;flex-wrap:wrap;align-items:baseline;gap:4px 9px}
.amount{font-weight:760;font-size:29px;letter-spacing:-.04em;line-height:1.1;font-variant-numeric:tabular-nums}
.metric,.basis,.reference{font-size:13px;line-height:1.4;color:var(--muted)}.reference{margin-top:5px}
.reference b{font-weight:650;color:var(--body)}.scenario{font-size:14px;color:var(--body)}.scenario b{color:var(--ink);font-weight:650}
.contact{font-size:14px;color:var(--body);margin-top:auto;padding-top:3px}.contact b{font-size:12px;font-weight:650;display:block;color:var(--muted)}
.links{display:flex;flex-wrap:wrap;gap:3px 15px;font-size:13px;line-height:1.8;font-weight:600}.links a{padding:2px 0}
.stream details{margin-top:-2px;font-size:13px}.stream summary{display:list-item;width:fit-content;color:var(--blue);font-weight:600}
.stream blockquote{margin:7px 0 4px;border-left:2px solid #c5d6ef;padding:1px 0 1px 12px;color:var(--body);font-size:14px}
#run,#hubspot{margin-top:30px}
.chart{padding:18px 20px}.chart-head{display:flex;align-items:center;flex-wrap:wrap;gap:7px 12px}
.chart-head h2{font-size:20px}.chart-head time{color:var(--muted);font-size:13px}
#cplay{margin-left:auto;display:inline-flex;align-items:center;gap:6px;background:var(--panel);border:1px solid var(--line);border-radius:7px;padding:6px 10px;font-size:13px;font-weight:600}
#cplay svg{width:12px;height:12px}
.legend{display:flex;flex-wrap:wrap;gap:3px 17px;color:var(--muted);font-size:13px;margin:10px 0 0}
.legend span{display:inline-flex;align-items:center;gap:4px}.legend i{width:9px;height:9px;border-radius:2px}
#chart{display:block;width:100%;height:160px}
.prog{height:2px;background:#edf0f3}.prog i{display:block;width:0;height:100%;background:var(--blue)}
.chart-foot{display:flex;flex-wrap:wrap;align-items:baseline;justify-content:space-between;gap:6px 20px;margin-top:9px}
.ticker{flex:1 1 300px;min-width:0;font-size:13px;color:var(--body);overflow-wrap:anywhere}.ticker b{color:var(--muted);margin-right:7px}.ticker .hit{color:var(--green)}
.crm{padding:18px 20px}.crm h2{font-size:20px}
.crm dl{margin:12px 0 0;display:grid;grid-template-columns:1.2fr 1.5fr 1.5fr .7fr;gap:8px 20px}.crm dt{font-size:12px;color:var(--muted);font-weight:650}.crm dd{margin:3px 0 0;font-size:14px;color:var(--body)}.crm dd.stage{color:var(--green);font-weight:650}
@media(max-width:730px){.wrap{padding:0 16px 26px}.cards{grid-template-columns:1fr}.company.wide{grid-column:auto}.company{padding:17px}.streams{gap:15px}.stream + .stream{padding-left:15px}.crm dl{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:540px){nav{gap:14px;padding:14px 0;font-size:13px}.hero{padding:26px 0 18px}.hero p{font-size:15px}
.streams{grid-template-columns:1fr}.stream + .stream{border:0;border-top:1px solid var(--line);padding:13px 0 0}
.chart,.crm{padding:15px}.chart-head{gap:5px 8px}.chart-head time{order:3;flex-basis:100%}.chart-head h2,.crm h2{font-size:18px}.company h2{font-size:20px}
.crm dl{column-gap:10px}.links{column-gap:12px}#run,#hubspot{margin-top:18px}}
@media(max-width:360px){nav{font-size:12px;gap:8px}nav .brand{gap:5px}nav a{white-space:nowrap}}
@media(prefers-reduced-motion:reduce){html{scroll-behavior:auto}}
"""

ICONS = {
    "play": '<svg viewBox="0 0 24 24" aria-hidden="true" fill="currentColor"><path d="M7 5v14l12-7z"/></svg>',
    "pause": '<svg viewBox="0 0 24 24" aria-hidden="true" fill="currentColor"><rect x="6" y="5" width="4" height="14"/><rect x="14" y="5" width="4" height="14"/></svg>',
}

PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Distress Signals | Revenue exposures</title><link rel="icon" href="__FAVICON__"><style>__STYLE__</style></head><body>
<main class="wrap">
<nav aria-label="Project"><a class="brand" href="index.html"><img src="__FAVICON__" alt="">Distress Signals</a><a href="run.html">Research record</a><a href="__REPO__">GitHub</a></nav>
<header class="hero"><h1 id="title"></h1><p id="purpose"></p></header>
<section id="found" aria-label="Company findings"><div class="cards" id="cards"></div></section>

<section id="run" class="chart" aria-labelledby="activity-title">
  <div class="chart-head"><h2 id="activity-title">Research activity</h2><time id="date"></time><button id="cplay" type="button"></button></div>
  <div class="legend"><span><i style="background:#2563eb"></i><b id="k-search"></b> searches</span>
    <span><i style="background:#94a3b8"></i><b id="k-doc"></b> source reads</span><span><i style="background:#7c3aed"></i><b id="k-calc"></b> queries &amp; calculations</span>
    <span><i style="background:#16a34a;border-radius:50%"></i>Case saved</span></div>
  <svg id="chart" role="img" aria-label="Research actions per minute and timestamps of saved cases"></svg>
  <div class="prog"><i id="prog"></i></div><div class="chart-foot"><div class="ticker" id="ticker"><b id="tmin"></b><span id="tlabel"></span></div>
    <div class="links"><a href="run.html#research">Research log</a><a href="run.html#also">Other leads</a></div></div>
</section>
<section id="hubspot" class="crm" aria-labelledby="crm-title"><h2 id="crm-title">Example HubSpot deal</h2><dl id="deal"></dl></section>
</main><script>
__FOUNDJS__
__CHARTJS__
const PACE = __PACE__, PLAY = `__PLAY__`, PAUSE = `__PAUSE__`, D = __DATA__;
const $ = s => document.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
$("#title").textContent = D.title;
const purpose = D.seed_kind === "mandate"
  ? "The research agent searched public records for upcoming external events and the companies with material revenue at stake."
  : D.assignment;
$("#purpose").textContent = purpose || ""; $("#purpose").hidden = !purpose;
$("#date").textContent = D.date || "";
const FLAGS = D.cases.map(c => ({t: c.found_at, label: c.company + " · " + c.stream}));
function activity(t) {
  if (t === Infinity) return D.bins;
  const bins = D.bins.map(() => ({search: 0, read: 0, calc: 0}));
  for (const entry of D.ticker) {
    if (entry.t <= t && entry.kind) bins[Math.min(bins.length - 1, Math.floor(entry.t / 60))][entry.kind]++;
  }
  return bins;
}
function draw(t) {
  const visible = activity(t), sum = key => visible.reduce((n, b) => n + b[key], 0);
  drawChart($("#chart"), D.bins, FLAGS, t, 160, visible);
  $("#k-search").textContent = sum("search"); $("#k-doc").textContent = sum("read"); $("#k-calc").textContent = sum("calc");
}
function stream(c, sharedBasis) {
  const oid = encodeURIComponent(c.oid), period = periodLabel(c);
  const measure = c.stake == null ? "" : `<div class="measure"><b class="amount">${esc(money(c.stake,c.currency))}</b><span class="metric">Largest modeled shortfall${period ? " · " + esc(period) : ""}</span></div>`;
  const share = c.share == null || !c.of ? "" : `<p class="basis">${esc((c.share * 100).toFixed(1))}% of ${sharedBasis ? "reference revenue" : esc(c.of)}</p>`;
  const contact = c.who ? `<p class="contact"><b>Suggested contact</b>${esc(c.who)}</p>` : "";
  let source = "";
  try {
    const url = new URL(c.source);
    if (["http:", "https:"].includes(url.protocol)) {
      const label = url.hostname.endsWith("sec.gov") && url.pathname.startsWith("/Archives/edgar/") ? "SEC filing" : "Source document";
      source = `<a href="${esc(url.href)}">${label}</a>`;
    }
  } catch {}
  return `<div class="stream"><h3>${esc(c.stream)}</h3>${measure}${share}${c.scenario ? `<p class="scenario"><b>Scenario</b> · ${esc(c.scenario)}</p>` : ""}${contact}
    <div class="links"><a href="run.html#case/${oid}">Full case</a>${source}<a href="run.html#found/${oid}">Research log</a></div>
    ${c.opening ? `<details><summary>Draft outreach</summary><blockquote>${esc(c.opening)}</blockquote></details>` : ""}</div>`;
}
function eventRecord(cases) {
  const first = cases[0];
  const sharedBasis = cases.length > 1 && first.of && cases.every(c => c.of === first.of && c.currency === first.currency && c.denominator_value === first.denominator_value);
  const date = first.event_date ? new Date(first.event_date + "T12:00:00Z").toLocaleDateString("en-US",{month:"short",day:"numeric",year:"numeric",timeZone:"UTC"}) : "";
  const basis = sharedBasis ? `<p class="reference"><b>Reference revenue</b> · ${first.denominator_value != null ? esc(money(first.denominator_value,first.currency)) + " · " : ""}${esc(first.of)}</p>` : "";
  return `<div class="event"><div class="event-meta"><strong>${esc(first.event)}</strong>${date ? `<small>${esc(first.date_label || "Event reference date")} · ${esc(date)}</small>` : ""}</div>${basis}
    <div class="streams${cases.length === 1 ? " single" : ""}">${cases.map(c => stream(c,sharedBasis)).join("")}</div></div>`;
}
function cards() {
  $("#cards").innerHTML = groupByCompany(D.cases).map(cases => {
    const events = new Map();
    for (const c of cases) {
      const key = JSON.stringify([c.event, c.event_date, c.date_label]);
      if (!events.has(key)) events.set(key, []);
      events.get(key).push(c);
    }
    return `<article class="company${cases.length > 1 ? " wide" : ""}"><h2>${esc(cases[0].company)}</h2>${[...events.values()].map(eventRecord).join("")}</article>`;
  }).join("") || "<p>No active cases recorded.</p>";
}
function deal() {
  const first = D.cases[0];
  if (!first) { $("#hubspot").hidden = true; return; }
  const fields = [["Company", first.company], ["Distress revenue stream", first.stream], ["Distress person", first.who], ["Deal stage", "Proposed"]];
  $("#deal").innerHTML = fields.filter(([,value]) => value).map(([name,value]) =>
    `<div><dt>${esc(name)}</dt><dd${name === "Deal stage" ? ' class="stage"' : ""}>${esc(value)}</dd></div>`).join("");
}
function finish() {
  draw(Infinity); $("#prog").style.width = "0";
  $("#tmin").textContent = `${D.minutes} min`; $("#tlabel").textContent = "";
}

// The replay: the run's minutes at a steady rate, a hold at each finding, each ticker line up long enough to read
const RUN_S = Math.max(1, D.duration_seconds ?? Math.max(D.minutes * 60, ...D.ticker.map(x => x.t))), BASE_MS = 1000 * Math.min(PACE.max, Math.max(PACE.min, D.minutes * PACE.per_minute));
const RATE = RUN_S / BASE_MS, HOLD = PACE.hold * 1000, STOPS = D.cases.map((c, i) => ({t: c.found_at, i})).sort((a, b) => a.t - b.t);
const TOTAL = BASE_MS + STOPS.length * HOLD, LENGTH = TOTAL < 50000 ? `${Math.round(TOTAL / 1000)} sec` : `about ${Math.max(1, Math.round(TOTAL / 60000))} min`;
function at(ms) { let acc = 0, prev = 0;
  for (const s of STOPS) { const seg = (s.t - prev) / RATE; if (ms < acc + seg) return {t: prev + (ms - acc) * RATE}; acc += seg;
    if (ms < acc + HOLD) return {t: s.t, stop: s.i}; acc += HOLD; prev = s.t; }
  return {t: Math.min(RUN_S, prev + (ms - acc) * RATE)}; }
const R = {state: "idle", ms: 0, last: 0, timer: 0, label: "", since: -1e9};
function show(now) {
  const {t, stop} = at(R.ms); draw(t); $("#prog").style.width = (100 * R.ms / TOTAL) + "%";
  $("#tmin").textContent = `${Math.floor(t / 60)} min`;
  let html = null, force = stop != null;
  if (force) { const c = D.cases[stop]; html = `<span class="hit">Case saved · ${esc(c.company)} · ${esc(c.stream)}</span>`; }
  else { let cur = null; for (const x of D.ticker) { if (x.t <= t) cur = x; else break; } if (cur) html = esc(cur.label); }
  if (html != null && html !== R.label && (force || now - R.since >= PACE.label * 1000)) { R.label = html; R.since = now; $("#tlabel").innerHTML = html; }
}
function frame() { const now = performance.now(); R.ms = Math.min(TOTAL, R.ms + now - R.last); R.last = now; show(now);
  if (R.ms < TOTAL) R.timer = setTimeout(frame, 50); else { R.state = "idle"; finish(); ctl(); } }
function ctl() { $("#cplay").innerHTML = R.state === "playing" ? `${PAUSE} Pause` : R.state === "paused" ? `${PLAY} Resume` : `${PLAY} Replay · ${LENGTH}`; }
function play() {
  if (R.state === "playing") { clearTimeout(R.timer); R.state = "paused"; ctl(); return; }
  clearTimeout(R.timer); if (R.state !== "paused") { R.ms = 0; R.label = ""; R.since = -1e9; $("#tlabel").textContent = ""; }
  R.state = "playing"; R.last = performance.now(); ctl(); frame();
}
$("#cplay").onclick = play;
window.addEventListener("resize", () => draw(R.state === "idle" ? Infinity : at(R.ms).t));
cards(); deal(); finish(); ctl();
</script></body></html>"""


def export_html(run_id):
    data = build(run_id)
    page = (PAGE.replace("__STYLE__", STYLE).replace("__FOUNDJS__", runpage.FOUND_JS).replace("__CHARTJS__", runpage.CHART_JS)
            .replace("__FAVICON__", runpage.FAVICON).replace("__REPO__", watch.REPO).replace("__PACE__", json.dumps(PACE))
            .replace("__PLAY__", ICONS["play"]).replace("__PAUSE__", ICONS["pause"]))
    return page.replace("__DATA__", json.dumps(data, default=str).replace("</", "<\\/"))


def export_guide(run_id, folder):
    """index.html (the visual guide) and run.html (the full research trail), ready for GitHub Pages."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "index.html").write_text(export_html(run_id))
    (folder / "run.html").write_text(watch.export_html(run_id, public=True, home="./"))
    (folder / ".nojekyll").write_text("")
    return folder
