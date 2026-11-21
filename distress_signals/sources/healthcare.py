"""Three series: clinical studies registered on ClinicalTrials.gov, Medicare's national coverage analyses, and
Medicare Advantage and Part D star ratings.

  clinicaltrials_studies       the registry's API applies every filter here, and continues by page token.
  medicare_coverage_analyses   CMS lists every analysis in one report without their dates; each one's tracking
                               sheet has them. The sheets are kept in out/ and read again only when CMS's
                               last-updated stamp for the analysis changes, so a search has every date to filter.
  medicare_star_ratings        CMS publishes one table a year, in a zip file. A search reads the years it
                               needs, newest first.
"""

import csv
import datetime as dt
import functools
import html
import io
import json
import re
import zipfile
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

from ..util import OUT, SourceError, http_json, session, to_date
from . import F, Series, outline, page, web

TRIALS = "https://clinicaltrials.gov/api/v2/studies"
COVERAGE = "https://api.coverage.cms.gov/v1"
TRACKING = "https://www.cms.gov/medicare-coverage-database/view/ncacal-tracking-sheet.aspx"
STARS = "https://www.cms.gov/medicare/health-drug-plans/part-c-d-performance-data"
SHEETS = OUT / "medicare_coverage.json"

STATUS = ("ACTIVE_NOT_RECRUITING", "COMPLETED", "ENROLLING_BY_INVITATION", "NOT_YET_RECRUITING", "RECRUITING", "SUSPENDED",
          "TERMINATED", "WITHDRAWN", "AVAILABLE", "NO_LONGER_AVAILABLE", "TEMPORARILY_NOT_AVAILABLE",
          "APPROVED_FOR_MARKETING", "WITHHELD", "UNKNOWN")
SPONSOR_CLASS = ("INDUSTRY", "NIH", "FED", "OTHER_GOV", "INDIV", "NETWORK", "OTHER", "AMBIG", "UNKNOWN")
PHASE = ("EARLY_PHASE1", "PHASE1", "PHASE2", "PHASE3", "PHASE4", "NA")
# the registry's name for each date field, in its advanced filter
AREAS = {"start_date": "StartDate", "primary_completion": "PrimaryCompletionDate", "completion": "CompletionDate",
         "first_posted": "StudyFirstPostDate", "last_update_posted": "LastUpdatePostDate"}


# ---------- clinical studies ----------

def _study(study):
    p = study.get("protocolSection") or {}
    ident, status = p.get("identificationModule") or {}, p.get("statusModule") or {}
    sponsors, design = p.get("sponsorCollaboratorsModule") or {}, p.get("designModule") or {}
    date = lambda key: (status.get(key) or {}).get("date")  # noqa: E731
    nct = ident.get("nctId")
    return {"id": nct, "url": f"https://clinicaltrials.gov/study/{nct}", "nct_id": nct, "title": ident.get("briefTitle"),
            "sponsor": (sponsors.get("leadSponsor") or {}).get("name"),
            "sponsor_class": (sponsors.get("leadSponsor") or {}).get("class"),
            "collaborator": [c.get("name") for c in sponsors.get("collaborators") or [] if c.get("name")],
            "status": status.get("overallStatus"), "phase": design.get("phases") or [],
            "condition": (p.get("conditionsModule") or {}).get("conditions") or [],
            "intervention": [i.get("name") for i in (p.get("armsInterventionsModule") or {}).get("interventions") or [] if i.get("name")],
            "start_date": date("startDateStruct"), "primary_completion": date("primaryCompletionDateStruct"),
            "primary_completion_type": (status.get("primaryCompletionDateStruct") or {}).get("type"),
            "completion": date("completionDateStruct"), "first_posted": date("studyFirstPostDateStruct"),
            "last_update_posted": date("lastUpdatePostDateStruct")}


def _search_studies(s):
    params = {"countTotal": "true", "pageSize": max(1, min(s.size, 1000)),
              "fields": "NCTId,BriefTitle,OverallStatus,LeadSponsorName,LeadSponsorClass,CollaboratorName,Phase,Condition,"
                        "InterventionName,StartDate,PrimaryCompletionDate,PrimaryCompletionDateType,CompletionDate,"
                        "StudyFirstPostDate,LastUpdatePostDate"}
    words = s.query()
    for key, value in (("query.term", words), ("query.lead", " OR ".join(s.any("sponsor"))),
                       ("query.cond", " OR ".join(s.any("condition"))), ("query.intr", " OR ".join(s.any("intervention"))),
                       # these two narrow the search; the registry checks the field itself
                       ("query.spons", " OR ".join(s.peek("collaborator").get("is", []))),
                       ("query.titles", " OR ".join(s.peek("title").get("is", []))),
                       ("filter.overallStatus", "|".join(s.any("status"))), ("filter.ids", "|".join(s.any("nct_id")))):
        if value:
            params[key] = value
    advanced = []
    for field, area in AREAS.items():
        lo, hi = s.between(field)
        if lo or hi:
            advanced.append(f"AREA[{area}]RANGE[{lo or 'MIN'}, {hi or 'MAX'}]")
    for field, area in (("sponsor_class", "LeadSponsorClass"), ("phase", "Phase"), ("primary_completion_type", "PrimaryCompletionDateType")):
        given = s.any(field)
        if given:
            advanced.append("(" + " OR ".join(f"AREA[{area}]{v}" for v in given) + ")")
    if advanced:
        params["filter.advanced"] = " AND ".join(advanced)
    if not words:
        params["sort"] = "LastUpdatePostDate:desc"
    if s.cursor:
        params["pageToken"] = s.cursor
    try:
        data = http_json(TRIALS, params=params, tries=3)
    except Exception as e:
        raise SourceError(f"ClinicalTrials.gov: {type(e).__name__}: {str(e)[:200]}")
    return page([_study(x) for x in data.get("studies") or []], data.get("nextPageToken"), data.get("totalCount"),
                "most relevant first" if words else "latest update first")


def _read_study(nct):
    try:
        study = http_json(f"{TRIALS}/{nct}", tries=3)
    except Exception as e:
        raise SourceError(f"ClinicalTrials.gov has no study {nct!r}: {type(e).__name__}: {str(e)[:120]}")
    record = _study(study)
    return {**record, "document": "\n".join(outline(study, skip=("contactsLocationsModule", "derivedSection"))),
            "published": record["last_update_posted"]}


# ---------- Medicare coverage analyses ----------

def _cms(path, **params):
    try:
        data = http_json(f"{COVERAGE}/{path}", params=params, tries=3)
    except Exception as e:
        raise SourceError(f"CMS coverage API {path}: {type(e).__name__}: {str(e)[:160]}")
    status = data["meta"]["status"]
    if str(status["id"]) != "200":
        raise SourceError(f"CMS coverage API: {status.get('message', status)}")
    return data


def _plain(value):
    """CMS gives some values as HTML."""
    if isinstance(value, str) and ("<" in value or "&" in value):
        parser = web._Text(TRACKING)
        parser.feed(html.unescape(value))
        return web._tidy("".join(parser.out))
    return value


def _sheet(kind, number):
    route, params = f"data/{kind}/tracking-sheet", {f"{kind}id": number, "page_size": 100}
    got = _cms(route, **params)
    pages = list(got["data"])
    while token := got["meta"].get("next_token"):
        got = _cms(route, **params, next_token=token)
        pages.extend(got["data"])
    if not pages:
        raise SourceError(f"CMS has no tracking sheet for {kind}:{number}.")
    return {k: ([{kk: _plain(vv) for kk, vv in x.items()} if isinstance(x, dict) else _plain(x) for x in v]
                if isinstance(v, list) else _plain(v)) for k, v in pages[0].items() if v not in ("", None, [])}


def _iso(value):
    d = to_date(value)
    return d.isoformat() if d else None


def _analyses():
    """Every analysis in CMS's report, each with its tracking sheet."""
    report = _cms("reports/national-coverage-ncacal/")
    if report["meta"].get("next_token"):
        raise SourceError("CMS returned only part of its national coverage report.")
    try:
        kept = json.loads(SHEETS.read_text())
    except (OSError, ValueError):
        kept = {}
    rows = {f"{e['document_type'].lower()}:{e['document_id']}": e for e in report["data"]}
    due = [k for k, e in rows.items() if (kept.get(k) or {}).get("stamp") != e.get("last_updated_sort")]

    def fetch(key):
        kind, number = key.split(":")
        return key, {"stamp": rows[key].get("last_updated_sort"), "sheet": _sheet(kind, number)}
    if due:
        with ThreadPoolExecutor(8) as ex:
            kept.update(dict(ex.map(fetch, due)))
        OUT.mkdir(exist_ok=True)
        SHEETS.write_text(json.dumps({k: v for k, v in kept.items() if k in rows}))
    out = []
    for key, e in rows.items():
        sheet = kept[key]["sheet"]
        kind, number = key.split(":")
        out.append({"id": key, "url": f"{TRACKING}?{kind}id={number}", "cag_id": e.get("document_display_id"),
                    "document_type": e.get("document_type"), "status": e.get("document_status"), "title": e.get("title"),
                    "review_type": e.get("review_type"), "benefit_category": sheet.get("benefit_category"),
                    "requestor": [r.get("requestor") for r in sheet.get("requestor_information") or [] if isinstance(r, dict) and r.get("requestor")],
                    "formal_request_date": _iso(sheet.get("formal_request_date")),
                    "expected_completion_date": _iso(sheet.get("expected_completion_date")),
                    "public_comment_start": _iso(sheet.get("public_comment_start_date")),
                    "public_comment_end": _iso(sheet.get("public_comment_end_date")),
                    "proposed_decision_memo_due": _iso(sheet.get("proposed_decision_memo_due_date")),
                    "proposed_decision_memo_date": _iso(sheet.get("proposed_decision_memo_date")),
                    "decision_memo_date": _iso(sheet.get("decision_memo_date")), "last_updated": _iso(e.get("last_updated")),
                    "_sheet": sheet})
    out.sort(key=lambda r: r["last_updated"] or "", reverse=True)
    return out


def _read_analysis(key):
    for r in _analyses():
        if r["id"] == key:
            return {**{k: v for k, v in r.items() if k != "_sheet"}, "document": "\n".join(outline(r["_sheet"])),
                    "published": r["last_updated"]}
    raise SourceError(f"medicare_coverage_analyses has no record {key!r}")


# ---------- star ratings ----------

@functools.lru_cache(maxsize=1)
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


@functools.lru_cache(maxsize=16)
def _stars(year):
    """Every contract in one year's summary ratings table: [(the edition, the table's file, the row's columns)]."""
    edition = _stars_catalog()[year]
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
        result.append((edition, names[0], fields))
    return result


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _ratings(year):
    out = []
    for edition, table, fields in _stars(year):
        contract = fields["Contract Number"]
        out.append({"id": f"{year}:{contract}", "url": edition["url"], "contract_number": contract, "rating_year": year,
                    "contract_name": fields.get("Contract Name"), "marketing_name": fields.get("Organization Marketing Name"),
                    "parent_organization": fields.get("Parent Organization"), "organization_type": fields.get("Organization Type"),
                    "part_c_summary": _number(fields.get(f"{year} Part C Summary")),
                    "part_d_summary": _number(fields.get(f"{year} Part D Summary")),
                    "overall_rating": _number(fields.get(f"{year} Overall")), "_edition": edition, "_table": table, "_fields": fields})
    return out


def _search_ratings(s):
    lo, hi = s.peek("rating_year").get("lo"), s.peek("rating_year").get("hi")
    years = [y for y in sorted(_stars_catalog(), reverse=True) if (lo is None or y >= lo) and (hi is None or y <= hi)]
    at = s.cursor or 0
    if at >= len(years):
        return page([])
    try:
        rows = _ratings(years[at])
    except (OSError, zipfile.BadZipFile, KeyError, ValueError) as e:
        raise SourceError(f"CMS's {years[at]} star ratings table could not be read: {type(e).__name__}: {str(e)[:150]}")
    return page([{k: v for k, v in r.items() if not k.startswith("_")} for r in rows], at + 1 if at + 1 < len(years) else None,
                order="latest rating year first")


def _read_rating(key):
    year, _, contract = key.partition(":")
    for r in _ratings(int(year)) if year.isdigit() and int(year) in _stars_catalog() else []:
        if r["contract_number"] == contract:
            edition = r["_edition"]
            lines = [f"CMS edition: {edition['label']}", f"Table: {r['_table']}"] + [f"{k}: {v}" for k, v in r["_fields"].items() if v]
            links = [{"text": f"{year} Star Ratings Fact Sheet", "url": edition["fact_sheet_url"]}] if edition.get("fact_sheet_url") else []
            return {**{k: v for k, v in r.items() if not k.startswith("_")}, "document": "\n".join(lines), "links": links,
                    "published": edition.get("edition_date")}
    raise SourceError(f"medicare_star_ratings has no record {key!r}")


CMS = "Centers for Medicare & Medicaid Services"
SERIES = [
    Series(
        id="clinicaltrials_studies", title="ClinicalTrials.gov",
        record="a clinical study registered on ClinicalTrials.gov, under its NCT number",
        creator="study sponsors and investigators", publisher="U.S. National Library of Medicine",
        jurisdiction="federal registry; studies in any country", since="1999",
        coverage=lambda series: ("1999-09-17", (_search_newest_study() or None)),
        fields=(F("nct_id", "identifier"), F("title", "text"), F("sponsor", "name"), F("sponsor_class", SPONSOR_CLASS),
                F("collaborator", "name", many=True), F("status", STATUS), F("phase", PHASE, many=True),
                F("condition", "text", many=True), F("intervention", "name", many=True), F("start_date", "date"),
                F("primary_completion", "date"), F("primary_completion_type", ("ACTUAL", "ESTIMATED")),
                F("completion", "date"), F("first_posted", "date"), F("last_update_posted", "date")),
        search=_search_studies, read=_read_study),
    Series(
        id="medicare_coverage_analyses", title="Medicare national coverage analyses",
        record="a national coverage analysis (NCA) or coding analysis for labs (CAL): CMS's review of whether and how "
               "Medicare covers an item or service, with its tracking sheet",
        creator=CMS, jurisdiction="federal",
        coverage=lambda series: (lambda dates: (min(dates), max(dates)))([r["last_updated"] for r in _analyses() if r["last_updated"]]),
        fields=(F("cag_id", "identifier"), F("document_type", ("NCA", "CAL")),
                F("status", ("Open", "Open For Public Comment", "Closed")), F("title", "text"), F("review_type", "text"),
                F("benefit_category", "text"), F("requestor", "name", many=True), F("formal_request_date", "date"),
                F("expected_completion_date", "date"), F("public_comment_start", "date"), F("public_comment_end", "date"),
                F("proposed_decision_memo_due", "date"), F("proposed_decision_memo_date", "date"),
                F("decision_memo_date", "date"), F("last_updated", "date")),
        search=lambda s: page([{k: v for k, v in r.items() if k != "_sheet"} for r in _analyses()], order="latest update first"),
        read=_read_analysis),
    Series(
        id="medicare_star_ratings", title="Medicare star ratings",
        record="a Medicare Advantage or Part D contract's summary star ratings for one rating year",
        creator=CMS, jurisdiction="federal",
        coverage=lambda series: (str(min(_stars_catalog())), str(max(_stars_catalog()))),
        fields=(F("contract_number", "identifier"), F("rating_year", "number"), F("contract_name", "name"),
                F("marketing_name", "name"), F("parent_organization", "name"), F("organization_type", "text"),
                F("part_c_summary", "number"), F("part_d_summary", "number"), F("overall_rating", "number")),
        search=_search_ratings, read=_read_rating),
]


def _search_newest_study():
    data = http_json(TRIALS, params={"pageSize": 1, "fields": "LastUpdatePostDate", "sort": "LastUpdatePostDate:desc"}, tries=3)
    return ((((data.get("studies") or [{}])[0].get("protocolSection") or {}).get("statusModule") or {})
            .get("lastUpdatePostDateStruct") or {}).get("date")
