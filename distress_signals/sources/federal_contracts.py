"""SAM opportunity notices, USAspending award lookups, and DoD's published contract announcements."""

import csv
import datetime as dt
import os
import re
import tempfile
import threading
from email.utils import formatdate, parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote, urlparse
from xml.etree import ElementTree

import requests
from bs4 import BeautifulSoup

from ..util import OUT, SourceError, http_json, session, today
from . import usaspending as public_records  # until this module is on the record shape

SAM_API = "https://api.sam.gov/opportunities/v2/search"
SAM_CSV = ("https://sam.gov/api/prod/fileextractservices/v1/api/download/"
           "Contract%20Opportunities/datagov/ContractOpportunitiesFullCSV.csv")
DOD_RSS = "https://public.govdelivery.com/accounts/USDOD/feed.rss"
BULLETINS = "https://content.govdelivery.com/accounts/USDOD/bulletins/"
FILTERS = {
    "sam_opportunities": {"collection", "since", "until", "deadline_from", "deadline_until", "notice_type",
                          "solicitation_number", "award_types", "agency_code", "parent_award_number", "parent_agency_code", "page", "limit"},
    "dod_contracts": {"bulletin_id", "since", "until"},
}
TYPES = {"a": "Award Notice", "p": "Presolicitation", "r": "Sources Sought", "s": "Special Notice",
         "o": "Solicitation", "k": "Combined Synopsis/Solicitation", "g": "Sale of Surplus Property",
         "u": "Justification", "i": "Intent to Bundle Requirements (DoD-Funded)"}
SAM_HEADERS = {"NoticeId", "Title", "Type", "PostedDate", "ResponseDeadLine", "AwardNumber", "Awardee", "Description"}
_cache_lock = threading.Lock()


def _dates(start, end):
    try:
        if any(dt.date.fromisoformat(s).isoformat() != s for s in (start, end) if s is not None):
            raise ValueError
        if start and end and start > end:
            raise ValueError
    except (TypeError, ValueError) as exc:
        raise SourceError("Give YYYY-MM-DD dates with the start on or before the end") from exc
    return start, end


def _in_window(value, start, end):
    day = (value or "")[:10]
    return (not start or bool(day and day >= start)) and (not end or bool(day and day <= end))


def _csv_path():
    folder = OUT / "sources"
    folder.mkdir(parents=True, exist_ok=True)
    cache = folder / "sam_active_notices.csv"
    with _cache_lock:
        headers = {"If-Modified-Since": formatdate(cache.stat().st_mtime, usegmt=True)} if cache.is_file() else {}
        with session().get(SAM_CSV, headers=headers, timeout=90, stream=True) as response:
            if response.status_code == 304 and cache.is_file():
                return cache
            response.raise_for_status()
            modified = response.headers.get("Last-Modified")
            # Keep the previous copy intact on a failed or interrupted transfer.
            with tempfile.NamedTemporaryFile(dir=folder, delete=False) as temp:
                name = Path(temp.name)
                try:
                    for chunk in response.iter_content(1024 * 1024):
                        temp.write(chunk)
                    temp.close()
                    with name.open("rb") as handle:
                        header = next(csv.reader([handle.readline().decode("utf-8-sig")]), [])
                    if not SAM_HEADERS.issubset(header):
                        raise SourceError("GSA did not return the active SAM notice CSV")
                    if modified:
                        stamp = parsedate_to_datetime(modified).timestamp()
                        os.utime(name, (stamp, stamp))
                    os.replace(name, cache)
                finally:
                    name.unlink(missing_ok=True)
    return cache


def _lines(handle):
    # GSA's CSV mixes UTF-8 with Windows-1252 bytes in notice text.
    replacements = {0xDC00 + i: bytes([i]).decode("cp1252", errors="replace") for i in range(128, 256)}
    for index, line in enumerate(handle):
        if index == 0:
            line = line.removeprefix(b"\xef\xbb\xbf")
        try:
            yield line.decode("utf-8")
        except UnicodeDecodeError:
            yield line.decode("utf-8", errors="surrogateescape").translate(replacements)


def _extract_row(record):
    notice = record["NoticeId"]
    return {"record_id": "sam_opportunities:extract/" + notice, "title": record["Title"],
            "url": record.get("Link") or f"https://sam.gov/opp/{notice}/view", "notice_id": notice,
            "notice_type": record["Type"], "solicitation_number": record.get("Sol#"), "published": record["PostedDate"],
            "response_deadline": record.get("ResponseDeadLine"), "department": record.get("Department/Ind.Agency"),
            "office": record.get("Office"), "fpds_agency_code": record.get("FPDS Code"), "award_number": record.get("AwardNumber"), "award_date": record.get("AwardDate"),
            "awardee_as_published": record.get("Awardee"), "award_amount": record.get("Award$")}


def _extract_search(query, page, limit, since=None, until=None, deadline_from=None, deadline_until=None,
                    notice_type=None, solicitation_number=None):
    _dates(since, until)
    _dates(deadline_from, deadline_until)
    words = query.casefold().split()
    selected, count = [], 0
    path = _csv_path()
    with path.open("rb") as handle:
        for r in csv.DictReader(_lines(handle)):
            if notice_type and r["Type"].casefold() != TYPES.get(notice_type, notice_type).casefold():
                continue
            if solicitation_number and r.get("Sol#", "").strip().casefold() != solicitation_number.casefold():
                continue
            if not _in_window(r["PostedDate"], since, until) or not _in_window(r["ResponseDeadLine"], deadline_from, deadline_until):
                continue
            fields = (r.get(k, "") or "" for k in ("Title", "Sol#", "NoticeId", "Department/Ind.Agency", "Sub-Tier",
                                                    "Office", "Awardee", "AwardNumber"))
            text = " ".join(fields).casefold()
            if all(w in text for w in words):
                if (page - 1) * limit <= count < page * limit:
                    selected.append(_extract_row(r))
                count += 1
    return {"results": selected, "total_matches": count, "complete": page * limit >= count,
            "next_page": page + 1 if page * limit < count else None, "collection": "GSA daily active notices"}


def _sam_json(url, params):
    key = os.environ.get("SAM_API_KEY")
    if not key:
        raise SourceError("Set SAM_API_KEY, or choose collection=active_file for GSA’s public daily download (about 225 MB).")
    try:
        return http_json(url, params={**params, "api_key": key}, tries=2, timeout=45)
    except (requests.RequestException, ValueError) as exc:
        response = getattr(exc, "response", None)
        status = response.status_code if response is not None else type(exc).__name__
        # The API key is a query parameter, including in requests' native exception string.
        raise SourceError(f"SAM opportunities API request failed ({status}); the active_file collection is public.") from None


def _api_row(record):
    notice, posted = record["noticeId"], record["postedDate"]
    award = record.get("award") or {}
    return {"record_id": f"sam_opportunities:api/{posted[:10]}/{notice}", "notice_id": notice,
            "title": record["title"], "url": f"https://sam.gov/opp/{notice}/view", "published": posted,
            "notice_type": record.get("type"), "solicitation_number": record.get("solicitationNumber"),
            "response_deadline": record.get("responseDeadLine"), "agency": record.get("fullParentPathName"),
            "agency_code_path": record.get("fullParentPathCode"),
            "award_number": award.get("number"), "awardee_as_published": award.get("awardee"),
            "award_date": award.get("date"), "award_amount": award.get("amount")}


def _api_search(query, page, limit, since=None, until=None, deadline_from=None, deadline_until=None,
                notice_type=None, solicitation_number=None, noticeid=None):
    end = until or today().isoformat()
    _dates(since, end)
    start = since or (dt.date.fromisoformat(end) - dt.timedelta(days=30)).isoformat()
    _dates(deadline_from, deadline_until)
    def us_date(value):
        return dt.date.fromisoformat(value).strftime("%m/%d/%Y") if value else None
    if notice_type:
        reverse_types = {label.casefold(): code for code, label in TYPES.items()}
        notice_type = reverse_types.get(notice_type.casefold(), notice_type)
        if notice_type not in TYPES:
            raise SourceError(f"SAM API notice_type accepts {', '.join(TYPES.values())} or their GSA codes")
    params = {"title": query or None, "postedFrom": us_date(start), "postedTo": us_date(end),
              "rdlfrom": us_date(deadline_from), "rdlto": us_date(deadline_until), "ptype": notice_type,
              "solnum": solicitation_number, "noticeid": noticeid, "limit": limit, "offset": page - 1}
    payload = _sam_json(SAM_API, {k: v for k, v in params.items() if v is not None})
    if not isinstance(payload.get("opportunitiesData"), list) or "totalRecords" not in payload:
        raise SourceError("SAM returned no notice collection or result count")
    return payload


def _award_summary(key):
    if not re.fullmatch(r"CONT_(?:AWD|IDV)_[A-Za-z0-9_-]+", key):
        raise SourceError("Use a USAspending award record ID returned by award_lookup")
    url = f"{public_records.API}/awards/{quote(key, safe='')}/"
    try:
        d = http_json(url, tries=2, timeout=45)
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            return None
        raise
    if d.get("generated_unique_award_id") != key:
        raise SourceError("USAspending returned a different award identifier")
    recipient, agency = d.get("recipient") or {}, (d.get("awarding_agency") or {}).get("subtier_agency") or {}
    return {"record_id": "sam_opportunities:award/" + key, "full_award_record_id": "usaspending:award:" + key,
            "publisher": "USAspending", "title": d.get("description") or key, "url": f"https://www.usaspending.gov/award/{key}",
            "award_number": d.get("piid"), "award_type": d.get("type_description"), "recipient": recipient.get("recipient_name"),
            "recipient_uei": recipient.get("recipient_uei"), "agency": agency.get("name"), "agency_code": agency.get("code"),
            "date_signed": d.get("date_signed"), "total_obligation": d.get("total_obligation"),
            "period_of_performance": d.get("period_of_performance"), "description": d.get("description")}


def _scoped_award(number, award_types, agency_code, parent_award_number=None, parent_agency_code=None):
    if bool(parent_award_number) != bool(parent_agency_code):
        raise SourceError("Supply both parent_award_number and parent_agency_code for an order")
    if not all(isinstance(x, str) and re.fullmatch(r"[A-Za-z0-9-]+", x)
               for x in (number, agency_code, parent_award_number or "-NONE-", parent_agency_code or "-NONE-")):
        raise SourceError("Use the published PIID and FPDS agency code from the award")
    if award_types == "idvs" and not parent_award_number:
        key = f"CONT_IDV_{number}_{agency_code}"
    elif award_types == "contracts":
        key = f"CONT_AWD_{number}_{agency_code}_{parent_award_number or '-NONE-'}_{parent_agency_code or '-NONE-'}"
    else:
        raise SourceError("award_types is contracts or idvs; parent IDs apply to contract orders")
    row = _award_summary(key.upper())
    return {"results": [row] if row else [], "complete": True, "checked_award_key": key.upper(),
            "scope": "One USAspending generated award key. Compare the award's recipient, description and date with the notice."}


def _lookup(award_number, page, limit, award_types):
    if not award_number.strip() or '"' in award_number or award_types not in ("contracts", "idvs"):
        raise SourceError("Give the award number as query and choose award_types=contracts or idvs")
    # USAspending accepts quoted PIIDs as exact-match filters; one PIID can occur at more than one agency.
    number = award_number.strip()
    filters = {"award_ids": [f'"{number}"'], "award_type_codes": public_records.AWARD_TYPES[award_types],
               "time_period": [{"start_date": public_records.EARLIEST, "end_date": today().isoformat()}]}
    data = public_records._call("/search/spending_by_award/", {"filters": filters, "fields": public_records.FIELDS,
                                                               "limit": limit, "page": page, "sort": "Award Amount",
                                                               "order": "desc"})
    rows = [public_records._row(r) for r in data["results"] if str(r.get("Award ID", "")).casefold() == number.casefold()]
    more = data["page_metadata"]["hasNext"]
    return {"results": rows, "complete": not more, "next_page": page + 1 if more else None,
            "collection": "USAspending award numbers"}


def _dod_bulletin(identifier):
    if not isinstance(identifier, str) or not re.fullmatch(r"[0-9a-f]+", identifier):
        raise SourceError("Give a GovDelivery USDOD bulletin ID")
    url = BULLETINS + identifier
    response = session().get(url, timeout=40)
    response.raise_for_status()
    soup = BeautifulSoup(response.content, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    body = soup.select_one("#article") or soup.select_one("#bulletin_body .rss_description")
    if not body or not title.casefold().startswith("contracts for "):
        raise SourceError(f"{url} did not return a DoD contract announcement")
    stamp = soup.select_one(".dateline")
    sent = stamp.get_text(" ", strip=True) if stamp else ""
    match = re.search(r"sent this bulletin at (\d{2}/\d{2}/\d{4})", sent)
    published = dt.datetime.strptime(match[1], "%m/%d/%Y").date().isoformat() if match else None
    return {"record_id": f"dod_contracts:{identifier}", "url": url, "title": title, "published": published,
            "bulletin_dateline": sent, "text": body.get_text("\n", strip=True)}


def _dod_search(query, bulletin_id=None, since=None, until=None):
    _dates(since, until)
    if bulletin_id:
        records = [_dod_bulletin(bulletin_id)]
        scope = "One requested DoD contract announcement"
    else:
        response = session().get(DOD_RSS, timeout=40)
        response.raise_for_status()
        channel = ElementTree.fromstring(response.content).find("channel")
        if channel is None:
            raise SourceError("DoD's GovDelivery account returned no RSS channel")
        items = channel.findall("item")
        records = []
        for item in items:
            if not (item.findtext("title") or "").casefold().startswith("contracts for "):
                continue
            date = parsedate_to_datetime(item.findtext("pubDate")).date().isoformat()
            if not _in_window(date, since, until):
                continue
            url = item.findtext("link") or ""
            if not url.startswith(BULLETINS):
                raise SourceError("The contract bulletin's URL was outside the department's GovDelivery archive")
            records.append(_dod_bulletin(url.removeprefix(BULLETINS)))
        scope = f"Contract announcements in the department's latest {len(items)} account bulletins"
    words = query.casefold().split()
    results = []
    for record in records:
        body = record.pop("text")
        if _in_window(record["published"], since, until) and all(w in (record["title"] + " " + body).casefold() for w in words):
            record["excerpt"] = next((p for p in body.splitlines() if all(w in p.casefold() for w in words)), "")[:1400]
            results.append(record)
    return {"results": results, "complete": True, "scope": scope, "announcements_scanned": len(records)}


def search(source, query, **filters):
    query = query or ""
    try:
        if source == "dod_contracts":
            return _dod_search(query, **filters)
        page, limit = filters.pop("page", 1), filters.pop("limit", 25)
        if type(page) is not int or page < 1 or type(limit) is not int or not 1 <= limit <= 100:
            raise SourceError("Use a positive page number and a limit from 1 to 100")
        default = "api" if os.environ.get("SAM_API_KEY") else "active_file"
        collection, types = filters.pop("collection", default), filters.pop("award_types", None)
        scoped = {k: filters.pop(k) for k in ("agency_code", "parent_award_number", "parent_agency_code") if k in filters}
        if scoped:
            if collection != "award_lookup" or not scoped.get("agency_code") or filters or page != 1:
                raise SourceError("An agency-scoped lookup requires collection=award_lookup and agency_code on page 1")
            return _scoped_award(query.strip(), types or "contracts", **scoped)
        if collection == "award_lookup" and not filters:
            return _lookup(query, page, limit, types or "contracts")
        if types or collection not in {"api", "active_file"}:
            raise SourceError("Use collection=api, active_file, or award_lookup; award_types applies only to award_lookup")
        if collection == "active_file":
            return _extract_search(query, page, limit, **filters)
        data = _api_search(query, page, limit, **filters)
        count = data["totalRecords"]
        return {"results": [_api_row(r) for r in data["opportunitiesData"]], "total_matches": count,
                "complete": page * limit >= count, "next_page": page + 1 if page * limit < count else None}
    except (requests.RequestException, ElementTree.ParseError, csv.Error, UnicodeError) as exc:
        raise SourceError(f"{source} could not read the published records ({type(exc).__name__})") from exc


def read(source, native_id):
    try:
        if source == "dod_contracts":
            row = _dod_bulletin(native_id)
            body = row.pop("text")
        elif native_id.startswith("award/"):
            row = _award_summary(native_id.removeprefix("award/"))
            if row is None:
                raise SourceError("USAspending no longer returns this award")
            body = ""
        elif match := re.fullmatch(r"extract/([a-zA-Z0-9_-]+)", native_id):
            with _csv_path().open("rb") as handle:
                raw = next((r for r in csv.DictReader(_lines(handle)) if r["NoticeId"] == match[1]), None)
            if not raw:
                raise SourceError("This notice is absent from GSA's current active file; use the SAM archive or notices API.")
            row = _extract_row(raw)
            body = raw.get("Description") or ""
        elif match := re.fullmatch(r"api/(\d{4}-\d{2}-\d{2})/([a-zA-Z0-9_-]+)", native_id):
            data = _api_search("", 1, 10, since=match[1], until=match[1], noticeid=match[2])
            raw = next((r for r in data["opportunitiesData"] if r["noticeId"] == match[2]), None)
            if not raw:
                raise SourceError("SAM returned no matching notice on its reported posting date")
            row = _api_row(raw)
            body = ""
            url = raw.get("description")
            if url and urlparse(url).scheme == "https" and urlparse(url).hostname == "api.sam.gov":
                description = _sam_json(url, {}).get("description")
                if description:
                    body = BeautifulSoup(description, "html.parser").get_text(" ", strip=True)
            row["links"] = [{"text": "Notice attachment", "url": uri} for uri in raw.get("resourceLinks") or []]
        else:
            raise SourceError("Use a notice or bulletin record ID from search_records")
    except (requests.RequestException, csv.Error, UnicodeError) as exc:
        raise SourceError(f"{source} could not read the source record ({type(exc).__name__})") from exc
    lines = [f"{k}: {v}" for k, v in row.items() if k not in {"record_id", "links"} and v not in (None, "")]
    return {"kind": "public_record", "source": source, "native_id": native_id, "url": row["url"],
            "published": row.get("published"), "text": "\n".join([*lines, "", body]), "data": row}
