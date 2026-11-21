import unittest
from unittest.mock import patch

from distress_signals.sources import policy_records as p
from distress_signals.util import SourceError


class PolicyRecordTests(unittest.TestCase):
    @patch.object(p, "_json")
    def test_parent_document_object_id_controls_comment_search(self, api):
        api.side_effect = [
            {"data": {"id": "HHS-OCR-2018-0002-0001", "attributes": {"objectId": "0900006482e6be53"}}},
            {"data": [{"id": "HHS-OCR-2018-0002-5313", "attributes": {"title": "Comment"}}],
             "meta": {"totalElements": 1, "hasNextPage": False, "totalPages": 1}},
        ]
        page = p.search("regulations_gov", record_type="comments", comment_on="HHS-OCR-2018-0002-0001")
        self.assertEqual(api.call_args.kwargs["params"]["filter[commentOnId]"], "0900006482e6be53")
        self.assertEqual(page["results"][0]["record_id"], "regulations_gov:comments:HHS-OCR-2018-0002-5313")
        with self.assertRaisesRegex(SourceError, "docket_id"):
            p.search("regulations_gov", record_type="comments", docket_id="HHS-OCR-2018-0002")

    @patch.object(p, "_reg_record")
    def test_comment_links_to_parent_and_docket_without_inventing_a_commenter(self, record):
        record.return_value = {"data": {"id": "HHS-OCR-2018-0002-5313", "attributes": {
            "docketId": "HHS-OCR-2018-0002", "commentOnDocumentId": "HHS-OCR-2018-0002-0001",
            "organization": None, "postedDate": "2018-03-29", "title": "Comment on proposed rule"}}}
        obs = p.read("regulations_gov", "comments:HHS-OCR-2018-0002-5313")
        self.assertNotIn("organization", obs["data"])
        self.assertEqual(obs["data"]["parent_document_record_id"], "regulations_gov:documents:HHS-OCR-2018-0002-0001")
        self.assertEqual(obs["published"], "2018-03-29")

    @patch.object(p, "_get")
    def test_oira_labels_requesting_firm_client_and_scheduled_date(self, get):
        get.return_value = b"""<h1>View Scheduled EO 12866 Meeting</h1>
        <table summary="This table contains the summary of EO 12866Meeting Record."><tr><td>
        <p><label>Title:</label>Revision to Rules of Practice</p>
        <p><label>Meeting Date/Time:</label>12/08/2026 11:00 AM</p>
        <p><label>Requestor:</label>Franklin Square Group<label>Requestor's Client:</label>ITC Modernization Alliance</p>
        </td></tr></table>"""
        obs = p.read("oira", "meeting:1601523")
        self.assertEqual(obs["data"]["Requestor"], "Franklin Square Group")
        self.assertEqual(obs["data"]["Requestor's Client"], "ITC Modernization Alliance")
        self.assertEqual(obs["data"]["record_heading"], "View Scheduled EO 12866 Meeting")
        self.assertIsNone(obs["published"])

    @patch.object(p, "_get")
    def test_pending_review_keeps_received_date_and_no_deadline(self, get):
        get.return_value = b"""<table summary="This table contains EO Reviews.">
        <tr><td><b>RIN:</b>0581-AE47</td><td><b>Status:</b><a href="/public/do/eoDetails?rrid=1538567">Pending Review</a></td></tr>
        <tr><td><b>TITLE:</b><span>Dairy Program</span><!-- JSP markup --></td></tr>
        <tr><td><b>STAGE:</b>Proposed Rule</td><td><b>RECEIVED DATE:</b>09/21/2026</td><td><b>LEGAL DEADLINE:</b>None</td></tr>
        </table>"""
        row, = p.search("oira", "Dairy", stage="proposed")["results"]
        self.assertEqual(row["title"], "Dairy Program")
        self.assertEqual(row["LEGAL DEADLINE"], "None")
        self.assertEqual(row["RECEIVED DATE"], "09/21/2026")

    @patch.object(p, "_get")
    def test_weekly_schedule_keeps_meetings_separate_and_reads_witness_affiliation(self, get):
        get.return_value = b"""<div class="committee-schedule-section"><div class="schedule-heading">
        <a href="/committee-schedule/daily/2026/09/15">Tuesday</a></div><ul><li class="expanded">
        <p><strong>House Financial Services</strong><br>10:00 AM<br>Hearing: Annual Testimony</p>
        <p><a href="/event/119th-congress/house-event/119546?s=1">Meeting Details</a></p><hr>
        <p><strong>Another Committee</strong><br>Another meeting</p>
        <p><a href="/event/119th-congress/joint-event/LC76113/text">Meeting Details</a></p></li></ul></div>"""
        page = p.search("congress", "testimony", week_of="2026-09-14")
        row, = page["results"]
        self.assertEqual(row["title"], "Hearing: Annual Testimony")
        self.assertEqual(row["meeting_date"], "2026-09-15")
        get.return_value = b"""<main><h1>Annual Testimony<span>119th Congress</span></h1>
        <table class="standard01"><tr><th>Date:</th><td>09/15/2026</td></tr></table>
        <ul class="event-witness"><li><strong>Scott Bessent</strong><span>Secretary, U.S. Department of the Treasury</span>
        </li></ul></main>"""
        obs = p.read(*row["record_id"].split(":", 1))
        self.assertEqual(obs["data"]["witnesses"][0]["position_and_organization"], "Secretary, U.S. Department of the Treasury")

    @patch.object(p, "_json")
    @patch.object(p, "_get")
    def test_govinfo_cursor_and_bill_action_read(self, get, api):
        api.return_value = {"count": 2, "offsetMark": "NEXT", "results": [
            {"packageId": "BILLS-119s4448es", "title": "Accelerating Broadband Permits", "collectionCode": "BILLS"}]}
        page = p.search("congress", "broadband", record_type="bills", limit=1)
        self.assertEqual(page["next_cursor"], "NEXT")
        get.return_value = b"""<billStatus><bill><congress>119</congress><type>S</type><number>4448</number><title>Accelerating Broadband Permits</title>
        <actions><item><actionDate>2026-11-19</actionDate><text>Passed Senate</text></item></actions></bill></billStatus>"""
        obs = p.read(*page["results"][0]["record_id"].split(":", 1))
        self.assertEqual(obs["data"]["actions"][0]["actionDate"], "2026-11-19")
        self.assertIsNone(obs["published"])

    @patch.object(p, "_get")
    def test_report_preserves_recipient_amount_columns(self, get):
        get.return_value = b"<title>Senate report</title><pre>Recipient                 Amount\nBlackbird               3,000,000</pre>"
        obs = p.read("congress", "reports:CRPT-119srpt44")
        self.assertIn("Blackbird               3,000,000", obs["text"])
        self.assertEqual(obs["kind"], "committee_report")
