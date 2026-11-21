"""Five series: FERC's eLibrary, state utility commission dockets (South Dakota) and hearings (Illinois), and
PJM's generator deactivations and the notice letters behind them.

  ferc_elibrary                  eLibrary's own search applies the words, a docket and one date range, and
                                 pages by number.
  utility_dockets                South Dakota publishes one index page per sector and year; a search reads the
                                 years it needs, newest first.
  utility_commission_hearings    Illinois publishes its hearing calendar a week at a time; a search reads the
                                 weeks it needs, latest first.
  pjm_generator_deactivations    PJM's export of every deactivation request, read whole.
  pjm_deactivation_notices       PJM's index of the letters: owners' notices, revisions and PJM's responses.
"""
import datetime as dt
import io
import json
import re
import xml.etree.ElementTree as ET
from urllib.parse import quote, urljoin
from zipfile import ZipFile

import requests
from bs4 import BeautifulSoup

from ..util import Missing, SourceError, http_json, session, to_date, to_float, today
from . import F, Series, outline, page, web

FERC_API = "https://elibrary.ferc.gov/eLibrarywebapi/api"
FERC = "https://elibrary.ferc.gov/eLibrary"
PJM_XML = "https://www.pjm.com/pjmfiles/media/planning/gen-deactivation-data/GenDeactivationUnits.xml"
PJM_NOTICES = "https://www.pjm.com/planning/service-requests/gen-deactivations/generator-deactivation-notices"
SD = "https://puc.sd.gov/dockets"
IL = "https://icc.illinois.gov/meetings"

def _text(node):
    return " ".join(node.stripped_strings)


def _matches(query, *values):
    haystack = " ".join(str(v) for v in values).casefold()
    return all(token in haystack for token in query.casefold().split())


def _get(url, **params):
    try:
        response = session().get(url, params=params or None, timeout=45)
        response.raise_for_status()
    except requests.RequestException as e:
        status = e.response.status_code if getattr(e, "response", None) is not None else None
        raise (Missing if status in (404, 410) else SourceError)(f"{url} could not be read ({status or type(e).__name__})") from None
    return response


def _ferc_search(query, page=1, limit=30, docket=None, since=None, until=None, accession=None, dated="posted_date"):
    dates = []
    if since or until:
        start = dt.date.fromisoformat(since) if since else dt.date(1900, 1, 1)
        end = dt.date.fromisoformat(until) if until else today()
        if start > end:
            raise SourceError("the range's start must be on or before its end")
        dates = [{"dateType": dated, "startDate": start.strftime("%m/%d/%Y"),
                  "endDate": end.strftime("%m/%d/%Y")}]
    body = {"searchText": query, "searchDescription": True, "searchFullText": True,
            "docketSearches": [{"docketNumber": docket, "subDocketNumbers": []}] if docket else [],
            "accessionNumber": accession, "dateSearches": dates, "allDates": not dates, "availability": ["P"],
            "classTypes": [], "affiliations": [], "categories": [], "libraries": [], "eFiling": False,
            "curPage": page, "resultsPerPage": limit, "sortBy": "", "groupBy": "NONE", "idolResultID": ""}
    data = http_json(f"{FERC_API}/Search/AdvancedSearch", method="POST", json_body=body, tries=2, timeout=60)
    if not data.get("success") or not isinstance(data.get("searchHits"), list):
        raise SourceError(f"FERC could not return this search: {data.get('errorMessage') or 'unexpected response'}")
    return data


def _ferc_files(hit):
    return [{**t, "url": f"{FERC}/filedownload?fileid={quote(t['fileId'])}"} for t in hit.get("transmittals") or [] if t.get("fileId")]


def _ferc_record(hit):
    accession = hit.get("acesssionNumber") or hit.get("accessionNumber")
    if not accession:
        raise SourceError("FERC returned a filing without its accession number")
    kinds = hit.get("classTypes") or []
    return {"id": accession, "url": f"{FERC}/filelist?accession_number={accession}", "accession_number": accession,
            "docket_number": hit.get("docketNumbers") or [], "description": " ".join((hit.get("description") or "").split()),
            "category": hit.get("category"), "document_class": list(dict.fromkeys(k.get("documentClass") for k in kinds if k.get("documentClass"))),
            "document_type": list(dict.fromkeys(k.get("documentType") for k in kinds if k.get("documentType"))),
            "affiliation": list(dict.fromkeys(a.get("affiliation") for a in hit.get("affiliations") or [] if a.get("affiliation"))),
            "filed_date": _iso_us(hit.get("filedDate")), "issued_date": _iso_us(hit.get("issuedDate")),
            "posted_date": _iso_us(hit.get("postedDate"))}


def _docx_text(content):
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    with ZipFile(io.BytesIO(content)) as package:
        document = package.getinfo("word/document.xml")
        if document.file_size > web.MAX_BYTES:
            raise SourceError("The Word document exceeds the extraction limit")
        root = ET.fromstring(package.read(document))
    return "\n".join("".join(node.text or "" for node in p.findall(".//w:t", ns))
                     for p in root.findall(".//w:p", ns))


READABLE = 6  # the attachments of one filing whose text a read returns


def _ferc_read(accession):
    if not re.fullmatch(r"\d{8}-\d{4}", accession):
        raise SourceError("A ferc_elibrary record_id is its accession number")
    hit = next((h for h in _ferc_search("", accession=accession)["searchHits"]
                if (h.get("acesssionNumber") or h.get("accessionNumber")) == accession), None)
    if hit is None:
        raise SourceError(f"No public FERC filing for {accession}")
    files, lines, read = _ferc_files(hit), [], 0
    lines.append(f"Files ({len(files)}):")
    lines += [f"  {f.get('fileName')} ({f.get('fileType')}, {f.get('fileDesc') or ''}): {f['url']}" for f in files]
    for f in files:
        kind = str(f.get("fileType")).upper()
        if kind not in {"PDF", "DOCX", "TXT", "TEXT"} or read >= READABLE:
            continue
        try:
            with session().post(f"{FERC_API}/File/DownloadP8File", json={"fileidLst": [f["fileId"]]}, timeout=60, stream=True) as response:
                response.raise_for_status()
                content = response.raw.read(web.MAX_BYTES + 1, decode_content=True)
        except requests.RequestException as e:
            raise SourceError(f"FERC eLibrary did not give the file {f.get('fileName')} of {accession} ({type(e).__name__})") from None
        if len(content) > web.MAX_BYTES:
            lines += ["", f"File {f.get('fileName')}: larger than the {web.MAX_BYTES // 1_000_000} MB a read takes."]
            continue
        try:
            text = web._pdf(content)[0] if content.startswith(b"%PDF-") else _docx_text(content) if kind == "DOCX" \
                else content.decode("utf-8-sig", errors="replace")
        except Exception as e:  # the file arrived and could not be parsed
            lines += ["", f"File {f.get('fileName')}: its text could not be extracted ({type(e).__name__})."]
            continue
        if text.strip():
            read += 1
            lines += ["", f"File {f.get('fileName')}:", text]
    record = _ferc_record(hit)
    return {**record, "document": "\n".join(lines), "published": record["posted_date"],
            "links": [{"text": f.get("fileName") or "file", "url": f["url"]} for f in files]}


def _iso_us(value):
    return dt.datetime.strptime(value, "%m/%d/%Y").date().isoformat() if value else None


def _sd(query, year=None, sector=None):
    sectors = {"electric": ("Electric", "EL"), "gas": ("NaturalGas", "NG")}
    if sector and sector not in sectors:
        raise SourceError("South Dakota sectors are electric and gas")
    year = int(year or today().year)
    if not 1900 <= year <= 9999:
        raise SourceError("year must have four digits")
    rows = []
    for name in ([sector] if sector else sectors):
        path, prefix = sectors[name]
        index = f"{SD}/{path}/{year}/default.aspx"
        soup = BeautifulSoup(_get(index).content, "html.parser")
        links = soup.find_all("a", href=re.compile(rf"(?:^|/){prefix}\d{{2}}-\d+\.aspx$", re.I))
        if not links and not soup.find(string=re.compile(rf"{year}.*Dockets", re.I)):
            raise SourceError(f"Could not identify South Dakota docket index {index}")
        for a in links:
            title = _text(a)
            if not _matches(query, title):
                continue
            url = urljoin(index, a["href"])
            row = {"record_id": url, "url": url, "title": title, "state": "SD", "sector": name}
            if a.parent.name == "p":
                row["index_entry"] = _text(a.parent)
            rows.append(row)
    return rows


def _illinois(query, week_start=None):
    start = dt.date.fromisoformat(week_start) if week_start else today()
    ticks = (start.toordinal() - 1) * 864_000_000_000
    index = _get(IL, bd=ticks, dts=7, sh="True", ssh="True")
    soup = BeautifulSoup(index.content, "html.parser")
    if not soup.select_one("#beginDate"):
        raise SourceError("Illinois Commerce Commission did not return its hearing calendar")
    rows = []
    for a in soup.select('a.day[href*="/meeting/hearing/"]'):
        url = urljoin(IL, a["href"])
        hearing = BeautifulSoup(_get(url).content, "html.parser")
        docket = hearing.select_one('h2 a[href^="/docket/"]')
        if not docket:
            raise SourceError(f"The hearing detail is missing its docket link: {url}")
        panel = docket.find_parent("h2").parent
        time = panel.find("h3")
        statement = panel.find("p")
        title = _text(docket)
        description = _text(statement) if statement else ""
        if _matches(query, title, description):
            rows.append({"record_id": url, "url": url, "title": title, "description": description,
                         "scheduled": _text(time) if time else None, "state": "IL",
                         "docket_url": urljoin(IL, docket["href"])})
    return rows


def _units():
    root = ET.fromstring(_get(PJM_XML).content)
    if root.tag != "Units":
        raise SourceError("PJM did not return its generator-unit XML export")
    groups = {}
    for unit in root.findall("Unit"):
        fields = {c.tag: c.text for c in unit}
        title, state = fields["UnitName"], fields["State"]
        native = quote(f"{state}/{title}", safe="")
        row = groups.setdefault(native, {"record_id": "pjm_deactivations:" + native,
                                        "url": PJM_XML, "title": title, "state": state, "entries": []})
        row["entries"].append(fields)
    return groups


def _pjm_notices(query):
    soup = BeautifulSoup(_get(PJM_NOTICES).content, "html.parser")
    table = soup.select_one("table.table-striped")
    if not table or not table.select("tr td a"):
        raise SourceError("PJM did not return its deactivation notice index")
    rows = []
    for tr in table.select("tr"):
        cells = tr.find_all("td")
        if len(cells) < 2:
            continue
        unit = _text(cells[0]).partition(":")[0].strip()
        for link in cells[0].select("a[href]"):
            title = f"{unit}: {_text(link)}"
            if _matches(query, title):
                url = urljoin(PJM_NOTICES, link["href"])
                rows.append({"record_id": url, "url": url, "title": title, "index_date": _text(cells[1])})
    return rows


# ---------- FERC eLibrary ----------

def _search_ferc(s):
    at = s.cursor or {"page": 1, "per": max(1, min(s.size, 200))}
    dated = next((f for f in ("posted_date", "filed_date", "issued_date") if s.peek(f)), "posted_date")
    since, until = s.between(dated)
    try:
        data = _ferc_search(s.query(), at["page"], at["per"], docket=s.one("docket_number"), since=since, until=until, dated=dated)
    except SourceError:
        raise
    except Exception as e:
        raise SourceError(f"FERC eLibrary: {type(e).__name__}: {str(e)[:160]}")
    total = int(data.get("totalHits") or 0)
    return page([_ferc_record(h) for h in data["searchHits"]],
                {"page": at["page"] + 1, "per": at["per"]} if at["page"] * at["per"] < total and data["searchHits"] else None, total)


def _ferc_newest(series):
    data = _ferc_search("", 1, 1)
    return series.since, _iso_us((data["searchHits"] or [{}])[0].get("postedDate"))


# ---------- South Dakota dockets ----------

SECTORS = {"electric": "Electric", "gas": "Natural gas"}


def _sd_year(year):
    out = []
    for r in _sd("", year):
        number = r["url"].rsplit("/", 1)[-1].rsplit(".", 1)[0]
        out.append({"id": re.split(r"/dockets/", r["url"], maxsplit=1, flags=re.I)[1].rsplit(".", 1)[0], "url": r["url"], "state": "South Dakota",
                    "sector": SECTORS[r["sector"]], "docket_number": number, "year": year,
                    "title": r["title"].split(" - ", 1)[-1].strip()})
    return out


def _search_dockets(s):
    span = s.peek("year")
    year = s.cursor or int(min(span.get("hi") or today().year, today().year))
    if span.get("lo") is not None and year < span["lo"]:
        return page([])
    try:
        rows = _sd_year(year)
    except Missing:  # the commission has no index for that year: the dockets end there
        return page([])
    return page(rows, year - 1, order="latest year first")


def _read_docket(key):
    path, year, number = (key.split("/") + ["", ""])[:3]
    record = next((r for r in _sd_year(int(year)) if r["id"] == key), None) if year.isdigit() else None
    if not record:
        raise SourceError(f"South Dakota's {year} index does not list the docket {number}")
    got = web.read(record["url"])
    return {**record, "document": got["text"], "links": got["data"].get("links") or []}


# ---------- Illinois hearings ----------

WEEKS_AHEAD = 26  # how far ahead of today the hearing calendar is read


def _hearing(r):
    kind, _, docket = r["title"].partition(" for ")
    try:
        when = dt.datetime.strptime((r.get("scheduled") or "").split(" at ")[0].strip(), "%B %d, %Y").date().isoformat()
    except ValueError:
        when = None
    return {"id": r["url"].rstrip("/").rsplit("/", 1)[-1], "url": r["url"], "state": "Illinois", "docket": docket or r["title"],
            "hearing_type": kind if docket else None, "description": r["description"], "scheduled": when,
            "scheduled_time": r.get("scheduled")}


def _search_hearings(s):
    span = s.peek("scheduled")
    last = dt.date.fromisoformat(span["hi"]) if span.get("hi") else today() + dt.timedelta(weeks=WEEKS_AHEAD)
    first = dt.date.fromisoformat(span["lo"]) if span.get("lo") else None
    week = dt.date.fromisoformat(s.cursor) if s.cursor else last - dt.timedelta(days=6)
    try:
        rows = sorted((_hearing(r) for r in _illinois("", week.isoformat())), key=lambda r: r["scheduled"] or "", reverse=True)
    except SourceError:
        raise
    except Exception as e:
        raise SourceError(f"Illinois Commerce Commission's calendar: {type(e).__name__}: {str(e)[:150]}")
    before = week - dt.timedelta(days=7)
    done = (first and before + dt.timedelta(days=6) < first) or before.year < 2010
    return page(rows, None if done else before.isoformat(), order="latest scheduled first")


def _read_hearing(key):
    url = f"{IL}/meeting/hearing/{key}"
    try:
        hearing = BeautifulSoup(_get(url).content, "html.parser")
    except Missing:
        raise SourceError(f"Illinois Commerce Commission has no hearing {key}") from None
    docket = hearing.select_one('h2 a[href^="/docket/"]')
    if not docket:
        raise SourceError(f"The hearing page has no docket: {url}")
    panel = docket.find_parent("h2").parent
    record = _hearing({"url": url, "title": _text(docket), "description": _text(panel.find("p")) if panel.find("p") else "",
                       "scheduled": _text(panel.find("h3")) if panel.find("h3") else None})
    got = web.read(url)
    return {**record, "document": got["text"], "published": None,
            "links": [{"text": "Docket", "url": urljoin(IL, docket["href"])}] + (got["data"].get("links") or [])[:40]}


# ---------- PJM ----------

DEACTIVATION = ("Future Deactivation", "Withdrawn Deactivation", "Deactivated Generator")


def _iso(value):
    d = to_date(value)
    return d.isoformat() if d else None


def _deactivations():
    out = []
    for unit in _units().values():
        for i, f in enumerate(unit["entries"]):
            out.append({"id": f"{quote(f['State'] or '', safe='')}/{quote(f['UnitName'] or '', safe='')}/{i}", "url": PJM_XML,
                        "unit": f.get("UnitName"), "state": f.get("State"), "zone": f.get("TransmissionOwnerZone"),
                        "fuel_type": f.get("FuelType"), "capacity_mw": to_float(f.get("Capacity")), "status": f.get("Status"),
                        "request_letter_date": _iso(f.get("OfficialOwnerRequestLetterDateOriginal")),
                        "request_revised_date": _iso(f.get("OfficialOwnerRequestLetterDateRevised")),
                        "requested_deactivation_date": _iso(f.get("RequestedDeactivationDate")),
                        "projected_deactivation_date": _iso(f.get("ProjectedDeactivationDate")),
                        "actual_deactivation_date": _iso(f.get("ActualDeactivationDate")),
                        "withdrawn_date": _iso(f.get("WithdrawnDeactivationDate")),
                        "reliability_analysis": f.get("ReliabilityAnalysis"), "_fields": f})
    out.sort(key=lambda r: max(filter(None, (r["request_letter_date"], r["request_revised_date"], r["actual_deactivation_date"],
                                             r["withdrawn_date"], r["projected_deactivation_date"])), default=""), reverse=True)
    return out


def _read_deactivation(key):
    for r in _deactivations():
        if r["id"] == key:
            f = r["_fields"]
            links = [{"text": k, "url": v} for k, v in f.items() if isinstance(v, str) and v.startswith("http")]
            links.append({"text": "Notices and PJM's responses", "url": PJM_NOTICES})
            return {**{k: v for k, v in r.items() if k != "_fields"}, "document": "\n".join(outline(f)), "links": links}
    raise SourceError("PJM's export no longer has this unit's entry")


def _notices():
    out = []
    for r in _pjm_notices(""):
        unit, _, title = r["title"].partition(": ")
        when = None
        m = re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", (r.get("index_date") or "").strip())
        if m:
            when = dt.date(int(m[3]), int(m[1]), int(m[2])).isoformat()
        out.append({"id": r["url"].split("pjm.com/", 1)[-1], "url": r["url"], "unit": unit, "title": title, "posted_date": when})
    out.sort(key=lambda r: r["posted_date"] or "", reverse=True)
    return out


def _read_notice(key):
    record = next((r for r in _notices() if r["id"] == key), None)
    if not record:
        raise SourceError("PJM's index no longer lists this letter")
    got = web.read(record["url"])
    return {**record, "document": got["text"], "links": got["data"].get("links") or [], "published": record["posted_date"]}


def _span(rows, field):
    dates = [r[field] for r in rows if r.get(field)]
    return (min(dates), max(dates)) if dates else (None, None)


PJM = "PJM Interconnection"
SERIES = [
    Series(
        id="ferc_elibrary", title="FERC's eLibrary",
        record="a filing made with the Federal Energy Regulatory Commission, or an order or notice it issued, under its "
               "accession number",
        creator="FERC and the parties to its proceedings", publisher="Federal Energy Regulatory Commission",
        jurisdiction="federal", since="1981", coverage=_ferc_newest,
        fields=(F("accession_number", "identifier"), F("docket_number", "identifier", many=True), F("description", "text"),
                F("category", "text"), F("document_class", "text", many=True), F("document_type", "text", many=True),
                F("affiliation", "name", many=True), F("filed_date", "date"), F("issued_date", "date"),
                F("posted_date", "date"), F("full_text", "full text")),
        search=_search_ferc, read=_ferc_read),
    Series(
        id="utility_dockets", title="state utility commission dockets",
        record="a docket opened at a state public utilities commission: a rate case, tariff filing, siting permit or other proceeding",
        creator="state public utilities commissions", jurisdiction="South Dakota (electric and natural gas)",
        fields=(F("state", "place"), F("sector", tuple(SECTORS.values())), F("docket_number", "identifier"), F("year", "number"),
                F("title", "text")),
        search=_search_dockets, read=_read_docket),
    Series(
        id="utility_commission_hearings", title="state utility commission hearings",
        record="a hearing scheduled in a docket before a state utility commission",
        creator="state utility commissions", jurisdiction="Illinois",
        fields=(F("state", "place"), F("docket", "identifier"), F("hearing_type", "text"), F("description", "text"),
                F("scheduled", "date"), F("scheduled_time", "text")),
        search=_search_hearings, read=_read_hearing),
    Series(
        id="pjm_generator_deactivations", title="PJM generator deactivations",
        record="a generating unit's request to deactivate, with its dates and status, as PJM lists it",
        creator=PJM, jurisdiction="the PJM region",
        jurisdictions=lambda series: "the PJM region: " + ", ".join(sorted({r["state"] for r in _deactivations() if r["state"]})),
        coverage=lambda series: _span(_deactivations(), "request_letter_date"),
        fields=(F("unit", "name"), F("state", "place"), F("zone", "identifier"), F("fuel_type", "text"),
                F("capacity_mw", "number"), F("status", DEACTIVATION), F("request_letter_date", "date"),
                F("request_revised_date", "date"), F("requested_deactivation_date", "date"),
                F("projected_deactivation_date", "date"), F("actual_deactivation_date", "date"), F("withdrawn_date", "date"),
                F("reliability_analysis", "text")),
        search=lambda s: page([{k: v for k, v in r.items() if k != "_fields"} for r in _deactivations()], order="latest letter first"),
        read=_read_deactivation),
    Series(
        id="pjm_deactivation_notices", title="PJM deactivation notices",
        record="a letter in PJM's index of deactivation notices: a generator owner's notice, a revision of it, or PJM's response",
        creator="generator owners and PJM Interconnection", publisher=PJM, jurisdiction="the PJM region",
        coverage=lambda series: _span(_notices(), "posted_date"),
        fields=(F("unit", "name"), F("title", "text"), F("posted_date", "date")),
        search=lambda s: page(_notices(), order="latest posted first"), read=_read_notice),
]
