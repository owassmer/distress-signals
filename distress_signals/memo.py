"""The memo of a saved case as a typeset document, written to be handed to the company's own finance officer:
the reader who holds the books and can check every figure.

Nothing in it is written anew. Every sentence is the analyst's as the case holds it or the fixed wording here,
and every figure is the store's, computed from the case as it stands now. `document` lays the case out in the
order a reader takes it: what is at stake in each outcome, the event, how it reaches the revenue, and how the
estimate is built, each figure with what it rests on. A passage the case quotes becomes a numbered note with its
record's title, date and address; a saved query a figure is read from becomes a numbered computation with its
SQL, so the figure can be worked out again. `html` renders the document as one self-contained page, and `pdf`
prints that page through headless Chromium."""

import datetime as dt
import html as _html
import re

from . import briefs, forms
from .models import FORMULAS

NAMES = {"revenue": "Revenue", "contribution": "Contribution", "deferred_past_period": "Revenue deferred past the period",
         "cash_delayed": "Cash delayed"}


def _date(s):
    d = dt.date.fromisoformat(str(s)[:10])
    return f"{d:%B} {d.day}, {d.year}"


def _span(start, end):
    a, b = dt.date.fromisoformat(str(start)[:10]), dt.date.fromisoformat(str(end)[:10])
    return f"{a:%B} {a.day} – {b:%B} {b.day}, {b.year}" if a.year == b.year else f"{_date(a)} – {_date(b)}"


class _Numbered:
    """Things cited, numbered in the order the document first cites them."""

    def __init__(self):
        self.items, self.index = [], {}

    def ref(self, key, make):
        if key not in self.index:
            self.items.append(make() | {"n": len(self.items) + 1})
            self.index[key] = len(self.items)
        return self.index[key]


def document(c):
    """A case (briefs.cases) as the memo's parts, in reading order. Text is the case's own; figures are shown as
    the store's other views show them (briefs.money, briefs.figure)."""
    co, case, res, props, cited = c["company"], c["case"], c["results"], c["props"], c["cited"]
    cur, notes, comps = case["currency"], _Numbered(), _Numbered()

    def note(cite):
        """The mark for a passage quoted from a record, or for a record cited whole: (the record's number, the
        passage's place among those quoted from it). A record is one note, with its passages under it."""
        oid, passage = cite["observation_id"], (cite.get("passage") or "").strip()
        o = cited.get(oid) or {}
        n = notes.ref(oid, lambda: {
            "title": (o.get("data") or {}).get("title") or o.get("url") or oid, "url": o.get("url"),
            "published": _date(o["published"]) if o.get("published") else None,
            "read": _date(o["retrieved_at"]) if o.get("retrieved_at") else None, "passages": []})
        quoted = notes.items[n - 1]["passages"]
        if not passage:
            return (n, None)
        if passage not in [q["text"] for q in quoted]:
            quoted.append({"text": passage, "checked": cite.get("checked", True)})
        return (n, [q["text"] for q in quoted].index(passage))

    def comp(rid):
        r = c["computed"].get(rid) or {}
        return comps.ref(rid, lambda: {"id": rid, "name": (r.get("name") or rid).replace("_", " "), "note": r.get("note"),
                                       "sql": r.get("sql"), "columns": r.get("columns") or [], "rows": (r.get("rows") or [])[:12],
                                       "row_count": r.get("row_count")})

    def shown(v, name):
        """(the figure, its unit in words): the unit is left out where the input's label already says it."""
        inp = case["inputs"][name]
        figure, unit = briefs.figure(v, inp["unit"], cur)
        return figure, "" if unit.strip().lower() == forms.label(name, inp, cur).lower() else unit

    def basis(fig):
        """What a figure rests on: the kind of basis, where it is read, the reasoning, and what the reasoning cites."""
        a, read, refs, cs = fig.get("assumption"), None, [], []
        if fig.get("source"):
            kind, refs = "Stated in a record", [note(fig["source"])]
        elif fig.get("result"):
            cell = fig["result"]
            n = comp(cell["result_id"])
            row = ", ".join(f"{k} {v}" for k, v in cell["row"].items())
            kind, cs = "Computed from records", [n]
            read = f"Read from computation C{n}: column {cell['column']}" + (f", row {row}" if row else "")
        else:
            kind = "Assumed"
        if a:
            kind += ", carried over by an assumption" if kind != "Assumed" else ""
            refs += [note(e) for e in a.get("evidence") or []]
            cs += [comp(r) for r in a.get("results") or []]
        return {"kind": kind, "read": read, "reasoning": " ".join(x for x in ((a or {}).get("note"), fig.get("note")) if x),
                "notes": list(dict.fromkeys(refs)), "computations": list(dict.fromkeys(cs))}

    person = case["person"]          # cited first: the heading names them
    person_note = note({"observation_id": person["observation_id"]}) if person.get("observation_id") else None

    # ---- what is at stake
    worst = max(res["worlds"], key=lambda w: w["shortfall"])
    extra = [(k, title) for k, title in (("deferred_past_period", "Deferred past the period"), ("lost", "Not recovered"),
                                        ("contribution_loss", "Contribution lost"), ("cash_delayed", "Cash delayed"))
             if any(w.get(k) is not None for w in res["worlds"])]
    outcomes = [{"outcome": w["label"].split("; "), "revenue": briefs.money(w["revenue"], cur),
                 "shortfall": briefs.money(w["shortfall"], cur), "share": briefs.pct(w["share"]),
                 "extra": [briefs.money(w.get(k), cur) for k, _ in extra], "worst": w is worst} for w in res["worlds"]]

    # ---- the event, and the other propositions the case reads
    def proposition(p):
        about = " ".join(x for x in (p.get("description"), p.get("timing")) if x)
        return {"title": p["title"], "text": p["text"], "about": about or None,
                "window": _span(p["window_start"], p["window_end"]) if p.get("window_start") else f"On or by {_date(p['window_end'])}",
                "shown_by": p.get("resolution_source"), "notes": [note(e) for e in p.get("evidence") or []]}

    event_keys = [e["key"] for e in c["events"]]
    events = [proposition(e) for e in c["events"]]
    others = [proposition(props[k]) | {"within": props[props[k]["parent"]]["title"] if props[k].get("parent") else None}
              for k in res["reads"] if k not in event_keys]

    # ---- how it reaches the revenue
    establishes = [{"claim": e["supports"], "note": note(e)} for e in case["evidence"]]

    # ---- how the estimate is built: each input once, with the value it takes in each outcome that changes it
    by_input = {name: [] for name in case["inputs"]}
    for g in case["given_values"]:
        for name, v in g["values"].items():
            figure, unit = shown(v["value"], name)
            by_input[name].append({"when": briefs.given_condition(c, g), "value": figure, "unit": unit, **basis(v)})
    figures = []
    for name, inp in case["inputs"].items():
        figure, unit = shown(inp["value"], name)
        figures.append({"name": name, "label": forms.label(name, inp, cur), "value": figure, "unit": unit, **basis(inp),
                        "outcomes": by_input[name]})
    formulas = [{"name": NAMES[k], "formula": case[k]} for k in FORMULAS if case.get(k)]
    if case.get("denominator"):
        formulas.append({"name": case["denominator"]["label"], "formula": case["denominator"]["formula"]})

    def moved(r):
        place, name = r["figure"].rsplit(".", 1)
        inp = case["inputs"][name]
        when = None
        if place.startswith("given_values"):
            when = briefs.given_condition(c, case["given_values"][int(place[place.index("[") + 1:place.index("]")])])
        return {"label": forms.label(name, inp, cur), "when": when, "value": shown(r["value"], name)[0],
                "basis": r["basis"], "moved_by": shown(r["moved_by"], name)[0],
                "shortfall_moves_by": briefs.money(r["shortfall_moves_by"], cur), "outcome": (r["in_world"] or "").split("; ")}

    findings = [{"kind": f["kind"], "statement": f["statement"],
                 "notes": [note(e) for e in f.get("evidence") or []], "computations": [comp(r) for r in f.get("results") or []]}
                for f in c["findings"]]
    return {
        "company": co["name"], "event": briefs.event_title(c), "withdrawn": c["status"] == "withdrawn",
        "as_of": _date(c["row"]["updated"]), "period": _span(case["period"]["start"], case["period"]["end"]),
        "currency": cur, "case_id": c["id"], "revision": c["row"]["revision"],
        "person": {"name": person.get("name"), "title": person.get("title"), "role": person["role"],
                   "note": person_note},
        "stream": case["stream"],
        "at_stake": {"shortfall": briefs.money(worst["shortfall"], cur), "share": briefs.pct(worst["share"]) if worst.get("share") is not None else None,
                     "of": (res.get("denominator") or {}).get("label"), "outcome": worst["label"].split("; "),
                     "baseline": briefs.money(res["baseline_revenue"], cur)},
        "outcomes": outcomes, "extra_columns": [title for _, title in extra],
        "events": events, "others": others,
        "business": co.get("description"), "dependency": case["dependency"], "timing": case["timing"], "establishes": establishes,
        "formulas": formulas,
        "changing": [f for f in figures if f["outcomes"]], "constant": [f for f in figures if not f["outcomes"]],
        "figures": figures,
        "sensitivity": [moved(r) for r in res.get("sensitivity") or [] if r["shortfall_moves_by"]],
        "condition": c["condition"], "findings": findings,
        "notes": notes.items, "computations": comps.items,
        "change_note": (c["revisions"][-1].get("change_note") or "").strip() if c["revisions"] else ""}


# ---------- the page ----------

CSS = """
@page { size: Letter; margin: 0.9in 0.95in 0.95in 0.95in; }
* { box-sizing: border-box; }
html { font-size: 10.4pt; }
body { margin: 0; color: #1b1b1b; font-family: Charter, "Bitstream Charter", "Iowan Old Style", "Palatino Linotype", Georgia, serif;
       line-height: 1.42; -webkit-font-smoothing: antialiased; }
main { max-width: 6.6in; margin: 0 auto; }
@media screen { body { background: #f3f2ef; } main { background: #fff; margin: 28px auto; padding: 0.9in 0.95in; max-width: 8.5in;
                box-shadow: 0 1px 6px rgba(0,0,0,.12); } }
.sans, table, .kicker, .meta, h2, .key, .tag, .where, figcaption, .notes, .small { font-family: -apple-system, "Helvetica Neue", Helvetica, Arial, sans-serif; }
.kicker { font-size: 7.6pt; letter-spacing: .14em; text-transform: uppercase; color: #1f3a5f; font-weight: 600; margin: 0 0 6pt; }
h1 { font-size: 19pt; line-height: 1.15; margin: 0 0 3pt; font-weight: 600; letter-spacing: -.005em; }
.event { font-size: 12.5pt; margin: 0 0 9pt; color: #333; }
.meta { font-size: 8pt; color: #555; border-top: 1px solid #1b1b1b; border-bottom: 1px solid #cfcfcf; padding: 5pt 0; margin: 0 0 14pt;
        display: flex; flex-wrap: wrap; gap: 2pt 16pt; }
.meta b { color: #1b1b1b; font-weight: 600; }
.withdrawn { font-family: -apple-system, Helvetica, Arial, sans-serif; font-size: 8.5pt; border: 1px solid #8a1c1c; color: #8a1c1c; padding: 4pt 7pt; margin: 0 0 10pt; }
h2 { font-size: 8.6pt; letter-spacing: .1em; text-transform: uppercase; color: #1f3a5f; font-weight: 600; margin: 17pt 0 6pt;
     padding-bottom: 3pt; border-bottom: 1px solid #cfcfcf; break-after: avoid; }
h3 { font-size: 10.6pt; margin: 10pt 0 2pt; font-weight: 600; break-after: avoid; }
p { margin: 0 0 6.5pt; orphans: 3; widows: 3; }
.key { display: flex; gap: 22pt; margin: 2pt 0 9pt; break-inside: avoid; }
.key div { min-width: 1.1in; }
.key .n { font-size: 20pt; font-weight: 600; line-height: 1.1; color: #1f3a5f; letter-spacing: -.01em; }
.key .l { font-size: 7.6pt; color: #555; text-transform: uppercase; letter-spacing: .07em; margin-top: 2pt; }
.key .t { font-size: 8.8pt; line-height: 1.35; color: #1b1b1b; }
table { width: 100%; border-collapse: collapse; font-size: 8.6pt; line-height: 1.32; margin: 4pt 0 8pt; font-variant-numeric: tabular-nums; }
th { text-align: left; font-weight: 600; color: #555; font-size: 7.4pt; text-transform: uppercase; letter-spacing: .06em;
     border-bottom: 1px solid #1b1b1b; padding: 3pt 6pt 3pt 0; vertical-align: bottom; }
td { border-bottom: 1px solid #e2e2e2; padding: 4pt 6pt 4pt 0; vertical-align: top; }
td.num, th.num { text-align: right; padding-right: 0; padding-left: 8pt; white-space: nowrap; }
table.where { table-layout: fixed; } table.where td, table.where th { overflow-wrap: anywhere; }
table.where td.num, table.where th.num { white-space: normal; }
table.where td:last-child, table.where th:last-child { padding-left: 12pt; }
.unit { color: #555; font-weight: 400; }
tr { break-inside: avoid; }
tr.worst td { font-weight: 600; }
tr.asis td { color: #555; }
.not { color: #555; font-style: italic; }
.small { font-size: 7.8pt; color: #555; margin: 0 0 7pt; }
.formula { font-family: "SF Mono", Menlo, Consolas, monospace; font-size: 8.2pt; background: #f6f6f4; border-left: 2px solid #1f3a5f;
           padding: 5pt 8pt; margin: 3pt 0 6pt; white-space: pre-wrap; word-break: break-word; break-inside: avoid; }
.formula b { font-family: -apple-system, Helvetica, Arial, sans-serif; font-weight: 600; }
code { font-family: "SF Mono", Menlo, Consolas, monospace; font-size: 8pt; color: #333; }
.fig { margin: 0 0 9pt; break-inside: avoid-page; }
.fig .head { display: flex; justify-content: space-between; gap: 10pt; align-items: baseline; border-bottom: 1px solid #e2e2e2; padding-bottom: 1.5pt; margin-bottom: 3pt; }
.fig .head .name { font-weight: 600; }
.val { margin: 0 0 4pt 0; padding-left: 9pt; border-left: 2px solid #e2e2e2; break-inside: avoid; }
.val .line { font-family: -apple-system, Helvetica, Arial, sans-serif; font-size: 8.6pt; display: flex; justify-content: space-between;
             gap: 10pt; align-items: baseline; }
.val .small { margin: 1pt 0 0; }
.val .line b { font-weight: 600; }
.val p { margin: 1pt 0 0; font-size: 9.6pt; }
.tag { font-size: 7.2pt; text-transform: uppercase; letter-spacing: .06em; color: #555; white-space: nowrap; }
.tag.assumed { color: #8a4b00; }
sup a, a.ref { color: #1f3a5f; text-decoration: none; font-family: -apple-system, Helvetica, Arial, sans-serif; font-size: 7pt; }
ul.claims { margin: 2pt 0 7pt; padding-left: 13pt; } ul.claims li { margin-bottom: 2.5pt; }
dl { margin: 2pt 0 7pt; display: grid; grid-template-columns: 0.95in 1fr; gap: 1.5pt 8pt; font-size: 9.4pt; }
dt { font-family: -apple-system, Helvetica, Arial, sans-serif; font-size: 7.6pt; text-transform: uppercase; letter-spacing: .06em; color: #555; padding-top: 1.5pt; }
dd { margin: 0; }
.appendix { break-before: page; }
.notes { font-size: 8pt; line-height: 1.36; }
.notes ol { margin: 0; padding-left: 16pt; } .notes li { margin-bottom: 4.5pt; break-inside: avoid; }
.notes .q { font-family: Charter, "Iowan Old Style", Georgia, serif; font-size: 8.6pt; color: #1b1b1b; display: block; margin-top: 1pt; }
.notes a { color: #1f3a5f; word-break: break-all; text-decoration: none; }
.comp { break-inside: avoid; margin-bottom: 9pt; }
.comp pre { font-family: "SF Mono", Menlo, Consolas, monospace; font-size: 6.9pt; line-height: 1.3; background: #f6f6f4; padding: 5pt 7pt; margin: 3pt 0;
            white-space: pre-wrap; word-break: break-word; }
.comp table { font-size: 7.4pt; }
.end { font-family: -apple-system, Helvetica, Arial, sans-serif; font-size: 7.4pt; color: #555; margin-top: 12pt; border-top: 1px solid #cfcfcf; padding-top: 4pt; }
"""


def _e(s):
    return _html.escape(str(s if s is not None else ""), quote=False)


def _outcome(parts):
    """An outcome as the store labels it, one proposition to a line, with those that turn out false set apart."""
    return "<br>".join(f'<span class="not">{_e(p)}</span>' if p.startswith("Not: ") else _e(p) for p in parts)


SHORT = {"Stated in a record": "Stated", "Computed from records": "Computed", "Assumed": "Assumed",
         "Stated in a record, carried over by an assumption": "Stated, carried over",
         "Computed from records, carried over by an assumption": "Computed, carried over"}


def _tag(kind, short=False):
    return f'<span class="tag{" assumed" if kind == "Assumed" else ""}">{_e(SHORT[kind] if short else kind)}</span>'


def html(doc):
    """The document as one self-contained page: no script, no outside resource, print rules for Letter."""
    d, o = doc, []
    add = o.append

    def refs(notes=(), computations=()):
        """The marks after a sentence: a record's number, with the passage's letter where the record is quoted more
        than once, and a computation's C-number."""
        marks = []
        for n, k in notes:
            letter = chr(97 + k) if k is not None and len(d["notes"][n - 1]["passages"]) > 1 else ""
            marks.append(f'<a href="#note-{n}">{n}{letter}</a>')
        marks += [f'<a href="#comp-{n}">C{n}</a>' for n in computations]
        return f"<sup>{', '.join(marks)}</sup>" if marks else ""

    def amount(v):
        return f'<b>{_e(v["value"])}</b>' + (f' <span class="unit">{_e(v["unit"])}</span>' if v.get("unit") else "")

    def value(label, v):
        read = f'<p class="small">{_e(v["read"])}.</p>' if v.get("read") else ""
        marks = refs(v["notes"], v["computations"])
        why = f'<p>{_e(v["reasoning"])}{marks}</p>' if v["reasoning"] else (f'<p class="small">Rests on {marks}</p>' if marks else "")
        return (f'<div class="val"><div class="line"><span>{_e(label)}: {amount(v)}</span>{_tag(v["kind"])}</div>{read}{why}</div>')

    def figure(f):
        rows = [value("As things stand", f)] + [value("If " + x["when"], x) for x in f["outcomes"]]
        return (f'<div class="fig"><div class="head"><span class="name">{_e(f["label"])}</span><code>{_e(f["name"])}</code></div>'
                f'{"".join(rows)}</div>')

    title = f"{d['company']}: {d['event']}"
    add(f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{_e(title)}</title>'
        f'<meta name="viewport" content="width=device-width,initial-scale=1"><style>{CSS}</style></head><body><main>')
    add('<p class="kicker">Revenue exposure memo</p>')
    add(f'<h1>{_e(d["company"])}</h1><p class="event">{_e(d["event"])}</p>')
    if d["withdrawn"]:
        add('<p class="withdrawn">This case is withdrawn. It reads as its last revision held it.</p>')
    who = ""
    if d["person"]["name"]:
        who = (f'<span><b>Responsible at the company</b> {_e(d["person"]["name"])}'
               f'{", " + _e(d["person"]["title"]) if d["person"]["title"] else ""}'
               f'{refs([d["person"]["note"]] if d["person"]["note"] else [])}</span>')
    add(f'<div class="meta"><span><b>As of</b> {_e(d["as_of"])}</span><span><b>Period</b> {_e(d["period"])}</span>'
        f'<span><b>Currency</b> {_e(d["currency"])}</span>{who}</div>')

    # what is at stake
    a = d["at_stake"]
    add('<h2>Revenue at stake</h2>')
    add(f'<p>{_e(d["stream"])}</p>')
    add('<div class="key">'
        f'<div><div class="n">{_e(a["shortfall"])}</div><div class="l">Largest shortfall in the period</div></div>'
        + (f'<div><div class="n">{_e(a["share"])}</div><div class="l">Share of revenue</div></div>' if a["share"] else "")
        + f'<div><div class="t">{_outcome(a["outcome"])}</div><div class="l">The outcome it falls in</div></div></div>')
    cols = "".join(f'<th class="num">{_e(t)}</th>' for t in d["extra_columns"])
    add(f'<table><thead><tr><th>Outcome</th><th class="num">Revenue in the period</th><th class="num">Shortfall</th>'
        f'<th class="num">Share</th>{cols}</tr></thead><tbody>')
    dash = "".join('<td class="num">–</td>' for _ in d["extra_columns"])
    add(f'<tr class="asis"><td>As things stand</td><td class="num">{_e(a["baseline"])}</td><td class="num">–</td>'
        f'<td class="num">–</td>{dash}</tr>')
    for w in d["outcomes"]:
        more = "".join(f'<td class="num">{_e(x)}</td>' for x in w["extra"])
        add(f'<tr{" class=worst" if w["worst"] else ""}><td>{_outcome(w["outcome"])}</td><td class="num">{_e(w["revenue"])}</td>'
            f'<td class="num">{_e(w["shortfall"])}</td><td class="num">{_e(w["share"])}</td>{more}</tr>')
    add('</tbody></table>')
    add('<p class="small">Shortfall is revenue as things stand less revenue in the outcome, over the period.'
        + (f' Share is of {_e(a["of"])}.' if a["of"] else "") + '</p>')

    # the event
    add(f'<h2>{"The events" if len(d["events"]) > 1 else "The event"}</h2>')

    def prop(p, head=True):
        if head:
            add(f'<h3>{_e(p["title"])}</h3>')
        add(f'<p>{_e(p["text"])}</p>')
        if p["about"]:
            add(f'<p>{_e(p["about"])}{refs(p["notes"])}</p>')
        rows = f'<dt>Decided</dt><dd>{_e(p["window"])}</dd>'
        if p["shown_by"]:
            rows += f'<dt>Shown by</dt><dd>{_e(p["shown_by"])}</dd>'
        if not p["about"] and p["notes"]:
            rows += f'<dt>Rests on</dt><dd>{refs(p["notes"])}</dd>'
        add(f'<dl>{rows}</dl>')

    for p in d["events"]:
        prop(p, head=len(d["events"]) > 1 or bool(d["others"]))
    for p in d["others"]:
        add(f'<h3>{_e(p["title"])}</h3>')
        if p["within"]:
            add(f'<p class="small">One of the ways of: {_e(p["within"])}</p>')
        prop(p, head=False)

    # how it reaches the revenue
    add('<h2>How the event reaches the revenue</h2>')
    if d["business"]:
        add(f'<p>{_e(d["business"])}</p>')
    add(f'<p>{_e(d["dependency"])}</p>')
    add(f'<h3>When</h3><p>{_e(d["timing"])}</p>')
    if d["establishes"]:
        add('<h3>What establishes this</h3><ul class="claims">'
            + "".join(f'<li>{_e(x["claim"])}{refs([x["note"]])}</li>' for x in d["establishes"]) + '</ul>')

    # how the estimate is built
    add('<h2>How the estimate is built</h2>')
    for f in d["formulas"]:
        add(f'<div class="formula"><b>{_e(f["name"])}</b> = {_e(f["formula"])}</div>')
    add('<table class="where"><thead><tr><th style="width:17%">In the formula</th><th>What it is</th>'
        '<th class="num" style="width:17%">As things stand</th><th style="width:22%">Basis</th></tr></thead><tbody>'
        + "".join(f'<tr><td><code>{_e(f["name"])}</code></td><td>{_e(f["label"])}</td><td class="num"><b>{_e(f["value"])}</b></td>'
                  f'<td>{_tag(f["kind"], short=True)}</td></tr>' for f in d["figures"]) + '</tbody></table>')
    if d["changing"]:
        add('<h3>What changes with the outcome</h3>')
        o.extend(figure(f) for f in d["changing"])
    if d["constant"]:
        add('<h3>What holds in every outcome</h3>')
        o.extend(figure(f) for f in d["constant"])

    # appendix
    add('<section class="appendix">')
    if d["notes"]:
        add('<h2>Notes: the records cited</h2><div class="notes"><ol>')
        for n in d["notes"]:
            when = "; ".join(x for x in (n["published"] and f"published {n['published']}", n["read"] and f"read {n['read']}") if x)
            link = f' <a href="{_html.escape(n["url"], quote=True)}">{_e(n["url"])}</a>' if n["url"] else ""
            many = len(n["passages"]) > 1
            quoted = "".join(
                f'<span class="q">{f"<b>{chr(97 + k)}.</b> " if many else ""}“{_e(q["text"])}”'
                f'{"" if q["checked"] else " (not checked against the record)"}</span>' for k, q in enumerate(n["passages"]))
            add(f'<li id="note-{n["n"]}"><b>{_e(n["title"])}</b>{", " + _e(when) if when else ""}.{link}{quoted}</li>')
        add('</ol></div>')
    if d["computations"]:
        add('<h2>Computations: the queries figures are read from</h2>')
        for k in d["computations"]:
            add(f'<div class="comp" id="comp-{k["n"]}"><h3>C{k["n"]}. {_e(k["name"])}</h3>')
            if k["note"]:
                add(f'<p>{_e(k["note"])}</p>')
            if k["sql"]:
                add(f'<pre>{_e(k["sql"])}</pre>')
            if k["columns"] and k["rows"]:
                head = "".join(f'<th>{_e(col)}</th>' for col in k["columns"])
                body = "".join("<tr>" + "".join(f'<td>{_e("" if x is None else x)}</td>' for x in row) + "</tr>" for row in k["rows"])
                add(f'<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>')
                if k["row_count"] and k["row_count"] > len(k["rows"]):
                    add(f'<p class="small">The first {len(k["rows"])} of {k["row_count"]} rows.</p>')
            add('</div>')
    if d["sensitivity"]:
        add('<h2>Sensitivity</h2><p class="small">Each stated figure moved, one at a time, by a tenth of the largest value its input '
            'takes in the case, with the largest change in any outcome’s shortfall and the outcome it falls in.</p>')
        add('<table class="where"><thead><tr><th>Figure</th><th class="num" style="width:11%">Value</th><th class="num" style="width:11%">Moved by</th>'
            '<th class="num" style="width:15%">Shortfall moves by</th><th style="width:33%">In the outcome</th></tr></thead><tbody>')
        for r in d["sensitivity"]:
            when = f'<br><span class="not">if {_e(r["when"])}</span>' if r["when"] else ""
            add(f'<tr><td>{_e(r["label"])}{when}<br><span class="tag">{_e(r["basis"])}</span></td><td class="num">{_e(r["value"])}</td>'
                f'<td class="num">{_e(r["moved_by"])}</td><td class="num">{_e(r["shortfall_moves_by"])}</td><td>{_outcome(r["outcome"])}</td></tr>')
        add('</tbody></table>')
    if d["findings"]:
        add('<h2>Findings recorded with the case</h2>')
        for f in d["findings"]:
            add(f'<p><span class="tag">{_e(f["kind"])}</span> {_e(f["statement"])}{refs(f["notes"], f["computations"])}</p>')
    add('<h2>The condition, as the store states it</h2>'
        f'<p class="small">The outcomes in which revenue differs from revenue as things stand, written from the propositions’ texts.</p><p>{_e(d["condition"])}</p>')
    add(f'<p class="end">{_e(d["case_id"])}, revision {_e(d["revision"])}, as of {_e(d["as_of"])}.'
        f'{" Latest change: " + _e(d["change_note"]) if d["change_note"] else ""}</p>')
    add('</section></main></body></html>')
    return "".join(o)


def filename(c):
    """A file name for a case's memo: the company and the events' keys."""
    slug = re.sub(r"[^a-z0-9]+", "-", c["company"]["name"].lower()).strip("-")
    return f"{slug}--{'+'.join(c['row']['events'])}"


def pdf(page, path, running=""):
    """Print the page to a Letter PDF through headless Chromium, with the running line and page numbers at
    the foot."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RuntimeError("the PDF is printed in headless Chromium: install requirements.txt, then run "
                           "python -m playwright install chromium --only-shell") from None
    foot = ('<div style="width:100%;font-family:-apple-system,Helvetica,Arial,sans-serif;font-size:6.5pt;color:#666;'
            'padding:0 0.95in;display:flex;justify-content:space-between;">'
            f'<span>{_e(running)}</span><span><span class="pageNumber"></span> of <span class="totalPages"></span></span></div>')
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            pg = browser.new_page()
            pg.set_content(page, wait_until="load")
            pg.pdf(path=str(path), format="Letter", print_background=True, display_header_footer=True,
                   header_template="<span></span>", footer_template=foot,
                   margin={"top": "0.9in", "bottom": "0.95in", "left": "0.95in", "right": "0.95in"})
        finally:
            browser.close()
    return path
