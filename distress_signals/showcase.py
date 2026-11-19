"""A visual guide to one run, for sharing: what the tool does, what this run found, and where it shows up in
HubSpot, as graphics with few words. It is a guide to the product, not a copy of the operating surface.

export_guide(run_id, folder) writes index.html (the guide) and run.html (the run page's public export: the
findings, each case in full, and the research as chapters with a replay), both self-contained, ready for
GitHub Pages. The guide's cards link into run.html.

  python -m distress_signals export-guide RUN_ID [--out FOLDER]
"""

import datetime as dt
import html
import json
import math
import re
from pathlib import Path

from . import runpage, watch

# The chart replay's length follows the run's: per_minute seconds for each minute of the run, kept within
# [min, max] seconds, plus a hold at each finding; each line of the ticker stays up at least `label` seconds.
PACE = {"per_minute": 2.0, "min": 40, "max": 100, "hold": 1.8, "label": 1.5}


def _short_event(title):
    t = re.sub(r"\s*\([^)]*\)", "", title or "").split(";")[0].strip()
    return t[:1].upper() + t[1:]


def _initials(name):
    words = [w for w in re.split(r"[\s,]+", name or "") if w[:1].isalpha()]
    return "".join(w[0] for w in words[:2]).upper() or "?"


def build(run_id):
    """The guide's data for a run: the numbers, the minute-by-minute activity, the revenue exposures found and when,
    and what was set aside or suggested."""
    v = watch.RunView(run_id)
    v.update()
    run = watch._run_row(run_id)
    status = watch._status(run_id, run, v)
    items = []
    for op in v.ops:
        if op["op"] == "add":
            items.append(dict(op["item"]))
        elif op["i"] < len(items):
            items[op["i"]].update(op["fields"])
    acts = [it for it in items if it["k"] == "act"]
    found, seen = [], set()
    for it in acts:
        c = it.get("case")
        if c and c["oid"] not in seen:
            seen.add(c["oid"])
            found.append((it.get("el") or 0, c["oid"]))
    details = watch._case_details({oid for _, oid in found}, public=True)
    t0 = dt.datetime.fromisoformat(status["t0"]) if status.get("t0") else None
    cases = []
    for t, oid in found:
        d = details.get(oid)
        if not d or d["status"] != "active":
            continue
        days = None
        if d.get("event_date") and t0:
            days = (dt.date.fromisoformat(d["event_date"]) - t0.date()).days
        who = d.get("who") or ""
        cases.append({"oid": oid, "company": d["company"], "company_id": d["company_id"], "stream": d["stream"], "stake": d["stake"], "share": d["share"],
                      "currency": d["currency"], "event": _short_event(d.get("event")), "event_date": d.get("event_date"),
                      "days": days, "who": who, "initials": _initials(who.split(",")[0]), "opening": d.get("opening"),
                      "found_at": t})
    seconds = (status.get("duration_ms") or 0) / 1000 or max([it.get("el") or 0 for it in acts] or [60])
    minutes = max(1, math.ceil(seconds / 60))
    bins = [{"search": 0, "read": 0, "calc": 0} for _ in range(minutes)]
    for it in acts:
        kind = "search" if it["icon"] == "search" else "read" if it["icon"] in ("read", "web") else "calc" if it["icon"] == "calc" else None
        if kind and it.get("status") != "blocked":
            bins[min(minutes - 1, int((it.get("el") or 0) // 60))][kind] += 1
    count = lambda k: sum(b[k] for b in bins)
    final = next((it for it in items if it["k"] == "final"), {})
    return {
        "title": watch._title(run)[0], "assignment": watch._assignment(run),
        "date": t0.strftime("%B %-d, %Y") if t0 else "", "minutes": round(seconds / 60), "cost": status.get("cost"),
        "stats": {"searches": count("search"), "documents": count("read"), "calculations": count("calc"),
                  "exposures": len(cases), "companies": len({c["company_id"] for c in cases})},
        "bins": bins, "cases": cases,
        "ticker": [{"t": it.get("el") or 0, "label": it["label"]} for it in acts if it.get("label")],
        "aside": [it.get("text") for it in acts if it.get("tool") == "record_finding" and it.get("finding") == "dropped"
                  and it.get("status") == "ok" and it.get("text")],
        "leads": [{"what": x.get("company_or_event"), "why": x.get("reason")} for x in final.get("leads") or []],
        "steps": sum(1 for it in items if it["k"] == "think"),
    }


STYLE = r"""
:root{--bg:#f7f8fa;--card:#fff;--line:#e7e9ee;--ink:#0f172a;--soft:#334155;--mute:#64748b;--faint:#94a3b8;
--blue:#2563eb;--bluebg:#e8efff;--green:#16a34a;--greenbg:#e9f9ef;--violet:#7c3aed;--violetbg:#f1ebff;--slate:#94a3b8;--hs:#ff7a59;--hsbg:#fff1ec}
*{box-sizing:border-box}[hidden]{display:none!important}html{scroll-behavior:smooth}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Inter,system-ui,sans-serif;-webkit-font-smoothing:antialiased}
a{color:var(--blue);text-decoration:none}.wrap{max-width:1040px;margin:0 auto;padding:0 28px}
nav{display:flex;align-items:center;gap:10px;padding:18px 0}nav .brand{display:flex;align-items:center;gap:9px;font-weight:700;font-size:14px;color:var(--soft)}
nav img{width:22px;height:22px}nav .sp{flex:1}
.btn{display:inline-flex;align-items:center;gap:8px;border:1px solid var(--line);background:var(--card);color:var(--soft);border-radius:99px;
padding:9px 16px;font-weight:600;font-size:14px;font-family:inherit;cursor:pointer;white-space:nowrap}.btn:hover{border-color:#cbd5e1}
.btn.primary{background:var(--ink);color:#fff;border-color:var(--ink)}.btn.primary:hover{background:#1e293b}
.hero{padding:44px 0 26px;text-align:center}.hero h1{font-size:44px;line-height:1.1;letter-spacing:-.035em;margin:0 auto 16px;max-width:820px;font-weight:800}
.hero p{font-size:18px;color:var(--soft);max-width:640px;margin:0 auto 26px}.hero .ctas{display:flex;gap:10px;justify-content:center;flex-wrap:wrap}
.eyebrow{font-size:12px;font-weight:750;letter-spacing:.12em;text-transform:uppercase;color:var(--blue);margin:0 0 10px}
section{padding:46px 0}h2{font-size:28px;letter-spacing:-.025em;margin:0 0 6px;font-weight:780}.lead{color:var(--mute);margin:0 0 26px;font-size:16px}
.flow{display:grid;grid-template-columns:repeat(4,1fr);gap:18px;position:relative}
.node{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:22px 18px 18px;position:relative;text-align:center}
.node .ic{width:52px;height:52px;border-radius:16px;display:grid;place-items:center;margin:0 auto 12px}.node .ic svg{width:26px;height:26px}
.node h3{margin:0 0 4px;font-size:17px}.node p{margin:0;color:var(--mute);font-size:14px;line-height:1.45}
.node .n{position:absolute;top:12px;left:14px;font-size:12px;font-weight:750;color:var(--faint)}
.node:not(:last-child):after{content:"";position:absolute;right:-16px;top:44px;width:14px;height:14px;border-top:2px solid #cbd5e1;border-right:2px solid #cbd5e1;transform:rotate(45deg);z-index:1}
.loop{display:flex;align-items:center;justify-content:center;gap:10px;margin-top:16px;color:var(--mute);font-size:14px}
.loop svg{width:120px;height:26px}
.tiles{display:grid;grid-template-columns:repeat(6,1fr);gap:12px;margin-bottom:18px}
.tile{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:16px 16px 14px}
.tile b{display:block;font-size:30px;letter-spacing:-.03em;line-height:1.1;font-variant-numeric:tabular-nums}.tile span{color:var(--mute);font-size:13px}
.chart{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:18px 20px 14px}
.chart .top{display:flex;align-items:center;gap:14px;margin-bottom:6px}.chart .top h3{margin:0;font-size:15px}.chart .sp{flex:1}
.legend{display:flex;gap:14px;font-size:12.5px;color:var(--mute)}.legend i{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:5px;vertical-align:-1px}
.prog{height:3px;background:#eef0f3;border-radius:9px;margin-top:6px;overflow:hidden}.prog i{display:block;height:100%;width:0;background:var(--ink)}
.ticker .hit{color:var(--green);font-weight:700}.btn.sm{padding:6px 12px;font-size:13px}.btn.sm svg{width:12px;height:12px}
.c.pop{box-shadow:0 0 0 3px rgba(22,163,74,.35),0 12px 30px rgba(22,163,74,.18);transform:translateY(-2px)}
.c .links{display:flex;flex-wrap:wrap;gap:6px 16px;font-size:13.5px;font-weight:600;margin-top:6px}
.ticker{height:24px;margin-top:6px;color:var(--soft);font-size:14px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ticker b{color:var(--faint);font-weight:650;margin-right:8px;font-variant-numeric:tabular-nums}
.cards{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.c{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:20px 20px 16px;position:relative;transition:opacity .4s,transform .4s}
.c.dim{opacity:.18;transform:scale(.985)}.c .hd{display:flex;gap:14px;align-items:flex-start}.c .who-co{flex:1;min-width:0}
.c .co{font-size:18px;font-weight:760;letter-spacing:-.01em}.c .st{font-size:13.5px;color:var(--mute)}
.ring{width:66px;height:66px;flex:0 0 66px;position:relative}.ring svg{width:66px;height:66px;transform:rotate(-90deg)}
.ring b{position:absolute;inset:0;display:grid;place-items:center;font-size:14px;font-weight:750}
.c .amt{font-size:34px;font-weight:820;letter-spacing:-.035em;margin:12px 0 2px;line-height:1}.c .amt small{font-size:14px;font-weight:550;color:var(--mute);letter-spacing:0;margin-left:6px}
.chip{display:flex;align-items:center;gap:10px;margin-top:12px;font-size:14px;color:var(--soft)}
.chip .cal{flex:0 0 46px;height:50px;border:1px solid var(--line);border-radius:10px;overflow:hidden;text-align:center;background:#fff}
.chip .cal i{display:block;background:#ef4444;color:#fff;font-style:normal;font-size:10px;font-weight:750;letter-spacing:.06em;padding:2px 0}
.chip .cal b{display:block;font-size:19px;line-height:1.35}.chip .ev{min-width:0}.chip .ev small{display:block;color:var(--mute);font-size:12.5px}
.av{flex:0 0 40px;width:40px;height:40px;border-radius:50%;display:grid;place-items:center;font-weight:750;font-size:14px;color:#fff;background:linear-gradient(135deg,#6366f1,#2563eb)}
.c details{margin-top:12px}.c summary{cursor:pointer;color:var(--blue);font-weight:600;font-size:13.5px;list-style:none}.c summary::-webkit-details-marker{display:none}
.c blockquote{margin:10px 0 0;padding:12px 14px;background:#f8fafc;border-left:3px solid var(--blue);border-radius:0 10px 10px 0;font-size:14px;color:var(--ink)}
.c .num{display:inline-grid;place-items:center;width:22px;height:22px;border-radius:50%;background:var(--green);color:#fff;font-size:12px;font-weight:750;
margin-right:9px;vertical-align:2px}.c .cf{margin-top:12px;display:flex;flex-direction:column;gap:2px}.c .cf>span{font-size:12.5px;color:var(--faint);font-weight:600}
.c .cf details{margin-top:4px}.c .multi{display:table;margin:5px 0 0;font-size:11.5px;font-weight:650;color:var(--green);
background:var(--greenbg);border-radius:99px;padding:0 8px;line-height:19px;vertical-align:1px}
.hs{display:grid;grid-template-columns:1.25fr 1fr;gap:18px;align-items:start}
.mock{background:var(--card);border:1px solid var(--line);border-radius:18px;overflow:hidden;position:relative}
.mock .bar{display:flex;align-items:center;gap:8px;padding:10px 14px;border-bottom:1px solid var(--line);background:#fbfbfc;font-size:12.5px;color:var(--mute);font-weight:600}
.mock .bar i{width:9px;height:9px;border-radius:50%;background:#e2e8f0;display:inline-block}.mock .bar .hsdot{background:var(--hs)}
.board{display:grid;grid-template-columns:1.5fr 1fr 1fr;gap:10px;padding:14px}.pin.sm{display:inline-grid;width:20px;height:20px;font-size:11px;
margin-right:8px;vertical-align:1px;box-shadow:none;position:static}.field .ind{padding-left:28px}.col h4{align-items:center}.cnt{display:inline-block;margin-left:7px;background:#eef0f3;color:var(--mute);
border-radius:99px;padding:0 7px;font-size:11px;line-height:18px}
.col h4{margin:0 0 8px;font-size:12px;color:var(--mute);text-transform:uppercase;letter-spacing:.07em;display:flex;justify-content:space-between}
.deal{background:#fff;border:1px solid var(--line);border-left:3px solid var(--hs);border-radius:10px;padding:8px 10px;margin-bottom:8px;font-size:12.5px;box-shadow:0 1px 2px rgba(15,23,42,.04)}
.deal b{display:block;font-size:13px}.deal span{color:var(--mute)}.col.empty .ph{border:1.5px dashed var(--line);border-radius:10px;height:54px;margin-bottom:8px}
.rec{padding:14px 16px}.rec .nm{font-weight:750;font-size:15px;margin-bottom:10px;display:flex;align-items:center;gap:8px}
.rec .nm i{width:28px;height:28px;border-radius:8px;background:var(--hsbg);color:var(--hs);display:grid;place-items:center;font-style:normal;font-size:13px;font-weight:800}
.field{display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-top:1px solid var(--line);font-size:13px;color:var(--mute);gap:10px}
.field b{color:var(--ink);font-weight:600;text-align:right}.sel{border:1px solid var(--line);border-radius:7px;padding:3px 9px;background:#fff;color:var(--ink);font-weight:600}
.ok{color:var(--green)!important}.note{margin-top:10px;background:#fafafa;border:1px solid var(--line);border-radius:10px;padding:9px 11px;font-size:12.5px;color:var(--soft)}
.note small{display:block;color:var(--faint);font-size:11.5px;margin-bottom:2px}
.pin{position:absolute;width:26px;height:26px;border-radius:50%;background:var(--ink);color:#fff;display:grid;place-items:center;font-size:13px;font-weight:750;box-shadow:0 0 0 4px rgba(15,23,42,.12)}
.steps3{display:grid;gap:12px;margin-top:16px}.s3{display:flex;gap:12px;align-items:flex-start}.s3 .pin{position:static;flex:0 0 26px}
.s3 b{display:block;font-size:15px}.s3 span{color:var(--mute);font-size:14px}
.more{display:flex;gap:12px;flex-wrap:wrap}.more details{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:12px 16px;flex:1;min-width:280px}
.more summary{cursor:pointer;font-weight:650;font-size:14.5px;list-style:none}.more summary::-webkit-details-marker{display:none}
.more summary span{color:var(--mute);font-weight:500}.more ul{margin:10px 0 0;padding:0;list-style:none}
.more li{font-size:13.5px;color:var(--soft);padding:7px 0;border-top:1px solid var(--line)}.more li:first-child{border-top:0}
footer{padding:34px 0 60px;color:var(--faint);font-size:13.5px;text-align:center}footer a{color:var(--mute)}
@media(max-width:900px){.flow{grid-template-columns:1fr 1fr}.node:nth-child(2):after{display:none}.tiles{grid-template-columns:repeat(3,1fr)}
.cards,.hs{grid-template-columns:1fr}}
@media(max-width:560px){.hero h1{font-size:32px}.flow{grid-template-columns:1fr}.node:after{display:none!important}.tiles{grid-template-columns:1fr 1fr}
.wrap{padding:0 16px}.board{grid-template-columns:1fr 1fr}.board .col:last-child{display:none}}
"""

ICONS = {
    "ask": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 5h16v11H9l-5 4z"/><path d="M9 10h6"/></svg>',
    "research": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5M8 11h6M11 8v6"/></svg>',
    "check": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"/><path d="m9 12 2 2 4-4"/></svg>',
    "deliver": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="7" width="18" height="13" rx="2"/><path d="M8 7V5a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M3 13h18"/></svg>',
    "play": '<svg viewBox="0 0 24 24" fill="currentColor" width="14" height="14"><path d="M7 5v14l12-7z"/></svg>',
    "pause": '<svg viewBox="0 0 24 24" fill="currentColor" width="14" height="14"><rect x="6" y="5" width="4" height="14" rx="1"/><rect x="14" y="5" width="4" height="14" rx="1"/></svg>',
}

PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>distress-signals: one research run, explained</title><link rel="icon" href="__FAVICON__"><style>__STYLE__</style></head><body>
<div class="wrap">
<nav><a class="brand" href="__REPO__"><img src="__FAVICON__" alt="">distress-signals</a><span class="sp"></span><a class="btn" href="__REPO__">View on GitHub</a></nav>

<header class="hero">
  <p class="eyebrow">One real run · <span id="date"></span></p>
  <h1>Find the companies with revenue riding on an event they don't control</h1>
  <p>An AI researcher digs through public records, works out what's at stake, and puts the person to call in your CRM.</p>
  <div class="ctas"><button class="btn primary" id="play">__PLAY__ Replay the run</button><a class="btn" href="run.html">Explore the full run</a></div>
</header>

<section id="how"><h2>How it works</h2><p class="lead">Four steps, from a question to a name and a number.</p>
  <div class="flow">
    <div class="node"><span class="n">1</span><div class="ic" style="background:var(--hsbg);color:var(--hs)">__ASK__</div><h3>Ask</h3><p>Request a run from any company in HubSpot</p></div>
    <div class="node"><span class="n">2</span><div class="ic" style="background:var(--bluebg);color:var(--blue)">__RESEARCH__</div><h3>Research</h3><p>SEC filings, federal rules, government contracts and payments, the web</p></div>
    <div class="node"><span class="n">3</span><div class="ic" style="background:var(--violetbg);color:var(--violet)">__CHECK__</div><h3>Check</h3><p>Every figure is quoted, calculated, or marked as an assumption</p></div>
    <div class="node"><span class="n">4</span><div class="ic" style="background:var(--greenbg);color:var(--green)">__DELIVER__</div><h3>Deliver</h3><p>Each exposure lands as a deal: what's at stake, who to call, an opening line</p></div>
  </div>
  <div class="loop"><svg viewBox="0 0 120 26" fill="none" stroke="#cbd5e1" stroke-width="2"><path d="M114 4v6a8 8 0 0 1-8 8H14a8 8 0 0 1-8-8V4"/><path d="m2 8 4-5 4 5"/></svg>
    Outcomes from your conversations feed the next run</div>
</section>

<section id="run"><h2>This run</h2><p class="lead" id="assign"></p>
  <div class="tiles">
    <div class="tile"><b id="k-min">0</b><span>minutes</span></div><div class="tile"><b id="k-search">0</b><span>searches</span></div>
    <div class="tile"><b id="k-doc">0</b><span>documents read</span></div><div class="tile"><b id="k-calc">0</b><span>calculations</span></div>
    <div class="tile"><b id="k-exp">0</b><span>revenue exposures</span></div><div class="tile"><b id="k-co">0</b><span>companies</span></div></div>
  <div class="chart"><div class="top"><h3>What it did, minute by minute</h3><button class="btn sm" id="cplay"></button><span class="sp"></span>
    <div class="legend"><span><i style="background:var(--blue)"></i>searches</span><span><i style="background:var(--slate)"></i>reading</span><span><i style="background:var(--violet)"></i>calculations</span><span><i style="background:var(--green)"></i>exposure found</span></div></div>
    <svg id="chart" style="width:100%;height:230px;display:block"></svg>
    <div class="prog"><i id="prog"></i></div><div class="ticker" id="ticker"><b id="tmin"></b><span id="tlabel"></span></div></div>
</section>

<section id="found"><h2>What it found</h2><p class="lead" id="foundlead"></p>
  <div class="cards" id="cards"></div></section>

<section id="hubspot"><h2>Where it shows up</h2><p class="lead">You work in HubSpot. This is a sketch of what you'd see.</p>
  <div class="hs">
    <div class="mock"><div class="bar"><i class="hsdot"></i><i></i><i></i><span>Deals · Sales pipeline</span></div>
      <div class="board" id="board"></div></div>
    <div><div class="mock"><div class="bar"><i class="hsdot"></i><i></i><i></i><span>Company record</span></div>
      <div class="rec"><div class="nm"><i>C</i>Any company you keep for research</div>
        <div class="field"><span><span class="pin sm">1</span>Distress run</span><span class="sel">Run the open search ▾</span></div>
        <div class="field"><span><span class="pin sm">2</span>Distress run status</span><b class="ok">Done ✓</b></div>
        <div class="field"><span class="ind">Distress run progress</span><b id="m-progress"></b></div>
        <div class="note"><small>Activity · a note each minute while it works</small><span id="m-note"></span></div></div></div>
      <div class="steps3">
        <div class="s3"><span class="pin">1</span><div><b>Ask</b><span>Set Distress run on any company: research it, an event, or run an open search</span></div></div>
        <div class="s3"><span class="pin">2</span><div><b>Follow</b><span>Notes stream in while it works, with a link to watch live</span></div></div>
        <div class="s3"><span class="pin">3</span><div><b>Act</b><span>Each exposure arrives as a deal in Proposed, ready to work</span></div></div></div></div>
  </div></section>

<section id="extra" style="padding-top:10px"><div class="more" id="more"></div></section>
<footer>Made with <a href="__REPO__">distress-signals</a> · <a href="run.html">the full run</a></footer>
</div>
<script>
__FOUNDJS__
__CHARTJS__
const PACE = __PACE__, PLAY = `__PLAY__`, PAUSE = `__PAUSE__`, D = __DATA__;
const $ = s => document.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const money = (x, c) => x == null ? "" : ((c && c !== "USD") ? c + " " : "$") + (Math.abs(x) >= 1e9 ? (x/1e9).toFixed(1) + "B" :
  Math.abs(x) >= 1e6 ? (x/1e6).toFixed(1) + "M" : Math.abs(x) >= 1e3 ? Math.round(x/1e3) + "K" : Math.round(x));
const MON = ["JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"];
$("#date").textContent = D.date; $("#assign").textContent = D.assignment ? "The assignment: " + D.assignment : "";
const FLAGS = D.cases.map((c, i) => ({t: c.found_at, n: i + 1}));
const draw = upto => drawChart($("#chart"), D.bins, FLAGS, upto, 230);
function setTiles(t) { const sum = k => Math.round(D.bins.reduce((a, b, i) => a + b[k] * Math.max(0, Math.min(1, (t - i * 60) / 60)), 0));
  $("#k-min").textContent = Math.min(D.minutes, Math.floor(t / 60)); $("#k-search").textContent = sum("search");
  $("#k-doc").textContent = sum("read"); $("#k-calc").textContent = sum("calc"); }
function setFound(t) { const {n, k} = tally(D.cases.filter(c => c.found_at <= t)); $("#k-exp").textContent = n; $("#k-co").textContent = k; }
const companyKey = c => c.company_id || c.company;
function ring(share) { if (share == null) return ""; const r = 28, c = 2 * Math.PI * r, p = Math.max(0, Math.min(1, share));
  return `<div class="ring"><svg viewBox="0 0 66 66"><circle cx="33" cy="33" r="${r}" fill="none" stroke="#eef0f3" stroke-width="7"/>`
    + `<circle cx="33" cy="33" r="${r}" fill="none" stroke="#16a34a" stroke-width="7" stroke-linecap="round" stroke-dasharray="${c * p} ${c}"/></svg><b>${Math.round(p * 100)}%</b></div>`; }
function cards() {
  const per = {}; D.cases.forEach(c => (per[companyKey(c)] ??= []).push(c));
  $("#foundlead").innerHTML = D.cases.length ? `<b>${esc(foundLine(D.cases).replace(/^./, s => s.toUpperCase()))}.</b> Each card is one revenue stream `
    + `riding on a dated event, with the amount at stake and the person to talk to.` : "Nothing met the bar in this run.";
  $("#cards").innerHTML = D.cases.map((c, i) => { const d = c.event_date ? new Date(c.event_date + "T12:00:00") : null, mine = per[companyKey(c)];
    return `<article class="c" data-i="${i}">`
      + `<div class="hd"><div class="who-co"><div class="co"><span class="num">${i + 1}</span>${esc(c.company)}</div><div class="st">${esc(c.stream)}${mine.length > 1 ? `<span class="multi">${mine.indexOf(c) + 1} of ${mine.length} at this company</span>` : ""}</div></div>${ring(c.share)}</div>`
      + `<div class="amt">${money(c.stake, c.currency)}<small>${c.share != null ? "of revenue at stake" : "at stake"}</small></div>`
      + (d ? `<div class="chip"><div class="cal"><i>${MON[d.getMonth()]}</i><b>${d.getDate()}</b></div><div class="ev">${esc(c.event)}`
        + `<small>${c.days != null ? (c.days > 0 ? c.days + " days out" : c.days === 0 ? "that day" : Math.abs(c.days) + " days before") : ""}</small></div></div>` : "")
      + `<div class="chip"><span class="av">${esc(c.initials)}</span><div class="ev">${esc(c.who)}<small>who to call</small></div></div>`
      + `<div class="cf"><span>found ${Math.max(1, Math.round(c.found_at / 60))} min in</span>`
      + `<span class="links"><a href="run.html#case/${encodeURIComponent(c.oid)}">Read the full case →</a><a href="run.html#found/${encodeURIComponent(c.oid)}">How it found this</a></span>`
      + (c.opening ? `<details><summary>Read the opening message</summary><blockquote>${esc(c.opening)}</blockquote></details>` : "") + `</div>`
      + `</article>`; }).join("");
  setFound(Infinity);
}
function board() {
  const deals = D.cases.map(c => `<div class="deal"><b>${esc(c.company)}</b><span>${money(c.stake, c.currency)} · ${esc(c.stream)}</span></div>`).join("");
  $("#board").innerHTML = `<div class="col"><h4><span><span class="pin sm">3</span>Proposed<span class="cnt">${D.cases.length}</span></span></h4>${deals}</div>`
    + `<div class="col empty"><h4>Contacted</h4><div class="ph"></div></div><div class="col empty"><h4>Meeting booked</h4><div class="ph"></div></div>`;
  $("#m-progress").textContent = `Finished · ${D.minutes} min · ${foundLine(D.cases)}`;
  const firstFind = D.cases[0]; $("#m-note").textContent = firstFind ? `${Math.max(1, Math.round(firstFind.found_at / 60))} min in: found ${firstFind.company}, ${money(firstFind.stake, firstFind.currency)} at stake` : "";
}
function extras() {
  const parts = [];
  if (D.aside.length) parts.push(`<details><summary>Looked at and set aside <span>· ${D.aside.length}</span></summary><ul>${D.aside.map(t => `<li>${esc(t)}</li>`).join("")}</ul></details>`);
  if (D.leads.length) parts.push(`<details><summary>Worth a look next <span>· ${D.leads.length}</span></summary><ul>${D.leads.map(l => `<li><b>${esc(l.what)}</b>: ${esc(l.why)}</li>`).join("")}</ul></details>`);
  $("#more").innerHTML = parts.join("");
}
function finish() { draw(Infinity); setTiles(Infinity); setFound(Infinity); $("#prog").style.width = "0";
  $("#tmin").textContent = `${D.minutes} min`; $("#tlabel").innerHTML = `Done: found ${foundLine(D.cases)}${D.cost != null ? `, for $${D.cost.toFixed(2)} in AI usage` : ""}.`;
  document.querySelectorAll(".c").forEach(c => c.classList.remove("dim", "pop")); }

// The replay: the run's minutes at a steady rate, a hold at each finding, each ticker line up long enough to read
const RUN_S = Math.max(60, D.minutes * 60), BASE_MS = 1000 * Math.min(PACE.max, Math.max(PACE.min, D.minutes * PACE.per_minute));
const RATE = RUN_S / BASE_MS, HOLD = PACE.hold * 1000, STOPS = D.cases.map((c, i) => ({t: c.found_at, i})).sort((a, b) => a.t - b.t);
const TOTAL = BASE_MS + STOPS.length * HOLD, LENGTH = TOTAL < 50000 ? `${Math.round(TOTAL / 1000)} sec` : `about ${Math.max(1, Math.round(TOTAL / 60000))} min`;
function at(ms) { let acc = 0, prev = 0;
  for (const s of STOPS) { const seg = (s.t - prev) / RATE; if (ms < acc + seg) return {t: prev + (ms - acc) * RATE}; acc += seg;
    if (ms < acc + HOLD) return {t: s.t, stop: s.i}; acc += HOLD; prev = s.t; }
  return {t: Math.min(RUN_S, prev + (ms - acc) * RATE)}; }
const R = {state: "idle", ms: 0, last: 0, timer: 0, label: "", since: -1e9};
function show(now) {
  const {t, stop} = at(R.ms); draw(t); setTiles(t); setFound(t); $("#prog").style.width = (100 * R.ms / TOTAL) + "%";
  document.querySelectorAll(".c").forEach(el => { const i = +el.dataset.i; el.classList.toggle("dim", D.cases[i].found_at > t); el.classList.toggle("pop", stop === i); });
  $("#tmin").textContent = `${Math.floor(t / 60)} min`;
  let html = null, force = stop != null;
  if (force) { const c = D.cases[stop]; html = `<span class="hit">Found ${stop + 1}: ${esc(c.company)} · ${money(c.stake, c.currency)} at stake</span>`; }
  else { let cur = null; for (const x of D.ticker) { if (x.t <= t) cur = x; else break; } if (cur) html = esc(cur.label); }
  if (html != null && html !== R.label && (force || now - R.since >= PACE.label * 1000)) { R.label = html; R.since = now; $("#tlabel").innerHTML = html; }
}
function frame() { const now = performance.now(); R.ms = Math.min(TOTAL, R.ms + now - R.last); R.last = now; show(now);
  if (R.ms < TOTAL) R.timer = setTimeout(frame, 50); else { R.state = "idle"; finish(); ctl(); } }
function ctl() { $("#cplay").innerHTML = R.state === "playing" ? `${PAUSE} Pause` : R.state === "paused" ? `${PLAY} Resume` : `${PLAY} Replay · ${LENGTH}`; }
function play(restart) {
  if (R.state === "playing" && !restart) { clearTimeout(R.timer); R.state = "paused"; ctl(); return; }
  clearTimeout(R.timer); if (restart || R.state !== "paused") { R.ms = 0; R.label = ""; R.since = -1e9; $("#tlabel").textContent = ""; }
  R.state = "playing"; R.last = performance.now(); ctl(); frame();
}
$("#play").onclick = () => { document.getElementById("run").scrollIntoView({behavior: "smooth"}); play(true); };
$("#cplay").onclick = () => play(false);
window.addEventListener("resize", () => draw(R.state === "idle" ? Infinity : at(R.ms).t));
cards(); board(); extras(); finish(); ctl();
</script></body></html>"""


def export_html(run_id):
    data = build(run_id)
    page = (PAGE.replace("__STYLE__", STYLE).replace("__FOUNDJS__", runpage.FOUND_JS).replace("__CHARTJS__", runpage.CHART_JS)
            .replace("__FAVICON__", runpage.FAVICON).replace("__REPO__", watch.REPO).replace("__PACE__", json.dumps(PACE))
            .replace("__ASK__", ICONS["ask"]).replace("__RESEARCH__", ICONS["research"]).replace("__CHECK__", ICONS["check"])
            .replace("__DELIVER__", ICONS["deliver"]).replace("__PLAY__", ICONS["play"]).replace("__PAUSE__", ICONS["pause"]))
    return page.replace("__DATA__", json.dumps(data, default=str).replace("</", "<\\/"))


def export_guide(run_id, folder):
    """index.html (the visual guide) and run.html (the full research trail), ready for GitHub Pages."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "index.html").write_text(export_html(run_id))
    (folder / "run.html").write_text(watch.export_html(run_id, public=True, home="./"))
    (folder / ".nojekyll").write_text("")
    return folder
