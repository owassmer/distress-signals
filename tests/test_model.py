"""The model of docs/model.md, through the save, calculate and lookup tools on a store of their own. The
cases of its section 9 are tests 1 to 14 here, by number; the rest are what the store does with propositions
and with a case's bases."""
import copy
import os
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from distress_signals import agent, briefs, db, forms, memo, tools, watch, worlds

# Two pages as the reader stores them: a line is one block of the page (data.blocks).
REPORT = ("The Company's largest program is the Regional Transit Program.\nIn fiscal 2026 the Company recognized $100 of "
          "revenue under the program. The agency's proposed budget would fund the program at 70% of its current level.\n"
          "The Company's contract fixes its fee for the program year beginning July 1, 2027 at $100.")
MINUTES = "The board will vote on the program in June.\nIt met twice this year."
FUNDING = {"metric": "program_funding_share", "unit": "share of this year's funding"}


def claim(key, title, text=None, **more):
    return {"key": key, "title": title, "text": text or title + ".", "kind": "scheduled_decision",
            "window_end": "2027-06-30", "resolution_source": "The agency's notice of the decision", **more}


def program(extra=()):
    return claim("program-renewal", "The agency renews the program", "The agency renews the program by June 30, 2027.",
                 description="The agency's board decides it.", ways=[
                     claim("renewed-in-full", "Renewed in full", comparison=">=", value=1, **FUNDING),
                     claim("cut-under-half", "Cut by less than half", comparison=">=", value=0.5, upper_comparison="<",
                           upper_value=1, **FUNDING),
                     claim("cut-half-or-more", "Cut by half or more", comparison="<", value=0.5, **FUNDING), *extra])


SUBCONTRACT = claim("subcontract-won", "Subcontract won",
                    "The contractor holds at least half of its program work as a subcontractor within a year of a non-renewal.",
                    kind="hitting", window_end="2028-06-30", implies_not=["program-renewal"])
A = {"note": "Stated for the test, with nothing behind it."}
LABEL = "A figure of the test"


def given(values, *true, not_=()):
    """A given value for each input named. A number is assumed; a dict is the value's basis as stated."""
    return {"given": list(true), "given_not": list(not_),
            "values": {k: v if isinstance(v, dict) else {"value": v, "assumption": dict(A)} for k, v in values.items()}}


ILLUSTRATION = [given({"share_kept": 0}, not_=["program-renewal"]), given({"share_kept": 0.7}, "cut-under-half"),
                given({"share_kept": 0.4}, "cut-half-or-more"), given({"share_kept": 0.5}, "subcontract-won")]


def case(given_values=None, company="Northfield Transit Services", events=("program-renewal",), **more):
    """A contractor that earns 100 a year from one program, which the agency may renew, cut or end."""
    return {"company": {"name": company, "description": "Runs transit services under contract."}, "events": list(events),
            "stream_key": "transit-program", "stream": "Revenue under the Regional Transit Program",
            "dependency": "The program is funded year to year by the agency.", "timing": "From the next fiscal year.",
            "period": {"start": "2027-07-01", "end": "2028-06-30"}, "currency": "USD",
            "revenue": "program_revenue * share_kept",
            "inputs": {"program_revenue": {"label": "Revenue under the Regional Transit Program", "unit": "USD", "value": 100,
                                           "source": {"observation_id": "obs_report", "passage": "recognized $100 of revenue under the"},
                                           "assumption": {"note": "Fiscal 2026 is the last year reported, and the program's terms "
                                                                  "are unchanged, so its revenue is carried into the period."}},
                       "share_kept": {"label": "Share of that revenue the company keeps", "unit": "share of this year's funding",
                                      "value": 1, "assumption": dict(A)}},
            "given_values": copy.deepcopy(given_values if given_values is not None else ILLUSTRATION),
            "denominator": {"label": "program revenue", "formula": "program_revenue"},
            "person": {"role": "Runs the company's finances"}, "change_note": "First save", **more}


class Model(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        env = patch.dict(os.environ, DISTRESS_DB=str(Path(self.folder.name) / "test.db"), DISTRESS_RUN_ID="test")
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("HUBSPOT_SERVICE_KEY", None)
        os.environ.pop("DISTRESS_RUN_PRINCIPAL", None)
        with closing(db.connect()) as con:
            db.put_observation(con, {"id": "obs_report", "kind": "page", "source": "example.org", "retrieved_at": "2026-11-23",
                                     "url": "https://example.org/annual-report", "text": REPORT, "data": {"blocks": True}})
            db.put_observation(con, {"id": "obs_minutes", "kind": "page", "source": "example.org", "retrieved_at": "2026-11-23",
                                     "url": "https://example.org/minutes", "text": MINUTES,
                                     "data": {"title": "Board minutes", "blocks": True}})
            db.put_observation(con, {"id": "obs_plain", "kind": "page", "source": "example.org", "retrieved_at": "2026-11-23",
                                     "url": "https://example.org/notice.txt",          # a text whose layout no reader recorded
                                     "text": "NOTICE. The agency will hold its hearing on the\nprogram on May 4, 2027, at 10 a.m."})
            db.put_result(con, {"id": "wk_rates", "name": "share_kept_after_past_cuts", "sql": "SELECT 1", "columns": ["outcome", "share_kept"],
                                "row_count": 2, "rows": [["cut under half", 0.65], ["cut half or more", 0.35]]})
            db.put_result(con, {"id": "wk_programs", "name": "program_revenue_by_company", "sql": "SELECT 1",
                                "columns": ["company", "program", "revenue"], "row_count": 3,
                                "rows": [["Northfield", "transit", 100.0], ["Northfield", "paratransit", 20.0],
                                         ["Lakeshore", "transit", 60.0]]})

    def worlds(self, out):
        return [(w["label"], round(w["revenue"], 2), round(w["shortfall"], 2)) for w in out["results"]["worlds"]]

    def save(self, *a, propositions=(program(), SUBCONTRACT), **k):
        return tools.save_case(case(*a, **k), copy.deepcopy(list(propositions)))

    def refused(self, *said, **k):
        with self.assertRaises(tools.ToolError) as e:
            self.save(**k)
        for s in said:
            self.assertIn(s, str(e.exception))

    # ---------- the cases of docs/model.md section 9 ----------

    def test_01_the_illustration(self):
        out = self.save()
        self.assertEqual(self.worlds(out), [("Renewed in full", 100, 0), ("Cut by less than half", 70, 30),
                                            ("Cut by half or more", 40, 60),
                                            ("Not: The agency renews the program; Subcontract won", 50, 50),
                                            ("Not: The agency renews the program; Not: Subcontract won", 0, 100)])
        self.assertEqual(out["results"]["baseline_revenue"], 100)
        self.assertEqual((out["results"]["worst"], out["results"]["max_shortfall"]),
                         ("Not: The agency renews the program; Not: Subcontract won", 100))
        self.assertEqual(out["results"]["worlds"][3]["world"], {"true": ["subcontract-won"], "false": ["program-renewal"]})
        self.assertEqual(out["results"]["worlds"][1]["values"], {"share_kept": 0.7})
        self.assertNotIn("Renewed in full", out["condition"])                # a world at the baseline is no part of it
        self.assertIn("Cut by less than half. Or: Cut by half or more.", out["condition"])

    def test_02_a_second_company_reads_the_event_with_a_claim_of_its_own(self):
        first = self.save()["case_id"]
        own = claim("lakeshore-rebid-won", "Lakeshore wins the rebid", implies_not=["program-renewal"])
        out = self.save([given({"share_kept": 0}, not_=["program-renewal"]), given({"share_kept": 0.6}, "lakeshore-rebid-won")],
                        company="Lakeshore Transit", propositions=[own])
        self.assertEqual(self.worlds(out), [("The agency renews the program", 100, 0),
                                            ("Not: The agency renews the program; Lakeshore wins the rebid", 60, 40),
                                            ("Not: The agency renews the program; Not: Lakeshore wins the rebid", 0, 100)])
        self.assertEqual(len(tools.lookup(first)["results"]["worlds"]), 5)
        self.assertEqual([w["key"] for w in tools.lookup("program-renewal")["proposition"]["ways"]],
                         ["renewed-in-full", "cut-under-half", "cut-half-or-more"])
        self.assertEqual(out["also_revised"], [])

    def test_a_case_computes_alike_as_stated_and_as_stored(self):
        """A statement elsewhere computes every saved case again from the store, where an object's names come back
        in another order than they were stated in. A case nothing reached keeps its revision."""
        stated = case()
        stated["inputs"] = dict(reversed(list(stated["inputs"].items())))     # share_kept first: not the stored order
        first = tools.save_case(stated, [program(), SUBCONTRACT])
        table = [(r["figure"], round(r["shortfall_moves_by"], 6)) for r in first["results"]["sensitivity"]]
        self.assertEqual(table[:2], [("inputs.program_revenue", 10), ("inputs.share_kept", 10)])   # two that move it alike
        own = claim("lakeshore-rebid-won", "Lakeshore wins the rebid", implies_not=["program-renewal"])
        out = self.save([given({"share_kept": 0}, not_=["program-renewal"]), given({"share_kept": 0.6}, "lakeshore-rebid-won")],
                        company="Lakeshore Transit", propositions=[own])
        self.assertEqual(out["also_revised"], [])
        seen = tools.lookup(first["case_id"])
        self.assertEqual(seen["revision"], 1)
        self.assertEqual([(r["figure"], round(r["shortfall_moves_by"], 6)) for r in seen["results"]["sensitivity"]], table)

    def test_a_quotation_is_a_figure_with_what_names_it(self):
        """A table's row is quoted by its heading and its figures. The three rows the store refused in the runs of
        2026-11-23 are here as the agent quoted them, and figures alone or a word or two are still refused."""
        for row in ("| EMEA | 161,356 | 148,355 | 8.8%", "Operating revenue 43 133,895,460 121,298,752",
                    "JetBlue Airways\n|\n\n|\n24\n|\n%\n|\n23\n|\n%\n|\n26\n|\n%", "recognized $100 of revenue"):
            self.assertTrue(tools.has_text(row), row)
        for bare in ("161,356 | 148,355 | 8.8%", "8.8%", "JetBlue Airways", "Europe (3)", "$100"):
            self.assertFalse(tools.has_text(bare), bare)
        with closing(db.connect()) as con:
            db.put_observation(con, {"id": "obs_table", "kind": "page", "source": "example.org", "retrieved_at": "2026-11-23",
                                     "url": "https://example.org/table", "data": {"blocks": True},
                                     "text": "Revenue by program\n| Transit | 100 | 96 | 4.2%\n| Paratransit | 20 | 19 | 5.3%"})
        row = {"observation_id": "obs_table", "passage": "| Transit | 100 | 96 | 4.2%"}
        out = self.save(inputs=case()["inputs"] | {"program_revenue": {"label": LABEL, "unit": "USD", "value": 100, "source": row}})
        self.assertEqual(tools.lookup(out["case_id"])["case"]["inputs"]["program_revenue"]["source"]["passage"],
                         "| Transit | 100 | 96 | 4.2%")
        self.refused("inputs.program_revenue.source", "figures with no word",
                     inputs=case()["inputs"] | {"program_revenue": {"label": LABEL, "unit": "USD", "value": 100,
                                                                    "source": row | {"passage": "100 | 96 | 4.2%"}}})

    def test_03_and_04_an_outcome_stays_whole_until_this_case_names_a_way_of_it(self):
        whole = [given({"share_kept": 0}, not_=["program-renewal"])]
        out = self.save(whole)
        two = [("The agency renews the program", 100, 0), ("Not: The agency renews the program", 0, 100)]
        self.assertEqual(self.worlds(out), two)
        self.save([given({"share_kept": 0.4}, "cut-half-or-more")], company="Lakeshore Transit", propositions=())
        self.assertEqual(self.worlds(tools.lookup(out["case_id"])), two)

    def test_05_a_value_given_an_outcome_holds_in_its_ways(self):
        out = self.save([given({"late_fee": 2}, "program-renewal"), given({"share_kept": 0.4}, "cut-half-or-more")],
                        revenue="program_revenue * share_kept - late_fee",
                        inputs=case()["inputs"] | {"late_fee": {"label": LABEL, "unit": "USD", "value": 0, "assumption": dict(A)}})
        self.assertEqual(self.worlds(out), [("Renewed in full", 98, 2), ("Cut by less than half", 98, 2),
                                            ("Cut by half or more", 38, 62), ("Not: The agency renews the program", 100, 0)])
        self.assertEqual(out["results"]["worlds"][2]["values"], {"share_kept": 0.4, "late_fee": 2})

    def test_06_two_given_values_that_no_link_orders_are_refused(self):
        unlinked = {k: v for k, v in SUBCONTRACT.items() if k != "implies_not"}
        self.refused('In the world "Cut by less than half; Subcontract won"', "'share_kept'", "strictly within",
                     propositions=[program(), unlinked])

    def test_07_a_value_given_the_whole_condition_is_the_most_specific(self):
        out = self.save(ILLUSTRATION + [given({"share_kept": 0.1}, not_=["program-renewal", "subcontract-won"])])
        self.assertEqual(self.worlds(out)[-1], ("Not: The agency renews the program; Not: Subcontract won", 10, 90))

    def test_08_every_world_can_differ_from_the_baseline(self):
        out = self.save(ILLUSTRATION[:3] + [given({"share_kept": 1.1}, "renewed-in-full")])
        self.assertEqual([w[2] for w in self.worlds(out)], [-10, 30, 60, 100])
        self.assertEqual(out["results"]["baseline_revenue"], 100)

    def test_09_two_events_on_one_stream_are_read_together(self):
        a = claim("carrier-a-leaves", "Carrier A leaves", ways=[claim("a-leaves-early", "A leaves early"), claim("a-leaves-late", "A leaves late")])
        b = claim("carrier-b-leaves", "Carrier B leaves", ways=[claim("b-leaves-early", "B leaves early"), claim("b-leaves-late", "B leaves late")])
        inputs = {k: {"label": LABEL, "unit": "USD", "value": v, "assumption": dict(A)} for k, v in (("r", 100), ("a", 0), ("b", 0))}
        out = self.save([given({"a": 5}, "a-leaves-late"), given({"a": 20}, "a-leaves-early"), given({"b": 3}, "b-leaves-late"),
                         given({"b": 30}, "b-leaves-early")], events=["carrier-b-leaves", "carrier-a-leaves"], revenue="r - a - b",
                        inputs=inputs, denominator=None, propositions=[a, b])
        got = dict((w[0], w[2]) for w in self.worlds(out))
        self.assertEqual(len(got), 9)
        self.assertEqual((got["A leaves early; B leaves early"], got["A leaves late; Not: Carrier B leaves"],
                          got["Not: Carrier A leaves; Not: Carrier B leaves"]), (50, 5, 0))
        self.assertEqual(tools.lookup(out["case_id"])["case"]["events"], ["carrier-a-leaves", "carrier-b-leaves"])

    def test_10_ways_whose_level_fields_overlap_are_refused(self):
        fourth = claim("cut-around-half", "Funded at 40% to 60%", comparison=">=", value=0.4, upper_comparison="<=",
                       upper_value=0.6, **FUNDING)
        self.refused("can both be true by their level fields", propositions=[program([fourth]), SUBCONTRACT])
        # Ways whose thresholds leave values in none of them are saved, and the save says which.
        gapped = program()
        gapped["ways"][2]["value"] = 0.4
        out = self.save(propositions=[gapped, SUBCONTRACT])
        self.assertIn("The ways of program-renewal leave program_funding_share >= 0.4 and < 0.5 in none of them", out["warnings"])

    def test_11_the_store_reads_an_observed_value_from_its_result(self):
        cell = {"result_id": "wk_programs", "column": "revenue", "row": {"company": "Northfield", "program": "transit"}}
        inputs = case()["inputs"] | {"program_revenue": {"label": LABEL, "unit": "USD", "result": cell}}
        out = self.save(inputs=inputs)
        self.assertEqual(out["results"]["baseline_revenue"], 100)                      # no number was typed
        self.assertEqual(tools.lookup(out["case_id"])["case"]["inputs"]["program_revenue"]["value"], 100)
        self.assertEqual(out["workspace_results_used"], ["wk_programs"])
        for row, said in (({"company": "Northfield"}, "picks out 2 of the 3 rows"), ({"company": "Nowhere"}, "picks out 0 of"),
                          ({"region": "east"}, "no column 'region'")):
            with self.subTest(row=row):
                self.refused(said, inputs=inputs | {"program_revenue": {"label": LABEL, "unit": "USD", "result": {**cell, "row": row}}})
        self.refused("holds 100", inputs=inputs | {"program_revenue": {"label": LABEL, "unit": "USD", "value": 120, "result": cell}})

    def test_12_a_link_is_followed_through_a_proposition_the_case_does_not_read(self):
        deep = claim("deep-cut", "The agency cuts to a skeleton program", implies=["cut-half-or-more"])
        out = self.save([given({"share_kept": 0.2}, "deep-cut")], propositions=[program(), deep])
        self.assertEqual(self.worlds(out), [("The agency renews the program; Not: The agency cuts to a skeleton program", 100, 0),
                                            ("The agency renews the program; The agency cuts to a skeleton program", 20, 80),
                                            ("Not: The agency renews the program", 100, 0)])

    def nested(self):
        event = program()
        event["ways"][2]["ways"] = [
            claim("quarter-or-more-left", "A quarter or more left", comparison=">=", value=0.25, upper_comparison="<", upper_value=0.5, **FUNDING),
            claim("under-a-quarter-left", "Under a quarter left", comparison="<", value=0.25, **FUNDING)]
        return event

    def test_13_a_way_named_brings_each_level_above_it(self):
        out = self.save([given({"share_kept": 0.2}, "under-a-quarter-left")], propositions=[self.nested()])
        self.assertEqual(self.worlds(out), [("Renewed in full", 100, 0), ("Cut by less than half", 100, 0),
                                            ("A quarter or more left", 100, 0), ("Under a quarter left", 20, 80),
                                            ("Not: The agency renews the program", 100, 0)])

    def test_14_two_given_values_with_the_same_worlds_are_refused(self):
        self.refused('In the world "Cut by less than half"', "strictly within",
                     given_values=[given({"share_kept": 0.7}, "cut-under-half"),
                                   given({"share_kept": 0.6}, "program-renewal", "cut-under-half")])

    # ---------- what cannot hold ----------

    def test_a_condition_that_holds_in_no_world_is_refused(self):
        self.refused("no world has cut-under-half and subcontract-won",
                     given_values=ILLUSTRATION + [given({"share_kept": 0.9}, "cut-under-half", "subcontract-won")])

    def test_names_the_store_does_not_hold_are_refused(self):
        self.refused("no saved proposition 'cancelled'", given_values=[given({"share_kept": 0}, "cancelled")])
        self.refused("no input named funding", given_values=[given({"funding": 0}, "program-renewal")])
        self.refused("'cut-under-half' is a way of 'program-renewal'", events=["cut-under-half"])
        orphan = claim("orphan", "A claim", implies=["nothing-saved"])
        self.refused("linked to 'nothing-saved'", propositions=[program(), SUBCONTRACT, orphan])
        with closing(db.connect()) as con:
            self.assertEqual(db.propositions(con), {})                                 # a refused save stored nothing

    def test_a_proposition_is_stated_whole(self):
        alone = claim("renewal-alone", "An event", ways=[claim("only-way", "Its only way")])
        self.refused("'only-way' is the only way of 'renewal-alone'", propositions=[program(), SUBCONTRACT, alone])
        no_kind = program()
        del no_kind["ways"][0]["kind"]
        self.refused("kind", propositions=[no_kind, SUBCONTRACT])
        level = claim("funding-level", "Funding holds", comparison=">=")
        self.refused("states its metric and its value", propositions=[program(), SUBCONTRACT, level])

    # ---------- propositions in the store ----------

    def test_a_key_names_one_proposition(self):
        self.save()
        other = claim("grant-renewal", "The state renews the grant", ways=[claim("cut-under-half", "Cut"), claim("held", "Held")])
        self.refused("the key 'cut-under-half' already names a way of 'program-renewal'", propositions=[other],
                     given_values=[given({"share_kept": 0}, not_=["grant-renewal"])], events=["grant-renewal"])

    def test_a_restated_proposition_keeps_what_saved_cases_read(self):
        first = self.save()["case_id"]
        coarser = program()
        coarser["ways"] = []
        with self.assertRaises(tools.ToolError) as e:
            self.save([given({"share_kept": 0}, not_=["program-renewal"])], company="Lakeshore Transit", propositions=[coarser])
        self.assertIn(first, str(e.exception))
        self.assertIn("leaves out 'renewed-in-full'", str(e.exception))
        unlinked = {k: v for k, v in SUBCONTRACT.items() if k != "implies_not"}     # the first case then has no figure
        with self.assertRaises(tools.ToolError) as e:
            self.save([given({"share_kept": 0}, not_=["program-renewal"])], company="Lakeshore Transit", propositions=[unlinked])
        self.assertIn(f"the saved case {first} no longer has its figures", str(e.exception))
        self.assertEqual(len(tools.lookup(first)["results"]["worlds"]), 5)

    def test_a_restated_proposition_moves_the_cases_that_read_it(self):
        first = self.save()["case_id"]
        reworded = program()
        reworded["text"] = "The agency's board votes to renew the program by June 30, 2027."
        out = self.save([given({"share_kept": 0}, not_=["program-renewal"])], company="Lakeshore Transit", propositions=[reworded])
        self.assertEqual(out["also_revised"], [first])
        seen = tools.lookup(first)
        self.assertEqual((seen["revision"], seen["revisions"][-1]["change_note"]),
                         (2, "The propositions this case rests on changed: program-renewal"))
        with closing(db.connect()) as con:                                                    # the first estimate stands as issued
            was = con.execute("SELECT snapshot FROM revisions WHERE case_id=? AND revision=1", (first,)).fetchone()[0]
        self.assertIn("The agency renews the program by June 30, 2027.", was)

    def skeleton(self):
        """A case that reads `deep-cut`, which implies a way of `program-renewal` the case does not read."""
        deep = claim("deep-cut", "The agency cuts to a skeleton program", implies=["cut-half-or-more"])
        return self.save([given({"share_kept": 0.2}, "deep-cut")], propositions=[program(), deep])["case_id"]

    def test_a_statement_keeps_what_a_saved_proposition_is_linked_to(self):
        first = self.skeleton()
        coarser = program()
        coarser["ways"] = []
        self.refused("leaves out 'cut-half-or-more', which the saved proposition 'deep-cut'", company="Lakeshore Transit",
                     given_values=[given({"share_kept": 0}, not_=["program-renewal"])], propositions=[coarser])
        with closing(db.connect()) as con:
            self.assertIn("cut-half-or-more", db.propositions(con))
        self.assertEqual(len(tools.lookup(first)["results"]["worlds"]), 3)

    def test_a_statement_moves_every_case_its_propositions_reach(self):
        first = self.skeleton()                                   # through a link: the case does not read the way restated
        moved = program()
        moved["ways"][2]["text"] = "The agency funds the program at less than half of this year's level."
        out = self.save([given({"share_kept": 0}, not_=["program-renewal"])], company="Lakeshore Transit", propositions=[moved])
        self.assertEqual(out["also_revised"], [first])
        seen = tools.lookup(first)
        self.assertEqual(seen["revisions"][-1]["change_note"], "The propositions this case rests on changed: cut-half-or-more")
        with closing(db.connect()) as con:
            self.assertIn("less than half of this year's level", db.dumps(db.latest_revision(con, first)["snapshot"]))
        # through the ways beside one it reads: a way added to an event divides the side for the case that reads another
        ways = [claim("goes-to-a-rival", "A rival takes the route"), claim("route-closed", "The route closes")]
        route = claim("route-rebid", "The agency rebids the route", ways=ways)
        second = self.save([given({"share_kept": 0}, "goes-to-a-rival")], events=["route-rebid"], stream_key="route",
                           propositions=[route])
        self.assertEqual(len(second["results"]["worlds"]), 3)
        route["ways"] = ways + [claim("split-in-two", "The route is split between two operators")]
        out = tools.record_finding({"kind": "inference", "statement": "The rebid's terms allow the route to be split between operators.",
                                    "proposition": "route-rebid"}, [route])
        self.assertEqual(out["also_revised"], [second["case_id"]])
        seen = tools.lookup(second["case_id"])
        self.assertEqual((seen["revision"], len(seen["results"]["worlds"])), (2, 4))
        with closing(db.connect()) as con:
            self.assertEqual(len(db.get_case(con, second["case_id"])["results"]["worlds"]), 4)

    def test_a_case_another_statement_moves_cites_what_its_propositions_now_cite(self):
        first = self.save()["case_id"]
        more = program()
        more["evidence"] = [{"observation_id": "obs_minutes", "passage": "will vote on the program in June"}]
        out = self.save([given({"share_kept": 0}, not_=["program-renewal"])], company="Lakeshore Transit", propositions=[more])
        self.assertEqual(out["also_revised"], [first])
        with closing(db.connect()) as con:
            self.assertEqual([o["id"] for o in db.cited(con, first)], ["obs_minutes", "obs_report"])
            self.assertIn('- "The board will vote on the program in June." (Board minutes)', briefs.memo(briefs.cases(con, [first])[0]))

    def test_a_withdrawn_case_reads_as_its_last_revision_held_it(self):
        first = self.save()["case_id"]
        tools.record_finding({"kind": "conclusion", "statement": "The agency has funded the program for two years.",
                              "case_id": first, "withdraw_case": True})
        coarser = program()
        coarser["ways"] = []
        out = self.save([given({"share_kept": 0}, not_=["program-renewal"])], company="Lakeshore Transit", propositions=[coarser])
        self.assertEqual(out["also_revised"], [])
        with closing(db.connect()) as con:                           # the ways are gone from the store
            self.assertEqual(sorted(db.propositions(con)), ["program-renewal", "subcontract-won"])
            self.assertIn("**Withdrawn.**", briefs.memo(briefs.cases(con, [first])[0]))
        seen = tools.lookup(first)
        self.assertEqual((seen["status"], seen["revision"], len(seen["results"]["worlds"])), ("withdrawn", 1, 5))
        self.assertEqual([w["key"] for w in seen["propositions"][0]["ways"]], ["renewed-in-full", "cut-under-half", "cut-half-or-more"])
        self.assertIn("reads as its last revision held it", seen["how_to_update"])
        self.assertEqual(len(tools.lookup("program-renewal")["cases"]), 2)
        with self.assertRaises(tools.ToolError) as e:                 # saved again, it is computed on what now stands
            tools.save_case(seen["case"] | {"change_note": "Back in the queue"}, [])
        self.assertIn("no saved proposition 'cut-under-half'", str(e.exception))
        back = tools.save_case(seen["case"] | {"change_note": "Back in the queue"}, seen["propositions"])
        self.assertEqual((tools.lookup(first)["status"], len(back["results"]["worlds"])), ("active", 5))

    def test_a_case_as_lookup_returns_it_saves_as_it_is(self):
        out = self.save([given({"share_kept": 0.2}, "under-a-quarter-left")], propositions=[self.nested(), SUBCONTRACT])
        seen = tools.lookup(out["case_id"])
        deep = seen["propositions"][0]["ways"][2]["ways"][0]
        self.assertEqual((deep["key"], "side" in deep), ("quarter-or-more-left", False))
        again = tools.save_case(seen["case"] | {"change_note": "As it was"}, seen["propositions"])
        self.assertEqual((again["revision"], again["changed"], again["propositions_saved"]), (1, False, []))

    def test_no_two_worlds_read_alike(self):
        won = claim("rebid-won", "The contractor wins the rebid", implies=["rebid-awarded"], implies_not=["program-renewal"])
        awarded = claim("rebid-awarded", "The rebid is awarded to the contractor", implies=["rebid-won"], implies_not=["program-renewal"])
        out = self.save([given({"share_kept": 0.5}, not_=["program-renewal"]), given({"share_kept": 0.2}, "rebid-won", "rebid-awarded")],
                        propositions=[program(), won, awarded])
        self.assertEqual(self.worlds(out), [("The agency renews the program", 100, 0),
                                            ("Not: The agency renews the program; The contractor wins the rebid", 20, 80),
                                            ("Not: The agency renews the program; Not: The contractor wins the rebid", 50, 50)])
        with closing(db.connect()) as con:
            self.assertIn("Revenue at stake: $80 ", briefs.memo(briefs.cases(con, [out["case_id"]])[0]))

    def test_level_fields_are_compared_between_claims_about_one_level(self):
        band = lambda key, lo, hi, **more: claim(key, key, comparison=">=", value=lo, upper_comparison="<", upper_value=hi, **FUNDING, **more)  # noqa: E731
        for lo, hi in ((0.5, 0.5), (1, 0.5)):
            with self.subTest(band=(lo, hi)):
                self.refused("a band runs from value up to upper_value", propositions=[program([band("no-values", lo, hi)]), SUBCONTRACT])
        flat = lambda *claims: worlds.flatten(copy.deepcopy(list(claims)))  # noqa: E731
        at = lambda key, value, **more: claim(key, key, comparison=">=", value=value, metric="funding", **more)  # noqa: E731
        apart = [claim("high", "high", comparison=">=", value=120, metric="price"), claim("low", "low", comparison="<", value=80, metric="price")]
        hit = [dict(c, kind="hitting") for c in apart]
        for props, said in ((flat(at("in-percent", 50, unit="percent"), at("in-dollars", 100, unit="USD")), []),
                            (flat(at("from-january", 10, kind="hitting", window_start="2027-01-01"),
                                  at("from-july", 10, kind="hitting", window_start="2027-07-01")), []),
                            (flat(at("at-ten", 10), at("at-twenty", 20)), [("at-twenty", "implies", "at-ten")]),
                            (flat(*apart), [("high", "excludes", "low")]),      # one reading of the level cannot be both
                            (flat(*hit), [])):                                  # a level can reach both within a window
            with self.subTest(props=list(props)):
                self.assertEqual(worlds.read_fields(props, list(props)), said)

    def test_a_ways_values_lie_within_those_of_what_it_divides(self):
        level = lambda *ways: claim("funding-held", "Funding holds at half or more", comparison=">=", value=0.5, **FUNDING, ways=list(ways))  # noqa: E731
        way = lambda key, **more: claim(key, key, **FUNDING, **more)  # noqa: E731
        top = way("nine-tenths-or-more", comparison=">=", value=0.9)
        case_on = dict(events=["funding-held"], given_values=[given({"share_kept": 0.2}, not_=["funding-held"])])
        self.refused("the way 'under-three-tenths' (program_funding_share < 0.3) and 'funding-held' (>= 0.5)", "the way has values the proposition does not",
                     propositions=[level(way("under-three-tenths", comparison="<", value=0.3), top)], **case_on)
        self.refused("share values", "none of its values", **case_on, propositions=[level(
            way("under-three-tenths", side="no", comparison="<", value=0.3),
            way("three-to-six-tenths", side="no", comparison=">=", value=0.3, upper_comparison="<", upper_value=0.6))])
        out = self.save(**case_on, propositions=[level(
            way("six-to-eight-tenths", comparison=">=", value=0.6, upper_comparison="<", upper_value=0.8), top,
            way("under-three-tenths", side="no", comparison="<", value=0.3),
            way("three-to-four-tenths", side="no", comparison=">=", value=0.3, upper_comparison="<", upper_value=0.4))])
        self.assertEqual(out["warnings"][-3:], [
            "The ways of funding-held leave program_funding_share >= 0.5 and < 0.6 in none of them",
            "The ways of funding-held leave program_funding_share >= 0.8 and < 0.9 in none of them",
            "The ways of funding-held on its No side leave program_funding_share >= 0.4 and < 0.5 in none of them"])

    def test_a_proposition_shows_its_ways_its_level_fields_and_its_cases(self):
        self.save(propositions=[self.nested(), SUBCONTRACT])
        seen = tools.lookup("under-a-quarter-left")
        self.assertEqual((seen["way_of"], seen["proposition"]["key"]), ("program-renewal", "program-renewal"))
        self.assertIn("under-a-quarter-left implies cut-half-or-more", seen["level_fields"])
        self.assertIn("cut-under-half excludes renewed-in-full", seen["level_fields"])
        brief = seen["cases"][0]
        self.assertEqual((brief["revenue"], brief["baseline_revenue"], len(brief["worlds"])), ("program_revenue * share_kept", 100, 5))
        self.assertEqual(brief["given_values"][1], {"given": ["cut-under-half"], "given_not": [], "values": {"share_kept": 0.7}})
        self.assertEqual(tools.lookup("The agency renews the program")["proposition"]["key"], "program-renewal")

    # ---------- the case ----------

    def test_a_case_is_its_company_its_events_and_its_stream(self):
        first = self.save()
        again = self.save(ILLUSTRATION[:3], propositions=(), change_note="Without the subcontract")
        self.assertEqual((again["case_id"], again["revision"]), (first["case_id"], 2))
        self.assertIn("Revised the saved case", again["warnings"][0])
        with self.assertRaises(tools.ToolError) as e:
            tools.save_case(case(case_id=first["case_id"], events=["subcontract-won"]), [])
        self.assertIn("names a different company, events or stream_key", str(e.exception))

    def test_an_assumption_opens_to_what_it_rests_on(self):
        rests = {"note": "The agency's proposed budget funds the program at 70% of this year's.",
                 "evidence": [{"observation_id": "obs_report", "passage": "would fund the program at 70% of"}],
                 "results": ["wk_programs"]}
        values = copy.deepcopy(ILLUSTRATION)
        values[1]["values"]["share_kept"]["assumption"] = rests
        out = self.save(values)
        seen = tools.lookup(out["case_id"])
        stored = seen["case"]["given_values"][1]["values"]["share_kept"]["assumption"]["evidence"][0]["passage"]
        self.assertEqual(stored, "The agency's proposed budget would fund the program at 70% of its current level.")
        self.assertIn("given_values[1].values.share_kept", [e["at"] for e in seen["evidence"]])
        for bad, said in (({**rests, "evidence": [{"observation_id": "obs_report", "passage": "would fund the program at 40% of"}]},
                           "passage not found word for word"), ({**rests, "results": ["wk_missing"]}, "no saved result 'wk_missing'")):
            values[1]["values"]["share_kept"]["assumption"] = bad
            with self.subTest(said=said):
                self.refused(said, given_values=values)

    def test_a_figure_is_read_from_its_record_and_carried_by_an_assumption(self):
        cell = {"result_id": "wk_programs", "column": "revenue", "row": {"company": "Northfield", "program": "transit"}}
        carried = {"note": "The result holds fiscal 2026; the program's terms are unchanged, so the figure is carried into the period.",
                   "evidence": [{"observation_id": "obs_report", "passage": "largest program is the Regional Transit Program"}]}
        inputs = case()["inputs"] | {"program_revenue": {"label": LABEL, "unit": "USD", "result": cell, "assumption": carried}}
        out = self.save(inputs=inputs)
        self.assertEqual(out["results"]["baseline_revenue"], 100)                      # read by the store, not typed
        seen = tools.lookup(out["case_id"])
        kept = seen["case"]["inputs"]["program_revenue"]
        self.assertEqual((kept["value"], kept["result"], kept["assumption"]["note"]), (100, cell, carried["note"]))
        self.assertIn("inputs.program_revenue", [e["at"] for e in seen["evidence"]])     # the passage under the assumption
        self.assertEqual(out["workspace_results_used"], ["wk_programs"])
        with closing(db.connect()) as con:
            self.assertIn("| `program_revenue` | $100 | read from a result, carried by an assumption | wk_programs, column revenue, "
                          "row company Northfield, program transit The result holds fiscal 2026;", briefs.memo(briefs.cases(con, [out["case_id"]])[0]))
        again = tools.save_case(seen["case"] | {"change_note": "As it was"}, [])        # the case as lookup returns it
        self.assertEqual((again["revision"], again["changed"]), (1, False))
        # a figure worked out from the cell's is another figure: the store says so
        self.refused("the value given is 103", "holds 100", inputs=inputs | {"program_revenue": {"label": LABEL, "unit": "USD", "value": 103, "result": cell, "assumption": carried}})
        # a figure is read in one place, and has a basis
        both = {"label": LABEL, "unit": "USD", "value": 100, "result": cell, "source": {"observation_id": "obs_report", "passage": "recognized $100 of revenue under the"}}
        self.refused("a figure is read in one place", inputs=inputs | {"program_revenue": both})
        self.refused("give the basis", inputs=inputs | {"program_revenue": {"label": LABEL, "unit": "USD", "value": 100}})
        self.refused("passage not found word for word", inputs=inputs | {"program_revenue": {"label": LABEL, "unit": "USD", "result": cell, "assumption": {
            **carried, "evidence": [{"observation_id": "obs_report", "passage": "largest program is the Regional Rail Program"}]}}})

    def test_a_given_value_has_the_same_basis_as_an_input(self):
        rate = {"result_id": "wk_rates", "column": "share_kept", "row": {"outcome": "cut under half"}}
        like = {"note": "The agency's last cut of this size left contractors this share of their work; the programs are alike in how work is let."}
        terms = {"observation_id": "obs_report", "passage": "would fund the program at 70% of"}
        values = [given({"share_kept": 0}, not_=["program-renewal"]),
                  given({"share_kept": {"result": rate, "assumption": like}}, "cut-under-half"),          # read by query, carried
                  given({"share_kept": {"value": 0.7, "source": terms}}, "cut-half-or-more")]            # stated for the outcome by a record
        out = self.save(values, propositions=[program()])
        self.assertEqual(self.worlds(out)[:3], [("Renewed in full", 100, 0), ("Cut by less than half", 65, 35), ("Cut by half or more", 70, 30)])
        seen = tools.lookup(out["case_id"])
        kept = seen["case"]["given_values"]
        self.assertEqual((kept[1]["values"]["share_kept"]["value"], kept[1]["values"]["share_kept"]["result"]), (0.65, rate))
        self.assertEqual(kept[2]["values"]["share_kept"]["source"]["passage"], "The agency's proposed budget would fund the program at 70% of its current level.")
        self.assertEqual(tools.lookup("program-renewal")["cases"][0]["given_values"][1]["values"], {"share_kept": 0.65})
        with closing(db.connect()) as con:
            memo = briefs.memo(briefs.cases(con, [out["case_id"]])[0])
        self.assertIn("- Given Cut by less than half: `share_kept` = 0.65 share of this year's funding (read from a result, carried by "
                      "an assumption). wk_rates, column share_kept, row outcome cut under half The agency's last cut", memo)
        self.assertIn("- Given Cut by half or more: `share_kept` = 0.7 share of this year's funding (reported). \"The agency's proposed", memo)
        draft = {k: v for k, v in case(values).items() if k in ("events", "revenue", "inputs", "given_values", "denominator")}
        self.assertEqual(tools.calculate_exposure(draft, [])["results"]["worlds"][1]["revenue"], 65)   # a draft reads the cell too
        values[1]["values"]["share_kept"] = {"value": 0.7, "result": rate, "assumption": like}
        self.refused("given_values[1].values.share_kept: the value given is 0.7", "holds 0.65", given_values=values, propositions=[program()])
        values[1]["values"]["share_kept"] = {"value": 0.7}
        self.refused("give the basis", given_values=values, propositions=[program()])

    def test_an_observed_value_is_the_passage_that_states_it_for_the_period(self):
        stated = {"label": LABEL, "unit": "USD", "value": 100, "source": {"observation_id": "obs_report", "passage": "fee for the program year beginning July 1, 2027"}}
        out = self.save(inputs=case()["inputs"] | {"program_revenue": stated})
        seen = tools.lookup(out["case_id"])
        self.assertEqual(seen["case"]["inputs"]["program_revenue"]["source"]["passage"],
                         "The Company's contract fixes its fee for the program year beginning July 1, 2027 at $100.")
        with closing(db.connect()) as con:
            self.assertIn("| `program_revenue` | $100 | reported | \"The Company's contract fixes its fee",
                          briefs.memo(briefs.cases(con, [out["case_id"]])[0]))
        stated["source"]["passage"] = "fee for the program year beginning July 1, 2028"
        self.refused("passage not found word for word", inputs=case()["inputs"] | {"program_revenue": stated})

    def test_a_draft_is_calculated_and_nothing_is_saved(self):
        draft = {k: v for k, v in case().items() if k in ("events", "revenue", "inputs", "given_values", "denominator")}
        out = tools.calculate_exposure(draft, [program(), SUBCONTRACT])
        self.assertEqual(len(out["results"]["worlds"]), 5)
        with closing(db.connect()) as con:
            self.assertEqual((db.propositions(con), db.cases(con)), ({}, []))

    def test_the_memo_reads_the_case_and_its_propositions(self):
        cid = self.save()["case_id"]
        with closing(db.connect()) as con:
            memo = briefs.memo(briefs.cases(con, [cid])[0])
        self.assertIn("**The agency renews the program.** Decided on or by 2027-06-30.", memo)
        self.assertIn('Revenue at stake: $100 (100.0% of program revenue)', memo)
        self.assertIn('in the world "Not: The agency renews the program; Not: Subcontract won"', memo)
        self.assertIn("| baseline | $100 |", memo)
        self.assertIn("| Cut by less than half | $70 | $30 | 30.0% |", memo)
        self.assertIn("  - **Cut by less than half**: Cut by less than half.", memo)
        self.assertIn("- Given Subcontract won: `share_kept` = 0.5 share of this year's funding (assumed). Stated for the test", memo)
        self.assertIn("- Given not: The agency renews the program: `share_kept` = 0 share", memo)
        self.assertIn("## The person responsible\n\nNot named: Runs the company's finances.", memo)
        self.assertIn("- revenue = `program_revenue * share_kept`", memo)
        self.assertIn('| `program_revenue` | $100 | reported, carried by an assumption | "In fiscal 2026 the Company recognized '
                      '$100 of revenue under the program." (https://example.org/annual-report) Fiscal 2026 is the last year', memo)

    def test_what_the_analyst_reads_is_what_the_store_does(self):
        text = agent.system()
        self.assertTrue(text.startswith("You are an economist and a forensic accountant.\n"))
        self.assertNotIn("{", text)                                                    # every placeholder is filled
        self.assertTrue(agent.system(role="an underwriter").startswith("You are an underwriter.\n"))
        # its illustration, computed by the store: event E with ways A and B, and R, which implies B
        P = lambda key, title, **more: claim(key, title, **more)  # noqa: E731
        event = P("event-e", "E", ways=[P("way-a", "A"), P("way-b", "B")])
        follows = P("claim-r", "R", kind="hitting", window_end="2028-06-30", implies=["way-b"])
        plain = lambda v, unit: {"label": LABEL, "unit": unit, "value": v, "assumption": dict(A)}  # noqa: E731
        out = tools.calculate_exposure({
            "events": ["event-e"], "revenue": "volume * price * (1 - lost * (1 - regained))", "denominator": None,
            "inputs": {"volume": plain(100, "units"), "price": plain(2, "USD"), "lost": plain(0, "share"), "regained": plain(0.5, "share")},
            "given_values": [given({"lost": 0.2}, "event-e"), given({"lost": 1}, "way-b"), given({"regained": 0.8}, "claim-r")]},
            [event, follows])["results"]
        self.assertEqual(out["baseline_revenue"], 200)
        got = {w["label"]: (round(w["revenue"]), round(w["shortfall"])) for w in out["worlds"]}
        self.assertEqual(got, {"Not: E": (200, 0), "A": (180, 20), "B; Not: R": (100, 100), "B; R": (160, 40)})
        said = {"not E": (200, 0), "A": (180, 20), "B, not R": (100, 100), "B, R": (160, 40)}
        for line in text.split("worlds  ", 1)[1].split("\n\n")[0].splitlines():
            world, _, rest = line.strip().partition("revenue")
            figures = tuple(int(x) for x in rest.replace("shortfall", "").split())
            self.assertEqual(said.pop(world.strip()), figures, line)
        self.assertEqual(said, {})                                                    # each of its worlds is in the text
        self.assertIn("baseline 200", text)
        # the sensitivity of the shortfall, worked by hand on the same case: a tenth of each input's largest value
        moves = [(r["figure"], r["value"], round(r["moved_by"], 2), round(r["shortfall_moves_by"], 2), r["in_world"]) for r in out["sensitivity"]]
        self.assertEqual(moves, [("inputs.regained", 0.5, 0.08, 16, "B; Not: R"), ("given_values[2].values.regained", 0.8, 0.08, 16, "B; R"),
                                 ("inputs.lost", 0, 0.1, 10, "A"), ("inputs.price", 2, 0.2, 10, "B; Not: R"),
                                 ("inputs.volume", 100, 10, 10, "B; Not: R"), ("given_values[0].values.lost", 0.2, 0.1, 10, "A"),
                                 ("given_values[1].values.lost", 1, 0.1, 10, "B; Not: R")])

    def test_each_figure_comes_back_with_its_basis_and_what_it_moves(self):
        fee = {"label": LABEL, "unit": "USD", "value": 0, "assumption": dict(A)}                       # an input that is nothing anywhere
        size = {"label": LABEL, "unit": "USD", "value": 400, "assumption": dict(A)}                    # an input only the denominator uses
        out = self.save(inputs=case()["inputs"] | {"late_fee": fee, "company_revenue": size}, revenue="program_revenue * share_kept - late_fee",
                        denominator={"label": "company revenue", "formula": "company_revenue"})
        table = {r["figure"]: r for r in out["results"]["sensitivity"]}
        self.assertNotIn("inputs.late_fee", table)
        self.assertEqual((table["inputs.company_revenue"]["shortfall_moves_by"], table["inputs.company_revenue"]["in_world"]), (0, None))
        self.assertEqual(table["inputs.program_revenue"]["basis"], "read, carried by an assumption")
        self.assertEqual(table["inputs.share_kept"]["basis"], "assumed")
        top = out["results"]["sensitivity"][0]                                        # with no program revenue kept, all of it is the shortfall
        self.assertEqual((top["figure"], round(top["shortfall_moves_by"], 6), top["in_world"]),
                         ("inputs.program_revenue", 10, "Not: The agency renews the program; Not: Subcontract won"))
        self.assertEqual(out["propositions_without_evidence"], out["results"]["reads"])   # the fixture's claims cite nothing
        with closing(db.connect()) as con:
            memo = briefs.memo(briefs.cases(con, [out["case_id"]])[0])
        self.assertIn("| `program_revenue` | $100 | read, carried by an assumption | $10 | $10 | Not: The agency renews the program; Not: Subcontract won |", memo)
        self.assertIn("| `share_kept`, given Cut by less than half | 0.7 share of this year's funding | assumed | 0.1 share of this year's funding | $10 | Cut by less than half |", memo)

    # ---------- changing part of a saved case ----------

    def revise(self, cid, propositions=(), note="A change", **changes):
        return tools.revise_case(cid, changes | {"change_note": note}, copy.deepcopy(list(propositions)))

    def figures(self, out):
        b = out["before_and_after"]
        return {w["world"]: (w["revenue"], w["shortfall"]) for w in b["worlds"]}

    def test_one_value_changed_returns_each_figure_before_and_after(self):
        cid = self.save()["case_id"]
        out = self.revise(cid, note="The agency's draft budget keeps 60%", given_values=[given({"share_kept": 0.6}, "cut-under-half")])
        self.assertEqual((out["case_id"], out["revision"], out["changed"]), (cid, 2, True))
        self.assertEqual(self.figures(out)["Cut by less than half"], ([70, 60], [30, 40]))
        self.assertEqual(self.figures(out)["Cut by half or more"], ([40, 40], [60, 60]))
        self.assertEqual((out["before_and_after"]["baseline_revenue"], out["before_and_after"]["revenue_at_stake"]), ([100, 100], [100, 100]))
        seen = tools.lookup(cid)
        self.assertEqual(seen["revisions"][-1]["change_note"], "The agency's draft budget keeps 60%")
        self.assertEqual(len(seen["case"]["given_values"]), 4)                          # the other given values stand
        self.assertEqual(seen["case"]["person"]["role"], "Runs the company's finances")
        same = self.revise(cid, given_values=[given({"share_kept": 0.6}, "cut-under-half")])
        self.assertEqual((same["revision"], same["changed"]), (2, False))                 # nothing changed, nothing written

    def test_an_input_changed_moves_the_baseline_and_every_world(self):
        cid = self.save()["case_id"]
        larger = {"label": LABEL, "unit": "USD", "value": 120, "assumption": {"note": "The 2027 contract raises the fee by a fifth over fiscal 2026."}}
        out = self.revise(cid, inputs={"program_revenue": larger})
        self.assertEqual(out["before_and_after"]["baseline_revenue"], [100, 120])
        self.assertEqual(self.figures(out)["Cut by less than half"], ([70, 84], [30, 36]))
        self.assertEqual(out["before_and_after"]["revenue_at_stake"], [100, 120])
        # a formula and the input it needs, in one change
        fee = {"label": LABEL, "unit": "USD", "value": 5, "assumption": dict(A)}
        out = self.revise(cid, revenue="program_revenue * share_kept - late_fee", inputs={"late_fee": fee})
        self.assertEqual(out["before_and_after"]["baseline_revenue"], [120, 115])
        self.refused_change(cid, "unknown input 'late_fee'", inputs={"late_fee": None})   # the formula still uses it
        self.assertEqual(tools.lookup(cid)["revision"], 3)

    def refused_change(self, cid, *said, **changes):
        with self.assertRaises(tools.ToolError) as e:
            self.revise(cid, **changes)
        for part in said:
            self.assertIn(part, str(e.exception))

    def test_a_given_value_added_or_removed_changes_the_worlds(self):
        cid = self.save(ILLUSTRATION[:3])["case_id"]                                    # the case without the subcontract
        out = self.revise(cid, given_values=[given({"share_kept": 0.5}, "subcontract-won")])
        got = self.figures(out)
        self.assertEqual(got["Not: The agency renews the program; Subcontract won"], ([None, 50], [None, 50]))
        self.assertEqual(got["Not: The agency renews the program; Not: Subcontract won"], ([None, 0], [None, 100]))
        self.assertEqual(out["before_and_after"]["worlds_removed"], ["Not: The agency renews the program"])
        out = self.revise(cid, given_values=[{"given": ["cut-under-half"], "values": {"share_kept": None}}])
        self.assertEqual(self.figures(out)["Cut by less than half"], ([70, 100], [30, 0]))  # with no given value it stands at the baseline
        self.assertEqual(len(tools.lookup(cid)["case"]["given_values"]), 3)
        # what cannot hold is refused, and the saved case stands
        self.refused_change(cid, 'In the world "Cut by half or more"', "strictly within",
                            given_values=[given({"share_kept": 0.3}, "program-renewal", "cut-half-or-more")])
        self.refused_change("case_000000000000", "no saved case", timing="Sooner.")
        gone = [{"given": g["given"], "given_not": g["given_not"], "values": {"share_kept": None}} for g in tools.lookup(cid)["case"]["given_values"]]
        self.refused_change(cid, "given_values", given_values=gone)                       # a case has a given value
        self.assertEqual(tools.lookup(cid)["revision"], 3)

    def test_a_change_can_restate_a_proposition_and_returns_a_withdrawn_case(self):
        first = self.save()["case_id"]
        other = self.save([given({"share_kept": 0}, not_=["program-renewal"])], company="Lakeshore Transit", propositions=())["case_id"]
        reworded = program()
        reworded["text"] = "The agency's board votes to renew the program by June 30, 2027."
        out = self.revise(first, propositions=[reworded], timing="From the first month of the fiscal year.")
        self.assertEqual((out["revision"], out["also_revised"]), (2, [other]))
        tools.record_finding({"kind": "conclusion", "statement": "The agency has funded the program for two years.",
                              "case_id": first, "withdraw_case": True})
        back = self.revise(first, timing="From July 2027.")
        self.assertEqual(tools.lookup(first)["status"], "active")
        self.assertIn("was withdrawn; saving it returns it to the active queue", " ".join(back["warnings"]))

    def test_figures_read_in_plain_digits(self):
        for (v, unit), said in (((120174241.0, "USD per year"), "$120.2M per year"), ((2496000.0, "USD"), "$2.5M"),
                                ((3327905.0, "enplaned passengers"), "3,327,905 enplaned passengers"),
                                ((9999.9, "passengers"), "10,000 passengers"), ((0.264, "share of passengers"), "0.264 share of passengers"),
                                ((0.05, "share"), "0.05 share"), ((82.9, "USD per square foot"), "$83 per square foot")):
            with self.subTest(v=v, unit=unit):
                self.assertEqual(briefs._shown(v, unit, "USD"), said)

    def test_a_finding_concerns_something_and_a_passage_comes_from_a_stored_record(self):
        self.save()
        with self.assertRaises(tools.ToolError) as e:
            tools.record_finding({"kind": "conclusion", "statement": "Contractors in the region keep about half their work."})
        self.assertIn("a finding concerns a company, a proposition or a case", str(e.exception))
        with self.assertRaises(tools.ToolError) as e:                  # a page the store has not read holds no passage
            tools.record_finding({"kind": "fact", "statement": "The board has set the date of its vote.", "proposition": "program-renewal",
                                  "evidence": [{"url": "https://example.org/never-read", "passage": "the board will vote in June"}]})
        self.assertIn("the store holds no record read from https://example.org/never-read", str(e.exception))
        out = tools.record_finding({"kind": "fact", "statement": "The board votes in June.", "proposition": "program-renewal",
                                    "evidence": [{"url": "https://example.org/minutes", "passage": "will vote on the program in June"}]})
        self.assertEqual(tools.lookup(out["finding_id"])["evidence"][0]["observation_id"], "obs_minutes")   # found by its address

    def test_a_passage_from_a_text_of_unrecorded_layout_stands_as_quoted(self):
        self.save()
        out = tools.record_finding({"kind": "fact", "statement": "The hearing is set for May 4, 2027.", "proposition": "program-renewal",
                                    "evidence": [{"observation_id": "obs_plain", "passage": "hold its hearing on the\nprogram on May 4, 2027"}]})
        self.assertEqual(tools.lookup(out["finding_id"])["evidence"][0]["passage"], "hold its hearing on the program on May 4, 2027")

    def test_a_finding_can_concern_a_proposition_and_withdraw_a_case(self):
        cid = self.save()["case_id"]
        out = tools.record_finding({"kind": "conclusion", "statement": "The agency has funded the program for two years.",
                                    "proposition": "program-renewal", "case_id": cid, "withdraw_case": True})
        self.assertEqual(out["withdrawn"], cid)
        self.assertEqual(tools.lookup(cid)["status"], "withdrawn")
        self.assertEqual(len(tools.lookup("program-renewal")["findings"]), 1)
        with self.assertRaises(tools.ToolError):
            tools.record_finding({"kind": "conclusion", "statement": "The grant is renewed with the program.", "proposition": "grant-renewal"})

    # ---------- an input's label, the export, and the typeset memo ----------

    def test_an_input_has_a_label_and_one_saved_before_labels_reads_by_what_it_states(self):
        bare = case()
        del bare["inputs"]["share_kept"]["label"]
        with self.assertRaises(tools.ToolError) as e:
            tools.save_case(bare, [program(), SUBCONTRACT])
        self.assertIn("inputs.share_kept.label", str(e.exception))
        cid = self.save()["case_id"]
        self.assertEqual(tools.lookup(cid)["case"]["inputs"]["share_kept"]["label"], "Share of that revenue the company keeps")
        with closing(db.connect()) as con, db.write(con):                # the case as one saved before inputs had labels
            snap = db.latest_revision(con, cid)["snapshot"]
            for inp in snap["case"]["inputs"].values():
                del inp["label"]
            con.execute("UPDATE cases SET case_json=? WHERE id=?", (db.dumps(snap["case"]), cid))
            con.execute("UPDATE revisions SET snapshot=? WHERE case_id=?", (db.dumps(snap), cid))
        seen = tools.lookup(cid)["case"]["inputs"]
        self.assertEqual((seen["program_revenue"]["label"], seen["share_kept"]["label"]),
                         ("Program revenue", "Share of this year's funding"))        # its name; its unit, which says more
        cell = {"unit": "USD", "result": {"result_id": "wk_programs", "column": "revenue", "row": {"company": "Northfield", "program": "transit"}}}
        self.assertEqual(forms.label("r", cell, "USD"), "Revenue")                   # the column it is read from
        out = tools.revise_case(cid, {"timing": "From the fiscal year that begins July 1, 2027.", "change_note": "The timing, restated"})
        self.assertEqual(out["revision"], 2)                                         # it still revises, and now carries the labels
        with closing(db.connect()) as con:
            self.assertEqual(db.get_case(con, cid)["case_json"]["inputs"]["program_revenue"]["label"], "Program revenue")

    def memo_case(self):
        """A case with a figure read from a saved query, a figure stated in a record with a date of its own, a
        passage behind an assumption, and a finding that quotes a record the case does not."""
        with closing(db.connect()) as con, db.write(con):
            db.put_observation(con, {"id": "obs_report", "kind": "page", "source": "example.org", "retrieved_at": "2026-11-23",
                                     "published": "2026-09-30", "url": "https://example.org/annual-report", "text": REPORT,
                                     "data": {"title": "Annual report <2026>", "blocks": True}})
        inputs = case()["inputs"]
        inputs["program_revenue"] = {"label": "Revenue under the Regional Transit Program", "unit": "USD", "assumption": {
            "note": "Fiscal 2026 is the last year reported & the terms are unchanged.", "results": ["wk_rates"]},
            "result": {"result_id": "wk_programs", "column": "revenue", "row": {"company": "Northfield", "program": "transit"}}}
        kept = given({"share_kept": {"value": 0.7, "assumption": {"note": "The agency's proposed budget sets the level.", "evidence": [
            {"observation_id": "obs_report", "passage": "would fund the program at 70% of its current level"}]}}}, "cut-under-half")
        out = self.save([given({"share_kept": 0}, not_=["program-renewal"]), kept], inputs=inputs,
                        evidence=[{"observation_id": "obs_report", "passage": "largest program is the Regional Transit Program",
                                   "supports": "The program is the company's largest."}])
        tools.record_finding({"kind": "fact", "statement": "The board votes in June.", "case_id": out["case_id"],
                              "evidence": [{"observation_id": "obs_minutes", "passage": "will vote on the program in June"}]})
        tools.record_finding({"kind": "conclusion", "statement": "The company's paratransit work does not depend on the agency.",
                              "company": out["company_id"]})                # about the company, apart from this case
        with closing(db.connect()) as con:
            return out, briefs.cases(con, [out["case_id"]])[0]

    def test_the_export_carries_each_record_with_its_date_and_each_saved_query(self):
        out, c = self.memo_case()
        ex = briefs.export(c)
        by = {x["observation_id"]: x for x in ex["sources"]}
        self.assertEqual((by["obs_report"]["published"], by["obs_report"]["title"]), ("2026-09-30", "Annual report <2026>"))
        self.assertIn("obs_minutes", by)                                              # the record its finding quotes
        self.assertEqual([(k["result_id"], k["name"], k["sql"], k["rows"][0]) for k in ex["computations"]],
                         [("wk_programs", "program_revenue_by_company", "SELECT 1", ["Northfield", "transit", 100.0]),
                          ("wk_rates", "share_kept_after_past_cuts", "SELECT 1", ["cut under half", 0.65])])

    def test_the_memo_is_the_case_and_nothing_else(self):
        out, c = self.memo_case()
        d = memo.document(c)
        res = out["results"]
        self.assertEqual((d["company"], d["event"], d["period"], d["revision"]),
                         ("Northfield Transit Services", "The agency renews the program", "July 1, 2027 – June 30, 2028", 1))
        self.assertEqual((d["at_stake"]["shortfall"], d["at_stake"]["share"], d["at_stake"]["outcome"]),
                         ("$100", "100.0%", ["Not: The agency renews the program"]))
        self.assertEqual([(w["outcome"], w["revenue"], w["shortfall"]) for w in d["outcomes"]],               # the largest shortfall first
                         [(w["label"].split("; "), briefs.money(w["revenue"]), briefs.money(w["shortfall"]))
                          for w in sorted(res["worlds"], key=lambda w: -w["shortfall"])])
        self.assertEqual([w["shortfall"] for w in d["outcomes"]], ["$100", "$30", "$0", "$0"])
        self.assertEqual(d["outcomes"][1]["changed"], [{"label": "Share of that revenue the company keeps", "value": "0.7 share of this year's funding"}])
        self.assertEqual(d["extra_columns"], [])                                      # a column no outcome fills is not drawn
        self.assertEqual(d["rests"], {"figures": 4, "read": 0, "carried": 1, "assumed": 3})
        self.assertEqual((d["decided"], d["dates"]), ("June 30, 2027", {"as_of": d["dates"]["as_of"], "period": ["2027-07-01", "2028-06-30"]}))
        self.assertEqual([f["statement"] for f in d["findings"]], ["The board votes in June."])   # the one recorded on this case
        self.assertNotIn("person", d)                                                 # the desk's, not the reader's
        self.assertEqual(len(d["condition"]), 2)                                      # each outcome that moves revenue, on its own
        self.assertEqual(memo._share(0.0023), "0.23%")                                # a small share is not shown as 0.0%
        self.assertEqual((memo._share(0.033), memo._share(0)), ("3.3%", "0.0%"))
        # each input once: its label, its value as things stand, then its value in each outcome that changes it
        self.assertEqual([(f["name"], f["label"], f["value"], f["kind"]) for f in d["figures"]],
                         [("program_revenue", "Revenue under the Regional Transit Program", "$100", "Computed from records, carried over by an assumption"),
                          ("share_kept", "Share of that revenue the company keeps", "1", "Assumed")])
        self.assertEqual([f["name"] for f in d["changing"]], ["share_kept"])
        self.assertEqual([(x["when"], x["value"]) for x in d["changing"][0]["outcomes"]],
                         [("not: The agency renews the program", "0"), ("Cut by less than half", "0.7")])
        # a record is one note, with its date and address and the passages quoted from it; a saved query is a computation
        report = next(n for n in d["notes"] if n["title"] == "Annual report <2026>")
        self.assertEqual((report["published"], report["read"], report["url"]),
                         ("September 30, 2026", "November 23, 2026", "https://example.org/annual-report"))
        quoted = {q["text"] for n in d["notes"] for q in n["passages"]}
        self.assertEqual(quoted, {"The Company's largest program is the Regional Transit Program.",
                                  "The agency's proposed budget would fund the program at 70% of its current level.",
                                  "The board will vote on the program in June."})
        self.assertEqual([(k["n"], k["id"]) for k in d["computations"]], [(1, "wk_programs"), (2, "wk_rates")])
        self.assertEqual(d["figures"][0]["computations"], [1, 2])
        self.assertEqual(d["figures"][0]["read"], "Read from computation C1: column revenue, row company Northfield, program transit")
        # the page: self-contained, the case's text escaped, each mark pointing at its note
        page = memo.html(d)
        self.assertIn('<svg class="dates"', page)
        self.assertIn("Period revenue is counted over: July 1, 2027 – June 30, 2028", page)
        self.assertNotIn("Sensitivity", page)
        self.assertNotIn("<script", page)
        self.assertNotIn(' src="', page)
        self.assertIn("Annual report &lt;2026&gt;", page)
        self.assertIn("last year reported &amp; the terms are unchanged.", page)
        for n in d["notes"]:
            self.assertIn(f'id="note-{n["n"]}"', page)
        self.assertIn(f'<a href="#note-{report["n"]}">{report["n"]}a</a>', page)      # quoted more than once: lettered
        self.assertIn('<a href="#comp-1">C1</a>', page)
        self.assertEqual(memo.filename(c), "northfield-transit-services--program-renewal")
        # a record's title: what the writing program left is dropped, and a record with none is named by publisher and file
        self.assertEqual(memo._title({"data": {"title": "Microsoft Word - 2025 Exhibit E"}}, "obs_x"), "2025 Exhibit E")
        self.assertEqual(memo._title({"data": {"title": ""}, "source": "www1.hkexnews.hk", "url": "https://www1.hkexnews.hk/a/2026032600626.pdf"}, "obs_x"),
                         "www1.hkexnews.hk · 2026032600626.pdf")

    def test_the_memo_prints_to_a_pdf(self):
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as pw:
                pw.chromium.launch(headless=True).close()
        except Exception as e:                                                        # no headless Chromium on this machine
            self.skipTest(f"headless Chromium is not installed: {type(e).__name__}")
        import pypdf
        _, c = self.memo_case()
        d = memo.document(c)
        path = memo.pdf(memo.html(d), Path(self.folder.name) / "memo.pdf", running="Northfield")
        pages = pypdf.PdfReader(str(path)).pages
        self.assertGreaterEqual(len(pages), 2)                                        # the notes begin a page of their own
        self.assertIn("Northfield Transit Services", pages[0].extract_text())
        self.assertIn("Revenue under the Regional Transit Program", "".join(p.extract_text() for p in pages))

    def test_the_watch_page_opens_a_case_to_its_memo(self):
        import threading
        import urllib.error
        import urllib.request
        from http.server import ThreadingHTTPServer
        out, c = self.memo_case()
        cid = out["case_id"]
        # a case's details carry its memo's address for the live page, and none for a page exported to stand alone
        name = "northfield-transit-services--program-renewal"
        self.assertEqual(watch._case_details([cid], memo_at=watch._served_memo)[cid]["memo"],
                         {"page": f"/cases/{cid}/memo", "pdf": f"/cases/{cid}/memo.pdf", "name": name + ".pdf"})
        shown = watch._case_details([cid])[cid]
        self.assertIsNone(shown["memo"])                                                # a page exported as one file
        self.assertEqual([x["name"] for x in shown["inputs"]][:2],                      # an input by its label, as in the memo
                         ["Revenue under the Regional Transit Program", "Share of that revenue the company keeps"])
        self.assertEqual(watch.title_label("lmm-passenger-revenue", "revenue at LMM"), "LMM Passenger Revenue")
        self.assertEqual(watch.title_label("lmm-passenger-revenue"), "Lmm Passenger Revenue")
        beside = lambda c: {"page": f"memos/{memo.filename(c)}.html"}  # noqa: E731     # the guide: files beside the page
        self.assertEqual(watch._case_details([cid], public=True, memo_at=beside)[cid]["memo"], {"page": f"memos/{name}.html"})
        server = ThreadingHTTPServer(("127.0.0.1", 0), watch.Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        at = f"http://127.0.0.1:{server.server_address[1]}"
        get = lambda path: urllib.request.urlopen(at + path, timeout=30)  # noqa: E731
        listing = get("/cases").read().decode()
        self.assertIn("Northfield Transit Services", listing)
        self.assertIn(f"href='/cases/{cid}/memo'", listing)
        self.assertIn(f"href='/cases/{cid}/memo.pdf' download='northfield-transit-services--program-renewal.pdf'", listing)
        self.assertIn("<a href='/cases' class=on>Cases</a>", listing)
        self.assertIn("<a href='/cases'>Cases</a>", get("/").read().decode())           # the runs list leads to it
        page = get(f"/cases/{cid}/memo").read().decode()
        self.assertIn('<nav class="bar">', page)
        self.assertIn('<a class="back" href="/cases">‹ Cases</a>', page)               # no run of this store saved it
        self.assertIn(f'href="/cases/{cid}/memo.pdf" download="northfield-transit-services--program-renewal.pdf"', page)
        self.assertNotIn('<nav class="bar">', memo.html(memo.document(c)))             # the page that is printed has no bar
        with closing(db.connect()) as con:                                             # a run that saved it: the way back is the case
            db.start_run(con, "run_abc", "company", "Northfield", None, "model", "log", None, "system", "prompt")
            with db.write(con):
                con.execute("UPDATE revisions SET run_id='run_abc' WHERE case_id=?", (cid,))
        self.assertIn(f'<a class="back" href="/runs/run_abc#case/{cid}">‹ Case</a>', get(f"/cases/{cid}/memo").read().decode())
        for missing in ("/cases/case_nope/memo", "/cases/case_nope/memo.pdf", f"/cases/{cid}/other"):
            with self.assertRaises(urllib.error.HTTPError) as e:
                get(missing)
            self.assertEqual(e.exception.code, 404)
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as pw:
                pw.chromium.launch(headless=True).close()
        except Exception as e:                                                         # no headless Chromium on this machine
            self.skipTest(f"headless Chromium is not installed: {type(e).__name__}")
        from distress_signals import showcase                                          # the guide's memos, written beside its page
        folder = Path(self.folder.name) / "guide"
        self.assertEqual(showcase.write_memos(folder, [c]), [name])
        beside = (folder / "memos" / f"{name}.html").read_text()
        self.assertIn(f'<a class="back" href="../index.html#case/{cid}">‹ Case</a>', beside)
        self.assertIn(f'href="{name}.pdf" download="{name}.pdf"', beside)
        self.assertTrue((folder / "memos" / f"{name}.pdf").read_bytes().startswith(b"%PDF"))
        got = get(f"/cases/{cid}/memo.pdf")
        self.assertEqual((got.headers["Content-Type"], got.headers["Content-Disposition"]),
                         ("application/pdf", 'inline; filename="northfield-transit-services--program-renewal.pdf"'))
        first = got.read()
        self.assertTrue(first.startswith(b"%PDF"))
        self.assertIs(watch.memo_pdf(cid)[0], watch.memo_pdf(cid)[0])                   # kept until the case changes
        tools.revise_case(cid, {"timing": "From the fiscal year that begins July 1, 2027.", "change_note": "The timing, restated"})
        self.assertEqual([k[1] for k in watch._PDF if k[0] == cid], [1])               # the kept one is of revision 1
        watch.memo_pdf(cid)
        self.assertEqual([k[1] for k in watch._PDF if k[0] == cid], [2])               # printed again from revision 2


if __name__ == "__main__":
    unittest.main()
