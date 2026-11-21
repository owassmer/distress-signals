import io
import zipfile
from unittest.mock import Mock, patch

from openpyxl import Workbook

from distress_signals.sources import healthcare as health


def test_trial_dates_preserve_precision_status_and_page_token():
    trial = {"protocolSection": {
        "identificationModule": {"nctId": "NCT07882108", "briefTitle": "Ginger-PD"},
        "sponsorCollaboratorsModule": {"leadSponsor": {"name": "Cambridge University Hospitals NHS Foundation Trust"}},
        "statusModule": {"primaryCompletionDateStruct": {"date": "2027-03", "type": "ESTIMATED"},
                         "studyFirstPostDateStruct": {"date": "2026-10-20"}}}}
    with patch.object(health, "http_json", return_value={"studies": [trial], "totalCount": 2, "nextPageToken": "next"}) as get:
        page = health.search("clinical_trials", "", sponsor="Cambridge", completion_from="2026-12-01", page_token="start")
    with patch.object(health, "http_json", return_value={"studies": [trial]}):
        last = health.search("clinical_trials", "", page_token=page["next_page_token"])
    assert last["complete"] and "total_matches" not in last
    params = get.call_args.kwargs["params"]
    assert params["pageToken"] == "start" and params["query.spons"] == "Cambridge"
    assert "AREA[PrimaryCompletionDate]RANGE[2026-12-01, MAX]" in params["filter.advanced"]
    assert "CompletionDateType" in params["fields"]
    assert not page["complete"] and page["next_page_token"] == "next"
    row = page["results"][0]
    assert row["primary_completion"] == {"date": "2027-03", "type": "ESTIMATED"}
    assert row["study_completion"] is None
    with patch.object(health, "http_json", return_value=trial):
        obs = health.read("clinical_trials", row["record_id"].split(":", 1)[1])
    assert obs["native_id"] == "NCT07882108" and row["lead_sponsor"]["name"] in obs["text"]


def test_medicare_report_deduplicates_aliases_and_read_preserves_distinct_dates():
    base = {"document_id": 322, "document_type": "NCA", "document_display_id": "CAG-00471N",
            "document_status": "Open For Public Comment", "last_updated": "11/18/2026"}
    report = {"meta": {"next_token": ""}, "data": [{**base, "title": "Leadless CRT - Wireless Stimulation Endocardially (WiSE)"},
                       {**base, "title": "Wireless Stimulation Endocardially for Cardiac Resynchronization Therapy System (WiSE CRT)"}]}
    with patch.object(health, "_cms", return_value=report):
        matches = health.search("medicare_coverage", "CAG-00471N")["results"]
        by_alias = health.search("medicare_coverage", "Therapy System")["results"]
    assert len(matches) == len(by_alias) == 1
    assert matches[0]["record_id"] == by_alias[0]["record_id"] == "medicare_coverage:nca:322"
    sheet = {"meta": {"next_token": ""}, "data": [{"title": matches[0]["title"],
             "expected_completion_date": "02/16/2027", "proposed_decision_memo_due_date": "12/03/2026",
             "proposed_decision_memo_date": "11/18/2026", "requestor_information": [{"requestor": "EBR Systems"}]}]}
    with patch.object(health, "_cms", return_value=sheet) as get:
        obs = health.read("medicare_coverage", "nca:322")
    assert get.call_args.args == ("data/nca/tracking-sheet",)
    assert obs["native_id"] == "nca:322" and "EBR Systems" in obs["text"]
    assert "proposed_decision_memo_due_date: 12/03/2026" in obs["text"]
    assert "proposed_decision_memo_date: 11/18/2026" in obs["text"]
    assert obs["published"] is None


def test_star_contract_is_resolved_in_the_selected_year_with_missing_values_intact():
    headers = ["Contract Number", "Contract Name", "Parent Organization", "2026 Overall"]
    book = Workbook()
    sheet = book.active
    sheet.append(["2026 Summary Star View: Medicare Report Card Master Table"])
    sheet.append(headers)
    sheet.append(["H0062 ", "SUPERIOR HEALTHPLAN, INC. ", "Centene Corporation ", "Not enough data available "])
    file = io.BytesIO()
    book.save(file)
    package = io.BytesIO()
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("Tables/2026 Star Ratings Data Table - Summary Ratings (July 22 2026).xlsx", file.getvalue())
    url = "https://www.cms.gov/files/zip/2026-star-ratings-data-tables.zip"
    response = Mock(content=package.getvalue())
    with patch.object(health, "_stars_catalog", return_value={2026: {"url": url, "label": "2026 Star Ratings Data Tables (ZIP)"}}), patch.object(health, "session") as session:
        session.return_value.get.return_value = response
        search = health.search("medicare_stars", "Centene", year=2026)
        obs = health.read("medicare_stars", "2026:H0062")
    assert search["results"][0]["record_id"] == "medicare_stars:2026:H0062"
    assert obs["data"]["fields"]["2026 Overall"] == "Not enough data available"
    assert "Contract Number: H0062" in obs["text"]


def test_current_csv_contract_summary_and_local_pagination():
    data = b"Annual summary,,\r\nContract Number,Contract Name,2027 Overall\r\nH0034,HAMASPIK,4\r\n"
    assert health._summary_rows(data, "summary.csv")[2] == ["H0034", "HAMASPIK", "4"]
    assert health._page(["H0028", "H0034"], 0, 1) == {
        "results": ["H0028"], "total_matches": 2, "next_offset": 1, "complete": False}
    assert health._page(["H0028", "H0034"], 1, 1)["complete"]
