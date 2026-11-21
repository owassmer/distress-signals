import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from distress_signals.sources import federal_contracts as records
from distress_signals.util import SourceError


class FederalContractTests(unittest.TestCase):
    def test_csv_search_read_and_notice_role(self):
        csv = (b"NoticeId,Title,Sol#,Type,PostedDate,ResponseDeadLine,AwardNumber,Awardee,Description\n"
               b"id1,Maintenance,S1,Solicitation,2026-11-19,2026-12-01T12:00:00-05:00,,,Submit by noon.\n"
               b"id2,Maintenance,S1,Award Notice,2026-11-20,,P2,Vendor LLC,Maintenance \x97 award\n")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "notices.csv"
            path.write_bytes(csv)
            with patch.object(records, "_csv_path", return_value=path):
                found = records.search("sam_opportunities", "Maintenance", collection="active_file",
                                       deadline_from="2026-11-30", deadline_until="2026-12-01")
                award = records.read("sam_opportunities", "extract/id2")
            self.assertEqual(found["total_matches"], 1)
            self.assertEqual(found["results"][0]["awardee_as_published"], "")
            self.assertEqual(award["data"]["award_number"], "P2")
            self.assertIn("Maintenance — award", award["text"])

    def test_cache_revalidates_and_preserves_existing_copy_after_failure(self):
        header = (",".join(sorted(records.SAM_HEADERS)) + "\n").encode()
        with tempfile.TemporaryDirectory() as folder, patch.object(records, "OUT", Path(folder)), patch.object(records, "session") as session:
            def response(code, chunks=()):
                obj = MagicMock(status_code=code, headers={"Last-Modified": "Fri, 20 Nov 2026 04:30:19 GMT"})
                obj.iter_content.return_value = iter(chunks)
                obj.__enter__.return_value = obj
                return obj
            session.return_value.get.side_effect = [response(200, [header]), response(304), response(200, [b"bad"])]
            path = records._csv_path()
            self.assertEqual(records._csv_path(), path)
            self.assertEqual(session.return_value.get.call_args.kwargs["headers"]["If-Modified-Since"],
                             "Fri, 20 Nov 2026 04:30:19 GMT")
            with self.assertRaisesRegex(SourceError, "CSV"):
                records._csv_path()
            self.assertEqual(path.read_bytes(), header)

    def test_sam_api_uses_gsas_zero_based_page_and_separate_date_ranges(self):
        with patch.object(records, "_sam_json", return_value={"opportunitiesData": [], "totalRecords": 0}) as api:
            records.search("sam_opportunities", "bridge", collection="api", page=3, limit=10, notice_type="Award Notice",
                           since="2026-10-01", until="2026-10-31", deadline_from="2026-11-01")
        params = api.call_args.args[1]
        self.assertEqual((params["offset"], params["ptype"]), (2, "a"))
        self.assertEqual((params["postedFrom"], params["rdlfrom"]), ("10/01/2026", "11/01/2026"))

    def test_lookup_keeps_distinct_awards_for_same_piid(self):
        results = [{"Award ID": "PIID1", "generated_internal_id": n} for n in ("A", "B")]
        results.append({"Award ID": "PIID12", "generated_internal_id": "X"})
        with patch.object(records.public_records, "_call", return_value={"results": results, "page_metadata": {"hasNext": False}}) as call:
            found = records.search("sam_opportunities", "PIID1", collection="award_lookup")
        self.assertEqual(call.call_args.args[1]["filters"]["award_ids"], ['"PIID1"'])
        self.assertEqual([r["record_id"] for r in found["results"]], ["usaspending:award:A", "usaspending:award:B"])

    def test_keyless_dod_feed_searches_full_announcement_and_preserves_estimate(self):
        rss = (b"<rss><channel><item><title>Contracts for Nov. 20, 2026</title><pubDate>Fri, 20 Nov 2026 16:16:09 -0600</pubDate>"
               b"<link>https://content.govdelivery.com/accounts/USDOD/bulletins/abcd</link></item>"
               b"<item><title>News</title></item></channel></rss>")
        html = (b"<title>Contracts for Nov. 20, 2026</title><span class='dateline'>U.S. Department of War sent this bulletin at "
                b"11/20/2026 05:16 PM EST</span><div id='article'><p>Korte Construction Co. was awarded a contract "
                b"with an estimated completion date of Sept. 26, 2029.</p></div>")
        with patch.object(records, "session") as session:
            session.return_value.get.side_effect = [MagicMock(content=b) for b in (rss, html, html)]
            found = records.search("dod_contracts", "Korte")
            detail = records.read("dod_contracts", found["results"][0]["record_id"].split(":", 1)[1])
        self.assertIn("latest 2 account bulletins", found["scope"])
        self.assertEqual(detail["published"], "2026-11-20")
        self.assertIn("estimated completion date", detail["text"])

    def test_no_key_selects_the_public_file(self):
        with patch.dict(os.environ, {"SAM_API_KEY": ""}), patch.object(records, "_extract_search", return_value={"results": []}) as fetch:
            records.search("sam_opportunities", "bridge")
        fetch.assert_called_once_with("bridge", 1, 25)

    def test_scoped_award_keeps_the_recipient_in_the_award_record(self):
        key = "CONT_AWD_A1_12C2_-NONE-_-NONE-"
        d = {"piid": "A1", "generated_unique_award_id": key, "recipient": {"recipient_name": "Award recipient"},
             "date_signed": "2026-02-09", "description": "Original award"}
        with patch.object(records, "http_json", return_value=d):
            found = records.search("sam_opportunities", "A1", collection="award_lookup", agency_code="12C2")
            native = found["results"][0]["record_id"].split(":", 1)[1]
            obs = records.read("sam_opportunities", native)
        self.assertIn("recipient: Award recipient", obs["text"])
        self.assertIn("date_signed: 2026-02-09", obs["text"])
        self.assertIsNone(obs["published"])

    def test_missing_key_explains_public_extract(self):
        with patch.dict(os.environ, {"SAM_API_KEY": ""}):
            with self.assertRaisesRegex(SourceError, "active_file"):
                records.search("sam_opportunities", "bridge", collection="api")


if __name__ == "__main__":
    unittest.main()
