"""Five series of the federal rulemaking record: Regulations.gov's dockets, documents and comments, and OIRA's
reviews of regulatory actions under Executive Order 12866 and its meetings with outside parties about them.

  regulatory_dockets, regulatory_documents, public_comments
        Regulations.gov's API, with an api.data.gov key. One search returns at most 5,000 records, so a search
        that runs past them goes on from the last record's modification time, as the API's documentation says to.
        The API's times are UTC and its dates are Eastern: a comment period that ends 2027-01-12T04:59:59Z ends
        on January 11.
  eo12866_reviews
        OIRA publishes its reviews as files: the actions now under review, and each year's concluded reviews.
        They are kept in out/sources/eo12866_reviews/.
  eo12866_meetings
        OIRA's meeting search lists meetings by date. Who asked for a meeting, and for which client, is on the
        meeting's own page, which is read for each meeting listed and kept in out/sources/eo12866_meetings/.
"""

import datetime as dt
import functools
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, urljoin, urlsplit
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup, Comment

from ..util import OUT, SourceError, http_json, session, today
from . import F, Series, newest, outline, page, web

REGS = "https://api.regulations.gov/v4"
REGS_PAGE = 250
EASTERN = ZoneInfo("America/New_York")
REGINFO = "https://www.reginfo.gov/public/do/"
UNDER_REVIEW = "EO_RULES_UNDER_REVIEW.xml"
REVIEWS_KEPT = OUT / "sources" / "eo12866_reviews"
MEETINGS_KEPT = OUT / "sources" / "eo12866_meetings"
MEETINGS_FIRST = dt.date(2011, 1, 1)
YES_NO = ("yes", "no")
MEETING_TYPES = ("Scheduled", "Completed", "No Show", "Cancelled")


def _clean(value):
    return " ".join((value or "").split())


def _yes_no(value):
    return {True: "yes", False: "no", "yes": "yes", "no": "no"}.get(value.lower() if isinstance(value, str) else value)


# ---------- Regulations.gov ----------

def _regs(path, params=None):
    try:
        return http_json(f"{REGS}/{path}", params=params, headers={"X-Api-Key": os.environ["DATA_GOV_API_KEY"]}, tries=3, timeout=60)
    except requests.RequestException as e:
        status = e.response.status_code if getattr(e, "response", None) is not None else None
        if status == 404:
            raise SourceError(f"Regulations.gov has no {path}")
        raise SourceError("Regulations.gov's hourly request limit is reached; search again later." if status == 429
                          else f"Regulations.gov did not answer ({status or type(e).__name__})")


def _eastern(stamp):
    return dt.datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(EASTERN)


def _day(stamp):
    return _eastern(stamp).date().isoformat() if stamp else None


def _regs_search(s, kind, filters, record):
    """A page of one kind of record, newest modification first. The cursor is a position among the records
    modified no later than a time: {o, le}."""
    at = s.cursor or {"o": 0, "le": None}
    params = {f"filter[{k}]": v for k, v in filters.items() if v not in (None, "")}
    params |= {"sort": "-lastModifiedDate", "page[size]": REGS_PAGE, "page[number]": at["o"] // REGS_PAGE + 1}
    if at["le"]:
        params["filter[lastModifiedDate][le]"] = at["le"]
    got = _regs(kind, params)
    rows, meta = got["data"], got["meta"]
    start = at["o"] % REGS_PAGE
    taken = rows[start:start + s.size]
    nxt = None
    if start + len(taken) < len(rows) or meta.get("hasNextPage"):
        nxt = {"o": at["o"] + len(taken), "le": at["le"]}
    elif rows and meta.get("totalElements", 0) > at["o"] + len(taken):
        # the last page a search returns, with more matching: go on from the last record's time, past the
        # records already returned that share it
        last = rows[-1]["attributes"]["lastModifiedDate"]
        nxt = {"o": sum(r["attributes"]["lastModifiedDate"] == last for r in rows),
               "le": _eastern(last).strftime("%Y-%m-%d %H:%M:%S")}
    return page([record(r) for r in taken], nxt, meta.get("totalElements") if s.cursor is None else None,
                "latest modified first")


def _range(s, field, key, filters):
    lo, hi = s.between(field)
    filters |= {f"{key}][ge": lo, f"{key}][le": hi}


def _docket(d):
    a = d["attributes"]
    return {"id": d["id"], "url": f"https://www.regulations.gov/docket/{d['id']}", "docket_id": d["id"], "agency": a.get("agencyId"),
            "docket_type": a.get("docketType"), "title": a.get("title"), "last_modified_date": _day(a.get("lastModifiedDate") or a.get("modifyDate"))}


def _search_dockets(s):
    if s.peek("docket_id"):
        return page([_docket(_regs(f"dockets/{i}")["data"]) for i in s.any("docket_id")])
    filters = {"agencyId": s.one("agency"), "docketType": s.one("docket_type"), "searchTerm": s.query() if s.words() else None}
    return _regs_search(s, "dockets", filters, _docket)


def _read_docket(key):
    d = _regs(f"dockets/{key}")["data"]
    return {**_docket(d), "document": "\n".join(outline(d["attributes"], skip=("highlightedContent", "displayProperties"))),
            "links": [{"text": "The docket's documents on Regulations.gov", "url": f"https://www.regulations.gov/docket/{key}/document"}]}


def _document(d):
    a = d["attributes"]
    return {"id": d["id"], "url": f"https://www.regulations.gov/document/{d['id']}", "document_id": d["id"], "docket_id": a.get("docketId"),
            "agency": a.get("agencyId"), "document_type": a.get("documentType"), "subtype": a.get("subtype"), "title": a.get("title"),
            "posted_date": _day(a.get("postedDate")), "comment_start_date": _day(a.get("commentStartDate")),
            "comment_end_date": _day(a.get("commentEndDate")), "fr_document_number": a.get("frDocNum"),
            "withdrawn": _yes_no(a.get("withdrawn")), "last_modified_date": _day(a.get("lastModifiedDate") or a.get("modifyDate"))}


def _search_documents(s):
    filters = {"agencyId": s.one("agency"), "docketId": s.one("docket_id"), "documentType": s.one("document_type"),
               "frDocNum": s.one("fr_document_number"), "searchTerm": s.query() if s.words() else None}
    withdrawn = s.one("withdrawn")
    filters |= {"withdrawn": {"yes": "true", "no": "false"}[withdrawn]} if withdrawn else {}
    _range(s, "posted_date", "postedDate", filters)
    _range(s, "comment_end_date", "commentEndDate", filters)
    return _regs_search(s, "documents", filters, _document)


def _files(d, included=()):
    """The files of a document or comment and of its attachments, as links."""
    links = [{"text": f"{d['attributes'].get('title') or d['id']} ({f.get('format')})", "url": f["fileUrl"]}
             for f in d["attributes"].get("fileFormats") or [] if f.get("fileUrl")]
    for item in included:
        links += [{"text": f"Attachment: {item['attributes'].get('title') or item['id']} ({f.get('format')})", "url": f["fileUrl"]}
                  for f in item["attributes"].get("fileFormats") or [] if f.get("fileUrl")]
    return links


# left out of a record's document: what its fields already state (the API's times are UTC, the fields' dates Eastern)
SKIP = ("highlightedContent", "displayProperties", "fileFormats", "objectId", "commentOn", "commentEndDate", "commentStartDate",
        "postedDate", "lastModifiedDate", "modifyDate", "docketId", "agencyId", "title", "documentType", "frDocNum", "withdrawn",
        "commentOnDocumentId")


def _read_document(key):
    got = _regs(f"documents/{key}", {"include": "attachments"})
    d, links = got["data"], _files(got["data"], got.get("included") or ())
    lines = outline(d["attributes"], skip=SKIP)
    own = [f["fileUrl"] for f in d["attributes"].get("fileFormats") or [] if f.get("fileUrl")]
    text = next((u for u in own if u.endswith((".htm", ".html"))), None) or next((u for u in own if u.endswith(".pdf")), None)
    if text:
        try:
            lines += ["", web.read(text)["text"]]
        except SourceError as e:
            lines += ["", f"The document's file could not be read here ({e}); its address is in the links."]
    return {**_document(d), "document": "\n".join(lines), "links": links, "published": _day(d["attributes"].get("postedDate"))}


def _comment(d, on=None):
    a = d["attributes"]
    return {"id": d["id"], "url": f"https://www.regulations.gov/comment/{d['id']}", "comment_id": d["id"],
            "docket_id": a.get("docketId") or d["id"].rsplit("-", 1)[0], "comment_on_document_id": a.get("commentOnDocumentId") or on,
            "agency": a.get("agencyId"), "title": a.get("title"), "posted_date": _day(a.get("postedDate")),
            "withdrawn": _yes_no(a.get("withdrawn")), "last_modified_date": _day(a.get("lastModifiedDate") or a.get("modifyDate"))}


@functools.lru_cache(maxsize=256)
def _object_id(document_id):
    return _regs(f"documents/{document_id}")["data"]["attributes"]["objectId"]


def _search_comments(s):
    on = s.one("comment_on_document_id")
    filters = {"agencyId": s.one("agency"), "docketId": s.one("docket_id"), "commentOnId": _object_id(on) if on else None,
               "searchTerm": s.query() if s.words() else None}
    _range(s, "posted_date", "postedDate", filters)
    return _regs_search(s, "comments", filters, lambda d: _comment(d, on))


def _read_comment(key):
    got = _regs(f"comments/{key}", {"include": "attachments"})
    d = got["data"]
    return {**_comment(d), "document": "\n".join(outline(d["attributes"], skip=SKIP)),
            "links": _files(d, got.get("included") or ()), "published": _day(d["attributes"].get("postedDate"))}


# ---------- OIRA's reviews ----------

_files_kept = {}


def _reginfo(path, params=None, keep=21600):
    """A page or file of reginfo.gov, kept in memory for `keep` seconds."""
    key = (path, json.dumps(params, sort_keys=True))
    hit = _files_kept.get(key)
    if hit and time.time() - hit[0] < keep:
        return hit[1]
    try:
        r = session().get(REGINFO + path, params=params, timeout=90)
        r.raise_for_status()
    except requests.RequestException as e:
        raise SourceError(f"reginfo.gov did not answer ({type(e).__name__})")
    if keep:
        _files_kept[key] = (time.time(), r.content)
    return r.content


def _review_files():
    """OIRA's review files, in the order a search reads them: under review, this year's concluded, each earlier year's."""
    listed = re.findall(r"XMLViewFileAction\?f=(EO_RULE_COMPLETED_(\d{4})\.xml)", _reginfo("XMLReportList").decode("utf-8", "replace"))
    years = sorted({(name, int(year)) for name, year in listed}, key=lambda x: -x[1])
    return [(UNDER_REVIEW, None), ("EO_RULE_COMPLETED_YTD.xml", today().year)] + years


@functools.lru_cache(maxsize=1)
def _agencies(day):
    root = ElementTree.fromstring(_reginfo("XMLViewFileAction", {"f": "AGY_AGENCY_LIST.xml"}))
    return {a.findtext("AGENCY_CODE"): _clean(a.findtext("NAME")) for a in root.iter("AGENCY")}


def _review_file(name, year):
    """One of OIRA's files, kept in out/sources/eo12866_reviews/: an earlier year's for a month, the current ones for six hours."""
    kept = REVIEWS_KEPT / name
    fresh = 30 * 86400 if year and year < today().year else 21600
    if not kept.is_file() or time.time() - kept.stat().st_mtime > fresh:
        content = _reginfo("XMLViewFileAction", {"f": name}, keep=0)
        ElementTree.fromstring(content)  # a file that does not parse is not kept
        REVIEWS_KEPT.mkdir(parents=True, exist_ok=True)
        kept.write_bytes(content)
    return kept


@functools.lru_cache(maxsize=128)
def _reviews(name, stamp):
    """The reviews in one of OIRA's files, latest received first."""
    root = ElementTree.parse(REVIEWS_KEPT / name).getroot()
    agencies, out = _agencies(today().isoformat()), []
    for act in root.iter("REGACT"):
        raw = {e.tag: _clean(e.text) for e in act if _clean(e.text)}
        rin, received = raw.get("RIN"), raw.get("DATE_RECEIVED")
        out.append({"id": f"{rin}/{received}", "url": "https://www.reginfo.gov/public/Forward?SearchTarget=RegReview&textfield=" + (rin or ""),
                    "rin": rin, "agency": agencies.get(raw.get("AGENCY_CODE")) or raw.get("AGENCY_CODE"), "title": raw.get("TITLE"),
                    "stage": raw.get("STAGE"), "status": "under review" if name == UNDER_REVIEW else "concluded",
                    "economically_significant": _yes_no(raw.get("ECONOMICALLY_SIGNIFICANT") or ""),
                    "date_received": received, "legal_deadline": raw.get("LEGAL_DEADLINE"),
                    "date_completed": raw.get("DATE_COMPLETED"), "decision": raw.get("DECISION"),
                    "date_published": raw.get("DATE_PUBLISHED"), "_raw": raw})
    return sorted(out, key=lambda r: r["date_received"] or "", reverse=True)


def _file_reviews(name, year=None):
    return _reviews(name, _review_file(name, year).stat().st_mtime)


def _search_reviews(s):
    status, (lo, hi) = s.peek("status").get("is"), (s.peek("date_completed").get("lo"), s.peek("date_completed").get("hi"))
    files = []
    for name, year in _review_files():
        if status and ("under review" if year is None else "concluded") not in status:
            continue
        if year is None and (lo or hi):
            continue  # a review still under way has no date completed
        if year and ((lo and year < int(lo[:4])) or (hi and year > int(hi[:4]))):
            continue
        files.append((name, year))
    at = s.cursor or {"f": 0, "o": 0}
    if at["f"] >= len(files):
        return page([])
    rows = _file_reviews(*files[at["f"]])
    part = rows[at["o"]:at["o"] + s.size]
    nxt = {"f": at["f"], "o": at["o"] + len(part)} if at["o"] + len(part) < len(rows) else {"f": at["f"] + 1, "o": 0}
    return page([{k: v for k, v in r.items() if k != "_raw"} for r in part], nxt if nxt["f"] < len(files) else None,
                order="under review first, then concluded by year; latest received first")


def _read_review(key):
    rin, _, received = key.partition("/")
    for name, year in _review_files():
        if year and received and year < int(received[:4]):
            break
        hit = next((r for r in _file_reviews(name, year) if r["id"] == key), None)
        if hit:
            labels = {k.replace("_", " ").capitalize(): v for k, v in hit["_raw"].items()}
            return {**{k: v for k, v in hit.items() if k != "_raw"}, "document": "\n".join(outline(labels)),
                    "links": [{"text": f"OIRA's reviews of RIN {rin}", "url": hit["url"]}]}
    raise SourceError(f"OIRA's files have no review of {rin} received {received}")


def _reviews_span(series):
    earliest = min(year for _, year in _review_files() if year)
    dates = [r["date_received"] for r in _file_reviews(UNDER_REVIEW) if r["date_received"]]
    return f"{earliest}", max(dates) if dates else None


# ---------- OIRA's meetings ----------

def _labelled(cell):
    """{label: value} from a reginfo.gov table: each bold label and the text that follows it."""
    result = {}
    for label in cell.find_all(["b", "label"]):
        name, values = _clean(label.get_text(" ", strip=True)).rstrip(":"), []
        for node in label.next_siblings:
            if isinstance(node, Comment):
                continue
            if getattr(node, "name", "") in ("b", "label"):
                break
            values.append(node.get_text(" ", strip=True) if getattr(node, "name", None) else str(node))
        if name:
            result[name] = _clean(" ".join(values))
    return result


def _mdy(day):
    return day.strftime("%m/%d/%Y")


def _listed_meetings(start, end, rin=None):
    """The meetings OIRA lists between two dates, latest first."""
    params = {"viewall": "y", "viewAllFlag": "Y", "searchStartDate": _mdy(start), "searchEndDate": _mdy(end)} | ({"rin": rin} if rin else {})
    soup = BeautifulSoup(_reginfo("eom12866SearchResults", params, keep=900), "html.parser")
    rows = []
    for tr in soup.select("table.datatable tr"):
        cells, link = tr.find_all("td", recursive=False), tr.select_one('a[href*="viewEO12866Meeting?"]')
        if not link or len(cells) != 6:
            continue
        when, rin_, agency, title, stage, kind = (_clean(c.get_text(" ", strip=True)) for c in cells)
        held = dt.datetime.strptime(when, "%m/%d/%Y %I:%M %p")
        rows.append({"id": parse_qs(urlsplit(link["href"]).query)["meetingId"][0], "meeting_date": held.date().isoformat(),
                     "_time": held.strftime("%H:%M"), "rin": rin_, "agency": agency.partition("-")[2] or agency, "title": title,
                     "stage": stage, "meeting_type": kind})
    count = soup.select_one('input[name="resultCount"]')
    if not rows and soup.find(string=lambda v: v and "No results found" in v):
        return []
    if count and int(count["value"]) != len(rows):
        raise SourceError(f"OIRA listed {len(rows)} of {count['value']} meetings between {start} and {end}")
    return sorted(rows, key=lambda r: (r["meeting_date"], r["_time"], r["id"]), reverse=True)


def _meeting(row):
    """A listed meeting with what its own page adds: who asked for it, for which client, who attended, what was handed over."""
    kept = MEETINGS_KEPT / f"{row['id']}.json"
    settled = row["meeting_date"] < (today() - dt.timedelta(days=30)).isoformat()
    if kept.is_file() and (settled or time.time() - kept.stat().st_mtime < 43200):
        return {**json.loads(kept.read_text()), **{k: v for k, v in row.items() if not k.startswith("_")}}
    url = REGINFO + "viewEO12866Meeting?meetingId=" + row["id"]
    soup = BeautifulSoup(_reginfo("viewEO12866Meeting", {"meetingId": row["id"]}, keep=0), "html.parser")
    table = soup.select_one('table[summary*="summary of EO 12866"]')
    if not table:
        raise SourceError(f"OIRA did not return meeting {row['id']}")
    stated = _labelled(table)
    attendees = []
    for tr in soup.select('table[summary="List of EO 12866 Meetings Attendees"] tr'):
        cells = tr.find_all("td", recursive=False)
        if len(cells) >= 2 and _clean(cells[-2].get_text(" ", strip=True)):
            attendees.append({"attendee": _clean(cells[-2].get_text(" ", strip=True)), "participation": _clean(cells[-1].get_text(" ", strip=True))})
    links = []
    for button in soup.select('table[summary="List of EO 12866 Meetings Documents"] button[onclick]'):
        target = re.search(r'(/public/do/eoDownloadDocument\?[^"\']+)', button["onclick"])
        if target:
            links.append({"text": _clean(button.get_text(" ", strip=True)), "url": urljoin(url, target[1])})
    found = {"url": url, "requestor": stated.get("Requestor") or None, "client": stated.get("Requestor's Client") or None,
             "_stated": stated, "_attendees": attendees, "links": links}
    MEETINGS_KEPT.mkdir(parents=True, exist_ok=True)
    kept.write_text(json.dumps(found))
    return {**found, **{k: v for k, v in row.items() if not k.startswith("_")}}


def _shown_meeting(m):
    return {k: v for k, v in m.items() if not k.startswith("_") and k != "links"} | {"meeting_id": m["id"]}


SPAN = dt.timedelta(days=91)  # OIRA's search returns nothing for a span holding about a thousand meetings


def _search_meetings(s):
    if s.peek("meeting_id"):
        return page([{k: v for k, v in _read_meeting(i).items() if k not in ("document", "links")} for i in s.any("meeting_id")])
    lo, hi = s.between("meeting_date")
    first = dt.date.fromisoformat(lo) if lo else MEETINGS_FIRST
    last = dt.date.fromisoformat(hi) if hi else today() + dt.timedelta(days=365)
    rin = s.one("rin")
    at = s.cursor or {"e": last.isoformat(), "o": 0}
    end = dt.date.fromisoformat(at["e"])
    if end < first:
        return page([])
    start = first if rin else max(first, end - SPAN)
    rows = _listed_meetings(start, end, rin)
    part = rows[at["o"]:at["o"] + s.size]
    with ThreadPoolExecutor(8) as ex:
        records = [_shown_meeting(m) for m in ex.map(_meeting, part)]
    if at["o"] + len(part) < len(rows):
        nxt = {"e": at["e"], "o": at["o"] + len(part)}
    else:
        before = start - dt.timedelta(days=1)
        nxt = {"e": before.isoformat(), "o": 0} if before >= first else None
    return page(records, nxt, order="latest meeting first")


def _read_meeting(key):
    if not key.isdigit():
        raise SourceError("An eo12866_meetings record_id is the one a search returned")
    soup = BeautifulSoup(_reginfo("viewEO12866Meeting", {"meetingId": key}, keep=0), "html.parser")
    table = soup.select_one('table[summary*="summary of EO 12866"]')
    stated = _labelled(table) if table else {}
    if not stated.get("Meeting Date/Time"):
        raise SourceError(f"OIRA has no meeting {key}")
    held = dt.datetime.strptime(stated["Meeting Date/Time"], "%m/%d/%Y %I:%M %p").date()
    row = next((r for r in _listed_meetings(held, held, stated.get("RIN")) if r["id"] == key), None)
    if not row:
        raise SourceError(f"OIRA's meeting search does not list meeting {key}")
    kept = MEETINGS_KEPT / f"{key}.json"
    kept.unlink(missing_ok=True)
    m = _meeting(row)
    lines = outline(m["_stated"]) + (["", "Attendees:"] + outline(m["_attendees"]) if m["_attendees"] else [])
    return {**_shown_meeting(m), "document": "\n".join(lines), "links": m["links"]}


DATA_GOV = ("DATA_GOV_API_KEY",)
AGENCIES = "federal agencies"
ERULEMAKING = "General Services Administration (Regulations.gov)"
OIRA = "Office of Information and Regulatory Affairs, Office of Management and Budget"
SERIES = [
    Series(
        id="regulatory_dockets", title="Regulations.gov dockets",
        record="a docket on Regulations.gov: an agency's file for one rulemaking or other action, holding its documents and the comments on them",
        creator=AGENCIES, publisher=ERULEMAKING, jurisdiction="federal", requires=DATA_GOV,
        fields=(F("docket_id", "identifier"), F("agency", "identifier"), F("docket_type", ("Rulemaking", "Nonrulemaking")),
                F("title", "text"), F("last_modified_date", "date"), F("full_text", "full text")),
        search=_search_dockets, read=_read_docket),
    Series(
        id="regulatory_documents", title="Regulations.gov documents",
        record="a document an agency posted to a docket on Regulations.gov: a proposed rule, a rule, a notice, or material supporting one",
        creator=AGENCIES, publisher=ERULEMAKING, jurisdiction="federal", requires=DATA_GOV, coverage=newest("posted_date"),
        fields=(F("document_id", "identifier"), F("docket_id", "identifier"), F("agency", "identifier"),
                F("document_type", ("Proposed Rule", "Rule", "Notice", "Supporting & Related Material", "Other")),
                F("subtype", "text"), F("title", "text"), F("posted_date", "date"), F("comment_start_date", "date"),
                F("comment_end_date", "date"), F("fr_document_number", "identifier"), F("withdrawn", YES_NO),
                F("last_modified_date", "date"), F("full_text", "full text")),
        search=_search_documents, read=_read_document),
    Series(
        id="public_comments", title="comments on Regulations.gov",
        record="a comment submitted to an agency on a document it posted for comment on Regulations.gov, with the submitter and "
               "organization the agency shows",
        creator="commenters: companies, associations, governments and individuals", publisher=ERULEMAKING, jurisdiction="federal",
        requires=DATA_GOV, coverage=newest("posted_date"),
        fields=(F("comment_id", "identifier"), F("docket_id", "identifier"), F("comment_on_document_id", "identifier"),
                F("agency", "identifier"), F("title", "text"), F("posted_date", "date"), F("withdrawn", YES_NO),
                F("last_modified_date", "date"), F("full_text", "full text")),
        search=_search_comments, read=_read_comment),
    Series(
        id="eo12866_reviews", title="OIRA's regulatory reviews",
        record="a review of a regulatory action by OIRA under Executive Order 12866, before the agency may publish it: under "
               "review now, or concluded with OIRA's decision",
        creator=OIRA, jurisdiction="federal", coverage=_reviews_span,
        fields=(F("rin", "identifier"), F("agency", "name"), F("title", "text"), F("stage", "text"),
                F("status", ("under review", "concluded")), F("economically_significant", YES_NO), F("date_received", "date"),
                F("legal_deadline", "text"), F("date_completed", "date"), F("decision", "text"), F("date_published", "date")),
        search=_search_reviews, read=_read_review),
    Series(
        id="eo12866_meetings", title="OIRA's meetings on rules under review",
        record="a meeting OIRA held or has scheduled with outside parties about a regulatory action under Executive Order 12866 "
               "review, with who asked for it and the client they act for",
        creator=OIRA, jurisdiction="federal", since=str(MEETINGS_FIRST.year), coverage=newest("meeting_date"),
        fields=(F("meeting_id", "identifier"), F("meeting_date", "date"), F("rin", "identifier"), F("agency", "text"),
                F("title", "text"),
                F("stage", ("Prerule Stage", "Proposed Rule Stage", "Interim Final Rule Stage", "Final Rule Stage", "Notice Stage")),
                F("meeting_type", MEETING_TYPES), F("requestor", "name"), F("client", "name")),
        search=_search_meetings, read=_read_meeting),
]
