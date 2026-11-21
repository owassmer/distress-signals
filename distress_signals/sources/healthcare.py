"""Study sponsors and milestones, Medicare coverage tracking sheets and annual contract ratings."""

import csv
import datetime as dt
import html
import io
import json
import re
import zipfile
from urllib.parse import urlparse

import requests

from ..util import SourceError, http_json, session
from . import web

TRIALS = "https://clinicaltrials.gov/api/v2/studies"
COVERAGE = "https://api.coverage.cms.gov/v1"
TRACKING = "https://www.cms.gov/medicare-coverage-database/view/ncacal-tracking-sheet.aspx"
STARS = "https://www.cms.gov/medicare/health-drug-plans/part-c-d-performance-data"

FILTERS = {
    "clinical_trials": {"sponsor", "status", "completion_from", "completion_until", "max_results", "page_token"},
    "medicare_coverage": {"status", "document_type", "max_results", "offset"},
    "medicare_stars": {"year", "max_results", "offset"},
}

_TRIAL_FIELDS = ("NCTId,BriefTitle,OverallStatus,LeadSponsorName,LeadSponsorClass,CollaboratorName,CollaboratorClass,"
                 "PrimaryCompletionDate,PrimaryCompletionDateType,CompletionDate,CompletionDateType,"
                 "StudyFirstPostDate,LastUpdatePostDate")


def _integer(value, name, low, high=None):
    if isinstance(value, bool) or not str(value).isdigit() or int(value) < low or (high and int(value) > high):
        raise SourceError(f"{name} must be an integer from {low}" + (f" to {high}" if high else " upward"))
    return int(value)


def _page(rows, offset, limit):
    offset = _integer(offset, "offset", 0)
    end = offset + limit
    return {"results": rows[offset:end], "total_matches": len(rows), "complete": end >= len(rows),
            **({"next_offset": end} if end < len(rows) else {})}


def _study(study):
    p = study["protocolSection"]
    ident, status = p["identificationModule"], p.get("statusModule", {})
    sponsors = p.get("sponsorCollaboratorsModule", {})
    nct = ident["nctId"]
    return {"record_id": f"clinical_trials:{nct}", "title": ident["briefTitle"], "url": f"https://clinicaltrials.gov/study/{nct}",
            "lead_sponsor": sponsors.get("leadSponsor"), "collaborators": sponsors.get("collaborators", []),
            "status": status.get("overallStatus"), "primary_completion": status.get("primaryCompletionDateStruct"),
            "study_completion": status.get("completionDateStruct"), "first_posted": status.get("studyFirstPostDateStruct"),
            "last_update_posted": status.get("lastUpdatePostDateStruct")}


def _trials(query, limit, sponsor=None, status=None, completion_from=None, completion_until=None, page_token=None):
    params = {"fields": _TRIAL_FIELDS, "countTotal": "true", "pageSize": limit}
    for key, value in (("query.term", query), ("query.spons", sponsor), ("filter.overallStatus", status),
                       ("pageToken", page_token)):
        if value:
            params[key] = value
    if completion_from or completion_until:
        try:
            lo = dt.date.fromisoformat(completion_from) if completion_from else None
            hi = dt.date.fromisoformat(completion_until) if completion_until else None
        except (TypeError, ValueError) as exc:
            raise SourceError("Give completion dates as YYYY-MM-DD.") from exc
        if lo and hi and lo > hi:
            raise SourceError("completion_from must be on or before completion_until")
        span = f"RANGE[{lo or 'MIN'}, {hi or 'MAX'}]"
        params["filter.advanced"] = f"(AREA[PrimaryCompletionDate]{span} OR AREA[CompletionDate]{span})"
    data = http_json(TRIALS, params=params)
    token = data.get("nextPageToken")
    return {"results": [_study(study) for study in data["studies"]], "complete": not token,
            **({"total_matches": data["totalCount"]} if "totalCount" in data else {}),
            **({"next_page_token": token} if token else {})}


def _cms(path, **params):
    data = http_json(f"{COVERAGE}/{path}", params=params)
    status = data["meta"]["status"]
    if str(status["id"]) != "200":
        raise SourceError(f"CMS coverage API: {status.get('message', status)}")
    return data


def _coverage(query, limit, offset=0, status=None, document_type=None):
    params = {}
    if status:
        options = {x.casefold(): x for x in ("Open", "Closed", "Open for Public Comment")}
        if str(status).casefold() not in options:
            raise SourceError(f"status must be one of {list(options.values())}")
        params["status"] = options[str(status).casefold()]
    if document_type:
        if str(document_type).upper() not in {"NCA", "CAL"}:
            raise SourceError("document_type must be NCA or CAL")
        params["document_type"] = document_type.upper()
    data = _cms("reports/national-coverage-ncacal/", **params)
    # The report has no pagination parameters in the Coverage API. Refuse an incomplete report
    # if CMS starts returning a token before publishing its continuation contract.
    if data["meta"].get("next_token"):
        raise SourceError("CMS returned a partial national coverage report. Narrow by status or document_type.")
    rows = {}
    for entry in data["data"]:
        if not any(query.casefold() in str(entry.get(k, "")).casefold() for k in ("title", "document_display_id", "document_id")):
            continue
        kind, number = entry["document_type"].lower(), entry["document_id"]
        native = f"{kind}:{number}"
        if native in rows:
            continue
        rows[native] = {"record_id": f"medicare_coverage:{native}", "title": entry["title"],
                        "url": f"{TRACKING}?{kind}id={number}", "document_type": entry["document_type"],
                        "cag_id": entry["document_display_id"], "status": entry["document_status"],
                        "last_updated": entry["last_updated"]}
    return _page(list(rows.values()), offset, limit)


def _stars_catalog():
    # Use every link on the agency's page; its navigation alone can exceed the generic reader's link limit.
    response = session().get(STARS, timeout=45)
    response.raise_for_status()
    page = web._Text(STARS)
    page.feed(response.text)
    editions = {}
    factsheets = {}
    for link in page.links:
        fact = re.match(r"^(\d{4}) Star Ratings Fact Sheet\b", link["text"], re.I)
        if fact:
            factsheets[int(fact[1])] = link["url"]
        year = re.match(r"^(\d{4}) Star Ratings Data Tables\b", link["text"], re.I)
        url = urlparse(link["url"])
        if year and url.hostname == "www.cms.gov" and url.path.lower().endswith(".zip"):
            edition = {"url": link["url"], "label": link["text"]}
            # CMS dates some editions in their link labels. A revised table can post well after ratings release.
            date = re.search(r"\(([A-Za-z]{3} \d{1,2},? \d{4})\)", link["text"])
            if date:
                edition["edition_date"] = dt.datetime.strptime(date[1].replace(",", ""), "%b %d %Y").date().isoformat()
            editions[int(year[1])] = edition
    if not editions:
        raise SourceError(f"CMS listed no standalone annual star ratings tables at {STARS}")
    for year, edition in editions.items():
        if year in factsheets:
            edition["fact_sheet_url"] = factsheets[year]
    return editions


def _summary_rows(raw, name):
    if name.lower().endswith(".csv"):
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("cp1252")
        return list(csv.reader(io.StringIO(text)))
    from openpyxl import load_workbook
    book = load_workbook(io.BytesIO(raw), read_only=True, data_only=True, keep_links=False)
    try:
        return list(book.active.values)
    finally:
        book.close()


def _stars(year):
    editions = _stars_catalog()
    year = _integer(year, "year", 1) if year is not None else max(editions)
    if year not in editions:
        raise SourceError(f"CMS standalone ratings tables cover {sorted(editions)}. Other archives: {STARS}")
    edition = editions[year]
    link = edition["url"]
    response = session().get(link, timeout=90)
    response.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = [n for n in archive.namelist() if re.search(r"Summary Ratings?\b.*\.(csv|xlsx)$", n.rsplit("/", 1)[-1], re.I)]
        if len(names) != 1:
            raise SourceError(f"Expected one contract summary file in {link}; found {len(names)}.")
        table = iter(_summary_rows(archive.read(names[0]), names[0]))
    headers = next((row for row in table if row and str(row[0]).strip() == "Contract Number"), [])
    if not {"Contract Name", f"{year} Overall"}.issubset(headers):
        raise SourceError(f"CMS summary columns have changed in {names[0]}.")
    result = []
    for row in table:
        fields = {k: str(v).strip() if v is not None else "" for k, v in zip(headers, row) if k}
        contract = fields.get("Contract Number", "")
        if not contract:
            continue
        result.append({"record_id": f"medicare_stars:{year}:{contract}", "title": fields["Contract Name"],
                       "url": link, "year": year, "edition": edition, "table": names[0], "contract": contract, "fields": fields})
    return year, result


def _observation(source, native, row, fields, published=None):
    def value_text(value):
        return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
    text = "\n".join(f"{key}: {value_text(value)}" for key, value in fields.items() if value is not None)
    return {"kind": "public_record", "source": source, "native_id": native, "url": row["url"],
            "published": published, "text": text, "data": row}


def _tracking_read(native):
    match = re.fullmatch(r"(nca|cal):([0-9]+)", native)
    if not match:
        raise SourceError("Use a Medicare coverage tracking-sheet ID from search_records.")
    kind, number = match.groups()
    route = f"data/{kind}/tracking-sheet"
    params = {f"{kind}id": number, "page_size": 100}
    sheet = _cms(route, **params)
    pages = list(sheet["data"])
    while token := sheet["meta"].get("next_token"):
        sheet = _cms(route, **params, next_token=token)
        pages.extend(sheet["data"])
    if not pages:
        raise SourceError(f"CMS has no tracking sheet for {native}.")
    url = f"{TRACKING}?{kind}id={number}"
    clean = []
    for entry in pages:
        fields = {}
        for key, value in entry.items():
            if isinstance(value, str) and "&lt;" in value:
                parser = web._Text(url)
                parser.feed(html.unescape(value))
                value = web._tidy("".join(parser.out))
            if value not in ("", None, []):
                fields[key] = value
        clean.append(fields)
    row = {"title": clean[0]["title"], "url": url, "tracking_sheets": clean}
    evidence = clean[0] if len(clean) == 1 else {"tracking_sheets": clean}
    return _observation("medicare_coverage", native, row, evidence)


def search(source, query="", max_results=25, **filters):
    limit = _integer(max_results, "max_results", 1, 200)
    try:
        if source == "clinical_trials":
            return _trials(query, limit, **filters)
        if source == "medicare_coverage":
            return _coverage(query, limit, **filters)
        if source == "medicare_stars":
            year, rows = _stars(filters.get("year"))
            keys = ("Contract Number", "Contract Name", "Organization Marketing Name", "Parent Organization")
            matches = [row for row in rows if any(query.casefold() in row["fields"].get(k, "").casefold() for k in keys)]
            return {"year": year, **_page(matches, filters.get("offset", 0), limit)}
        raise SourceError(f"Unknown healthcare source {source!r}")
    except (requests.RequestException, zipfile.BadZipFile, ValueError, KeyError) as exc:
        raise SourceError(f"{source} search failed: {exc}") from exc


def read(source, native_id):
    try:
        if source == "clinical_trials" and re.fullmatch(r"NCT\d{8}", native_id):
            study = http_json(f"{TRIALS}/{native_id}")
            row = _study(study)
            return _observation(source, native_id, row, study, (row["last_update_posted"] or {}).get("date"))
        if source == "medicare_coverage":
            return _tracking_read(native_id)
        if source == "medicare_stars" and (match := re.fullmatch(r"(\d{4}):([A-Z0-9]+)", native_id)):
            year, rows = _stars(match[1])
            for row in rows:
                if row["contract"] == match[2]:
                    details = {"CMS edition": row["edition"]["label"], "Rating year": year, **row["fields"]}
                    if sheet := row["edition"].get("fact_sheet_url"):
                        details["CMS fact sheet"] = sheet
                        row["links"] = [{"text": f"{year} CMS Star Ratings Fact Sheet", "url": sheet}]
                    return _observation(source, native_id, row, details, row["edition"].get("edition_date"))
        raise SourceError(f"Could not resolve {source}:{native_id}; use the record_id from search_records.")
    except (requests.RequestException, zipfile.BadZipFile, ValueError, KeyError) as exc:
        raise SourceError(f"Could not read {source}:{native_id}: {exc}") from exc
