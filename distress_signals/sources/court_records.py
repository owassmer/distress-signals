"""Federal court docket entries, Supreme Court proceedings, and GAO bid-protest dockets."""

import datetime as dt
import os
import re
from urllib.parse import parse_qs, urljoin, urlsplit

import requests
from bs4 import BeautifulSoup
from pypdf.errors import PyPdfError

from ..util import SourceError, session
from . import web


FILTERS = {"federal_dockets": {"page", "cursor", "docket_id", "court"},
           "supreme_court": {"page"}, "gao_protests": {"agency", "status", "page"}}
COURT = "https://www.courtlistener.com"
CL = f"{COURT}/api/rest/v4"
IA = "https://archive.org"
SC = "https://www.supremecourt.gov"
SC_FORM = f"{SC}/docket/docket.aspx"
GAO = "https://www.gao.gov"
GOVCON = "https://govconapi.com/api/v1/protests"
ARCHIVE_ID = re.compile(r"gov\.uscourts\.[a-z0-9]+\.\d+")
SC_ID = re.compile(r"(?:\d{2}(?:-\d+|[a-z]\d+)|\d+o\d+)", re.I)
BNO = re.compile(r"B-\d{6}(?:\.\d+)?", re.I)


def _request(url, *, data=None, params=None, headers=None):
    try:
        if data is None:
            response = session().get(url, params=params, headers=headers, timeout=45)
        else:
            response = session().post(url, data=data, timeout=45)
        response.raise_for_status()
    except requests.RequestException as exc:
        code = exc.response.status_code if exc.response is not None else type(exc).__name__
        raise SourceError(f"Could not read {url} ({code}).") from None
    return response


def _json(url, **kwargs):
    try:
        return _request(url, **kwargs).json()
    except ValueError:
        raise SourceError(f"The publisher did not return JSON at {url}.") from None


def _page(filters):
    value = filters.get("page", 1)
    if type(value) is not int or value < 1:
        raise SourceError("page must be a positive integer.")
    return value


def _identifier(value, pattern):
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise SourceError("Unrecognized docket identifier.")
    return value


def _observe(source, native, url, title, fields, sections, links=None, published=None):
    lines = [title, *(f"{key}: {value}" for key, value in fields.items() if value is not None), *sections]
    return {"kind": "record", "source": source, "native_id": native, "url": url, "published": published,
            "text": "\n".join(lines), "data": {"title": title, **fields, "links": links or []}}


def _public_file(identifier):
    return f"{IA}/download/{identifier}/{identifier}.docket.json"


def _archive_search(query, filters):
    page = _page(filters)
    if filters.get("cursor") or filters.get("docket_id"):
        raise SourceError("A live RECAP cursor or CourtListener docket_id requires COURTLISTENER_TOKEN.")
    terms = ["collection:usfederalcourts", "identifier:gov.uscourts.*"]
    if court := filters.get("court"):
        _identifier(court, re.compile("[a-z0-9]+"))
        terms.append(f"identifier:gov.uscourts.{court}.*")
    if query:
        # A quoted title phrase is literal, rather than an Internet Archive query expression.
        escaped = query.replace("\\", "\\\\").replace('"', '\\"')
        terms.append(f'title:"{escaped}"')
    params = {"q": " AND ".join(terms), "fl[]": ["identifier", "title", "addeddate"], "sort[]": "addeddate desc",
              "rows": 25, "page": page, "output": "json"}
    payload = _json(f"{IA}/advancedsearch.php", params=params)["response"]
    results = [{"record_id": f"federal_dockets:{r['identifier']}", "title": r["title"],
                "url": f"{IA}/details/{r['identifier']}", "archive_added": r.get("addeddate")}
               for r in payload["docs"] if ARCHIVE_ID.fullmatch(r["identifier"])]
    more = payload["start"] + len(payload["docs"]) < payload["numFound"]
    return {"results": results, "next_page": page + 1 if more else None, "complete": not more,
            "note": "Caption search of the Internet Archive RECAP collection. Archive inclusion and "
                    "freshness vary by docket. Read the docket snapshot or the filings that were deposited."}


def _live(path, params=None):
    token = os.getenv("COURTLISTENER_TOKEN")
    if not token:
        raise SourceError("Set COURTLISTENER_TOKEN to use CourtListener's authenticated RECAP API.")
    return _json(f"{CL}/{path}/", params=params, headers={"Authorization": f"Token {token}"})


def _cl_result(row):
    path = row.get("docket_absolute_url") or row.get("absolute_url") or ""
    match = re.search(r"/docket/(\d+)/", path)
    ident = row.get("docket_id") or (match[1] if match else row.get("id"))
    _identifier(str(ident), re.compile(r"\d+"))
    return {"record_id": f"federal_dockets:cl:{ident}", "title": row["caseName"],
            "url": urljoin(COURT, path or f"/docket/{ident}/"), "court": row.get("court"),
            "docket_number": row.get("docketNumber"), "case_filed": row.get("dateFiled"),
            "case_terminated": row.get("dateTerminated"),
            "matching_entries": [{k: d.get(k) for k in ("entry_date_filed", "entry_number", "short_description",
                                                         "description", "snippet", "absolute_url")}
                                 for d in row.get("recap_documents", [])]}


def _federal_search(query, filters):
    if ARCHIVE_ID.fullmatch(query):
        if filters:
            raise SourceError("A direct RECAP archive identifier takes no filters.")
        record = _read_archive(query)
        return {"results": [{"record_id": f"federal_dockets:{query}", "url": record["url"],
                             "title": record["data"]["title"], "last_filing": record["data"].get("last_filing")}]}
    if not os.getenv("COURTLISTENER_TOKEN"):
        return _archive_search(query, filters)
    if "page" in filters:
        raise SourceError("Live RECAP results use next_cursor, passed back as cursor.")
    params = {}
    if filters.get("cursor"):
        params["cursor"] = filters["cursor"]
    docket = filters.get("docket_id")
    if docket is not None:
        if query or filters.get("court"):
            raise SourceError("When listing entries by docket_id, use an empty query and supply only its cursor.")
        params["docket"] = _identifier(str(docket), re.compile(r"\d+"))
        payload = _live("docket-entries", params)
        results = [{"record_id": f"federal_dockets:entry:{r['id']}", "title": r.get("description") or
                    f"Docket {docket}, entry {r.get('entry_number')}", "url": f"{CL}/docket-entries/{r['id']}/",
                    "entry_filed": r.get("date_filed"), "entry_number": r.get("entry_number")}
                   for r in payload["results"]]
    else:
        params.update(type="r", q=query)
        if filters.get("court"):
            params["court"] = filters["court"]
        payload = _live("search", params)
        results = [_cl_result(r) for r in payload["results"]]
    next_url = payload.get("next")
    cursor = parse_qs(urlsplit(next_url or "").query).get("cursor", [None])[0]
    return {"results": results, "complete": not next_url, "next_cursor": cursor,
            "note": "RECAP covers contributed federal court records. Entry filing dates date the document."}


def _entry_text(entries):
    lines, links = [], []
    for entry in entries:
        lines.append(f"Entry {entry.get('entry_number') or ''} filed {entry.get('date_filed') or ''}")
        if entry.get("description"):
            lines.append(entry["description"])
        for document in entry.get("recap_documents") or []:
            if not isinstance(document, dict):
                continue
            if document.get("description"):
                lines.append(document["description"])
            # Only link to files already contributed to the free archive.
            path = document.get("filepath_local")
            if path and document.get("is_available"):
                url = urljoin("https://storage.courtlistener.com/", path)
                parsed = urlsplit(url)
                if parsed.scheme == "https" and parsed.hostname == "storage.courtlistener.com" and parsed.path.startswith("/recap/"):
                    links.append({"text": document.get("description") or f"Entry {entry.get('entry_number')}",
                                  "url": url})
    return lines, links


def _read_archive(native):
    _identifier(native, ARCHIVE_ID)
    index_url = f"{IA}/details/{native}"
    index = _json(f"{IA}/metadata/{native}")
    metadata = index.get("metadata") or {}
    if metadata.get("identifier") != native:
        raise SourceError("The RECAP collection has no such docket.")
    files = index.get("files") or []
    if not any(f["name"] == f"{native}.docket.json" for f in files):
        entries = []
        for file in files:
            name = file["name"]
            match = re.fullmatch(re.escape(native) + r"\.(\d+)\.(\d+)(?:_[\w-]+)?\.pdf", name, re.I)
            if match:
                entries.append((int(match[1]), int(match[2]), name))
        entries.sort(reverse=True)
        if not entries:
            raise SourceError("The RECAP collection lists this case without a docket snapshot or filing PDFs.")
        links = [{"text": f"Entry {n}, attachment {a}", "url": f"{IA}/download/{native}/{name}"}
                 for n, a, name in entries]
        lines = ["The archive holds the filings below without a docket sheet.",
                 *(f"{link['text']}: {link['url']}" for link in links)]
        try:
            lines.extend(["", links[0]["text"], web.read(links[0]["url"])["text"]])
        except (SourceError, PyPdfError):
            lines.append("The first available PDF could not be extracted. Its file link is retained.")
        return _observe("federal_dockets", native, index_url, metadata.get("title") or native,
                        {"archived_filings": len(links), "docket_snapshot": False}, lines, links)
    url = _public_file(native)
    d = _json(url)
    parties = [f"{p['name']} ({', '.join(t['name'] for t in p.get('party_types', []))})" for p in d.get("parties", [])]
    fields = {"docket_number": d.get("docket_number"), "case_filed": d.get("date_filed"),
              "case_terminated": d.get("date_terminated"), "last_filing": d.get("date_last_filing"),
              "archive_snapshot_updated": d.get("date_modified")}
    lines, links = _entry_text(d["docket_entries"])
    return _observe("federal_dockets", native, url, d.get("case_name_full") or d["case_name"], fields,
                    [*parties, *lines], links)


def _read_live(native):
    kind, _, ident = native.partition(":")
    _identifier(ident, re.compile(r"\d+"))
    if kind == "entry":
        d = _live(f"docket-entries/{ident}")
        lines, links = _entry_text([d])
        return _observe("federal_dockets", native, f"{CL}/docket-entries/{ident}/", f"Docket entry {ident}",
                        {"docket": d.get("docket"), "entry_number": d.get("entry_number")}, lines, links,
                        published=d.get("date_filed"))
    if kind != "cl":
        raise SourceError("Invalid CourtListener record identifier.")
    docket = _live(f"dockets/{ident}")
    batch = _live("docket-entries", {"docket": ident})
    lines, links = _entry_text(batch["results"])
    fields = {"docket_number": docket.get("docket_number"), "case_filed": docket.get("date_filed"),
              "case_terminated": docket.get("date_terminated"), "last_filing": docket.get("date_last_filing")}
    if batch.get("next"):
        lines.append(f"Additional entries available. Search federal_dockets with docket_id={ident} to page them.")
    return _observe("federal_dockets", native, urljoin(COURT, docket.get("absolute_url") or f"/docket/{ident}/"),
                    docket.get("case_name_full") or docket["case_name"], fields, lines, links)


def _sc_url(number):
    return f"{SC}/docket/docketfiles/html/public/{number.lower()}.html"


def _sc_case(number):
    _identifier(number, SC_ID)
    return _json(f"{SC}/rss/cases/json/{number}.json")


def _sc_summary(d):
    number = d["CaseNumber"].strip()
    title = " v. ".join(t.strip() for t in (d.get("PetitionerTitle"), d.get("RespondentTitle")) if t and t.strip())
    return {"record_id": f"supreme_court:{number}", "title": title or number, "url": _sc_url(number),
            "case_number": number, "docketed": d.get("DocketedDate")}


def _sc_search(query, page):
    if SC_ID.fullmatch(query):
        return {"results": [_sc_summary(_sc_case(query))] if page == 1 else [], "complete": True}
    if len(query) < 3:
        raise SourceError("Give a Supreme Court case number or at least three characters of docket search text.")
    doc = BeautifulSoup(_request(SC_FORM).content, "html.parser")
    control = "ctl00$ctl00$MainEditable$mainContent$"
    for current in range(1, page + 1):
        form = doc.find("form")
        if form is None or not form.select_one('input[name$="mainContent$txtQuery"]'):
            raise SourceError("The Supreme Court docket search form is unavailable.")
        fields = {tag["name"]: tag.get("value", "") for tag in form.select('input[type="hidden"][name]')}
        fields[control + "txtQuery"] = query
        if current == 1:
            fields[control + "cmdSearch"] = "Search"
        else:
            if not doc.select_one('a[id$="cmdNext"][href]'):
                return {"results": [], "complete": True}
            fields["__EVENTTARGET"] = control + "cmdNext"
        doc = BeautifulSoup(_request(SC_FORM, data=fields).content, "html.parser")
    hits = []
    for link in doc.select('fieldset a[href*="/docket/docketfiles/html/public/"]'):
        match = re.search(r"/public/([^/?#]+)\.html", link["href"], re.I)
        if not match or not SC_ID.fullmatch(match[1]):
            continue
        number = match[1].upper()
        title_cell = link.parent.find("td")
        title = " ".join(title_cell.stripped_strings) if title_cell else link.get_text(" ", strip=True)
        hits.append({"record_id": f"supreme_court:{number}", "case_number": number,
                     "title": " ".join(title.split()), "url": _sc_url(number)})
    label = doc.select_one('[id$="lblCurrentPage"]')
    notice = label.get_text(" ", strip=True) if label else ""
    if not hits and not ("no items found" in doc.get_text(" ", strip=True).lower()):
        raise SourceError("The court returned no recognizable docket results. Refine the docket search.")
    more = bool(doc.select_one('a[id$="cmdNext"][href]'))
    limited = bool(re.search(r"\d+\+ items found|limited to the last", notice, re.I))
    return {"results": hits, "next_page": page + 1 if more else None, "complete": not more and not limited,
            "court_search_notice": notice}


def _read_sc(native):
    d = _sc_case(native)
    row = _sc_summary(d)
    parties = [f"{role}: {p['PartyName']}" for role in ("Petitioner", "Respondent", "Other")
               for p in d.get(role) or [] if p.get("PartyName")]
    lines, links = list(dict.fromkeys(parties)), []
    for entry in d.get("ProceedingsandOrder") or []:
        lines.append(f"{entry.get('Date') or ''}: {entry.get('Text') or ''}")
        for link in entry.get("Links") or []:
            if link.get("DocumentUrl"):
                links.append({"text": link.get("Description") or "Filed document", "url": link["DocumentUrl"]})
    return _observe("supreme_court", native, row["url"], row["title"],
                    {"docketed": row["docketed"], "lower_court": d.get("LowerCourt"),
                     "docket_updated": d.get("sJsonCreationDate")}, lines, links)


def _gao_record(number):
    _identifier(number, BNO)
    url = f"{GAO}/docket/{number.lower()}"
    doc = BeautifulSoup(_request(url).content, "html.parser")
    fields = {}
    for container in doc.select("main .field"):
        label, value = container.select_one(".field__label"), container.select_one(".field__item")
        if label and value:
            stamp = value.find("time", datetime=True)
            fields[label.get_text(" ", strip=True)] = stamp["datetime"][:10] if stamp else value.get_text(" ", strip=True)
    if fields.get("File number", "").upper() != number.upper() or not fields.get("Protester"):
        raise SourceError("GAO did not return the requested bid-protest docket.")
    posted = doc.find(string=re.compile(r"^\s*Posted on ", re.I))
    posted = posted.strip().removeprefix("Posted on").strip(" .") if posted else None
    if posted:
        try:
            posted = dt.datetime.strptime(posted, "%b %d, %Y").date().isoformat()
        except ValueError:
            pass
    links = [{"text": a.get_text(" ", strip=True) or "Decision", "url": urljoin(GAO, a["href"])}
             for a in doc.select('main a[href*="/products/"],main a[href*="/assets/"]')]
    return _observe("gao_protests", number.upper(), url, fields["Protester"], fields, [], links, published=posted)


def _gao_search(query, filters):
    page = _page(filters)
    if BNO.fullmatch(query):
        if any(k in filters for k in ("agency", "status")):
            raise SourceError("A direct GAO B-number takes no agency or status filters.")
        if page != 1:
            return {"results": [], "complete": True}
        record = _gao_record(query)
        hit = {**record["data"], "record_id": f"gao_protests:{record['native_id']}", "url": record["url"]}
        return {"results": [hit], "complete": True}
    key = os.getenv("GOVCON_API_KEY")
    if not key:
        raise SourceError("A GAO B-number opens the official docket without a key. "
                          "Set GOVCON_API_KEY for protest discovery by text, agency or status.")
    params = {"search": query, "limit": 25, "offset": (page - 1) * 25, "sort": "filed"}
    if filters.get("agency"):
        params["agency"] = filters["agency"]
    if status := filters.get("status"):
        if status not in {"Open", "Closed"}:
            raise SourceError("GAO status is Open or Closed.")
        params["status"] = status
    data = _json(GOVCON, params=params, headers={"Authorization": f"Bearer {key}"})
    hits = []
    for r in data["data"]:
        number = _identifier(r["b_number"], BNO).upper()
        hits.append({"record_id": f"gao_protests:{number}", "title": r["protester"], "url": f"{GAO}/docket/{number.lower()}",
                     **{k: r.get(k) for k in ("solicitation_number", "agency", "docket_status", "filed_date",
                                                 "due_date", "decision_date", "outcome")}})
    more = data["pagination"]["has_next"]
    return {"results": hits, "next_page": page + 1 if more else None, "complete": not more,
            "discovery_publisher": "GovCon API", "coverage": data.get("coverage")}


def search(source, query, **filters):
    query = (query or "").strip()
    if source == "federal_dockets":
        return _federal_search(query, filters)
    if source == "supreme_court":
        return _sc_search(query, _page(filters))
    return _gao_search(query, filters)


def read(source, native_id):
    if source == "federal_dockets":
        return _read_archive(native_id) if ARCHIVE_ID.fullmatch(native_id) else _read_live(native_id)
    if source == "supreme_court":
        return _read_sc(native_id)
    return _gao_record(native_id)
