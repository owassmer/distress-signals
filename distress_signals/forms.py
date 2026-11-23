"""A saved case as it reads now. Every read of a saved case goes through here (the analyst's lookup, a
refresh, the memo, HubSpot, the run page and the exports): its figures are computed again from what it
states and from the propositions as they now stand."""

from . import exposure


def snapshot(snap, props=None):
    """A revision's snapshot as it reads now: company, case, the propositions it reads, results, warnings.
    `props` is the saved propositions as they now stand; without them, the propositions as the revision held
    them."""
    props = props if props is not None else snap["propositions"]
    results, warnings = exposure.compute(snap["case"], props)
    return {"company": snap["company"], "case": snap["case"], "propositions": props, "results": results,
            "warnings": warnings}
