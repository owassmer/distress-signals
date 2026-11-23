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
    worst = max(rows, key=lambda r: r["shortfall"])
    known = [r["lost"] for r in rows if r["lost"] is not None]
    return {"reads": read, "baseline_revenue": base_rev, "baseline_contribution": base_con,
            "denominator": {"label": case["denominator"]["label"], "value": den} if case.get("denominator") else None,
            "worlds": rows, "worst": worst["label"], "max_shortfall": worst["shortfall"], "max_share": worst["share"],
            "max_lost": max(known) if known else None,
            "condition": " Or: ".join(r["text"] for r in rows if abs(r["shortfall"]) > 1e-9)}, warnings
