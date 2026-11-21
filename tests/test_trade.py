import os
import unittest
from unittest.mock import Mock, patch

from distress_signals.sources import trade
from distress_signals.util import SourceError


class TradeRecords(unittest.TestCase):
    @patch.object(trade, "_investigations")
    def test_investigation_phase_and_dates(self, source):
        first = {"Investigation ID": 8816, "Case ID": 8313, "Investigation Number": "337-1464",
                 "Full Title": "Certain Vaporizer Devices (Violation)", "Target Date": "02-22-2027",
                 "Date ALJ Issued Target Date Order": {"date": "2026-01-06T12:00:00.000Z", "isNa": False},
                 "Date of Publication of FR Notice (NOI)": {"date": "2025-12-19T12:00:00.000Z", "isNa": False},
                 "Participants": [{"Participant": {"Name": "Glas, LLC"}, "Participant Type": {"name": "Respondent"}}]}
        source.return_value = {"8816": first, "8817": dict(first, **{"Investigation ID": 8817, "Target Date": None})}
        both = trade.search("usitc", "Glas", limit=1)
        self.assertEqual((both["total_matches"], both["next_offset"]), (2, 1))
        self.assertEqual(trade.search("usitc", "Glas", offset=1)["results"][0]["record_id"], "usitc:8817")
        result, = trade.search("usitc", "Glas", target_from="2027-01-01")["results"]
        self.assertEqual(result["target_date"], "2027-02-22")
        self.assertEqual(result["target_date_order_issued"], "2026-01-06")
        record = trade.read("usitc", "8816")
        self.assertIsNone(record["published"])
        self.assertIn('"Target Date": "02-22-2027"', record["text"])
        self.assertEqual(record["data"]["participants"][0]["role"], "Respondent")

    @patch.object(trade, "_get")
    def test_ofac_indexes_distinguish_terms_and_older_documents(self, get):
        get.return_value.text = ('<main><h3>Russia-related sanctions</h3><li><a href="/media/937386/download?inline">'
                                 'General License 131L</a> - Negotiation for the sale of Lukoil '
                                 'International GmbH (November 19, 2026)</li></main>')
        license, = trade.search("ofac", "Lukoil")["results"]
        self.assertIn("November 19, 2026", license["title"])
        self.assertNotIn("expiration", license)
        self.assertEqual(license["record_id"], "https://ofac.treasury.gov/media/937386/download?inline")
        get.return_value.text = ('<h5>Sale of Lukoil International GmbH</h5><a '
                                 'href="/system/files/2025-12/russia_gl131a.pdf">russia_gl131a.pdf</a>'
                                 '<a rel="next" href="?page=1">Next</a>')
        results = trade.search("ofac", "Lukoil", file_page=1)
        self.assertEqual(results["results"][0]["filename"], "russia_gl131a.pdf")
        self.assertEqual(get.call_args.kwargs["params"]["page"], 0)
        self.assertEqual(results["next_page"], 2)
        get.return_value.text = '<div class="view-empty">No Results Found.</div>'
        self.assertEqual(trade.search("ofac", "Lukoil", file_page=2)["results"], [])
        get.return_value.text = '<html>Unavailable</html>'
        with self.assertRaises(SourceError):
            trade.search("ofac", "Lukoil", file_page=1)

    @patch.object(trade, "_get")
    def test_edis_public_record_and_attachment_access(self, get):
        doc = b'<results><documents><document><id>901247</id><securityLevel>Public</securityLevel><documentTitle>Statement</documentTitle><investigationNumber>337-1464</investigationNumber><investigationPhase>Violation</investigationPhase><onBehalfOf>Glas, LLC</onBehalfOf></document></documents></results>'
        files = b'<results><attachments><attachment><documentId>901247</documentId><id>2655847</id></attachment></attachments></results>'
        get.return_value.content = doc
        result = trade.search("usitc", "337-1464", filing_page=1)
        self.assertEqual(get.call_args.kwargs["params"]["securityLevel"], "Public")
        self.assertEqual(result["results"][0]["filed_on_behalf_of"], "Glas, LLC")
        get.side_effect = [Mock(content=doc), Mock(content=files)] * 2
        self.assertIn("usitc:attachment/901247/2655847", trade.read("usitc", "document/901247")["text"])
        with patch.dict(os.environ, {"EDIS_API_TOKEN": ""}), self.assertRaisesRegex(SourceError, "EDIS_API_TOKEN"):
            trade.read("usitc", "attachment/901247/2655847")
        get.side_effect = [Mock(content=doc.replace(b">Public<", b">Confidential<"))]
        with self.assertRaisesRegex(SourceError, "public filing"):
            trade.read("usitc", "attachment/901247/2655847")

    @patch.object(trade, "_get")
    def test_daily_export_revalidates_and_rejects_incomplete_updates(self, get):
        first = Mock(status_code=200, headers={"Last-Modified": "Fri, 20 Nov 2026 23:23:55 GMT"})
        first.json.return_value = {"count": 1, "data": [{"Investigation ID": 1}]}
        bad = Mock(status_code=200)
        bad.json.return_value = {"count": 2, "data": []}
        get.side_effect = [first, Mock(status_code=304), bad]
        with patch.object(trade, "_snapshot", None):
            self.assertIs(trade._investigations(), trade._investigations())
            self.assertEqual(get.call_args.kwargs["headers"]["If-Modified-Since"], first.headers["Last-Modified"])
            with self.assertRaises(SourceError):
                trade._investigations()
