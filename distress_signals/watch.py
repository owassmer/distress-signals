"""A page for watching runs, served on this Mac: http://localhost:8790/ lists the runs, and /runs/<id>
shows one as it goes. Each step opens with what the model is thinking, with what it did folded beneath;
cases appear as they are saved, with the revenue at stake and a link to their deal in HubSpot. A finished
run replays the same way, with its summary and leads. It reads each run's event stream
(out/runs/<id>.jsonl) and the store, and nothing else.

A case opens to its memo: /cases/<id>/memo is the typeset memo as a page, and /cases/<id>/memo.pdf the same
printed, each made from the case as it stands when asked for. /cases lists every saved case with both.

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

from . import activityview, briefs, db, example_review, findingsview, forms, hubspot, memo, sources
from .feed import _result_text, _search_label, _search_result
from .runpage import CHART_JS, FAVICON, FOUND_JS, ICONS, LEADS_HTML, PACE, PAGE, PAGE_STEPS, PUBLICATION, SHELL_STYLE, STYLE
from .util import OUT, load_env

PORT = int(os.environ.get("DISTRESS_WATCH_PORT", "8790"))
REPO = "https://github.com/owassmer/distress-signals"
ICON = {"WebSearch": "search", "WebFetch": "web", "search_records": "search", "read_record": "read", "compute": "calc",
        "lookup": "lookup", "calculate_exposure": "calc", "record_finding": "finding", "save_case": "save", "revise_case": "save",
        "StructuredOutput": "answer"}
FINDING = {"fact": "Source finding", "inference": "Research inference", "conclusion": "Research conclusion"}
ACRONYMS = {"us", "usa", "uk", "eu", "va", "dod", "hhs", "cms", "fda", "sba", "nasa", "faa", "fcc", "ftc", "doe", "epa",
            "irs", "dhs", "usda", "snap", "nfip", "aca", "ira", "pjm", "ercot", "mde", "thc", "cbd", "ai", "ev", "fy",
            "mlb", "nfl", "nba", "nhl", "lng", "ip", "saas", "b2b", "rd", "sec", "cr", "bpa", "idiq"}
SMALL = {"and", "or", "of", "for", "the", "to", "in", "on", "from", "by", "at", "a", "an", "with"}


GUIDE_TITLE = "An AI agent for sales prospecting"
GUIDE_PURPOSE = ("Distress Signals helps brokers and advisers find businesses that may need to hedge against revenue losses. "
                 "It searches public records for companies where a single decision or disruption outside their control could cost them a significant share of sales. "
                 "Each proposed deal in HubSpot shows how much is at stake and whom to contact.")


def url(run_id=None):
    return f"http://localhost:{PORT}/" + (f"runs/{run_id}" if run_id else "")


def _domain(u):
    d = urlparse(u or "").netloc
    return d[4:] if d.startswith("www.") else d


def title_label(slug, text=""):
    """A stream_key or similar slug as a short title. A word is written in capitals where it is a known acronym,
    or where `text` (what the case itself says of the stream) writes it so: 'lmm' beside 'revenue at LMM'."""
    words = [w for w in re.split(r"[-_]+", slug or "") if w]
    said = set(re.findall(r"\b[A-Z][A-Z0-9]{1,5}\b", text or ""))
    return " ".join(w.upper() if w in ACRONYMS or w.upper() in said else w if (i and w in SMALL) else w.capitalize()
                    for i, w in enumerate(words))


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
    """What a read's subject is called: the page's site, or the source's own word for one of its records."""
    if ref.startswith("http"):
        return _domain(ref)
    return sources.noun(ref) or "the source record"


def describe(name, i):
    """What a tool call does, in plain words: label, optional text, link and details."""
    if name == "WebSearch":
        return {"label": f"Searched the web for “{i.get('query')}”"}
    if name == "WebFetch":
        return {"label": f"Read a page on {_domain(i.get('url'))}", "link": i.get("url")}
    if name == "search_records":
        return {"label": _search_label(i)}
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
        return {"label": "Draft revenue calculation"}
    if name == "record_finding":
        f = i.get("finding") or {}
        return {"label": "Case withdrawal request" if f.get("withdraw_case") else FINDING.get(f.get("kind"), "Recorded a finding"),
                "text": f.get("statement"), "finding": f.get("kind"), "no_case": not f.get("case_id")}
    if name == "save_case":
        o = i.get("case") or {}
        return {"label": f"Case submission for {_company_name(o.get('company'))}", "text": title_label(o.get("stream_key"))}
    if name == "revise_case":
        return {"label": f"Case revision for {i.get('case_id')}", "text": (i.get("changes") or {}).get("change_note")}
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
        return {"status": "ok", "meta": _search_result(out)}
    if name == "record_finding":
        finding = i.get("finding") or {}
        if finding.get("withdraw_case"):
            return {"status": "ok", "label": "Case withdrawn"}
    if name == "compute":
        n = out.get("row_count", len(out.get("rows") or []))
        return {"status": "ok", "meta": f"{n} row{'s' if n != 1 else ''}"}
    if name == "lookup":
        if out.get("companies") is not None:
            cs = out["companies"]
            k = sum(len(c.get("cases") or []) for c in cs)
            return {"status": "ok", "meta": f"{len(cs)} matching compan{'y' if len(cs) == 1 else 'ies'}, {k} case{'s' if k != 1 else ''}"}
        if out.get("case"):
            return {"status": "ok", "meta": "Saved case returned"}
        if out.get("proposition"):
            k = len(out.get("cases") or [])
            return {"status": "ok", "meta": f"Proposition record · {k} associated case{'s' if k != 1 else ''}"}
        return {"status": "ok", "meta": "Saved record returned"}
    if name == "calculate_exposure":
        n = len((out.get("results") or {}).get("worlds") or [])
        return {"status": "ok", "meta": f"{n} world{'s' if n != 1 else ''} compared with baseline" if n else None}
    if name == "save_case":
        o, res = i.get("case") or {}, out.get("results") or {}
        company = _company_name(out.get("company_id") or o.get("company"))
        return {"status": "ok", "label": f"Saved case for {company}", "case": {
            "oid": out.get("case_id"), "company": company, "company_id": out.get("company_id"),
            "stream": title_label(o.get("stream_key")),
            "detail": o.get("stream"), "revision": out.get("revision"), "stake": res.get("max_shortfall"),
            "share": res.get("max_share"), "of": (res.get("denominator") or {}).get("label"),
            "currency": o.get("currency"), "deal": _deal_url(out.get("case_id"))}}
    if name == "revise_case":
        res = out.get("results") or {}
        company = _company_name(out.get("company_id"))
        marker = {"oid": out.get("case_id"), "company": company, "company_id": out.get("company_id"),
                  "revision": out.get("revision"), "stake": res.get("max_shortfall"), "share": res.get("max_share"),
                  "of": (res.get("denominator") or {}).get("label"), "deal": _deal_url(out.get("case_id"))}
        return {"status": "ok", "label": f"Revised case for {company}", "case": {k: v for k, v in marker.items() if v is not None}}
    return {"status": "ok"}


def _deal_url(case_id):
    try:
        con = db.connect()
        d = db.get_delivery(con, hubspot.DEST, "deal", case_id)
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
    """What the analyst was given, in the operator's words: the open search's assignment, or the subject or brief."""
    if not run:
        return ""
    if run["seed_kind"] == "mandate":
        return run.get("prompt") or ""
    text = run.get("seed") or ""
    return " · ".join(part for part in (text, run.get("question")) if part)


def _input(label, x, url_of):
    """One figure behind a case, with its basis: where it is read (reported: the passage; derived: the result
    cell the store read) and the assumption by which it holds (the reasoning, and the first passage it rests on).
    A figure that rests on an assumption shows as assumed."""
    a, cell = x.get("assumption") or {}, x.get("result") or {}
    kind = "assumed" if a else "reported" if x.get("source") else "derived"
    src = x.get("source") or next(iter(a.get("evidence") or []), {})
    read = f"{cell['result_id']}, {cell['column']}" + "".join(f", {k} {v}" for k, v in cell["row"].items()) if cell else None
    how = " ".join(part for part in (f"Read from {read}." if read and a else read, a.get("note")) if part) or None
    return {"name": label, "value": x.get("value"), "unit": x.get("unit"), "note": x.get("note"), "kind": kind,
            "passage": src.get("passage"), "checked": src.get("checked"), "url": url_of(src.get("observation_id")),
            "how": how}


def _served_memo(c):
    """Where this server gives a case's memo: the page, the PDF, and the name the PDF is saved under."""
    return {"page": f"/cases/{c['id']}/memo", "pdf": f"/cases/{c['id']}/memo.pdf", "name": memo.filename(c) + ".pdf"}


def _case_details(oids, public=False, memo_at=None):
    """The final form of the cases a run saved: the findings cards, and each case's own page with the event,
    the outcomes, the figures behind them, the evidence, and who to call. memo_at(case) says where the case's
    memo is: {page, pdf, name}. This server gives its own addresses (_served_memo), the guide the files it
    writes beside the page, and a page exported as one file has none."""
    if not oids:
        return {}
    out = {}
    con = db.connect()
    try:
        for c in briefs.cases(con, list(oids)):
            case, res, person = c["case"], c["results"], c["case"]["person"]
            of_events = lambda k: " ".join(e[k] for e in c["events"] if e.get(k)) or None  # noqa: E731
            cited = c.get("cited") or {}
            url_of = lambda oid: (cited.get(oid) or {}).get("url") if oid else None  # noqa: E731

            cur = case["currency"]
            label = lambda k: forms.label(k, case["inputs"][k], cur)  # noqa: E731   # an input by what it is, as the memo names it
            said = " ".join(x for x in (case.get("stream"), case.get("dependency"), c["company"].get("description"),
                                        (res.get("denominator") or {}).get("label")) if x)

            def changed(r):
                """The inputs that take another value in a world, each with its value there."""
                return "; ".join(f"{label(k)}: {briefs.figure(v, case['inputs'][k]['unit'], cur)[0]}"
                                 for k, v in r["values"].items())

            out[c["id"]] = {
                "oid": c["id"], "status": c["status"], "company": c["company"]["name"],
                "company_id": c["row"]["company_id"], "stream": title_label(case.get("stream_key"), said),
                "event": briefs.event_title(c), "event_date": briefs.event_date(c), "date_label": "Decided on or by",
                "event_text": of_events("text"), "resolution_source": of_events("resolution_source"),
                "dependency": case.get("dependency"),
                "stake": res.get("max_shortfall"), "share": res.get("max_share"),
                "of": (res.get("denominator") or {}).get("label"), "denominator_value": (res.get("denominator") or {}).get("value"),
                "currency": case.get("currency"),
                "period": f"{case['period']['start']} to {case['period']['end']}",
                "who": ", ".join(x for x in (person.get("name"), person.get("title")) if x) or person.get("role"),
                "deal": None if public else _deal_url(c["id"]),
                "memo": memo_at(c) if memo_at else None,
                "condition": c["condition"], "timing": case.get("timing"),
                "event_description": of_events("description"), "event_timing": of_events("timing"),
                "period_start": case["period"]["start"], "period_end": case["period"]["end"],
                "baseline": {"revenue": res.get("baseline_revenue"), "description": None},
                "scenarios": [{"label": r["label"], "summary": r["label"], "description": changed(r),
                               "revenue": r.get("revenue"), "shortfall": r.get("shortfall"), "share": r.get("share")}
                              for r in res.get("worlds") or []],
                "inputs": [_input(label(k), x, url_of) for k, x in (case.get("inputs") or {}).items()]
                + [_input(f"{label(k)}, given {briefs.given_condition(c, g)}", v | {"unit": case["inputs"][k]["unit"]}, url_of)
                   for g in case["given_values"] for k, v in g["values"].items()],
                "evidence": [{"passage": e.get("passage"), "supports": e.get("supports"), "checked": e.get("checked"), "url": url_of(e.get("observation_id"))}
                             for e in case.get("evidence") or []],
                "role": person.get("role")}
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


def public_cases(run_id, ids, memo_at=None):
    return example_review.reviewed_cases(run_id, _case_details(ids, public=True, memo_at=memo_at))


def public_leads(run_id, items):
    aside = [item["text"] for item in items if item.get("tool") == "record_finding" and item.get("finding") == "conclusion"
             and item.get("no_case") and item.get("status") == "ok" and item.get("text")]
    leads = [lead for item in items if item["k"] == "final" for lead in item.get("leads") or []]
    return example_review.reviewed_leads(run_id, aside, leads)


def _page(run_id, data=None, home="/"):
    run = _run_row(run_id)
    title = _title(run)
    purpose = _assignment(run)
    results_header = '<h2 id="findings-title">Company findings</h2>'
    share_meta = ""
    project_credit = ""
    if data and data.get("example_deal"):
        project_credit = '<a class="project-credit" href="https://github.com/owassmer">Built by <strong>Owen Wassmer</strong></a>'
        title, purpose = GUIDE_TITLE, GUIDE_PURPOSE
        replay = ('<button type="button" id="demo-replay" class="demo-play" aria-controls="activity" hidden>'
                  '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 2.5v11L13 8z"/></svg>'
                  '<span>Replay search · <span id="demo-replay-duration"></span></span></button>')
        results_header = f'<div class="demo-header"><h2 id="findings-title">Prospects</h2>{replay}</div>'
        description = html.escape(purpose, quote=True)
        share_meta = (f'<meta name="description" content="{description}">'
                      f'<meta property="og:title" content="{html.escape(title, quote=True)} · Distress Signals">'
                      f'<meta property="og:description" content="{description}"><meta property="og:type" content="website">')
    index_link = ""
    if data is None or home not in ("/", REPO, "#findings"):
        index_link = f'<a class="project-link operator-home" href="{html.escape(home, quote=True)}">All runs</a>'
    page = (PAGE.replace("__STYLE__", STYLE + SHELL_STYLE + findingsview.STYLE + activityview.STYLE + PUBLICATION)
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


def export_html(run_id, public=False, home=REPO, *, example_deal=False, memo_at=None):
    """One run as a single self-contained page: everything embedded, no server, with a replay button.
    public=True leaves out links into the operator's HubSpot. memo_at(case) gives each case the files of its
    memo, for a page written with them beside it (the guide); without it the page links to no memo."""
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
        data["cases"] = public_cases(run_id, v.cases, memo_at)
        data["lead_review"] = public_leads(run_id, _items(ops))
    else:
        data["cases"] = _case_details(v.cases, memo_at=memo_at)
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
        n = len(json.loads(r["case_ids"] or "[]"))
        pill = '<span class="pill running"><span class="pulse"></span>Live</span>' if state == "running" else \
            f'<span class="pill {state}">{state.capitalize()}</span>'
        cards.append(f"<a class='run' href='/runs/{r['id']}'><div class='what'><b>{html.escape(title)}</b>"
                     f"<div>{html.escape(sub or r['id'])}</div></div><div class='n'>{start.strftime('%b %-d, %-I:%M %p')}<br>"
                     f"{mins} min · {n} case{'s' if n != 1 else ''}{f' · ${cost:.2f}' if cost else ''}</div>{pill}</a>")
    body = "".join(cards) or "<div class='empty'>No runs yet. Request one in HubSpot with a record's Distress run field.</div>"
    return _listing("Runs", "Open one to watch it live, or replay a finished one.", body, "runs")


LIST_STYLE = """
.top .switch{margin-left:auto;display:flex;gap:4px;background:#eceef2;border-radius:99px;padding:3px}
.top .switch a{font-size:13px;font-weight:600;color:var(--mute);padding:6px 14px;border-radius:99px}
.top .switch a:hover{text-decoration:none;color:var(--ink)}.top .switch a.on{background:var(--card);color:var(--ink);box-shadow:0 1px 2px rgba(0,0,0,.08)}
.case{display:flex;align-items:center;gap:18px;background:var(--card);border:1px solid var(--line);border-radius:15px;padding:15px 20px;margin-bottom:10px}
.case .what{flex:1;min-width:0}.case .what a{color:var(--ink);font-weight:650}.case .what div{color:var(--mute);font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.case .n{font-size:13px;color:var(--mute);text-align:right;min-width:170px;font-variant-numeric:tabular-nums}.case .n b{color:var(--ink);font-size:15px}
.case .go{display:flex;gap:8px}.case .go .btn.dark{color:#fff}.case.withdrawn{opacity:.6}
@media(max-width:640px){.case{flex-wrap:wrap}.case .n{text-align:left;min-width:0}.top .switch{margin-left:0}}
"""


def _listing(title, sub, body, on):
    """The frame the two lists share: runs and cases, with the switch between them."""
    switch = "".join(f"<a href='{href}'{' class=on' if key == on else ''}>{label}</a>"
                     for key, href, label in (("runs", "/", "Runs"), ("cases", "/cases", "Cases")))
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'><title>{title} · distress-signals</title>"
            f"<link rel='icon' href=\"{FAVICON}\"><style>{STYLE}{LIST_STYLE}{PUBLICATION}</style></head><body><div class='top'><div class='in'>"
            f"<a class='brand' href='/'><img src=\"{FAVICON}\" alt=''>distress-signals</a><div class='divider'></div>"
            f"<div><div class='ttl'>{title}</div><div class='sub'>{sub}</div></div><div class='switch'>{switch}</div>"
            f"</div></div><div class='runs'>{body}</div></body></html>")


DOC_ICON = ('<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round" aria-hidden="true">'
            '<path d="M4 1.75h5.25L12.5 5v9.25h-8.5z"/><path d="M9.25 1.75V5h3.25"/><path d="M6.25 8.25h4M6.25 10.75h4"/></svg>')


def _home(con, case_id):
    """Where a case is shown in full: the page of the latest run that saved it, at the case."""
    known = {r["id"] for r in con.execute("SELECT id FROM runs")}
    run = next((r["run_id"] for r in reversed(db.revisions(con, case_id)) if r["run_id"] in known), None)
    return f"/runs/{run}#case/{case_id}" if run else None


def _cases_index():
    """Every saved case as it stands now, the latest first: what is at stake, and its memo as a page and a PDF."""
    con = db.connect()
    try:
        found = sorted(briefs.cases(con), key=lambda c: c["row"]["updated"], reverse=True)
        homes = {c["id"]: _home(con, c["id"]) for c in found}
    finally:
        con.close()
    cards = []
    for c in found:
        res, cur, e = c["results"], c["case"]["currency"], html.escape
        d = memo.document(c)
        share = f" · {d['at_stake']['share']}" if d["at_stake"]["share"] else ""
        name = e(c["company"]["name"])
        head = f"<a href='{e(homes[c['id']], quote=True)}'>{name}</a>" if homes[c["id"]] else f"<b>{name}</b>"
        cards.append(
            f"<div class='case{' withdrawn' if c['status'] == 'withdrawn' else ''}'><div class='what'>{head}"
            f"<div>{e(d['event'])}{' · withdrawn' if c['status'] == 'withdrawn' else ''}</div></div>"
            f"<div class='n'><b>{e(briefs.money(res['max_shortfall'], cur))}</b>{e(share)}<br>{e(d['period'])}</div>"
            f"<div class='go'><a class='btn sm dark' href='/cases/{c['id']}/memo'>{DOC_ICON}Memo</a>"
            f"<a class='btn sm' href='/cases/{c['id']}/memo.pdf' download='{e(memo.filename(c), quote=True)}.pdf'>PDF</a></div></div>")
    body = "".join(cards) or "<div class='empty'>No cases yet. A run saves them as it finds them.</div>"
    return _listing("Cases", "Each as it stands now. Open its memo, or take the PDF.", body, "cases")


def _memo_case(case_id):
    """A case with where it is shown, for its memo; None where the store holds no such case."""
    con = db.connect()
    try:
        found = briefs.cases(con, [case_id])
        return (found[0], _home(con, case_id)) if found else None
    finally:
        con.close()


def memo_page(case_id):
    """A case's memo as a page, with the way back to the case and the PDF across the top; None for no such case."""
    got = _memo_case(case_id)
    if not got:
        return None
    c, home = got
    bar = {"back": home or "/cases", "back_label": "Case" if home else "Cases", "pdf": f"/cases/{case_id}/memo.pdf",
           "name": memo.filename(c) + ".pdf"}
    return memo.html(memo.document(c), bar=bar)


_PDF, _PRINTING = {}, threading.Lock()


def memo_pdf(case_id):
    """(the PDF's bytes, its file name) for a case's memo, printed from the case as it stands; None for no such
    case. One is printed at a time, and a case's PDF is kept until the case next changes."""
    got = _memo_case(case_id)
    if not got:
        return None
    c, _ = got
    key = (case_id, c["row"]["revision"], c["row"]["updated"], c["status"])
    with _PRINTING:
        if key not in _PDF:
            import tempfile
            d = memo.document(c)
            with tempfile.TemporaryDirectory() as folder:
                path = memo.pdf(memo.html(d), os.path.join(folder, "memo.pdf"),
                                running=f"{d['company']} · {d['event']} · as of {d['as_of']}")
                with open(path, "rb") as f:
                    data = f.read()
            for old in [k for k in _PDF if k[0] == case_id]:
                del _PDF[old]
            _PDF[key] = data
        return _PDF[key], memo.filename(c) + ".pdf"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, body, kind="text/html; charset=utf-8", status=200, name=None):
        data = body if isinstance(body, bytes) else body.encode()
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        if name:                                                   # shown in the browser, and saved under this name
            self.send_header("Content-Disposition", f'inline; filename="{name}"')
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
                                                  "cases": _case_details(v.cases, memo_at=_served_memo)}, default=str), "application/json")
                if parts[2] == "export":
                    return self._send(export_html(rid, public="public" in parse_qs(u.query)))
            if parts[0] == "cases" and len(parts) == 1:
                return self._send(_cases_index())
            if parts[0] == "cases" and len(parts) == 3 and parts[1].startswith("case_"):
                if parts[2] == "memo":
                    page = memo_page(parts[1])
                    if page:
                        return self._send(page)
                if parts[2] == "memo.pdf":
                    got = memo_pdf(parts[1])
                    if got:
                        return self._send(got[0], "application/pdf", name=got[1])
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
