"""Exposure arithmetic: the revenue at stake if a condition occurs. The researcher states inputs, a
baseline and the affected versions; this computes every result. A formula is an expression over input
names: numbers, + - * /, parentheses, min() and max(). Nothing else is evaluated.

compute() is the one calculation, used by calculate_exposure, save_opportunity and every read of a
saved case.

Revenue is what the company recognizes within the period, work or sales made up within the period
included. The shortfall is the baseline's revenue less the affected version's. Revenue deferred past the
period is recognized later; lost revenue is the shortfall less that deferral, and is reported only when
the deferral is stated, because otherwise later recovery is unknown. Cash delayed is a timing effect on
collections, reported apart."""

import ast
import operator

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


def compute(inputs, baseline, affected, denominator=None):
    """inputs {name: value}; baseline {revenue, contribution?}; affected [{name, revenue,
    deferred_past_period?, contribution?, cash_delayed?}]; denominator {label, formula} or None.
    Returns (results, warnings); raises FormulaError for a formula that can't be computed."""
    if not affected:
        raise ValueError("give at least one affected version")
    warnings = []
    den = None
    if denominator:
        den = evaluate(denominator["formula"], inputs)
        if den <= 0:
            raise FormulaError(f"denominator {denominator['formula']!r} is {den}, not positive")
    else:
        warnings.append("No denominator, so the shortfall isn't expressed as a share.")
    opt = lambda s, k: evaluate(s[k], inputs) if s.get(k) else None
    base_rev, base_con = evaluate(baseline["revenue"], inputs), opt(baseline, "contribution")
    rows = []
    for a in affected:
        rev = evaluate(a["revenue"], inputs)
        shortfall = base_rev - rev
        deferred = opt(a, "deferred_past_period")
        lost = shortfall - deferred if deferred is not None else None
        con = opt(a, "contribution")
        rows.append({"name": a["name"], "revenue": rev, "shortfall": shortfall,
                     "share": shortfall / den if den else None, "deferred_past_period": deferred, "lost": lost,
                     "lost_share": lost / den if den and lost is not None else None,
                     "contribution_loss": base_con - con if con is not None and base_con is not None else None,
                     "cash_delayed": opt(a, "cash_delayed")})
        if shortfall < 0:
            warnings.append(f"{a['name']}: revenue is higher than the baseline (shortfall {shortfall:,.0f}).")
        if deferred is not None and deferred > shortfall + 1e-6:
            warnings.append(f"{a['name']}: revenue deferred past the period ({deferred:,.0f}) is more than the shortfall "
                            f"within it ({shortfall:,.0f}); the deferral is part of that shortfall.")
        if con is not None and base_con is None:
            warnings.append(f"{a['name']}: contribution is given but the baseline has none.")
    worst = max(rows, key=lambda r: r["shortfall"])
    known = [r["lost"] for r in rows if r["lost"] is not None]
    return {"baseline_revenue": base_rev, "baseline_contribution": base_con,
            "denominator": {"label": denominator["label"], "value": den} if denominator else None,
            "affected": rows, "worst": worst["name"], "max_shortfall": worst["shortfall"], "max_share": worst["share"],
            "max_lost": max(known) if known else None}, warnings
