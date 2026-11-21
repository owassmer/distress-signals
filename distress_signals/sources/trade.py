"""Three series: USITC investigations, the public documents filed in them (EDIS), and OFAC's general licenses.

  usitc_investigations     the Commission's daily export of every investigation, read whole and filtered here.
  usitc_filings            EDIS lists public filings newest first, 20 at a time, by investigation, document type
                           and firm. An attachment's text needs an EDIS account token (EDIS_API_TOKEN); without
                           one a read returns the filing's details and lists its attachments.
  ofac_general_licenses    Treasury's File Finder, restricted to general licenses: every version it has posted,
                           searched in full text.
"""

import datetime as dt
import json
import os
import re
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urljoin
from xml.etree import ElementTree

import requests

from ..util import Missing, SourceError, session
from . import F, Series, outline, page, web

IDS = "https://ids.usitc.gov/investigations.json"
EDIS = "https://edis.usitc.gov/data"
LICENSES = "https://ofac.treasury.gov/selected-general-licenses-issued-ofac"
FILE_FINDER = "https://ofac.treasury.gov/file-finder"


def _get(url, **kwargs):
    try:
        response = session().get(url, timeout=90, **kwargs)
        response.raise_for_status()
        return response
    except requests.RequestException as exc:
        code = exc.response.status_code if exc.response is not None else type(exc).__name__
        raise (Missing if code in (404, 410) else SourceError)(f"{url} could not be read ({code})") from None


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


def _edis_rows(path, collection, params=None):
    response = _get(f"{EDIS}/{path}", params=params)
    try:
        group = ElementTree.fromstring(response.content).find(collection)
        if group is None:
            raise ValueError("missing collection")
        return [{field.tag: unescape(field.text or "") for field in item} for item in group]
    except (ValueError, ElementTree.ParseError) as exc:
        raise SourceError(f"EDIS did not return {collection} for {path}") from exc




# ---------- investigations ----------

STATUS = ("Active", "Pending before the ALJ", "Pending before the Commission", "Completed", "Terminated", "Inactive",
          "Not Instituted", "Complaint/Request Withdrawn")
MOVING = ("Complainant", "Petitioner", "Non-Party Petitioner")


def _name(value):
    return (value or {}).get("Name") or (value or {}).get("name") if isinstance(value, dict) else value


def _investigation(record):
    native = record["Investigation ID"]
    party = lambda roles, others=False: [  # noqa: E731
        (p.get("Participant") or {}).get("Name") for p in record.get("Participants") or []
        if (p.get("Participant") or {}).get("Name") and (((p.get("Participant Type") or {}).get("name") in roles) != others)]
    return {"id": str(native), "url": f"https://ids.usitc.gov/case/{record.get('Case ID')}/investigation/{native}",
            "investigation_number": record.get("Investigation Number"), "title": record.get("Full Title"),
            "investigation_type": _name(record.get("Investigation Type")), "phase": _name(record.get("Investigation Phase")),
            "status": _name(record.get("Investigation Status")),
            "country": [c.get("name") for c in record.get("Countries") or [] if c.get("name")],
            "complainant": party(MOVING), "respondent": party(("Respondent",)), "other_party": party(MOVING + ("Respondent",), True),
            "start_date": _date_field(record.get("Start Date")),
            "institution_notice": _date_field(record.get("Date of Publication of FR Notice (NOI)")),
            "target_date": _date_field(record.get("Target Date")), "vote_date": _date_field(record.get("Vote Date")),
            "commerce_final_determination": _date_field(record.get("Commerce Final Determination Date"))}


def _search_investigations(s):
    rows = [_investigation(r) for r in _investigations().values()]
    rows.sort(key=lambda r: r["start_date"] or r["institution_notice"] or "", reverse=True)
    return page(rows, order="latest start date first")


def _read_investigation(native):
    record = _investigations().get(str(native))
    if not record:
        raise SourceError(f"USITC investigation {native} is not in the current export")
    links = [{"text": d.get("Document Title") or "Investigation document", "url": d["Document Link"]}
             for d in record.get("Investigation Documents") or [] if d.get("Document Link")]
    return {**_investigation(record), "document": "\n".join(outline(record)), "links": links}


def _investigations_span(series):
    dates = [d for r in _investigations().values() for d in [_date_field(r.get("Start Date"))] if d]
    return (min(dates), max(dates)) if dates else (None, None)


# ---------- filings (EDIS) ----------

def _filing(d):
    stamp = lambda v: (v or "")[:10].replace("/", "-") or None  # noqa: E731
    return {"id": d["id"], "url": f"https://edis.usitc.gov/external/search/document/{d['id']}", "document_id": d["id"],
            "document_type": d.get("documentType"), "title": d.get("documentTitle"),
            "investigation_number": d.get("investigationNumber"), "investigation_title": d.get("investigationTitle"),
            "investigation_type": d.get("investigationType"), "investigation_phase": d.get("investigationPhase"),
            "firm": d.get("firmOrganization"), "filed_by": d.get("filedBy"), "on_behalf_of": d.get("onBehalfOf"),
            "document_date": stamp(d.get("documentDate")), "received_date": stamp(d.get("officialReceivedDate"))}


def _search_filings(s):
    ids = s.any("document_id")
    if ids:
        found = [d for i in ids for d in _edis_rows(f"document/{i}", "documents") if d.get("securityLevel") == "Public"]
        return page([_filing(d) for d in found])
    params = {"securityLevel": "Public", "pageNumber": s.cursor or 1}
    number, kind = s.one("investigation_number"), s.one("document_type")
    phase = s.one("investigation_phase")
    params |= {"investigationPhase": phase} if phase else {}
    firm = (s.peek("firm").get("is") or [None])[0]  # EDIS narrows by firm; the registry checks the words
    lo, hi = s.between("received_date")
    for key, value in (("investigationNumber", number), ("documentType", kind), ("firmOrg", firm),
                       ("officialReceivedDate", lo if lo and lo == hi else None)):
        if value:
            params[key] = value
    rows = [_filing(d) for d in _edis_rows("document", "documents", params) if d.get("securityLevel") == "Public"]
    done = not rows or bool(lo and rows[-1]["received_date"] and rows[-1]["received_date"] < lo)  # newest first: past the range
    kept = [r for r in rows if (not lo or (r["received_date"] or "") >= lo) and (not hi or (r["received_date"] or "9") <= hi)]
    return page(kept, None if done else params["pageNumber"] + 1, order="latest received first")


def _read_filing(did):
    docs = _edis_rows(f"document/{did}", "documents")
    if not docs or docs[0].get("id") != str(did) or docs[0].get("securityLevel") != "Public":
        raise SourceError(f"EDIS document {did} is unavailable as a public filing")
    files = [f for f in _edis_rows(f"attachment/{did}", "attachments")]
    lines = [f"Attachments ({len(files)}):"] + [f"  {f.get('id')}: {f.get('title') or f.get('originalFileName') or ''} "
                                                 f"({f.get('fileSize') or '?'} bytes)" for f in files]
    token = os.getenv("EDIS_API_TOKEN")
    for f in files[:8] if token else []:
        url = f"{EDIS}/download/{did}/{f['id']}"
        try:
            with _get(url, headers={"Authorization": f"Bearer {token}"}, stream=True) as response:
                content = response.raw.read(web.MAX_BYTES + 1, decode_content=True)
        except Missing as e:
            lines += ["", f"Attachment {f['id']}: EDIS has no file for it ({e})."]
            continue
        name = f"Attachment {f['id']}: {f.get('title') or f.get('originalFileName') or ''}"
        if not content.startswith(b"%PDF-") or len(content) > web.MAX_BYTES:
            lines += ["", f"{name}: not a PDF within the size a read takes."]
            continue
        try:
            lines += ["", name, web._pdf(content)[0]]
        except Exception as e:  # the file arrived and could not be parsed
            lines += ["", f"{name}: its text could not be extracted ({type(e).__name__})."]
    return {**_filing(docs[0]), "document": "\n".join(lines), "published": _filing(docs[0])["received_date"]}


def _newest_filing(series):
    rows = _edis_rows("document", "documents", {"securityLevel": "Public", "pageNumber": 1})
    return series.since, _filing(rows[0])["received_date"] if rows else None


# ---------- OFAC general licenses ----------

def _search_licenses(s):
    at = s.cursor or 0
    response = _get(FILE_FINDER, params={"search_api_fulltext": s.query(), "field_tags": "14811", "page": at})
    view = _LicenseFiles()
    view.feed(response.text)
    if not view.rows and not view.empty:
        raise SourceError("OFAC File Finder did not return the general license results page")
    rows = [{"id": r["url"].split("/system/files/", 1)[1], "url": r["url"], "title": r["title"], "file_name": r["filename"]}
            for r in view.rows]
    return page(rows, at + 1 if view.has_next else None)


def _read_license(path):
    url = f"https://ofac.treasury.gov/system/files/{path}"
    got = web.read(url)
    name = path.rsplit("/", 1)[-1]
    view = _LicenseFiles()  # the title is the File Finder's, as a search returns it
    view.feed(_get(FILE_FINDER, params={"search_api_fulltext": name.rsplit(".", 1)[0], "field_tags": "14811", "page": 0}).text)
    title = next((r["title"] for r in view.rows if r["url"] == url), None) or name
    return {"id": path, "url": url, "title": title, "file_name": name, "document": got["text"],
            "links": got["data"].get("links") or []}


USITC = "U.S. International Trade Commission"
SERIES = [
    Series(
        id="usitc_investigations", title="USITC investigations",
        record="a U.S. International Trade Commission investigation in one phase: an import-injury (antidumping or "
               "countervailing duty) case, a Section 337 unfair-import case, or a fact-finding study",
        creator=USITC, jurisdiction="federal", coverage=_investigations_span,
        fields=(F("investigation_number", "identifier"), F("title", "text"), F("investigation_type", "text"), F("phase", "text"),
                F("status", STATUS), F("country", "place", many=True), F("complainant", "name", many=True),
                F("respondent", "name", many=True), F("other_party", "name", many=True), F("start_date", "date"),
                F("institution_notice", "date"), F("target_date", "date"), F("vote_date", "date"),
                F("commerce_final_determination", "date")),
        search=_search_investigations, read=_read_investigation),
    Series(
        id="usitc_filings", title="USITC filings",
        record="a public document filed in a USITC investigation, in the Commission's docket system (EDIS)",
        creator="parties to USITC investigations and the Commission", publisher=USITC, jurisdiction="federal",
        since="1996", coverage=_newest_filing,
        fields=(F("document_id", "identifier"), F("document_type", "identifier"), F("title", "text"),
                F("investigation_number", "identifier"), F("investigation_title", "text"), F("investigation_type", "text"),
                F("investigation_phase", "text"), F("firm", "name"), F("filed_by", "name"), F("on_behalf_of", "name"),
                F("document_date", "date"), F("received_date", "date")),
        search=_search_filings, read=_read_filing),
    Series(
        id="ofac_general_licenses", title="OFAC general licenses",
        record="a general license issued by the Office of Foreign Assets Control under a sanctions program, in each "
               "version posted, with related notices",
        creator="U.S. Department of the Treasury, Office of Foreign Assets Control", jurisdiction="federal",
        fields=(F("title", "text"), F("file_name", "identifier"), F("full_text", "full text")),
        search=_search_licenses, read=_read_license),
]
