"""Views of saved cases, each read as it stands now (forms.py): the memo, which HubSpot carries on the
case's deal, and the export (the case whole: company, propositions, inputs, given values, worlds,
evidence, findings, revisions), which `export CASE_ID` prints."""

from . import db, forms, worlds
from .models import FORMULAS

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
    out, saved = [], db.propositions(con)
    rows = db.cases(con) if ids is None else [r for r in (db.get_case(con, i) for i in ids) if r]
    for row in rows:
        current = forms.read(row, db.latest_revision(con, row["id"])["snapshot"], saved)
        props = current["propositions"]
        cited = {o["id"]: o for o in db.cited(con, row["id"])}
        fs = db.findings(con, "case_id=? OR company_id=?", (row["id"], row["company_id"]))
        out.append({"id": row["id"], "row": row, "status": row["status"], "company": current["company"],
                    "events": [props[k] for k in row["events"]], "props": props, "case": current["case"],
                    "results": current["results"], "condition": current["results"]["condition"],
                    "cited": cited, "findings": fs, "revisions": db.revisions(con, row["id"])})
    return out


def event_title(c):
    return " and ".join(e["title"] for e in c["events"])


def event_date(c):
    return min(str(e["window_end"]) for e in c["events"])


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


def _read_lines(c):
    """The propositions the case reads, each way under what it divides."""
    props, read, out = c["props"], c["results"]["reads"], []

    def line(key, depth):
        p = props[key]
        side = " (if not)" if p.get("side") == "no" else ""
        out.append(f"{'  ' * depth}- **{p['title']}**{side}: {p['text']}")
        for w in sorted((w for w in props.values() if w.get("parent") == key and w["key"] in read),
                        key=lambda w: (w["side"] != "yes", w["order"])):
            line(w["key"], depth + 1)

    for k in read:
        if not props[k].get("parent") or props[k]["parent"] not in read:
            line(k, 0)
    return out


def given_condition(c, g):
    """What a given value is given, in the propositions' titles."""
    return " and ".join([*(c["props"][k]["title"] for k in g["given"]),
                         *(f"not: {c['props'][k]['title']}" for k in g["given_not"])])


def _given_lines(c):
    case, cur, out = c["case"], c["case"]["currency"], []
    for g in case["given_values"]:
        for name, v in g["values"].items():
            out.append(f"- Given {given_condition(c, g)}: `{name}` = {_shown(v['value'], case['inputs'][name]['unit'], cur)}. "
                       f"{_rests_on(v['assumption'], c['cited'])}")
    return out


def memo(c):
    co, case, res, props = c["company"], c["case"], c["results"], c["props"]
    cur, buyer, reasons = case["currency"], case["buyer"], case["reasons"]
    worst = max(res["worlds"], key=lambda r: r["shortfall"])
    lost = f", of which {money(worst['lost'], cur)} is not recovered later" if worst.get("lost") is not None else ""
    share = f" ({pct(worst['share'])} of {res['denominator']['label']})" if worst.get("share") is not None else ""
    out = [f"# {co['name']}: {case['stream']}", "",
           f"{'**Withdrawn.** ' if c['status'] == 'withdrawn' else ''}**{event_title(c)}.** {reasons['why_now']}", "",
           f"Revenue at stake: {money(worst['shortfall'], cur)}{share} between {case['period']['start']} and "
           f"{case['period']['end']}{lost}, in the world \"{worst['label']}\". "
           f"Priority {reasons['priority']}: {reasons['priority_reason']}", "",
           "## What has to happen", "", c["condition"], ""]
    for ev in c["events"]:
        out += [" ".join(x for x in (ev.get("description"), ev.get("timing"), f"Decided on or by {ev['window_end']}.",
                                     f"Shown by: {ev['resolution_source']}" if ev.get("resolution_source") else None) if x), ""]
    out += ["**The propositions this case reads.**", ""] + _read_lines(c) + [""]
    out += [f"- {_passage(e, c['cited'])}" for k in res["reads"] for e in props[k].get("evidence") or []]
    out += ["", "## Why the company earns less", "", co.get("description") or "", "", case["dependency"], "",
            "## When it would feel the effect", "", case["timing"], "",
            "## How much of the business is exposed", "",
            f"Period {case['period']['start']} to {case['period']['end']}, {cur}.", "",
            "| World | Revenue in period | Shortfall | Share | Deferred past period | Not recovered | Contribution loss | Cash delayed |",
            "|---|---|---|---|---|---|---|---|",
            f"| baseline | {money(res['baseline_revenue'], cur)} | – | – | – | – | – | – |"]
    for r in res["worlds"]:
        out.append(f"| {r['label']} | {money(r['revenue'], cur)} | {money(r['shortfall'], cur)} | "
                   f"{pct(r['share'])} | {money(r['deferred_past_period'], cur)} | {money(r['lost'], cur)} | "
                   f"{money(r['contribution_loss'], cur)} | {money(r['cash_delayed'], cur)} |")
    out += ["", "**Formulas.**", ""] + [f"- {k.replace('_', ' ')} = `{case[k]}`" for k in FORMULAS if case.get(k)]
    if case.get("denominator"):
        out.append(f"- denominator ({case['denominator']['label']}) = `{case['denominator']['formula']}`")
    out += ["", "**Inputs, at their values in the baseline.**", "", "| Input | Value | Basis | |", "|---|---|---|---|"]
    out += [_input_line(k, v, cur, c["cited"]) for k, v in case["inputs"].items()]
    out += ["", "**Given values.**", ""] + _given_lines(c)
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
    return {"case_id": c["id"], "status": c["status"], "revision": c["row"]["revision"], "updated": c["row"]["updated"],
            "company": c["company"],
            "propositions": [worlds.nest(c["props"], k) for k in dict.fromkeys(worlds.root(c["props"], k) for k in c["results"]["reads"])],
            "case": c["case"], "results": c["results"],
            "sources": [{"observation_id": k, "kind": o["kind"], "url": o.get("url"), "title": (o.get("data") or {}).get("title"),
                         "retrieved_at": o.get("retrieved_at")} for k, o in c["cited"].items()],
            "findings": [{k: f[k] for k in ("id", "kind", "statement", "evidence", "results", "created")} for f in c["findings"]],
            "revisions": c["revisions"]}
