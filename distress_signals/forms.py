"""A saved case as it reads now. Every read of a saved case goes through here (the analyst's lookup, a
refresh, the memo, HubSpot, the run page and the exports). An active case's figures are computed again from
what it states and from the propositions as they now stand. A withdrawn case reads as its last revision held
it, with the propositions as they stood then."""

import copy

from . import exposure


def label(name, inp, currency):
    """What an input is, in words. An input saved before inputs had labels reads by the nearest thing it states:
    its unit where that says more than the currency, else the column of the result it is read from, else its
    name."""
    if inp.get("label"):
        return inp["label"]
    unit = inp.get("unit") or ""
    if unit and unit != currency and not unit.startswith(currency + " "):
        words = unit
    else:
        words = ((inp.get("result") or {}).get("column") or name).replace("_", " ")
    return words[:1].upper() + words[1:]


def labelled(case):
    """The case with a label on every input."""
    if all(inp.get("label") for inp in case["inputs"].values()):
        return case
    case = copy.deepcopy(case)
    for name, inp in case["inputs"].items():
        inp["label"] = label(name, inp, case["currency"])
    return case


def snapshot(snap, props=None):
    """A revision's snapshot as it reads now: company, case, the propositions it reads, results, warnings.
    `props` is the saved propositions as they now stand; without them, the propositions as the revision held
    them."""
    props = props if props is not None else snap["propositions"]
    results, warnings = exposure.compute(snap["case"], props)
    return {"company": snap["company"], "case": labelled(snap["case"]), "propositions": props, "results": results,
            "warnings": warnings}


def read(row, snap, props):
    """A case's latest revision as the case reads now. `row` is the case's row, `props` the saved propositions
    as they now stand."""
    return snapshot(snap, props if row["status"] == "active" else None)
