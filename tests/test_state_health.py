import json
from unittest import TestCase
from unittest.mock import Mock, patch

from distress_signals.sources import state_health as records


def response(text):
    return Mock(content=text.encode(), text=text)


class StateHealthTests(TestCase):
    def test_ny_pending_requires_publisher_evidence_and_preserves_multiple_rows(self):
        header = "<tr><th>Insurer</th><th>SERFF Number</th><th>Effective Date</th><th>Rate Application</th></tr>"
        def form(date):
            return (f"<tr><td>MetroPlus Health Plan</td><td>MPHP-134890289</td><td>{date}</td>"
                    "<td><a href='/documents/application'>Rate Application</a></td></tr>")
        table = "<table>" + header + form("12/01/2026") + form("01/01/2027") + "</table>"
        note = "<p>These are the rates proposed by health insurers, and have not been approved by DFS.</p>"
        with patch.object(records, "_ny_index", return_value={2027: records.NEW_YORK}), patch.object(records, "_get") as get:
            get.return_value = response(note + table)
            row, = records._ny_rows("lg-hmo")
            self.assertEqual(row["status"], "Pending")
            self.assertEqual([r["Effective Date"] for r in row["fields"]], ["12/01/2026", "01/01/2027"])
            self.assertTrue(row["url"].endswith("/documents/application"))
            get.return_value = response(table)
            self.assertNotIn("status", records._ny_rows("lg-hmo")[0])

    def test_oregon_repeated_rate_key_retains_both_reporting_rows(self):
        columns = ["RATE_KEY", "COMPANY_NAME", "FILING_NUMBER_DISP", "PRODUCT_TYPE", "SUBMISSION_DATE", "EFFECTIVE_DATE",
                   "EFFECTIVE_YEAR", "STATUS", "DECISION_DATE", "PERCENT_CHANGE_REQUESTED", "PERCENT_CHANGE_APPROVED", "AFFECTED_COUNT"]
        a = [8247, "HEALTH NET HEALTH PLAN OF OREGON, INC.", "HNOR-128563646", "Individual (Major Medical)", None,
             "10/01/2012", "2012", "Approved", None, None, 0, 0]
        b = [*a[:-2], 1.2, 42]
        body = "<script>var dataSet_raw = " + json.dumps({"COLUMNS": columns, "DATA": [a, b]}) + ";</script>"
        with patch.object(records, "_get", return_value=response(body)):
            row, = records._oregon(2012)
            self.assertEqual(len(row["fields"]), 2)
            self.assertEqual(row["record_id"], "serff:or:8247")
            obs = records.read("serff", "or:8247")
            self.assertIn("AFFECTED_COUNT: 0", obs["text"])
            self.assertIn("AFFECTED_COUNT: 42", obs["text"])
            self.assertEqual(records._oregon(2027), [])

    def test_texas_joins_solicitation_to_program_and_retains_forecast_precision(self):
        page = """<h3>Medicaid Managed Care Procurement Timelines</h3>
          <details><summary>Medicaid and CHIP Dental Services</summary><div class="details-wrapper">
          <li><a href="https://www.txsmartbuy.gov/esbd/HHS0017108">Read posting</a></li>
          <li>Anticipated notice of award: Q4 SFY 2027</li></div></details><h2>Services</h2>
          <details><summary>Another program</summary></details>"""
        detail = """<div class="esbd-result-cell"><strong>Response Due Date:</strong><p>12/7/2026</p></div>"""
        with patch.object(records, "_get", side_effect=[response(page), response(detail)]):
            row, = records._texas()
        self.assertEqual(row["fields"][0]["Response Due Date"], "12/7/2026")
        self.assertIn("Q4 SFY 2027", row["procurement_record"])
        self.assertEqual(row["url"], "https://www.txsmartbuy.gov/esbd/HHS0017108")

    def test_iowa_search_picks_contracts_before_next_section(self):
        panel = """<h3>Iowa Total Care</h3><div class="accordion__items-panel"><a href="/media/15880/download?inline"
          data-entity-file-mimetype="application/pdf">MEDIOMC26002 – Iowa Total Care Contract<span>(1.96 MB)</span>
          </a></div>"""
        page = f'<h2 id="managed-care-plan-contracts-amp-amendments">Contracts</h2><div>{panel}</div><h2>Audits</h2><div>{panel}</div>'
        with patch.object(records, "_get", return_value=response(page)):
            row, = records.search("medicaid_contracts", "Iowa Total Care", state="IA")["results"]
        self.assertEqual(row["record_id"], "medicaid_contracts:ia:15880")
        self.assertEqual(row["title"], "MEDIOMC26002 – Iowa Total Care Contract")
        self.assertEqual(row["plan"], "Iowa Total Care")

    def test_unavailable_jurisdiction_is_reported(self):
        with patch.object(records, "_iowa", side_effect=records.SourceError("unavailable")), patch.object(records, "_texas", return_value=[]):
            page = records.search("medicaid_contracts", "")
        self.assertFalse(page["complete"])
        self.assertEqual(page["source_errors"], {"IA": "unavailable"})
