"""A saved case as it reads now. Every read of a saved case goes through here (the analyst's lookup, a
refresh, the memo, HubSpot, the run page and the exports): its results are computed again from what it
states and from its event as the event now stands."""

from . import exposure, outcomes
from .models import FORMULAS


def results(event, case):
    """(results, warnings) for a case on its event: the one calculation, on the stored forms."""
    return exposure.compute({k: v["value"] for k, v in case["inputs"].items()},
                            {k: case.get(k) for k in FORMULAS},
                            {o: {k: g["value"] for k, g in values.items()} for o, values in case["by_outcome"].items()},
                            case.get("denominator"), outcomes.within(event) if event else None)


def snapshot(snap, event=None):
    """A revision's snapshot as it reads now: company, event, case, results, warnings. `event` is the event
    as it now stands, where the caller has it; else the event as the revision held it."""
    event = event or {k: v for k, v in snap["event"].items() if k != "id"}
    res, warnings = results(event, snap["case"])
    return {"company": snap["company"], "event": {"id": snap["event"].get("id"), **event}, "case": snap["case"],
            "results": res, "warnings": warnings}
