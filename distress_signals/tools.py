"""The researcher's domain functions. mcp_server.py exposes them; each takes and returns plain
JSON-able values and raises ToolError with a message the researcher can act on.

  search_records, read_record     public records and stored observations
  compute                         SQL over the retrieved records, saved results the case can cite
  record_finding                  research kept before (and beside) a case; can withdraw a case
  lookup                     saved cases, companies, events, findings and results, in current form
  calculate_exposure              the calculation save runs, on a draft
  save_opportunity                validate, check provenance, compute, store, revise

The analyst owns the economic judgment; code observes and computes. Every input in a case carries
one provenance (reported, derived or assumed) and the store checks the first two."""

import bisect
import copy
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
import unicodedata

from pydantic import ValidationError

from . import db, exposure, forms, names, outcomes
from .models import Calculation, Event, Finding, Opportunity
from . import sources
from .sources import web
from .util import SourceError, exclusions, now_iso


class ToolError(Exception):
    pass


_BRANCHES = {"str", "Event", "Company", "Calculation", "Opportunity", "Finding"}


def _invalid(e: ValidationError):
    """Pydantic's errors in the researcher's terms: field paths only, and no 'should be a string'
    noise from the ID-or-object fields when the object itself was given."""
    errs = e.errors()
    real = [x for x in errs if x["loc"][-1:] != ("str",)] or errs
    lines = []
    for err in real[:12]:
        loc = ".".join(str(x) for x in err["loc"] if not (isinstance(x, str) and ("[" in x or x in _BRANCHES)))
        lines.append(f"{loc}: {err['msg']}" if loc else err["msg"])
    more = len(real) - len(lines)
    return "Invalid input:\n" + "\n".join(lines) + (f"\n(and {more} more)" if more > 0 else "")


def _validate(model, payload):
    try:
        return model.model_validate(payload)
    except ValidationError as e:
        raise ToolError(_invalid(e))


# ---------- quoted passages and derived figures ----------

_MARKS = str.maketrans({"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"', "\u2013": "-",
                        "\u2014": "-", "\u2212": "-", "\u00a0": " "})


def _norm(s):
    s = unicodedata.normalize("NFKC", s or "").translate(_MARKS)
    return re.sub(r"\s+", " ", s).strip().casefold()


def _parts(passage):
    """The parts of a passage to find, in order: '...' marks an omission between them."""
    return [p for p in (frag.strip(" \"'") for frag in re.split(r"\.\.\.", _norm(passage))) if p]


def _find(frag, t, pos):
    i = t.find(frag, pos)
    if i < 0:
        frag = frag.rstrip(".,;:")
        i = t.find(frag, pos) if frag else -1
    return i, len(frag)


def quoted(passage, text):
    """True when the passage appears in the text, in order, allowing '...' for omissions."""
    t, pos = _norm(text), 0
    for frag in _parts(passage):
        i, n = _find(frag, t, pos)
        if i < 0:
            return False
        pos = i + n
    return True


def whole_sentences(passage, text):
    """The passage as the whole sentences of the text it falls in, by sentencex (a line broken inside a sentence
    does not end it), with each sentence's whitespace made single spaces; where lines run without sentence
    punctuation, as the rows of a table or a form do, each line is its own. Where the passage
    omits text between them ('...'), the sentences either side are joined by ' \u2026 '. The passage as given where
    it cannot be placed in the sentences."""
    from sentencex import segment
    spans, at = [], 0
    for s in segment("en", text):
        start = text.find(s, at) if s else -1
        if start < 0:
            continue
        at = start + len(s)
        if "\n" in s.strip() and not s.rstrip().rstrip("\"')]\u201d\u2019").endswith((".", "!", "?", ":", ";")):
            # lines that end in no sentence punctuation are rows of a table or a form: each line is its own
            line = start
            for row in s.split("\n"):
                spans.append((line, line + len(row)))
                line += len(row) + 1
        else:
            spans.append((start, at))
    kept, starts, normed, pos = [], [], [], 0
    for s, e in spans:
        n = _norm(text[s:e])
        if n:
            kept.append((s, e))
            starts.append(pos)
            normed.append(n)
            pos += len(n) + 1
    flat, pos, chosen = " ".join(normed), 0, []
    for frag in _parts(passage):
        i, n = _find(frag, flat, pos)
        if i < 0:
            return passage
        first, last = bisect.bisect_right(starts, i) - 1, bisect.bisect_right(starts, i + n - 1) - 1
        chosen += [k for k in range(first, last + 1) if k not in chosen]
        pos = i + n
    out, prev = [], None
    for k in sorted(chosen):
        if prev is not None:
            out.append(" " if k == prev + 1 or not text[kept[prev][1]:kept[k][0]].strip() else " \u2026 ")
        out.append(" ".join(text[kept[k][0]:kept[k][1]].split()))
        prev = k
    return "".join(out) or passage


_WORD = re.compile(r"[^\W\d_]{2,}")  # a word: two or more letters


def has_text(passage):
    """A cited passage is the section of text that states the claim: at least three words, so never a
    number alone."""
    return len(_WORD.findall(passage or "")) >= 3


def _cite(con, cite, where, cited_ids):
    """Resolve {observation_id|url, passage} to a stored observation and check the passage.
    Returns {observation_id, passage, checked}."""
    passage = cite["passage"]
    if not has_text(passage):
        raise ToolError(f"{where}: {passage[:80]!r} doesn't show what it states. Quote the relevant section of the "
                        "source word for word, never the number alone: the sentence that states the figure, or its "
                        "line item with the table's heading. '...' marks an omission.")
    if cite.get("url"):
        url = cite["url"].strip()
        oid = db.make_id("obs_", "page", url)
        # A bulk download can be the public URL of many distinct, independently stored rows.
        obs = db.get_observation(con, oid)
        if not (obs and obs.get("text") and quoted(passage, obs["text"])):
            matches = []
            for row in con.execute("SELECT id, text FROM observations WHERE url=? AND text IS NOT NULL", (url,)):
                if quoted(passage, row["text"]):
                    matches.append(row["id"])
                elif obs is None or not obs.get("text"):
                    obs = db.get_observation(con, row["id"])
            if len(matches) > 1:
                raise ToolError(f"{where}: several records at {url} contain this passage. Cite its observation_id.")
            if matches:
                obs = db.get_observation(con, matches[0])
        if obs is None:
            db.put_observation(con, {"id": oid, "kind": "page", "source": "web", "url": url, "retrieved_at": now_iso(),
                                     "text": None, "data": {"unchecked": True, "passages": [passage]}})
            obs = db.get_observation(con, oid)
    else:
        obs = db.get_observation(con, cite["observation_id"])
        if obs is None:
            raise ToolError(f"{where}: no observation {cite['observation_id']!r}")
    cited_ids.append(obs["id"])
    db.mark_read(con, obs["id"])
    if obs.get("text"):
        if not quoted(passage, obs["text"]):
            raise ToolError(f"{where}: passage not found word for word in {obs['id']} ({obs.get('url') or obs['source']}): "
                            f"{passage[:160]!r}. Quote it exactly, using '...' for omissions; read_record(id, find=...) "
                            "shows the stored text.")
        passage = whole_sentences(passage, obs["text"])  # what is stored and shown is the whole sentence quoted from
        checked = True
    elif obs["data"].get("unchecked"):
        if passage not in obs["data"].setdefault("passages", []):
            obs["data"]["passages"].append(passage)
            db.put_observation(con, obs)
        checked = False
    else:
        raise ToolError(f"{where}: {obs['id']} has no text to quote. Compute the figure from its rows with compute "
                        "and cite the result as derived.")
    return {"observation_id": obs["id"], "passage": passage, "checked": checked}


def _derived(con, name, inp, used):
    """A derived input's value must be a value in the workspace result it names."""
    rid = inp["derived"]["result_id"]
    r = db.get_result(con, rid)
    if not r:
        raise ToolError(f"inputs.{name}.derived: no saved result {rid!r}; save one with compute(save_as=...)")
    cells = [v for row in r["rows"] for v in row if isinstance(v, (int, float)) and not isinstance(v, bool)]
    v = inp["value"]
    if not any(abs(c - v) <= max(abs(c) * 1e-6, 0.005) for c in cells):
        raise ToolError(f"inputs.{name}: value {v} isn't in result {rid} ({r['name']}); its first rows: "
                        f"{json.dumps(r['rows'][:3])[:300]}")
    used.append(rid)


def _need_observation(con, oid, where, cited_ids):
    if db.get_observation(con, oid) is None:
        raise ToolError(f"{where}: no observation {oid!r}")
    cited_ids.append(oid)


# ---------- companies ----------

def _excluded(names_):
    for raw in names_:
        for n, reason in exclusions():
            if names.matches(raw, n):
                return f"{reason}: {n}"
    return ""


def _merge_aliases(name, *lists):
    seen, out = {name.casefold()}, []
    for a in (x for lst in lists for x in lst or []):
        if a and a.casefold() not in seen:
            seen.add(a.casefold())
            out.append(a)
    return out


def _resolve_company(con, c, warnings):
    """(company ID, company data). A saved ID alone reads it. A company given with its id edits that
    company. A company given without one matches by domain, else by an unambiguous name, keeping what
    is already known about it, or is saved as new."""
    if isinstance(c, str):
        row = db.get_company(con, c)
        if not row:
            raise ToolError(f"company: no saved company {c!r}. Give the company in full, or an ID from lookup.")
        return row["id"], row["data"]
    data = c.model_dump(mode="json")
    given = data.pop("id", None)
    domain = names.domain_key(data.get("domain")) or None
    data["domain"] = domain
    reason = _excluded([data["name"], *data["aliases"]])
    if reason:
        raise ToolError(f"company: {data['name']} is excluded by campaign preference ({reason}).")
    listed = lambda rows: "; ".join(f"{r['name']} ({r['domain'] or 'no domain'}, {r['id']})" for r in rows)
    if given:
        row = db.get_company(con, given)
        if not row:
            raise ToolError(f"company.id: no saved company {given!r}")
        other = db.companies_by_domain(con, domain) if domain else None
        if other and other["id"] != given:
            raise ToolError(f"company: domain {domain} belongs to {listed([other])}")
    else:
        keys = {names.vendor_key(n) for n in [data["name"], *data["aliases"]]} - {""}
        candidates = list({r["id"]: r for k in keys for r in db.companies_by_key(con, k)}.values())
        row = db.companies_by_domain(con, domain) if domain else None
        if row is None and domain:
            undomained = [r for r in candidates if not r["domain"]]
            if len(undomained) > 1:
                raise ToolError(f"company: {data['name']} matches several saved companies without a domain: "
                                f"{listed(undomained)}. Pass the right company's id.")
            row = undomained[0] if undomained else None
            others = [r for r in candidates if r["domain"] and r["domain"] != domain]
            if row is None and others:
                warnings.append(f"Saved {data['name']} at {domain} as a new company; {listed(others)} has the same name. "
                                "If it is the same company, pass its id.")
        elif row is None and len(candidates) > 1:
            raise ToolError(f"company: {data['name']} matches several saved companies: {listed(candidates)}. "
                            "Give its domain, or pass the company's id.")
        elif row is None and candidates:
            row = candidates[0]
    if row:
        old = row["data"]
        data = {**old, **{k: v for k, v in data.items() if v not in (None, [], "")},
                "aliases": _merge_aliases(data["name"], data["aliases"], [old["name"]], old.get("aliases")),
                "domain": domain or old.get("domain")}
        cid = row["id"]
        con.execute("DELETE FROM company_keys WHERE company_id=?", (cid,))
    else:
        cid = db.make_id("co_", domain or "name:" + names.vendor_key(data["name"]))
    keys = {names.vendor_key(n) for n in [data["name"], *data["aliases"]]} - {""}
    db.put_company(con, cid, data["name"], data["domain"], data, keys)
    return cid, data


def _principal_check(company):
    """A company run's cases are for its principal company; others become findings or leads."""
    principal = os.environ.get("DISTRESS_RUN_PRINCIPAL")
    if not principal:
        return
    own = [company["name"], *company.get("aliases", [])]
    if not any(names.matches(n, principal) or names.matches(principal, n) for n in own):
        raise ToolError(f"This run's assignment is {principal}; its cases are for that company. Record what you found "
                        f"about {company['name']} with record_finding, and name it in follow_on_leads.")


# ---------- events ----------

def _outcome_list(event):
    """The event's outcomes, one to a line, each way under the outcome it lies within."""
    return "\n".join(f"{'  ' * d}{k}: {n['title']}" for k, n, _, d in outcomes.tree(event))


def _resolve_event(con, e, cited_ids, saving=None):
    """(event ID, event data). A saved key or ID reads the event; an event given in full is saved,
    replacing the saved definition of the same key. The other cases on the event read the revised
    definition from then on, so it keeps every outcome one of them names: an outcome's key is its
    identity, and a finer distinction is drawn by giving the outcome ways. `saving` is the case being
    saved with the event, whose own versions are checked against the new definition."""
    if isinstance(e, str):
        row = db.get_event(con, e)
        if not row:
            raise ToolError(f"event: no saved event {e!r}. Give the event in full, or a key or ID from lookup.")
        data = forms.event(row["data"])
        cited_ids += [c["observation_id"] for c in data.get("evidence") or []]
        return row["id"], data
    data = e.model_dump(mode="json")
    given = data.pop("id", None)
    row = db.get_event(con, data["key"])
    eid = row["id"] if row else db.make_id("ev_", data["key"])
    if given and given != eid:
        raise ToolError(f"event: id {given} belongs to a different event than key {data['key']!r}. The key stays the "
                        "same when an event is revised; keep the saved key, or leave id out to save a new event.")
    data["evidence"] = [_cite(con, c, f"event.evidence[{i}]", cited_ids) for i, c in enumerate(data["evidence"])]
    data = forms.event(data)
    if row:
        left = [(r["id"], gone) for r in db.opportunities(con, "event_id=? AND status='active'", (eid,))
                if r["id"] != saving and (gone := outcomes.unknown(data, r["case_json"]))]
        if left:
            raise ToolError(f"event: saved cases on {data['key']!r} state revenue under outcomes this definition leaves "
                            "out: " + "; ".join(f"{oid} under {', '.join(gone)}" for oid, gone in left) + ". An outcome's "
                            "key stays the same when its wording is revised, and a finer distinction is drawn by giving "
                            "the outcome ways. Keep those outcomes, or revise those cases first.")
    if not row or forms.event(row["data"]) != data:
        db.put_event(con, eid, data["key"], data["title"], data)
    return eid, data


def _check_outcomes(event, case):
    """Every version names an outcome of the event, and the outcomes named lie apart."""
    if not outcomes.stated(event):
        raise ToolError(f"event: {event.get('key')!r} was saved before events carried outcomes. Give it in full, with its "
                        "text (the claim that will turn out true or false), kind, window_end, resolution_source and "
                        "the ways of each side.")
    gone = outcomes.unknown(event, case)
    if gone:
        raise ToolError(f"affected: the event has no outcome {', '.join(repr(k) for k in gone)}. A version's outcome is "
                        f"'yes', 'no' or the key of one of the event's ways. Its outcomes:\n{_outcome_list(event)}")
    inside = outcomes.overlapping(event, case)
    if inside:
        raise ToolError("affected: " + "; ".join(f"{a!r} lies within {b!r}" for a, b in inside) + ". A version's figure "
                        "is for its outcome as a whole, so the outcomes a case names lie apart: name the outcome whole, "
                        "or name each of its ways that the case has a figure for.")


# ---------- the calculation ----------

def _key_date(event, case):
    return event.get("window_end") or case["case"].get("why_now_date")


def _derive(case):
    """The one calculation path, on the stored form of inputs, baseline, affected and denominator."""
    try:
        return exposure.compute({k: v["value"] for k, v in case["inputs"].items()}, case["baseline"], case["affected"],
                                case.get("denominator"))
    except ValueError as e:
        raise ToolError(str(e))


def _digest(snapshot):
    return hashlib.sha1(db.dumps(snapshot).encode()).hexdigest()


def _write(con, oid, row_ids, case, results, event, snapshot, change_note, last):
    """Write a revision when the snapshot differs from the last one. Returns (revision, changed)."""
    digest = _digest(snapshot)
    if last and last["hash"] == digest:
        return last["revision"], False
    revision = (last["revision"] if last else 0) + 1
    db.put_opportunity(con, {"id": oid, **row_ids, "priority": case["case"]["priority"],
                             "key_date": _key_date(event, case), "max_loss": results["max_shortfall"],
                             "currency": case["currency"], "case_json": case, "results": results,
                             "revision": revision})
    db.add_revision(con, oid, revision, digest, change_note, snapshot)
    return revision, True


def calculate_exposure(case: dict) -> dict:
    """Run the calculation save_opportunity runs, on a draft."""
    c = _validate(Calculation, case)
    results, warnings = _derive(c.model_dump(mode="json"))
    return {"results": results, "warnings": warnings}


# ---------- save ----------

def save_opportunity(opportunity: dict) -> dict:
    """Validate, check provenance, compute results, save company, event and case, and write a revision
    when the content changed."""
    o = _validate(Opportunity, opportunity)
    con = db.connect()
    try:
        with db.write(con):
            out = _save(con, o)
    finally:
        con.close()
    _deliver(out["opportunity_id"])
    return out


def _deliver(opportunity_id):
    """Put a saved or withdrawn case in front of the operator in HubSpot. A delivery problem never fails
    the save: the worker delivers changed cases again."""
    from . import hubspot
    if not hubspot.configured():
        return
    try:
        hubspot.sync_case(opportunity_id)
    except Exception as e:
        print(f"HubSpot delivery of {opportunity_id} failed: {type(e).__name__}: {str(e)[:300]}", file=sys.stderr)


def _crm(con, company_row):
    """What the operator's HubSpot holds for a company, when HubSpot is set up."""
    from . import hubspot
    if not hubspot.configured():
        return {}
    try:
        return {"crm": hubspot.company_view(con, company_row)}
    except Exception as e:
        print(f"HubSpot read for {company_row['id']} failed: {type(e).__name__}: {str(e)[:300]}", file=sys.stderr)
        return {}


def _save(con, o: Opportunity):
    cited_ids, results_used, warnings = [], [], []
    company_id, company = _resolve_company(con, o.company, warnings)
    _principal_check(company)
    if not company.get("description"):
        warnings.append("The company has no description of how it earns its revenue.")
    key = o.event if isinstance(o.event, str) else o.event.key
    event_id = (db.get_event(con, key) or {}).get("id") or db.make_id("ev_", key)
    oid = o.opportunity_id or db.make_id("opp_", company_id, event_id, o.stream_key)
    existing = db.get_opportunity(con, oid)
    if o.opportunity_id:
        if not existing:
            raise ToolError(f"opportunity_id: no saved case {o.opportunity_id!r}")
        was = (existing["company_id"], existing["event_id"], existing["stream_key"])
        if was != (company_id, event_id, o.stream_key):
            co, ev = db.get_company(con, was[0]), db.get_event(con, was[1])
            raise ToolError(f"{o.opportunity_id} is the case for {co['name']} / {ev['key']} / {was[2]}, but this save "
                            "names a different company, event or stream_key. Keep them to revise that case, or save a "
                            "different exposure without opportunity_id.")
    elif existing:
        warnings.append(f"Revised the saved case {oid} for this company, event and stream_key.")
    event_id, event = _resolve_event(con, o.event, cited_ids, saving=oid)

    case = o.model_dump(mode="json", exclude={"opportunity_id", "company", "event", "change_note"})
    _check_outcomes(event, case)
    for name, inp in case["inputs"].items():
        if inp.get("source"):
            inp["source"] = _cite(con, inp["source"], f"inputs.{name}.source", cited_ids)
        elif inp.get("derived"):
            _derived(con, name, inp, results_used)
    case["evidence"] = [_cite(con, ev, f"evidence[{i}]", cited_ids) | {"supports": ev["supports"]}
                        for i, ev in enumerate(case["evidence"])]
    if case["buyer"].get("observation_id"):
        _need_observation(con, case["buyer"]["observation_id"], "buyer.observation_id", cited_ids)
    elif case["buyer"].get("name"):
        warnings.append("The person is named without a source; give buyer.observation_id.")
    else:
        warnings.append("No person named yet.")

    scaled = re.compile(r"(?i)\b(thousands?|millions?|billions?|mn|bn|mm)\b")
    money = re.compile(rf"(?i)\b{o.currency}\b|[$€£¥]")
    for name, inp in case["inputs"].items():
        if money.search(inp["unit"]) and scaled.search(inp["unit"]):
            warnings.append(f"inputs.{name}: its unit is '{inp['unit']}', but amounts of money are in whole units of "
                            f"{o.currency}, so the results read {inp['value']:,} as {o.currency} {inp['value']:,}.")
    results, calc_warnings = _derive(case)
    warnings += calc_warnings
    snapshot = {"company": {"id": company_id, **company}, "event": {"id": event_id, **event},
                "case": case, "results": results}
    last = db.latest_revision(con, oid)
    revision, changed = _write(con, oid, {"company_id": company_id, "event_id": event_id, "stream_key": o.stream_key},
                               case, results, event, snapshot, o.change_note, last)
    if changed:
        db.set_citations(con, oid, cited_ids)
    if existing and existing.get("status") == "withdrawn":
        db.set_status(con, oid, "active")
        warnings.append(f"{oid} was withdrawn; saving it returns it to the active queue.")
    unchecked = sum(not c["checked"] for c in [*(i["source"] for i in case["inputs"].values() if i.get("source")),
                                                *case["evidence"], *event.get("evidence", [])])
    return {"opportunity_id": oid, "company_id": company_id, "event_id": event_id, "revision": revision,
            "changed": changed, "condition": outcomes.condition(event, case),
            "open_outcomes": outcomes.open_outcomes(event, case), "whole_outcomes": outcomes.whole(event, case),
            "results": results, "warnings": warnings,
            "unchecked_passages": unchecked, "workspace_results_used": sorted(set(results_used))}


# ---------- findings ----------

def record_finding(finding: dict) -> dict:
    f = _validate(Finding, finding)
    con = db.connect()
    try:
        with db.write(con):
            warnings, cited = [], []
            company_id = _resolve_company(con, f.company, warnings)[0] if f.company else None
            event_id = None
            if isinstance(f.event, Event):
                event_id = _resolve_event(con, f.event, cited)[0]
            elif f.event:
                row = db.get_event(con, f.event)
                if not row:
                    raise ToolError(f"event: no saved event {f.event!r}; give the event in full to save it")
                event_id = row["id"]
            if f.opportunity_id and not db.get_opportunity(con, f.opportunity_id):
                raise ToolError(f"opportunity_id: no saved case {f.opportunity_id!r}")
            for rid in f.results:
                if not db.get_result(con, rid):
                    raise ToolError(f"results: no saved result {rid!r}")
            if f.supersedes and not db.get_finding(con, f.supersedes):
                raise ToolError(f"supersedes: no finding {f.supersedes!r}")
            evidence = [_cite(con, c.model_dump(mode="json"), f"evidence[{i}]", cited) for i, c in enumerate(f.evidence)]
            fid = db.make_id("fnd_", f.kind, f.statement, company_id, event_id, now_iso())
            db.put_finding(con, {"id": fid, "kind": f.kind, "statement": f.statement, "company_id": company_id,
                                 "event_id": event_id, "opportunity_id": f.opportunity_id, "evidence": evidence,
                                 "results": f.results, "supersedes": f.supersedes})
            if f.withdraw_case:
                db.set_status(con, f.opportunity_id, "withdrawn")
                db.set_progress(con, f.opportunity_id, {**(db.get_opportunity(con, f.opportunity_id)["progress"] or {}),
                                                        "withdrawn_by": fid})
    finally:
        con.close()
    if f.withdraw_case:
        _deliver(f.opportunity_id)
    return {"finding_id": fid, "company_id": company_id, "event_id": event_id,
            **({"withdrawn": f.opportunity_id} if f.withdraw_case else {}),
            "unchecked_passages": sum(not e["checked"] for e in evidence), "warnings": warnings}


# ---------- the workspace ----------

MAX_ROWS = 2000      # rows a saved result keeps
SHOW_ROWS = 200      # rows a call returns
QUERY_SECONDS = 15


def _workspace():
    """An in-memory database holding what the current run has read and recorded: its observations, a table
    for each series with one row for each record read (the latest read of it), the views that cross series,
    and its findings and workspace results. Saved cases, companies, events and earlier runs' research
    aren't in it."""
    src, mem = db.connect(), sqlite3.connect(":memory:")
    run = os.environ.get("DISTRESS_RUN_ID")
    q = lambda name: '"' + name + '"'  # noqa: E731

    def chunks(sql, keys):
        for i in range(0, len(keys), 500):
            part = keys[i:i + 500]
            yield from src.execute(sql.format(",".join("?" * len(part))), part)
    try:
        ids = db.run_observations(src)
        for t, (col, own) in {"observations": ("id", False), "findings": ("run_id", True),
                              "workspace_results": ("run_id", True)}.items():
            cols = [r[1] for r in src.execute(f"PRAGMA table_info({t})")]
            mem.execute(f"CREATE TABLE {t} ({', '.join(cols)})")
            keys = ([run] if run else None) if own else ids
            rows = src.execute(f"SELECT {', '.join(cols)} FROM {t}") if keys is None else \
                chunks(f"SELECT {', '.join(cols)} FROM {t} WHERE {col} IN ({{}})", keys)
            mem.executemany(f"INSERT INTO {t} VALUES ({','.join('?' * len(cols))})", [tuple(r) for r in rows])
        read_at = dict(mem.execute("SELECT id, retrieved_at FROM observations"))
        for t in sources.tables():
            cols = [r[1] for r in src.execute(f"PRAGMA table_info({q(t)})")]
            mem.execute(f"CREATE TABLE {q(t)} ({', '.join(q(c) + (' PRIMARY KEY' if c == 'record_id' else '') for c in cols)})")
            rows = sorted(chunks(f"SELECT {', '.join(map(q, cols))} FROM {q(t)} WHERE observation_id IN ({{}})", ids),
                          key=lambda r: read_at.get(r["observation_id"]) or "")
            mem.executemany(f"INSERT OR REPLACE INTO {q(t)} VALUES ({','.join('?' * len(cols))})", [tuple(r) for r in rows])
        for view, sql in sources.views().items():
            mem.execute(f"CREATE VIEW {view} AS {sql}")
    finally:
        src.close()
    mem.execute("PRAGMA query_only=ON")
    return mem


def compute(sql: str, save_as: str | None = None, note: str | None = None) -> dict:
    """One read-only SQL query over what this run has read and recorded."""
    q = (sql or "").strip().rstrip(";")
    if not re.match(r"(?is)^(select|with)\b", q):
        raise ToolError("compute runs one SELECT (or WITH ... SELECT) query")
    con = _workspace()
    deadline = time.monotonic() + QUERY_SECONDS
    con.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10000)
    try:
        cur = con.execute(q)
        cols = [c[0] for c in cur.description or []]
        rows = [list(r) for r in cur.fetchmany(MAX_ROWS + 1)]
    except Exception as e:
        raise ToolError(f"query failed: {e}" + (" (stopped after 15 seconds)" if time.monotonic() > deadline else ""))
    finally:
        con.close()
    more = len(rows) > MAX_ROWS
    rows = rows[:MAX_ROWS]
    out = {"columns": cols, "rows": rows[:SHOW_ROWS], "row_count": len(rows), **({"more_rows": True} if more else {})}
    if len(rows) > SHOW_ROWS:
        out["note"] = f"Showing {SHOW_ROWS} of {len(rows)} rows; aggregate, or save the result to keep them all."
    if save_as:
        rid = db.make_id("wk_", save_as, q)
        w = db.connect()
        try:
            with db.write(w):
                db.put_result(w, {"id": rid, "name": save_as, "sql": q, "columns": cols, "rows": rows,
                                  "row_count": len(rows), "note": note})
        finally:
            w.close()
        out["result_id"] = rid
    return out


# ---------- reading saved research ----------

def _editable(oid, current):
    """The saved case in the exact form save_opportunity accepts: company and event by ID, store-set
    fields removed."""
    case = copy.deepcopy(current["case"])
    case.pop("condition", None)
    for inp in case["inputs"].values():
        if inp.get("source"):
            inp["source"].pop("checked", None)
    for ev in case["evidence"]:
        ev.pop("checked", None)
    return {"opportunity_id": oid, "company": current["company"]["id"], "event": current["event"]["id"], **case,
            "change_note": ""}


def _event_view(data):
    e = forms.event(data)
    e["evidence"] = [{k: v for k, v in c.items() if k != "checked"} for c in e.get("evidence") or []]
    return e


def _finding_view(con, f):
    return {"finding_id": f["id"], "kind": f["kind"], "statement": f["statement"], "company_id": f["company_id"],
            "event_id": f["event_id"], "opportunity_id": f["opportunity_id"], "results": f["results"],
            "evidence": [{"observation_id": e["observation_id"], "passage": e["passage"]} for e in f["evidence"]],
            "created": f["created"]}


def _brief(con, row):
    """A case's dated estimate in brief: who, which stream, over what period, the baseline and its reference
    conditions, and the revenue and shortfall in each world the case names, with the formula behind each.
    lookup of the case gives the inputs and their bases."""
    c, e = db.get_company(con, row["company_id"]), db.get_event(con, row["event_id"])
    last = db.latest_revision(con, row["id"])
    current = forms.snapshot(last["snapshot"])
    case, res, event = current["case"], current["results"], forms.event(e["data"])
    told = outcomes.stated(event)
    by_name = {a["name"]: a for a in case["affected"]}
    return {"opportunity_id": row["id"], "status": row["status"], "company": c["name"], "event": e["key"],
            "stream": case["stream"], "period": case["period"], "estimated": last["saved_at"],
            "condition": outcomes.condition(event, case),
            "baseline": {"description": case["baseline"]["description"], "formula": case["baseline"]["revenue"],
                         "revenue": res["baseline_revenue"]},
            "worlds": [{"outcome": r["outcome"], "version": r["name"], "description": by_name[r["name"]]["description"],
                        "formula": by_name[r["name"]]["revenue"], "revenue": r["revenue"], "shortfall": r["shortfall"],
                        "share": r["share"]} for r in res["affected"]],
            "open_outcomes": outcomes.open_outcomes(event, case) if told else [],
            "whole_outcomes": outcomes.whole(event, case) if told else [],
            "priority": row["priority"], "max_shortfall": res["max_shortfall"], "currency": row["currency"],
            "revision": row["revision"], "updated": row["updated"]}


UPDATE = ("Edit 'opportunity' and pass it to save_opportunity with a change_note. To edit the company or the event, put "
          "its object (with its id) in 'opportunity' in place of the ID. record_finding with withdraw_case withdraws "
          "the case. read_record(observation_id) returns any cited record in full.")


def lookup(id_or_query: str) -> dict:
    """A saved case (opp_...), company (co_...), event (ev_...), finding (fnd_...) or workspace result
    (wk_...) by its ID; a company by its name, alias or domain; an event by its key or title. Cases and
    events are given in their current form."""
    q = (id_or_query or "").strip()
    if not q:
        raise ToolError("give an ID, a company's name, alias or domain, or an event's key or title")
    con = db.connect()
    try:
        if q.startswith("opp_"):
            row = db.get_opportunity(con, q)
            if not row:
                raise ToolError(f"no saved case {q!r}")
            current = forms.snapshot(db.latest_revision(con, q)["snapshot"])
            company = db.get_company(con, row["company_id"])
            ev_row = db.get_event(con, row["event_id"])
            current["company"] = {"id": company["id"], **company["data"]}
            current["event"] = {"id": ev_row["id"], **forms.event(ev_row["data"])}
            titles = {o["id"]: o for o in db.cited(con, q)}
            case, event = current["case"], current["event"]
            evidence = [{"at": f"inputs.{k}", "passage": v["source"]["passage"], "checked": v["source"].get("checked"),
                         "observation_id": v["source"]["observation_id"]}
                        for k, v in case["inputs"].items() if v.get("source")]
            evidence += [{"at": f"evidence[{i}]", "passage": e["passage"], "checked": e.get("checked"),
                          "supports": e["supports"], "observation_id": e["observation_id"]}
                         for i, e in enumerate(case["evidence"])]
            for e in evidence:
                o = titles.get(e["observation_id"]) or {}
                e["source"] = {"title": (o.get("data") or {}).get("title"), "url": o.get("url"), "kind": o.get("kind")}
            fs = db.findings(con, "opportunity_id=? OR company_id=?", (q, row["company_id"]))
            return {"opportunity": _editable(q, current), "status": row["status"],
                    **({"format_notes": current["notes"]} if current["notes"] else {}),
                    "company_id": company["id"], "company": company["data"],
                    "event_id": ev_row["id"], "event": _event_view(ev_row["data"]),
                    "condition": outcomes.condition(event, case),
                    "open_outcomes": outcomes.open_outcomes(event, case) if outcomes.stated(event) else [],
                    "whole_outcomes": outcomes.whole(event, case) if outcomes.stated(event) else [],
                    "results": current["results"],
                    "warnings": current["warnings"], "evidence": evidence,
                    "findings": [_finding_view(con, f) for f in fs], "revision": row["revision"],
                    **({"crm": {k: v for k, v in (row["progress"] or {}).items() if k in ("stage", "outcome", "outcome_note")}}
                       if row["progress"] else {}),
                    "updated": row["updated"], "how_to_update": UPDATE,
                    "revisions": db.revisions(con, q)[-8:]}
        if q.startswith("co_"):
            c = db.get_company(con, q)
            if not c:
                raise ToolError(f"no saved company {q!r}")
            return {"company_id": q, "company": c["data"],
                    "cases": [_brief(con, r) for r in db.opportunities(con, "company_id=?", (q,))],
                    "findings": [_finding_view(con, f) for f in db.findings(con, "company_id=?", (q,))], **_crm(con, c)}
        if q.startswith("fnd_"):
            f = db.get_finding(con, q)
            if not f:
                raise ToolError(f"no finding {q!r}")
            return _finding_view(con, f) | {"status": f["status"], "supersedes": f["supersedes"]}
        if q.startswith("wk_"):
            r = db.get_result(con, q)
            if not r:
                raise ToolError(f"no saved result {q!r}")
            return {"result_id": q, "name": r["name"], "sql": r["sql"], "note": r["note"], "columns": r["columns"],
                    "rows": r["rows"][:SHOW_ROWS], "row_count": r["row_count"], "created": r["created"]}
        e = db.get_event(con, q)
        if e:
            return {"event_id": e["id"], "event": _event_view(e["data"]),
                    "cases": [_brief(con, r) for r in db.opportunities(con, "event_id=?", (e["id"],))],
                    "findings": [_finding_view(con, f) for f in db.findings(con, "event_id=?", (e["id"],))]}
        key, domain = names.vendor_key(q), names.domain_key(q)
        cos = {r["id"]: r for r in db.companies_by_key(con, key)} if key else {}
        if domain and "." in domain and db.companies_by_domain(con, domain):
            row = db.companies_by_domain(con, domain)
            cos[row["id"]] = row
        if not cos:
            row = con.execute("SELECT id FROM events WHERE lower(title)=?", (q.lower(),)).fetchone()
            if row:
                return lookup(row["id"])
            raise ToolError(f"Nothing saved is named {q!r}. lookup takes an ID, a company's name, alias or domain, "
                            "or an event's key or title.")
        return {"companies": [{"company_id": c["id"], "company": c["data"],
                               "cases": [_brief(con, r) for r in db.opportunities(con, "company_id=?", (c["id"],))],
                               "findings": [_finding_view(con, f) for f in db.findings(con, "company_id=?", (c["id"],))],
                               **_crm(con, c)}
                              for c in cos.values()]}
    finally:
        con.close()


# ---------- records ----------

VIEW = 12000  # characters of text a read returns at once


def search_records(series: str, query: str = "", filters: dict | None = None, limit: int = sources.LIMIT,
                   cursor: str | None = None) -> dict:
    try:
        return sources.search(series, query, filters, limit, cursor)
    except SourceError as e:
        raise ToolError(str(e))


def _locate(ref):
    """(observation ID, fetch function) for a URL, a record_id or a result_set."""
    if re.match(r"^https?://", ref):
        return db.make_id("obs_", "page", ref), lambda: web.read(ref)
    if not sources.parse(ref):
        raise ToolError("read_record takes a record_id or a result_set that search_records returned, a URL, or an "
                        "observation ID (obs_...)")

    def fetch():
        got = sources.read(ref)
        return got
    series, kind, rest = sources.parse(ref)
    return db.make_id("obs_", series.id, kind, rest), fetch


def _source_ref(obs):
    """The reference that reads an observation's source again."""
    return sources.again(obs) or obs.get("url")


def _view(obs, find=None, offset=0):
    text, data = obs.get("text") or "", obs.get("data") or {}
    out = {"observation_id": obs["id"], "kind": obs["kind"], "source": obs["source"], "url": obs.get("url"),
           "title": data.get("title"), "published": obs.get("published"), "retrieved_at": obs["retrieved_at"],
           "length": len(text)}
    if obs["kind"] in ("record", "result_set"):
        out["workspace_table"] = obs["source"]
        if data.get("count") is not None:
            out["records"] = data["count"]
    if not text and data.get("unchecked"):
        out["note"] = "Only quoted passages are stored for this page (read with WebFetch)."
        out["passages"] = data.get("passages", [])
        return out
    if find:
        low, hits = text.lower(), []
        # plain text, not a pattern: a backslash written as a regex escape (\$, \.) is dropped
        for term in [re.sub(r"\\(.)", r"\1", t).strip() for t in find.split("|") if t.strip()]:
            start, n = 0, 0
            while n < 8 and len(hits) < 24:
                i = low.find(term.lower(), start)
                if i < 0:
                    break
                hits.append({"term": term, "offset": i, "snippet": text[max(0, i - 600): i + len(term) + 600]})
                start, n = i + len(term), n + 1
        out["matches"] = hits
        if not hits:
            out["note"] = "No matches. Try other words, or read the text by offset."
        return out
    out["text"] = text[offset: offset + VIEW]
    if offset + VIEW < len(text):
        out["next_offset"] = offset + VIEW
    if offset == 0 and data.get("links"):
        out["links"] = data["links"][:80]
    return out


def read_record(id_or_url: str, find: str | None = None, offset: int = 0, fresh: bool = False) -> dict:
    """Read a record and store it as an observation. A stored record is reused unless fresh, which reads
    its source again (for an observation ID too) and updates the same observation and its rows."""
    ref = (id_or_url or "").strip()
    con = db.connect()
    try:
        if ref.startswith("obs_"):
            obs = db.get_observation(con, ref)
            if not obs:
                raise ToolError(f"no observation {ref!r}")
            if not fresh:
                with db.write(con):
                    db.mark_read(con, ref)
                return _view(obs, find, offset)
            src = _source_ref(obs)
            if not src:
                raise ToolError(f"{ref} has no source that can be read again")
            oid, fetch = ref, _locate(src)[1]
        else:
            oid, fetch = _locate(ref)
            obs = db.get_observation(con, oid)
            if obs and obs.get("text") and not fresh:
                with db.write(con):
                    db.mark_read(con, oid)
                return _view(obs, find, offset)
        try:
            got = fetch()
        except SourceError as e:
            raise ToolError(str(e))
        with db.write(con):
            db.put_observation(con, {**got, "id": oid, "retrieved_at": now_iso()})
            db.mark_read(con, oid)
        return _view(db.get_observation(con, oid), find, offset)
    finally:
        con.close()
