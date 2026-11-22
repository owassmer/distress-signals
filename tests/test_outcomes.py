"""An event's outcomes and the cases that name them, through the save and lookup tools on a store of their
own: which outcomes a version can name, which are left open, what a figure for an outcome covers when the
outcome is refined, what a revised event keeps, and how the condition reads."""
import copy
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from distress_signals import briefs, db, forms, outcomes, tools

EVENT = {"key": "carrier-filing", "title": "The carrier files or stops flying",
         "text": "The carrier files a Chapter 11 petition or stops scheduled passenger service by December 31, 2027.",
         "description": "The carrier, its lenders and the bankruptcy court decide it.", "kind": "hitting",
         "window_end": "2027-12-31", "resolution_source": "The bankruptcy court's docket for the petition",
         "yes_ways": [{"key": "keeps-flying", "title": "It reorganizes and keeps flying",
                       "text": "The carrier reorganizes in Chapter 11 and still carries passengers on December 31, 2027."},
                      {"key": "stops-flying", "title": "It stops flying",
                       "text": "The carrier has stopped scheduled passenger service by December 31, 2027."}]}


def case(company="Northfield Airport Authority", event=None, affected=None, **more):
    """An airport operator whose passenger-driven revenue is 100 and whose carrier has 30% of its passengers."""
    affected = affected or [
        {"name": "cut", "outcome": "keeps-flying", "description": "It flies 30% less here; half is replaced.",
         "timeline": "From the filing through the year.", "revenue": "pax_rev - pax_rev * share * cut * (1 - replaced)"},
        {"name": "exit", "outcome": "stops-flying", "description": "It carries no one; half is replaced.",
         "timeline": "All year.", "revenue": "pax_rev - pax_rev * share * (1 - replaced) - unpaid"}]
    assumed = lambda v, unit: {"value": v, "unit": unit, "assumption": "Stated for the test."}  # noqa: E731
    return {"company": {"name": company, "description": "Runs an airport."}, "event": event or copy.deepcopy(EVENT),
            "stream_key": "passenger-driven", "stream": "Parking, rental cars and concessions",
            "dependency": "Airline fees are spread over the remaining carriers; passenger-driven revenue is not.",
            "timing": "From the first schedule after a filing.", "period": {"start": "2027-01-01", "end": "2027-12-31"},
            "currency": "USD",
            "inputs": {"pax_rev": assumed(100, "USD per year"), "share": assumed(0.3, "share of passengers"),
                       "cut": assumed(0.3, "share of its flying"), "replaced": assumed(0.5, "share replaced"),
                       "unpaid": assumed(2, "USD")},
            "baseline": {"description": "The carrier flies its current schedule.", "revenue": "pax_rev"},
            "affected": affected,
            "case": {"why_matters": "It is the largest carrier here.", "why_now": "Its lenders have organized.",
                     "priority": 2, "priority_reason": "Mid-size exposure."},
            "buyer": {"role": "Runs the airport's finances"}, "outreach": {"draft": "How much of your parking follows them?"},
            "next_action": "Contact the finance director.", "change_note": "First save", **more}


class Outcomes(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        env = patch.dict(os.environ, DISTRESS_DB=str(Path(self.folder.name) / "test.db"), DISTRESS_RUN_ID="test")
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("HUBSPOT_SERVICE_KEY", None)
        os.environ.pop("DISTRESS_RUN_PRINCIPAL", None)

    def test_a_case_names_outcomes_of_its_event(self):
        out = tools.save_opportunity(case())
        rows = {r["name"]: r for r in out["results"]["affected"]}
        self.assertEqual(rows["cut"]["outcome"], "keeps-flying")
        self.assertAlmostEqual(rows["cut"]["shortfall"], 4.5)       # 100 * 0.3 * 0.3 * (1 - 0.5)
        self.assertAlmostEqual(rows["exit"]["shortfall"], 17.0)     # 100 * 0.3 * (1 - 0.5) + 2
        self.assertEqual(out["open_outcomes"], ["no"])              # the case says nothing about the No side
        self.assertEqual(out["whole_outcomes"], [])
        self.assertEqual(out["condition"], EVENT["yes_ways"][0]["text"] + " Or: " + EVENT["yes_ways"][1]["text"])

    def test_an_outcome_the_event_lacks_is_refused(self):
        bad = case()
        bad["affected"][0]["outcome"] = "liquidates"
        with self.assertRaises(tools.ToolError) as e:
            tools.save_opportunity(bad)
        self.assertIn("no outcome 'liquidates'", str(e.exception))
        self.assertIn("stops-flying: It stops flying", str(e.exception))   # the outcomes it has are listed
        con = db.connect()
        self.assertIsNone(db.get_event(con, "carrier-filing"))             # the refused save stored nothing
        con.close()

    def test_zero_is_stated_and_the_rest_is_open(self):
        # A world whose revenue equals the baseline's is named with that revenue: a shortfall of zero, stated.
        full = case()
        full["affected"].append({"name": "carries_on", "outcome": "no", "description": "It flies its schedule.",
                                 "timeline": "All year.", "revenue": "pax_rev"})
        out = tools.save_opportunity(full)
        rows = {r["name"]: r for r in out["results"]["affected"]}
        self.assertEqual(rows["carries_on"]["shortfall"], 0)
        self.assertEqual(out["open_outcomes"], [])
        # The world at the baseline is no part of what has to happen for revenue to be affected.
        self.assertEqual(out["condition"], EVENT["yes_ways"][0]["text"] + " Or: " + EVENT["yes_ways"][1]["text"])
        self.assertNotIn("turns out false", tools.lookup(out["opportunity_id"])["condition"])
        event = {**EVENT, "no_ways": [{"key": "refinances", "title": "It refinances", "text": "It refinances out of court."},
                                      {"key": "muddles", "title": "It carries on", "text": "It neither files nor refinances."}]}
        named = lambda *keys: {"affected": [{"outcome": k} for k in keys]}  # noqa: E731
        self.assertEqual(outcomes.open_outcomes(event, named("yes")), ["no"])
        self.assertEqual(outcomes.open_outcomes(event, named("stops-flying")), ["keeps-flying", "no"])
        self.assertEqual(outcomes.open_outcomes(event, named("keeps-flying", "stops-flying", "refinances")), ["muddles"])

    def test_the_outcomes_a_case_names_lie_apart(self):
        # A figure for the Yes side is for the side as a whole, so it cannot sit beside a figure for one of its ways.
        both = case()
        both["affected"][0]["outcome"] = "yes"
        with self.assertRaises(tools.ToolError) as e:
            tools.save_opportunity(both)
        self.assertIn("'stops-flying' lies within 'yes'", str(e.exception))

    def test_a_revised_event_keeps_the_outcomes_saved_cases_name(self):
        first = tools.save_opportunity(case())["opportunity_id"]
        # Another company's case draws a finer distinction within an outcome: the first case still holds.
        finer = copy.deepcopy(EVENT)
        finer["yes_ways"][1]["ways"] = [
            {"key": "stops-early", "title": "It stops before July", "text": "It stops before July 1, 2027."},
            {"key": "stops-late", "title": "It stops from July", "text": "It stops on or after July 1, 2027."}]
        second = case("Lakeshore Airport Authority", event=finer, affected=[
            {"name": "early_exit", "outcome": "stops-early", "description": "It leaves in spring.", "timeline": "From spring.",
             "revenue": "pax_rev - pax_rev * share * (1 - replaced) - unpaid"}])
        out = tools.save_opportunity(second)
        self.assertEqual(out["open_outcomes"], ["keeps-flying", "stops-late", "no"])
        # The first case's figure stays the figure for the outcome as a whole; its new ways have none yet.
        seen = tools.lookup(first)
        self.assertEqual(seen["open_outcomes"], ["no"])
        self.assertEqual(seen["whole_outcomes"], [{"outcome": "stops-flying", "ways": ["stops-early", "stops-late"]}])
        # A definition that leaves out an outcome the first case names is refused, and names the case.
        coarser = copy.deepcopy(EVENT)
        coarser["yes_ways"] = []
        third = case("Lakeshore Airport Authority", event=coarser, affected=[
            {"name": "any", "outcome": "yes", "description": "Either way.", "timeline": "All year.", "revenue": "pax_rev - 5"}])
        with self.assertRaises(tools.ToolError) as e:
            tools.save_opportunity(third)
        self.assertIn(first, str(e.exception))
        self.assertIn("keeps-flying", str(e.exception))
        con = db.connect()
        self.assertEqual(len(forms.event(db.get_event(con, "carrier-filing")["data"])["yes_ways"][1]["ways"]), 2)
        con.close()

    def test_the_event_shows_each_case_by_outcome(self):
        tools.save_opportunity(case())
        seen = tools.lookup("carrier-filing")
        self.assertEqual(seen["event"]["kind"], "hitting")
        self.assertEqual([w["key"] for w in seen["event"]["yes_ways"]], ["keeps-flying", "stops-flying"])
        brief = seen["cases"][0]
        self.assertEqual(brief["open_outcomes"], ["no"])
        self.assertEqual(brief["baseline"], {"description": "The carrier flies its current schedule.", "formula": "pax_rev",
                                             "revenue": 100.0})
        self.assertEqual([(w["outcome"], round(w["shortfall"], 1), w["formula"]) for w in brief["worlds"]],
                         [("keeps-flying", 4.5, "pax_rev - pax_rev * share * cut * (1 - replaced)"),
                          ("stops-flying", 17.0, "pax_rev - pax_rev * share * (1 - replaced) - unpaid")])
        self.assertTrue(brief["estimated"])

    def test_the_memo_reads_the_condition_from_the_event(self):
        oid = tools.save_opportunity(case())["opportunity_id"]
        con = db.connect()
        memo = briefs.memo(briefs.cases(con, [oid])[0])
        con.close()
        self.assertIn("The carrier has stopped scheduled passenger service by December 31, 2027.", memo)
        self.assertIn("| exit | It stops flying |", memo)
        self.assertIn("| baseline | – | $100 |", memo)
        self.assertIn("**It reorganizes and keeps flying** (this case: cut)", memo)
        self.assertIn("- **No**: The claim turns out false.", memo)

    def test_a_case_saved_before_outcomes_reads_with_what_it_holds(self):
        earlier_event = {"key": "old-event", "title": "An old event", "description": "What could happen.",
                         "date": "2027-02-16", "timing": "Soon.", "evidence": []}
        earlier_case = {k: v for k, v in case().items() if k in forms.CASE_FIELDS} | {"condition": "The agency says no."}
        for a in earlier_case["affected"]:
            del a["outcome"]
        current, notes = forms.case(earlier_case)
        self.assertEqual(forms.event(earlier_event)["window_end"], "2027-02-16")
        self.assertIn("Saved before events carried outcomes", notes[0])
        self.assertIn("The agency says no.", notes[0])
        self.assertEqual(outcomes.condition(forms.event(earlier_event), current, forms._results(current)[0]),
                         "The agency says no.")
        # Saving it again by its event's key asks for the event in full.
        con = db.connect()
        with db.write(con):
            db.put_event(con, "ev_old", "old-event", "An old event", earlier_event)
        con.close()
        with self.assertRaises(tools.ToolError) as e:
            tools.save_opportunity(case(event="old-event"))
        self.assertIn("Give it in full", str(e.exception))


if __name__ == "__main__":
    unittest.main()
