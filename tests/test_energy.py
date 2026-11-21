"""Exercise record identity, publication dates, attachments and state docket coverage offline."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from distress_signals.sources import energy
from distress_signals.util import SourceError


class EnergyRecords(unittest.TestCase):
    def test_ferc_posted_date_and_attachment_identity(self):
        hit = {"acesssionNumber": "20251120-3042", "description": "Letter order",
               "filedDate": "11/19/2025", "postedDate": "11/20/2025", "docketNumbers": ["ER26-1"],
               "transmittals": [{"fileType": "PDF", "fileId": "file-1", "fileName": "document.pdf"},
                                {"fileType": "XLSX", "fileId": "file-2", "fileName": "filing.xlsx"}]}
        response = {"success": True, "totalHits": 2, "searchHits": [hit]}
        with patch.object(energy, "http_json", return_value=response) as http:
            result = energy.search("ferc_elibrary", "order", docket="ER26-1", since="2025-11-19",
                                   until="2025-11-21", limit=1)
            body = http.call_args.kwargs["json_body"]
            self.assertEqual(body["dateSearches"][0], {"dateType": "posted_date", "startDate": "11/19/2025",
                                                      "endDate": "11/21/2025"})
            self.assertEqual(body["docketSearches"][0]["docketNumber"], "ER26-1")
            self.assertEqual(result["next_page"], 2)
            filing = result["results"][0]
            self.assertEqual(filing["files"][0]["record_id"], "ferc_elibrary:20251120-3042/file-1")
            self.assertNotIn("record_id", filing["files"][1])
            observation = energy.read("ferc_elibrary", filing["accession"])
            self.assertEqual(observation["published"], "2025-11-20")
            self.assertEqual(observation["native_id"], "20251120-3042")
            with self.assertRaises(SourceError):
                energy.read("ferc_elibrary", "20251120-3042/unlisted")

    def test_pjm_preserves_prior_withdrawal_for_same_unit(self):
        xml = b"""<Units><Unit><UnitName>Unit 2</UnitName><State>Maryland</State>
            <FuelType>Gas</FuelType><Status>Future Deactivation</Status><TransmissionOwnerZone>BGE</TransmissionOwnerZone>
            <RequestedDeactivationDate>05/01/2027</RequestedDeactivationDate></Unit>
            <Unit><UnitName>Unit 2</UnitName><State>Maryland</State><FuelType>Gas</FuelType>
            <Status>Withdrawn Deactivation</Status><TransmissionOwnerZone>BGE</TransmissionOwnerZone>
            <WithdrawnDeactivationDate>05/01/2021</WithdrawnDeactivationDate></Unit></Units>"""
        with patch.object(energy, "_get", return_value=SimpleNamespace(content=xml)):
            result = energy.search("pjm_deactivations", "Maryland gas", status="Future Deactivation")["results"][0]
            self.assertEqual(len(result["entries"]), 1)
            native = result["record_id"].split(":", 1)[1]
            observation = energy.read("pjm_deactivations", native)
            self.assertEqual(len(observation["data"]["entries"]), 2)
            self.assertIn("WithdrawnDeactivationDate", observation["text"])
            self.assertIsNone(observation["published"])

    def test_docket_links_keep_utility_and_filing_status(self):
        html = b'<p><a href="EL26-003.aspx">EL26-003 - Application by <strong>Black Hills Power</strong></a>' \
               b'<br>Date Filed: 02/19/26 - Pending</p>'
        with patch.object(energy, "_get", return_value=SimpleNamespace(content=html)):
            result = energy.search("utility_dockets", "black hills", state="SD", year=2026, sector="electric")
        row = result["results"][0]
        self.assertEqual(row["record_id"], "https://puc.sd.gov/dockets/Electric/2026/EL26-003.aspx")
        self.assertIn("02/19/26", row["index_entry"])
        self.assertIn("Black Hills Power", row["title"])

    def test_calendar_search_uses_company_on_detail_page(self):
        pages = [b'<input id="beginDate"><a class="day" href="/meetings/meeting/hearing/123">Status</a>',
                 b'<div><h2><a href="/docket/P2026-0549">Status for 26-0549</a></h2>'
                 b'<h3>November 20, 2026 at 10:00 AM</h3><p>Ameren Illinois Company<br>Petition</p></div>']
        with patch.object(energy, "_get", side_effect=[SimpleNamespace(content=p) for p in pages]):
            result = energy.search("utility_dockets", "Ameren", state="IL", week_start="2026-11-20")
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["results"][0]["scheduled"], "November 20, 2026 at 10:00 AM")


if __name__ == "__main__":
    unittest.main()
