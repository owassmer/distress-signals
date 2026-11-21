"""Rulemaking dockets, OIRA regulatory reviews and meeting records, and congressional records."""

import datetime as dt
import json
import os
import re
import xml.etree.ElementTree as ET
from urllib.parse import parse_qs, urljoin, urlsplit

import requests
from bs4 import BeautifulSoup, Comment

from ..util import SourceError, http_json, session, today
from . import web

REG = "https://api.regulations.gov/v4"
OIRA = "https://www.reginfo.gov/public/do/"
LOC = "https://www.congress.gov"
GPO = "https://www.govinfo.gov"
FILTERS = {
    "regulations_gov": {"record_type", "agency", "docket_id", "comment_on", "comment_end_from", "comment_end_until",
                        "since", "until", "limit", "offset"},
    "oira": {"record_type", "rin", "stage", "since", "until", "limit", "offset"},
    "congress": {"record_type", "week_of", "cursor", "limit", "offset"},
}
REG_KINDS = {"documents": "document", "comments": "comment", "dockets": "docket"}
GPO_KINDS = {"reports": "CRPT", "transcripts": "CHRG", "bills": "BILLS"}
BILL_NAMES = {"hr": "house-bill", "s": "senate-bill", "hres": "house-resolution", "sres": "senate-resolution",
              "hjres": "house-joint-resolution", "sjres": "senate-joint-resolution",
              "hconres": "house-concurrent-resolution", "sconres": "senate-concurrent-resolution"}
BILL_TYPE = "(?:" + "|".join(BILL_NAMES) + ")"


def _json(url, *, key_env, params=None, body=None):
    key = os.getenv(key_env) or os.getenv("DATA_GOV_API_KEY") or "DEMO_KEY"
    try:
        return http_json(url, method="GET" if body is None else "POST", params=params, json_body=body,
                         headers={"X-Api-Key": key}, tries=2, timeout=45)
    except requests.RequestException as exc:
        status = exc.response.status_code if exc.response is not None else None
        if status in (403, 429):
            raise SourceError(f"{url} returned HTTP {status}. Set {key_env} for your own API quota.") from None
        raise SourceError(f"{url} failed" + (f" with HTTP {status}" if status else "")) from None


def _get(url, params=None):
    r = session().get(url, params=params, timeout=45)
    r.raise_for_status()
    return r.content


def _clean(value):
    return " ".join(value.split())


def _text(tag):
    return _clean(tag.get_text(" ", strip=True)) if tag else ""


def _iso(value):
    if value is None:
        return None
    try:
        return dt.date.fromisoformat(value).isoformat()
    except (ValueError, TypeError):
        raise SourceError("Use dates in YYYY-MM-DD format") from None


def _page(rows, offset, limit):
    end = offset + limit
    return {"results": rows[offset:end], "total_matches": len(rows), "complete": end >= len(rows),
            "next_offset": end if end < len(rows) else None}


def _obs(source, native_id, kind, url, data, published=None, text=None):
    return {"source": source, "kind": kind, "native_id": native_id, "url": url, "data": data,
            "published": published, "text": text or json.dumps(data, indent=2, ensure_ascii=False)}


def _reg_id(identifier):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", identifier):
        raise SourceError("Invalid Regulations.gov ID")
    return identifier


def _reg_record(kind, identifier):
    item = _json(f"{REG}/{kind}/{_reg_id(identifier)}", key_env="REGULATIONS_GOV_API_KEY",
                 params={} if kind == "dockets" else {"include": "attachments"})
    if item["data"]["id"] != identifier:
        raise SourceError("Regulations.gov returned a different record")
    return item


def _reg_row(kind, item):
    attrs = item["attributes"]
    url = f"https://www.regulations.gov/{REG_KINDS[kind]}/{item['id']}"
    fields = ("agencyId", "docketId", "commentOnDocumentId", "postedDate", "documentType", "docketType",
              "commentStartDate", "commentEndDate", "openForComment", "effectiveDate", "organization", "withdrawn")
    return {"record_id": f"regulations_gov:{kind}:{item['id']}", "url": url, "id": item["id"],
            "title": attrs.get("title") or item["id"], **{k: attrs[k] for k in fields if attrs.get(k) is not None}}


def _reg_search(query, filters, limit, offset):
    kind = filters.get("record_type", "documents")
    if kind not in REG_KINDS:
        raise SourceError("Choose documents, comments or dockets")
    if filters.get("docket_id") and kind != "documents":
        raise SourceError("docket_id selects documents; use comment_on for the comments on a document, or search dockets by ID")
    params = {}
    for name, field in (("agency", "agencyId"), ("docket_id", "docketId")):
        if filters.get(name):
            params[f"filter[{field}]"] = filters[name]
    if query:
        params["filter[searchTerm]"] = query
    for name, field in (("since", "postedDate][ge"), ("until", "postedDate][le"),
                        ("comment_end_from", "commentEndDate][ge"), ("comment_end_until", "commentEndDate][le")):
        if filters.get(name):
            if kind == "dockets" or (kind != "documents" and name.startswith("comment_end")):
                raise SourceError(f"{name} is unavailable for {kind}")
            params[f"filter[{field}]"] = _iso(filters[name])
    if filters.get("comment_on"):
        if kind != "comments":
            raise SourceError("comment_on is for comment searches")
        parent = _reg_record("documents", filters["comment_on"])["data"]
        params["filter[commentOnId]"] = parent["attributes"]["objectId"]
    # Use the largest supported page size to reach the largest result window the API exposes.
    size = 250
    page, start = divmod(offset, size)
    params.update({"page[size]": size, "page[number]": page + 1})
    response = _json(f"{REG}/{kind}", key_env="REGULATIONS_GOV_API_KEY", params=params)
    meta = response["meta"]
    returned = response["data"][start:start + limit]
    next_offset = offset + len(returned)
    total = meta["totalElements"]
    more = bool(returned) and next_offset < total and (
        start + len(returned) < len(response["data"]) or (meta["hasNextPage"] and page + 1 < meta["totalPages"]))
    return {"results": [_reg_row(kind, d) for d in returned], "total_matches": total,
            "complete": next_offset >= total, "next_offset": next_offset if more else None,
            **({"note": "Narrow the query to access further matches."} if not more and next_offset < total else {})}


def _reg_read(native):
    kind, _, identifier = native.partition(":")
    if kind not in REG_KINDS:
        raise SourceError("Invalid Regulations.gov record type")
    item = _reg_record(kind, identifier)
    a = item["data"]["attributes"]
    data = _reg_row(kind, item["data"])
    fields = ("comment", "docAbstract", "summary", "reasonWithdrawn", "objectId", "frDocNum", "rin", "additionalRins",
              "effectiveDate", "implementationDate", "agencyId", "authors", "organization", "submitterRep",
              "commentOn", "fileFormats", "modifyDate", "lastModifiedDate", "subtype")
    data.update({k: a[k] for k in fields if a.get(k) is not None})
    if a.get("commentOnDocumentId"):
        data["parent_document_record_id"] = "regulations_gov:documents:" + a["commentOnDocumentId"]
    if a.get("docketId") and kind != "dockets":
        data["docket_record_id"] = "regulations_gov:dockets:" + a["docketId"]
    data["attachments"] = [{k: v for k, v in obj["attributes"].items() if v and k in
                            ("title", "fileFormats", "docAbstract", "postedDate")} for obj in item.get("included", [])]
    return _obs("regulations_gov", native, REG_KINDS[kind], data["url"], data, a.get("postedDate"))


def _fields(cell):
    """OIRA uses bold labels in review cells and consecutive labels within meeting paragraphs."""
    result = {}
    for label in cell.find_all(["b", "label"]):
        name = _text(label).rstrip(":")
        values = []
        for node in label.next_siblings:
            if isinstance(node, Comment):
                continue
            if getattr(node, "name", "") in ("b", "label"):
                break
            values.append(node.get_text(" ", strip=True) if getattr(node, "name", None) else str(node))
        if name:
            result[name] = _clean(" ".join(values))
    return result


def _reviews():
    soup = BeautifulSoup(_get(OIRA + "eoReviewSearch"), "html.parser")
    tables = soup.select('table[summary="This table contains EO Reviews."]')
    if not tables:
        raise SourceError("OIRA pending review index did not contain its review tables")
    rows = []
    for table in tables:
        for tt in table.select(".tooltip"):
            tt.decompose()
        entry = None
        for tr in table.select("tr"):
            fields = _fields(tr)
            if "RIN" in fields and "Status" in fields:
                link = tr.select_one('a[href*="eoDetails?rrid="]')
                if not link:
                    raise SourceError("OIRA review is missing its identifier")
                rid = parse_qs(urlsplit(link["href"]).query)["rrid"][0]
                entry = {"record_id": "oira:review:" + rid, "url": OIRA + "eoDetails?rrid=" + rid}
                rows.append(entry)
            if entry is not None:
                entry.update(fields)
                entry["title"] = entry.get("TITLE") or entry["RIN"]
    return rows


def _meeting_list(rin, since, until):
    start = since or (today() - dt.timedelta(days=30)).isoformat()
    end = until or (today() + dt.timedelta(days=60)).isoformat()
    if start > end:
        raise SourceError("Meeting start must be on or before end")
    params = {"viewall": "y", "viewAllFlag": "Y", "searchStartDate": dt.date.fromisoformat(start).strftime("%m/%d/%Y"),
              "searchEndDate": dt.date.fromisoformat(end).strftime("%m/%d/%Y")}
    if rin:
        params["rin"] = rin
    soup = BeautifulSoup(_get(OIRA + "eom12866SearchResults", params), "html.parser")
    rows = []
    for tr in soup.select("table.datatable tr"):
        cells = tr.find_all("td", recursive=False)
        link = tr.select_one('a[href*="viewEO12866Meeting?"]')
        if not link:
            continue
        if len(cells) != 6:
            raise SourceError("OIRA meeting index changed its columns")
        date, rin_value, agency, title, stage, status = map(_text, cells)
        mid = parse_qs(urlsplit(link["href"]).query)["meetingId"][0]
        rows.append({"record_id": f"oira:meeting:{mid}", "url": OIRA + "viewEO12866Meeting?meetingId=" + mid,
                     "meeting_date_time": date, "RIN": rin_value, "agency": agency,
                     "title": title, "stage": stage, "meeting_status": status})
    count_input = soup.select_one('input[name="resultCount"]')
    if not rows and soup.find(string=lambda v: v and v.strip() == "No results found for your search."):
        return []
    if not count_input:
        raise SourceError("OIRA meeting index did not report its result count")
    if count_input and len(rows) != int(count_input["value"]):
        raise SourceError("OIRA returned part of the meeting list. Narrow the dates or RIN.")
    return rows


def _oira_search(query, filters, limit, offset):
    kind = filters.get("record_type", "reviews")
    if kind == "reviews":
        if filters.get("since") or filters.get("until"):
            raise SourceError("since and until select meeting dates; current reviews include the date received")
        rows = _reviews()
    elif kind == "meetings":
        rows = _meeting_list(filters.get("rin"), _iso(filters.get("since")), _iso(filters.get("until")))
    else:
        raise SourceError("Choose reviews or meetings")
    def matches(row):
        return ((not filters.get("rin") or row.get("RIN", "").casefold() == filters["rin"].casefold())
                and (not filters.get("stage") or filters["stage"].casefold() in (row.get("stage") or row.get("STAGE", "")).casefold())
                and (not query or query.casefold() in " ".join(str(v) for v in row.values()).casefold()))
    return _page([r for r in rows if matches(r)], offset, limit)


def _oira_read(native):
    kind, _, identifier = native.partition(":")
    if not identifier.isdigit() or kind not in ("meeting", "review"):
        raise SourceError("Invalid OIRA record ID")
    url = OIRA + ("viewEO12866Meeting?meetingId=" if kind == "meeting" else "eoDetails?rrid=") + identifier
    soup = BeautifulSoup(_get(url), "html.parser")
    table = soup.select_one('table[summary*="summary of EO 12866Meeting"],table[summary*="OIRA conclusion of EO"]')
    if not table:
        raise SourceError("OIRA did not return the requested record")
    data = _fields(table)
    data["title"] = data.get("Title") or data.get("RIN")
    if not data["title"]:
        raise SourceError("OIRA did not return an identifiable review or meeting")
    if kind == "meeting":
        data["attendees"] = []
        for tr in soup.select('table[summary="List of EO 12866 Meetings Attendees"] tr'):
            cells = tr.find_all("td", recursive=False)
            if len(cells) >= 2 and _text(cells[-2]):
                data["attendees"].append({"listed_attendee": _text(cells[-2]), "participation": _text(cells[-1])})
        data["materials"] = []
        for button in soup.select('table[summary="List of EO 12866 Meetings Documents"] button[onclick]'):
            link = re.search(r'(/public/do/eoDownloadDocument\?[^"\']+)', button["onclick"])
            if link:
                data["materials"].append({"title": _text(button), "url": urljoin(url, link[1])})
    h = table.find_previous(["h1", "h2", "h3"])
    if h:
        data["record_heading"] = _text(h)
    return _obs("oira", native, "oira_" + kind, url, data)


def _weekly(monday):
    url = f"{LOC}/committee-schedule/weekly/{monday:%Y/%m/%d}"
    soup = BeautifulSoup(_get(url), "html.parser")
    sections = soup.select(".committee-schedule-section")
    if not sections:
        raise SourceError(f"Congress.gov did not return a weekly schedule for {monday}")
    rows = []
    for section in sections:
        day_link = section.select_one('.schedule-heading a[href*="/committee-schedule/daily/"]')
        if not day_link:
            raise SourceError("Congress.gov schedule did not identify the day")
        day = day_link["href"].rsplit("daily/", 1)[1].replace("/", "-")
        for listing in section.select("li.expanded"):
            lines, title = [], None
            for block in listing.find_all(recursive=False):
                if block.name == "hr":
                    lines, title = [], None
                    continue
                event = block.select_one('a[href*="/event/"]')
                if not event:
                    words = [_clean(s) for s in block.stripped_strings]
                    if not lines and words:
                        title = words[-1]
                    lines.extend(words)
                    continue
                path = urlsplit(event["href"]).path
                match = re.fullmatch(r"/event/(\d+)(?:st|nd|rd|th)-congress/([a-z]+)-event/([A-Za-z0-9]+)(?:/text)?", path)
                if not match:
                    raise SourceError(f"Unrecognized Congress.gov event link {path}")
                committee = lines[0] if lines else None
                rows.append({"record_id": "congress:meeting:" + ":".join(match.groups()), "url": LOC + path,
                             "title": title or _text(event), "committee": committee,
                             "meeting_date": day, "schedule_text": " ".join(lines), "schedule_url": url})
                lines, title = [], None
    return rows


def _congress_search(query, filters, limit, offset):
    kind = filters.get("record_type", "meetings")
    if kind == "meetings":
        if filters.get("cursor"):
            raise SourceError("Use offset for weekly committee meetings")
        week = dt.date.fromisoformat(_iso(filters["week_of"])) if filters.get("week_of") else today()
        monday = week - dt.timedelta(days=week.weekday())
        rows = _weekly(monday)
        if not filters.get("week_of"):
            rows.extend(_weekly(monday + dt.timedelta(days=7)))
        if query:
            rows = [r for r in rows if query.casefold() in " ".join(str(v) for v in r.values()).casefold()]
        return _page(rows, offset, limit)
    if kind not in GPO_KINDS:
        raise SourceError("Choose meetings, reports, transcripts or bills")
    if filters.get("week_of") or offset:
        raise SourceError("Use cursor for GovInfo records and week_of/offset for committee schedules")
    query_text = f"collection:{GPO_KINDS[kind]}" + (f" ({query})" if query else "")
    body = {"query": query_text, "pageSize": limit, "offsetMark": filters.get("cursor") or "*",
            "resultLevel": "package", "sorts": [{"field": "publishdate", "sortOrder": "DESC"}]}
    data = _json("https://api.govinfo.gov/search", body=body, key_env="GOVINFO_API_KEY")
    rows = []
    for item in data["results"]:
        package = item["packageId"]
        native = f"{kind}:{package}"
        if kind == "bills":
            m = re.fullmatch(rf"BILLS-(\d+)({BILL_TYPE})(\d+)[A-Za-z]+", package)
            if not m:
                raise SourceError(f"Could not resolve the GovInfo bill version {package}")
            native = "bill:" + ":".join(m.groups())
        rows.append({"record_id": "congress:" + native, "url": f"{GPO}/app/details/{package}",
                     "title": item["title"], "package_id": package, "date_issued": item.get("dateIssued"),
                     "government_author": item.get("governmentAuthor"), "collection": item["collectionCode"],
                     **({"version_text_url": f"{GPO}/content/pkg/{package}/pdf/{package}.pdf"} if kind == "bills" else {})})
    cursor = data.get("offsetMark")
    more = len(rows) == limit and bool(cursor) and cursor != body["offsetMark"] and (
        body["offsetMark"] != "*" or data["count"] > len(rows))
    return {"results": rows, "total_matches": data["count"], "complete": not more, "next_cursor": cursor if more else None}


def _congress_number(number):
    suffix = "th" if 10 < int(number) % 100 < 14 else {1: "st", 2: "nd", 3: "rd"}.get(int(number) % 10, "th")
    return number + suffix + "-congress"


def _meeting_read(native, congress, chamber, event):
    if chamber not in ("house", "senate", "joint", "nochamber") or not re.fullmatch(r"[A-Za-z0-9]+", event) or not congress.isdigit():
        raise SourceError("Invalid congressional event ID")
    url = f"{LOC}/event/{_congress_number(congress)}/{chamber}-event/{event}"
    soup = BeautifulSoup(_get(url), "html.parser")
    main = soup.select_one("main")
    if not main or not main.select_one("h1") or not main.select_one("table.standard01"):
        raise SourceError(f"Congress.gov did not return the requested meeting {event}")
    h1 = main.select_one("h1")
    title = _clean(" ".join(h1.find_all(string=True, recursive=False))) or _text(h1)
    metadata = {}
    for tr in main.select_one("table.standard01").select("tr"):
        cells = tr.find_all(["th", "td"], recursive=False)
        if len(cells) == 2:
            metadata[_text(cells[0]).rstrip(":")] = _text(cells[1])
    witnesses = []
    for li in main.select(".event-witness > li"):
        witnesses.append({"name": _text(li.find("strong")), "position_and_organization": _text(li.find("span")),
                          "materials": [{"title": _text(a.find_parent("li")), "url": urljoin(url, a["href"])}
                                        for a in li.select("a[href]")]})
    data = {"title": title, "event_id": event, "chamber": chamber, "congress": congress, "schedule": metadata,
            "witnesses": witnesses}
    # Keep the publisher's text and links, including dates added, meeting status and written testimony.
    content = main.select_one("article") or main
    data["materials"] = [{"title": _text(a), "url": urljoin(url, a["href"])} for a in content.select("a[href]")
                         if a["href"].lower().endswith((".pdf", ".htm", ".docx"))]
    return _obs("congress", native, "committee_event", url, data,
                text=json.dumps(data, ensure_ascii=False, indent=2) + "\n\n" + _text(content))


def _bill_read(native, congress, bill_type, number):
    if not re.fullmatch(BILL_TYPE, bill_type) or not congress.isdigit() or not number.isdigit():
        raise SourceError("Invalid congressional bill ID")
    url = f"{GPO}/bulkdata/BILLSTATUS/{congress}/{bill_type}/BILLSTATUS-{congress}{bill_type}{number}.xml"
    # GovInfo's bill-status XML begins with the 108th Congress. Congress.gov also publishes earlier actions.
    raw = None
    if int(congress) >= 108:
        try:
            raw = _get(url)
        except requests.HTTPError as exc:
            if exc.response is None or exc.response.status_code != 404:
                raise
    if raw is None:
        url = f"{LOC}/bill/{_congress_number(congress)}/{BILL_NAMES[bill_type]}/{number}/all-actions"
        soup = BeautifulSoup(_get(url), "html.parser")
        table = soup.select_one("table.expanded-actions")
        if not table:
            raise SourceError("Congress.gov did not return its bill action table")
        headings = [_text(th) for th in table.select("thead th")]
        rows = [dict(zip(headings, [_text(td) for td in tr.select("td")])) for tr in table.select("tr") if tr.find("td")]
        return _obs("congress", native, "bill_actions", url, {"title": _text(soup.select_one("h1")), "actions": rows})
    bill = ET.fromstring(raw).find("bill")
    if bill is None or bill.findtext("number") != number or (bill.findtext("type") or "").lower() != bill_type:
        raise SourceError("GovInfo did not return the requested bill status record")
    actions = []
    for item in bill.findall("./actions/item"):
        actions.append({**{k: item.findtext(k) for k in ("actionDate", "actionTime", "text", "type", "actionCode")
                           if item.findtext(k)}, "source_system": item.findtext("sourceSystem/name")})
    data = {"title": bill.findtext("title"), "congress": congress, "bill_type": bill_type, "number": number,
            "introduced_date": bill.findtext("introducedDate"), "actions": actions,
            "last_update": bill.findtext("updateDate")}
    return _obs("congress", native, "bill_actions", url, data)


def _congress_read(native):
    parts = native.split(":")
    if len(parts) == 4 and parts[0] == "meeting":
        return _meeting_read(native, *parts[1:])
    if len(parts) == 4 and parts[0] == "bill":
        return _bill_read(native, *parts[1:])
    if (len(parts) != 2 or parts[0] not in ("transcripts", "reports")
            or not re.fullmatch(GPO_KINDS[parts[0]] + r"-[A-Za-z0-9-]+", parts[1])):
        raise SourceError("Invalid congressional record ID")
    kind, package = parts
    base = f"{GPO}/content/pkg/{package}"
    html_url, pdf_url = f"{base}/html/{package}.htm", f"{base}/pdf/{package}.pdf"
    try:
        raw = _get(html_url)
    except requests.HTTPError as exc:
        if exc.response is None or exc.response.status_code != 404:
            raise
        raw = b""
    html = BeautifulSoup(raw, "html.parser")
    pre = html.find("pre")
    if pre and pre.get_text(strip=True):
        # Preserve the publisher's fixed-width tables, including recipient and amount columns.
        obs = _obs("congress", native, kind, html_url, {"title": _text(html.title) or package}, text=pre.get_text())
    else:
        obs = web.read(pdf_url)
    obs.update({"source": "congress", "native_id": native, "kind": "hearing_transcript" if kind == "transcripts" else "committee_report"})
    obs["data"].update({"package_id": package, "pdf_url": pdf_url, "package_url": f"{GPO}/app/details/{package}"})
    obs["data"]["title"] = obs["data"].get("title") or package
    return obs


def search(source, query=None, *, limit=20, offset=0, **filters):
    limit, offset = int(limit), int(offset)
    if not 1 <= limit <= 100 or offset < 0:
        raise SourceError("Use a limit from 1 to 100 and a nonnegative offset")
    return {"regulations_gov": _reg_search, "oira": _oira_search, "congress": _congress_search}[source](
        query, filters, limit, offset)


def read(source, native_id):
    return {"regulations_gov": _reg_read, "oira": _oira_read, "congress": _congress_read}[source](native_id)
