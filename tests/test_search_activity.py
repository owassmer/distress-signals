import json
from unittest import TestCase

from distress_signals import feed, watch


class SearchActivityTests(TestCase):
    def test_filter_only_search_uses_public_source_name(self):
        args = {"source": "ferc_eqr", "query": "", "filters": {"quarter": "2026Q2", "page": 1}}
        expected = "Searched FERC Electric Quarterly Reports (quarter 2026Q2)"
        self.assertEqual(watch.describe("search_records", args)["label"], expected)
        self.assertEqual(feed.describe({"name": "mcp__ds__search_records", "input": args}), expected)
        historical = {"source": "federal_register", "query": "solar", "filters": {"type": "notice"}}
        self.assertEqual(watch.describe("search_records", historical)["label"],
                         "Searched the Federal Register for notices matching “solar”")

    def test_zero_results_with_partial_failure(self):
        data = {"results": [], "count": 0, "complete": False, "source_errors": {"IA": "publisher unavailable"}}
        content = json.dumps(data)
        expected = "0 results returned · 1 source unavailable"
        self.assertEqual(watch.outcome("search_records", {}, content, False)["meta"], expected)
        stream = feed.Feed("example", "companies", "1")
        stream.uses["call"] = {"name": "mcp__ds__search_records"}
        stream._event({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "call", "content": [{"type": "text", "text": content}]}]}})
        self.assertIn(("Search", expected), stream.items)
        self.assertEqual(watch.outcome("search_records", {}, '{"count":0,"complete":true}', False)["meta"],
                         "0 results returned")

    def test_paged_search_does_not_report_a_source_failure(self):
        response = {"results": [{}], "total_matches": 4, "complete": False, "next_offset": 1}
        self.assertEqual(watch.outcome("search_records", {}, json.dumps(response), False)["meta"],
                         "1 result returned of 4 matches")
