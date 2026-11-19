"""Reader-facing edits for the published example. Each edit applies only to its reviewed source record."""

from functools import lru_cache
from hashlib import sha256
import json
from math import isclose
from pathlib import Path

RUN_ID = "run_49fa0fa723da"


@lru_cache(maxsize=1)
def _review():
    path = Path(__file__).resolve().parents[1] / "docs" / "example" / f"{RUN_ID}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _digest(record):
    serialized = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return sha256(serialized.encode("utf-8")).hexdigest()


def _case_digest(case):
    # The first export included the percentage and shortfall but omitted the numeric denominator
    # and passage-check flags. Compare the added denominator to the original ratio when available.
    original = dict(case)
    denominator = original.pop("denominator_value", None)
    if denominator is not None:
        share, stake = case.get("share"), case.get("stake")
        if share is None or stake is None or not isclose(share * denominator, stake, rel_tol=1e-12):
            return None
    for section in ("inputs", "evidence"):
        if section in case:
            original[section] = [
                {k: v for k, v in row.items() if k != "checked"}
                for row in case[section]
            ]
    return _digest(original)


def reviewed_cases(run_id, cases):
    if run_id != RUN_ID:
        return cases
    result = dict(cases)
    for case_id, review in _review()["cases"].items():
        case = cases.get(case_id)
        if case is None or _case_digest(case) != review["source_digest"]:
            continue
        edits = review["edits"]
        updated = case | edits
        updated["baseline"] = case["baseline"] | edits["baseline"]
        for section in ("scenarios", "inputs", "evidence"):
            updated[section] = [row | changes for row, changes in zip(case[section], edits[section], strict=True)]
        result[case_id] = updated
    return result


def reviewed_leads(run_id, aside, leads):
    if run_id != RUN_ID:
        return {"aside": aside, "leads": leads}
    review = _review()
    return {
        "aside": [review["aside"].get(_digest(text), text) for text in aside],
        "leads": [lead | review["leads"].get(_digest(lead), {}) for lead in leads],
    }
