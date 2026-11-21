import os
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import requests

from distress_signals.sources import drug_patent as source
from distress_signals.util import SourceError


class DrugPatentTest(unittest.TestCase):
    @patch.object(source, "http_json")
    def test_protection_window_and_product_level_record_id(self, get):
        get.return_value = {"meta": {"results": {"total": 3}}, "results": [{
            "product_number": "003", "products": [{"brand_name": "JANUMET XR", "application_type": "N",
                                                    "application_number": "202270"}],
            "patents": [{"patent_number": "7326708", "expiration_date": "20261124"}],
            "exclusivity": [{"exclusivity_expiration_date": "20200201"}],
        }]}
        page = source.search("orange_book", "JANUMET XR", expires_from="2026-11-21", expires_until="2027-01-01", limit=1)
        row = page["results"][0]
        terms = get.call_args.kwargs["params"]["search"]
        self.assertIn(" OR exclusivity.exclusivity_expiration_date:", terms)
        self.assertEqual([p["patent_number"] for p in row["protections"]], ["7326708"])
        identity = parse_qs(urlparse(row["url"]).query)["search"][0]
        self.assertIn('product_number:"003"', identity)
        self.assertIn('products.application_number:"202270"', identity)
        self.assertEqual(page["next_offset"], 1)
        with self.assertRaisesRegex(SourceError, "unique record"):
            source.read(*row["record_id"].split(":", 1))
        get.return_value["meta"]["results"]["total"] = 1
        obs = source.read(*row["record_id"].split(":", 1))
        self.assertEqual(obs["native_id"], "N:202270:003")
        self.assertIn("JANUMET XR", obs["data"]["title"])
        self.assertEqual(obs["data"]["patents"][0]["expiration_date"], "20261124")
        get.return_value["results"][0]["product_number"] = "002"
        with self.assertRaisesRegex(SourceError, "different Orange Book product"):
            source.read(*row["record_id"].split(":", 1))

    @patch.object(source, "http_json")
    def test_approval_keeps_historical_action(self, get):
        get.return_value = {"meta": {"results": {"total": 1}}, "results": [{
            "application_number": "ANDA076515", "sponsor_name": "SANDOZ",
            "products": [{"brand_name": "OMEPRAZOLE"}],
            "submissions": [{"submission_status": "AP", "submission_status_date": "20090121"},
                            {"submission_status": "AP", "submission_status_date": "20110104"}],
        }]}
        page = source.search("fda_advisory", "SANDOZ", record_type="approvals")
        row = page["results"][0]
        self.assertEqual(row["latest_recorded_action"]["submission_status_date"], "20110104")
        self.assertIn('application_number:"ANDA076515"', parse_qs(urlparse(row["url"]).query)["search"])
        obs = source.read(*row["record_id"].split(":", 1))
        self.assertEqual(obs["data"]["sponsor_name"], "SANDOZ")
        self.assertEqual(obs["data"]["submissions"][0]["submission_status_date"], "20090121")
        self.assertIsNone(obs["published"])

    @patch.object(source, "http_json")
    def test_calendar_uses_announced_date_and_preserves_cancelled_meetings(self, get):
        title = "Advisory Committee Meeting – Cancelled"
        get.return_value = [{"field_start_date": "10/30/2026 08:30 AM EDT", "field_end_date": "10/30/2026 03:00 PM EDT",
                             "title": f'<a href="/advisory-committees/meeting">{title}</a>',
                             "field_contributing_office": "", "field_center": "Center for Drug Evaluation and Research"},
                            {"field_start_date": "", "field_end_date": "", "title": '<a href="/older">Older item</a>',
                             "field_contributing_office": "", "field_center": ""}]
        page = source.search("fda_advisory", "advisory", since="2026-10-30", until="2026-10-30")
        self.assertEqual(page["total_matches"], 1)
        self.assertEqual(page["results"][0]["title"], title)
        self.assertEqual(page["results"][0]["record_id"], "https://www.fda.gov/advisory-committees/meeting")

    @patch.object(source, "http_json")
    def test_empty_openfda_search_distinct_from_route_failure(self, get):
        response = requests.Response()
        response.status_code = 404
        response._content = b'{"error":{"code":"NOT_FOUND","message":"No matches found!"}}'
        get.side_effect = requests.HTTPError(response=response)
        self.assertEqual(source.search("orange_book", "absent")["results"], [])
        response._content = b"Page not found"
        with self.assertRaises(SourceError):
            source.search("orange_book", "absent")

    @patch.dict(os.environ, {"USPTO_API_KEY": "key"})
    @patch.object(source, "http_json")
    def test_ptab_order_record_can_be_refreshed_with_its_own_identity(self, get):
        doc = {"trialNumber": "IPR2025-00756", "documentData": {"documentIdentifier": "12345",
               "documentTitleText": "Scheduling Order", "documentFilingDate": "2025-12-02",
               "documentOCRText": "DUE DATE 1: March 3, 2026."}}
        get.return_value = {"count": 1, "patentTrialDocumentDataBag": [doc]}
        row = source.search("ptab", "Scheduling", trial_number="IPR2025-00756", record_type="documents")["results"][0]
        self.assertEqual(get.call_args.kwargs["headers"], {"X-API-KEY": "key"})
        self.assertIn('trialNumber:"IPR2025-00756"', get.call_args.kwargs["params"]["q"])
        self.assertNotIn("documentOCRText", row["documentData"])
        observation = source.read(*row["record_id"].split(":", 1))
        self.assertEqual(observation["native_id"], "documents:12345")
        self.assertEqual(observation["published"], "2025-12-02")
        self.assertIn("March 3, 2026", observation["text"])
