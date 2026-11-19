"""HubSpot is the operator's surface. Python writes every record and reads requests and outcomes back:

  - the company, matched by domain (else by name) and created with its name, domain and description; a
    company already in HubSpot is linked, never overwritten
  - one deal per case, matched by distress_opportunity_id, carrying the case's fields and its memo. A new
    deal starts in the default pipeline's "Proposed" stage; the stage and the outcome are the operator's
  - the case's person as a contact (name, title, responsibility and the source that names them), linked
    to the company and the deal. No email is written, and none is ever constructed

Saving or withdrawing a case delivers it at once (sync_case); push re-delivers whatever changed. A record's
"Distress run" field requests research: on a company, research it, research the event described in the
note, run the standing mandate, or run the note as a brief; on a case's deal, refresh it. The worker (worker.py) runs requests and writes their
status and results back here; follow-on leads that name a company become proposed companies.
pull_outcomes reads each deal's stage, outcome and note back onto its case.

Functions the MCP server calls (sync_case, company_view) never print: its stdout carries the protocol.
"""

import hashlib
import html
import json
import os
import sys
import time

from . import briefs, db
from .util import OUT, now_iso, session

API = "https://api.hubapi.com"
DEST = "hubspot"
GROUP = {"name": "distress_signals", "label": "Distress signals"}
PROPOSED = "Proposed"
OUTCOMES = ["exposure_confirmed", "already_addressed", "no_exposure", "wrong_contact", "meeting_booked", "not_interested"]
SCOPES = ("crm.objects.companies.read/write, crm.objects.contacts.read/write, crm.objects.deals.read/write, "
          "crm.schemas.companies.read/write, crm.schemas.contacts.read/write, crm.schemas.deals.read/write")
TEXT_LIMIT = 65000  # a HubSpot multi-line text property holds 65,536 characters

# name, label, type, field type, options (values, or (value, label) pairs), description
CASE_PROPERTIES = [
    ("distress_opportunity_id", "Distress opportunity ID", "string", "text", None, "The case's ID in distress-signals"),
    ("distress_status", "Distress status", "enumeration", "select", ["active", "withdrawn"],
     "Whether the case is in the active queue"),
    ("distress_priority", "Distress priority", "number", "number", None,
     "Where the company stands among those worth contacting: 1 is first"),
    ("distress_event", "Distress event", "string", "text", None, "The upcoming event the revenue depends on"),
    ("distress_event_date", "Distress event date", "date", "date", None, "The date the event is decided or begins"),
    ("distress_condition", "Distress condition", "string", "textarea", None,
     "What has to happen for the revenue to be affected"),
    ("distress_dependency", "Distress dependency", "string", "textarea", None, "Why the company earns less if that happens"),
    ("distress_timing", "Distress timing", "string", "textarea", None, "When the company would feel the effect"),
    ("distress_stream", "Distress revenue stream", "string", "textarea", None, "The revenue stream that depends on the event"),
    ("distress_period", "Distress period", "string", "text", None, "The span over which the revenue at stake is counted"),
    ("distress_revenue_at_stake", "Distress revenue at stake", "number", "number", None,
     "The largest shortfall within the period, in whole units of the case's currency"),
    ("distress_currency", "Distress currency", "string", "text", None, "The currency of the revenue at stake"),
    ("distress_share", "Distress share of revenue (%)", "number", "number", None,
     "The revenue at stake as a percentage of the case's denominator"),
    ("distress_share_of", "Distress share of", "string", "text", None, "What the share is a percentage of"),
    ("distress_why_now", "Distress why now", "string", "textarea", None, "Why it makes sense to reach out now"),
    ("distress_why_now_date", "Distress why-now date", "date", "date", None, "The date that makes it timely"),
    ("distress_person", "Distress person", "string", "textarea", None, "Who to approach, and what they are responsible for"),
    ("distress_opening", "Distress opening", "string", "textarea", None,
     "A short opening the person could answer from their knowledge of the business"),
    ("distress_next_action", "Distress next action", "string", "textarea", None,
     "What the operator or the researcher should do next"),
    ("distress_memo", "Distress memo", "string", "textarea", None, "The case's memo"),
    ("distress_revision", "Distress revision", "number", "number", None, "The case revision the deal shows"),
    ("distress_change_note", "Distress change note", "string", "textarea", None, "What that revision changed"),
    ("distress_outcome", "Distress outcome", "enumeration", "select", OUTCOMES,
     "What the conversation established; read back onto the case"),
    ("distress_outcome_note", "Distress outcome note", "string", "textarea", None, "The explanation behind the outcome"),
]


def _run_fields(requests, statuses):
    return [
        ("distress_run", "Distress run", "enumeration", "select", requests,
         "Set it to request a research run; the worker clears it when the run starts"),
        ("distress_run_note", "Distress run note", "string", "textarea", None,
         "A question for the run, or the brief to run"),
        ("distress_run_status", "Distress run status", "enumeration", "select", statuses, "Set by the worker"),
        ("distress_run_progress", "Distress run progress", "string", "textarea", None,
         "What the run is doing now; updated each minute while it goes, with the details in the activity timeline"),
        ("distress_run_summary", "Distress run summary", "string", "textarea", None,
         "What the latest run found: the cases it saved and its follow-on leads"),
        ("distress_run_id", "Distress run ID", "string", "text", None, "The latest run's ID; its transcript is kept with the worker"),
        ("distress_run_started", "Distress run started", "datetime", "date", None, "When the latest run started"),
        ("distress_run_finished", "Distress run finished", "datetime", "date", None, "When the latest run finished"),
        ("distress_run_cost", "Distress run cost (USD)", "number", "number", None, "What the latest run cost"),
    ]


STATUSES = [("queued", "Queued"), ("running", "Running"), ("done", "Done"), ("failed", "Failed")]
DEAL_PROPERTIES = CASE_PROPERTIES + _run_fields([("refresh", "Refresh this case")], STATUSES)
COMPANY_PROPERTIES = _run_fields([("research", "Research this company"), ("event", "Research the event in the note"),
                                  ("mandate", "Run the open search"), ("brief", "Run the note as a brief")], [("proposed", "Proposed lead"), *STATUSES]) + [
    ("distress_lead_reason", "Distress lead reason", "string", "textarea", None,
     "Why a run proposed researching this company, with its sources"),
]
CONTACT_PROPERTIES = [
    ("distress_role", "Distress role", "string", "textarea", None, "What this person is responsible for, as the case states it"),
    ("distress_source", "Distress source", "string", "text", None, "The public business source that names this person"),
]
UNIQUE = {"distress_opportunity_id"}


class HubSpotError(RuntimeError):
    pass


def configured():
    return bool(os.environ.get("HUBSPOT_SERVICE_KEY"))


def _api(method, path, body=None, ok404=False):
    key = os.environ.get("HUBSPOT_SERVICE_KEY")
    if not key:
        raise HubSpotError("HUBSPOT_SERVICE_KEY is not set in .env")
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    for i in range(6):
        r = session().request(method, API + path, json=body, headers=headers, timeout=60)
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(float(r.headers.get("Retry-After") or min(30, 2 ** i)))
            continue
        if ok404 and r.status_code == 404:
            return None
        if r.status_code >= 400:
            hint = f" The service key needs: {SCOPES}." if r.status_code == 403 else ""
            raise HubSpotError(f"HubSpot {method} {path}: {r.status_code} {r.text[:400]}{hint}")
        return r.json() if r.content else {}
    raise HubSpotError(f"HubSpot {method} {path}: still throttled or failing after retries")


def _forbidden(e):
    return ": 403 " in str(e)


def _search(obj, filters, properties=(), operator="EQ"):
    body = {"filterGroups": [{"filters": [{"propertyName": p, "operator": operator, "value": v} for p, v in filters]}],
            "properties": list(properties), "limit": 2}
    found = _api("POST", f"/crm/v3/objects/{obj}/search", body)["results"]
    return found[0] if found else None


def _associate(from_obj, from_id, to_obj, to_id):
    _api("PUT", f"/crm/v4/objects/{from_obj}/{from_id}/associations/default/{to_obj}/{to_id}")


def _digest(x):
    return hashlib.sha1(json.dumps(x, sort_keys=True, default=str).encode()).hexdigest()


def _delivered(con, entity_type, entity_id):
    return db.get_delivery(con, DEST, entity_type, entity_id)


def _record(con, entity_type, entity_id, external_id, digest=None):
    with db.write(con):
        db.put_delivery(con, DEST, entity_type, entity_id, external_id, digest)


def _blank(props):
    return {k: ("" if v is None else v) for k, v in props.items()}


_PORTAL = {}
TYPE_IDS = {"contacts": "0-1", "companies": "0-2", "deals": "0-3"}


def record_url(obj, hs_id):
    if not _PORTAL:
        _PORTAL["id"] = _api("GET", "/account-info/v3/details")["portalId"]
    return f"https://app.hubspot.com/contacts/{_PORTAL['id']}/record/{TYPE_IDS[obj]}/{hs_id}"


def add_note(obj, hs_id, body_html):
    """A note on a record's activity timeline."""
    note = _api("POST", "/crm/v3/objects/notes", {"properties": {"hs_timestamp": int(time.time() * 1000),
                                                                  "hs_note_body": body_html}})
    _associate("notes", note["id"], obj, hs_id)
    return note["id"]


def _case_note(con, c, action):
    """The timeline note for a case's deal when it is proposed, revised to a new revision, or withdrawn,
    each once; and what to remember about it. (None, {}) when there is nothing new to say."""
    prog = (db.get_opportunity(con, c["id"]) or {}).get("progress") or {}
    rev, change = c["row"]["revision"], (c["revisions"][-1]["change_note"] if c["revisions"] else "")
    if c["status"] == "withdrawn":
        if prog.get("noted_withdrawn"):
            return None, {}
        fid = prog.get("withdrawn_by")
        f = db.get_finding(con, fid) if fid else None
        return f"<p><b>Case withdrawn.</b> {html.escape((f or {}).get('statement') or change)}</p>", {"noted_withdrawn": True}
    if action != "created" and prog.get("noted_revision") == rev:
        return None, {}
    if change.startswith("From HubSpot"):
        return None, {"noted_revision": rev}
    res, case = c["results"], c["case"]
    stake = "" if res.get("max_shortfall") is None else (
        f" Revenue at stake: {briefs.money(res['max_shortfall'], case['currency'])}"
        + ("" if res.get("max_share") is None else f" ({res['max_share']:.1%} of {(res.get('denominator') or {}).get('label', 'revenue')})")
        + f", {case['period']['start']} to {case['period']['end']}.")
    head = "Case proposed" if action == "created" else f"Case revised to revision {rev}"
    return f"<p><b>{head}.</b>{html.escape(stake)} {html.escape(change)}</p>", {"noted_revision": rev, "noted_withdrawn": False}


# ---------- setup ----------

def _ensure(obj, specs, out):
    groups = {g["name"] for g in _api("GET", f"/crm/v3/properties/{obj}/groups")["results"]}
    if GROUP["name"] not in groups:
        _api("POST", f"/crm/v3/properties/{obj}/groups", GROUP)
        out.append(f"{obj}: created property group '{GROUP['label']}'")
    existing = {p["name"] for p in _api("GET", f"/crm/v3/properties/{obj}")["results"]}
    created = updated = 0
    for i, (name, label, typ, field, options, desc) in enumerate(specs):
        body = {"name": name, "label": label, "type": typ, "fieldType": field, "groupName": GROUP["name"],
                "description": desc, "displayOrder": i}
        if options:
            pairs = [(o, o.replace("_", " ").capitalize()) if isinstance(o, str) else o for o in options]
            body["options"] = [{"label": lab, "value": v, "displayOrder": j} for j, (v, lab) in enumerate(pairs)]
        if name in existing:
            _api("PATCH", f"/crm/v3/properties/{obj}/{name}",
                 {k: body[k] for k in ("label", "description", "groupName", "displayOrder", *(["options"] if options else []))})
            updated += 1
            continue
        try:
            _api("POST", f"/crm/v3/properties/{obj}", body | ({"hasUniqueValue": True} if name in UNIQUE else {}))
        except HubSpotError as e:
            if name not in UNIQUE or "nique" not in str(e):
                raise
            _api("POST", f"/crm/v3/properties/{obj}", body)  # matched by search either way
        created += 1
    out.append(f"{obj}: {created} properties created, {updated} updated")


def _default_pipeline():
    pipes = _api("GET", "/crm/v3/pipelines/deals")["results"]
    return next((x for x in pipes if x["id"] == "default"), None) or min(pipes, key=lambda x: x.get("displayOrder", 0))


def _ensure_proposed_stage(out):
    p = _default_pipeline()
    if any(s["label"] == PROPOSED for s in p["stages"]):
        out.append(f"deals: '{PROPOSED}' stage already in {p['label']}")
        return
    for s in sorted(p["stages"], key=lambda s: s.get("displayOrder", 0), reverse=True):  # display order can't be negative
        _api("PATCH", f"/crm/v3/pipelines/deals/{p['id']}/stages/{s['id']}",
             {"displayOrder": s.get("displayOrder", 0) + 1, "metadata": s.get("metadata") or {}})
    _api("POST", f"/crm/v3/pipelines/deals/{p['id']}/stages",
         {"label": PROPOSED, "displayOrder": 0, "metadata": {"probability": "0.0"}})
    out.append(f"deals: added a '{PROPOSED}' stage at the start of {p['label']}")


def setup():
    """Properties on deals, companies and contacts, and the Proposed stage. Safe to run again."""
    out = []
    _ensure("deals", DEAL_PROPERTIES, out)
    for obj, specs in (("companies", COMPANY_PROPERTIES), ("contacts", CONTACT_PROPERTIES)):
        try:
            _ensure(obj, specs, out)
        except HubSpotError as e:
            if not _forbidden(e):
                raise
            out.append(f"{obj}: not set up; the service key can't change {obj} ({str(e)[:200]})")
    _ensure_proposed_stage(out)
    print("\n".join(out))


# ---------- delivering cases ----------

_STAGE = None


def _first_stage():
    """(pipeline ID, stage ID, label) where new deals start: the default pipeline's Proposed stage, or its
    first stage."""
    global _STAGE
    if _STAGE is None:
        p = _default_pipeline()
        stages = sorted(p["stages"], key=lambda s: s.get("displayOrder", 0))
        s = next((x for x in stages if x["label"] == PROPOSED), stages[0])
        _STAGE = (p["id"], s["id"], f"{p['label']}: {s['label']}")
    return _STAGE


ACRONYMS = {"us", "usa", "uk", "eu", "va", "dod", "hhs", "cms", "fda", "sba", "nasa", "faa", "fcc", "ftc", "doe", "epa",
            "irs", "dhs", "usda", "snap", "nfip", "aca", "ira", "pjm", "ercot", "mde", "thc", "cbd", "ai", "ev", "fy",
            "mlb", "nfl", "nba", "nhl", "lng", "ip", "saas", "b2b", "rd"}
SMALL = {"and", "or", "of", "for", "the", "to", "in", "on", "from", "by", "at", "a", "an", "with"}


def _stream_label(c):
    """The case's revenue stream as a short title, from its stream_key."""
    words = [w for w in (c["case"].get("stream_key") or "").split("-") if w]
    if not words:
        return "Revenue at stake"
    return " ".join(w.upper() if w in ACRONYMS else w if (i and w in SMALL) else w.capitalize() for i, w in enumerate(words))


def deal_properties(c):
    """The deal's case properties for a case in its current form (briefs.cases). The stage, the outcome
    and the run fields are not among them."""
    case, res, co, ev = c["case"], c["results"], c["company"], c["event"]
    reasons, buyer = case["case"], case["buyer"]
    person = ", ".join(x for x in (buyer.get("name"), buyer.get("title")) if x)
    return _blank({
        # company, then the revenue stream (a company can have several cases on one event), then the event
        "dealname": f"{co['name']}: {_stream_label(c)} · {ev['title']}",
        "distress_opportunity_id": c["id"],
        "distress_status": c["status"],
        "distress_priority": reasons["priority"],
        "distress_event": ev["title"],
        "distress_event_date": ev.get("date"),
        "distress_condition": case["condition"],
        "distress_dependency": case["dependency"],
        "distress_timing": case["timing"],
        "distress_stream": case["stream"],
        "distress_period": f"{case['period']['start']} to {case['period']['end']}",
        "distress_revenue_at_stake": None if res.get("max_shortfall") is None else round(res["max_shortfall"]),
        "distress_currency": case["currency"],
        "distress_share": None if res.get("max_share") is None else round(100 * res["max_share"], 1),
        "distress_share_of": (res.get("denominator") or {}).get("label"),
        "distress_why_now": reasons["why_now"],
        "distress_why_now_date": reasons.get("why_now_date"),
        "distress_person": f"{person}: {buyer['role']}" if person else buyer["role"],
        "distress_opening": case["outreach"]["draft"],
        "distress_next_action": case["next_action"],
        "distress_memo": briefs.memo(c)[:TEXT_LIMIT],
        "distress_revision": c["row"]["revision"],
        "distress_change_note": c["revisions"][-1]["change_note"] if c["revisions"] else None,
    })


def contact_properties(con, c):
    """The person as a contact, or None when the case names only a role. No email: the operator adds it."""
    b = c["case"]["buyer"]
    if not b.get("name"):
        return None
    parts = b["name"].split()
    first, last = (" ".join(parts[:-1]), parts[-1]) if len(parts) > 1 else ("", parts[0])
    oid = b.get("observation_id")
    source = ((c["cited"].get(oid) or db.get_observation(con, oid) or {}) if oid else {}).get("url")
    return _blank({"firstname": first, "lastname": last, "jobtitle": b.get("title"), "company": c["company"]["name"],
                   "distress_role": b["role"], "distress_source": source})


def _hs_company(con, company_id, name, domain, data=None, create=True):
    """The HubSpot company for a saved company: known, found by domain or name, or created."""
    d = _delivered(con, "company", company_id)
    if d:
        return d["external_id"], "known"
    found = (_search("companies", [("domain", domain)]) if domain else None) or _search("companies", [("name", name)])
    if found:
        hs, action = found["id"], "linked"
    elif create:
        props = {k: v for k, v in (("name", name), ("domain", domain), ("description", (data or {}).get("description"))) if v}
        hs, action = _api("POST", "/crm/v3/objects/companies", {"properties": props})["id"], "created"
    else:
        return None, "not in HubSpot"
    _record(con, "company", company_id, hs)
    return hs, action


def _contact_key(c):
    return f"{c['row']['company_id']}:{' '.join(c['case']['buyer']['name'].lower().split())}"


def _contact(con, c, props, hs_company):
    if props is None:
        return None, "no named person"
    try:
        return _write_contact(con, c, props, hs_company)
    except HubSpotError as e:
        if not _forbidden(e):
            raise
        return None, "not written (the service key can't write contacts)"


def _write_contact(con, c, props, hs_company):
    key = _contact_key(c)
    digest = _digest(props)
    d = _delivered(con, "contact", key)
    if d:
        if d["hash"] != digest:  # names and company stay as the operator may have corrected them
            _api("PATCH", f"/crm/v3/objects/contacts/{d['external_id']}",
                 {"properties": {k: props[k] for k in ("jobtitle", "distress_role", "distress_source")}})
            _record(con, "contact", key, d["external_id"], digest)
            return d["external_id"], "updated"
        return d["external_id"], "known"
    try:  # the same person at the same company, if the operator already has them
        found = _search("contacts", [("firstname", props["firstname"]), ("lastname", props["lastname"]),
                                     ("associatedcompanyid", hs_company)]) if props["firstname"] else None
    except HubSpotError:
        found = None
    hs, action = (found["id"], "linked") if found else (_api("POST", "/crm/v3/objects/contacts", {"properties": props})["id"], "created")
    _associate("contacts", hs, "companies", hs_company)
    _record(con, "contact", key, hs, digest)
    return hs, action


def _deal(con, c, props):
    d = _delivered(con, "deal", c["id"])
    hs = d["external_id"] if d else (_search("deals", [("distress_opportunity_id", c["id"])]) or {}).get("id")
    if hs:
        if _api("PATCH", f"/crm/v3/objects/deals/{hs}", {"properties": props}, ok404=True) is None:
            return None, "deleted in HubSpot, so not recreated"
        return hs, "updated"
    pipeline, stage, label = _first_stage()
    hs = _api("POST", "/crm/v3/objects/deals", {"properties": props | {"pipeline": pipeline, "dealstage": stage}})["id"]
    with db.write(con):  # the starting stage, so a later pull reports only what moved
        db.set_progress(con, c["id"], (c["row"]["progress"] or {}) | {"deal_id": hs, "stage": label})
    return hs, "created"


def _deliver(con, c, dry_run=False, planned=None):
    """Deliver one case. Returns (what happened, a line describing it or None)."""
    name = c["company"]["name"]
    prior = _delivered(con, "deal", c["id"])
    if c["status"] == "withdrawn" and not prior:
        return "skipped", f"{c['id']} {name}: withdrawn and never delivered; left out"
    deal, person = deal_properties(c), contact_properties(con, c)
    # the memo's last line carries the case's update time, which progress also moves; leave it out
    digest = _digest({"deal": deal | {"distress_memo": deal["distress_memo"].split(f"\n*{c['id']}, revision")[0]},
                      "contact": person})
    if prior and prior["hash"] == digest:
        if person and not dry_run and not _delivered(con, "contact", _contact_key(c)):
            # the deal is current but its person isn't in HubSpot yet (e.g. contacts became writable)
            hs_company = _delivered(con, "company", c["row"]["company_id"])["external_id"]
            hs_contact, ct_action = _contact(con, c, person, hs_company)
            if hs_contact:
                _associate("deals", prior["external_id"], "contacts", hs_contact)
                return "written", f"{c['id']} {name}: deal unchanged; contact {hs_contact} {ct_action}"
        return "unchanged", None
    if dry_run:
        planned.append({"opportunity_id": c["id"], "deal": deal, "contact": person,
                        "company": {k: c["company"].get(k) for k in ("name", "domain", "description")}})
        return "planned", f"{c['id']} {name}: would {'update' if prior else 'create'} the deal" + ("" if person else " (no named person)")
    hs_company, co_action = _hs_company(con, c["row"]["company_id"], c["company"]["name"], c["company"].get("domain"), c["company"])
    hs_contact, ct_action = _contact(con, c, person, hs_company)
    hs_deal, deal_action = _deal(con, c, deal)
    if hs_deal:
        _associate("deals", hs_deal, "companies", hs_company)
        if hs_contact:
            _associate("deals", hs_deal, "contacts", hs_contact)
        _record(con, "deal", c["id"], hs_deal, digest)
        note, mark = _case_note(con, c, deal_action)
        if note:
            add_note("deals", hs_deal, note)
        if mark:
            with db.write(con):
                db.set_progress(con, c["id"], ((db.get_opportunity(con, c["id"]) or {}).get("progress") or {}) | mark)
    return "written", (f"{c['id']} {name}: deal {hs_deal} {deal_action}; company {hs_company} {co_action}; "
                       f"contact {hs_contact or '-'} {ct_action}")


def sync_case(opportunity_id):
    """Deliver one case now; the save and withdraw tools call it. Reports to stderr only."""
    con = db.connect()
    try:
        for c in briefs.cases(con, [opportunity_id]):
            _, line = _deliver(con, c)
            if line:
                print(line, file=sys.stderr)
    finally:
        con.close()


def push(opportunity_ids=None, dry_run=False, quiet=False):
    """Deliver cases: the ones named, or every active case plus any delivered case that changed (a
    withdrawn case updates its deal). A withdrawn case that was never delivered is left out."""
    con = db.connect()
    cases = {c["id"]: c for c in briefs.cases(con, opportunity_ids or None)}
    missing = [i for i in opportunity_ids or [] if i not in cases]
    if missing:
        raise SystemExit(f"no saved case {', '.join(missing)}")
    chosen = [cases[i] for i in opportunity_ids] if opportunity_ids else \
        [c for c in cases.values() if c["status"] == "active" or _delivered(con, "deal", c["id"])]
    planned, counts = [], {}
    for c in chosen:
        what, line = _deliver(con, c, dry_run, planned)
        counts[what] = counts.get(what, 0) + 1
        if line and not (quiet and what == "skipped"):
            print(line, flush=True)
    if dry_run:
        OUT.mkdir(exist_ok=True)
        (OUT / "hubspot_payloads.jsonl").write_text("".join(json.dumps(p, default=str) + "\n" for p in planned))
        print(f"dry run: {len(planned)} deal(s) would be written; payloads in {OUT / 'hubspot_payloads.jsonl'}")
    elif not quiet or counts.get("written"):
        print(", ".join(f"{n} {k}" for k, n in counts.items()) or "nothing to deliver", flush=True)
    return counts


# ---------- what HubSpot holds, for the researcher ----------

_LABELS = {}


def _stage_labels():
    if not _LABELS:
        _LABELS.update({s["id"]: f"{p['label']}: {s['label']}" for p in _api("GET", "/crm/v3/pipelines/deals")["results"]
                        for s in p["stages"]})
    return _LABELS


def company_view(con, company_row):
    """Whether a saved company is in HubSpot, and its deals there (the operator's own included) with their
    stages and outcomes."""
    hs, _ = _hs_company(con, company_row["id"], company_row["name"], company_row.get("domain"), create=False)
    if not hs:
        return {"in_hubspot": False}
    links = _api("GET", f"/crm/v4/objects/companies/{hs}/associations/deals", ok404=True) or {}
    ids = sorted({str(x["toObjectId"]) for x in links.get("results", [])})[:50]
    deals = []
    if ids:
        labels = _stage_labels()
        props = ["dealname", "dealstage", "distress_opportunity_id", "distress_outcome", "distress_outcome_note", "createdate",
                 "hs_lastmodifieddate"]
        for x in _api("POST", "/crm/v3/objects/deals/batch/read", {"properties": props, "inputs": [{"id": i} for i in ids]})["results"]:
            p = x["properties"]
            deals.append({k: v for k, v in (("deal", p.get("dealname")), ("stage", labels.get(p.get("dealstage"), p.get("dealstage"))),
                                             ("case", p.get("distress_opportunity_id")), ("outcome", p.get("distress_outcome")),
                                             ("outcome_note", p.get("distress_outcome_note")),
                                             ("created", (p.get("createdate") or "")[:10]),
                                             ("updated", (p.get("hs_lastmodifieddate") or "")[:10])) if v})
    return {"in_hubspot": True, "deals": deals}


# ---------- run requests ----------

def requests():
    """Records whose Distress run field asks for a run, oldest change first: [(object, ID, properties)]."""
    out = []
    for obj, props in (("companies", ["name", "domain", "distress_run", "distress_run_note", "distress_run_status"]),
                       ("deals", ["dealname", "distress_run", "distress_run_note", "distress_run_status", "distress_opportunity_id"])):
        body = {"filterGroups": [{"filters": [{"propertyName": "distress_run", "operator": "HAS_PROPERTY"}]}],
                "properties": [*props, "hs_lastmodifieddate"], "limit": 50,
                "sorts": [{"propertyName": "hs_lastmodifieddate", "direction": "ASCENDING"}]}
        out += [(obj, x["id"], x["properties"]) for x in _api("POST", f"/crm/v3/objects/{obj}/search", body)["results"]]
    return sorted(out, key=lambda r: r[2].get("hs_lastmodifieddate") or "")


def is_requested(obj, hs_id):
    """Whether the record still asks for a run, read directly: search can lag a change by a few seconds."""
    r = _api("GET", f"/crm/v3/objects/{obj}/{hs_id}?properties=distress_run", ok404=True)
    return bool(r and (r.get("properties") or {}).get("distress_run"))


def _set(obj, hs_id, props):
    _api("PATCH", f"/crm/v3/objects/{obj}/{hs_id}", {"properties": _blank(props)})


def mark_queued(obj, hs_id):
    _set(obj, hs_id, {"distress_run_status": "queued"})


def mark_running(obj, hs_id):
    _set(obj, hs_id, {"distress_run": None, "distress_run_status": "running", "distress_run_started": int(time.time() * 1000),
                      "distress_run_finished": None, "distress_run_summary": None, "distress_run_id": None,
                      "distress_run_cost": None, "distress_run_progress": "Starting"})


def set_run_id(obj, hs_id, run_id):
    _set(obj, hs_id, {"distress_run_id": run_id})


def set_run_progress(obj, hs_id, text):
    _set(obj, hs_id, {"distress_run_progress": text[:TEXT_LIMIT]})


def mark_finished(obj, hs_id, status, summary, cost=None):
    _set(obj, hs_id, {"distress_run_status": status, "distress_run_finished": int(time.time() * 1000),
                      "distress_run_summary": summary[:TEXT_LIMIT], "distress_run_cost": None if cost is None else round(cost, 2)})


def recover_running():
    """Records a previous worker left running: their runs stopped with it."""
    n = 0
    for obj in ("companies", "deals"):
        body = {"filterGroups": [{"filters": [{"propertyName": "distress_run_status", "operator": "IN", "values": ["running", "queued"]}]}],
                "properties": ["distress_run_status"], "limit": 100}
        for x in _api("POST", f"/crm/v3/objects/{obj}/search", body)["results"]:
            if x["properties"].get("distress_run_status") == "running":
                mark_finished(obj, x["id"], "failed", "The worker stopped while this run was going. Request it again.")
                n += 1
    return n


def propose_leads(leads, run_id):
    """Each follow-on lead that names a company puts that company in HubSpot as a proposed lead, with the
    reason and sources. A company with a run requested or going is left alone."""
    n = 0
    for lead in leads or []:
        co = lead.get("company") or {}
        if not co.get("name"):
            continue
        props = ["distress_run", "distress_run_status"]
        found = (_search("companies", [("domain", co["domain"])], props) if co.get("domain") else None) \
            or _search("companies", [("name", co["name"])], props)
        reason = "\n\n".join(x for x in (lead.get("reason"), "Sources:\n" + "\n".join(lead["source_urls"]) if lead.get("source_urls") else "",
                                         f"Proposed by run {run_id}, {now_iso()[:10]}.") if x)
        fields = {"distress_run_status": "proposed", "distress_lead_reason": reason[:TEXT_LIMIT]}
        if found:
            p = found["properties"]
            if p.get("distress_run") or p.get("distress_run_status") in ("queued", "running"):
                continue
            _set("companies", found["id"], fields)
        else:
            _api("POST", "/crm/v3/objects/companies",
                 {"properties": {k: v for k, v in (("name", co["name"]), ("domain", co.get("domain"))) if v} | fields})
        n += 1
    return n


# ---------- outcomes ----------

def apply_outcome(con, opportunity_id, progress):
    """Record a deal's stage, outcome and note on its case: the case's progress, and a revision whose
    change note says what HubSpot shows. Returns the note, or None when nothing worth a revision changed."""
    opp = db.get_opportunity(con, opportunity_id)
    if opp is None:
        return None
    old = opp["progress"] or {}
    if all(old.get(k) == v for k, v in progress.items()):
        return None
    moved = old.get("stage") is not None and old.get("stage") != progress.get("stage")
    if not moved and (old.get("outcome"), old.get("outcome_note")) == (progress.get("outcome"), progress.get("outcome_note")):
        with db.write(con):  # only the deal ID or a first reading of the stage: no revision
            db.set_progress(con, opportunity_id, old | progress)
        return None
    parts = [f"stage {progress['stage']}"] if progress.get("stage") else []
    if progress.get("outcome"):
        parts.append(f"outcome {progress['outcome']}" + (f": {progress['outcome_note']}" if progress.get("outcome_note") else ""))
    note = "From HubSpot: " + ("; ".join(parts) or "outcome cleared")
    with db.write(con):
        last = db.latest_revision(con, opportunity_id)
        revision = opp["revision"] + 1
        db.add_revision(con, opportunity_id, revision, last["hash"], note, last["snapshot"])
        con.execute("UPDATE opportunities SET revision=? WHERE id=?", (revision, opportunity_id))
        db.set_progress(con, opportunity_id, old | progress | {"pulled_at": now_iso()})
    return note


def pull_outcomes(quiet=False):
    """Read every delivered deal's stage, outcome and note back onto its case."""
    con = db.connect()
    rows = db.deliveries(con, DEST, "deal")
    if not rows:
        if not quiet:
            print("no deals delivered yet")
        return 0
    _LABELS.clear()
    labels, found = _stage_labels(), {}
    for i in range(0, len(rows), 100):
        batch = {"properties": ["dealstage", "distress_outcome", "distress_outcome_note"],
                 "inputs": [{"id": r["external_id"]} for r in rows[i:i + 100]]}
        found |= {x["id"]: x["properties"] for x in _api("POST", "/crm/v3/objects/deals/batch/read", batch)["results"]}
    changed = 0
    for r in rows:
        p = found.get(r["external_id"])
        if p is None:
            if not quiet:
                print(f"{r['entity_id']}: deal {r['external_id']} is no longer in HubSpot")
            continue
        note = apply_outcome(con, r["entity_id"], {
            "deal_id": r["external_id"], "stage": labels.get(p.get("dealstage"), p.get("dealstage")),
            "outcome": p.get("distress_outcome") or None, "outcome_note": p.get("distress_outcome_note") or None})
        if note:
            changed += 1
            print(f"{r['entity_id']}: {note}", flush=True)
    if not quiet:
        print(f"{changed} case(s) updated from HubSpot")
    return changed
