"""The registry of series, offline: the search grammar, what the registry does with a publisher's pages, what a
read stores, and that every declared series builds its table and the views across series. Each series is tried
against its publisher by tests/live_series.py."""
import datetime as dt
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from distress_signals import db, sources, tools, watch
from distress_signals.sources import F, Series, page
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
        with db.connect() as con:
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
        with db.connect() as con:
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


class Citations(unittest.TestCase):
    def test_a_url_citation_resolves_to_the_record_holding_the_passage(self):
        url = "https://example.org/ratings.zip"
        with tempfile.TemporaryDirectory() as folder, db.connect(Path(folder) / "test.db") as con:
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
