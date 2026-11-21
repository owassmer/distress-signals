import os
import unittest
from unittest.mock import MagicMock, patch

from distress_signals.sources import communications as records
from distress_signals.util import SourceError


class CommunicationsTests(unittest.TestCase):
    def test_ecfs_keeps_filer_dates_and_document_links(self):
        filing = {"id_submission": "101", "date_disseminated": "2026-02-01", "date_comment_period": "2026-03-30",
                  "date_reply_comment": "2026-04-30", "documents": [{"filename": "Notice.pdf", "src": "https://docs.fcc.gov/notice.pdf"}],
                  "filers": [{"name": "Communications Bureau"}], "proceedings": [{"name": "26-1", "description": "Review"}]}
        page = {"filing": [filing, {**filing, "id_submission": "102"}]}
        with patch.object(records, "_fcc_json", side_effect=[page, filing]) as api:
            result = records.search("fcc", "", docket="26-1", limit=1)
            row = result["results"][0]
            self.assertEqual(result["next_offset"], 1)
            self.assertEqual(api.call_args.kwargs["params"]["q"], 'proceedings.name:("26-1")')
            self.assertEqual(row["record_id"], "fcc:101")
            obs = records.read("fcc", "101")
        self.assertEqual(obs["published"], "2026-02-01")
        self.assertIn("comment_due: 2026-03-30", obs["text"])
        self.assertIn("Communications Bureau", obs["text"])
        self.assertEqual(obs["data"]["links"][0]["url"], filing["documents"][0]["src"])

    def test_auction_record_keeps_native_frn_and_round(self):
        raw = b"auction_id,round,bidder,frn\n113,1,Example Wireless,0000123456\n113,2,Example Wireless,0000123456\n"
        with patch.object(records, "session") as session:
            response = session.return_value.get.return_value.__enter__.return_value
            response.raw = MagicMock()
            response.raw.read.return_value = raw
            result = records.search("fcc", "wireless", auction_number=113, report="bidder_status", offset=1, limit=1)
            row = result["results"][0]
            self.assertEqual(row["fields"]["round"], "2")
            self.assertEqual(result["total_matches"], 2)
            obs = records.read("fcc", row["record_id"].split(":", 1)[1])
        self.assertIn("frn: 0000123456", obs["text"])
        self.assertIsNone(obs["published"])

    def test_adams_filters_and_read_use_agency_fields(self):
        doc = {"AccessionNumber": "ML23269A176", "DocumentTitle": "Application review schedule",
               "DateAddedTimestamp": "2023-10-12", "DocumentDate": "2023-10-11",
               "AddresseeAffiliation": ["Kairos Power LLC"], "content": "Estimated completion: March 11, 2024",
               "Url": "https://www.nrc.gov/docs/ML2326/ML23269A176.pdf"}
        with patch.object(records, "_adams_json", side_effect=[{"results": [{"document": doc}], "count": 22},
                                                              {"document": doc}]) as api:
            result = records.search("nrc_adams", "review", organization="Kairos", since="2023-01-01", offset=10)
            body = api.call_args.kwargs["json_body"]
            self.assertEqual(body["filters"][0]["field"], "DateAddedTimestamp")
            self.assertEqual({f["field"] for f in body["anyFilters"]}, {"AuthorAffiliation", "AddresseeAffiliation"})
            self.assertEqual(result["next_offset"], 11)
            obs = records.read("nrc_adams", "ML23269A176")
        self.assertEqual(obs["published"], "2023-10-12")
        self.assertIn("Estimated completion: March 11, 2024", obs["text"])
        self.assertIn("Kairos Power LLC", obs["text"])

    def test_missing_adams_subscription_is_an_error(self):
        with patch.dict(os.environ, {"NRC_ADAMS_API_KEY": ""}):
            with self.assertRaisesRegex(SourceError, "NRC_ADAMS_API_KEY"):
                records.search("nrc_adams", "license renewal")

    def test_unsupported_auction_report_does_not_appear_empty(self):
        with patch.object(records, "session") as session:
            response = session.return_value.get.return_value.__enter__.return_value
            response.raw.read.return_value = b"<!doctype html>\n<html>No report</html>"
            with self.assertRaisesRegex(SourceError, "does not supply"):
                records.search("fcc", "", auction_number=113, report="announcements")


if __name__ == "__main__":
    unittest.main()
