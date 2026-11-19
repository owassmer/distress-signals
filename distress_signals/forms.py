"""Saved research in its current form. Every read of a saved case or event goes through here (the
analyst's lookup, a refresh, the memos, the queue and the exports), so a case saved under an
earlier form reads as the current one: event outcomes, probabilities and probability-weighted figures
are gone, scenarios become a baseline and affected versions, and the results are computed again."""

import copy
import re

from . import exposure

# Text written under the earlier form that states a forecast: a probability, odds, or a
# probability-weighted figure. Only cases saved under that form are read through it.
FORECAST = re.compile(r"(?i)probabilit|\bodds\b|\bexpected (loss|lost|value|figure|shortfall)|"
                      r"\b\d{1,3}(\.\d+)?% (chance|likel)|\bchance of (a |an )?(lapse|shutdown)")
PROSE = [("dependency",), ("condition",), ("timing",), ("next_action",), ("case", "why_matters"), ("case", "why_now"),
         ("case", "priority_reason"), ("outreach", "draft"), ("baseline", "description")]


def _scrub(text):
    """The text without its sentences that state a forecast, and how many were removed."""
    if not text:
        return text, 0
    parts = re.split(r"(?<=[.!?])\s+", text)
    kept = [x for x in parts if not FORECAST.search(x)]
    return " ".join(kept), len(parts) - len(kept)


def note(text):
    """A revision note as it reads now: the earlier form's automatic notes listed outcome changes and their
    probabilities."""
    if (text or "").startswith("Recomputed after event"):
        return "Recomputed after the shared event was revised"
    return text

EVENT_FIELDS = ("key", "title", "description", "date", "timing", "evidence")
CASE_FIELDS = ("stream_key", "stream", "condition", "dependency", "timing", "period", "currency", "inputs", "baseline",
               "affected", "denominator", "case", "buyer", "outreach", "evidence", "next_action")
AFFECTED_FIELDS = ("name", "description", "timeline", "revenue", "deferred_past_period", "contribution", "cash_delayed")


def event(data):
    """The event's current form."""
    d = copy.deepcopy(data or {})
    if "description" in d and "outcomes" not in d:
        return {k: d.get(k) for k in EVENT_FIELDS if k in d} | {"evidence": d.get("evidence") or []}
    dates = d.get("dates") or {}
    timing = ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in dates.items() if v) or None
    return {"key": d.get("key"), "title": d.get("title"),
            "description": _scrub(d.get("statement") or d.get("title") or "")[0],
            "date": dates.get("decision") or dates.get("effective") or dates.get("window_start"),
            "timing": timing, "evidence": d.get("evidence") or []}


def _results(case):
    return exposure.compute({k: v["value"] for k, v in case["inputs"].items()}, case["baseline"], case["affected"],
                            case.get("denominator"))


def case(data, old_event=None):
    """(the case's current form, notes on what changed from its saved form)."""
    c, notes = copy.deepcopy(data), []
    for inp in c["inputs"].values():
        if inp.get("source") and inp.get("assumption"):
            inp["note"] = ((inp.get("note") or "") + " " + inp.pop("assumption")).strip()
    if "affected" not in c:
        removed = 0
        for path in PROSE:
            holder = c
            for k in path[:-1]:
                holder = holder.get(k) or {}
            if isinstance(holder.get(path[-1]), str):
                holder[path[-1]], n = _scrub(holder[path[-1]])
                removed += n
        for inp in c["inputs"].values():
            for k in ("assumption", "note"):
                if inp.get(k):
                    inp[k], n = _scrub(inp[k])
                    removed += n
        if removed:
            notes.append(f"Removed {removed} sentence(s) about a figure this tool no longer computes")
        scenarios = c.pop("scenarios")
        base = next((s for s in scenarios if s.get("outcome") == "baseline"), scenarios[0])
        c["baseline"] = {"description": base.get("description") or "", "revenue": base["revenue"],
                         "contribution": base.get("contribution")}
        situations = {o["name"]: o.get("description") for o in (old_event or {}).get("outcomes", [])}
        c["affected"] = []
        for s in scenarios:
            if s is base:
                continue
            c["affected"].append({k: s.get(k) for k in AFFECTED_FIELDS} | {
                "description": _scrub(s.get("description") or "")[0], "timeline": _scrub(s.get("timeline") or "")[0],
                "_situation": _scrub(situations.get(s.get("outcome")) or "")[0] or None})
            if s.get("cash_shifted"):
                notes.append(f"{s['name']}: cash_shifted is retired; state revenue made up within the period in revenue, "
                             "revenue recognized later in deferred_past_period, and late collections in cash_delayed")
        try:
            rows = {r["name"]: r for r in _results(c)[0]["affected"]}
        except ValueError:
            rows = {}
        kept = [a for a in c["affected"] if rows.get(a["name"], {}).get("shortfall", 1) or a.get("cash_delayed")]
        if len(kept) < len(c["affected"]):
            dropped = sorted({a["name"] for a in c["affected"]} - {a["name"] for a in kept})
            notes.append(f"Removed versions that left revenue unchanged: {', '.join(dropped)}")
        c["affected"] = kept or c["affected"][:1]
        worst = max(c["affected"], key=lambda a: rows.get(a["name"], {}).get("shortfall", 0))
        c.setdefault("condition", worst.pop("_situation", None) or worst["description"])
        c.setdefault("timing", worst["timeline"])
        for a in c["affected"]:
            a.pop("_situation", None)
        notes.append("Saved under an earlier form: condition and timing are carried from its worst version; check them")
    if isinstance(c.get("outreach"), dict) and "question" in c["outreach"]:
        c["outreach"] = {"draft": c["outreach"]["draft"]}
    c.setdefault("condition", "")
    c.setdefault("timing", "")
    return {k: c.get(k) for k in CASE_FIELDS}, notes


def snapshot(snap):
    """A revision's snapshot in current form: company, event, case, results, warnings, notes."""
    c, notes = case(snap["case"], snap.get("event"))
    results, warnings = _results(c)
    return {"company": snap["company"], "event": {"id": snap["event"].get("id"), **event(snap["event"])},
            "case": c, "results": results, "warnings": warnings, "notes": notes}
