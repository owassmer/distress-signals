import unittest
from unittest.mock import patch

from distress_signals.sources import issuer_websites as websites


class IssuerWebsitesTests(unittest.TestCase):
    def setUp(self):
        self.digest = "f" * 64
        self.document = {"document_sha256": self.digest, "source_observations": [
            {"title": "Financial statements", "source_url": "https://issuer.example/report.pdf",
             "collection_id": "issuer-collection"}]}
        self.epoch, self.generation = "e" * 64, "a" * 64

    def test_search_page_preserves_snapshot_and_original_url(self):
        batch = {"epoch_sha256": self.epoch, "generation_sha256": self.generation, "more": True,
                 "results": [self.document]}
        with patch.object(websites, "_get", return_value=batch) as get:
            result = websites.search("revenue", offset=1, limit=1, epoch=self.epoch, generation=self.generation)
        self.assertEqual(get.call_args.args[1]["epoch"], self.epoch)
        self.assertEqual(result["next_offset"], 2)
        self.assertEqual(result["results"][0]["issuer_document_url"], "https://issuer.example/report.pdf")
        self.assertTrue(result["results"][0]["record_id"].endswith(self.digest))

    def test_native_read_checks_hash_and_returns_metadata_separately(self):
        with patch.object(websites, "_get", return_value={"document": self.document}):
            native = f"website/{self.digest}"
            obs = websites.read(native)
            self.assertEqual(obs["native_id"], native)
            self.assertEqual(obs["kind"], "filing_index")
            self.assertIn("freedar.ca/api/", obs["url"])
            self.assertEqual(obs["data"]["links"][0]["url"], "https://issuer.example/report.pdf")
            self.assertIsNone(obs["published"])
            with self.assertRaisesRegex(websites.SourceError, "different document"):
                websites.read("website/" + "b" * 64)
