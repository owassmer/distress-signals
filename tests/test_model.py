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

from distress_signals import agent, briefs, db, tools, worlds

REPORT = ("The Company's largest program is the Regional Transit Program.\nIn fiscal 2026 the Company recognized $100 of "
          "revenue under the\nprogram. The agency's proposed budget would fund the program at 70% of\nits current level. "
          "The Company's contract fixes its\nfee for the program year beginning July 1, 2027 at $100.")
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
            "inputs": {"program_revenue": {"unit": "USD", "value": 100,
                                           "source": {"observation_id": "obs_report", "passage": "recognized $100 of revenue under the"},
                                           "assumption": {"note": "Fiscal 2026 is the last year reported, and the program's terms "
                                                                  "are unchanged, so its revenue is carried into the period."}},
                       "share_kept": {"unit": "share of this year's funding", "value": 1, "assumption": dict(A)}},
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
                                     "url": "https://example.org/annual-report", "text": REPORT})
            db.put_observation(con, {"id": "obs_minutes", "kind": "page", "source": "example.org", "retrieved_at": "2026-11-23",
                                     "url": "https://example.org/minutes", "text": MINUTES, "data": {"title": "Board minutes"}})
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
                        inputs=case()["inputs"] | {"late_fee": {"unit": "USD", "value": 0, "assumption": dict(A)}})
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
        inputs = {k: {"unit": "USD", "value": v, "assumption": dict(A)} for k, v in (("r", 100), ("a", 0), ("b", 0))}
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
        inputs = case()["inputs"] | {"program_revenue": {"unit": "USD", "result": cell}}
        out = self.save(inputs=inputs)
        self.assertEqual(out["results"]["baseline_revenue"], 100)                      # no number was typed
        self.assertEqual(tools.lookup(out["case_id"])["case"]["inputs"]["program_revenue"]["value"], 100)
        self.assertEqual(out["workspace_results_used"], ["wk_programs"])
        for row, said in (({"company": "Northfield"}, "picks out 2 of the 3 rows"), ({"company": "Nowhere"}, "picks out 0 of"),
                          ({"region": "east"}, "no column 'region'")):
            with self.subTest(row=row):
                self.refused(said, inputs=inputs | {"program_revenue": {"unit": "USD", "result": {**cell, "row": row}}})
        self.refused("holds 100", inputs=inputs | {"program_revenue": {"unit": "USD", "value": 120, "result": cell}})

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
        inputs = case()["inputs"] | {"program_revenue": {"unit": "USD", "result": cell, "assumption": carried}}
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
        self.refused("the value given is 103", "holds 100", inputs=inputs | {"program_revenue": {"unit": "USD", "value": 103, "result": cell, "assumption": carried}})
        # a figure is read in one place, and has a basis
        both = {"unit": "USD", "value": 100, "result": cell, "source": {"observation_id": "obs_report", "passage": "recognized $100 of revenue under the"}}
        self.refused("a figure is read in one place", inputs=inputs | {"program_revenue": both})
        self.refused("give the basis", inputs=inputs | {"program_revenue": {"unit": "USD", "value": 100}})
        self.refused("passage not found word for word", inputs=inputs | {"program_revenue": {"unit": "USD", "result": cell, "assumption": {
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
        stated = {"unit": "USD", "value": 100, "source": {"observation_id": "obs_report", "passage": "fee for the program year beginning July 1, 2027"}}
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
        self.assertTrue(text.startswith("You are an underwriter and a forensic accountant.\n"))
        self.assertNotIn("{", text)                                                    # every placeholder is filled
        self.assertTrue(agent.system(role="an underwriter").startswith("You are an underwriter.\n"))
        # its illustration, computed by the store: event E with ways A and B, and R, which implies B
        P = lambda key, title, **more: claim(key, title, **more)  # noqa: E731
        event = P("event-e", "E", ways=[P("way-a", "A"), P("way-b", "B")])
        follows = P("claim-r", "R", kind="hitting", window_end="2028-06-30", implies=["way-b"])
        plain = lambda v, unit: {"unit": unit, "value": v, "assumption": dict(A)}  # noqa: E731
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

    def test_figures_read_in_plain_digits(self):
        for (v, unit), said in (((120174241.0, "USD per year"), "$120.2M per year"), ((2496000.0, "USD"), "$2.5M"),
                                ((3327905.0, "enplaned passengers"), "3,327,905 enplaned passengers"),
                                ((9999.9, "passengers"), "10,000 passengers"), ((0.264, "share of passengers"), "0.264 share of passengers"),
                                ((0.05, "share"), "0.05 share"), ((82.9, "USD per square foot"), "$83 per square foot")):
            with self.subTest(v=v, unit=unit):
                self.assertEqual(briefs._shown(v, unit, "USD"), said)

    def test_a_finding_can_concern_a_proposition_and_withdraw_a_case(self):
        cid = self.save()["case_id"]
        out = tools.record_finding({"kind": "conclusion", "statement": "The agency has funded the program for two years.",
                                    "proposition": "program-renewal", "case_id": cid, "withdraw_case": True})
        self.assertEqual(out["withdrawn"], cid)
        self.assertEqual(tools.lookup(cid)["status"], "withdrawn")
        self.assertEqual(len(tools.lookup("program-renewal")["findings"]), 1)
        with self.assertRaises(tools.ToolError):
            tools.record_finding({"kind": "conclusion", "statement": "The grant is renewed with the program.", "proposition": "grant-renewal"})


if __name__ == "__main__":
    unittest.main()
