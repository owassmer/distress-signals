"""An event's outcomes, read from the event as stored. The outcomes are its two sides, 'yes' and 'no',
and all their ways. A case's versions name outcomes by key: those are its worlds, each the outcome as a
whole, and none lies within another. An outcome the case names no revenue for is open."""

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


def stated(event):
    """Whether the event is stated as a claim with outcomes (the current form)."""
    return bool(event.get("text") and event.get("kind"))


def path(event, key):
    """The outcome and the outcomes it lies within, outermost first: [node, ...]."""
    ix, out = index(event), []
    while key:
        out.append(ix[key]["node"])
        key = ix[key]["parent"]
    return out[::-1]


def label(event, key):
    """An outcome in a few words: the event's title for the Yes side, its title negated for the No side, and
    a way's own title."""
    if key == "yes":
        return event.get("title") or ""
    if key == "no":
        return f"Not: {event.get('title') or ''}"
    return index(event)[key]["node"]["title"]


def sentence(event, key):
    """What would make an outcome true, in the event's words: the claim for the Yes side, the claim turning
    out false for the No side, and a way's own text."""
    if key == "yes":
        return event.get("text") or ""
    if key == "no":
        return f"This turns out false: {event.get('text') or ''}"
    return index(event)[key]["node"]["text"]


def named(case):
    """The outcome keys a case's versions name, in the order first named."""
    return list(dict.fromkeys(a["outcome"] for a in case["affected"] if a.get("outcome")))


def unknown(event, case):
    """The outcome keys a case's versions name that the event doesn't have."""
    have = index(event)
    return [k for k in named(case) if k not in have]


def within(event, key):
    """The keys of every way within an outcome, at any depth."""
    ix = index(event)
    return [k for k in ix if any(n["key"] == key for n in path(event, k)[:-1])]


def overlapping(event, case):
    """[(inner, outer)] for each pair of outcomes a case names where one lies within the other."""
    names = [k for k in named(case) if k in index(event)]
    return [(a, b) for b in names for a in within(event, b) if a in names]


def open_outcomes(event, case):
    """The outcomes a case names no revenue for: the largest outcomes that it does not name and that hold no
    way it names."""
    names, ix = set(named(case)), index(event)
    kids = {}
    for k, v in ix.items():
        kids.setdefault(v["parent"], []).append(k)

    def touched(key):
        return key in names or any(touched(c) for c in kids.get(key, []))

    out = []

    def walk(key):
        if key in names:
            return
        if not touched(key):
            out.append(key)
            return
        for c in kids.get(key, []):
            walk(c)

    for side in SIDES:
        walk(side)
    return out


def whole(event, case):
    """[{"outcome", "ways"}] for each outcome a case names that has ways: the case's figure is for the outcome
    as a whole, and each of those ways has a figure once the case names it."""
    ix = index(event)
    return [{"outcome": k, "ways": [w["key"] for w in ix[k]["node"].get("ways") or []]}
            for k in named(case) if k in ix and ix[k]["node"].get("ways")]


def condition(event, case):
    """What has to happen for the case's revenue to be affected: the outcomes its versions name, in the
    event's words. A case saved before events carried outcomes reads with the condition it stated."""
    if not stated(event) or not named(case):
        return case.get("condition") or ""
    return " Or: ".join(sentence(event, k) for k in named(case) if k in index(event))
