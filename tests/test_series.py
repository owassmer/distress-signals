"""The registry of series, offline: the search grammar, what the registry does with a publisher's pages, what a
read stores, and that every declared series builds its table and the views across series. Each series is tried
against its publisher by tests/live_series.py."""
import datetime as dt
import json
import os
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from distress_signals import db, sources, tools, watch
from distress_signals.sources import F, Series, page, web
from distress_signals.util import SourceError

MADE = [{"id": str(n), "url": f"https://example.org/widgets/{n}", "widget_id": str(n), "maker": f"Acme Works {n % 7}",
         "kind": "gear" if n % 2 else "spring", "made_date": (dt.date(2026, 1, 1) + dt.timedelta(days=n)).isoformat(),
         "price": float(n), "parts": [f"part {n}", f"part {n + 1}"]} for n in range(250)]
asked = []


def _search(s):
    """A publisher that applies kind and pages 40 at a time; the registry is left the rest."""
    kinds = s.any("kind")
    rows = [r for r in MADE if not kinds or r["kind"] in kinds]
    at = s.cursor or 0
    asked.append((at, s.size))
    part = rows[at:at + min(s.size, 40)]
    return page(part, at + len(part) if at + len(part) < len(rows) else None, len(rows), "by number")


def _read(key):
    return {**MADE[int(key)], "document": f"Widget {key} was made by Acme Works {int(key) % 7}.", "links": []}


WIDGETS = Series(id="widgets", title="the widget register", record="a widget", creator="widget makers", jurisdiction="federal",
                 fields=(F("widget_id", "identifier"), F("maker", "name"), F("kind", ("gear", "spring")), F("made_date", "date"),
                         F("price", "amount"), F("parts", "text", many=True), F("full_text", "full text")),
                 search=_search, read=_read)


class Registry(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        for p in (patch.object(sources, "_all", {"widgets": WIDGETS}),
                  patch.dict(os.environ, DISTRESS_DB=str(Path(self.folder.name) / "test.db"), DISTRESS_RUN_ID="test")):
            p.start()
            self.addCleanup(p.stop)
        asked.clear()

    def test_a_search_is_checked_against_the_series_fields(self):
        for filters, said in (({"colour": "red"}, "no filter 'colour'"), ({"kind": "cog"}, "gear | spring"),
                              ({"made_date_from": "March 1"}, "YYYY-MM-DD"), ({"price_min": "cheap"}, "is a number")):
            with self.subTest(filters=filters), self.assertRaisesRegex(SourceError, said):
                sources.search("widgets", filters=filters)
        with self.assertRaisesRegex(tools.ToolError, "series is one of"):
            tools.search_records("gadgets")

    def test_the_registry_applies_what_the_publisher_leaves_and_continues_without_repeats(self):
        want = [r["id"] for r in MADE if r["kind"] == "gear" and "3" in r["maker"].split() and r["made_date"] >= "2026-03-01"]
        seen, cursor, calls = [], None, 0
        while True:
            got = sources.search("widgets", filters={"kind": "gear", "maker": "acme 3", "made_date_from": "2026-03-01"}, limit=4, cursor=cursor)
            seen += [r["record_id"].partition(":")[2] for r in got["records"]]
            cursor, calls = got.get("cursor"), calls + 1
            if not cursor:
                break
        self.assertEqual(seen, want)
        self.assertGreater(calls, 1)
        self.assertNotIn("total", got)  # a count is the publisher's only where it applied every filter
        whole = sources.search("widgets", filters={"kind": "gear"}, limit=3)
        self.assertEqual((whole["count"], whole["total"], whole["order"]), (3, 125, "by number"))
        self.assertEqual(sources.search("widgets", "part 249")["records"][0]["widget_id"], "248")

    def test_a_cursor_is_a_position_whatever_size_is_asked_next(self):
        first = sources.search("widgets", limit=3)
        rest = sources.search("widgets", limit=200, cursor=first["cursor"])
        self.assertEqual([r["widget_id"] for r in first["records"] + rest["records"]], [str(n) for n in range(203)])

    def test_search_read_cite_and_read_again(self):
        found = tools.search_records("widgets", filters={"widget_id": "12"})
        with closing(db.connect()) as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM observations").fetchone()[0], 0)
        first = tools.read_record(found["records"][0]["record_id"])
        self.assertIn("made_date: 2026-01-13", first["text"])
        passage = "Widget 12 was made by Acme Works 5."
        finding = tools.record_finding({"kind": "fact", "statement": passage,
                                        "evidence": [{"observation_id": first["observation_id"], "passage": passage}]})
        self.assertEqual(finding["unchecked_passages"], 0)
        with patch.dict(MADE[12], maker="Acme Works, renamed"):
            again = tools.read_record(first["observation_id"], fresh=True)
        self.assertEqual(again["observation_id"], first["observation_id"])
        self.assertIn("maker: Acme Works, renamed", again["text"])
        self.assertEqual(tools.compute('SELECT maker FROM "widgets"')["rows"], [["Acme Works, renamed"]])

    def test_a_result_set_fills_the_series_table_and_the_views(self):
        found = tools.search_records("widgets", filters={"kind": "spring", "price_max": 20}, limit=1)
        read = tools.read_record(found["result_set"])
        self.assertEqual(read["records"], 11)
        self.assertEqual(tools.compute('SELECT COUNT(*), SUM(price) FROM "widgets"')["rows"], [[11, 110.0]])
        self.assertEqual(tools.compute("SELECT COUNT(*), MIN(date), MAX(date) FROM record_dates WHERE series='widgets'")["rows"],
                         [[11, "2026-01-01", "2026-01-21"]])
        self.assertEqual(tools.compute("SELECT COUNT(DISTINCT name) FROM record_names")["rows"], [[7]])

    def test_a_card_over_the_cap_is_refused(self):
        with self.assertRaisesRegex(ValueError, "at most"):
            Series(id="long", title="long", record="x" * sources.CARD_CHARS, creator="c", jurisdiction="j", fields=(),
                   search=_search, read=_read)

    def test_the_run_page_words_a_search(self):
        args = {"series": "widgets", "query": "brass", "filters": {"kind": "gear", "made_date_from": "2026-03-01"}}
        self.assertEqual(watch.describe("search_records", args)["label"],
                         "Searched the widget register for “brass” (kind gear; made date from 2026-03-01)")


class Catalog(unittest.TestCase):
    def test_every_declared_series_has_its_entry_its_table_and_a_place_in_the_views(self):
        declared = sources.declared()
        self.assertGreaterEqual(len(declared), 60)
        entry = sources.catalog(sorted(declared))
        for series in declared.values():
            self.assertIn(f"- {series.id}: {series.record}", entry)
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, DISTRESS_DB=str(Path(folder) / "test.db"), DISTRESS_RUN_ID="test"):
            for series in declared:
                self.assertEqual(tools.compute(f'SELECT COUNT(*) FROM "{series}"')["rows"], [[0]])
            for view in ("record_dates", "record_names", "record_amounts"):
                self.assertEqual(tools.compute(f"SELECT COUNT(*) FROM {view}")["rows"], [[0]])

    def test_a_run_lists_only_the_series_it_holds_keys_for_in_an_order_of_its_own(self):
        keyed = {i for i, s in sources.declared().items() if s.requires}
        with patch.dict(os.environ, {k: "" for s in sources.declared().values() for k in s.requires}):
            self.assertFalse(keyed & set(sources.available()))
            self.assertEqual(sources.order("run_a"), sources.order("run_a"))
            self.assertNotEqual(sources.order("run_a"), sources.order("run_b"))
            self.assertEqual(sorted(sources.order("run_a")), sorted(sources.available()))


class MsrbDeliveries(unittest.TestCase):
    """A delivery as the MSRB sends it: zip files of XML transactions and PDFs, the latest transaction standing."""

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        p = patch.dict(os.environ, MSRB_PRIMARY_MARKET_DIR=self.folder.name)
        p.start()
        self.addCleanup(p.stop)
        self.n = 0

    def deliver(self, day, status="Publish", file_status="Current", kind="PrimaryMarketDisclosure"):
        import zipfile
        from distress_signals.sources import msrb
        xml = f"""<SubscriptionDataMessage xmlns="http://emma.msrb.org/schemas/subscriptionV3messages/types">
          <Submission SubmissionIdentifier="EP1" SubmissionType="{kind}" SubmissionStatus="{status}"
            SubmissionTransactionDateTime="2026-01-{day:02d}T12:00:00-05:00">
            <Offering><Issue IssuerName="AIRPORT  AUTHORITY" IssuerState="CA" IssueDescription="REVENUE BONDS" IssueClosingDate="2026-02-01">
              <UnderlyingSecurities><Security CUSIP9="111111AA1" MaturityDate="2046-07-01" MaturityPrincipalAmount="5000000"/>
              <Security CUSIP9="111111AB9" MaturityDate="2036-07-01" MaturityPrincipalAmount="2500000"/></UnderlyingSecurities></Issue></Offering>
            <Document DocumentIdentifier="D1" DocumentType="OfficialStatement" DocumentStatus="Current" DocumentPostingDate="2026-01-{day:02d}">
              <UnderlyingFiles><File FileIdentifier="F1" FileStatus="{file_status}" FilePostingDate="2026-01-{day:02d}"/></UnderlyingFiles></Document>
          </Submission></SubscriptionDataMessage>"""
        self.n += 1
        with zipfile.ZipFile(Path(self.folder.name) / f"PM_{self.n}.zip", "w") as z:
            z.writestr(f"PM_DATA/2026/01/{day:02d}/Index Data/EP{self.n}.xml", xml)
            z.writestr(f"PM_DATA/2026/01/{day:02d}/Doc/F1.pdf", b"%PDF-1.4 statement")
        msrb._rows.cache_clear()

    def test_the_latest_transaction_stands_and_a_withdrawal_removes_the_record(self):
        from distress_signals.sources import msrb
        with patch.object(msrb.web, "_pdf", return_value=("[page 1]\nThe airport's revenues", "")):
            self.deliver(1)
            got = sources.search("emma_official_statements", filters={"cusip": "111111AB9"})["records"]
            self.assertEqual(len(got), 1)
            self.assertEqual((got[0]["issuer"], got[0]["principal_amount"], got[0]["final_maturity_date"]),
                             (["AIRPORT AUTHORITY"], 7500000.0, "2046-07-01"))
            self.assertIn("The airport's revenues", sources.read(got[0]["record_id"])["text"])
            self.assertEqual(sources.search("emma_advance_refunding_documents")["records"], [])
            self.deliver(3, status="Withdrawn")
            self.deliver(2)  # an earlier transaction delivered later does not bring it back
            self.assertEqual(sources.search("emma_official_statements")["records"], [])
            with self.assertRaises(SourceError):
                sources.read(got[0]["record_id"])

    def test_a_file_no_longer_current_is_no_longer_a_record(self):
        self.deliver(1)
        self.deliver(2, file_status="Archived")
        self.assertEqual(sources.search("emma_official_statements")["records"], [])


class FailedReads(unittest.TestCase):
    """A read that could not get the record stores nothing; a file that arrived without text is stored as such."""

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        for p in (patch.dict(os.environ, DISTRESS_DB=str(Path(self.folder.name) / "test.db"), DISTRESS_RUN_ID="test"),
                  patch("distress_signals.sources.issuers._documents_of", return_value=[
                      {"CDIACNumber": "2026-0001", "DocumentId": 7, "Issuer": "Airport Authority", "DocumentType": "Official Statement",
                       "SaleDate": "2026-10-01T00:00:00", "PrincipalAmount": 1.0, "DocumentFileName": "os.pdf"}])):
            p.start()
            self.addCleanup(p.stop)

    def stored(self):
        with closing(db.connect()) as con:
            return con.execute("SELECT COUNT(*) FROM observations").fetchone()[0]

    def test_a_failed_download_fails_the_read_and_stores_nothing(self):
        from distress_signals.sources import issuers
        for failing in ("_file", "web"):
            with self.subTest(failing=failing):
                if failing == "_file":
                    p = patch.object(issuers, "_file", side_effect=SourceError("DebtWatch did not answer"))
                else:
                    p = patch.object(issuers.web, "read", side_effect=SourceError("couldn't fetch the file: ReadTimeout"))
                with patch.object(issuers, "_file", return_value="https://example.org/signed"), p, self.assertRaises(tools.ToolError):
                    tools.read_record("california_debt_documents:2026-0001/7")
                self.assertEqual(self.stored(), 0)

    def test_a_file_without_text_is_stored_with_that_stated(self):
        from distress_signals.sources import issuers
        from distress_signals.util import Unreadable
        with patch.object(issuers, "_file", return_value="https://example.org/signed"), \
                patch.object(issuers.web, "read", side_effect=Unreadable("https://example.org/signed is a PDF with no text layer")):
            got = tools.read_record("california_debt_documents:2026-0001/7")
        self.assertIn("could not be extracted: the file is a PDF with no text layer", got["text"])
        self.assertEqual(self.stored(), 1)


class AirportFilings(unittest.TestCase):
    """FAA CATS exports, as the FAA writes them: a title and a date stamp, then the header and a row per filing."""

    def export(self, folder, name, header, *rows):
        path = Path(folder) / name
        lines = ['"Operating and Financial Summary"', '"As of November 21, 2026 2:08:09 PM EST"', ""]
        lines += [",".join(f'"{c}"' for c in r) for r in (header,) + rows]
        path.write_text("\n".join(lines))
        return ((path, 1.0),)

    def test_a_hyphen_is_no_value_and_a_dollar_sign_makes_an_amount(self):
        from distress_signals.sources import faa_cats
        with tempfile.TemporaryDirectory() as folder:
            files = self.export(folder, "127.csv", list(faa_cats.IDENT) + ["Passenger airline landing fees", "Operating Income (Loss)",
                                                                           "Enplanements", "Parking", "Hotel"],
                                ["2025", "OH", "Medium", "CLEVELAND-HOPKINS INTL", "CLE", "12/31/2025", "07/08/2026",
                                 "$1,000", "($75,815)", "4,883,878", "-", "$0"])
            faa_cats._lines.cache_clear()
            with patch.object(faa_cats, "_exports", return_value=files):
                got = {r["line_item"]: (r.get("amount"), r.get("quantity")) for r in
                       sources.search("faa_form_127_line_items", filters={"fiscal_year_min": 2025, "fiscal_year_max": 2025,
                                                                          "hub_size": "Medium"})["records"]}
        self.assertEqual(got, {"Passenger airline landing fees": (1000.0, None), "Operating Income (Loss)": (-75815.0, None),
                               "Enplanements": (None, 4883878.0), "Hotel": (0.0, None)})

    def test_a_payment_reported_as_zero_is_no_payment(self):
        from distress_signals.sources import faa_cats
        with tempfile.TemporaryDirectory() as folder:
            files = self.export(folder, "126.csv", list(faa_cats.PAYEE) + ["Utilities", "Law Enforcement", "Other"],
                                ["2025", "OH", "RICKENBACKER INTERNATIONAL", "LCK", "City of Columbus", "City", "$137,134", "$0", "-"])
            faa_cats._payments.cache_clear()
            with patch.object(faa_cats, "_exports", return_value=files):
                got = sources.search("faa_form_126_payments", filters={"fiscal_year_min": 2025, "fiscal_year_max": 2025})["records"]
        self.assertEqual([(r["payment_type"], r["amount"]) for r in got], [("Utilities", 137134.0)])


class AirlineTraffic(unittest.TestCase):
    def row(self, airline, origin, dest, passengers, cls="F", month=8, freight=0):
        return {"YEAR": "2025", "MONTH": str(month), "AIRLINE_ID": {"WN": "19393", "DL": "19790"}[airline], "UNIQUE_CARRIER": airline,
                "UNIQUE_CARRIER_NAME": {"WN": "Southwest Airlines Co.", "DL": "Delta Air Lines Inc."}[airline], "ORIGIN": origin,
                "ORIGIN_CITY_NAME": "Dallas, TX", "ORIGIN_STATE_ABR": "TX", "DEST": dest, "DEST_CITY_NAME": "Houston, TX",
                "DEST_STATE_ABR": "TX", "PASSENGERS": f"{passengers:.2f}", "FREIGHT": f"{freight:.2f}", "MAIL": "", "DISTANCE": "239.00",
                "CLASS": cls}

    def test_an_airport_s_enplanements_are_the_sum_of_its_markets(self):
        from distress_signals.sources import bts_t100
        rows = [self.row("WN", "DAL", "HOU", 1000), self.row("WN", "DAL", "AUS", 400), self.row("WN", "DAL", "AUS", 100),
                self.row("DL", "DAL", "ATL", 50), self.row("WN", "DAL", "HOU", 0), self.row("WN", "DAL", "ELP", 0, cls="L")]
        with tempfile.TemporaryDirectory() as folder, patch.object(bts_t100, "DB", Path(folder) / "t100.db"), \
                patch.object(bts_t100, "_latest_month", return_value=(2025, 12)), patch.object(bts_t100, "_download", return_value=rows):
            got = sources.search("t100_domestic_enplanements", filters={"year_min": 2025, "year_max": 2025, "airport": "dal"})["records"]
            markets = sources.search("t100_domestic_markets", filters={"year_min": 2025, "year_max": 2025, "airline_id": "19393"})["records"]
            read = sources.read(got[0]["record_id"])
        self.assertEqual([(r["airline"], r["passengers"], r["markets"]) for r in got],
                         [("Southwest Airlines Co.", 1500.0, 2), ("Delta Air Lines Inc.", 50.0, 1)])
        self.assertEqual(sorted((r["dest"], r["passengers"]) for r in markets), [("AUS", 500.0), ("HOU", 1000.0)])
        self.assertIn("DAL Dallas, TX | HOU Houston, TX | Scheduled Passenger/Cargo | 1,000", read["text"])


class SingleAudits(unittest.TestCase):
    def test_the_api_is_sent_each_table_s_own_forms(self):
        from distress_signals.sources import fac
        with patch.dict(os.environ, DATA_GOV_API_KEY="k"), patch.object(fac, "_api", return_value=[]) as api:
            sources.search("fac_single_audits", filters={"going_concern": "yes", "entity_type": "local", "audit_year_min": 2025})
            general = api.call_args.args[1]
            sources.search("fac_federal_awards", filters={"assistance_listing": ["20.106", "20.006"], "major_program": "yes"})
            awards = api.call_args.args[1]
        self.assertEqual((general["is_going_concern_included"], general["entity_type"], general["is_public"]),
                         ("eq.Yes", "eq.local", "eq.true"))
        self.assertIn("audit_year.gte.2025", general["and"])
        self.assertEqual(awards["is_major"], "eq.Y")
        self.assertEqual(awards["or"], "(and(federal_agency_prefix.eq.20,federal_award_extension.eq.106),"
                                       "and(federal_agency_prefix.eq.20,federal_award_extension.eq.006))")


class Citations(unittest.TestCase):
    TEXT = ("Item 2. Results of Operations\n\nNearly all our revenue results from sales in the\nUnited States, but we also "
            "have limited sales of replacement batteries in the U.K. to hospitals enrolled in our study. We had 40 implants "
            "at 24 hospitals.\n\n| Average selling price in ending backlog | $ | 143,191 | $ | 137,634 | 4.0 | %\n"
            "| Total backlog | $ | 2,900.0 | $ | 3,100.0 | (6.5) | %\n\nThe petition names the Company. Commerce will "
            "examine all known producers.")

    def test_a_cited_passage_is_stored_as_the_whole_sentences_it_falls_in(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, DISTRESS_DB=str(Path(folder) / "test.db"),
                                                                    DISTRESS_RUN_ID="test"):
            with closing(db.connect()) as con:
                db.put_observation(con, {"id": "obs_q", "kind": "record", "source": "edgar_filing_documents",
                                         "retrieved_at": "2026-11-21", "url": "https://example.org/10q", "text": self.TEXT})
            cases = {
                "Nearly all our revenue results from sales in the":
                    "Nearly all our revenue results from sales in the United States, but we also have limited sales of "
                    "replacement batteries in the U.K. to hospitals enrolled in our study.",
                "Average selling price in ending backlog | $ | 143,191":
                    "| Average selling price in ending backlog | $ | 143,191 | $ | 137,634 | 4.0 | %",
                "The petition names the Company ... all known producers":
                    "The petition names the Company. Commerce will examine all known producers.",
                "Nearly all our revenue ... We had 40 implants":
                    "Nearly all our revenue results from sales in the United States, but we also have limited sales of "
                    "replacement batteries in the U.K. to hospitals enrolled in our study. We had 40 implants at 24 hospitals.",
                "Nearly all our revenue ... The petition names":
                    "Nearly all our revenue results from sales in the United States, but we also have limited sales of "
                    "replacement batteries in the U.K. to hospitals enrolled in our study. \u2026 The petition names the Company.",
            }
            for given, stored in cases.items():
                with self.subTest(given=given):
                    tools.record_finding({"kind": "fact", "statement": "The filing states this.", "evidence": [{"observation_id": "obs_q", "passage": given}]})
                    with closing(db.connect()) as con:
                        got = json.loads(con.execute("SELECT evidence FROM findings ORDER BY rowid DESC LIMIT 1").fetchone()[0])[0]
                    self.assertEqual(got["passage"], stored)
                    self.assertTrue(tools.quoted(got["passage"], self.TEXT))  # a revision that sends it back is checked the same way

    def test_a_passage_from_rows_is_its_lines_and_a_list_item_gives_up_its_sentences(self):
        # A list item: the segmenter keeps it whole to the end of its paragraph; its sentences are taken apart.
        item = ("Intro sentence here.\n\u2022 Criterion 2: The contractor must stay solvent. In 2020 the group was\n"
                "acquired by a larger company. The organization will continue to operate as a\nsubsidiary of it. "
                "Key leadership remains.\nAfter the list comes this.")
        self.assertEqual(tools.whole_sentences("will continue to operate as a", item),
                         "The organization will continue to operate as a subsidiary of it.")
        self.assertEqual(tools.whole_sentences("The contractor must stay solvent", item),
                         "\u2022 Criterion 2: The contractor must stay solvent.")
        # A record whose document its adapter wrote as rows: the line quoted, whatever punctuation its lines carry.
        with patch.dict(sources._all or sources.declared(), {WIDGETS.id: WIDGETS}):
            form = {"kind": "record", "source": "widgets", "data": {"document_rows": True},
                    "text": "the widget register: widgets:7\nmaker: Acme Works 0\n\nofficer: Joseph H.\ndue_date: 02/16/2027\n"
                            "comment_end: 12/18/2026\nreviewer: Jane Q."}
            self.assertEqual(tools.whole_sentences("due_date: 02/16/2027", form["text"], tools._rows_until(form)), "due_date: 02/16/2027")
            self.assertEqual(tools.whole_sentences("due_date: 02/16/2027", form["text"]),          # the sentence rule alone
                             "officer: Joseph H. due_date: 02/16/2027 comment_end: 12/18/2026 reviewer: Jane Q.")
            # The fields a record's heading is followed by are rows; its document, after the blank line, is prose.
            prose = {"kind": "record", "source": "widgets", "data": {},
                     "text": "the widget register: widgets:7\nmaker: Acme Works 0\nkind: gear\n\nThis widget was made in the\nspring. It is a gear."}
            until = tools._rows_until(prose)
            self.assertEqual(tools.whole_sentences("kind: gear", prose["text"], until), "kind: gear")
            self.assertEqual(tools.whole_sentences("was made in the", prose["text"], until), "This widget was made in the spring.")
            self.assertEqual(tools._rows_until({**prose, "kind": "result_set"}), len(prose["text"]))
        self.assertEqual(tools._rows_until({"kind": "page", "source": "example.org", "data": {}, "text": prose["text"]}), 0)

    def test_characters_that_print_nothing_are_no_part_of_a_passage(self):
        text = "The chief executive told Reuters \u200bin \u200cOctober that the airline was not in \u200btalks with creditors \u2060and had no plans to restructure."
        plain = "told Reuters in October that the airline was not in talks with creditors"
        self.assertTrue(tools.quoted(plain, text))
        kept = tools.whole_sentences(plain, text)
        self.assertEqual(kept, "The chief executive told Reuters in October that the airline was not in talks with creditors and had no plans to restructure.")
        self.assertEqual(web._tidy("talks\u200b with\u2060 creditors"), "talks with creditors")

    def test_a_url_citation_resolves_to_the_record_holding_the_passage(self):
        url = "https://example.org/ratings.zip"
        with tempfile.TemporaryDirectory() as folder, closing(db.connect(Path(folder) / "test.db")) as con:
            cites = []

            def cite(passage):
                return tools._cite(con, {"url": url, "passage": passage}, "evidence", cites)

            self.assertFalse(cite("An earlier public statement.")["checked"])
            for identifier, name in (("obs_a", "First organization."), ("obs_b", "Second organization.")):
                db.put_observation(con, {"id": identifier, "kind": "record", "source": "medicare_star_ratings",
                                         "retrieved_at": "2026-11-20", "url": url,
                                         "text": f"Contract name: {name} Overall contract rating: 4."})
            matched = cite("Contract name: Second organization.")
            self.assertEqual((matched["observation_id"], matched["checked"]), ("obs_b", True))
            with self.assertRaisesRegex(tools.ToolError, "several records"):
                cite("Overall contract rating: 4.")


if __name__ == "__main__":
    unittest.main()
