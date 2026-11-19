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
                else "Ran the numbers on what it had read", "text": i.get("note"), "details": [i.get("sql")] if i.get("sql") else []}
    if name == "lookup":
        return {"label": f"Checked its earlier findings for “{i.get('id_or_query')}”"}
    if name == "calculate_exposure":
        return {"label": "Worked out what's at stake"}
    if name == "record_finding":
        f = i.get("finding") or {}
        return {"label": "Withdrew a case" if f.get("withdraw_case") else FINDING.get(f.get("kind"), "Recorded a finding"),
                "text": f.get("statement"), "finding": f.get("kind")}
    if name == "save_opportunity":
        o = i.get("opportunity") or {}
        return {"label": f"Writing up {_company_name(o.get('company'))}", "text": title_label(o.get("stream_key"))}
    if name == "StructuredOutput":
        return {"label": "Wrote up its findings"}
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
        return {"status": "ok", "label": f"Wrote up {company}", "case": {
            "oid": out.get("opportunity_id"), "company": company, "company_id": out.get("company_id"),
            "stream": title_label(o.get("stream_key")),
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
    """A plain title for the run, and a sentence saying what the page shows."""
    if not run:
        return "A research run", ""
    kind, seed = run["seed_kind"], run.get("seed") or ""
    title = {"mandate": "An open search", "company": f"Researching {seed}", "event": "Who is exposed to an event",
             "brief": "Answering a question", "refresh": "Re-checking a case", "source": "Starting from a source",
             "lead": f"Following a lead: {seed}"}.get(kind, "A research run")
    lede = {"mandate": "An AI researcher was asked to find companies whose revenue depends on an upcoming event they don't "
                       "control. This is one session, start to finish: what it found, and how it got there.",
            "company": f"An AI researcher was asked whether {seed}'s revenue depends on an upcoming event it doesn't control. "
                       "This is one session, start to finish: what it found, and how it got there.",
            "event": "An AI researcher was given an upcoming event and asked whose revenue depends on it. This is one "
                     "session, start to finish: what it found, and how it got there.",
            "refresh": "An AI researcher checked a saved case again against current sources."}.get(
        kind, "An AI researcher was given a question. This is one session, start to finish: what it found, and how it got there.")
    return title, lede


def _assignment(run):
    """What the researcher was given, in its own words: the mandate's first paragraph, or the subject or brief."""
    if not run:
        return ""
    if run["seed_kind"] == "mandate":
        sysp = run.get("system") or ""
        body = sysp.split("## Mandate", 1)[-1].split("##", 1)[0] if "## Mandate" in sysp else ""
        first = " ".join(body.strip().split("\n\n")[0].split())
        return re.split(r"(?<=\.)\s", first, 1)[0]
    text = run.get("seed") or ""
    return text + (f" (Note: {run['question']})" if run.get("question") else "")


def _case_details(oids, public=False):
    """The final form of the cases a run saved, for the findings cards."""
    if not oids:
        return {}
    from . import briefs
    out = {}
    con = db.connect()
    try:
        for c in briefs.cases(con, list(oids)):
            case, ev, res, buyer = c["case"], c["event"], c["results"], c["case"]["buyer"]
            out[c["id"]] = {
                "oid": c["id"], "status": c["status"], "company": c["company"]["name"],
                "company_id": c["row"]["company_id"], "stream": title_label(case.get("stream_key")),
                "event": ev.get("title"), "event_date": ev.get("date"), "dependency": case.get("dependency"),
                "why_now": case["case"].get("why_now"), "stake": res.get("max_shortfall"), "share": res.get("max_share"),
                "of": (res.get("denominator") or {}).get("label"), "currency": case.get("currency"),
                "period": f"{case['period']['start']} to {case['period']['end']}",
                "who": ", ".join(x for x in (buyer.get("name"), buyer.get("title")) if x) or buyer.get("role"),
                "opening": (case.get("outreach") or {}).get("draft"),
                "deal": None if public else _deal_url(c["id"])}
    finally:
        con.close()
    return out


# util.found_line for the pages: cases are [{company_id, company}], one per active case
FOUND_JS = r"""function tally(cases) { return {n: cases.length, k: new Set(cases.map(c => c.company_id || c.company)).size}; }
function foundLine(cases) { const {n, k} = tally(cases); if (!n) return "no revenue exposures";
  return `${n} revenue exposure${n === 1 ? "" : "s"} ${k === 1 ? "at" : "across"} ${k} compan${k === 1 ? "y" : "ies"}`; }"""

FAVICON = ("data:image/svg+xml," + "%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' "
           "rx='8' fill='%23111827'/%3E%3Cpath d='M7 20l5-6 4 4 9-10' stroke='%2334d399' stroke-width='3' fill='none' "
           "stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E")

STYLE = r"""
:root{--bg:#f6f7f9;--card:#fff;--line:#e6e8ec;--line2:#eef0f3;--ink:#0f172a;--soft:#334155;--mute:#64748b;--faint:#94a3b8;
--blue:#2563eb;--green:#15803d;--greenbg:#f0fdf4;--greenline:#bbf7d0;--amber:#b45309}
*{box-sizing:border-box}[hidden]{display:none!important}html{scroll-behavior:smooth}
html,body{margin:0;background:var(--bg);color:var(--ink);font:15.5px/1.62 -apple-system,BlinkMacSystemFont,"Segoe UI",Inter,system-ui,sans-serif;
-webkit-font-smoothing:antialiased}a{color:var(--blue);text-decoration:none}a:hover{text-decoration:underline}
.top{position:sticky;top:0;z-index:5;background:rgba(246,247,249,.9);backdrop-filter:saturate(1.6) blur(12px);border-bottom:1px solid var(--line)}
.top .in{max-width:920px;margin:0 auto;padding:12px 28px;display:flex;align-items:center;gap:14px}
.brand{font-weight:700;font-size:13px;color:var(--mute);display:flex;align-items:center;gap:8px;white-space:nowrap}
.brand img{width:20px;height:20px}.brand:hover{text-decoration:none;color:var(--ink)}
.spacer{flex:1}.pill{display:inline-flex;align-items:center;gap:6px;font-size:12px;font-weight:650;padding:3px 10px;border-radius:99px}
.pill.running{background:#dbeafe;color:#1d4ed8}.pill.replay{background:#ede9fe;color:#6d28d9}.pill.failed{background:#fee2e2;color:#b91c1c}
.pulse{width:7px;height:7px;border-radius:50%;background:#2563eb;animation:pulse 1.4s infinite}
@keyframes pulse{0%{box-shadow:0 0 0 0 rgba(37,99,235,.55)}100%{box-shadow:0 0 0 9px rgba(37,99,235,0)}}
.clock{font-variant-numeric:tabular-nums;font-weight:650;color:var(--soft);font-size:13px}
.btn{border:1px solid var(--line);background:var(--card);color:var(--soft);border-radius:99px;padding:6px 14px;font-weight:600;font-size:13px;line-height:1;font-family:inherit;
cursor:pointer;white-space:nowrap}.btn:hover{border-color:#cbd5e1;text-decoration:none}.btn.dark{background:var(--ink);color:#fff;border-color:var(--ink)}
.doc{max-width:920px;margin:0 auto;padding:34px 28px 30px}
h1{font-size:30px;line-height:1.2;letter-spacing:-.025em;margin:0 0 10px;font-weight:760}
.lede{font-size:17px;color:var(--soft);margin:0 0 22px;max-width:720px}
.assign{margin:0 0 18px;padding:14px 18px;background:var(--card);border:1px solid var(--line);border-radius:12px;color:var(--soft)}
.assign .k{font-size:11px;text-transform:uppercase;letter-spacing:.08em;color:var(--faint);font-weight:700;display:block;margin-bottom:3px}
.facts{display:flex;flex-wrap:wrap;gap:8px 22px;color:var(--mute);font-size:14px;margin:0 0 6px}.facts b{color:var(--ink);font-weight:700}
h2{font-size:21px;letter-spacing:-.015em;margin:46px 0 6px;font-weight:720}.hint{color:var(--mute);font-size:14px;margin:0 0 18px}
.opp{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:20px 22px;margin:0 0 14px;box-shadow:0 1px 2px rgba(15,23,42,.03)}
.opp .head{display:flex;gap:18px;align-items:flex-start;justify-content:space-between}
.opp .co{font-size:18px;font-weight:720;letter-spacing:-.01em}.opp .st{font-size:13.5px;color:var(--mute);font-weight:550}
.opp .amt{text-align:right;white-space:nowrap}.opp .amt b{display:block;font-size:26px;font-weight:780;letter-spacing:-.02em;line-height:1.1}
.opp .amt span{font-size:12.5px;color:var(--mute)}
.opp .row{display:grid;grid-template-columns:110px minmax(0,1fr);gap:4px 14px;margin-top:14px;font-size:14.5px;color:var(--soft)}
.opp .row dt{color:var(--faint);font-size:12px;text-transform:uppercase;letter-spacing:.06em;font-weight:700;padding-top:3px}
.opp .row dd{margin:0}.opp .row dd.long{display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
.opp.open .row dd.long{display:block;-webkit-line-clamp:unset}.opp blockquote{margin:14px 0 0;padding:12px 16px;background:#f8fafc;border-left:3px solid var(--blue);
border-radius:0 10px 10px 0;color:var(--ink);font-size:14.5px}
.opp .foot{display:flex;gap:16px;margin-top:14px;font-size:13.5px;font-weight:600}
@media(max-width:640px){.opp .head{flex-direction:column;gap:8px}.opp .amt{text-align:left}.opp .row{grid-template-columns:1fr}.opp .row dt{padding-top:6px}
h1{font-size:25px}.top .in,.doc{padding-left:16px;padding-right:16px}.brand span{display:none}}
.empty{color:var(--faint);font-size:14px}
.also{display:grid;grid-template-columns:1fr 1fr;gap:16px;align-items:start}@media(max-width:760px){.also{grid-template-columns:1fr}}
.also .box{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px 18px}
.also h3{margin:0 0 8px;font-size:12px;text-transform:uppercase;letter-spacing:.08em;color:var(--mute)}
.also ul{margin:0;padding:0;list-style:none}.also li{font-size:13.5px;color:var(--soft);padding:8px 0;border-top:1px solid var(--line2)}
.also li:first-child{border-top:0;padding-top:0}.clamp3{display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
.step{position:relative;padding:0 0 22px 28px}.step.new{animation:fade .35s ease}
.step:before{content:"";position:absolute;left:6px;top:16px;bottom:-2px;width:2px;background:var(--line)}
.step:last-of-type:before{display:none}
.step .dot{position:absolute;left:0;top:8px;width:14px;height:14px;border-radius:50%;background:var(--card);border:2px solid #cbd5e1}
.step.live .dot{border-color:var(--blue);background:#dbeafe}.step.has-case .dot{border-color:#22c55e;background:#dcfce7}
.step.flash{animation:flash 1.6s ease}@keyframes flash{0%,40%{background:#fef9c3}100%{background:transparent}}
@keyframes fade{from{opacity:0;transform:translateY(5px)}to{opacity:1;transform:none}}
.when{font-size:11.5px;color:var(--faint);font-weight:650;letter-spacing:.03em;font-variant-numeric:tabular-nums}
.thought{color:var(--soft);font-size:14.5px}.thought p{margin:0 0 6px}
.clamp{max-height:6.6em;overflow:hidden;-webkit-mask-image:linear-gradient(#000 55%,transparent)}
.more{border:0;background:none;color:var(--blue);font-weight:600;font-size:12.5px;line-height:1;font-family:inherit;padding:0;margin:2px 0 8px;cursor:pointer}
.say{margin:2px 0 8px;padding:9px 13px;background:var(--card);border:1px solid var(--line);border-left:3px solid var(--faint);
border-radius:9px;font-size:14px;color:var(--soft)}.say p{margin:0 0 6px}.say p:last-child{margin:0}
.found{display:inline-flex;align-items:center;gap:8px;margin:4px 0 6px;padding:6px 12px;background:var(--greenbg);border:1px solid var(--greenline);
border-radius:99px;font-size:13px;color:var(--green);font-weight:650;cursor:pointer}
.toggle{display:inline-flex;flex-wrap:wrap;align-items:center;gap:7px;border:1px solid var(--line);background:var(--card);border-radius:99px;
padding:3px 12px;font-weight:500;font-size:12.5px;line-height:1.6;font-family:inherit;color:var(--mute);cursor:pointer;margin-top:2px}.toggle:hover{border-color:#cbd5e1}
.toggle svg{width:13px;height:13px;vertical-align:-2px;margin-right:3px}.sep{color:#cbd5e1}.warn{color:var(--amber)}.chev{color:var(--faint);font-weight:600}
.acts ul{list-style:none;margin:8px 0 0 6px;padding:0;border-left:2px solid var(--line2)}
.act{display:flex;gap:10px;padding:4px 0 4px 13px;font-size:13.5px;color:var(--soft)}.act a{color:var(--soft)}.act a:hover{color:var(--blue)}
.act svg{width:15px;height:15px;flex:0 0 15px;margin-top:3px;color:var(--faint)}.act .meta{color:var(--faint);margin-left:6px;font-size:12.5px}
.act.blocked,.act.blocked a{color:var(--faint)}.act.blocked .meta{color:var(--amber)}.act.refused .meta{color:#b91c1c}
.act .atext{color:var(--mute);font-size:13px;margin-top:1px}.det{margin-top:4px;display:flex;flex-direction:column;gap:2px}
.det a{font-size:12.5px;color:var(--mute)}.det pre{margin:4px 0 0;padding:8px 10px;background:#f8fafc;border:1px solid var(--line);border-radius:7px;
font:11.5px/1.5 ui-monospace,Menlo,monospace;white-space:pre-wrap;color:var(--mute);max-height:150px;overflow:auto}
.spin{display:inline-block;width:10px;height:10px;margin-left:8px;border:2px solid #cbd5e1;border-top-color:var(--blue);border-radius:50%;
animation:spin .8s linear infinite;vertical-align:-1px}@keyframes spin{to{transform:rotate(360deg)}}
.end{margin:2px 0 0 28px;color:var(--mute);font-size:14px}
.thought,.say,.act,.opp,.also li{overflow-wrap:anywhere}
#latest{position:fixed;bottom:24px;left:50%;transform:translateX(-50%);background:var(--ink);color:#fff;border:0;border-radius:99px;
padding:9px 18px;font-weight:600;font-size:13px;line-height:1;font-family:inherit;cursor:pointer;box-shadow:0 8px 24px rgba(15,23,42,.22)}
footer{max-width:920px;margin:0 auto;padding:10px 28px 56px;color:var(--faint);font-size:13px}footer a{color:var(--mute)}
.runs{max-width:920px;margin:0 auto;padding:28px 28px 80px}
.run{display:flex;align-items:center;gap:18px;background:var(--card);border:1px solid var(--line);border-radius:15px;padding:15px 20px;margin-bottom:10px;color:var(--ink)}
.run:hover{border-color:#cbd5e1;text-decoration:none}.run .what{flex:1;min-width:0}.run .what b{font-weight:650}
.run .what div{color:var(--mute);font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.run .n{font-size:13px;color:var(--mute);text-align:right;min-width:150px;font-variant-numeric:tabular-nums}.pill.finished{background:#dcfce7;color:var(--green)}
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
<div class="top"><div class="in"><a class="brand" href="__HOME__"><img src="__FAVICON__" alt=""><span>distress-signals</span></a>
  <span id="pill" class="pill" hidden></span><span class="clock" id="clock" hidden></span><div class="spacer"></div>
  <button class="btn" id="replay" hidden>▶ Watch it run</button><a class="btn" href="__REPO__" target="_blank" id="gh">About the project</a></div></div>
<main class="doc">
  <h1>__TITLE__</h1><p class="lede">__LEDE__</p>
  <div class="assign" __ASSIGNHIDDEN__><span class="k">The assignment</span>__ASSIGNMENT__</div>
  <div class="facts" id="facts"></div>
  <h2>What it found</h2><p class="hint" id="foundhint">Each card is one revenue stream riding on a specific upcoming event, with the amount at stake and the person to talk to. A company can have more than one.</p>
  <div id="cards"><p class="empty" id="nocards">Nothing yet. Companies appear here as the researcher finds them.</p></div>
  <div class="also" id="also" hidden><div class="box" id="asidebox" hidden><h3>Looked at and set aside</h3><ul id="aside"></ul></div>
    <div class="box" id="leadsbox" hidden><h3>Worth a look next</h3><ul id="leads"></ul></div></div>
  <h2>How it got there</h2><p class="hint">Each step is what the researcher was thinking at that moment. Underneath are the searches, documents and
  calculations behind it.</p>
  <div id="timeline"></div>
</main>
<footer>Made with <a href="__REPO__" target="_blank">distress-signals</a>, which finds companies whose revenue depends on an upcoming event they
don't control, works out what's at stake, and names the person to talk to.</footer>
<button id="latest" hidden>Jump to the latest step ↓</button>
<script>
__FOUNDJS__
const RUN = "__RUN__", ICONS = __ICONS__, DATA = __DATA__;
const NOUN = {search:["search","searches"],read:["document","documents"],web:["web page","web pages"],calc:["calculation","calculations"],
  lookup:["check","checks"],finding:["note","notes"],save:["write-up","write-ups"],answer:["summary","summaries"]};
let after = 0, status = {}, cur = null, items = [], replaying = false, details = {}, order = [], stepOf = {};
const $ = s => document.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const inline = s => s.replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>").replace(/`([^`]+)`/g, "$1");
const paras = s => esc(s).split(/\n{2,}/).map(p => "<p>" + inline(p).replace(/^\s*[-•] /gm, "• ").replace(/\n/g, "<br>") + "</p>").join("");
const money = (x, c) => x == null ? "" : ((c && c !== "USD") ? c + " " : "$") + (Math.abs(x) >= 1e9 ? (x/1e9).toFixed(2) + "B" :
  Math.abs(x) >= 1e6 ? (x/1e6).toFixed(1) + "M" : Math.abs(x) >= 1e3 ? Math.round(x/1e3) + "K" : Math.round(x));
const mins = s => s == null ? "" : s < 60 ? "Start" : `${Math.round(s / 60)} min in`;
const date = d => d ? new Date(d + "T12:00:00").toLocaleDateString([], {month: "short", day: "numeric", year: "numeric"}) : "";
const live = () => status.state === "running" && !DATA;
const nearBottom = () => window.innerHeight + window.scrollY >= document.body.scrollHeight - 200;

function clampable(el, h) { if (el.scrollHeight > h) { el.classList.add("clamp"); const b = document.createElement("button"); b.className = "more";
  b.textContent = "Read more"; b.onclick = () => { el.classList.toggle("clamp"); b.textContent = el.classList.contains("clamp") ? "Read more" : "Show less"; }; el.after(b); } }
function newStep(text, el) {
  const s = document.createElement("section"); s.className = "step new live";
  s.innerHTML = `<div class="dot"></div><div class="when">${mins(el)}</div><div class="thought">${text ? paras(text) : "<p>Getting started.</p>"}</div>
    <div class="says"></div><div class="founds"></div><div class="acts" hidden><button class="toggle"></button><ul></ul></div>`;
  $("#timeline").appendChild(s); clampable(s.querySelector(".thought"), 110);
  const st = {el: s, acts: [], open: false, manual: false};
  s.querySelector(".toggle").onclick = () => { st.open = !st.open; st.manual = true; draw(st); };
  if (cur) { cur.el.classList.remove("live"); if (!cur.manual && cur.open) { cur.open = false; draw(cur); } }
  st.open = live(); cur = st; return st;
}
function actHTML(a) {
  const label = a.link ? `<a href="${esc(a.link)}" target="_blank" rel="noopener">${esc(a.label)}</a>` : esc(a.label);
  const det = (a.details || []).filter(Boolean).map(d => typeof d === "string" ? `<pre>${esc(d)}</pre>` :
    `<a href="${esc(d.url)}" target="_blank" rel="noopener">${esc(d.title)}</a>`).join("");
  return `<li class="act ${a.status || "pending"}">${ICONS[a.icon] || ""}<div><div>${label}${a.meta ? `<span class="meta">${esc(a.meta)}</span>` : ""}` +
    `${a.status ? "" : '<span class="spin"></span>'}</div>${a.text ? `<div class="atext">${esc(a.text)}</div>` : ""}${det ? `<div class="det">${det}</div>` : ""}</div></li>`;
}
function draw(st) {
  const box = st.el.querySelector(".acts"); box.hidden = !st.acts.length; if (!st.acts.length) return;
  const counts = {}; st.acts.forEach(a => counts[a.icon] = (counts[a.icon] || 0) + 1);
  const blocked = st.acts.filter(a => a.status === "blocked").length;
  box.querySelector(".toggle").innerHTML = Object.entries(counts).map(([k, n]) => `<span>${ICONS[k] || ""}${n} ${(NOUN[k] || [k, k])[n > 1 ? 1 : 0]}</span>`)
    .join('<span class="sep">·</span>') + (blocked ? `<span class="sep">·</span><span class="warn">${blocked} blocked</span>` : "") +
    `<span class="sep">·</span><span class="chev">${st.open ? "hide" : "show"}</span>`;
  const ul = box.querySelector("ul"); ul.hidden = !st.open; if (st.open) ul.innerHTML = st.acts.map(actHTML).join("");
}
function card(d) {
  const share = d.share != null ? `${(100 * d.share).toFixed(0)}% of revenue` : "";
  return `<div class="head"><div><div class="co">${esc(d.company)}</div><div class="st">${esc(d.stream || "")}</div></div>` +
    (d.stake != null ? `<div class="amt"><b>${money(d.stake, d.currency)}</b><span title="${esc(d.of || "")}">at stake${share ? " · " + share : ""}</span></div>` : "") + `</div>` +
    `<dl class="row">` + (d.event ? `<dt>The event</dt><dd>${esc(d.event)}${d.event_date && !String(d.event).includes(d.event_date.slice(0, 4)) ? " · " + date(d.event_date) : ""}</dd>` : "") +
    (d.dependency ? `<dt>Why it matters</dt><dd class="long">${esc(d.dependency)}</dd>` : "") + (d.why_now ? `<dt>Why now</dt><dd class="long">${esc(d.why_now)}</dd>` : "") +
    (d.who ? `<dt>Who to call</dt><dd>${esc(d.who)}</dd>` : "") + `</dl>` +
    (d.opening ? `<blockquote>${esc(d.opening)}</blockquote>` : "") +
    `<div class="foot"><a href="#" data-full>Read the full case</a><a href="#" data-trace="${esc(d.oid)}">How it found this ↓</a>` +
    `${d.deal ? `<a href="${esc(d.deal)}" target="_blank">Open in HubSpot →</a>` : ""}</div>`;
}
function renderCards() {
  const shown = order.filter(oid => (details[oid] || {}).status !== "withdrawn");
  $("#nocards").hidden = shown.length > 0;
  $("#cards").querySelectorAll(".opp").forEach(e => e.remove());
  shown.forEach(oid => { const d = details[oid] || {}; const a = document.createElement("article"); a.className = "opp"; a.innerHTML = card(d); $("#cards").appendChild(a); });
  $("#cards").querySelectorAll("[data-trace]").forEach(x => x.onclick = e => { e.preventDefault(); trace(x.dataset.trace); });
  $("#cards").querySelectorAll(".opp").forEach(a => { const longs = [...a.querySelectorAll("dd.long")], full = a.querySelector("[data-full]");
    if (!longs.some(x => x.scrollHeight > x.clientHeight + 2)) { full.remove(); return; }
    full.onclick = e => { e.preventDefault(); const open = a.classList.toggle("open"); full.textContent = open ? "Show less" : "Read the full case"; }; });
}
function trace(oid) { const st = stepOf[oid]; if (!st) return; st.open = true; st.manual = true; draw(st);
  st.el.scrollIntoView({behavior: "smooth", block: "center"}); st.el.classList.remove("flash"); void st.el.offsetWidth; st.el.classList.add("flash"); }
function apply(op) {
  if (op.op === "add") {
    const it = op.item; items.push(it);
    if (it.k === "think") newStep(it.t, it.el);
    else if (it.k === "say" || it.k === "note") { if (!cur) newStep("", it.el); const d = document.createElement("div"); d.className = "say"; d.innerHTML = paras(it.t);
      cur.el.querySelector(".says").appendChild(d); clampable(d, 120); }
    else if (it.k === "act") { if (!cur) newStep("", it.el); it.step = cur; cur.acts.push(it); draw(cur); }
    else if (it.k === "final") { const leads = it.leads || []; if (leads.length) { $("#also").hidden = false; $("#leadsbox").hidden = false;
      $("#leads").innerHTML = leads.map(l => `<li><div class="clamp3"><b>${esc(l.company_or_event)}</b>: ${esc(l.reason)}</div></li>`).join(""); } }
    else if (it.k === "end") { $("#timeline").insertAdjacentHTML("beforeend", `<p class="end">End of the run.</p>`); if (cur) cur.el.classList.remove("live"); }
  } else if (op.op === "update") {
    const it = items[op.i]; if (!it) return; Object.assign(it, op.fields); if (it.step) draw(it.step);
    const c = op.fields.case;
    if (c && it.step) { stepOf[c.oid] = it.step; it.step.el.classList.add("has-case");
      if (!details[c.oid]) details[c.oid] = {oid: c.oid, company: c.company, company_id: c.company_id, stream: c.stream, stake: c.stake, share: c.share, currency: c.currency};
      if (!order.includes(c.oid)) order.push(c.oid);
      const f = document.createElement("div"); f.className = "found"; f.innerHTML = `${ICONS.save}Found: ${esc(c.company)}${c.stake != null ? " · " + money(c.stake, c.currency) + " at stake" : ""}`;
      f.onclick = () => { const i = order.indexOf(c.oid); const el = $("#cards").querySelectorAll(".opp")[i]; if (el) el.scrollIntoView({behavior: "smooth", block: "center"}); };
      it.step.el.querySelector(".founds").appendChild(f); renderCards(); }
    if (it.tool === "record_finding" && it.finding === "dropped" && it.status === "ok") { $("#also").hidden = false; $("#asidebox").hidden = false;
      $("#aside").insertAdjacentHTML("beforeend", `<li><div class="clamp3">${esc(it.text)}</div></li>`); }
  }
}
function facts() {
  const acts = items.filter(x => x.k === "act"), n = k => acts.filter(a => k.includes(a.icon) && a.status !== "blocked").length;
  const ms = status.duration_ms ?? (status.t0 ? (live() ? Date.now() : Date.parse(status.last || status.t0)) - Date.parse(status.t0) : null);
  const {n: found, k: cos} = tally(order.map(oid => details[oid] || {}).filter(d => d.status !== "withdrawn"));
  const parts = [ms != null ? `<span><b>${Math.max(1, Math.round(ms / 60000))}</b> minutes</span>` : "", `<span><b>${n(["search"])}</b> searches</span>`,
    `<span><b>${n(["read", "web"])}</b> documents read</span>`, `<span><b>${n(["calc"])}</b> calculations</span>`,
    `<span><b>${found}</b> revenue exposure${found === 1 ? "" : "s"} ${cos === 1 ? "at" : "across"} <b>${cos}</b> compan${cos === 1 ? "y" : "ies"}</span>`, status.cost != null ? `<span><b>$${status.cost.toFixed(2)}</b> in AI usage</span>` : ""];
  $("#facts").innerHTML = parts.filter(Boolean).join("");
}
function tick() {
  if (!live() || !status.t0) return; const sec = Math.floor((Date.now() - Date.parse(status.t0)) / 1000);
  $("#clock").textContent = `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, "0")}`; facts();
}
function showStatus(s, cases) {
  status = s; if (cases) { Object.assign(details, cases); renderCards(); }
  const p = $("#pill"); p.hidden = !(live() || replaying || s.state === "failed");
  p.className = "pill " + (replaying ? "replay" : s.state); p.innerHTML = replaying ? "Replaying" : s.state === "running" ? '<span class="pulse"></span>Live' : "Failed";
  $("#clock").hidden = !live(); facts(); tick();
}
function reset() { $("#timeline").innerHTML = ""; items = []; cur = null; order = []; stepOf = {}; renderCards();
  $("#aside").innerHTML = ""; $("#leads").innerHTML = ""; $("#also").hidden = true; $("#asidebox").hidden = true; $("#leadsbox").hidden = true; }
function replay() {
  replaying = true; const all = DATA.ops, total = all.length, keep = {...details}; reset(); details = keep; let i = 0; showStatus({...DATA.status});
  document.getElementById("timeline").scrollIntoView({behavior: "smooth"});
  (function step() { const n = Math.max(1, Math.round(total / 450)); for (let k = 0; k < n && i < total; k++) apply(all[i++]); facts();
    window.scrollTo({top: document.body.scrollHeight});
    if (i < total) setTimeout(step, 40); else { replaying = false; showStatus(DATA.status); window.scrollTo({top: 0, behavior: "smooth"}); } })();
}
async function poll() {
  try {
    const follow = nearBottom(); const first = after === 0;
    const j = await (await fetch(`/runs/${RUN}/ops?after=${after}`)).json();
    j.ops.forEach(apply); after = j.next; showStatus(j.status, j.cases);
    if (j.ops.length && live() && !first) { if (follow) window.scrollTo({top: document.body.scrollHeight, behavior: "smooth"}); else $("#latest").hidden = false; }
    if (first && live()) window.scrollTo({top: document.body.scrollHeight});
    if (j.status.state === "running" || j.status.state === "unknown") setTimeout(poll, 2500);
  } catch (e) { setTimeout(poll, 5000); }
}
$("#latest").onclick = () => { window.scrollTo({top: document.body.scrollHeight, behavior: "smooth"}); $("#latest").hidden = true; };
window.addEventListener("scroll", () => { if (nearBottom()) $("#latest").hidden = true; });
setInterval(tick, 1000);
if (DATA) { details = DATA.cases || {}; DATA.ops.forEach(apply); showStatus(DATA.status, DATA.cases); $("#replay").hidden = false; $("#replay").onclick = replay; }
else poll();
</script></body></html>"""


def _page(run_id, data=None, home="/"):
    run = _run_row(run_id)
    title, lede = _title(run)
    assignment = _assignment(run)
    return (PAGE.replace("__STYLE__", STYLE).replace("__FOUNDJS__", FOUND_JS).replace("__ICONS__", json.dumps(ICONS)).replace("__RUN__", run_id)
            .replace("__FAVICON__", FAVICON).replace("__REPO__", REPO).replace("__HOME__", home)
            .replace("__DATA__", json.dumps(data, default=str).replace("</", "<\\/") if data is not None else "null")
            .replace("__ASSIGNHIDDEN__", "" if assignment else "hidden").replace("__ASSIGNMENT__", html.escape(assignment))
            .replace("__TITLE__", html.escape(title)).replace("__LEDE__", html.escape(lede)))


def export_html(run_id, public=False, home=REPO):
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
    return _page(run_id, {"ops": ops, "status": status, "cases": _case_details(v.cases, public)}, home=home)


def _index():
    con = db.connect()
    try:
        rows = [dict(r) for r in con.execute("SELECT * FROM runs ORDER BY started DESC LIMIT 100")]
    finally:
        con.close()
    cards = []
    for r in rows:
        state = "failed" if r["error"] else ("finished" if r["finished"] else "running")
        title, _ = _title(r)
        sub = _assignment(r)
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
                    return self._send(json.dumps({"ops": v.ops[after:], "next": len(v.ops), "status": _status(rid, _run_row(rid), v),
                                                  "cases": _case_details(v.cases)}, default=str), "application/json")
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
