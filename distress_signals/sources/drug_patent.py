"""FDA product protections, advisory meetings and approvals, and USPTO trial records."""

import datetime as dt
import io
import json
import os
import re
from urllib.parse import quote, urlencode, urlsplit

import requests

from ..util import SourceError, http_json, session, stable_id
from . import web

FDA = "https://api.fda.gov/drug"
CALENDAR = "https://www.fda.gov/datatables-json/advisory-committee-calendar-json"
PTAB = "https://api.uspto.gov/api/v1/patent/trials"
FILTERS = {
    "orange_book": {"expires_from", "expires_until", "limit", "offset"},
    "fda_advisory": {"record_type", "since", "until", "limit", "offset"},
    "ptab": {"record_type", "trial_number", "since", "until", "limit", "offset"},
}
HEARINGS = "https://www.uspto.gov/patents/ptab/hearings/hearing-schedules-ptab"
HEARING_COLUMNS = ["Proceeding No.", "Serial No.", "PARTY", "Date", "Time", "Location", "Room"]
PTAB_BAG = {"proceedings": "patentTrialProceedingDataBag", "documents": "patentTrialDocumentDataBag"}


def _request(url, **kwargs):
    # openFDA represents a valid empty search with a specific 404 response.
    try:
        return http_json(url, tries=3, timeout=45, **kwargs)
    except requests.HTTPError as exc:
        response = exc.response
        if response is not None:
            if response.status_code == 404 and url.startswith(FDA + "/"):
                try:
                    error = response.json().get("error", {})
                except ValueError:
                    error = {}
                if error.get("code") == "NOT_FOUND" and error.get("message") == "No matches found!":
                    return {"meta": {"results": {"total": 0}}, "results": []}
            raise SourceError(f"{url} returned HTTP {response.status_code}") from None
        raise SourceError(f"Request failed for {url}") from None
    except (requests.RequestException, ValueError) as exc:
        raise SourceError(f"Unable to read {url} ({type(exc).__name__})") from None


def _page(rows, total, offset):
    end = offset + len(rows)
    return {"results": rows, "total_matches": total, "complete": end >= total,
            "next_offset": end if rows and end < total else None}


def _bounds(start, end):
    for value in (start, end):
        if value is not None:
            try:
                if dt.date.fromisoformat(value).isoformat() != value:
                    raise ValueError
            except (ValueError, TypeError):
                raise SourceError("Dates must use YYYY-MM-DD") from None
    if start and end and start > end:
        raise SourceError("The start date must be on or before the end date")


def _literal(text):
    return '"' + str(text).replace('\\', '\\\\').replace('"', '\\"') + '"'


def _fda_records(dataset, clauses, limit, offset):
    params = {"limit": limit, "skip": offset}
    if clauses:
        params["search"] = " AND ".join(clauses)
    if offset > 25000:
        raise SourceError("openFDA allows skip up to 25000. Narrow the search for further matches.")
    return _request(f"{FDA}/{dataset}.json", params=params)


def _words(query, fields):
    # openFDA indexes some name fields as case-sensitive keywords and others as analyzed text.
    # Preserve phrase matching and include a case-insensitive Lucene term for mixed-case names.
    terms = [_literal(form) for form in dict.fromkeys((query, query.upper(), query.lower(), query.title()))]
    if any(c.isascii() and c.isalpha() for c in query):
        escaped = "".join(f"[{c.lower()}{c.upper()}]" if c.isascii() and c.isalpha()
                          else "\\" + c if c in r'\/.?+*|{}[]()"#@&<>~' else c for c in query)
        terms.append(f"/{escaped}/")
    names = "(" + " OR ".join(terms) + ")"
    return "(" + " OR ".join(f"{field}:{names}" for field in fields) + ")"


def _fda_url(dataset, expression):
    return f"{FDA}/{dataset}.json?" + urlencode({"search": expression, "limit": 1})


def _orange_key(item):
    product = item["products"][0]
    return product["application_type"], product["application_number"], item["product_number"]


def _orange_identity(key):
    fields = ("products.application_type", "products.application_number", "product_number")
    return " AND ".join(f"{field}:{_literal(value)}" for field, value in zip(fields, key))


def _orange(query, start, end, limit, offset):
    _bounds(start, end)
    fields = ("products.brand_name", "products.active_ingredients.name", "products.application_full_name",
              "products.application_name", "products.application_number", "patents.patent_number")
    clauses = [_words(query, fields)] if query else []
    if start or end:
        dates = f"[{start or '*'} TO {end or '*'}]"
        clauses.append(f"(patents.expiration_date:{dates} OR exclusivity.exclusivity_expiration_date:{dates})")
    response = _fda_records("orangebook", clauses, limit, offset)
    rows = []
    for item in response["results"]:
        products = item["products"]
        first = products[0]
        key = _orange_key(item)
        url = _fda_url("orangebook", _orange_identity(key))
        protections = []
        for group, date_field in (("patents", "expiration_date"), ("exclusivity", "exclusivity_expiration_date")):
            for entry in item.get(group, []):
                value = entry.get(date_field)
                if value:
                    date = dt.date.fromisoformat(value).isoformat()
                    if (not start or date >= start) and (not end or date <= end):
                        protections.append({"type": group, **entry})
        rows.append({"record_id": "orange_book:" + ":".join(key), "title": first.get("brand_name") or first["application_number"],
                     "url": url,
                     "product_number": item["product_number"], "products": products, "protections": protections})
    return _page(rows, response["meta"]["results"]["total"], offset)


def _approvals(query, limit, offset):
    fields = ("application_number", "sponsor_name", "products.brand_name", "products.active_ingredients.name")
    response = _fda_records("drugsfda", [_words(query, fields)] if query else [], limit, offset)
    rows = []
    for item in response["results"]:
        app = item["application_number"]
        url = _fda_url("drugsfda", f"application_number:{_literal(app)}")
        actions = [a for a in item.get("submissions", []) if a.get("submission_status_date")]
        latest = max(actions, key=lambda a: a["submission_status_date"], default=None)
        rows.append({"record_id": "fda_advisory:approval:" + app, "title": app + " — " + str(item.get("sponsor_name") or "FDA application"),
                     "url": url, "application_number": app, "sponsor_name": item.get("sponsor_name"),
                     "products": sorted({p["brand_name"] for p in item.get("products", []) if p.get("brand_name")}),
                     "latest_recorded_action": latest})
    return _page(rows, response["meta"]["results"]["total"], offset)


def _meetings(query, start, end, limit, offset):
    _bounds(start, end)
    rows = []
    for item in _request(CALENDAR):
        parser = web._Text(CALENDAR)
        parser.feed(item["title"])
        if len(parser.links) != 1:
            raise SourceError("FDA calendar returned a meeting without a unique announcement link")
        link = parser.links[0]
        title = "".join(parser.out).strip()
        searchable = " ".join([title, item["field_center"], item["field_contributing_office"]]).casefold()
        if query and query.casefold() not in searchable:
            continue
        raw_start = item["field_start_date"]
        day = dt.datetime.strptime(raw_start.split()[0], "%m/%d/%Y").date().isoformat() if raw_start else None
        if (start and (not day or day < start)) or (end and (not day or day > end)):
            continue
        rows.append({"record_id": link["url"], "url": link["url"], "title": title,
                     "meeting_date": day, "start": raw_start, "end": item["field_end_date"],
                     "center": item["field_center"], "contributing_office": item["field_contributing_office"]})
    rows.sort(key=lambda r: (r["meeting_date"] or "", r["title"]), reverse=True)
    return _page(rows[offset:offset + limit], len(rows), offset)


def _hearing_pdf(url):
    parts = urlsplit(url)
    return (parts.scheme == "https" and parts.netloc == "www.uspto.gov"
            and parts.path.startswith("/sites/default/files/") and parts.path.lower().endswith(".pdf")
            and not parts.query and not parts.fragment and ".." not in parts.path)


def _hearing_rows(url):
    import pdfplumber

    if not _hearing_pdf(url):
        raise SourceError("Invalid USPTO hearing schedule URL")
    with session().get(url, timeout=45, stream=True) as response:
        response.raise_for_status()
        content = response.raw.read(web.MAX_BYTES + 1, decode_content=True)
    if len(content) > web.MAX_BYTES:
        raise SourceError("USPTO hearing schedule exceeds the PDF size limit")
    rows = []
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        for page in pdf.pages:
            table = page.extract_table() or []
            if HEARING_COLUMNS not in table:
                raise SourceError(f"Unrecognized USPTO schedule table on page {page.page_number} of {url}")
            for cells in table[table.index(HEARING_COLUMNS) + 1:]:
                if not any(cells):
                    continue
                if len(cells) != len(HEARING_COLUMNS) or not all(cells[i] for i in (0, 2, 3)):
                    raise SourceError(f"Incomplete hearing row on page {page.page_number} of {url}")
                proceedings, serial, parties, date, time, location, room = cells
                try:
                    day = dt.datetime.strptime(date.strip(), "%A, %B %d, %Y").date().isoformat()
                except ValueError:
                    raise SourceError(f"Unrecognized USPTO hearing date {date!r} in {url}") from None
                ids = ["".join(x.split()) for x in proceedings.splitlines() if x.strip()]
                hearing = {"proceedings": ids, "serial_number": serial, "parties": " ".join(parties.split()),
                           "hearing_date": day, "time": time, "location": location, "room": room}
                key = stable_id(*ids, day, time)
                rows.append({"record_id": f"ptab:hearings:{key}:{url}", "title": f"{hearing['parties']} — {day}",
                             "url": f"{url}#page={page.page_number}", **hearing})
    return rows


def _hearings(query, trial_number, start, end, limit, offset):
    _bounds(start, end)
    index = web.read(HEARINGS)
    links = [link["url"] for link in index["data"]["links"]
             if "hearing schedule" in link["text"].lower() and _hearing_pdf(link["url"])]
    if not links:
        raise SourceError(f"USPTO has no linked hearing schedule PDF at {HEARINGS}")
    matches = []
    for url in links:
        for row in _hearing_rows(url):
            if trial_number and "".join(trial_number.split()).upper() not in row["proceedings"]:
                continue
            day = row["hearing_date"]
            if (start and day < start) or (end and day > end):
                continue
            terms = " ".join([row["title"], *row["proceedings"], row["serial_number"] or "", row["location"] or ""])
            if query and query.casefold() not in terms.casefold() and "".join(query.split()).upper() not in row["proceedings"]:
                continue
            matches.append(row)
    matches.sort(key=lambda r: (r["hearing_date"], r["proceedings"]))
    return {**_page(matches[offset:offset + limit], len(matches), offset), "schedule_editions": links}


def _odp(path, params=None):
    key = os.environ.get("USPTO_API_KEY")
    if not key:
        raise SourceError("PTAB proceedings and filings require USPTO_API_KEY from the USPTO Open Data Portal. "
                          "Use record_type=hearings for the public PTAB hearing calendar without a key.")
    return _request(f"{PTAB}/{path}", params=params, headers={"X-API-KEY": key})


def _ptab_row(item, kind):
    trial = item["trialNumber"]
    document = item.get("documentData") or {}
    identifier = document["documentIdentifier"] if kind == "documents" else trial
    title = document.get("documentTitleText") or document.get("documentName") or trial
    url = f"{PTAB}/{kind}/{quote(identifier, safe='')}"
    return {"record_id": f"ptab:{kind}:{identifier}", "title": title,
            "url": document.get("fileDownloadURI") or document.get("downloadURI") or url,
            **{k: v for k, v in item.items() if k != "documentData"},
            **({"documentData": {k: v for k, v in document.items() if k != "documentOCRText"}} if document else {})}


def _ptab(query, kind, trial_number, limit, offset):
    if kind not in PTAB_BAG:
        raise SourceError("PTAB record_type must be proceedings, documents or hearings")
    params = {"limit": limit, "offset": offset}
    terms = [f"({query})"] if query else []
    if trial_number:
        terms.append("trialNumber:" + _literal(trial_number))
    if terms:
        params["q"] = " AND ".join(terms)
    if kind == "documents":
        # The detail read retrieves OCR. The index supplies the fields needed to identify each filing.
        params["fields"] = ("trialNumber,trialMetaData,patentOwnerData,regularPetitionerData,respondentData,"
                            "derivationPetitionerData,documentData.documentIdentifier,documentData.documentName,"
                            "documentData.documentTitleText,documentData.documentCategory,documentData.documentFilingDate,"
                            "documentData.documentTypeDescriptionText,documentData.filingPartyCategory,documentData.fileDownloadURI")
    page = _odp(kind + "/search", params)
    return _page([_ptab_row(item, kind) for item in page[PTAB_BAG[kind]]], page["count"], offset)


def search(source, query=None, *, limit=20, offset=0, **filters):
    if int(limit) < 1 or int(limit) > 99 or int(offset) < 0:
        raise SourceError("Use a limit from 1 to 99 and a nonnegative offset")
    limit, offset = int(limit), int(offset)
    if source == "ptab":
        kind = filters.get("record_type", "proceedings")
        if kind == "hearings":
            return _hearings(query, filters.get("trial_number"), filters.get("since"), filters.get("until"), limit, offset)
        if filters.get("since") or filters.get("until"):
            raise SourceError("since and until filter scheduled PTAB hearings; use ODP field syntax for other dates")
        return _ptab(query, kind, filters.get("trial_number"), limit, offset)
    if source == "orange_book":
        return _orange(query, filters.get("expires_from"), filters.get("expires_until"), limit, offset)
    if source == "fda_advisory":
        kind = filters.get("record_type", "meetings")
        if kind == "meetings":
            return _meetings(query, filters.get("since"), filters.get("until"), limit, offset)
        if kind == "approvals":
            if filters.get("since") or filters.get("until"):
                raise SourceError("since and until filter meeting dates; omit them for approvals")
            return _approvals(query, limit, offset)
        raise SourceError("FDA record_type must be meetings or approvals")
    raise SourceError(f"Unknown source {source!r}")


def _read_fda(source, native_id):
    if source == "orange_book":
        if not re.fullmatch(r"[AN]:[0-9]{6}:[0-9]{3}", native_id):
            raise SourceError("Invalid Orange Book product ID")
        key = tuple(native_id.split(":"))
        dataset, kind, match = "orangebook", "orange_book_product", _orange_identity(key)
    else:
        if not re.fullmatch(r"approval:(NDA|ANDA|BLA)[0-9]{6}", native_id):
            raise SourceError("Invalid Drugs@FDA application ID")
        app = native_id.removeprefix("approval:")
        dataset, kind, match = "drugsfda", "fda_application", "application_number:" + _literal(app)
    response = _fda_records(dataset, [match], 1, 0)
    if response["meta"]["results"]["total"] != 1 or len(response["results"]) != 1:
        raise SourceError(f"openFDA did not return a unique record for {native_id}")
    item = response["results"][0]
    if source == "orange_book":
        if _orange_key(item) != key:
            raise SourceError("FDA returned a different Orange Book product")
        title = (item["products"][0].get("brand_name") or "Orange Book product") + " — " + native_id
    else:
        if item["application_number"] != app:
            raise SourceError("FDA returned a different drug application")
        title = app + " — " + str(item.get("sponsor_name") or "FDA application")
    data = {"title": title, **item, "dataset_last_updated": response["meta"].get("last_updated")}
    return {"source": source, "kind": kind, "native_id": native_id, "url": _fda_url(dataset, match),
            "published": None, "data": data, "text": json.dumps(data, ensure_ascii=False, indent=2)}


def read(source, native_id):
    if source in ("orange_book", "fda_advisory"):
        return _read_fda(source, native_id)
    kind, _, identifier = native_id.partition(":")
    if source == "ptab" and kind == "hearings":
        key, _, url = identifier.partition(":")
        if not re.fullmatch(r"[0-9a-f]{20}", key) or not _hearing_pdf(url):
            raise SourceError("Invalid PTAB hearing record ID")
        rows = [row for row in _hearing_rows(url) if row["record_id"] == f"ptab:{native_id}"]
        if len(rows) != 1:
            raise SourceError("The cited USPTO schedule does not contain a unique matching hearing")
        row = rows[0]
        return {"source": source, "kind": "ptab_hearing", "native_id": native_id, "url": row["url"],
                "published": None, "data": row, "text": json.dumps(row, ensure_ascii=False, indent=2)}
    if source != "ptab" or kind not in PTAB_BAG or not re.fullmatch(r"[A-Za-z0-9_-]+", identifier):
        raise SourceError("Invalid PTAB record ID")
    page = _odp(f"{kind}/{identifier}")
    matches = page.get(PTAB_BAG[kind]) or []
    if len(matches) != 1:
        raise SourceError(f"Expected one PTAB {kind} record for {identifier}, received {len(matches)}")
    item = matches[0]
    row = _ptab_row(item, kind)
    if row["record_id"] != "ptab:" + native_id:
        raise SourceError("USPTO returned a different record identifier")
    text = json.dumps(row, ensure_ascii=False, indent=2)
    document = item.get("documentData") or {}
    if document.get("documentOCRText"):
        text += "\n\n" + document["documentOCRText"]
    return {"source": source, "kind": "ptab_document" if kind == "documents" else "ptab_proceeding",
            "native_id": native_id, "url": row["url"], "published": document.get("documentFilingDate"),
            "data": row, "text": text}
