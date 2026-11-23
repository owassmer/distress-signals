"""A case's model, through the save, calculate and lookup tools on a store of their own: one revenue formula,
the inputs at their values in the baseline, and the inputs' values in each outcome of the event that changes
them. What an outcome's values hold in, what each basis is checked against, what a revised event keeps, and
how the condition, the event view and the memo read."""
import copy
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from distress_signals import briefs, db, exposure, tools

REPORT = ("The Company's largest program is the Regional Transit Program.\nIn fiscal 2026 the Company recognized $100 of "
          "revenue under the\nprogram. The agency's proposed budget would fund the program at 70% of\nits current level.")


def claim(key, title, text, **more):
    return {"key": key, "title": title, "text": text, "kind": "scheduled_decision", "window_end": "2027-06-30",
            "resolution_source": "The agency's notice of the decision", **more}


EVENT = {**claim("program-renewal", "The agency renews the program", "The agency renews the program by June 30, 2027."),
         "description": "The agency's board decides it.",
         "yes_ways": [claim("renewed-in-full", "Renewed at its current funding", "Renewed at this year's funding or more."),
                      claim("renewed-smaller", "Renewed at less funding", "Renewed at less than this year's funding.")]}
ASSUMED = {"note": "Stated for the test, with nothing behind it."}


def case(company="Northfield Transit Services", event=None, by_outcome=None, inputs=None, **more):
    """A contractor that earns 100 a year from one program, which the agency may renew, cut or end."""
    return {"company": {"name": company, "description": "Runs transit services under contract."},
            "event": event if event is not None else copy.deepcopy(EVENT),
            "stream_key": "transit-program", "stream": "Revenue under the Regional Transit Program",
            "dependency": "The program is funded year to year by the agency.", "timing": "From the next fiscal year.",
            "period": {"start": "2027-07-01", "end": "2028-06-30"}, "currency": "USD",
            "revenue": "program_revenue * share_kept - late_payment",
            "inputs": inputs or {
                "program_revenue": {"unit": "USD", "value": 100, "source": {"observation_id": "obs_report", "passage": "recognized $100 of revenue under the"}},
                "share_kept": {"unit": "share of this year's funding", "value": 1, "assumption": dict(ASSUMED)},
                "late_payment": {"unit": "USD", "value": 0, "assumption": dict(ASSUMED)}},
            "by_outcome": by_outcome or {"no": {"share_kept": {"value": 0, "assumption": dict(ASSUMED)}},
                                         "renewed-smaller": {"share_kept": {"value": 0.7, "assumption": dict(ASSUMED)}}},
            "denominator": {"label": "program revenue", "formula": "program_revenue"},
            "case": {"why_matters": "It is the company's largest program.", "why_now": "The board votes in June.",
                     "priority": 2, "priority_reason": "One program, one decision."},
            "buyer": {"role": "Runs the company's finances"}, "outreach": {"draft": "How much of the program is committed?"},
            "next_action": "Contact the finance director.", "change_note": "First save", **more}


class CaseModel(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        env = patch.dict(os.environ, DISTRESS_DB=str(Path(self.folder.name) / "test.db"), DISTRESS_RUN_ID="test")
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("HUBSPOT_SERVICE_KEY", None)
        os.environ.pop("DISTRESS_RUN_PRINCIPAL", None)
        with db.connect() as con:
            db.put_observation(con, {"id": "obs_report", "kind": "page", "source": "example.org", "retrieved_at": "2026-11-23",
                                     "url": "https://example.org/annual-report", "text": REPORT})
            db.put_result(con, {"id": "wk_programs", "name": "program_revenue_by_company", "sql": "SELECT 1",
                                "columns": ["company", "program", "revenue"], "row_count": 3,
                                "rows": [["Northfield", "transit", 100.0], ["Northfield", "paratransit", 20.0],
                                         ["Lakeshore", "transit", 60.0]]})

    def rows(self, out):
        return {r["outcome"]: r for r in out["results"]["outcomes"]}

    def test_one_formula_gives_the_baseline_and_each_outcome(self):
        out = tools.save_opportunity(case())
        self.assertEqual(out["results"]["baseline_revenue"], 100)
        rows = self.rows(out)
        self.assertEqual(list(rows), ["no", "renewed-smaller"])
        self.assertEqual((rows["no"]["revenue"], rows["no"]["shortfall"], rows["no"]["share"]), (0, 100, 1))
        self.assertAlmostEqual(rows["renewed-smaller"]["shortfall"], 30)
        self.assertEqual((out["results"]["worst"], out["results"]["max_shortfall"]), ("no", 100))
        self.assertEqual(out["condition"], "Renewed at less than this year's funding. Or: This turns out false: The agency "
                                           "renews the program by June 30, 2027.")
        # An outcome given the baseline's own value changes nothing, and is no part of the condition.
        same = case(change_note="An outcome at the baseline")
        same["by_outcome"]["renewed-in-full"] = {"share_kept": {"value": 1, "assumption": dict(ASSUMED)}}
        again = tools.save_opportunity(same)
        self.assertEqual(self.rows(again)["renewed-in-full"]["shortfall"], 0)
        self.assertEqual(again["condition"], out["condition"])

    def test_an_outcomes_values_hold_in_its_ways(self):
        event = copy.deepcopy(EVENT)
        event["yes_ways"][1]["ways"] = [claim("cut-under-half", "Cut by less than half", "Funded at half or more."),
                                        claim("cut-half-or-more", "Cut by half or more", "Funded at less than half.")]
        out = tools.save_opportunity(case(event=event, by_outcome={
            "renewed-smaller": {"share_kept": {"value": 0.7, "assumption": dict(ASSUMED)}},
            "cut-half-or-more": {"late_payment": {"value": 5, "assumption": dict(ASSUMED)}}}))
        rows = self.rows(out)
        self.assertAlmostEqual(rows["renewed-smaller"]["revenue"], 70)
        self.assertAlmostEqual(rows["cut-half-or-more"]["revenue"], 65)       # the outcome's 0.7, and its own late payment
        # A way that gives the same input its own value takes its own.
        out = tools.save_opportunity(case(event=event, by_outcome={
            "renewed-smaller": {"share_kept": {"value": 0.7, "assumption": dict(ASSUMED)}},
            "cut-half-or-more": {"share_kept": {"value": 0.4, "assumption": dict(ASSUMED)}}}, change_note="Its own value"))
        self.assertAlmostEqual(self.rows(out)["cut-half-or-more"]["revenue"], 40)
        # Without the event's tree each outcome stands on the baseline alone.
        alone, _ = exposure.compute({"program_revenue": 100, "share_kept": 1, "late_payment": 0},
                                    {"revenue": "program_revenue * share_kept - late_payment"},
                                    {"renewed-smaller": {"share_kept": 0.7}, "cut-half-or-more": {"late_payment": 5}})
        self.assertAlmostEqual(alone["outcomes"][1]["revenue"], 95)

    def test_an_outcome_the_event_lacks_is_refused(self):
        with self.assertRaises(tools.ToolError) as e:
            tools.save_opportunity(case(by_outcome={"cancelled": {"share_kept": {"value": 0, "assumption": dict(ASSUMED)}}}))
        self.assertIn("no outcome 'cancelled'", str(e.exception))
        self.assertIn("renewed-smaller: Renewed at less funding", str(e.exception))   # the outcomes it has are listed
        with db.connect() as con:
            self.assertIsNone(db.get_event(con, "program-renewal"))                    # the refused save stored nothing

    def test_an_outcome_gives_values_to_inputs_the_case_states(self):
        with self.assertRaises(tools.ToolError) as e:
            tools.save_opportunity(case(by_outcome={"no": {"funding": {"value": 0, "assumption": dict(ASSUMED)}}}))
        self.assertIn("no input named funding", str(e.exception))

    def test_a_way_is_stated_as_the_event_is(self):
        event = copy.deepcopy(EVENT)
        del event["yes_ways"][0]["kind"]
        with self.assertRaises(tools.ToolError) as e:
            tools.save_opportunity(case(event=event))
        self.assertIn("kind", str(e.exception))

    def test_the_store_reads_an_observed_value_from_its_result(self):
        cell = {"result_id": "wk_programs", "column": "revenue", "row": {"company": "Northfield", "program": "transit"}}
        inputs = case()["inputs"] | {"program_revenue": {"unit": "USD", "result": cell}}
        out = tools.save_opportunity(case(inputs=inputs))
        self.assertEqual(out["results"]["baseline_revenue"], 100)                      # no number was typed
        self.assertEqual(tools.lookup(out["opportunity_id"])["opportunity"]["inputs"]["program_revenue"]["value"], 100)
        self.assertEqual(out["workspace_results_used"], ["wk_programs"])
        for row, said in (({"company": "Northfield"}, "picks out 2 of the 3 rows"), ({"company": "Nowhere"}, "picks out 0 of"),
                          ({"region": "east"}, "no column 'region'")):
            with self.subTest(row=row), self.assertRaises(tools.ToolError) as e:
                tools.save_opportunity(case(inputs=inputs | {"program_revenue": {"unit": "USD", "result": {**cell, "row": row}}}))
            self.assertIn(said, str(e.exception))
        with self.assertRaises(tools.ToolError) as e:      # a typed value that is not the cell's
            tools.save_opportunity(case(inputs=inputs | {"program_revenue": {"unit": "USD", "value": 120, "result": cell}}))
        self.assertIn("holds 100", str(e.exception))
        draft = {k: v for k, v in case(inputs=inputs).items() if k in ("revenue", "inputs", "by_outcome")}
        self.assertEqual(tools.calculate_exposure(draft)["results"]["baseline_revenue"], 100)

    def test_an_assumption_opens_to_what_it_rests_on(self):
        rests = {"note": "The agency's proposed budget funds the program at 70% of this year's.",
                 "evidence": [{"observation_id": "obs_report", "passage": "would fund the program at 70% of"}],
                 "results": ["wk_programs"]}
        out = tools.save_opportunity(case(by_outcome={"renewed-smaller": {"share_kept": {"value": 0.7, "assumption": rests}}}))
        seen = tools.lookup(out["opportunity_id"])
        stored = seen["opportunity"]["by_outcome"]["renewed-smaller"]["share_kept"]["assumption"]["evidence"][0]["passage"]
        self.assertEqual(stored, "The agency's proposed budget would fund the program at 70% of its current level.")
        self.assertIn("by_outcome.renewed-smaller.share_kept", [e["at"] for e in seen["evidence"]])
        self.assertEqual(out["workspace_results_used"], ["wk_programs"])
        for bad, said in (({**rests, "evidence": [{"observation_id": "obs_report", "passage": "would fund the program at 40% of"}]},
                           "passage not found word for word"), ({**rests, "results": ["wk_missing"]}, "no saved result 'wk_missing'")):
            with self.subTest(said=said), self.assertRaises(tools.ToolError) as e:
                tools.save_opportunity(case(by_outcome={"renewed-smaller": {"share_kept": {"value": 0.7, "assumption": bad}}}))
            self.assertIn(said, str(e.exception))

    def test_a_revised_event_keeps_the_outcomes_saved_cases_give_values_for(self):
        first = tools.save_opportunity(case())["opportunity_id"]
        # Another company's case draws a finer distinction within an outcome: the first case stands as it was.
        finer = copy.deepcopy(EVENT)
        finer["yes_ways"][1]["ways"] = [claim("cut-under-half", "Cut by less than half", "Funded at half or more."),
                                        claim("cut-half-or-more", "Cut by half or more", "Funded at less than half.")]
        tools.save_opportunity(case("Lakeshore Transit", event=finer, by_outcome={
            "cut-half-or-more": {"share_kept": {"value": 0.4, "assumption": dict(ASSUMED)}}}))
        self.assertAlmostEqual(self.rows(tools.lookup(first))["renewed-smaller"]["shortfall"], 30)
        # A definition that leaves out an outcome the first case gives values for is refused, and names the case.
        coarser = copy.deepcopy(EVENT)
        coarser["yes_ways"] = []
        with self.assertRaises(tools.ToolError) as e:
            tools.save_opportunity(case("Lakeshore Transit", event=coarser, by_outcome={
                "no": {"share_kept": {"value": 0, "assumption": dict(ASSUMED)}}}))
        self.assertIn(first, str(e.exception))
        self.assertIn("renewed-smaller", str(e.exception))

    def test_the_event_shows_each_case_by_outcome(self):
        tools.save_opportunity(case())
        seen = tools.lookup("program-renewal")
        self.assertEqual([w["key"] for w in seen["event"]["yes_ways"]], ["renewed-in-full", "renewed-smaller"])
        brief = seen["cases"][0]
        self.assertEqual((brief["revenue"], brief["baseline_revenue"]), ("program_revenue * share_kept - late_payment", 100))
        self.assertEqual([(o["outcome"], o["values"], round(o["shortfall"], 1)) for o in brief["outcomes"]],
                         [("no", {"share_kept": 0}, 100), ("renewed-smaller", {"share_kept": 0.7}, 30)])

    def test_the_memo_reads_the_case_from_its_event(self):
        oid = tools.save_opportunity(case())["opportunity_id"]
        with db.connect() as con:
            memo = briefs.memo(briefs.cases(con, [oid])[0])
        self.assertIn('Revenue at stake: $100 (100.0% of program revenue)', memo)
        self.assertIn('in the outcome "Not: The agency renews the program"', memo)
        self.assertIn("| baseline | $100 |", memo)
        self.assertIn("| Renewed at less funding | $70 | $30 | 30.0% |", memo)
        self.assertIn("  - **Renewed at less funding**: Renewed at less than this year's funding.", memo)
        self.assertIn("    - `share_kept` = 0.7 share of this year's funding: Stated for the test", memo)
        self.assertIn("- revenue = `program_revenue * share_kept - late_payment`", memo)
        self.assertIn('| `program_revenue` | $100 | reported | "In fiscal 2026 the Company recognized $100 of revenue under the program."', memo)

    def test_a_draft_is_calculated_with_its_event_or_alone(self):
        draft = {k: v for k, v in case().items() if k in ("revenue", "inputs", "by_outcome", "denominator")}
        self.assertAlmostEqual(self.rows(tools.calculate_exposure(draft))["renewed-smaller"]["shortfall"], 30)
        with self.assertRaises(tools.ToolError) as e:
            tools.calculate_exposure({**draft, "event": copy.deepcopy(EVENT),
                                      "by_outcome": {"cancelled": {"share_kept": {"value": 0, "assumption": dict(ASSUMED)}}}})
        self.assertIn("no outcome 'cancelled'", str(e.exception))


if __name__ == "__main__":
    unittest.main()
