"""The worlds of a case (docs/model.md section 4). Propositions are held flat, by key: each with the key of
the proposition it is a way of (`parent`) and the side it divides, the keys it implies and implies not, and
its level fields. A case reads its events, every proposition its given values name, and with each way so
named the ways beside it and the ways it lies within, up to its event. Its worlds are the combinations of
those propositions being true or false that the links allow."""

import itertools


class Unstated(ValueError):
    """What is stated does not determine a figure: a key the store does not hold, a condition that holds in no
    world, or an input two given values set where neither is the more specific."""


def flatten(roots):
    """{key: proposition} from propositions given in full, each way with its `parent` and `side`."""
    out = {}

    def walk(p, parent, order):
        flat = {k: v for k, v in p.items() if k != "ways"} | {"parent": parent, "order": order}
        flat["side"] = (p.get("side") or "yes") if parent else None
        out[p["key"]] = flat
        for i, w in enumerate(p.get("ways") or []):
            walk(w, p["key"], i)

    for r in roots:
        walk(r, None, 0)
    return out


def nest(props, key):
    """A proposition in full, its ways nested in it, from the flat form, as a statement gives it: the side is
    named on a way of a proposition that is not itself a way, and the ways of a way divide it."""
    p = {k: v for k, v in props[key].items() if k not in ("parent", "order") and v not in (None, [], "")}
    if props[key].get("parent") and props[props[key]["parent"]].get("parent"):
        p.pop("side", None)
    ways = sorted((w for w in props.values() if w.get("parent") == key), key=lambda w: w["order"])
    return p | {"ways": [nest(props, w["key"]) for w in ways]}


def root(props, key):
    while props[key].get("parent"):
        key = props[key]["parent"]
    return key


def beside(props, key):
    """The ways beside a way, itself included, in their stated order."""
    p = props[key]
    if not p.get("parent"):
        return []
    return [w["key"] for w in sorted((w for w in props.values() if w.get("parent") == p["parent"] and w["side"] == p["side"]),
                                     key=lambda w: w["order"])]


# ---------- level fields ----------

def interval(p):
    """(low, low included, high, high included) for a claim about a level; None for one that occurs."""
    c, v = p.get("comparison") or "occurs", p.get("value")
    inf = float("inf")
    if c == "occurs":
        return None
    if c == "==":
        return (v, True, v, True)
    if c in (">", ">="):
        hi, hi_in = (p["upper_value"], p["upper_comparison"] == "<=") if p.get("upper_comparison") else (inf, False)
        return (v, c == ">=", hi, hi_in)
    return (-inf, False, v, c == "<=")


def comparable(a, b):
    """Two claims about one level: one metric in one unit and one resolution source, decided the same way over
    the same window."""
    same = lambda k: a.get(k) == b.get(k)  # noqa: E731
    return bool(interval(a) and interval(b) and a.get("metric") and same("metric") and same("unit")
                and same("resolution_source") and same("kind") and str(a.get("window_end")) == str(b.get("window_end"))
                and str(a.get("window_start")) == str(b.get("window_start")))


def _within(x, y):
    (lo, lo_in, hi, hi_in), (LO, LO_in, HI, HI_in) = x, y
    return (lo > LO or (lo == LO and (LO_in or not lo_in))) and (hi < HI or (hi == HI and (HI_in or not hi_in)))


def _apart(x, y):
    (lo, lo_in, hi, hi_in), (LO, LO_in, HI, HI_in) = x, y
    return hi < LO or (hi == LO and not (hi_in and LO_in)) or HI < lo or (HI == lo and not (HI_in and lo_in))


def read_fields(props, keys):
    """What the level fields of some propositions say of one another: [(a, 'implies' | 'excludes', b)]. One
    whose values all lie among another's implies it. Two with no value in common exclude one another where the
    level is read once, on a date or at a decision; a level reached at any time through a window can reach
    both."""
    out = []
    for a, b in itertools.permutations(keys, 2):
        if comparable(props[a], props[b]):
            if _within(interval(props[a]), interval(props[b])):
                out.append((a, "implies", b))
            elif a < b and props[a]["kind"] != "hitting" and _apart(interval(props[a]), interval(props[b])):
                out.append((a, "excludes", b))
    return out


def outside(props, key):
    """For a way whose level fields are compared with those of what it divides: whether they put it outside.
    A way of the Yes side lies within the proposition's values; a way of the No side has none of them."""
    p = props[key]
    parent = props.get(p.get("parent"))
    if not parent or not comparable(p, parent):
        return False
    if p["side"] == "no":
        return parent["kind"] != "hitting" and not _apart(interval(p), interval(parent))
    return not _within(interval(p), interval(parent))


def overlapping(props, key):
    """[(a, b)] among the ways beside a way whose level fields let both be true."""
    ways = [w for w in beside(props, key)]
    return [(a, b) for a, b in itertools.combinations(ways, 2)
            if comparable(props[a], props[b]) and not _apart(interval(props[a]), interval(props[b]))]


def gaps(props, key):
    """The values the ways beside a way leave in none of them, each as (low, low included, high, high
    included). They are looked for among the values of what the ways divide, where its level fields are
    compared with theirs: the proposition's own values on its Yes side, the rest on its No side. Otherwise
    between the lowest of the ways' values and the highest."""
    ways = [w for w in beside(props, key) if interval(props[w])]
    if len(ways) < 2 or not all(comparable(props[ways[0]], props[w]) for w in ways[1:]):
        return []
    spans = sorted((interval(props[w]) for w in ways), key=lambda s: (s[0], not s[1]))
    parent, side, inf = props[props[key]["parent"]], props[key]["side"], float("inf")
    if comparable(props[ways[0]], parent) and not (side == "no" and parent["kind"] == "hitting"):
        lo, lo_in, hi, hi_in = interval(parent)
        whole = [(lo, lo_in, hi, hi_in)] if side == "yes" else [(-inf, False, lo, not lo_in), (hi, not hi_in, inf, False)]
    else:
        top = max(spans, key=lambda s: (s[2], s[3]))
        whole = [(spans[0][0], spans[0][1], top[2], top[3])]
    return [piece for stretch in whole for piece in _left(stretch, spans)]


def _left(stretch, spans):
    """What a stretch of values keeps once the spans, in the order of their lower ends, are taken out of it."""
    (cur, cur_in, hi, hi_in), out = stretch, []
    for s_lo, s_in, s_hi, s_hi_in in spans:
        if s_lo > cur or (s_lo == cur and cur_in and not s_in):
            out.append((cur, cur_in, s_lo, not s_in))
        if s_hi > cur or (s_hi == cur and cur_in and s_hi_in):
            cur, cur_in = s_hi, not s_hi_in
    out.append((cur, cur_in, hi, hi_in))
    kept = []
    for a, a_in, b, b_in in out:
        if b > hi or (b == hi and b_in and not hi_in):
            b, b_in = hi, hi_in
        if a < b or (a == b and a_in and b_in):
            kept.append((a, a_in, b, b_in))
    return kept


def values_in(span):
    """A stretch of values in the comparisons a proposition's fields use: '>= 0.4 and < 0.5'."""
    lo, lo_in, hi, hi_in = span
    if lo == hi:
        return f"== {lo:g}"
    parts = ([f"{'>=' if lo_in else '>'} {lo:g}"] if lo != float("-inf") else []) \
        + ([f"{'<=' if hi_in else '<'} {hi:g}"] if hi != float("inf") else [])
    return " and ".join(parts)


# ---------- worlds ----------

def reads(props, events, given_values):
    """The propositions a case reads, its events first, then the others in the order they come to be read."""
    out = list(events)
    named = [k for g in given_values for k in [*g.get("given", []), *g.get("given_not", [])]]
    missing = [k for k in [*events, *named] if k not in props]
    if missing:
        raise Unstated(f"no saved proposition {', '.join(repr(k) for k in dict.fromkeys(missing))}. Give it in full in "
                       "propositions, or name one that is saved.")
    for k in named:
        chain = []
        while props[k].get("parent"):          # with a way: the ways beside it, and so on up to its event
            chain.append(beside(props, k))
            k = props[k]["parent"]
        for group in reversed(chain):
            out += group
        out.append(k)
    return list(dict.fromkeys(out))


def _closure(props, keys):
    out, todo = [], list(keys)
    while todo:
        k = todo.pop(0)
        if k in out or k not in props:
            continue
        out.append(k)
        p = props[k]
        todo += beside(props, k) + ([p["parent"]] if p.get("parent") else []) + list(p.get("implies") or []) \
            + list(p.get("implies_not") or [])
    return out


def _states(props, node, within, true):
    """Every way the part of a proposition's tree that lies in `within` can stand, with the proposition true or
    false: [{key: bool}]."""
    kids = [w for w in sorted(props.values(), key=lambda w: w["order"]) if w.get("parent") == node and w["key"] in within]

    def all_false(k):
        out = {k: False}
        for w in props.values():
            if w.get("parent") == k and w["key"] in within:
                out |= all_false(w["key"])
        return out

    side = "yes" if true else "no"
    group = [w["key"] for w in kids if w["side"] == side]
    rest = {}
    for w in kids:
        if w["side"] != side:
            rest |= all_false(w["key"])
    if props[node].get("parent") and not true:
        return [all_false(node)]
    if not group:
        return [{node: true} | rest]
    out = []
    for chosen in group:
        for inner in _states(props, chosen, within, True):
            state = {node: true} | rest | inner
            for other in group:
                if other != chosen:
                    state |= all_false(other)
            out.append(state)
    return out


def worlds(props, read):
    """The case's worlds: [{key: bool}] over the propositions it reads, each combination the links allow."""
    within = _closure(props, read)
    roots = [k for k in within if not props[k].get("parent")]
    per_root = [[s for true in (True, False) for s in _states(props, r, within, true)] for r in roots]
    fields = read_fields(props, within)
    out, seen = [], set()
    for combo in itertools.product(*per_root):
        t = {}
        for s in combo:
            t |= s
        ok = all(not t[k] or (all(t[b] for b in props[k].get("implies") or [] if b in t)
                              and not any(t[b] for b in props[k].get("implies_not") or [] if b in t)) for k in t)
        ok = ok and all(not (t[a] and ((rel == "implies" and not t[b]) or (rel == "excludes" and t[b]))) for a, rel, b in fields)
        if ok:
            w = tuple(t[k] for k in read)
            if w not in seen:
                seen.add(w)
                out.append(dict(zip(read, w)))
    return out


def describe(props, events, read, all_worlds, world):
    """A world in the propositions' words: which are true in it, at their most particular, and which are false.
    The case's events come first and are always named. Each other proposition is named where, with those
    before it as they stand in this world, it could still have stood either way; so no two worlds read alike."""
    roots = list(dict.fromkeys(root(props, k) for k in read))
    true, false, titles, texts, before = [], [], [], [], []
    for r in roots:
        mine = [k for k in read if root(props, k) == r]
        deepest = [k for k in mine if world[k] and not any(world[c] for c in mine if props[c].get("parent") == k)]
        alike = [w for w in all_worlds if all(w[k] == world[k] for k in before)]
        before += mine
        if r not in events and len({tuple(w[k] for k in mine) for w in alike}) < 2:
            continue
        if deepest:
            true += deepest
            titles += [props[k]["title"] for k in deepest]
            texts += [props[k]["text"] for k in deepest]
        else:
            false.append(r)
            titles.append(f"Not: {props[r]['title']}")
            texts.append(f"This turns out false: {props[r]['text']}")
    return {"true": true, "false": false}, "; ".join(titles), " And: ".join(texts)


def values(all_worlds, inputs, given_values, labels):
    """Each input's value in each world: [{input: value}]. `labels` names each world, for a refusal. An input takes the value of the one given value that holds in the world, sets it, and has its worlds strictly
    within those of every other that holds there and sets it; an input none sets stands at its own value."""
    holds = []
    for i, g in enumerate(given_values):
        where = frozenset(n for n, w in enumerate(all_worlds)
                          if all(w[k] for k in g.get("given", [])) and not any(w[k] for k in g.get("given_not", [])))
        if not where:
            raise Unstated(f"given_values[{i}]: no world has {_condition(g)}. The links between these propositions leave "
                           "that combination out.")
        holds.append(where)
    out = []
    for n, w in enumerate(all_worlds):
        row = dict(inputs)
        for name in inputs:
            apply = [i for i, g in enumerate(given_values) if n in holds[i] and name in g["values"]]
            best = [i for i in apply if all(holds[i] < holds[j] for j in apply if j != i)]
            if apply and len(best) != 1:
                raise Unstated(f"In the world \"{labels[n]}\", {len(apply)} given values set {name!r} ("
                               + "; ".join(f"given_values[{i}], {_condition(given_values[i])}" for i in apply)
                               + ") and none has its worlds strictly within those of the others. State the value "
                               f"{name!r} takes given all of what holds there, or link the propositions so that one "
                               "lies within another.")
            if apply:
                row[name] = given_values[best[0]]["values"][name]
        out.append(row)
    return out


def _condition(g):
    return " and ".join([*g.get("given", []), *(f"not {k}" for k in g.get("given_not", []))])
