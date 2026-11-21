import csv
import io
import json
import unittest
from unittest.mock import patch
from zipfile import ZIP_DEFLATED, ZipFile

from distress_signals.sources import contract_data as c
from distress_signals.util import SourceError


class Contracts(unittest.TestCase):
    def test_usac_form_versions_and_dates(self):
        current = {"funding_request_number": "12345", "form_version": "Current", "application_number": "101",
                   "spin_name": "A provider", "contract_type_name": "Tariff", "contract_expiration_date": "2027-07-01"}
        application = {"application_number": "101", "form_version": "Original", "certified_datetime": "2026-04-01",
                       "file_url": {"url": "https://example.org/471.pdf"}}
        with patch.object(c, "_soda", side_effect=[[current], [current], [application]]) as query:
            found = c.search("erate", "provider's", end_from="2027-06-01", funding_year=2026)
            where = query.call_args_list[0].kwargs["where"]
            self.assertIn("provider''s", where)
            self.assertIn("form_version='Current'", where)
            self.assertIn("funding_year='2026'", where)
            row = found["results"][0]
            self.assertEqual(row["record_id"], "erate:12345/Current")
            self.assertEqual(row["contract_type_name"], "Tariff")
            item = c.read("erate", "12345/Current")
        self.assertEqual(item["data"]["links"][-1]["text"], "FCC Form 471 PDF (Original)")
        self.assertIsNone(item["published"])
        self.assertEqual(json.loads(item["text"])["form_471"], [application])

    def test_local_read_includes_amendments_and_publisher_numbers(self):
        rows = [{"contract_number": "26/42", "vendor_name": "Supplier", "description": "Install equipment",
                 "end_date": "2026-10-01", "system_release": "2025-05-01"},
                {"contract_number": "26/42", "vendor_name": "Supplier", "description": "Extend term",
                 "end_date": "2027-10-01", "system_release": "2026-07-01"}]
        with patch.object(c, "_soda", side_effect=[[rows[1]], rows]) as query:
            res = c.search("municipal_contracts", "", jurisdiction="cook_county", end_from="2027-01-01")
            ref = res["results"][0]["record_id"].split(":", 1)[1]
            self.assertEqual(ref, "cook_county/26%2F42")
            item = c.read("municipal_contracts", ref)
            self.assertEqual(query.call_args.kwargs["where"], "contract_number='26/42'")
            self.assertEqual(json.loads(item["text"]), rows)

    def test_quarterly_filing_identity_product_rows_and_batch_continuation(self):
        lines = [
            {"contract_unique_id": "C7", "seller_company_name": "Station", "customer_company_name": "Utility",
             "contract_termination_date": "20271231", "product_name": "ENERGY"},
            {"contract_unique_id": "C7", "seller_company_name": "Station", "customer_company_name": "Utility",
             "contract_termination_date": "20271231", "product_name": "CAPACITY"},
            {"contract_unique_id": "C9", "seller_company_name": "Station", "customer_company_name": "Utility",
             "contract_termination_date": "", "product_name": "ENERGY"}]
        sheet = io.StringIO()
        writer = csv.DictWriter(sheet, fieldnames=list(lines[0]))
        writer.writeheader()
        writer.writerows(lines)
        inner = io.BytesIO()
        with ZipFile(inner, "w", ZIP_DEFLATED) as package:
            package.writestr("202609_station_contracts.CSV", sheet.getvalue())
        quarter = io.BytesIO()
        with ZipFile(quarter, "w", ZIP_DEFLATED) as package:
            package.writestr("CSV_2026_Q3_123_456.ZIP", inner.getvalue())
            package.writestr("CSV_2026_Q3_124_457.ZIP", inner.getvalue())

        def open_quarter(*args, **kwargs):
            return ZipFile(io.BytesIO(quarter.getvalue()))

        with patch.object(c, "_eqr_catalog", return_value=("2026Q3", "https://example.org/q.zip", {"123": "Station"})), \
             patch.object(c, "RemoteZip", side_effect=open_quarter):
            found = c.search("ferc_eqr", "Utility", quarter="2026Q3", end_from="2027-01-01", filings_per_page=1)
            self.assertEqual(found["next_filing_offset"], 1)
            self.assertFalse(found["complete"])
            self.assertEqual(len(found["results"]), 1)
            row = found["results"][0]
            self.assertEqual(row["matching_product_lines"], 2)
            self.assertEqual(len(row["terms"]), 1)
            native = row["record_id"].split(":", 1)[1]
            self.assertEqual(native, "2026Q3/123/456/C7")
            item = c.read("ferc_eqr", native)
            self.assertEqual(json.loads(item["text"])["contracts"], lines[:2])
            with self.assertRaisesRegex(SourceError, "filing version is absent"):
                c.read("ferc_eqr", "2026Q3/123/455/C7")


if __name__ == "__main__":
    unittest.main()
