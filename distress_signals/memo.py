"""The memo of a saved case as a typeset document, written to be handed to the company's own finance officer:
the reader who holds the books and can check every figure.

Nothing in it is written anew. Every sentence is the analyst's as the case holds it or the fixed wording here,
and every figure is the store's, computed from the case as it stands now. `document` lays the case out in the
order a reader takes it: what is at stake in each outcome and when, the event, how it reaches the revenue, and
how the estimate is built, each figure with what it rests on. A record the case quotes is one numbered note
with its title, dates and address and the passages quoted from it; a saved query a figure is read from is one
numbered computation with its rows and its SQL, so the figure can be worked out again. `html` renders the
document as one self-contained page, and `pdf` prints that page through headless Chromium.

It leaves out what is the desk's and not the reader's: the person the case names at the company, and findings
recorded about the company apart from this case. Those stay in lookup, the export and the deal's memo."""

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


def _share(x):
    """A share of revenue: to two places under one percent, where one place would show a real shortfall as 0.0%."""
    return None if x is None else (f"{x:.2%}" if 0 < abs(x) < 0.01 else f"{x:.1%}")


def _title(o, oid):
    """A record's title for a note. A title that is only what the writing program left ('Microsoft Word - ...')
    loses that; a record with none is named by its publisher and its file."""
    title = re.sub(r"^Microsoft Word - ", "", ((o.get("data") or {}).get("title") or "").strip())
    if title:
        return title
    name = (o.get("url") or "").rstrip("/").rsplit("/", 1)[-1]
    return " · ".join(x for x in (o.get("source"), name) if x) or oid


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
            "title": _title(o, oid), "url": o.get("url"),
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

    # ---- what is at stake
    worst = max(res["worlds"], key=lambda w: w["shortfall"])
    extra = [(k, title) for k, title in (("deferred_past_period", "Deferred past the period"), ("lost", "Not recovered"),
                                        ("contribution_loss", "Contribution lost"), ("cash_delayed", "Cash delayed"))
             if any(w.get(k) is not None for w in res["worlds"])]
    def changed(w):
        """The inputs that take another value in an outcome, each by its label."""
        return [{"label": forms.label(k, case["inputs"][k], cur), "value": shown(v, k)[0]} for k, v in w["values"].items()]

    ranked = sorted(res["worlds"], key=lambda w: -w["shortfall"])                  # the largest shortfall first
    top = worst["shortfall"] if worst["shortfall"] > 0 else None
    outcomes = [{"outcome": w["label"].split("; "), "revenue": briefs.money(w["revenue"], cur),
                 "shortfall": briefs.money(w["shortfall"], cur), "share": _share(w["share"]) or "–",
                 "extra": [briefs.money(w.get(k), cur) for k, _ in extra], "worst": w is worst, "changed": changed(w),
                 "moves": abs(w["shortfall"]) > 1e-9, "gain": w["shortfall"] < -1e-9,
                 "against_worst": max(0.0, min(1.0, w["shortfall"] / top)) if top else 0.0}       # drawn as a bar
                for w in ranked]

    # ---- the event, and the other propositions the case reads
    def proposition(p):
        about = " ".join(x for x in (p.get("description"), p.get("timing")) if x)
        return {"title": p["title"], "text": p["text"], "about": about or None, "kind": p.get("kind"),
                "only_if": [props[k]["title"] for k in p.get("implies") or [] if k in props],
                "only_if_not": [props[k]["title"] for k in p.get("implies_not") or [] if k in props],
                "start": str(p.get("window_start") or "")[:10] or None, "end": str(p["window_end"])[:10],
                "window": _span(p["window_start"], p["window_end"]) if p.get("window_start") else f"On or by {_date(p['window_end'])}",
                "shown_by": p.get("resolution_source"), "notes": [note(e) for e in p.get("evidence") or []]}

    event_keys = [e["key"] for e in c["events"]]
    events = [proposition(e) for e in c["events"]]
    others = [proposition(props[k]) | {"within": props[props[k]["parent"]]["title"] if props[k].get("parent") else None}
              for k in res["reads"] if k not in event_keys]

    # the same propositions as a tree: a way under what it divides, and one that is true only with another under it
    read = [*event_keys, *(k for k in res["reads"] if k not in event_keys)]
    under = {}
    for k in read:
        p = props[k]
        above = p.get("parent") if p.get("parent") in read else next((x for x in p.get("implies") or [] if x in read), None)
        if above and k not in event_keys:
            how = ("A way it can turn out" if p.get("side") != "no" else "A way it can fail to") if p.get("parent") == above else "True only with it"
            under.setdefault(above, []).append((k, how))

    def node(k, how=None):
        return proposition(props[k]) | {"relation": how, "children": [node(x, h) for x, h in under.get(k, [])]}

    placed = {x for kids in under.values() for x, _ in kids}
    tree = [node(k) for k in read if k not in placed]

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
        formulas.append({"name": "What the share is of", "formula": case["denominator"]["formula"]})

    stated = [*case["inputs"].values(), *(v for g in case["given_values"] for v in g["values"].values())]
    read = [bool(f.get("source") or f.get("result")) for f in stated]
    carried = [bool(f.get("assumption")) for f in stated]
    rests = {"figures": len(stated), "read": sum(r and not a for r, a in zip(read, carried)),
             "carried": sum(r and a for r, a in zip(read, carried)), "assumed": sum(not r for r in read)}

    findings = [{"kind": f["kind"], "statement": f["statement"],
                 "notes": [note(e) for e in f.get("evidence") or []], "computations": [comp(r) for r in f.get("results") or []]}
                for f in c["findings"] if f.get("case_id") == c["id"]]            # those recorded on this case
    return {
        "company": co["name"], "event": briefs.event_title(c), "withdrawn": c["status"] == "withdrawn",
        "as_of": _date(c["row"]["updated"]), "decided": _date(briefs.event_date(c)),
        "period": _span(case["period"]["start"], case["period"]["end"]),
        "dates": {"as_of": str(c["row"]["updated"])[:10], "period": [str(case["period"]["start"]), str(case["period"]["end"])]},
        "currency": cur, "case_id": c["id"], "revision": c["row"]["revision"],
        "stream": case["stream"],
        "at_stake": {"shortfall": briefs.money(worst["shortfall"], cur), "share": _share(worst.get("share")),
                     "contribution": briefs.money(worst["contribution_loss"], cur) if worst.get("contribution_loss") is not None else None,
                     "of": (res.get("denominator") or {}).get("label"), "outcome": worst["label"].split("; "),
                     "of_value": briefs.money(res["denominator"]["value"], cur) if res.get("denominator") else None,
                     "baseline": briefs.money(res["baseline_revenue"], cur)},
        "outcomes": outcomes, "extra_columns": [title for _, title in extra], "rests": rests,
        "events": events, "others": others, "tree": tree,
        "labels": {name: forms.label(name, inp, cur) for name, inp in case["inputs"].items()},
        "business": co.get("description"), "relationships": co.get("relationships"),
        "dependency": case["dependency"], "timing": case["timing"], "establishes": establishes,
        "formulas": formulas,
        "changing": [f for f in figures if f["outcomes"]], "constant": [f for f in figures if not f["outcomes"]],
        "figures": figures,
        "condition": [x.strip() for x in c["condition"].split(" Or: ") if x.strip()], "findings": findings,
        "notes": notes.items, "computations": comps.items,
        "change_note": (c["revisions"][-1].get("change_note") or "").strip() if c["revisions"] else ""}


# ---------- the page ----------

# The palette and the faces every page of the product shares (the memo here; the run pages through runpage.PUBLICATION).
TOKENS = """
  --ink: #0F172A; --body: #1E293B; --soft: #475569; --mute: #64748B;
  --navy: #1B365D; --navy-tint: #EEF3F9; --navy-line: #C7D7EA;
  --loss: #991B1B; --loss-tint: #FEF2F2; --gain: #166534;
  --obs-ink: #0F5132; --obs-bg: #EDF7F2; --obs-line: #B8DEC9;
  --asm-ink: #8A3A12; --asm-bg: #FEF7EC; --asm-line: #EBD2A9;
  --rule: #CBD5E1; --rule-soft: #E2E8F0; --tint: #F8FAFC; --paper: #FFFFFF; --desk-bg: #E8ECF1;
  --serif: "Iowan Old Style", "Charter", "Palatino Linotype", Georgia, serif;
  --sans: -apple-system, BlinkMacSystemFont, "Helvetica Neue", "Inter", system-ui, sans-serif;
  --mono: "SF Mono", ui-monospace, Menlo, Consolas, monospace;
"""

CSS = ":root {" + TOKENS + "}" + """
* { box-sizing: border-box; }
html { background: var(--desk-bg); color: var(--body); -webkit-font-smoothing: antialiased; }
body { margin: 0; font-family: var(--sans); font-size: 9pt; line-height: 1.47; color: var(--body); }
a { color: var(--navy); }

/* the strip across the top of the screen: not printed */
.toolbar { position: sticky; top: 0; z-index: 40; background: rgba(15,23,42,.96); backdrop-filter: blur(8px); color: #E2E8F0;
  border-bottom: 1px solid #334155; padding: 9px 24px; display: flex; align-items: center; justify-content: space-between; gap: 16px; font-size: 12.5px; }
.tb-left { display: flex; align-items: center; gap: 16px; min-width: 0; }
.tb-back { color: #FFFFFF; font-weight: 600; text-decoration: none; white-space: nowrap; }
.tb-brand { font-weight: 700; letter-spacing: .08em; text-transform: uppercase; font-size: 11px; color: #94A3B8; white-space: nowrap; }
.tb-nav { display: flex; gap: 14px; overflow-x: auto; }
.tb-nav a { color: #CBD5E1; text-decoration: none; font-weight: 500; white-space: nowrap; }
.tb-nav a:hover, .tb-back:hover { color: #FFFFFF; text-decoration: underline; }
.tb-right { display: flex; align-items: center; gap: 12px; flex-shrink: 0; }
.tb-right a { color: #CBD5E1; text-decoration: none; font-weight: 600; white-space: nowrap; }
.tb-right a.get { border: 1px solid #475569; background: var(--navy); color: #FFFFFF; padding: 5px 12px; border-radius: 3px; }
.tb-right a.get:hover { background: #264878; }
@media (max-width: 760px) { .tb-nav, .tb-brand { display: none; } }

.sheet { width: 8.5in; max-width: calc(100% - 32px); margin: 24px auto 48px; background: var(--paper); padding: .54in .62in .66in;
  box-shadow: 0 2px 10px rgba(15,23,42,.08), 0 18px 40px rgba(15,23,42,.08); border: 1px solid #D6DCE4; }

.masthead { border-top: 2.5pt solid var(--navy); padding-top: 8.5pt; margin-bottom: 10pt; }
.mast-eyebrow { display: flex; justify-content: space-between; align-items: baseline; gap: 12pt; font-size: 7.1pt; font-weight: 700;
  letter-spacing: .13em; text-transform: uppercase; color: var(--navy); margin-bottom: 4.5pt; }
.mast-eyebrow .right { color: var(--mute); font-weight: 600; letter-spacing: .07em; font-variant-numeric: tabular-nums; }
h1.co-name { font-family: var(--serif); font-size: 19.5pt; font-weight: 700; line-height: 1.12; letter-spacing: -.015em; color: var(--ink); margin: 0 0 2.5pt; }
.subject-line { font-family: var(--serif); font-size: 10.6pt; line-height: 1.3; color: var(--soft); margin: 0 0 8pt; }
.meta-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 6pt 12pt; border-top: .75pt solid var(--rule); border-bottom: .75pt solid var(--rule); padding: 6pt 0; }
.meta-cell .ml { font-size: 6.4pt; font-weight: 700; letter-spacing: .09em; text-transform: uppercase; color: var(--mute); margin-bottom: 1pt; }
.meta-cell .mv { font-size: 8.4pt; font-weight: 600; color: var(--ink); line-height: 1.25; font-variant-numeric: tabular-nums; }
.meta-cell .ms { font-size: 7.2pt; color: var(--mute); line-height: 1.25; margin-top: 1pt; }
.withdrawn-banner { background: var(--loss-tint); border: 1pt solid #FCA5A5; border-left: 3pt solid var(--loss); color: var(--loss); padding: 7pt 10pt; font-size: 8.4pt; margin-bottom: 10pt; }

.kpi-strip { display: grid; grid-template-columns: repeat(auto-fit, minmax(110pt, 1fr)); background: var(--tint); border: .75pt solid var(--rule-soft);
  border-top: 1.25pt solid var(--ink); margin-bottom: 10pt; break-inside: avoid; }
.kpi { padding: 7.5pt 10pt; border-right: .75pt solid var(--rule-soft); }
.kpi:last-child { border-right: 0; }
.kpi-primary { border-top: 2pt solid var(--loss); margin-top: -1.25pt; background: #FFFFFF; }
.kpi-label { font-size: 6.4pt; font-weight: 700; letter-spacing: .09em; text-transform: uppercase; color: var(--mute); margin-bottom: 2.5pt; }
.kpi-val { font-size: 16pt; font-weight: 700; line-height: 1.08; color: var(--ink); font-variant-numeric: tabular-nums lining-nums; letter-spacing: -.02em; }
.kpi-val.loss { color: var(--loss); }
.kpi-sub { font-size: 7.2pt; color: var(--soft); margin-top: 2pt; line-height: 1.25; font-variant-numeric: tabular-nums; }
.ep-bar { display: flex; height: 5.5pt; width: 100%; background: var(--rule-soft); overflow: hidden; margin: 5.5pt 0 4pt; border-radius: 1pt; }
.ep-seg.observed { background: #1B7A4D; } .ep-seg.carried { background: #1B365D; } .ep-seg.assumed { background: #C26D2D; }

.timeline-wrap { border: .75pt solid var(--rule-soft); padding: 5pt 8pt 3.5pt; margin-bottom: 11pt; background: #FFFFFF; break-inside: avoid; }
.timeline-cap { font-size: 6.5pt; font-weight: 700; letter-spacing: .09em; text-transform: uppercase; color: var(--mute); margin-bottom: 1.5pt; }
.timeline-svg { width: 100%; height: auto; display: block; font-family: var(--sans); }

section { margin-bottom: 12pt; }
h2.sec-h { font-family: var(--serif); font-size: 11.6pt; font-weight: 700; color: var(--ink); border-bottom: 1pt solid var(--navy); padding-bottom: 2.5pt;
  margin: 12pt 0 7pt; display: flex; align-items: baseline; gap: 7pt; break-after: avoid; }
.sec-num { font-family: var(--sans); font-size: 7.5pt; font-weight: 700; color: var(--navy); letter-spacing: .06em; font-variant-numeric: tabular-nums; }
h3.sub-h { font-family: var(--sans); font-size: 7.65pt; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; color: var(--navy); margin: 8.5pt 0 3.5pt; break-after: avoid; }

table.data-table { width: 100%; border-collapse: collapse; font-size: 8.15pt; line-height: 1.36; border-top: 1.25pt solid var(--ink); border-bottom: 1pt solid var(--ink); }
table.data-table thead { display: table-header-group; }
table.data-table th { text-align: left; font-size: 6.65pt; font-weight: 700; letter-spacing: .065em; text-transform: uppercase; color: var(--soft);
  background: var(--tint); padding: 5pt 6pt; border-bottom: .75pt solid var(--rule); white-space: nowrap; }
table.data-table th.wrap-th { white-space: normal; line-height: 1.25; }
table.data-table th.num, table.data-table td.num { text-align: right; font-variant-numeric: tabular-nums lining-nums; white-space: nowrap; }
table.data-table td { padding: 3.6pt 5.5pt; border-bottom: .5pt solid var(--rule-soft); vertical-align: top; }
table.data-table tr { break-inside: avoid; }
table.data-table tbody tr:last-child td { border-bottom: 0; }
table.data-table.compact { font-size: 7.6pt; }
table.data-table.compact th, table.data-table.compact td { padding: 3.8pt 5pt; }
table.kv-table .kv-k { color: var(--soft); font-weight: 500; }
table.kv-table .kv-split { border-left: .75pt solid var(--rule-soft); padding-left: 9pt; }
.row-baseline td { background: var(--tint); color: var(--soft); }
.row-worst td { background: #FFFBFB; }
.row-neutral td { color: var(--mute); }
.bold { font-weight: 700; color: var(--ink); } .loss { color: var(--loss) !important; } .gain { color: var(--gain) !important; } .mute { color: var(--mute); }
.sc-title-row { display: flex; flex-wrap: wrap; align-items: baseline; gap: 3pt 5pt; }
.sc-title { font-weight: 600; color: var(--ink); }
.row-neutral .sc-title { font-weight: 500; color: var(--soft); }
.sc-sub-tag { font-weight: 500; font-size: 7.3pt; color: var(--mute); }
.cond-chip { display: inline-block; font-size: 6.7pt; font-weight: 600; padding: .5pt 4pt; border-radius: 2pt; line-height: 1.3; }
.cond-chip.pos { background: var(--navy-tint); color: var(--navy); border: .5pt solid var(--navy-line); }
.cond-chip.neg { background: #F1F5F9; color: var(--soft); border: .5pt solid var(--rule); }
.sc-changes { font-size: 7.25pt; color: var(--mute); margin-top: 2pt; line-height: 1.3; }
th.bar-col { width: 72pt; } td.bar-cell { width: 72pt; vertical-align: middle; }
.mag-track { height: 6.5pt; width: 100%; background: #EDF2F7; border-radius: 1pt; overflow: hidden; }
.mag-fill { height: 100%; background: var(--loss); border-radius: 1pt; }
.table-note { font-size: 7.25pt; color: var(--mute); margin-top: 4pt; line-height: 1.35; }

.context-lead { margin-bottom: 8pt; padding-bottom: 7pt; border-bottom: .5pt solid var(--rule-soft); break-inside: avoid; }
.mech-two-col { display: grid; grid-template-columns: 1.15fr .85fr; gap: 14pt; }
.prose-block { break-inside: avoid; }
.prose-kicker { font-size: 6.8pt; font-weight: 700; letter-spacing: .085em; text-transform: uppercase; color: var(--navy); margin-bottom: 2.5pt; }
.prose-block p { margin: 0 0 3.5pt; font-size: 8.45pt; line-height: 1.46; color: var(--body); }
.prose-block p:last-child { margin-bottom: 0; }
.stream-def { color: var(--soft); font-style: italic; font-size: 8.15pt !important; }
.rel-line { color: var(--soft); font-size: 8.15pt !important; }
.stream-def strong, .rel-line strong { color: var(--ink); font-weight: 600; font-style: normal; }
ul.claims { margin: 2pt 0 0; padding-left: 12pt; font-size: 8.3pt; } ul.claims li { margin-bottom: 2pt; }

.prop-node { border: .75pt solid var(--rule-soft); border-left: 2.25pt solid var(--navy); background: #FFFFFF; padding: 7pt 9.5pt; margin-bottom: 7pt; }
.prop-main { break-inside: avoid; }
.prop-node.child { border-left: 1.75pt solid #94A3B8; background: var(--tint); margin-top: 5pt; margin-bottom: 0; break-inside: avoid; }
.prop-children { margin-top: 5.5pt; padding-left: 10pt; border-left: 1pt dotted var(--rule); }
.prop-head { display: flex; justify-content: space-between; align-items: baseline; gap: 10pt; margin-bottom: 3pt; flex-wrap: wrap; }
.prop-title { font-family: var(--serif); font-size: 9.8pt; font-weight: 700; color: var(--ink); }
.prop-meta { font-size: 7.1pt; font-weight: 600; color: var(--mute); font-variant-numeric: tabular-nums; }
.prop-meta .sep { margin: 0 3pt; color: var(--rule); }
.prop-text { margin: 0 0 4pt; font-size: 8.5pt; color: var(--ink); line-height: 1.44; }
.prop-desc { margin: 0 0 3pt; font-size: 8pt; color: var(--soft); line-height: 1.42; }
.res-source { margin-top: 4.5pt; padding: 4pt 7pt; background: var(--navy-tint); border: .5pt solid var(--navy-line); font-size: 7.7pt; color: var(--navy);
  display: flex; gap: 8pt; align-items: baseline; }
.res-kicker { font-size: 6.3pt; font-weight: 700; letter-spacing: .09em; text-transform: uppercase; white-space: nowrap; flex-shrink: 0; }

.formula-box { background: var(--tint); border: .75pt solid var(--rule-soft); border-left: 2.25pt solid var(--navy); padding: 7pt 9.5pt; margin-bottom: 8pt; break-inside: avoid; }
.formula-row { margin-bottom: 5pt; } .formula-row:last-child { margin-bottom: 0; }
.formula-lbl { font-size: 6.6pt; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; color: var(--navy); }
.formula-readable { font-family: var(--serif); font-size: 8.65pt; color: var(--ink); margin: 1pt 0; }
.formula-code { font-family: var(--mono); font-size: 7.15pt; color: var(--mute); overflow-wrap: anywhere; }

table.drivers { table-layout: fixed; } table.drivers td, table.drivers th { overflow-wrap: anywhere; }
table.drivers th.c-in { width: 20%; } table.drivers th.c-out { width: 19%; } table.drivers th.c-val { width: 11%; } table.drivers th.c-basis { width: 15%; }
table.drivers td.num { white-space: normal; }
.drv-sym { font-family: var(--mono); font-size: 6.8pt; color: var(--mute); margin-top: 1pt; }
.drv-cond { color: var(--body); font-size: 7.75pt; }
.drv-base-row td { background: #FCFDFF; } .drv-base-row .drv-cond { color: var(--mute); font-style: italic; }
.drv-note { font-size: 7.65pt; color: var(--soft); line-height: 1.34; }
.cell-src { display: block; font-weight: 600; color: var(--navy); font-size: 7.2pt; margin-bottom: 1pt; }
.unit { display: block; font-weight: 400; color: var(--mute); font-size: 6.9pt; white-space: normal; line-height: 1.25; }
.ep-pill { display: inline-block; font-size: 6.3pt; font-weight: 700; letter-spacing: .05em; text-transform: uppercase; padding: 1.5pt 4pt; border-radius: 2pt; white-space: nowrap; }
.ep-pill.observed { color: var(--obs-ink); background: var(--obs-bg); border: .5pt solid var(--obs-line); }
.ep-pill.carried { color: var(--navy); background: var(--navy-tint); border: .5pt solid var(--navy-line); }
.ep-pill.assumed { color: var(--asm-ink); background: var(--asm-bg); border: .5pt solid var(--asm-line); }

.fn-group { margin-left: 2pt; white-space: nowrap; }
.fn-ref { font-size: 6.7pt; font-weight: 700; color: var(--navy); text-decoration: none; vertical-align: super; line-height: 0; margin-left: 1pt; font-variant-numeric: tabular-nums; }
.fn-ref.q-ref { color: #6D28D9; } .fn-ref:hover { text-decoration: underline; }

.appendix { break-before: page; }
.appendix-banner { font-size: 7.1pt; font-weight: 700; letter-spacing: .14em; text-transform: uppercase; color: var(--mute); border-bottom: .75pt solid var(--rule);
  padding-bottom: 3.5pt; margin-bottom: 9pt; }
.finding-card { border: .75pt solid var(--rule-soft); border-left: 2pt solid #475569; padding: 7pt 9.5pt; margin-bottom: 7.5pt; background: var(--tint); break-inside: avoid; }
.finding-kind { font-size: 6.7pt; font-weight: 700; letter-spacing: .09em; text-transform: uppercase; color: var(--navy); margin-bottom: 2.5pt; }
.finding-card p { margin: 0; font-size: 8.2pt; line-height: 1.46; color: var(--body); }
.src-entry { display: grid; grid-template-columns: 22pt 1fr; gap: 5pt; padding: 5.5pt 0; border-bottom: .5pt solid var(--rule-soft); break-inside: avoid; }
.src-entry:last-child { border-bottom: 0; }
.src-num { font-size: 8pt; font-weight: 700; color: var(--navy); font-variant-numeric: tabular-nums; padding-top: .5pt; }
.src-title { font-weight: 700; font-size: 8.4pt; color: var(--ink); line-height: 1.3; }
.src-meta { font-size: 7.35pt; color: var(--mute); margin-top: 1pt; }
.src-url { display: inline-block; font-size: 7pt; color: var(--navy); text-decoration: none; overflow-wrap: anywhere; word-break: break-word; margin-top: 1pt; }
.quote-list { margin-top: 4pt; display: flex; flex-direction: column; gap: 3.5pt; }
.quote-item { border-left: 1.5pt solid var(--rule); padding-left: 6.5pt; }
.quote-sup { display: block; font-size: 7pt; font-weight: 700; color: var(--navy); margin-bottom: .5pt; }
.quote-item blockquote { margin: 0; font-family: var(--serif); font-size: 7.85pt; font-style: italic; color: var(--body); line-height: 1.4; }
.comp-card { border: .75pt solid var(--rule-soft); padding: 6pt 8.5pt; margin-bottom: 6pt; background: #FFFFFF; break-inside: avoid; }
.comp-head { display: flex; align-items: baseline; gap: 6pt; margin-bottom: 3pt; flex-wrap: wrap; }
.comp-id { font-size: 7.9pt; font-weight: 700; color: #6D28D9; font-variant-numeric: tabular-nums; }
.comp-title { font-weight: 700; font-size: 8.6pt; color: var(--ink); }
.comp-note { margin: 0 0 5.5pt; font-size: 7.75pt; color: var(--soft); line-height: 1.38; }
.comp-table-wrap { margin-bottom: 5.5pt; } .comp-trunc { font-size: 7pt; color: var(--mute); margin-top: 2.5pt; }
.sql-kicker { font-size: 6.9pt; font-weight: 700; letter-spacing: .06em; text-transform: uppercase; color: var(--mute); margin-bottom: 2.5pt; }
pre.sql { margin: 2pt 0 0; padding: 4.5pt 7pt; background: var(--tint); border: .5pt solid var(--rule-soft); font-family: var(--mono); font-size: 6.6pt;
  line-height: 1.32; color: var(--soft); white-space: pre-wrap; overflow-wrap: anywhere; }
ul.cond-list { list-style: none; margin: 4pt 0 0; padding: 0; font-size: 7.85pt; color: var(--soft); line-height: 1.44; }
ul.cond-list li { display: grid; grid-template-columns: 52pt 1fr; gap: 6pt; margin-bottom: 4.5pt; align-items: baseline; }
.clause-tag { font-size: 6.4pt; font-weight: 700; letter-spacing: .07em; text-transform: uppercase; color: var(--navy); background: var(--navy-tint);
  border: .5pt solid var(--navy-line); padding: 1pt 4pt; text-align: center; border-radius: 2pt; }
.doc-foot { margin-top: 16pt; padding-top: 6pt; border-top: .75pt solid var(--rule); display: flex; justify-content: space-between; gap: 12pt; font-size: 7pt; color: var(--mute); }

@page { size: Letter; margin: .5in .56in .62in; }
@media print {
  html { background: #FFFFFF; }
  .toolbar { display: none !important; }
  .sheet { width: auto; max-width: none; margin: 0; padding: 0; box-shadow: none; border: 0; }
}
"""


def _e(s):
    return _html.escape(str(s if s is not None else ""), quote=False)


PILL = {"Stated in a record": ("observed", "Read"), "Computed from records": ("observed", "Read"), "Assumed": ("assumed", "Assumed"),
        "Stated in a record, carried over by an assumption": ("carried", "Read · carried over"),
        "Computed from records, carried over by an assumption": ("carried", "Read · carried over")}


def _pill(kind):
    cls, word = PILL[kind]
    return f'<span class="ep-pill {cls}">{_e(word)}</span>'


def _scenario(parts, changed=()):
    """An outcome as the store labels it: its first proposition as the title, the rest as chips, with those that
    turn out false set apart, and beneath them the inputs that take another value there."""
    first, rest = parts[0], parts[1:]
    chips = "".join(f'<span class="cond-chip {"neg" if p.startswith("Not: ") else "pos"}">{_e(p)}</span>' for p in rest)
    moves = " · ".join(f'{_e(x["label"])}: {_e(x["value"])}' for x in changed)
    return (f'<div class="sc-title-row"><span class="sc-title">{_e(first)}</span>{chips}</div>'
            + (f'<div class="sc-changes">{moves}</div>' if moves else ""))


def _in_words(formula, labels):
    """A formula with each input by its label, and the signs a reader expects."""
    out = []
    for tok in re.findall(r"[A-Za-z_][A-Za-z0-9_]*|\d+(?:\.\d+)?|\S", formula):
        out.append(f"<em>{_e(labels[tok])}</em>" if tok in labels else {"*": " &times; ", "-": " &minus; ", "+": " + ", "/": " &divide; ",
                                                                         ",": ", "}.get(tok, _e(tok)))
    return "".join(out)


def _dates(d):
    """One strip of dates as an inline drawing: the day the memo is as of, when each proposition is decided (a
    span where it has a window, a day where it has one date), and the period revenue is counted over, on one
    axis marked by quarter. It shows how much of the period lies after each decision."""
    day = dt.date.fromisoformat
    as_of, (p0, p1) = day(d["dates"]["as_of"]), (day(x) for x in d["dates"]["period"])
    rows = [(p["title"], day(p["start"]) if p["start"] else None, day(p["end"])) for p in [*d["events"], *d["others"]]]
    lo = min([as_of, p0] + [a for _, a, _ in rows if a] + [b for _, _, b in rows])
    hi = max([p1] + [b for _, _, b in rows])
    lo, hi = lo - dt.timedelta(days=12), hi + dt.timedelta(days=12)
    width, step, top = 680, 19, 24
    x = lambda t: round(6 + (width - 12) * (t - lo).days / ((hi - lo).days or 1), 1)  # noqa: E731
    base = top + step * (len(rows) + 1) + 3
    o = [f'<svg class="timeline-svg" viewBox="0 0 {width} {base + 14}" role="img" aria-label="The dates the estimate turns on">']
    year, long = lo.year, (hi - lo).days > 1100
    while dt.date(year, 1, 1) <= hi:                                 # the axis: a line at each quarter, named
        for q, month in enumerate((1, 4, 7, 10), 1):
            t = dt.date(year, month, 1)
            if lo <= t <= hi and (month == 1 or not long):
                anchor = "end" if x(t) > width - 26 else "start" if x(t) < 26 else "middle"
                o.append(f'<line x1="{x(t)}" x2="{x(t)}" y1="14" y2="{base}" stroke="#E2E8F0" stroke-width="1"/>')
                name = f"{year}" if long else f"Q{q} ’{year % 100:02d}"
                o.append(f'<text x="{x(t)}" y="{base + 11}" fill="#64748B" font-size="8.2" text-anchor="{anchor}">{name}</text>')
        year += 1

    def row(n, a, b, text, period=False):
        y = top + step * n
        left, right = x(a), x(b)
        far = left > width * 0.55                                   # a row near the right edge is labelled leftwards
        ink = "#0F172A" if period else "#475569"
        o.append(f'<text x="{right if far else left}" y="{y - 2.5}" fill="{ink}" font-size="8.2" font-weight="{700 if period else 600}" '
                 f'text-anchor="{"end" if far else "start"}">{_e(text)}</text>')
        if period:
            o.append(f'<rect x="{left}" y="{y}" width="{round(right - left, 1)}" height="7.5" rx="1.5" fill="#1B365D" fill-opacity=".18" stroke="#1B365D" stroke-width="1.1"/>')
            return
        if right - left >= 2:
            o.append(f'<rect x="{left}" y="{y + .5}" width="{round(right - left, 1)}" height="6.5" rx="1.5" fill="#EEF3F9" stroke="#1B365D" stroke-width="1"/>')
        else:                                                       # one date: a dotted line from today to the day
            o.append(f'<line x1="{x(as_of)}" x2="{right}" y1="{y + 3.7}" y2="{y + 3.7}" stroke="#64748B" stroke-width="1" stroke-dasharray="1.5,2.5"/>')
        o.append(f'<polygon points="{right},{y - .3} {right + 4},{y + 3.7} {right},{y + 7.7} {right - 4},{y + 3.7}" fill="#1B365D"/>')

    for n, (title, a, b) in enumerate(rows):
        when = f"{a:%b} {a.day}, {a.year} – {b:%b} {b.day}, {b.year}" if a else f"{b:%b} {b.day}, {b.year}"
        row(n, a or b, b, f"{title[:70] + ('…' if len(title) > 70 else '')} · decided {when}")
    row(len(rows), p0, p1, f"Period revenue is counted over · {d['period']}", period=True)
    o.append(f'<line x1="{x(as_of)}" x2="{x(as_of)}" y1="11" y2="{base}" stroke="#991B1B" stroke-width="1.2" stroke-dasharray="2.5,2.5"/>')
    o.append(f'<text x="{x(as_of) + 3}" y="9" fill="#991B1B" font-size="7.8" font-weight="600">As of {as_of:%b} {as_of.day}, {as_of.year}</text></svg>')
    return "".join(o)


def html(doc, bar=None):
    """The document as one self-contained page: no script, no outside resource, print rules for Letter. A strip
    across the top of the screen leads to each section, and is not printed. `bar` adds to it what the page's
    host offers: {back, back_label, pdf, name}, the way back to the case and the PDF."""
    d, o = doc, []
    add = o.append

    def refs(notes=(), computations=()):
        """The marks after a sentence: a record's number, with the passage's letter where the record is quoted more
        than once, and a computation's C-number."""
        marks = []
        for n, k in notes:
            letter = chr(97 + k) if k is not None and len(d["notes"][n - 1]["passages"]) > 1 else ""
            marks.append(f'<a class="fn-ref" href="#note-{n}">[{n}{letter}]</a>')
        marks += [f'<a class="fn-ref q-ref" href="#comp-{n}">[C{n}]</a>' for n in computations]
        return f'<span class="fn-group">{"".join(marks)}</span>' if marks else ""

    def amount(v):
        return f'{_e(v["value"])}' + (f'<span class="unit">{_e(v["unit"])}</span>' if v.get("unit") else "")

    def why(v):
        read = f'<span class="cell-src">{_e(v["read"])}</span>' if v.get("read") else ""
        return f'{read}{_e(v["reasoning"])}{refs(v["notes"], v["computations"])}'

    title = f"{d['company']}: {d['event']}"
    sections = [("sec-stake", "At stake"), ("sec-reach", "Mechanism"), ("sec-event", "Event"), ("sec-model", "Estimate"),
                ("sec-notes", "Records")]
    back = (f'<a class="tb-back" href="{_html.escape(bar["back"], quote=True)}">← {_e(bar["back_label"])}</a>' if bar else "")
    get = (f'<a href="{_html.escape(bar["pdf"], quote=True)}">Open PDF</a>'
           f'<a class="get" href="{_html.escape(bar["pdf"], quote=True)}" download="{_html.escape(bar["name"], quote=True)}">Download PDF</a>' if bar else "")
    add(f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{_e(title)}</title>'
        f'<meta name="viewport" content="width=device-width,initial-scale=1"><style>{CSS}</style></head><body>'
        f'<div class="toolbar"><div class="tb-left">{back}<span class="tb-brand">Revenue exposure memo</span><nav class="tb-nav">'
        + "".join(f'<a href="#{k}">{_e(v)}</a>' for k, v in sections) + f'</nav></div><div class="tb-right">{get}</div></div><div class="sheet">')

    # the masthead
    a = d["at_stake"]
    add(f'<header class="masthead"><div class="mast-eyebrow"><span>Revenue exposure memo</span><span class="right">As of {_e(d["as_of"])}</span></div>'
        f'<h1 class="co-name">{_e(d["company"])}</h1><p class="subject-line">{_e(d["event"])}</p><div class="meta-grid">'
        f'<div class="meta-cell"><div class="ml">Decided on or by</div><div class="mv">{_e(d["decided"])}</div><div class="ms">The event</div></div>'
        f'<div class="meta-cell"><div class="ml">Period</div><div class="mv">{_e(d["period"])}</div><div class="ms">Revenue is counted over it</div></div>'
        + (f'<div class="meta-cell"><div class="ml">Share is of</div><div class="mv">{_e(a["of_value"])}</div><div class="ms">{_e(a["of"])}</div></div>' if a["of"] else "")
        + f'<div class="meta-cell"><div class="ml">Currency</div><div class="mv">{_e(d["currency"])}</div><div class="ms">Each figure is conditional on its outcome</div></div>'
        '</div></header>')
    if d["withdrawn"]:
        add('<div class="withdrawn-banner">This case is withdrawn. It reads as its last revision held it.</div>')

    # the key figures
    r = d["rests"]
    n = max(r["figures"], 1)
    add('<div class="kpi-strip">'
        f'<div class="kpi kpi-primary"><div class="kpi-label">Largest shortfall</div><div class="kpi-val loss">{_e(a["shortfall"])}</div>'
        f'<div class="kpi-sub">{_e(a["share"] + " of revenue" if a["share"] else "in the period")}</div></div>'
        + (f'<div class="kpi"><div class="kpi-label">Contribution lost with it</div><div class="kpi-val">{_e(a["contribution"])}</div>'
           '<div class="kpi-sub">Revenue less the costs that move with it</div></div>' if a["contribution"] else "")
        + f'<div class="kpi"><div class="kpi-label">Revenue as things stand</div><div class="kpi-val">{_e(a["baseline"])}</div>'
          f'<div class="kpi-sub">{_e(d["period"])}</div></div>'
        f'<div class="kpi"><div class="kpi-label">Basis of the {r["figures"]} figures</div><div class="ep-bar">'
        f'<span class="ep-seg observed" style="width:{100 * r["read"] / n:.1f}%"></span><span class="ep-seg carried" style="width:{100 * r["carried"] / n:.1f}%"></span>'
        f'<span class="ep-seg assumed" style="width:{100 * r["assumed"] / n:.1f}%"></span></div>'
        f'<div class="kpi-sub">{r["read"]} read · {r["carried"]} read and carried over · {r["assumed"]} assumed</div></div></div>')
    add(f'<div class="timeline-wrap"><div class="timeline-cap">When each is decided, against the period</div>{_dates(d)}</div>')

    # 01 what is at stake
    cols = "".join(f'<th class="num">{_e(t)}</th>' for t in d["extra_columns"])
    add('<section id="sec-stake"><h2 class="sec-h"><span class="sec-num">01</span> Revenue at stake in each outcome</h2>'
        f'<table class="data-table"><thead><tr><th>Outcome</th><th class="num">Revenue in the period</th><th class="num">Shortfall</th>'
        f'<th class="num">Share</th>{cols}<th class="bar-col">Against the largest</th></tr></thead><tbody>')
    dash = "".join('<td class="num mute">–</td>' for _ in d["extra_columns"])
    add(f'<tr class="row-baseline"><td><div class="sc-title-row"><span class="sc-title">As things stand</span></div></td>'
        f'<td class="num">{_e(a["baseline"])}</td><td class="num mute">–</td><td class="num mute">–</td>{dash}<td class="bar-cell"><div class="mag-track"></div></td></tr>')
    for w in d["outcomes"]:
        more = "".join(f'<td class="num">{_e(x)}</td>' for x in w["extra"])
        cls = "row-worst" if w["worst"] and w["moves"] else "" if w["moves"] else "row-neutral"
        tone = "gain bold" if w["gain"] else "loss bold" if w["moves"] else "mute"
        add(f'<tr class="{cls}"><td>{_scenario(w["outcome"], w["changed"])}</td><td class="num">{_e(w["revenue"])}</td>'
            f'<td class="num {tone}">{_e(w["shortfall"])}</td><td class="num{" bold" if w["worst"] and w["moves"] else ""}">{_e(w["share"])}</td>{more}'
            f'<td class="bar-cell"><div class="mag-track"><div class="mag-fill" style="width:{100 * w["against_worst"]:.1f}%"></div></div></td></tr>')
    add('</tbody></table><p class="table-note">Each figure is what revenue would be if that outcome happens, and says nothing of how likely it is. '
        'Shortfall is revenue as things stand less revenue in the outcome, over the period.'
        + (f' Share is of {_e(a["of"])} ({_e(a["of_value"])}).' if a["of"] else "")
        + ' Under each outcome are the inputs that take another value there.</p></section>')

    # 02 the company, and how the event reaches its revenue
    add('<section id="sec-reach"><h2 class="sec-h"><span class="sec-num">02</span> The company, and how the event reaches its revenue</h2>'
        '<div class="prose-block context-lead"><div class="prose-kicker">The company and the revenue</div>'
        + (f'<p>{_e(d["business"])}</p>' if d["business"] else "")
        + f'<p class="stream-def"><strong>The revenue this memo is about:</strong> {_e(d["stream"])}</p>'
        + (f'<p class="rel-line"><strong>Owners and counterparties:</strong> {_e(d["relationships"])}</p>' if d["relationships"] else "")
        + '</div><div class="mech-two-col"><div class="prose-block"><div class="prose-kicker">How the event reaches the revenue</div>'
        f'<p>{_e(d["dependency"])}</p></div><div class="prose-block"><div class="prose-kicker">When</div><p>{_e(d["timing"])}</p></div></div>')
    if d["establishes"]:
        add('<h3 class="sub-h">What establishes this</h3><ul class="claims">'
            + "".join(f'<li>{_e(x["claim"])}{refs([x["note"]])}</li>' for x in d["establishes"]) + '</ul>')
    add('</section>')

    # 03 the event
    def node(p, child=False, shown_above=None):
        meta = [x for x in (p.get("relation"), f"Decided · {p['window']}") if x]
        body = (f'<div class="prop-main"><div class="prop-head"><div class="prop-title">{_e(p["title"])}</div><div class="prop-meta">'
                + " <span class='sep'>·</span> ".join(f"<span>{_e(x)}</span>" for x in meta) + '</div></div>'
                f'<p class="prop-text">{_e(p["text"])}{"" if p["about"] else refs(p["notes"])}</p>'
                + (f'<p class="prop-desc">{_e(p["about"])}{refs(p["notes"])}</p>' if p["about"] else "")
                + (f'<div class="res-source"><span class="res-kicker">Shown by</span><span>{_e(p["shown_by"])}</span></div>'
                   if p["shown_by"] and p["shown_by"] != shown_above else "") + '</div>')
        kids = "".join(node(k, True, p["shown_by"]) for k in p["children"])
        return (f'<div class="prop-node {"child" if child else "root"}">{body}'
                + (f'<div class="prop-children">{kids}</div>' if kids else "") + '</div>')

    add(f'<section id="sec-event"><h2 class="sec-h"><span class="sec-num">03</span> {"The events" if len(d["events"]) > 1 else "The event"}, and the ways it can turn out</h2>'
        + "".join(node(p) for p in d["tree"]) + '</section>')

    # 04 how the estimate is built
    add('<section id="sec-model"><h2 class="sec-h"><span class="sec-num">04</span> How the estimate is built</h2><div class="formula-box">'
        + "".join(f'<div class="formula-row"><div class="formula-lbl">{_e(f["name"])}</div><div class="formula-readable">{_in_words(f["formula"], d["labels"])}</div>'
                  f'<div class="formula-code">{_e(f["formula"])}</div></div>' for f in d["formulas"]) + '</div>')
    if d["changing"]:
        add('<h3 class="sub-h">What changes with the outcome</h3><table class="data-table drivers"><thead><tr><th class="c-in">Input</th><th class="c-out">Outcome</th>'
            '<th class="num c-val">Value</th><th class="c-basis">Basis</th><th>Reasoning</th></tr></thead><tbody>')
        for f in d["changing"]:
            rows = [("As things stand", f)] + [(x["when"], x) for x in f["outcomes"]]
            for i, (when, v) in enumerate(rows):
                name = (f'<td class="drv-name" rowspan="{len(rows)}"><strong>{_e(f["label"])}</strong><div class="drv-sym">{_e(f["name"])}</div></td>' if i == 0 else "")
                add(f'<tr class="{"drv-base-row" if i == 0 else "drv-cond-row"}">{name}<td class="drv-cond">{_e(when)}</td>'
                    f'<td class="num bold">{amount(v)}</td><td>{_pill(v["kind"])}</td><td class="drv-note">{why(v)}</td></tr>')
        add('</tbody></table>')
    if d["constant"]:
        add('<h3 class="sub-h">What holds in every outcome</h3><table class="data-table drivers"><thead><tr><th class="c-in" style="width:26%">Input</th>'
            '<th class="num c-val" style="width:13%">Value</th><th class="c-basis">Basis</th><th>Reasoning</th></tr></thead><tbody>')
        for f in d["constant"]:
            add(f'<tr><td class="drv-name"><strong>{_e(f["label"])}</strong><div class="drv-sym">{_e(f["name"])}</div></td>'
                f'<td class="num bold">{amount(f)}</td><td>{_pill(f["kind"])}</td><td class="drv-note">{why(f)}</td></tr>')
        add('</tbody></table>')
    add('</section>')

    # the appendix
    add('<div class="appendix"><div class="appendix-banner">Appendix · what the memo rests on</div>')
    if d["findings"]:
        add('<section><h2 class="sec-h"><span class="sec-num">A</span> Findings recorded on the case</h2>'
            + "".join(f'<div class="finding-card"><div class="finding-kind">{_e(f["kind"])}</div><p>{_e(f["statement"])}{refs(f["notes"], f["computations"])}</p></div>'
                      for f in d["findings"]) + '</section>')
    if d["notes"]:
        add('<section id="sec-notes"><h2 class="sec-h"><span class="sec-num">B</span> The records cited</h2>')
        for n in d["notes"]:
            when = " · ".join(x for x in (n["published"] and f"Published {n['published']}", n["read"] and f"Read {n['read']}") if x)
            link = f'<a class="src-url" href="{_html.escape(n["url"], quote=True)}">{_e(n["url"])}</a>' if n["url"] else ""
            many = len(n["passages"]) > 1
            quoted = "".join(
                f'<div class="quote-item">{f"<span class=quote-sup>{n["n"]}{chr(97 + k)}</span>" if many else ""}'
                f'<blockquote>“{_e(q["text"])}”{"" if q["checked"] else " (not checked against the record)"}</blockquote></div>'
                for k, q in enumerate(n["passages"]))
            add(f'<div class="src-entry" id="note-{n["n"]}"><div class="src-num">[{n["n"]}]</div><div><div class="src-title">{_e(n["title"])}</div>'
                f'<div class="src-meta">{_e(when)}</div>{link}{f"<div class=quote-list>{quoted}</div>" if quoted else ""}</div></div>')
        add('</section>')
    if d["computations"]:
        add('<section><h2 class="sec-h"><span class="sec-num">C</span> The computations figures are read from</h2>')
        for k in d["computations"]:
            add(f'<div class="comp-card" id="comp-{k["n"]}"><div class="comp-head"><span class="comp-id">[C{k["n"]}]</span>'
                f'<span class="comp-title">{_e(k["name"])}</span></div>' + (f'<p class="comp-note">{_e(k["note"])}</p>' if k["note"] else ""))
            cols_, rows_ = k["columns"], k["rows"]
            cell = lambda x: _e("" if x is None else f"{x:,.4g}" if isinstance(x, float) and abs(x) < 1000 else f"{x:,.0f}" if isinstance(x, float) else x)  # noqa: E731
            if cols_ and len(rows_) == 1 and len(cols_) > 5:                     # one wide row: as pairs, so nothing is cut at the page's edge
                pairs = list(zip(cols_, rows_[0]))
                half = (len(pairs) + 1) // 2
                body = "".join(
                    "<tr>" + f'<td class="kv-k">{_e(pairs[i][0].replace("_", " "))}</td><td class="num">{cell(pairs[i][1])}</td>'
                    + (f'<td class="kv-k kv-split">{_e(pairs[i + half][0].replace("_", " "))}</td><td class="num">{cell(pairs[i + half][1])}</td>'
                       if i + half < len(pairs) else '<td class="kv-split"></td><td></td>') + "</tr>" for i in range(half))
                add(f'<div class="comp-table-wrap"><table class="data-table compact kv-table"><tbody>{body}</tbody></table></div>')
            elif cols_ and rows_:
                numeric = [all(isinstance(row[i], (int, float)) or row[i] is None for row in rows_) for i in range(len(cols_))]
                head = "".join(f'<th class="wrap-th{" num" if numeric[i] else ""}">{_e(col.replace("_", " "))}</th>' for i, col in enumerate(cols_))
                body = "".join("<tr>" + "".join(f'<td class="{"num" if numeric[i] else ""}">{cell(x)}</td>' for i, x in enumerate(row)) + "</tr>" for row in rows_)
                add(f'<div class="comp-table-wrap"><table class="data-table compact"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>'
                    + (f'<div class="comp-trunc">The first {len(rows_)} of {k["row_count"]} rows.</div>' if k["row_count"] and k["row_count"] > len(rows_) else "")
                    + '</div>')
            if k["sql"]:
                add(f'<div class="sql-kicker">The query</div><pre class="sql">{_e(k["sql"])}</pre>')
            add('</div>')
        add('</section>')
    add('<section><h2 class="sec-h"><span class="sec-num">D</span> The condition, as the store states it</h2>'
        '<p class="table-note">Revenue differs from revenue as things stand in any one of these, each written from its propositions’ texts.</p>'
        '<ul class="cond-list">' + "".join(f'<li><span class="clause-tag">Outcome {i}</span><span>{_e(x)}</span></li>' for i, x in enumerate(d["condition"], 1))
        + '</ul></section></div>')
    add(f'<div class="doc-foot"><span>{_e(d["case_id"])} · revision {_e(d["revision"])} · as of {_e(d["as_of"])}</span>'
        f'<span>{_e(d["change_note"][:160])}</span></div>')
    add('</div></body></html>')
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
    foot = ('<div style="width:100%;font-family:-apple-system,Helvetica,Arial,sans-serif;font-size:6.5pt;color:#64748B;'
            'padding:0 0.56in;display:flex;justify-content:space-between;">'
            f'<span>{_e(running)}</span><span>Page <span class="pageNumber"></span> of <span class="totalPages"></span></span></div>')
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            pg = browser.new_page()
            pg.set_content(page, wait_until="load")
            pg.pdf(path=str(path), format="Letter", print_background=True, display_header_footer=True,
                   header_template="<span></span>", footer_template=foot,
                   margin={"top": "0.5in", "bottom": "0.62in", "left": "0.56in", "right": "0.56in"})
        finally:
            browser.close()
    return path
