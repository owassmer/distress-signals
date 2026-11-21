"""USITC investigation schedules, participants and EDIS filings; OFAC's selected general licenses."""

import datetime as dt
import json
import os
import re
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urljoin
from xml.etree import ElementTree

import requests

from ..util import SourceError, session
from . import web

IDS = "https://ids.usitc.gov/investigations.json"
EDIS = "https://edis.usitc.gov/data"
LICENSES = "https://ofac.treasury.gov/selected-general-licenses-issued-ofac"
FILE_FINDER = "https://ofac.treasury.gov/file-finder"
FILTERS = {"usitc": {"target_from", "target_until", "filing_page", "offset", "limit"},
           "ofac": {"offset", "limit", "file_page"}}


def _get(url, **kwargs):
    try:
        response = session().get(url, timeout=90, **kwargs)
        response.raise_for_status()
        return response
    except requests.RequestException as exc:
        code = exc.response.status_code if exc.response is not None else type(exc).__name__
        raise SourceError(f"{url} could not be read ({code})") from exc


# Revalidate the daily export. A 304 saves the 37 MB download and JSON decoding on subsequent reads.
_snapshot = None


def _investigations():
    global _snapshot
    cached = _snapshot
    headers = {"If-Modified-Since": cached[0]} if cached and cached[0] else {}
    response = _get(IDS, headers=headers)
    if response.status_code == 304 and cached:
        return cached[1]
    try:
        payload = response.json()
        records = payload["data"]
        if not isinstance(records, list) or len(records) != int(payload["count"]):
            raise ValueError("incomplete export")
        data = {str(record["Investigation ID"]): record for record in records}
    except (KeyError, TypeError, ValueError) as exc:
        raise SourceError(f"USITC did not return its investigation export at {IDS}") from exc
    _snapshot = response.headers.get("Last-Modified"), data
    return data


def _date_field(value):
    if isinstance(value, dict):
        if value.get("isNa") or not value.get("date"):
            return None
        return value["date"][:10]
    if value:
        return dt.datetime.strptime(value, "%m-%d-%Y").date().isoformat()
    return None


def _iso_date(value):
    if value is None:
        return None
    try:
        return dt.date.fromisoformat(value).isoformat()
    except (TypeError, ValueError) as exc:
        raise SourceError("USITC target date filters use YYYY-MM-DD") from exc


def _investigation_row(record):
    native = record["Investigation ID"]
    participants = [{"name": (p.get("Participant") or {}).get("Name"),
                     "role": (p.get("Participant Type") or {}).get("name")}
                    for p in record.get("Participants", [])]
    return {"record_id": f"usitc:{native}", "title": record["Full Title"],
            "url": f"https://ids.usitc.gov/case/{record['Case ID']}/investigation/{native}",
            "investigation_number": record.get("Investigation Number"),
            "status": (record.get("Investigation Status") or {}).get("Name"), "participants": participants,
            "notice_of_institution_published": _date_field(record.get("Date of Publication of FR Notice (NOI)")),
            "target_date_order_issued": _date_field(record.get("Date ALJ Issued Target Date Order")),
            "target_date": _date_field(record.get("Target Date"))}


class _LicenseIndex(HTMLParser):
    """Keep the program heading and entire list item (the license subject often follows the anchor)."""
    def __init__(self):
        super().__init__()
        self.main = False
        self.heading = self.item = None
        self.program = self.url = ""
        self.rows = []

    def handle_starttag(self, tag, attrs):
        if tag == "main":
            self.main = True
        elif self.main and tag == "h3":
            self.heading = []
        elif self.main and self.program and tag == "li":
            self.item, self.url = [], ""
        elif self.item is not None and tag == "a":
            url = urljoin(LICENSES, dict(attrs).get("href", ""))
            if re.fullmatch(r"https://ofac\.treasury\.gov/media/\d+/download(?:\?.*)?", url):
                self.url = url

    def handle_data(self, text):
        if self.heading is not None:
            self.heading.append(text)
        if self.item is not None:
            self.item.append(text)

    def handle_endtag(self, tag):
        if tag == "main":
            self.main = False
        elif tag == "h3" and self.heading is not None:
            self.program = " ".join("".join(self.heading).split())
            self.heading = None
        elif tag == "li" and self.item is not None:
            if self.url:
                self.rows.append({"title": " ".join("".join(self.item).split()), "program": self.program,
                                  "url": self.url, "record_id": self.url})
            self.item = None


class _LicenseFiles(HTMLParser):
    def __init__(self):
        super().__init__()
        self.title = None
        self.in_title = self.has_next = self.empty = False
        self.rows = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "h5":
            self.in_title, self.title = True, []
        elif tag == "div" and "view-empty" in attrs.get("class", "").split():
            self.empty = True
        elif tag == "a":
            self.has_next |= "next" in attrs.get("rel", "").split()
            url = urljoin(FILE_FINDER, attrs.get("href", ""))
            if self.title and url.startswith("https://ofac.treasury.gov/system/files/"):
                self.rows.append({"record_id": url, "url": url, "title": "".join(self.title).strip(),
                                  "filename": url.rsplit("/", 1)[-1]})
                self.title = None

    def handle_data(self, text):
        if self.in_title:
            self.title.append(text)

    def handle_endtag(self, tag):
        if tag == "h5":
            self.in_title = False


def _license_files(query, page):
    if type(page) is not int or page < 1:
        raise SourceError("OFAC file_page starts at 1")
    params = {"search_api_fulltext": query, "field_tags": "14811", "page": page - 1}
    response = _get(FILE_FINDER, params=params)
    view = _LicenseFiles()
    view.feed(response.text)
    if not view.rows and not view.empty:
        raise SourceError("OFAC File Finder did not return the general license results page")
    return {"results": view.rows, "page": page, "next_page": page + 1 if view.has_next else None,
            "complete": not view.has_next, "index_url": response.url,
            "coverage": "Treasury File Finder, General Licenses; includes earlier versions."}


def _edis_rows(path, collection, params=None):
    response = _get(f"{EDIS}/{path}", params=params)
    try:
        group = ElementTree.fromstring(response.content).find(collection)
        if group is None:
            raise ValueError("missing collection")
        return [{field.tag: unescape(field.text or "") for field in item} for item in group]
    except (ValueError, ElementTree.ParseError) as exc:
        raise SourceError(f"EDIS did not return {collection} for {path}") from exc


def _filings(number, page):
    if not re.fullmatch(r"[\w-]+", number) or not isinstance(page, int) or isinstance(page, bool) or page < 1:
        raise SourceError("EDIS filing_page needs a positive page number and an investigation number as query")
    rows = _edis_rows("document", "documents", {"investigationNumber": number, "securityLevel": "Public", "pageNumber": page})
    results = [{"record_id": f"usitc:document/{d['id']}", "title": d["documentTitle"], "url": f"{EDIS}/document/{d['id']}",
                "investigation_number": d["investigationNumber"], "phase": d["investigationPhase"],
                "filed_on_behalf_of": d.get("onBehalfOf"), "received": d.get("officialReceivedDate")}
               for d in rows if d.get("securityLevel") == "Public"]
    # EDIS does not report a total. A subsequent empty page establishes the end.
    return {"results": results, "page": page, "next_page": page + 1 if rows else None, "complete": not rows}


def search(source, query, *, limit=50, offset=0, target_from=None, target_until=None, filing_page=None, file_page=None):
    if file_page is not None:
        if offset or limit != 50:
            raise SourceError("file_page selects an OFAC File Finder page; use offset and limit for the selected index")
        return _license_files(query, file_page)
    if filing_page is not None:
        if target_from or target_until or offset or limit != 50:
            raise SourceError("filing_page selects an EDIS page; use target dates, offset and limit for IDS searches")
        return _filings(query.strip(), filing_page)
    if type(limit) is not int or not 1 <= limit <= 250 or type(offset) is not int or offset < 0:
        raise SourceError("limit must be an integer from 1 to 250 and offset a nonnegative integer")
    words = query.casefold().split()
    if source == "ofac":
        index = _LicenseIndex()
        index.feed(_get(LICENSES).text)
        if not index.rows:
            raise SourceError(f"OFAC returned no license documents at {LICENSES}")
        rows = [row for row in index.rows if all(w in (row['program'] + ' ' + row['title']).casefold() for w in words)]
    else:
        start, end = _iso_date(target_from), _iso_date(target_until)
        if start and end and start > end:
            raise SourceError("target_from must be on or before target_until")
        rows = []
        for record in _investigations().values():
            row = _investigation_row(record)
            target = row["target_date"]
            if (start and (not target or target < start)) or (end and (not target or target > end)):
                continue
            searchable = " ".join([row["title"], row["investigation_number"] or "",
                                   *(p['name'] or '' for p in row['participants'])]).casefold()
            if all(word in searchable for word in words):
                rows.append(row)
        rows.sort(key=lambda r: (r["target_date"] or "9999", r["record_id"]))
    next_offset = offset + limit if offset + limit < len(rows) else None
    return {"results": rows[offset:offset + limit], "total_matches": len(rows), "next_offset": next_offset,
            "complete": next_offset is None, "index_url": LICENSES if source == "ofac" else IDS}


def read(source, native_id):
    if source != "usitc":
        raise SourceError("Read an OFAC license with the URL from its search result")
    if native_id.isdigit():
        record = _investigations().get(native_id)
        if not record:
            raise SourceError(f"USITC investigation {native_id} is absent from the current IDS export")
        row = _investigation_row(record)
        # Retain the source's labels and values, including superseded/cancelled events and participant roles.
        text = json.dumps(record, ensure_ascii=False, indent=2)
        links = [{"text": d.get("Document Title") or "Investigation document", "url": d["Document Link"]}
                 for d in record.get("Investigation Documents", []) if d.get("Document Link")]
        return {"kind": "investigation", "source": source, "native_id": native_id, "url": row["url"],
                "published": None, "text": text, "data": {**row, "links": links, "export": IDS}}
    document = re.fullmatch(r"document/(\d+)", native_id)
    attachment = re.fullmatch(r"attachment/(\d+)/(\d+)", native_id)
    if not document and not attachment:
        raise SourceError("Use the USITC record_id returned by search_records or the filing's attachment reference")
    did = (document or attachment)[1]
    docs = _edis_rows(f"document/{did}", "documents")
    if not docs or docs[0].get("id") != did or docs[0].get("securityLevel") != "Public":
        raise SourceError(f"EDIS document {did} is unavailable as a public filing")
    doc = docs[0]
    files = _edis_rows(f"attachment/{did}", "attachments")
    url = f"{EDIS}/document/{did}"
    title, kind = doc["documentTitle"], "filing"
    if attachment:
        aid = attachment[2]
        if not any(f.get("id") == aid and f.get("documentId") == did for f in files):
            raise SourceError(f"EDIS document {did} does not list attachment {aid}")
        token = os.getenv("EDIS_API_TOKEN")
        if not token:
            raise SourceError("Reading EDIS attachment PDFs requires EDIS_API_TOKEN from the EDIS account menu")
        url = f"{EDIS}/download/{did}/{aid}"
        with _get(url, headers={"Authorization": f"Bearer {token}"}, stream=True) as response:
            content = response.raw.read(web.MAX_BYTES + 1, decode_content=True)
        if not content.startswith(b"%PDF-") or len(content) > web.MAX_BYTES:
            raise SourceError("EDIS did not return a readable PDF within the document size limit")
        text, pdf_title = web._pdf(content)
        if len(text.strip()) < 40:
            raise SourceError("The EDIS attachment has no extractable text")
        title = pdf_title or title
    else:
        for f in files:
            f["record_id"] = f"usitc:attachment/{did}/{f['id']}"
        text = json.dumps({"document": doc, "attachments": files}, ensure_ascii=False, indent=2)
    return {"kind": kind, "source": source, "native_id": native_id, "url": url, "published": None,
            "text": text, "data": {"title": title, "received": doc.get("officialReceivedDate")}}
