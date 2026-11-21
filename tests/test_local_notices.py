import unittest
from unittest.mock import patch

from bs4 import BeautifulSoup

from distress_signals.sources import local_notices as notices


class LocalNoticesTests(unittest.TestCase):
    def test_meeting_text_and_separate_publication_date(self):
        event = {"EventId": 42, "EventBodyName": "Council", "EventDate": "2026-11-25T00:00:00",
                 "EventAgendaLastPublishedUTC": "2026-11-19T17:00:00", "EventInSiteURL": "https://city.gov/42"}
        items = [{"EventItemTitle": "Approval of a service lease with a named vendor.",
                  "EventItemMatterName": None, "EventItemAgendaNote": "Renewal terms", "EventItemId": 3}]

        def response(client, path, params=None):
            if path == "events":
                self.assertIn("2026-11-25", params["$filter"])
                return [event]
            if path == "events/42":
                return event
            self.assertEqual(path, "events/42/eventitems")
            return items

        with patch.object(notices, "_legistar", side_effect=response):
            data = notices.search("local_agendas", "RENEWAL", client="kingcounty", since="2026-11-25", until="2026-11-25")
            row, = data["results"]
            self.assertEqual(row["record_id"], "local_agendas:kingcounty:42")
            self.assertEqual(row["meeting_date"], "2026-11-25")
            self.assertEqual(row["agenda_last_published"], event["EventAgendaLastPublishedUTC"])
            read = notices.read("local_agendas", "kingcounty:42")
            self.assertIn("a named vendor", read["text"])
            self.assertEqual(read["published"], event["EventAgendaLastPublishedUTC"])

    def test_warn_archive_preserves_missing_dates(self):
        row = {"state": "MI", "company": "Motor Co.", "id": "abcd", "effective_date": "2026-12-15",
               "notice_date": "", "first_seen": "2026-11-03", "employees_affected": "25"}
        coverage = {"states": {"MI": {"status": "ok"}}, "generated_at": "2026-11-04"}
        directory = {"states": {"MI": {"url": "https://state.example/warn"}}}
        with patch.object(notices, "_archive_rows", return_value=[row]), \
             patch.object(notices, "http_json", side_effect=[coverage, directory, directory]):
            summary = notices.search("warn", "Motor", state="MI", effective_from="2026-12-01")
            hit, = summary["results"]
            self.assertIsNone(hit["notice_date"])
            self.assertEqual(hit["first_seen"], "2026-11-03")
            self.assertEqual(hit["state_source_url"], directory["states"]["MI"]["url"])
            obs = notices.read("warn", "MI:abcd")
            self.assertIsNone(obs["published"])
            self.assertEqual(obs["data"]["publisher"], "APVentureEngine normalized WARN archive")
            self.assertEqual(obs["native_id"], "MI:abcd")

    def test_washington_returns_filed_letter_and_receipt_date(self):
        headers = ["Company", "Location", "Layoff Start Date", "# of Workers", "Closure Layoff",
                   "Type of Layoff", "Received Date", "Notice"]
        values = ["Employer", "Seattle", "01/26/2027", "423", "Layoff", "Permanent", "11/18/2026",
                  '<a href="/file/letter.pdf">Download</a>']
        table = "<table><tr>" + "".join(f"<th>{h}</th>" for h in headers) + "</tr><tr>"
        table += "".join(f"<td>{v}</td>" for v in values) + "</tr></table>"
        grid = BeautifulSoup(table, "html.parser").table
        with patch.object(notices, "_wa_form", return_value=(grid, True)):
            result = notices.search("warn", "Employer", state="WA", effective_from="2027-01-01")
            hit, = result["results"]
            self.assertEqual(hit["letter_url"], "https://fortress.wa.gov/file/letter.pdf")
            self.assertTrue(hit["record_id"].startswith("warn:entry:Employer:"))
            with patch.object(notices.web, "read", side_effect=notices.SourceError("IncompleteRead")):
                read = notices.read("warn", hit["record_id"].partition(":")[2])
            self.assertIn("2027-01-26", read["text"])
            self.assertIn("could not be read", read["text"])
            self.assertEqual(hit["effective_date"], "2027-01-26")
            self.assertEqual(hit["received_date"], "11/18/2026")
            self.assertEqual(result["next_page"], 2)
            self.assertFalse(result["complete"])
