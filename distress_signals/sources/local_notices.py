"""WARN notices and the agenda items published for local legislative meetings."""

import csv
import datetime as dt
import io
import os
import re
from urllib.parse import quote, unquote, urljoin

import requests
from bs4 import BeautifulSoup
from pypdf.errors import PyPdfError

from ..util import SourceError, http_json, session, stable_id, to_date, today
from . import web


FILTERS = {
    "warn": {"state", "effective_from", "effective_until", "page"},
    "local_agendas": {"client", "since", "until", "page"},
}
WARN_INDEX = "https://warnindex.com"
# Existing observations may still identify rows in the APVentureEngine archive.
ARCHIVE = "https://raw.githubusercontent.com/APVentureEngine/warn-act-notices/main/data"
WA = "https://fortress.wa.gov/esd/file/WARN/Public/SearchWARN.aspx"
LEGISTAR = "https://webapi.legistar.com/v1"


def _dates(start, end):
    try:
        for value in (start, end):
            if value is not None and dt.date.fromisoformat(value).isoformat() != value:
                raise ValueError()
        if start and end and start > end:
            raise ValueError()
    except (ValueError, TypeError):
        raise SourceError("Use YYYY-MM-DD dates, with the start on or before the end.") from None
    return start, end


def _includes(value, start, end):
    return (not start or bool(value and value >= start)) and (not end or bool(value and value <= end))


def _archive_file(state=None):
    return f"{ARCHIVE}/by-state/{state.lower()}.csv" if state else f"{ARCHIVE}/warn_notices.csv"


def _archive_rows(state):
    url = _archive_file(state)
    response = session().get(url, timeout=40)
    response.raise_for_status()
    rows = csv.DictReader(io.StringIO(response.content.decode("utf-8-sig")))
    if not {"id", "state", "company", "notice_date", "effective_date"}.issubset(rows.fieldnames or []):
        raise SourceError("The WARN archive's records no longer have the expected columns.")
    return rows


def _warn_row(row, directory):
    state = row["state"]
    return {
        "record_id": f"warn:{state}:{row['id']}", "title": row["company"],
        "url": _archive_file(state), "state_source_url": directory.get(state, {}).get("url"),
        "publisher": "APVentureEngine normalized WARN archive",
        **{key: row.get(key) or None for key in ("state", "location", "employees_affected", "notice_type",
                                                  "notice_date", "effective_date", "first_seen")},
    }


def _index_row(row):
    record_id = row["notice_id"]
    return {"record_id": f"warn:index:{record_id}", "title": row["employer"],
            "url": f"{WARN_INDEX}/notices/{record_id}", "publisher": "WARN Index",
            "state": row["state"], "location": row.get("location"), "address": row.get("address"),
            "notice_date": row.get("notice_date"), "effective_date": row.get("effective_date"),
            "employees_affected": row.get("layoff_count"), "notice_type": row.get("closure_type"),
            "state_source_url": row.get("source_url"), "raw_notice_url": row.get("raw_notice_url"),
            "scraped_at": row.get("scraped_at")}


def _warn_index(query, state, start, end, page):
    # The publisher's after/before filter is on the notice date. The planned layoff is a different date.
    size = 50
    offset = (page - 1) * size
    params = {"offset": offset, "limit": size, "sort_by": "notice_date", "sort_dir": "desc"}
    if state:
        params["state"] = state
    if query:
        params["employer"] = query
    data = http_json(f"{WARN_INDEX}/api/notices", params=params)
    items = data["items"]
    more = offset + len(items) < data["total"]
    return {"results": [_index_row(r) for r in items if _includes(r.get("effective_date"), start, end)],
            "notices_scanned": len(items), "total_before_effective_date_filter": data["total"],
            "complete": not more, "next_page": page + 1 if more else None,
            "note": "WARN Index transcribes state records. Each page checks the next 50 notices in filing-date "
                    "order and filters their effective dates. Follow state_source_url or raw_notice_url "
                    "to check the original record."}


def _wa_form(query, page):
    """Use the form's current state and only the page numbers offered by the state website."""
    def request(data=None):
        r = session().post(WA, data=data, timeout=30) if data is not None else session().get(WA, timeout=30)
        r.raise_for_status()
        doc = BeautifulSoup(r.content, "html.parser")
        if not doc.select_one('input[name="ucPSW$txtSearch"]'):
            raise SourceError("Washington's WARN search form is unavailable.")
        return doc

    def fields(doc):
        return {tag["name"]: tag.get("value", "") for tag in doc.select('input[type="hidden"][name]')}

    doc = request()
    if query:
        doc = request({**fields(doc), "ucPSW$txtSearch": query, "ucPSW$btnSearchCompany": "Search"})
    current = 1
    while True:
        grid = doc.select_one("#ucPSW_gvMain")
        if grid is None:
            return None, False
        available = {int(n) for link in grid.find_all("a", href=True)
                     for n in re.findall(r"Page\$(\d+)", link["href"])}
        if current == page:
            return grid, any(n > page for n in available)
        forward = [n for n in available if current < n <= page]
        if not forward:
            return None, False
        current = max(forward)
        doc = request({**fields(doc), "ucPSW$txtSearch": query, "__EVENTTARGET": "ucPSW$gvMain",
                       "__EVENTARGUMENT": f"Page${current}"})
        selected = doc.select_one("#ucPSW_gvMain td[colspan] span")
        if selected and selected.get_text(strip=True) != str(current):
            raise SourceError("Washington WARN did not return the requested results page.")


def _warn_washington(query, start, end, page):
    grid, more = _wa_form(query, page)
    results = []
    if grid is not None:
        headings = [th.get_text(" ", strip=True) for th in grid.find_all("th")]
        expected = {"Company", "Location", "Layoff Start Date", "# of Workers", "Received Date", "Notice"}
        if not expected.issubset(headings):
            raise SourceError("Washington's WARN table columns changed.")
        for tr in grid.find_all("tr", recursive=False):
            cells = tr.find_all("td", recursive=False)
            if len(cells) != len(headings):
                continue
            row = {h: c.get_text(" ", strip=True) for h, c in zip(headings, cells)}
            link = cells[headings.index("Notice")].find("a", href=True)
            effective = to_date(row["Layoff Start Date"])
            date = effective.isoformat() if effective else None
            if not _includes(date, start, end):
                continue
            letter = urljoin(WA, link["href"]) if link else None
            identity = stable_id(letter) if letter else stable_id(row["Company"], row["Location"],
                                                                   row["Received Date"], row["Layoff Start Date"])
            record_id = f"warn:entry:{quote(row['Company'], safe='')}:{identity}"
            results.append({"record_id": record_id, "url": letter or WA, "directory_url": WA, "letter_url": letter,
                            "title": row["Company"], "location": row["Location"],
                            "effective_date": date, "received_date": row["Received Date"],
                            "employees_affected": row["# of Workers"], "notice_type": row.get("Closure Layoff"),
                            "layoff_type": row.get("Type of Layoff")})
    return {"results": results, "complete": not more, "next_page": page + 1 if more else None,
            "state_directory": WA,
            "note": "Washington ESD pages employer matches by receipt date. Date filters apply to "
                    "layoff start dates on this page."}


def _client(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-z0-9-]{1,70}", value):
        raise SourceError("Use the jurisdiction's lowercase Legistar client slug.")
    return value


def _legistar(client, path, params=None):
    params = dict(params or {})
    token_name = "LEGISTAR_TOKEN_" + client.upper().replace("-", "_")
    token = os.environ.get(token_name)
    if token:
        params["token"] = token
    try:
        return http_json(f"{LEGISTAR}/{client}/{path}", params=params, tries=2, timeout=30)
    except (requests.RequestException, ValueError) as e:
        # The publisher takes its access token in the URL. Keep it out of tool logs.
        response = getattr(e, "response", None)
        status = response.status_code if response is not None else None
        if status in (401, 403):
            raise SourceError(f"Legistar denied access for {client}. Set its issued token in {token_name}.") from None
        raise SourceError(f"Legistar could not read {client}/{path} (HTTP {status or 'unavailable'}).") from None


def _meeting(client, event):
    event_id = event["EventId"]
    return {"record_id": f"local_agendas:{client}:{event_id}", "title": event["EventBodyName"],
            "url": event.get("EventInSiteURL") or f"{LEGISTAR}/{client}/events/{event_id}",
            "meeting_date": event["EventDate"][:10], "meeting_time": event.get("EventTime"),
            "agenda_last_published": event.get("EventAgendaLastPublishedUTC"),
            "agenda_status": event.get("EventAgendaStatusName"), "agenda_pdf": event.get("EventAgendaFile")}


def _items(client, event_id):
    rows = _legistar(client, f"events/{event_id}/eventitems", {"AgendaNote": 1})
    return [{k: row.get(k) for k in ("EventItemId", "EventItemAgendaNumber", "EventItemMatterFile",
                                      "EventItemTitle", "EventItemMatterName", "EventItemAgendaNote")}
            for row in rows]


def _agendas(query, client, since, until, page):
    client = _client(client)
    start = since or today().isoformat()
    start, end = _dates(start, until)
    end = end or (dt.date.fromisoformat(start) + dt.timedelta(days=30)).isoformat()
    # Legistar EventDate is a local calendar date at midnight, independent of EventTime.
    params = {"$filter": f"EventDate ge datetime'{start}' and EventDate le datetime'{end}'",
              "$orderby": "EventDate asc,EventId asc", "$top": 6, "$skip": (page - 1) * 5}
    events = _legistar(client, "events", params)
    more = len(events) > 5
    results = []
    for event in events[:5]:
        matches = []
        for item in _items(client, event["EventId"]):
            words = (item.get(key) or "" for key in ("EventItemTitle", "EventItemMatterName",
                                                       "EventItemMatterFile", "EventItemAgendaNote"))
            if any(query.casefold() in word.casefold() for word in words):
                matches.append(item)
        if matches:
            results.append({**_meeting(client, event), "matching_items": len(matches),
                            "items": matches[:5]})
    return {"results": results, "client": client, "since": start, "until": end,
            "meetings_scanned": min(5, len(events)), "complete": not more,
            "next_page": page + 1 if more else None,
            "note": "One page searches five meetings. Read a meeting for its full public agenda-item text."}


def search(source, query, **filters):
    page = filters.get("page", 1)
    if type(page) is not int or page < 1:
        raise SourceError("page must be a positive integer.")
    try:
        if source == "local_agendas":
            return _agendas(query or "", filters.get("client"), filters.get("since"),
                            filters.get("until"), page)
        value = filters.get("state")
        if value is not None and (not isinstance(value, str) or not re.fullmatch(r"[A-Za-z]{2}", value)):
            raise SourceError("state must be a two-letter state or district code.")
        state = (value or "").upper()
        start, end = _dates(filters.get("effective_from"), filters.get("effective_until"))
        if state == "WA":
            return _warn_washington(query or "", start, end, page)
        return _warn_index(query or "", state, start, end, page)
    except (requests.RequestException, ValueError, KeyError, TypeError) as e:
        raise SourceError(f"{source} could not read the publisher's records ({type(e).__name__}).") from None


def _read_wa(native_id):
    _, encoded, identity = native_id.split(":")
    employer = unquote(encoded)
    if not employer or not re.fullmatch(r"[a-f0-9]{20}", identity):
        raise SourceError("Invalid Washington WARN entry ID.")
    page = 1
    while page:
        batch = _warn_washington(employer, None, None, page)
        row = next((r for r in batch["results"] if r["record_id"] == f"warn:{native_id}"), None)
        if row:
            lines = [f"{key}: {value}" for key, value in row.items() if key != "record_id" and value is not None]
            if row["letter_url"]:
                try:
                    letter = web.read(row["letter_url"])
                    lines.extend(["", "Filed letter", letter["text"]])
                except (SourceError, PyPdfError):
                    row["url"] = row["directory_url"]
                    lines.append("The linked letter could not be read. The fields above come from Washington ESD's directory.")
            return {"kind": "record", "source": "warn", "native_id": native_id, "url": row["url"],
                    "published": None, "text": "\n".join(lines), "data": row}
        page = batch["next_page"]
    raise SourceError("This notice no longer appears in Washington's WARN directory.")


def read(source, native_id):
    try:
        if source == "warn" and native_id.startswith("entry:"):
            return _read_wa(native_id)
        if source == "local_agendas":
            client, event_id = native_id.split(":")
            client = _client(client)
            if not event_id.isdecimal():
                raise SourceError("Invalid Legistar event ID.")
            event = _legistar(client, f"events/{event_id}")
            data = _meeting(client, event)
            data["agenda_items"] = _items(client, event_id)
            published = data["agenda_last_published"]
            fields = {k: v for k, v in data.items() if k not in {"record_id", "agenda_items"}}
            lines = [f"{k}: {v}" for k, v in fields.items() if v is not None]
            for item in data["agenda_items"]:
                lines.append("")
                lines.extend(f"{k}: {v}" for k, v in item.items() if v is not None)
        elif source == "warn" and native_id.startswith("index:"):
            row_id = native_id.removeprefix("index:")
            if not re.fullmatch(r"[a-f0-9]{32}", row_id):
                raise SourceError("Invalid WARN Index notice ID.")
            row = http_json(f"{WARN_INDEX}/api/notices/{row_id}")
            data = _index_row(row)
            published = None
            lines = [data["publisher"], data["url"]]
            lines.extend(f"{k}: {v}" for k, v in row.items() if v is not None)
        else:
            state, row_id = native_id.split(":")
            if not re.fullmatch(r"[A-Z]{2}", state) or not re.fullmatch(r"[a-fA-F0-9]+", row_id):
                raise SourceError("Invalid WARN archive record ID.")
            row = next((r for r in _archive_rows(state) if r["id"] == row_id), None)
            if row is None:
                raise SourceError("This notice no longer appears in the WARN archive.")
            directory = http_json(f"{ARCHIVE}/source_formats.json")["states"]
            data = {**_warn_row(row, directory), "archive_row": row}
            published = None
            lines = [data["publisher"], data["url"]]
            lines.extend(f"{key}: {value}" for key, value in row.items() if value)
        return {"kind": "record", "source": source, "native_id": native_id, "url": data["url"],
                "published": published, "text": "\n".join(lines),
                "data": data}
    except (requests.RequestException, ValueError, TypeError, KeyError) as e:
        raise SourceError(f"Could not read {source} record ({type(e).__name__}).") from None
