"""Views of saved cases, each read as it stands now (forms.py): the memo, which HubSpot carries on the
case's deal, and the export (the case whole: company, event, inputs, values by outcome, results,
evidence, findings, revisions), which `export OPP_ID` prints."""

from . import db, forms, outcomes

BASIS = {"source": "reported", "result": "read from a result", "assumption": "assumed"}


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
    """Saved cases as they stand now; all of them, or those with the given IDs."""
    out = []
    rows = db.opportunities(con) if ids is None else [r for r in (db.get_opportunity(con, i) for i in ids) if r]
    for row in rows:
        event = db.get_event(con, row["event_id"])["data"]
        current = forms.snapshot(db.latest_revision(con, row["id"])["snapshot"], event)
        cited = {o["id"]: o for o in db.cited(con, row["id"])}
        fs = db.findings(con, "opportunity_id=? OR company_id=?", (row["id"], row["company_id"]))
        out.append({"id": row["id"], "row": row, "status": row["status"], "company": current["company"],
                    "event": event, "case": current["case"], "results": current["results"],
                    "condition": outcomes.condition(event, current["case"], current["results"]),
                    "cited": cited, "findings": fs, "revisions": db.revisions(con, row["id"])})
    return out


def _passage(cite, cited):
    o = cited.get(cite["observation_id"]) or {}
    where = (o.get("data") or {}).get("title") or o.get("url") or cite["observation_id"]
    return f"\"{cite['passage'].strip()}\" ({where}{'' if cite.get('checked', True) else '; unchecked'})"


def _rests_on(a, cited):
    """An assumption in a line: its reasoning, then the passages and the results it rests on."""
    parts = [a["note"].strip()] + [_passage(c, cited) for c in a.get("evidence") or []]
    if a.get("results"):
        parts.append("results " + ", ".join(a["results"]))
    return " ".join(parts)


def _shown(v, unit, cur):
    return money(v, cur) if unit == cur else f"{v:,.4g} {unit}"


def _input_line(name, inp, cur, cited):
    kind = next(BASIS[k] for k in BASIS if inp.get(k))
    if inp.get("source"):
        basis = _passage(inp["source"], cited)
    elif inp.get("result"):
        cell = inp["result"]
        row = ", ".join(f"{k} {v}" for k, v in cell["row"].items())
        basis = f"{cell['result_id']}, column {cell['column']}" + (f", row {row}" if row else "")
    else:
        basis = _rests_on(inp["assumption"], cited)
    return (f"| `{name}` | {_shown(inp['value'], inp['unit'], cur)} | {kind} | {basis.replace('|', '/')}"
            f"{' ' + inp['note'] if inp.get('note') else ''} |")


def _outcome_lines(c):
    """The event's outcomes, each way under the outcome it lies within, with the values the case gives it."""
    ev, case, cur, out = c["event"], c["case"], c["case"]["currency"], []
    for key, node, _, depth in outcomes.tree(ev):
        title, text = {"yes": ("Yes", ev["text"]), "no": ("No", "The claim turns out false.")}.get(key, (node["title"], node["text"]))
        out.append(f"{'  ' * depth}- **{title}**: {text}")
        for name, g in (case["by_outcome"].get(key) or {}).items():
            out.append(f"{'  ' * (depth + 1)}- `{name}` = {_shown(g['value'], case['inputs'][name]['unit'], cur)}: "
                       f"{_rests_on(g['assumption'], c['cited'])}")
    return out


def memo(c):
    co, ev, case, res = c["company"], c["event"], c["case"], c["results"]
    cur, buyer, reasons = case["currency"], case["buyer"], case["case"]
    worst = next(r for r in res["outcomes"] if r["outcome"] == res["worst"])
    lost = f", of which {money(worst['lost'], cur)} is not recovered later" if worst.get("lost") is not None else ""
    share = f" ({pct(worst['share'])} of {res['denominator']['label']})" if worst.get("share") is not None else ""
    out = [f"# {co['name']}: {case['stream']}", "",
           f"{'**Withdrawn.** ' if c['status'] == 'withdrawn' else ''}**{ev['title']}.** {reasons['why_now']}", "",
           f"Revenue at stake: {money(worst['shortfall'], cur)}{share} between {case['period']['start']} and "
           f"{case['period']['end']}{lost}, in the outcome \"{outcomes.label(ev, worst['outcome'])}\". "
           f"Priority {reasons['priority']}: {reasons['priority_reason']}", "",
           "## What has to happen", "", c["condition"], "",
           f"{ev['description']}" + (f" {ev['timing']}" if ev.get("timing") else "") +
           f" Decided on or by {ev['window_end']}. Shown by: {ev['resolution_source']}", "",
           "**The event's outcomes, and the values this case gives them.**", ""] + _outcome_lines(c) + [""]
    out += [f"- {_passage(e, c['cited'])}" for e in ev.get("evidence") or []]
    out += ["", "## Why the company earns less", "", co.get("description") or "", "", case["dependency"], "",
            "## When it would feel the effect", "", case["timing"], "",
            "## How much of the business is exposed", "",
            f"Period {case['period']['start']} to {case['period']['end']}, {cur}.", "",
            "| Outcome | Revenue in period | Shortfall | Share | Deferred past period | Not recovered | Contribution loss | Cash delayed |",
            "|---|---|---|---|---|---|---|---|",
            f"| baseline | {money(res['baseline_revenue'], cur)} | – | – | – | – | – | – |"]
    for r in res["outcomes"]:
        out.append(f"| {outcomes.label(ev, r['outcome'])} | {money(r['revenue'], cur)} | {money(r['shortfall'], cur)} | "
                   f"{pct(r['share'])} | {money(r['deferred_past_period'], cur)} | {money(r['lost'], cur)} | "
                   f"{money(r['contribution_loss'], cur)} | {money(r['cash_delayed'], cur)} |")
    out += ["", "**Formulas.**", ""] + [f"- {k.replace('_', ' ')} = `{case[k]}`" for k in forms.FORMULAS if case.get(k)]
    if case.get("denominator"):
        out.append(f"- denominator ({case['denominator']['label']}) = `{case['denominator']['formula']}`")
    out += ["", "**Inputs, at their values in the baseline.**", "", "| Input | Value | Basis | |", "|---|---|---|---|"]
    out += [_input_line(k, v, cur, c["cited"]) for k, v in case["inputs"].items()]
    out += ["", "## Why reach out now", "", reasons["why_now"], "", reasons["why_matters"], "",
            "## Who to approach", "",
            f"{buyer.get('name') or 'Not yet named'}{', ' + buyer['title'] if buyer.get('title') else ''}: {buyer['role']}.", "",
            _quote(case["outreach"]["draft"]), "", f"**Next action:** {case['next_action']}", ""]
    if c["findings"]:
        out += ["## Findings", ""] + [f"- *{f['kind']}*: {f['statement']}" for f in c["findings"]] + [""]
    out += ["## Sources", ""]
    out += [f"- {e['supports']}: {_passage(e, c['cited'])}" for e in case["evidence"]]
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
