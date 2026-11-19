"""A page for watching runs, served on this Mac: http://localhost:8790/ lists the runs, and /runs/<id>
shows one as it goes. Each step opens with what the model is thinking, with what it did folded beneath;
cases appear as they are saved, with the revenue at stake and a link to their deal in HubSpot. A finished
run replays the same way, with its summary and leads. It reads each run's event stream
(out/runs/<id>.jsonl) and the store, and nothing else.

export_html writes one run as a single self-contained page, with a replay button, for sharing; public=True
leaves out links into the operator's HubSpot.

  python -m distress_signals watch                                   (install-worker runs it as a service)
  python -m distress_signals export-run RUN_ID [--public] [--out FILE]
"""

import datetime as dt
import html
import json
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import db, hubspot
from .feed import _result_text
from .util import OUT, load_env

PORT = int(os.environ.get("DISTRESS_WATCH_PORT", "8790"))
REPO = "https://github.com/owassmer/distress-signals"
ICON = {"WebSearch": "search", "WebFetch": "web", "search_records": "search", "read_record": "read", "compute": "calc",
        "lookup": "lookup", "calculate_exposure": "calc", "record_finding": "finding", "save_opportunity": "save",
        "StructuredOutput": "answer"}
FINDING = {"dropped": "Set aside", "fact": "Noted a fact", "inference": "Drew an inference", "conclusion": "Concluded",
           "question": "Noted an open question"}
ACRONYMS = {"us", "usa", "uk", "eu", "va", "dod", "hhs", "cms", "fda", "sba", "nasa", "faa", "fcc", "ftc", "doe", "epa",
            "irs", "dhs", "usda", "snap", "nfip", "aca", "ira", "pjm", "ercot", "mde", "thc", "cbd", "ai", "ev", "fy",
            "mlb", "nfl", "nba", "nhl", "lng", "ip", "saas", "b2b", "rd", "sec", "cr", "bpa", "idiq"}
SMALL = {"and", "or", "of", "for", "the", "to", "in", "on", "from", "by", "at", "a", "an", "with"}


def url(run_id=None):
    return f"http://localhost:{PORT}/" + (f"runs/{run_id}" if run_id else "")


def _domain(u):
    d = urlparse(u or "").netloc
    return d[4:] if d.startswith("www.") else d


def title_label(slug):
    """A stream_key or similar slug as a short title."""
    words = [w for w in re.split(r"[-_]+", slug or "") if w]
    return " ".join(w.upper() if w in ACRONYMS else w if (i and w in SMALL) else w.capitalize() for i, w in enumerate(words))


def _company_name(ref):
    """A company given in full, by name, or by its saved ID, as its name."""
    if isinstance(ref, dict):
        return ref.get("name")
    if isinstance(ref, str) and ref.startswith("co_"):
        try:
            con = db.connect()
            row = db.get_company(con, ref)
            con.close()
            return row["name"] if row else ref
        except Exception:
            return ref
    return ref


def _quoted(terms):
    return ", ".join(f"“{t}”" for t in terms[:4]) + (" …" if len(terms) > 4 else "")


def _where(ref):
    return (_domain(ref) if ref.startswith("http") else "the company's SEC record" if ref.startswith("sec:")
            else "a federal award" if ref.startswith("usaspending:") else "a payment ledger" if ref.startswith("ledger")
            else "a record it had read")


def describe(name, i):
    """What a tool call does, in plain words: label, optional text, link and details."""
    if name == "WebSearch":
        return {"label": f"Searched the web for “{i.get('query')}”"}
    if name == "WebFetch":
        return {"label": f"Read a page on {_domain(i.get('url'))}", "link": i.get("url")}
    if name == "search_records":
        src, q, f = i.get("source"), i.get("query"), i.get("filters") or {}
        if src == "filings":
            return {"label": f"Searched the full text of SEC filings for “{q}”"}
        if src == "federal_register":
            when = " taking effect " + " to ".join(x for x in (f.get("effective_from"), f.get("effective_until")) if x) \
                if f.get("effective_from") or f.get("effective_until") else ""
            kind = {"rule": "rules", "proposed_rule": "proposed rules", "notice": "notices"}.get(f.get("type"), "documents")
            return {"label": f"Searched the Federal Register for {kind}{when}" + (f" matching “{q}”" if q else "")}
        if src == "sec":
            return {"label": f"Looked up “{q}” in SEC company records"}
        if src == "usaspending":
            return {"label": f"Searched federal awards to “{q}”"}
        if src == "ledgers":
            return {"label": f"Searched state and local government payments to “{q}”"}
        return {"label": f"Searched {src} for “{q}”"}
    if name == "read_record":
        ref = str(i.get("id_or_url") or "")
        terms = [t.strip() for t in (i.get("find") or "").split("|") if t.strip()]
        where = _where(ref)
        return {"label": f"Looked in {where} for {_quoted(terms)}" if terms else f"Read {where}",
                "link": ref if ref.startswith("http") else None}
    if name == "compute":
        saved = i.get("save_as")
        return {"label": f"Calculated “{title_label(saved)}” from the records it had read" if saved
                else "Queried the records it had read", "text": i.get("note"), "details": [i.get("sql")] if i.get("sql") else []}
    if name == "lookup":
        return {"label": f"Checked saved research for “{i.get('id_or_query')}”"}
    if name == "calculate_exposure":
        return {"label": "Worked out the revenue at stake for a draft case"}
    if name == "record_finding":
        f = i.get("finding") or {}
        return {"label": "Withdrew a case" if f.get("withdraw_case") else FINDING.get(f.get("kind"), "Recorded a finding"),
                "text": f.get("statement"), "finding": f.get("kind")}
    if name == "save_opportunity":
        o = i.get("opportunity") or {}
        return {"label": f"Saving the case for {_company_name(o.get('company'))}", "text": title_label(o.get("stream_key"))}
    if name == "StructuredOutput":
        return {"label": "Wrote up the final answer"}
    return {"label": name}


def outcome(name, i, text, is_error):
    """What came back, in plain words: status (ok, blocked or refused) and whatever is worth showing."""
    if is_error:
        t = text.replace("Error executing tool ", "")
        t = re.sub(r"^[a-z_]+: ", "", t)
        if name == "lookup" and "Nothing saved is named" in t:
            return {"status": "ok", "meta": "nothing saved under that name"}
        if re.search(r"answered (401|403|429)", t):
            return {"status": "blocked", "meta": "the site blocked automated reading"}
        if "couldn't fetch" in t or "Timeout" in t or "timed out" in t:
            return {"status": "blocked", "meta": "the site didn't respond"}
        return {"status": "refused", "meta": t.split(". ")[0][:260]}
    try:
        out = json.loads(text)
    except ValueError:
        out = None
    if name == "WebSearch":
        m = re.search(r"Links: (\[.*?\])\s*\n", text, re.S)
        links = []
        if m:
            try:
                links = [{"title": x.get("title") or x.get("url"), "url": x.get("url")} for x in json.loads(m.group(1))][:6]
            except ValueError:
                pass
        return {"status": "ok", "meta": f"{len(links)} results" if links else None, "details": links}
    if not isinstance(out, dict):
        return {"status": "ok"}
    if name == "read_record":
        ref = str(i.get("id_or_url") or "")
        title = (out.get("title") or "").strip()
        terms = [t.strip() for t in (i.get("find") or "").split("|") if t.strip()]
        site = _domain(out.get("url") or ref)
        if not title:
            return {"status": "ok", "link": out.get("url")}
        short = title if len(title) <= 90 else title[:88] + "…"
        found = len(out.get("passages") or out.get("matches") or [])
        return {"status": "ok", "link": out.get("url") or (ref if ref.startswith("http") else None),
                "label": f"Looked in “{short}” for {_quoted(terms)}" if terms else f"Read “{short}”",
                "meta": ", ".join(x for x in (site, f"{found} passage{'s' if found != 1 else ''}" if terms else None) if x)}
    if name == "search_records":
        n = out.get("count", len(out.get("results") or []))
        return {"status": "ok", "meta": f"{n} found" + (f" of {out['total_matches']}" if out.get("total_matches") and out["total_matches"] > n else "")}
    if name == "compute":
        n = out.get("row_count", len(out.get("rows") or []))
        return {"status": "ok", "meta": f"{n} row{'s' if n != 1 else ''}"}
    if name == "lookup":
        if out.get("companies") is not None:
            cs = out["companies"]
            k = sum(len(c.get("cases") or []) for c in cs)
            return {"status": "ok", "meta": f"found {len(cs)} compan{'y' if len(cs) == 1 else 'ies'}, {k} case{'s' if k != 1 else ''}"}
        if out.get("event_id"):
            k = len(out.get("cases") or [])
            return {"status": "ok", "meta": f"found the event, {k} case{'s' if k != 1 else ''}"}
        if out.get("opportunity"):
            return {"status": "ok", "meta": "found the case"}
        return {"status": "ok", "meta": "found it"}
    if name == "calculate_exposure":
        res = out.get("results") or {}
        return {"status": "ok", "meta": f"up to {_money(res.get('max_shortfall'))} at stake" if res.get("max_shortfall") else None}
    if name == "save_opportunity":
        o, res = i.get("opportunity") or {}, out.get("results") or {}
        company = _company_name(out.get("company_id") or o.get("company"))
        return {"status": "ok", "label": f"Saved the case for {company}", "case": {
            "oid": out.get("opportunity_id"), "company": company, "stream": title_label(o.get("stream_key")),
            "detail": o.get("stream"), "revision": out.get("revision"), "stake": res.get("max_shortfall"),
            "share": res.get("max_share"), "of": (res.get("denominator") or {}).get("label"),
            "currency": o.get("currency"), "deal": _deal_url(out.get("opportunity_id"))}}
    return {"status": "ok"}


def _money(x):
    if x is None:
        return ""
    for div, unit in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(x) >= div:
            return f"${x / div:,.1f}{unit}"
    return f"${x:,.0f}"


def _deal_url(opportunity_id):
    try:
        con = db.connect()
        d = db.get_delivery(con, hubspot.DEST, "deal", opportunity_id)
        con.close()
        return hubspot.record_url("deals", d["external_id"]) if d and hubspot.configured() else None
    except Exception:
        return None


def _ts(s):
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None


class RunView:
    """A run's events as operations for the page: add an item, or update one when its result arrives."""

    def __init__(self, run_id):
        self.path = OUT / "runs" / f"{run_id}.jsonl"
        self.offset, self.ops, self.n, self.uses = 0, [], 0, {}
        self.calls, self.cases, self.done, self.cost, self.t0, self.last, self.duration = 0, set(), False, None, None, None, None
        self.lock = threading.Lock()

    def update(self):
        with self.lock:
            if not self.path.exists():
                return
            with open(self.path, "rb") as f:
                f.seek(self.offset)
                chunk = f.read()
            end = chunk.rfind(b"\n")
            if end < 0:
                return
            self.offset += end + 1
            for line in chunk[:end].splitlines():
                try:
                    self._event(json.loads(line))
                except (ValueError, KeyError, TypeError, AttributeError):
                    continue

    def _add(self, item):
        self.ops.append({"op": "add", "item": item})
        self.n += 1
        return self.n - 1

    def _event(self, e):
        t = e.get("type")
        if t == "assistant":
            at = _ts(e.get("timestamp"))
            if at:
                self.t0 = self.t0 or at
                self.last = at
            el = int((at - self.t0).total_seconds()) if at and self.t0 else None
            for b in e["message"]["content"]:
                kind = b.get("type")
                if kind == "thinking" and (b.get("thinking") or "").strip():
                    self._add({"k": "think", "t": b["thinking"].strip(), "el": el})
                elif kind == "text" and (b.get("text") or "").strip():
                    self._add({"k": "say", "t": b["text"].strip(), "el": el})
                elif kind == "tool_use":
                    name, i = b["name"].replace("mcp__ds__", ""), b.get("input") or {}
                    self.calls += 1
                    if name == "StructuredOutput":
                        self._add({"k": "final", "summary": i.get("summary"), "leads": i.get("follow_on_leads") or []})
                    self.uses[b["id"]] = (self._add({"k": "act", "tool": name, "icon": ICON.get(name, "lookup"), "el": el,
                                                     **describe(name, i)}), name, i)
        elif t == "user" and isinstance(e["message"].get("content"), list):
            for b in e["message"]["content"]:
                if b.get("type") == "tool_result" and b.get("tool_use_id") in self.uses:
                    idx, name, i = self.uses[b["tool_use_id"]]
                    fields = outcome(name, i, _result_text(b), b.get("is_error"))
                    if fields.get("case"):
                        self.cases.add(fields["case"]["oid"])
                    self.ops.append({"op": "update", "i": idx, "fields": {k: v for k, v in fields.items() if v is not None}})
        elif t == "system" and e.get("subtype") == "compact_boundary":
            self._add({"k": "note", "t": "Its context was compacted to make room."})
        elif t == "result":
            self.done, self.cost, self.duration = True, e.get("total_cost_usd"), e.get("duration_ms")
            self._add({"k": "end", "turns": e.get("num_turns"), "cost": self.cost, "duration_ms": self.duration,
                       "calls": self.calls, "cases": len(self.cases)})


_VIEWS, _LOCK, _REQUESTERS = {}, threading.Lock(), {}


def view(run_id):
    with _LOCK:
        if run_id not in _VIEWS:
            _VIEWS[run_id] = RunView(run_id)
    v = _VIEWS[run_id]
    v.update()
    return v


def _run_row(run_id):
    con = db.connect()
    try:
        r = con.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        return dict(r) if r else None
    finally:
        con.close()


def _requester(run_id):
    """The HubSpot record that asked for the run, if any."""
    if run_id not in _REQUESTERS and hubspot.configured():
        found = None
        for obj, name in (("companies", "name"), ("deals", "dealname")):
            try:
                x = hubspot._search(obj, [("distress_run_id", run_id)], [name])
            except Exception:
                x = None
            if x:
                found = {"name": x["properties"].get(name), "url": hubspot.record_url(obj, x["id"])}
                break
        _REQUESTERS[run_id] = found
    return _REQUESTERS.get(run_id)


def _status(run_id, run, v):
    if run is None:
        return {"state": "unknown"}
    state = "failed" if run.get("error") else ("finished" if run.get("finished") or v.done else "running")
    return {"state": state, "t0": v.t0.isoformat() if v.t0 else None, "duration_ms": v.duration,
            "last": v.last.isoformat() if v.last else None, "calls": v.calls, "cases": len(v.cases), "cost": v.cost,
            "error": (run.get("error") or "")[:300], "requester": _requester(run_id)}


def _title(run):
    if not run:
        return "Run", ""
    kind = run["seed_kind"]
    title = {"mandate": "The standing mandate", "company": f"Researching {run['seed']}", "event": "An event and the companies exposed to it",
             "brief": "A brief", "refresh": "Refreshing a case", "source": "From a source", "lead": f"Lead: {run['seed']}"}.get(kind, kind)
    sub = run["seed"] if kind in ("event", "brief", "source", "refresh") and run.get("seed") else ""
    if run.get("question"):
        sub = (sub + " · " if sub else "") + f"Note: {run['question']}"
    return title, sub


FAVICON = ("data:image/svg+xml," + "%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' "
           "rx='8' fill='%23111827'/%3E%3Cpath d='M7 20l5-6 4 4 9-10' stroke='%2334d399' stroke-width='3' fill='none' "
           "stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E")

STYLE = r"""
:root{--bg:#f5f6f8;--card:#fff;--line:#e6e8ec;--line2:#eef0f3;--ink:#0f172a;--soft:#334155;--mute:#64748b;--faint:#94a3b8;
--blue:#2563eb;--bluebg:#eff4ff;--green:#15803d;--greenbg:#f0fdf4;--greenline:#bbf7d0;--amber:#b45309}
*{box-sizing:border-box}[hidden]{display:none!important}html,body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.62 -apple-system,BlinkMacSystemFont,"Segoe UI",Inter,system-ui,sans-serif;-webkit-font-smoothing:antialiased}
a{color:var(--blue);text-decoration:none}a:hover{text-decoration:underline}
.top{position:sticky;top:0;z-index:5;background:rgba(245,246,248,.88);backdrop-filter:saturate(1.6) blur(12px);border-bottom:1px solid var(--line)}
.top .in{max-width:1200px;margin:0 auto;padding:14px 32px 10px;display:flex;align-items:center;gap:16px}
.brand{font-weight:700;font-size:13px;letter-spacing:.02em;color:var(--mute);display:flex;align-items:center;gap:8px;white-space:nowrap}
.brand img{width:20px;height:20px}.divider{width:1px;height:28px;background:var(--line)}
.ttl{font-weight:680;font-size:17px;letter-spacing:-.015em;display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.sub{color:var(--mute);font-size:13px;margin-top:1px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:640px}
.spacer{flex:1}.btn{border:1px solid var(--line);background:var(--card);color:var(--soft);border-radius:99px;padding:6px 14px;
font:600 13px/1 inherit;cursor:pointer;white-space:nowrap}.btn:hover{border-color:#cbd5e1}
.pill{display:inline-flex;align-items:center;gap:6px;font-size:12px;font-weight:650;padding:3px 10px;border-radius:99px;letter-spacing:.01em}
.pill.running{background:#dbeafe;color:#1d4ed8}.pill.finished{background:#dcfce7;color:var(--green)}
.pill.failed{background:#fee2e2;color:#b91c1c}.pill.replay{background:#ede9fe;color:#6d28d9}
.pulse{width:7px;height:7px;border-radius:50%;background:#2563eb;animation:pulse 1.4s infinite}
@keyframes pulse{0%{box-shadow:0 0 0 0 rgba(37,99,235,.55)}100%{box-shadow:0 0 0 9px rgba(37,99,235,0)}}
.now{max-width:1200px;margin:0 auto;padding:0 32px 11px;color:var(--soft);font-size:13.5px;display:flex;gap:10px;align-items:baseline}
.now b{color:var(--faint);font-weight:650;font-size:11px;text-transform:uppercase;letter-spacing:.08em}
.wrap{max-width:1200px;margin:0 auto;padding:26px 32px 40px;display:grid;grid-template-columns:minmax(0,1fr) 340px;gap:32px}
@media(max-width:1000px){.wrap{grid-template-columns:minmax(0,1fr)}aside{position:static!important;max-height:none!important}}
@media(max-width:640px){.top .in{padding:12px 16px 10px;gap:10px}.brand span,.divider{display:none}.ttl{font-size:15.5px}
.ask{max-width:calc(100vw - 150px)}.btn{padding:5px 11px;font-size:12px}.wrap{padding:18px 16px 30px;gap:22px}
.now{padding:0 16px 10px}.step{padding-left:24px}.end{margin-left:24px}footer{padding:6px 16px 40px}.case .stake{font-size:21px}}
.thought,.say,.act,.case,.mini,.aside-list li,.summary .body{overflow-wrap:anywhere}
.step{position:relative;padding:0 0 26px 30px}.step.new{animation:fade .4s ease}
.step:before{content:"";position:absolute;left:6px;top:16px;bottom:-2px;width:2px;background:var(--line)}
.step:last-of-type:before{display:none}
.step .dot{position:absolute;left:0;top:8px;width:14px;height:14px;border-radius:50%;background:var(--card);border:2px solid #cbd5e1}
.step.live .dot{border-color:var(--blue);background:#dbeafe}.step.has-case .dot{border-color:#22c55e;background:#dcfce7}
@keyframes fade{from{opacity:0;transform:translateY(5px)}to{opacity:1;transform:none}}
.when{font-size:11.5px;color:var(--faint);font-weight:600;letter-spacing:.03em;margin-bottom:2px;font-variant-numeric:tabular-nums}
.thought{color:var(--soft)}.thought p{margin:0 0 8px}
.thought.clamp{max-height:9.8em;overflow:hidden;-webkit-mask-image:linear-gradient(#000 65%,transparent)}
.more{border:0;background:none;color:var(--blue);font:600 12.5px/1 inherit;padding:0;margin:0 0 8px;cursor:pointer}
.say{margin:2px 0 10px;padding:9px 13px;background:var(--card);border:1px solid var(--line);border-left:3px solid var(--blue);
border-radius:9px;font-size:14px;color:var(--ink)}.say p{margin:0 0 6px}.say p:last-child{margin:0}
.toggle{display:inline-flex;flex-wrap:wrap;align-items:center;gap:7px;border:1px solid var(--line);background:var(--card);
border-radius:99px;padding:4px 13px;font:500 12.5px/1.6 inherit;color:var(--mute);cursor:pointer;margin-top:2px}
.toggle:hover{border-color:#cbd5e1;color:var(--soft)}.toggle svg{width:13px;height:13px;vertical-align:-2px;margin-right:3px}
.sep{color:#cbd5e1}.warn{color:var(--amber)}.chev{margin-left:2px;color:var(--faint);font-weight:600}
.acts ul{list-style:none;margin:10px 0 0 6px;padding:0;border-left:2px solid var(--line2)}
.act{display:flex;gap:10px;padding:5px 0 5px 13px;font-size:13.5px;color:var(--soft)}
.act svg{width:15px;height:15px;flex:0 0 15px;margin-top:3px;color:var(--faint)}
.act a{color:var(--soft)}.act a:hover{color:var(--blue)}
.act .meta{color:var(--faint);margin-left:6px;font-size:12.5px}.act.blocked{color:var(--faint)}.act.blocked a{color:var(--faint)}
.act.blocked .meta{color:var(--amber)}.act.refused .meta{color:#b91c1c}.act .atext{color:var(--mute);font-size:13px;margin-top:2px}
.det{margin-top:5px;display:flex;flex-direction:column;gap:3px}.det a{font-size:12.5px;color:var(--mute)}
.det pre{margin:4px 0 0;padding:9px 11px;background:#f8fafc;border:1px solid var(--line);border-radius:7px;font:11.5px/1.5 ui-monospace,Menlo,monospace;
white-space:pre-wrap;color:var(--mute);max-height:150px;overflow:auto}
.spin{display:inline-block;width:10px;height:10px;margin-left:8px;border:2px solid #cbd5e1;border-top-color:var(--blue);
border-radius:50%;animation:spin .8s linear infinite;vertical-align:-1px}@keyframes spin{to{transform:rotate(360deg)}}
.case{background:var(--greenbg);border:1px solid var(--greenline);border-radius:13px;padding:13px 16px;margin:12px 0 2px}
.case .k{font-size:10.5px;text-transform:uppercase;letter-spacing:.08em;color:var(--green);font-weight:750}
.case .co{font-weight:680;font-size:15.5px;margin-top:3px}.case .st{font-size:13px;color:var(--soft);font-weight:550}
.case .stake{font-size:24px;font-weight:750;letter-spacing:-.02em;margin-top:6px;line-height:1.2}
.case .share{font-size:13px;font-weight:500;color:var(--mute)}.case .of{font-size:12.5px;color:var(--mute);margin-top:1px}.case .detail{font-size:13px;color:var(--mute);margin-top:4px}
.case a{display:inline-block;margin-top:8px;font-size:13px;font-weight:650}
.end{margin:4px 0 0 30px;padding:16px 18px;border-radius:13px;background:var(--ink);color:#e2e8f0;font-size:14px}
.end b{color:#fff}
aside{position:sticky;top:96px;align-self:start;display:flex;flex-direction:column;gap:16px;max-height:calc(100vh - 110px);overflow:auto;padding-bottom:6px}
.card{background:var(--card);border:1px solid var(--line);border-radius:15px;padding:17px 19px}
.card h3{margin:0 0 11px;font-size:11.5px;text-transform:uppercase;letter-spacing:.08em;color:var(--mute);display:flex;justify-content:space-between;align-items:center}
.card h3 a{text-transform:none;letter-spacing:0;font-size:12.5px;font-weight:600}
.clock{font-size:32px;font-weight:760;letter-spacing:-.03em;font-variant-numeric:tabular-nums;line-height:1.1}
.stats{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:14px}.stats div{font-size:11.5px;color:var(--mute)}
.stats b{display:block;font-size:17px;color:var(--ink);font-variant-numeric:tabular-nums;font-weight:700}
.mini{padding:11px 0;border-top:1px solid var(--line2)}.mini:first-child{border-top:0;padding-top:0}
.mini .co{font-weight:650;font-size:14px}.mini .st{font-size:12.5px;color:var(--mute)}.mini .v{font-size:13px;color:var(--soft);margin-top:2px}
.mini a{font-size:12.5px;font-weight:600}.empty{color:var(--faint);font-size:13px}
.aside-list{margin:0;padding:0;list-style:none}.aside-list li{font-size:13px;color:var(--soft);padding:8px 0;border-top:1px solid var(--line2)}
.aside-list li:first-child{border-top:0;padding-top:0}.clamp3{display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
.ask{font-size:13px;color:var(--mute);margin-top:1px;max-width:720px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.summary .body p{margin:0 0 8px;font-size:13.5px;color:var(--soft)}.summary .body.clamp{max-height:16em;overflow:hidden;-webkit-mask-image:linear-gradient(#000 70%,transparent)}
#latest{position:fixed;bottom:24px;left:50%;transform:translateX(-50%);display:none;background:var(--ink);color:#fff;border:0;
border-radius:99px;padding:9px 18px;font:600 13px/1 inherit;cursor:pointer;box-shadow:0 8px 24px rgba(15,23,42,.22)}
footer{max-width:1200px;margin:0 auto;padding:6px 32px 50px;color:var(--faint);font-size:12.5px}footer a{color:var(--mute)}
.runs{max-width:920px;margin:0 auto;padding:28px 32px 80px}
.run{display:flex;align-items:center;gap:18px;background:var(--card);border:1px solid var(--line);border-radius:15px;padding:15px 20px;margin-bottom:10px;color:var(--ink)}
.run:hover{border-color:#cbd5e1;text-decoration:none;box-shadow:0 2px 10px rgba(15,23,42,.05)}.run .what{flex:1;min-width:0}
.run .what b{font-weight:650}.run .what div{color:var(--mute);font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.run .n{font-size:13px;color:var(--mute);text-align:right;min-width:150px;font-variant-numeric:tabular-nums}
"""

ICONS = {
    "search": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>',
    "read": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5M9 13h6M9 17h4"/></svg>',
    "web": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/></svg>',
    "calc": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><rect x="5" y="3" width="14" height="18" rx="2"/><path d="M8 7h8M8 12h2M14 12h2M8 16h2M14 16h2"/></svg>',
    "lookup": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><path d="M6 3h12v18l-6-4-6 4z"/></svg>',
    "finding": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><path d="M5 21V4h11l-1.5 4L16 12H5"/></svg>',
    "save": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/></svg>',
    "answer": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><path d="M4 5h16v11H8l-4 4z"/></svg>',
}

PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__ · distress-signals</title><link rel="icon" href="__FAVICON__"><style>__STYLE__</style></head><body>
<div class="top"><div class="in">
  <a class="brand" href="__HOME__"><img src="__FAVICON__" alt=""><span>distress-signals</span></a><div class="divider"></div>
  <div style="min-width:0"><div class="ttl">__TITLE__ <span id="pill" class="pill"></span></div><div class="ask" title="__ASKFULL__">__ASK__</div></div>
  <div class="spacer"></div><button class="btn" id="replay" hidden>▶ Replay</button></div>
  <div class="now" id="nowrow" hidden><b>Now</b><span id="now"></span></div></div>
<div class="wrap"><main id="timeline"></main>
<aside>
  <div class="card"><h3><span>This run</span><a id="req" target="_blank" hidden></a></h3><div class="clock" id="clock">0:00</div>
    <div class="stats"><div><b id="calls">0</b>tool calls</div><div><b id="ncases">0</b>cases saved</div>
    <div><b id="started">–</b>started</div><div><b id="cost">–</b>cost</div></div></div>
  <div class="card"><h3><span>Cases</span><span id="casecount"></span></h3><div id="cases"><div class="empty" id="nocases">None yet. Each case appears here as it's saved.</div></div></div>
  <div class="card" id="asidebox" hidden><h3>Set aside</h3><ul class="aside-list" id="aside"></ul></div>
  <div class="card summary" id="final" hidden><h3>The analyst's summary</h3><div class="body clamp" id="summary"></div>
    <button class="more" id="summore">Read the full summary</button><h3 style="margin-top:12px">Follow-on leads</h3><ul class="aside-list" id="leads"></ul></div>
</aside></div>
<footer>A run of <a href="__REPO__" target="_blank">distress-signals</a>: a researcher that finds companies whose revenue depends on an upcoming outside event, and the person to talk to.</footer>
<button id="latest">Jump to latest ↓</button>
<script>
const RUN = "__RUN__", ICONS = __ICONS__, DATA = __DATA__;
const NOUN = {search:["search","searches"],read:["read","reads"],web:["page","pages"],calc:["calculation","calculations"],
  lookup:["check","checks"],finding:["note","notes"],save:["case","cases"],answer:["answer","answers"]};
let after = 0, status = {}, cur = null, items = [], replaying = false;
const $ = s => document.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const inline = s => s.replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>").replace(/`([^`]+)`/g, "<code>$1</code>");
const paras = s => esc(s).split(/\n{2,}/).map(p => /^\s*[-•] /m.test(p) ? "<p>" + p.split("\n").map(l => inline(l.replace(/^\s*[-•] /, "• "))).join("<br>") + "</p>"
  : "<p>" + inline(p).replace(/\n/g, "<br>") + "</p>").join("");
const money = (x, c) => x == null ? "" : ((c && c !== "USD") ? c + " " : "$") + (Math.abs(x) >= 1e9 ? (x/1e9).toFixed(2) + "B" :
  Math.abs(x) >= 1e6 ? (x/1e6).toFixed(1) + "M" : Math.abs(x) >= 1e3 ? Math.round(x/1e3) + "K" : Math.round(x));
const mins = s => s == null ? "" : s < 60 ? "Start" : `${Math.round(s / 60)} min in`;
const dur = ms => { const s = Math.round(ms / 1000), h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60); return h ? `${h} h ${m} min` : `${m} min`; };
const nearBottom = () => window.innerHeight + window.scrollY >= document.body.scrollHeight - 160;

function newStep(text, el) {
  const s = document.createElement("section"); s.className = "step new live";
  s.innerHTML = `<div class="dot"></div><div class="when">${mins(el)}</div><div class="thought">${text ? paras(text) : "<p>Getting started.</p>"}</div>
    <div class="says"></div><div class="acts" hidden><button class="toggle"></button><ul></ul></div><div class="cards"></div>`;
  $("#timeline").appendChild(s);
  const th = s.querySelector(".thought");
  if (th.scrollHeight > 180) { th.classList.add("clamp"); const b = document.createElement("button"); b.className = "more";
    b.textContent = "Read more"; b.onclick = () => { th.classList.toggle("clamp"); b.textContent = th.classList.contains("clamp") ? "Read more" : "Show less"; }; th.after(b); }
  const st = {el: s, acts: [], open: true, manual: false};
  s.querySelector(".toggle").onclick = () => { st.open = !st.open; st.manual = true; draw(st); };
  if (cur) { cur.el.classList.remove("live"); if (!cur.manual) { cur.open = false; draw(cur); } }
  cur = st; return st;
}
function actHTML(a) {
  const label = a.link ? `<a href="${esc(a.link)}" target="_blank" rel="noopener">${esc(a.label)}</a>` : esc(a.label);
  const meta = a.meta ? `<span class="meta">${esc(a.meta)}</span>` : "";
  const det = (a.details || []).filter(Boolean).map(d => typeof d === "string" ? `<pre>${esc(d)}</pre>` :
    `<a href="${esc(d.url)}" target="_blank" rel="noopener">${esc(d.title)}</a>`).join("");
  return `<li class="act ${a.status || "pending"}">${ICONS[a.icon] || ""}<div><div>${label}${meta}${a.status ? "" : '<span class="spin"></span>'}</div>` +
    (a.text ? `<div class="atext">${esc(a.text)}</div>` : "") + (det ? `<div class="det">${det}</div>` : "") + "</div></li>";
}
function draw(st) {
  const box = st.el.querySelector(".acts"); box.hidden = !st.acts.length; if (!st.acts.length) return;
  const counts = {}; st.acts.forEach(a => counts[a.icon] = (counts[a.icon] || 0) + 1);
  const blocked = st.acts.filter(a => a.status === "blocked").length;
  box.querySelector(".toggle").innerHTML = Object.entries(counts).map(([k, n]) => `<span>${ICONS[k] || ""}${n} ${(NOUN[k] || [k, k])[n > 1 ? 1 : 0]}</span>`)
    .join('<span class="sep">·</span>') + (blocked ? `<span class="sep">·</span><span class="warn">${blocked} blocked</span>` : "") +
    `<span class="chev">${st.open ? "Hide" : "Show"}</span>`;
  const ul = box.querySelector("ul"); ul.hidden = !st.open; if (st.open) ul.innerHTML = st.acts.map(actHTML).join("");
}
function caseCard(c) {
  return `<div class="k">Case saved${c.revision > 1 ? " · revision " + c.revision : ""}</div><div class="co">${esc(c.company)}</div>` +
    (c.stream ? `<div class="st">${esc(c.stream)}</div>` : "") +
    (c.stake != null ? `<div class="stake">${money(c.stake, c.currency)} <span class="share">at stake${c.share != null ? ` · ${(100 * c.share).toFixed(1)}%` : ""}</span></div>` : "") +
    (c.share != null && c.of ? `<div class="of">of ${esc(c.of)}</div>` : "") +
    (c.deal ? `<a href="${esc(c.deal)}" target="_blank">Open the deal in HubSpot →</a>` : "");
}
function addCase(c) {
  const card = document.createElement("div"); card.className = "case"; card.innerHTML = caseCard(c);
  cur.el.querySelector(".cards").appendChild(card); cur.el.classList.add("has-case");
  let mini = document.getElementById("case-" + c.oid);
  if (!mini) { mini = document.createElement("div"); mini.className = "mini"; mini.id = "case-" + c.oid; $("#cases").appendChild(mini); }
  mini.innerHTML = `<div class="co">${esc(c.company)}</div>${c.stream ? `<div class="st">${esc(c.stream)}</div>` : ""}` +
    `<div class="v">${c.stake != null ? "<b>" + money(c.stake, c.currency) + "</b> at stake" : ""}${c.share != null ? " · " + (100 * c.share).toFixed(1) + "%" : ""}</div>` +
    (c.deal ? `<a href="${esc(c.deal)}" target="_blank">Open in HubSpot →</a>` : "");
  mini.onclick = e => { if (e.target.tagName !== "A") card.scrollIntoView({behavior: "smooth", block: "center"}); }; mini.style.cursor = "pointer";
  $("#nocases").hidden = true; $("#casecount").textContent = $("#cases").querySelectorAll(".mini").length;
}
function apply(op) {
  if (op.op === "add") {
    const it = op.item; items.push(it);
    if (it.k === "think") newStep(it.t, it.el);
    else if (it.k === "say" || it.k === "note") { if (!cur) newStep("", it.el); cur.el.querySelector(".says").insertAdjacentHTML("beforeend", `<div class="say">${paras(it.t)}</div>`); }
    else if (it.k === "act") { if (!cur) newStep("", it.el); it.step = cur; cur.acts.push(it); draw(cur); $("#now").textContent = it.label; }
    else if (it.k === "final") { $("#final").hidden = false; $("#summary").innerHTML = paras(it.summary || "");
      $("#leads").innerHTML = (it.leads || []).map(l => `<li><div class="clamp3"><b>${esc(l.company_or_event)}</b>: ${esc(l.reason)}</div></li>`).join("") || '<li class="empty">None</li>'; }
    else if (it.k === "end") { $("#timeline").insertAdjacentHTML("beforeend", `<div class="end"><b>Finished</b>` +
      `${it.duration_ms ? " in " + dur(it.duration_ms) : ""} · ${it.calls} tool calls · ${it.cases} case${it.cases === 1 ? "" : "s"}` +
      `${it.cost ? " · $" + it.cost.toFixed(2) : ""}</div>`); if (cur) cur.el.classList.remove("live"); }
  } else if (op.op === "update") {
    const it = items[op.i]; if (!it) return; Object.assign(it, op.fields);
    if (it.step) draw(it.step);
    if (op.fields.case) addCase(op.fields.case);
    if (it.tool === "record_finding" && it.finding === "dropped" && it.status === "ok") {
      $("#asidebox").hidden = false; $("#aside").insertAdjacentHTML("beforeend", `<li><div class="clamp3">${esc(it.text)}</div></li>`); }
  }
}
function tick() {
  if (!status.t0) return;
  const ms = status.duration_ms ?? (status.state === "running" ? Date.now() - Date.parse(status.t0) : Date.parse(status.last || status.t0) - Date.parse(status.t0));
  const sec = Math.max(0, Math.floor(ms / 1000)), h = Math.floor(sec / 3600), m = Math.floor(sec % 3600 / 60), ss = sec % 60;
  $("#clock").textContent = h ? `${h}:${String(m).padStart(2, "0")}:${String(ss).padStart(2, "0")}` : `${m}:${String(ss).padStart(2, "0")}`;
}
function showStatus(s) {
  status = s; const p = $("#pill");
  p.className = "pill " + (replaying ? "replay" : s.state);
  p.innerHTML = replaying ? "Replay" : s.state === "running" ? '<span class="pulse"></span>Live' : s.state === "finished" ? "Finished" : s.state === "failed" ? "Failed" : "";
  $("#calls").textContent = s.calls ?? 0; $("#ncases").textContent = s.cases ?? 0;
  $("#started").textContent = s.t0 ? new Date(s.t0).toLocaleString([], {month: "short", day: "numeric", hour: "numeric", minute: "2-digit"}) : "–";
  $("#cost").textContent = s.cost != null ? "$" + s.cost.toFixed(2) : "–";
  if (s.requester && s.requester.url) { $("#req").hidden = false; $("#req").textContent = "Open in HubSpot →"; $("#req").href = s.requester.url; $("#req").title = s.requester.name || ""; }
  $("#nowrow").hidden = s.state !== "running" || replaying;
  tick();
}
function reset() { $("#timeline").innerHTML = ""; $("#cases").innerHTML = '<div class="empty" id="nocases">None yet. Each case appears here as it\'s saved.</div>';
  $("#casecount").textContent = ""; $("#aside").innerHTML = ""; $("#asidebox").hidden = true; $("#final").hidden = true; items = []; cur = null; }
function replay() {
  replaying = true; reset(); const ops = DATA.ops, total = ops.length; let i = 0;
  const final = DATA.status; status = {...final}; showStatus({...final, calls: 0, cases: 0});
  (function step() {
    const n = Math.max(1, Math.round(total / 400)); for (let k = 0; k < n && i < total; k++) apply(ops[i++]);
    $("#calls").textContent = items.filter(x => x.k === "act").length; $("#ncases").textContent = $("#cases").querySelectorAll(".mini").length;
    window.scrollTo({top: document.body.scrollHeight});
    if (i < total) setTimeout(step, 45); else { replaying = false; showStatus(final); }
  })();
}
async function poll() {
  try {
    const follow = nearBottom() || after === 0;
    const j = await (await fetch(`/runs/${RUN}/ops?after=${after}`)).json();
    j.ops.forEach(apply); const first = after === 0; after = j.next; showStatus(j.status);
    if (j.ops.length) { if (follow) window.scrollTo({top: document.body.scrollHeight, behavior: first ? "auto" : "smooth"}); else $("#latest").style.display = "block"; }
    if (j.status.state === "running" || j.status.state === "unknown") setTimeout(poll, 2500);
  } catch (e) { setTimeout(poll, 5000); }
}
$("#latest").onclick = () => { window.scrollTo({top: document.body.scrollHeight, behavior: "smooth"}); $("#latest").style.display = "none"; };
$("#summore").onclick = () => { const b = $("#summary"); b.classList.toggle("clamp"); $("#summore").textContent = b.classList.contains("clamp") ? "Read the full summary" : "Show less"; };
window.addEventListener("scroll", () => { if (nearBottom()) $("#latest").style.display = "none"; });
setInterval(tick, 1000);
if (DATA) { DATA.ops.forEach(apply); showStatus(DATA.status); $("#replay").hidden = false; $("#replay").onclick = replay; window.scrollTo(0, 0); }
else poll();
</script></body></html>"""


def _asked(run):
    """What the run was asked, in a line: the mandate's first sentence, or the subject, brief or note."""
    if not run:
        return ""
    if run["seed_kind"] == "mandate":
        sysp = run.get("system") or ""
        body = sysp.split("## Mandate", 1)[-1].split("##", 1)[0] if "## Mandate" in sysp else ""
        first = re.split(r"(?<=\.)\s", " ".join(body.split()), 1)[0] if body.strip() else ""
        return f"Asked: {first}" if first else ""
    _, sub = _title(run)
    return sub


def _page(run_id, data=None, home="/"):
    run = _run_row(run_id)
    title, _ = _title(run)
    ask = _asked(run)
    return (PAGE.replace("__STYLE__", STYLE).replace("__ICONS__", json.dumps(ICONS)).replace("__RUN__", run_id)
            .replace("__FAVICON__", FAVICON).replace("__REPO__", REPO).replace("__HOME__", home)
            .replace("__DATA__", json.dumps(data, default=str).replace("</", "<\\/") if data is not None else "null")
            .replace("__TITLE__", html.escape(title)).replace("__ASKFULL__", html.escape(ask)).replace("__ASK__", html.escape(ask)))


def export_html(run_id, public=False):
    """One run as a single self-contained page: everything embedded, no server, with a replay button.
    public=True leaves out links into the operator's HubSpot."""
    v = RunView(run_id)
    v.update()
    status = _status(run_id, _run_row(run_id), v)
    ops = json.loads(json.dumps(v.ops, default=str))
    if public:
        status["requester"] = None
        for op in ops:
            case = (op.get("fields") or {}).get("case")
            if case:
                case.pop("deal", None)
    return _page(run_id, {"ops": ops, "status": status}, home=REPO)


def _index():
    con = db.connect()
    try:
        rows = [dict(r) for r in con.execute("SELECT * FROM runs ORDER BY started DESC LIMIT 100")]
    finally:
        con.close()
    cards = []
    for r in rows:
        state = "failed" if r["error"] else ("finished" if r["finished"] else "running")
        title, sub = _title(r)
        start = dt.datetime.fromisoformat(r["started"])
        end = dt.datetime.fromisoformat(r["finished"]) if r["finished"] else dt.datetime.now()
        mins = int((end - start).total_seconds() // 60)
        cost = (json.loads(r["stats"]).get("total_cost_usd") if r.get("stats") else None)
        n = len(json.loads(r["opportunity_ids"] or "[]"))
        pill = '<span class="pill running"><span class="pulse"></span>Live</span>' if state == "running" else \
            f'<span class="pill {state}">{state.capitalize()}</span>'
        cards.append(f"<a class='run' href='/runs/{r['id']}'><div class='what'><b>{html.escape(title)}</b>"
                     f"<div>{html.escape(sub or r['id'])}</div></div><div class='n'>{start.strftime('%b %-d, %-I:%M %p')}<br>"
                     f"{mins} min · {n} case{'s' if n != 1 else ''}{f' · ${cost:.2f}' if cost else ''}</div>{pill}</a>")
    body = "".join(cards) or "<div class='empty'>No runs yet. Request one in HubSpot with a record's Distress run field.</div>"
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'><title>Runs · distress-signals</title>"
            f"<link rel='icon' href=\"{FAVICON}\"><style>{STYLE}</style></head><body><div class='top'><div class='in'>"
            f"<span class='brand'><img src=\"{FAVICON}\" alt=''>distress-signals</span><div class='divider'></div>"
            f"<div><div class='ttl'>Runs</div><div class='sub'>Open one to watch it live, or replay a finished one.</div></div>"
            f"</div></div><div class='runs'>{body}</div></body></html>")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, body, kind="text/html; charset=utf-8", status=200):
        data = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urlparse(self.path)
        parts = [p for p in u.path.split("/") if p]
        try:
            if not parts:
                return self._send(_index())
            if parts[0] == "runs" and len(parts) >= 2 and parts[1].startswith("run_"):
                rid = parts[1]
                if len(parts) == 2:
                    return self._send(_page(rid))
                if parts[2] in ("ops", "events"):
                    after = int((parse_qs(u.query).get("after") or ["0"])[0])
                    v = view(rid)
                    return self._send(json.dumps({"ops": v.ops[after:], "next": len(v.ops),
                                                  "status": _status(rid, _run_row(rid), v)}, default=str), "application/json")
                if parts[2] == "export":
                    return self._send(export_html(rid, public="public" in parse_qs(u.query)))
            self._send("Not found", "text/plain", 404)
        except Exception as e:
            self._send(f"{type(e).__name__}: {e}", "text/plain", 500)


def serve():
    load_env()
    try:
        server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError as e:  # another program has the port: say so, and don't restart in a tight loop
        print(f"can't serve the watch page on port {PORT}: {e}. Set DISTRESS_WATCH_PORT in .env to a free port.", flush=True)
        import time
        time.sleep(300)
        raise SystemExit(1)
    print(f"watch page at {url()}", flush=True)
    server.serve_forever()
