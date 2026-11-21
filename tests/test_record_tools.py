import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from distress_signals import db, sources, tools
from distress_signals.sources import healthcare
from distress_signals.mcp_server import server


class PublicRecordToolsTest(unittest.TestCase):
    def test_every_searchable_source_is_exposed_over_mcp(self):
        definitions = asyncio.run(server.list_tools())
        search = next(tool for tool in definitions if tool.name == "search_records")
        names = search.input_schema["properties"]["source"]["enum"]
        self.assertEqual(names, sources.order(os.getenv("DISTRESS_RUN_ID")))
        self.assertEqual(len(names), len(set(names)))
        for source, entry in sources.all().items():
            with self.subTest(source=source):
                self.assertIn(entry.card(), search.description)
                self.assertLessEqual(len(entry.card()), sources.CARD_CHARS)
                with self.assertRaisesRegex(tools.ToolError, "takes filters"):
                    tools.search_records(source, "", {"unknown_filter": True})

    def test_search_read_cite_and_refresh_native_record(self):
        source = "clinical_trials"
        native_id = "NCT00000001"
        ref = f"{source}:{native_id}"
        url = f"https://clinicaltrials.gov/study/{native_id}"
        title = "Test study"
        passage = "Estimated completion date: December 2027."
        search_result = {"results": [{"record_id": ref, "title": title, "url": url}]}
        record = {"source": source, "kind": "page", "native_id": native_id, "url": url,
                  "text": passage, "data": {"title": title}}
        updated = {**record, "text": "Estimated completion date: January 2028."}
        module = healthcare
        with (tempfile.TemporaryDirectory() as folder,
              patch.dict(os.environ, DISTRESS_DB=str(Path(folder) / "test.db"), DISTRESS_RUN_ID="test"),
              patch.object(module, "search", return_value=search_result),
              patch.object(module, "read", side_effect=[record, updated]) as fetch):
            found = tools.search_records(source, title)
            with db.connect() as con:
                self.assertEqual(con.execute("SELECT COUNT(*) FROM observations").fetchone()[0], 0)
            first = tools.read_record(found["results"][0]["record_id"])
            oid = first["observation_id"]
            self.assertEqual(tools.read_record(oid, find="December")["matches"][0]["term"], "December")
            finding = tools.record_finding({"kind": "fact", "statement": passage,
                                            "evidence": [{"observation_id": oid, "passage": passage}]})
            self.assertEqual(finding["unchecked_passages"], 0)
            refreshed = tools.read_record(oid, fresh=True)
            self.assertEqual(refreshed["observation_id"], oid)
            self.assertEqual(refreshed["text"], updated["text"])
            self.assertEqual(tools.compute("SELECT text FROM observations")["rows"], [[updated["text"]]])
            self.assertEqual(fetch.call_count, 2)
            fetch.assert_called_with(source, native_id)

    def test_url_citation_resolves_the_row_containing_the_passage(self):
        url = "https://example.org/ratings.zip"
        with tempfile.TemporaryDirectory() as folder, db.connect(Path(folder) / "test.db") as con:
            cites = []
            def cite(passage):
                return tools._cite(con, {"url": url, "passage": passage}, "evidence", cites)

            # A URL citation can predate the reads of records in the archive.
            self.assertFalse(cite("An earlier public statement.")["checked"])
            for identifier, name in (("obs_a", "First organization."), ("obs_b", "Second organization.")):
                db.put_observation(con, {"id": identifier, "kind": "record", "source": "medicare_stars",
                                         "retrieved_at": "2026-11-20", "url": url,
                                         "text": f"Contract name: {name} Overall contract rating: 4."})
            matched = cite("Contract name: Second organization.")
            self.assertEqual(matched["observation_id"], "obs_b")
            self.assertTrue(matched["checked"])
            with self.assertRaisesRegex(tools.ToolError, "several records"):
                cite("Overall contract rating: 4.")


def test_concurrent_researchers_can_initialize_the_observation_store(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    start = Barrier(6)
    def open_store(_):
        start.wait()
        with db.connect(tmp_path / "shared.db") as con:
            return con.execute("SELECT COUNT(*) FROM opportunities WHERE status='active'").fetchone()[0]

    with ThreadPoolExecutor(max_workers=6) as pool:
        assert list(pool.map(open_store, range(6))) == [0] * 6
