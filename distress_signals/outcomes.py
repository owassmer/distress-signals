"""An event's outcomes, read from the event as stored. The outcomes are its two sides, 'yes' and 'no',
and all their ways. A case gives its inputs' values by outcome, each outcome by its key; an outcome's
values hold in its ways."""

from .models import SIDES


def tree(event):
    """[(key, node, parent key or None, depth)] for every outcome, each side followed by its ways."""
    out = []

    def walk(ways, parent, depth):
        for w in ways or []:
            out.append((w["key"], w, parent, depth))
            walk(w.get("ways"), w["key"], depth + 1)

    for side in SIDES:
        out.append((side, {"key": side, "title": label(event, side), "text": sentence(event, side),
                           "ways": event.get(f"{side}_ways") or []}, None, 0))
        walk(event.get(f"{side}_ways"), side, 1)
    return out


def index(event):
    """{key: {"node", "parent", "depth"}} for every outcome of the event."""
    return {k: {"node": n, "parent": p, "depth": d} for k, n, p, d in tree(event)}


def within(event):
    """{key: the key of the outcome it lies within, or None for a side}."""
    return {k: p for k, _, p, _ in tree(event)}


def label(event, key):
    """An outcome in a few words: the event's title for the Yes side, its title negated for the No side, and
    a way's own title."""
    if key == "yes":
        return event["title"]
    if key == "no":
        return f"Not: {event['title']}"
    return index(event)[key]["node"]["title"]


def sentence(event, key):
    """What would make an outcome true, in the event's words: the claim for the Yes side, the claim turning
    out false for the No side, and a way's own text."""
    if key == "yes":
        return event["text"]
    if key == "no":
        return f"This turns out false: {event['text']}"
    return index(event)[key]["node"]["text"]


def named(event, case):
    """The outcomes a case gives values for, in the event's order."""
    return [k for k, _, _, _ in tree(event) if k in case["by_outcome"]]


def unknown(event, case):
    """The outcome keys a case gives values for that the event doesn't have."""
    have = index(event)
    return [k for k in case["by_outcome"] if k not in have]


def condition(event, case, results):
    """What has to happen for the case's revenue to be affected: the outcomes it gives values for, where
    the revenue differs from the baseline's, in the event's words."""
    moved = {r["outcome"] for r in results["outcomes"] if r["shortfall"]}
    return " Or: ".join(sentence(event, k) for k in named(event, case) if k in moved)
