"""A saved case as it reads now. Every read of a saved case goes through here (the analyst's lookup, a
refresh, the memo, HubSpot, the run page and the exports). An active case's figures are computed again from
what it states and from the propositions as they now stand. A withdrawn case reads as its last revision held
it, with the propositions as they stood then."""

from . import exposure


def snapshot(snap, props=None):
    """A revision's snapshot as it reads now: company, case, the propositions it reads, results, warnings.
    `props` is the saved propositions as they now stand; without them, the propositions as the revision held
    them."""
    props = props if props is not None else snap["propositions"]
    results, warnings = exposure.compute(snap["case"], props)
    return {"company": snap["company"], "case": snap["case"], "propositions": props, "results": results,
            "warnings": warnings}


def read(row, snap, props):
    """A case's latest revision as the case reads now. `row` is the case's row, `props` the saved propositions
    as they now stand."""
    return snapshot(snap, props if row["status"] == "active" else None)
