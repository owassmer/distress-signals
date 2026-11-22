"""Views of saved cases, each read in its current form (forms.py): the memo, which HubSpot carries on
the case's deal, and the export (the case whole: company, event, inputs, versions, results, evidence,
findings, revisions), which `export OPP_ID` prints."""

from . import db, forms, outcomes

PROVENANCE = {"source": "reported", "derived": "derived", "assumption": "assumed"}


def money(x, cur="USD"):
    if x is None:
        return "–"
    sign = "-" if x < 0 else ""
    x = abs(x)
    unit = (1e9, "B") if x >= 1e9 else (1e6, "M") if x >= 1e6 else (1e3, "K") if x >= 1e3 else (1, "")
    prefix = "$" if cur == "USD" else cur + " "
    return f"{sign}{prefix}{x / unit[0]:,.{1 if unit[1] else 0}f}{unit[1]}"


def pct(x):
    return "–" if x is None else f"{x:.1%}"


def cases(con, ids=None):
    """Saved cases in their current form; all of them, or those with the given IDs."""
    out = []
    rows = db.opportunities(con) if ids is None else [r for r in (db.get_opportunity(con, i) for i in ids) if r]
    for row in rows:
        current = forms.snapshot(db.latest_revision(con, row["id"])["snapshot"])
        ev_row = db.get_event(con, row["event_id"])
        cited = {o["id"]: o for o in db.cited(con, row["id"])}
        fs = db.findings(con, "opportunity_id=? OR company_id=?", (row["id"], row["company_id"]))
        event = forms.event(ev_row["data"])
        out.append({"id": row["id"], "row": row, "status": row["status"], "company": current["company"],
                    "event": event, "case": current["case"], "results": current["results"],
                    "condition": outcomes.condition(event, current["case"]),
                    "baseline_outcomes": outcomes.baseline(event, current["case"]) if outcomes.stated(event) else [],
                    "cited": cited, "findings": fs,
                    "revisions": db.revisions(con, row["id"])})
    return out


def _basis(cite, cited):
    o = cited.get(cite["observation_id"]) or {}
    where = (o.get("data") or {}).get("title") or o.get("url") or cite["observation_id"]
    return f"\"{cite['passage'].strip()}\" ({where}{'' if cite.get('checked', True) else '; unchecked'})"


def _input_line(name, inp, cur, cited):
    v = inp["value"]
    shown = money(v, cur) if inp["unit"] == cur else f"{v:,.4g} {inp['unit']}"
    kind = next(PROVENANCE[k] for k in PROVENANCE if inp.get(k))
    if inp.get("source"):
        basis = _basis(inp["source"], cited)
    elif inp.get("derived"):
        basis = f"workspace result {inp['derived']['result_id']}" + (f": {inp['derived']['note']}" if inp["derived"].get("note") else "")
    else:
        basis = inp["assumption"]
    return f"| `{name}` | {shown} | {kind} | {basis.replace('|', '/')}{' ' + inp['note'] if inp.get('note') else ''} |"


def _outcome_lines(c):
    """The event's outcomes, each way under the outcome it lies within, marked with what the case says of it."""
    ev, by = c["event"], {}
    for a in c["case"]["affected"]:
        by.setdefault(a.get("outcome"), []).append(a["name"].replace("_", " "))
    out = []
    for key, node, _, depth in outcomes.tree(ev):
        mark = f" (this case: {', '.join(by[key])})" if key in by else " (baseline)" if key in c["baseline_outcomes"] else ""
        title, text = {"yes": ("Yes", ev["text"]), "no": ("No", "The claim turns out false.")}.get(
            key, (node["title"], node["text"]))
        out.append(f"{'  ' * depth}- **{title}**{mark}: {text}")
    return out


def memo(c):
    co, ev, case, res = c["company"], c["event"], c["case"], c["results"]
    cur, buyer, reasons = case["currency"], case["buyer"], case["case"]
    stated = outcomes.stated(ev)
    under = lambda key: outcomes.label(ev, key) if stated and key in outcomes.index(ev) else "–"  # noqa: E731
    worst = next(r for r in res["affected"] if r["name"] == res["worst"])
    if_worst = f"under \"{under(worst['outcome'])}\" ({worst['name'].replace('_', ' ')})" if stated else \
        f"if {worst['name'].replace('_', ' ')}"
    lost = f", of which {money(worst['lost'], cur)} is not recovered later" if worst.get("lost") is not None else ""
    share = f" ({pct(worst['share'])} of {res['denominator']['label']})" if worst.get("share") is not None else ""
    out = [f"# {co['name']}: {case['stream']}", "",
           f"{'**Withdrawn.** ' if c['status'] == 'withdrawn' else ''}**{ev['title']}.** {reasons['why_now']}", "",
           f"Revenue at stake: {money(worst['shortfall'], cur)}{share} between {case['period']['start']} and "
           f"{case['period']['end']}{lost}, {if_worst}. Priority {reasons['priority']}: "
           f"{reasons['priority_reason']}", "",
           "## What has to happen", "", c["condition"], "",
           f"{ev.get('description') or ''}" + (f" {ev['timing']}" if ev.get("timing") else "") +
           (f" Decided on or by {ev['window_end']}." if ev.get("window_end") else "") +
           (f" Shown by: {ev['resolution_source']}" if ev.get("resolution_source") else ""), ""]
    if stated:
        out += ["**The event's outcomes.**", ""] + _outcome_lines(c) + [""]
    out += [f"- {_basis(e, c['cited'])}" for e in ev.get("evidence") or []]
    out += ["", "## Why the company earns less", "", co.get("description") or "", "", case["dependency"], "",
            "## When it would feel the effect", "", case["timing"], ""]
    out += [f"- {a['name'].replace('_', ' ')}: {a['timeline']}" for a in case["affected"]]
    out += ["", "## How much of the business is exposed", "",
            f"Baseline: {case['baseline']['description']}. Period {case['period']['start']} to {case['period']['end']}, {cur}.", "",
            "| Version | Under | Revenue in period | Shortfall | Share | Deferred past period | Not recovered | Contribution loss | Cash delayed |",
            "|---|---|---|---|---|---|---|---|---|",
            f"| baseline | {'; '.join(under(k) for k in c['baseline_outcomes']) or '–'} | "
            f"{money(res['baseline_revenue'], cur)} | – | – | – | – | – | – |"]
    for r in res["affected"]:
        out.append(f"| {r['name'].replace('_', ' ')} | {under(r['outcome'])} | {money(r['revenue'], cur)} | {money(r['shortfall'], cur)} | "
                   f"{pct(r['share'])} | {money(r['deferred_past_period'], cur)} | {money(r['lost'], cur)} | "
                   f"{money(r['contribution_loss'], cur)} | {money(r['cash_delayed'], cur)} |")
    out += ["", "**Formulas.**", "", f"- baseline: revenue = `{case['baseline']['revenue']}`" +
            (f"; contribution = `{case['baseline']['contribution']}`" if case["baseline"].get("contribution") else "")]
    out += [f"- {a['name'].replace('_', ' ')}: revenue = `{a['revenue']}`" + "".join(f"; {k.replace('_', ' ')} = `{a[k]}`" for k in
            ("deferred_past_period", "contribution", "cash_delayed") if a.get(k)) for a in case["affected"]]
    if case.get("denominator"):
        out.append(f"- denominator ({case['denominator']['label']}) = `{case['denominator']['formula']}`")
    out += ["", "**Inputs.**", "", "| Input | Value | Provenance | Basis |", "|---|---|---|---|"]
    out += [_input_line(k, v, cur, c["cited"]) for k, v in case["inputs"].items()]
    out += ["", "## Why reach out now", "", reasons["why_now"], "", reasons["why_matters"], "",
            "## Who to approach", "",
            f"{buyer.get('name') or 'Not yet named'}{', ' + buyer['title'] if buyer.get('title') else ''}: {buyer['role']}.", "",
            _quote(case["outreach"]["draft"]), "", f"**Next action:** {case['next_action']}", ""]
    if c["findings"]:
        out += ["## Findings", ""] + [f"- *{f['kind']}*: {f['statement']}" for f in c["findings"]] + [""]
    out += ["## Sources", ""]
    out += [f"- {e['supports']}: {_basis(e, c['cited'])}" for e in case["evidence"]]
    out += ["", f"*{c['id']}, revision {c['row']['revision']}, {c['row']['updated']}. Latest change: "
            f"{c['revisions'][-1]['change_note']}*", ""]
    return "\n".join(out)


def _quote(text):
    return "\n".join("> " + line if line.strip() else ">" for line in text.strip().splitlines())


def export(c):
    return {"opportunity_id": c["id"], "status": c["status"], "revision": c["row"]["revision"], "updated": c["row"]["updated"],
            "company": c["company"], "event": c["event"], "case": c["case"], "results": c["results"],
            "sources": [{"observation_id": k, "kind": o["kind"], "url": o.get("url"), "title": (o.get("data") or {}).get("title"),
                         "retrieved_at": o.get("retrieved_at")} for k, o in c["cited"].items()],
            "findings": [{k: f[k] for k in ("id", "kind", "statement", "evidence", "results", "created")} for f in c["findings"]],
            "revisions": c["revisions"]}
