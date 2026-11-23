"""Views of saved cases, each read as it stands now (forms.py): the memo, which HubSpot carries on the
case's deal, and the export (the case whole: company, propositions, inputs, given values, worlds,
evidence, findings, revisions), which `export CASE_ID` prints."""

from . import db, forms, worlds
from .models import FORMULAS

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


def figure(v, unit, cur):
    """(a figure, the rest of its unit): as money where the unit is the currency or begins with it ('USD per
    year'), and otherwise in plain digits, to four significant digits below a thousand."""
    if unit == cur or unit.startswith(cur + " "):
        return money(v, cur), unit[len(cur):].strip()
    return (f"{v:,.0f}" if abs(v) >= 1000 else f"{v:,.4g}"), unit


def _shown(v, unit, cur):
    return " ".join(x for x in figure(v, unit, cur) if x)


def basis(fig, cited):
    """(what kind of basis a figure has, the basis in a line): where the figure is read, then the assumption by
    which it holds."""
    read, kind = "", "assumed"
    if fig.get("source"):
        read, kind = _passage(fig["source"], cited), "reported"
    elif fig.get("result"):
        cell = fig["result"]
        row = ", ".join(f"{k} {v}" for k, v in cell["row"].items())
        read, kind = f"{cell['result_id']}, column {cell['column']}" + (f", row {row}" if row else ""), "read from a result"
    if fig.get("assumption") and read:
        kind += ", carried by an assumption"
    return kind, " ".join(x for x in (read, _rests_on(fig["assumption"], cited) if fig.get("assumption") else "") if x)


def _input_line(name, inp, cur, cited):
    kind, text = basis(inp, cited)
    return (f"| `{name}` | {_shown(inp['value'], inp['unit'], cur)} | {kind} | {text.replace('|', '/')}"
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
            kind, text = basis(v, c["cited"])
            out.append(f"- Given {given_condition(c, g)}: `{name}` = {_shown(v['value'], case['inputs'][name]['unit'], cur)} "
                       f"({kind}). {text}")
    return out


def memo(c):
    co, case, res, props = c["company"], c["case"], c["results"], c["props"]
    cur, person = case["currency"], case["person"]
    worst = max(res["worlds"], key=lambda r: r["shortfall"])
    lost = f", of which {money(worst['lost'], cur)} is not recovered later" if worst.get("lost") is not None else ""
    share = f" ({pct(worst['share'])} of {res['denominator']['label']})" if worst.get("share") is not None else ""
    out = [f"# {co['name']}: {case['stream']}", "",
           f"{'**Withdrawn.** ' if c['status'] == 'withdrawn' else ''}**{event_title(c)}.** Decided on or by {event_date(c)}.", "",
           f"Revenue at stake: {money(worst['shortfall'], cur)}{share} between {case['period']['start']} and "
           f"{case['period']['end']}{lost}, in the world \"{worst['label']}\".", "",
           "## What has to happen", "", c["condition"], ""]
    for ev in c["events"]:
        out += [" ".join(x for x in (ev.get("description"), ev.get("timing"), f"Decided on or by {ev['window_end']}.",
                                     f"Shown by: {ev['resolution_source']}" if ev.get("resolution_source") else None) if x), ""]
    out += ["**The propositions this case reads.**", ""] + _read_lines(c) + [""]
    out += [f"- {_passage(e, c['cited'])}" for k in res["reads"] for e in props[k].get("evidence") or []]
    out += ["", "## How the event reaches the revenue", "", co.get("description") or "", "", case["dependency"], "",
            "## When the effect reaches the revenue", "", case["timing"], "",
            "## The revenue in each world", "",
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
    out += ["", "**Inputs, at their values as things stand.**", "", "| Input | Value | Basis | |", "|---|---|---|---|"]
    out += [_input_line(k, v, cur, c["cited"]) for k, v in case["inputs"].items()]
    out += ["", "**Given values.**", ""] + _given_lines(c)
    out += ["", "## The person responsible", "",
            f"{person.get('name') or 'Not named'}{', ' + person['title'] if person.get('title') else ''}: {person['role']}.", ""]
    if c["findings"]:
        out += ["## Findings", ""] + [f"- *{f['kind']}*: {f['statement']}" for f in c["findings"]] + [""]
    out += ["## Sources", ""]
    out += [f"- {e['supports']}: {_passage(e, c['cited'])}" for e in case["evidence"]]
    out += ["", f"*{c['id']}, revision {c['row']['revision']}, {c['row']['updated']}. Latest change: "
            f"{c['revisions'][-1]['change_note']}*", ""]
    return "\n".join(out)


def export(c):
    return {"case_id": c["id"], "status": c["status"], "revision": c["row"]["revision"], "updated": c["row"]["updated"],
            "company": c["company"],
            "propositions": [worlds.nest(c["props"], k) for k in dict.fromkeys(worlds.root(c["props"], k) for k in c["results"]["reads"])],
            "case": c["case"], "results": c["results"],
            "sources": [{"observation_id": k, "kind": o["kind"], "url": o.get("url"), "title": (o.get("data") or {}).get("title"),
                         "retrieved_at": o.get("retrieved_at")} for k, o in c["cited"].items()],
            "findings": [{k: f[k] for k in ("id", "kind", "statement", "evidence", "results", "created")} for f in c["findings"]],
            "revisions": c["revisions"]}
