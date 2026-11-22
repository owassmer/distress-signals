"""Saved research in its current form. Every read of a saved case or event goes through here (the
analyst's lookup, a refresh, the memo, HubSpot, the run page and the exports), and the results are
computed again on each read. A case saved before events carried outcomes reads with what it holds, the
condition it stated, and a note that says what the current form asks for."""

import copy

from . import exposure, outcomes

EVENT_FIELDS = ("key", "title", "text", "description", "kind", "window_start", "window_end", "timing",
                "resolution_source", "yes_ways", "no_ways", "evidence")
CASE_FIELDS = ("stream_key", "stream", "dependency", "timing", "period", "currency", "inputs", "baseline",
               "affected", "denominator", "case", "buyer", "outreach", "evidence", "next_action")
EARLIER = ("Saved before events carried outcomes. Give the event in full, with its text (the claim that will turn "
           "out true or false), kind, window_end, resolution_source and the ways of each side, and give each "
           "affected version the outcome it holds under.")


def event(data):
    """The event's current form. An event saved before events carried outcomes keeps its title, description,
    timing and evidence, and its date as window_end."""
    d = copy.deepcopy(data or {})
    if not outcomes.stated(d):
        return {k: v for k, v in (("key", d.get("key")), ("title", d.get("title")), ("description", d.get("description")),
                                  ("window_end", d.get("window_end") or d.get("date")), ("timing", d.get("timing")))
                if v is not None} | {"evidence": d.get("evidence") or []}
    out = {k: d[k] for k in EVENT_FIELDS if d.get(k) is not None} | {"evidence": d.get("evidence") or []}
    for side in ("yes_ways", "no_ways"):
        out[side] = [_way(w) for w in out.get(side) or []]
    return out


def _way(w):
    """A way with the fields it states: its own window and resolution source where it has them, and its ways."""
    return {k: v for k, v in w.items() if k != "ways" and v is not None} | {"ways": [_way(x) for x in w.get("ways") or []]}


def _results(case):
    return exposure.compute({k: v["value"] for k, v in case["inputs"].items()}, case["baseline"], case["affected"],
                            case.get("denominator"))


def case(data):
    """(the case's current form, notes on what the current form asks of it)."""
    c, notes = copy.deepcopy(data), []
    out = {k: c.get(k) for k in CASE_FIELDS}
    if any(not a.get("outcome") for a in c["affected"]):
        said = f" The condition it stated: {c['condition']}" if c.get("condition") else ""
        notes.append(EARLIER + said)
        out["condition"] = c.get("condition") or ""
    return out, notes


def snapshot(snap):
    """A revision's snapshot in current form: company, event, case, results, warnings, notes."""
    c, notes = case(snap["case"])
    results, warnings = _results(c)
    return {"company": snap["company"], "event": {"id": snap["event"].get("id"), **event(snap["event"])},
            "case": c, "results": results, "warnings": warnings, "notes": notes}
