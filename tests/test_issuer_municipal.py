from unittest.mock import patch

from distress_signals.sources import issuer_municipal as ca


def test_debtwatch_keeps_date_meanings_and_document_provenance():
    issue = {"CDIACNumber": "2026-1156", "IssueName": "Revenue Bonds", "SoldStatus": "Sold",
             "Issuer": "Financing Authority", "FirstOptionalCallDate": "2036-08-01", "SaleDate": "2026-08-11"}
    document = {"CDIACNumber": "2026-1156", "DocumentId": 115449, "DocumentType": "Official Statement",
                "DocumentFileName": "official.pdf"}
    with patch.object(ca, "_search", side_effect=[
        {"records": [issue]}, {"records": [document], "totalMatchingRecords": 1},
    ]):
        obs = ca.read("california:2026-1156")
    assert obs["published"] is None
    assert "First optional call date: 2036-08-01" in obs["text"]
    assert obs["data"]["publisher"] == ca.PUBLISHER
    assert obs["data"]["documents"][0]["url"].endswith("/115449/download?inline=true")


def test_debtwatch_offset_crosses_page():
    def response(dataset, *, query, page, size):
        return {"totalMatchingRecords": 60, "records": [
            {"CDIACNumber": f"2026-{i:04}"} for i in range((page-1)*size, min(page*size, 60))
        ]}
    with patch.object(ca, "_search", side_effect=response):
        result = ca.search("bonds", limit=4, offset=48)
    assert [r["record_id"] for r in result["results"]] == [f"emma:california:2026-00{i}" for i in range(48, 52)]
    assert not result["complete"]
