"""Exposure arithmetic: the revenue at stake in each world of a case (docs/model.md section 4). The
researcher states one revenue formula, the inputs at their values in the baseline, and the values the
inputs take given outcomes; this computes every figure. A formula is an expression over input names:
numbers, + - * /, parentheses, min() and max(). Nothing else is evaluated.

compute() is the one calculation, used by calculate_exposure, save_case and every read of a saved case.

The baseline is the formula at the inputs' values. A world's revenue is the formula at the inputs' values
there (worlds.py). Revenue is what the company recognizes within the period, work or sales made up within
the period included. The shortfall is the baseline's revenue less the world's. Revenue deferred past the
period is recognized later; lost revenue is the shortfall less that deferral, and is reported only when
the deferral's formula is given, because otherwise later recovery is unknown. Cash delayed is a timing
effect on collections, reported apart."""

import ast
import operator

from . import worlds

_BIN = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv}
_FUNCS = {"min": min, "max": max}


class FormulaError(ValueError):
    pass


def evaluate(formula, values):
    try:
        tree = ast.parse(formula, mode="eval")
    except SyntaxError:
        raise FormulaError(f"can't parse formula {formula!r}")

    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) and not isinstance(n.value, bool):
            return float(n.value)
        if isinstance(n, ast.Name):
            if n.id not in values:
                raise FormulaError(f"unknown input {n.id!r} in {formula!r}")
            return float(values[n.id])
        if isinstance(n, ast.BinOp) and type(n.op) in _BIN:
            a, b = ev(n.left), ev(n.right)
            if isinstance(n.op, ast.Div) and b == 0:
                raise FormulaError(f"division by zero in {formula!r}")
            return _BIN[type(n.op)](a, b)
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.USub, ast.UAdd)):
            v = ev(n.operand)
            return -v if isinstance(n.op, ast.USub) else v
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in _FUNCS
                and n.args and not n.keywords):
            return _FUNCS[n.func.id](ev(a) for a in n.args)
        raise FormulaError(f"{formula!r}: only numbers, input names, + - * /, parentheses, min() and max()")

    return ev(tree)


def basis_kind(figure):
    """Which parts of a basis a figure has: read from a record, read and carried by an assumption, or assumed."""
    read = bool(figure.get("source") or figure.get("result"))
    return ("read, carried by an assumption" if figure.get("assumption") else "read") if read else "assumed"


def sensitivity(case, every, labels, inputs, given, shortfalls):
    """How far the shortfall moves with each stated figure, one figure at a time: each input's own value and each
    given value is moved up and down by a tenth of the largest value its input takes anywhere in the case, the
    worlds' shortfalls are computed again, and the largest change in any world is kept, with that world. The
    figures come back largest change first: [{figure, value, basis, moved_by, shortfall_moves_by, in_world}].
    Figures that move it alike come in the order of their names, which is the same however the case was written
    down: a case computes alike as stated and as read back from the store, where names come in another order."""
    largest = {k: max([abs(v)] + [abs(g["values"][k]) for g in given if k in g["values"]]) for k, v in inputs.items()}
    figures = [(f"inputs.{k}", None, k, case["inputs"][k]) for k in sorted(inputs)]
    figures += [(f"given_values[{i}].values.{k}", i, k, case["given_values"][i]["values"][k])
                for i, g in enumerate(given) for k in sorted(g["values"])]
    out = []
    for at, i, k, stated in figures:
        step, value = 0.1 * largest[k], inputs[k] if i is None else given[i]["values"][k]
        if not step:
            continue
        change, where = 0.0, None
        for moved in (value + step, value - step):
            ins = inputs | {k: moved} if i is None else inputs
            giv = given if i is None else [g if j != i else g | {"values": g["values"] | {k: moved}} for j, g in enumerate(given)]
            try:
                base = evaluate(case["revenue"], ins)
                again = [base - evaluate(case["revenue"], at_w) for at_w in worlds.values(every, ins, giv, labels)]
            except (FormulaError, worlds.Unstated):
                continue
            for n, (was, now) in enumerate(zip(shortfalls, again)):
                if abs(now - was) > change + 1e-9 * max(1.0, abs(was)):      # the first world, among those it moves alike
                    change, where = abs(now - was), n
        out.append({"figure": at, "value": value, "basis": basis_kind(stated), "moved_by": step,
                    "shortfall_moves_by": change, "in_world": labels[where] if where is not None else None})
    top = max((r["shortfall_moves_by"] for r in out), default=0) or 1.0
    return sorted(out, key=lambda r: -round(r["shortfall_moves_by"] / top, 9))   # a stable sort: alike, in the order above


def compute(case, props):
    """case: events, the formulas, inputs {name: {value, ...}}, given_values, denominator. props: the saved
    propositions, flat (worlds.flatten). Returns (results, warnings); raises FormulaError for a formula that
    can't be computed and worlds.Unstated where the statements don't determine a figure."""
    inputs = {k: v["value"] for k, v in case["inputs"].items()}
    given = [{"given": g.get("given") or [], "given_not": g.get("given_not") or [],
              "values": {k: v["value"] for k, v in g["values"].items()}} for g in case["given_values"]]
    read = worlds.reads(props, case["events"], given)
    every = worlds.worlds(props, read)
    told = [worlds.describe(props, case["events"], read, every, w) for w in every]
    by_world = worlds.values(every, inputs, given, [label for _, label, _ in told])
    warnings, den = [], None
    if case.get("denominator"):
        den = evaluate(case["denominator"]["formula"], inputs)
        if den <= 0:
            raise FormulaError(f"denominator {case['denominator']['formula']!r} is {den}, not positive")
    else:
        warnings.append("No denominator, so the shortfall isn't expressed as a share.")
    opt = lambda k, at: evaluate(case[k], at) if case.get(k) else None  # noqa: E731
    base_rev, base_con = evaluate(case["revenue"], inputs), opt("contribution", inputs)
    rows = []
    for (which, label, text), at in zip(told, by_world):
        rev = evaluate(case["revenue"], at)
        shortfall = base_rev - rev
        deferred, con = opt("deferred_past_period", at), opt("contribution", at)
        lost = shortfall - deferred if deferred is not None else None
        rows.append({"world": which, "label": label, "text": text,
                     "values": {k: v for k, v in at.items() if v != inputs[k]},
                     "revenue": rev, "shortfall": shortfall, "share": shortfall / den if den else None,
                     "deferred_past_period": deferred, "lost": lost,
                     "lost_share": lost / den if den and lost is not None else None,
                     "contribution_loss": base_con - con if con is not None else None,
                     "cash_delayed": opt("cash_delayed", at)})
        if deferred is not None and deferred > shortfall + 1e-6:
            warnings.append(f"{label}: revenue deferred past the period ({deferred:,.0f}) is more than the shortfall "
                            f"within it ({shortfall:,.0f}); the deferral is part of that shortfall.")
    labels = [label for _, label, _ in told]
    moves = sensitivity(case, every, labels, inputs, given, [r["shortfall"] for r in rows])
    worst = max(rows, key=lambda r: r["shortfall"])
    known = [r["lost"] for r in rows if r["lost"] is not None]
    return {"reads": read, "baseline_revenue": base_rev, "baseline_contribution": base_con,
            "denominator": {"label": case["denominator"]["label"], "value": den} if case.get("denominator") else None,
            "worlds": rows, "worst": worst["label"], "max_shortfall": worst["shortfall"], "max_share": worst["share"],
            "max_lost": max(known) if known else None, "sensitivity": moves,
            "condition": " Or: ".join(r["text"] for r in rows if abs(r["shortfall"]) > 1e-9)}, warnings
