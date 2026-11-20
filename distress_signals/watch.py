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

from . import activityview, db, example_review, findingsview, hubspot
from .feed import _result_text
from .runpage import CHART_JS, FAVICON, FOUND_JS, ICONS, LEADS_HTML, PACE, PAGE, PAGE_STEPS, SHELL_STYLE, STYLE
from .util import OUT, load_env

PORT = int(os.environ.get("DISTRESS_WATCH_PORT", "8790"))
REPO = "https://github.com/owassmer/distress-signals"
ICON = {"WebSearch": "search", "WebFetch": "web", "search_records": "search", "read_record": "read", "compute": "calc",
        "lookup": "lookup", "calculate_exposure": "calc", "record_finding": "finding", "save_opportunity": "save",
        "StructuredOutput": "answer"}
FINDING = {"dropped": "Proposed lead exclusion", "fact": "Source finding", "inference": "Research inference",
           "conclusion": "Research conclusion", "question": "Open research question"}
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
            else "the source record")


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
        return {"label": f"Searched {where} for {_quoted(terms)}" if terms else f"Read {where}",
                "link": ref if ref.startswith("http") else None}
    if name == "compute":
        saved = i.get("save_as")
        return {"label": f"SQL query for “{title_label(saved)}”" if saved else "SQL query on research records",
                "text": i.get("note"), "details": [i["sql"]] if i.get("sql") else []}
    if name == "lookup":
        return {"label": f"Searched saved records for “{i.get('id_or_query')}”"}
    if name == "calculate_exposure":
        return {"label": "Draft revenue scenario calculation"}
    if name == "record_finding":
        f = i.get("finding") or {}
        return {"label": "Case withdrawal request" if f.get("withdraw_case") else FINDING.get(f.get("kind"), "Recorded a finding"),
                "text": f.get("statement"), "finding": f.get("kind")}
    if name == "save_opportunity":
        o = i.get("opportunity") or {}
        return {"label": f"Case submission for {_company_name(o.get('company'))}", "text": title_label(o.get("stream_key"))}
    if name == "StructuredOutput":
        return {"label": "Run summary and follow-up leads"}
    return {"label": name}


def outcome(name, i, text, is_error):
    """What came back, in plain words: status (ok, blocked or refused) and whatever is worth showing."""
    if is_error:
        t = text.replace("Error executing tool ", "")
        t = re.sub(r"^[a-z_]+: ", "", t)
        if name == "lookup" and "Nothing saved is named" in t:
            return {"status": "ok", "meta": "No matching saved record"}
        access = re.search(r"answered (401|403|429)", t)
        if access:
            return {"status": "blocked", "label": f"Source request to {_where(i.get('id_or_url') or i.get('url') or '')}",
                    "meta": f"Source returned HTTP {access[1]}"}
        if "couldn't fetch" in t or "Timeout" in t or "timed out" in t:
            return {"status": "blocked", "meta": "Source request failed"}
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
        return {"status": "ok", "meta": f"{len(links)} linked result{'s' if len(links) != 1 else ''} shown" if links else None, "details": links}
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
                "label": f"Searched “{short}” for {_quoted(terms)}" if terms else f"Read “{short}”",
                "meta": ", ".join(x for x in (site, f"{found} passage{'s' if found != 1 else ''}" if terms else None) if x)}
    if name == "search_records":
        n = out.get("count", len(out.get("results") or []))
        return {"status": "ok", "meta": f"{n} result{'s' if n != 1 else ''} returned"
                + (f" of {out['total_matches']} matches" if out.get("total_matches") and out["total_matches"] > n else "")}
    if name == "record_finding":
        finding = i.get("finding") or {}
        if finding.get("withdraw_case"):
            return {"status": "ok", "label": "Case withdrawn"}
        if finding.get("kind") == "dropped":
            return {"status": "ok", "label": "Lead set aside"}
    if name == "compute":
        n = out.get("row_count", len(out.get("rows") or []))
        return {"status": "ok", "meta": f"{n} row{'s' if n != 1 else ''}"}
    if name == "lookup":
        if out.get("companies") is not None:
            cs = out["companies"]
            k = sum(len(c.get("cases") or []) for c in cs)
            return {"status": "ok", "meta": f"{len(cs)} matching compan{'y' if len(cs) == 1 else 'ies'}, {k} case{'s' if k != 1 else ''}"}
        if out.get("opportunity"):
            return {"status": "ok", "meta": "Saved case returned"}
        if out.get("event_id"):
            k = len(out.get("cases") or [])
            return {"status": "ok", "meta": f"Event record · {k} associated case{'s' if k != 1 else ''}"}
        return {"status": "ok", "meta": "Saved record returned"}
    if name == "calculate_exposure":
        scenarios = (out.get("results") or {}).get("affected") or []
        n = len(scenarios)
        return {"status": "ok", "meta": f"{n} scenario{'s' if n != 1 else ''} compared with baseline" if n else None}
    if name == "save_opportunity":
        o, res = i.get("opportunity") or {}, out.get("results") or {}
        company = _company_name(out.get("company_id") or o.get("company"))
        return {"status": "ok", "label": f"Saved case for {company}", "case": {
            "oid": out.get("opportunity_id"), "company": company, "company_id": out.get("company_id"),
            "stream": title_label(o.get("stream_key")),
            "detail": o.get("stream"), "revision": out.get("revision"), "stake": res.get("max_shortfall"),
            "share": res.get("max_share"), "of": (res.get("denominator") or {}).get("label"),
            "currency": o.get("currency"), "deal": _deal_url(out.get("opportunity_id"))}}
    return {"status": "ok"}


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
    """A heading for the request that started the run."""
    if not run:
        return "Research run"
    kind, seed = run["seed_kind"], run.get("seed") or ""
    return {"mandate": "Open prospect search", "company": f"Company research · {seed}",
             "event": "Companies exposed to the event", "brief": "Research brief",
             "refresh": "Case reassessment", "source": "Source research",
             "lead": f"Follow-up research · {seed}"}.get(kind, "Research run")


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
    return " · ".join(part for part in (text, run.get("question")) if part)


def _input(name, x, url_of):
    """One figure behind a case, with its provenance: reported (the passage), derived (the calculation) or assumed."""
    src = x.get("source") or {}
    kind = "reported" if x.get("source") else "derived" if x.get("derived") else "assumed"
    return {"name": title_label(name), "value": x.get("value"), "unit": x.get("unit"), "note": x.get("note"), "kind": kind,
            "passage": src.get("passage"), "checked": src.get("checked"), "url": url_of(src.get("observation_id")),
            "how": (x.get("derived") or {}).get("note") if kind == "derived" else x.get("assumption")}


def _case_details(oids, public=False):
    """The final form of the cases a run saved: the findings cards, and each case's own page with the event,
    the outcomes, the figures behind them, the evidence, and who to call."""
    if not oids:
        return {}
    from . import briefs
    out = {}
    con = db.connect()
    try:
        for c in briefs.cases(con, list(oids)):
            case, ev, res, buyer = c["case"], c["event"], c["results"], c["case"]["buyer"]
            cited = c.get("cited") or {}
            url_of = lambda oid: (cited.get(oid) or {}).get("url") if oid else None  # noqa: E731
            scen = {a["name"]: a for a in case.get("affected") or []}
            out[c["id"]] = {
                "oid": c["id"], "status": c["status"], "company": c["company"]["name"],
                "company_id": c["row"]["company_id"], "stream": title_label(case.get("stream_key")),
                "event": ev.get("title"), "event_date": ev.get("date"), "dependency": case.get("dependency"),
                "why_now": case["case"].get("why_now"), "stake": res.get("max_shortfall"), "share": res.get("max_share"),
                "of": (res.get("denominator") or {}).get("label"), "denominator_value": (res.get("denominator") or {}).get("value"),
                "currency": case.get("currency"),
                "period": f"{case['period']['start']} to {case['period']['end']}",
                "who": ", ".join(x for x in (buyer.get("name"), buyer.get("title")) if x) or buyer.get("role"),
                "opening": (case.get("outreach") or {}).get("draft"),
                "deal": None if public else _deal_url(c["id"]),
                "why_matters": case["case"].get("why_matters"), "condition": case.get("condition"), "timing": case.get("timing"),
                "event_description": ev.get("description"), "event_timing": ev.get("timing"),
                "period_start": case["period"]["start"], "period_end": case["period"]["end"],
                "baseline": {"revenue": res.get("baseline_revenue"), "description": (case.get("baseline") or {}).get("description")},
                "scenarios": [{"label": title_label(r["name"]), "description": (scen.get(r["name"]) or {}).get("description"),
                               "revenue": r.get("revenue"), "shortfall": r.get("shortfall"), "share": r.get("share")}
                              for r in res.get("affected") or []],
                "inputs": [_input(k, x, url_of) for k, x in (case.get("inputs") or {}).items()],
                "evidence": [{"passage": e.get("passage"), "supports": e.get("supports"), "checked": e.get("checked"), "url": url_of(e.get("observation_id"))}
                             for e in case.get("evidence") or []],
                "role": buyer.get("role"), "next_action": case.get("next_action")}
    finally:
        con.close()
    return out


def _items(ops):
    """Materialize the run’s log entries, including the results of completed tool calls."""
    items = []
    for op in ops:
        if op["op"] == "add":
            items.append(dict(op["item"]))
        elif 0 <= op["i"] < len(items):
            items[op["i"]].update(op["fields"])
    return items


def public_cases(run_id, ids):
    return example_review.reviewed_cases(run_id, _case_details(ids, public=True))


def public_leads(run_id, items):
    aside = [item["text"] for item in items if item.get("tool") == "record_finding" and item.get("finding") == "dropped"
             and item.get("status") == "ok" and item.get("text")]
    leads = [lead for item in items if item["k"] == "final" for lead in item.get("leads") or []]
    return example_review.reviewed_leads(run_id, aside, leads)


def _page(run_id, data=None, home="/"):
    run = _run_row(run_id)
    title = _title(run)
    assignment = _assignment(run)
    purpose = assignment
    results_header = '<h2 id="findings-title">Company findings</h2>'
    share_meta = ""
    project_credit = ""
    if data and data.get("example_deal"):
        project_credit = '<a class="project-credit" href="https://github.com/owassmer">Built by <strong>Owen Wassmer</strong></a>'
        title = "An AI agent for sales prospecting"
        purpose = ("Distress Signals searches public records for upcoming events and the companies whose revenue depends on them. "
                   "It creates proposed deals in HubSpot showing how much is at stake, who to contact, and why the timing matters.")
        brief = (f'<details class="demo-brief"><summary>Search brief</summary>'
                 f'<blockquote>{html.escape(assignment)}</blockquote></details>') if assignment else ""
        replay = ('<button type="button" id="demo-replay" class="demo-play" aria-controls="activity" hidden>'
                  '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 2.5v11L13 8z"/></svg>'
                  '<span>Replay search · <span id="demo-replay-duration"></span></span></button>')
        results_header = (f'<div class="demo-header"><h2 id="findings-title">Prospects from this search</h2>'
                          f'{replay}{brief}</div>')
        description = html.escape(purpose, quote=True)
        share_meta = (f'<meta name="description" content="{description}">'
                      f'<meta property="og:title" content="{html.escape(title, quote=True)} · Distress Signals">'
                      f'<meta property="og:description" content="{description}"><meta property="og:type" content="website">')
    index_link = ""
    if data is None or home not in ("/", REPO, "#findings"):
        index_link = f'<a class="project-link operator-home" href="{html.escape(home, quote=True)}">All runs</a>'
    page = (PAGE.replace("__STYLE__", STYLE + SHELL_STYLE + findingsview.STYLE + activityview.STYLE)
            .replace("__FINDINGSHTML__", findingsview.HTML).replace("__FINDINGSJS__", findingsview.JS)
            .replace("__LEADSHTML__", LEADS_HTML).replace("__ACTIVITYHTML__", activityview.HTML.strip()).replace("__ACTIVITYJS__", activityview.JS)
            .replace("__RESULTSHEADER__", results_header).replace("__SHAREMETA__", share_meta)
            .replace("__RUNINDEX__", index_link).replace("__CREDIT__", project_credit)
            .replace("__FOUNDJS__", FOUND_JS).replace("__CHARTJS__", CHART_JS)
            .replace("__ICONS__", json.dumps(ICONS)).replace("__PAGESTEPS__", str(PAGE_STEPS)).replace("__PACE__", json.dumps(PACE))
            .replace("__RUN__", run_id).replace("__FAVICON__", FAVICON).replace("__REPO__", REPO)
            .replace("__PURPOSEHIDDEN__", "" if purpose else "hidden").replace("__PURPOSE__", html.escape(purpose))
            .replace("__TITLE__", html.escape(title)))
    # the run's own text goes in last, so nothing in it is taken for a placeholder
    return page.replace("__DATA__", json.dumps(data, default=str).replace("</", "<\\/") if data is not None else "null")


def export_html(run_id, public=False, home=REPO, *, example_deal=False):
    """One run as a single self-contained page: everything embedded, no server, with a replay button.
    public=True leaves out links into the operator's HubSpot."""
    v = RunView(run_id)
    v.update()
    status = _status(run_id, _run_row(run_id), v)
    ops = json.loads(json.dumps(v.ops, default=str))
    if public:
        status["requester"] = None
        status.pop("cost", None)
        for op in ops:
            if op.get("item", {}).get("k") == "end":
                op["item"].pop("cost", None)
            case = (op.get("fields") or {}).get("case")
            if case:
                case.pop("deal", None)
    data = {"ops": ops, "status": status}
    if example_deal:
        data["example_deal"] = True
    if public:
        data["cases"] = public_cases(run_id, v.cases)
        data["lead_review"] = public_leads(run_id, _items(ops))
    else:
        data["cases"] = _case_details(v.cases)
    return _page(run_id, data, home=home)


def _index():
    con = db.connect()
    try:
        rows = [dict(r) for r in con.execute("SELECT * FROM runs ORDER BY started DESC LIMIT 100")]
    finally:
        con.close()
    cards = []
    for r in rows:
        state = "failed" if r["error"] else ("finished" if r["finished"] else "running")
        title = _title(r)
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
