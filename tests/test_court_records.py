import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from distress_signals.sources import court_records as court


class CourtRecordsTests(unittest.TestCase):
    def test_gao_closed_docket_keeps_due_date_and_earlier_disposition(self):
        fields = {"Protester": "Protesting Company", "Agency": "Department", "File number": "B-100000.2",
                  "Outcome": "Dismissed", "Due Date": "2027-03-12", "Decision Date": "2026-12-01"}
        tags = "".join(f'<div class="field"><b class="field__label">{k}</b>'
                       f'<span class="field__item">{v}</span></div>' for k, v in fields.items())
        response = SimpleNamespace(content=f"<main><span>Posted on Dec 01, 2026.</span>{tags}</main>".encode())
        with patch.object(court, "_request", return_value=response):
            hit, = court.search("gao_protests", "b-100000.2")["results"]
            obs = court.read("gao_protests", hit["record_id"].partition(":")[2])
        self.assertEqual(obs["published"], "2026-12-01")
        self.assertEqual(hit["Decision Date"], "2026-12-01")
        self.assertEqual(hit["Outcome"], "Dismissed")
        self.assertEqual(hit["Due Date"], "2027-03-12")
        self.assertNotIn("awardee", obs["data"])
        self.assertIn("Outcome: Dismissed", obs["text"])

    def test_supreme_court_entry_keeps_the_date_the_order_was_made(self):
        docket = {"CaseNumber": "26-101 ", "PetitionerTitle": "A, Petitioner", "RespondentTitle": "B",
                  "DocketedDate": "October 12, 2026", "sJsonCreationDate": "11/20/2026",
                  "ProceedingsandOrder": [{"Date": "Nov 10 2026", "Text": "DISTRIBUTED for Conference of 12/4/2026.",
                                            "Links": [{"Description": "Record", "DocumentUrl": "https://court.example/filing"}]}]}
        with patch.object(court, "_json", return_value=docket):
            hit, = court.search("supreme_court", "26-101")["results"]
            obs = court.read("supreme_court", hit["record_id"].partition(":")[2])
        self.assertEqual(hit["docketed"], "October 12, 2026")
        self.assertIn("Nov 10 2026: DISTRIBUTED for Conference of 12/4/2026.", obs["text"])
        self.assertEqual(obs["data"]["links"][0]["url"], "https://court.example/filing")

    def test_recap_snapshot_records_case_status_parties_and_orders(self):
        ident = "gov.uscourts.test.678"
        index = {"metadata": {"identifier": ident}, "files": [{"name": ident + ".docket.json"}]}
        docket = {"case_name": "A v. B", "date_modified": "2026-11-20", "date_filed": "2026-08-01",
                  "parties": [{"name": "A", "party_types": [{"name": "Plaintiff"}]}], "docket_entries": [
                      {"entry_number": 5, "date_filed": "2026-11-10", "description": "Hearing set for December 4.",
                       "recap_documents": [{"description": "Filed order", "is_available": True,
                                            "filepath_local": "recap/123/123.5.0.pdf"}]}]}
        with patch.object(court, "_json", side_effect=[index, docket]):
            obs = court.read("federal_dockets", ident)
        self.assertIn("A (Plaintiff)", obs["text"])
        self.assertIn("Entry 5 filed 2026-11-10\nHearing set for December 4.", obs["text"])
        self.assertEqual(obs["data"]["archive_snapshot_updated"], "2026-11-20")

    def test_current_recap_item_with_only_a_filing(self):
        native = "gov.uscourts.test.789"
        index = {"metadata": {"identifier": native, "title": "Current matter"}, "files": [
                 {"name": f"{native}.8.0.pdf"}, {"name": f"{native}.10.0.pdf"}]}
        with patch.object(court, "_json", return_value=index), \
             patch.object(court.web, "read", return_value={"text": "Scheduling order"}) as read:
            obs = court.read("federal_dockets", native)
        self.assertEqual(obs["data"]["archived_filings"], 2)
        self.assertIn("Scheduling order", obs["text"])
        self.assertTrue(read.call_args.args[0].endswith(".10.0.pdf"))

    def test_live_search_keeps_cursor_and_stores_docket_id(self):
        batch = {"results": [{"docket_id": 99, "caseName": "A v. B", "docket_absolute_url": "/docket/99/a-v-b/",
                              "recap_documents": [{"entry_date_filed": "2026-10-01", "description": "Order"}]}],
                 "next": "https://www.courtlistener.com/api/rest/v4/search/?cursor=opaque%2Btoken"}
        with patch.dict(os.environ, {"COURTLISTENER_TOKEN": "test"}), \
             patch.object(court, "_live", return_value=batch):
            result = court.search("federal_dockets", "hearing")
        self.assertEqual(result["next_cursor"], "opaque+token")
        self.assertEqual(result["results"][0]["record_id"], "federal_dockets:cl:99")
        self.assertFalse(result["complete"])
