"""FERC filings, state utility proceedings and PJM unit deactivations."""
import datetime as dt
import io
import json
import re
import xml.etree.ElementTree as ET
from urllib.parse import quote, urljoin
from zipfile import ZipFile

from bs4 import BeautifulSoup

from ..util import SourceError, http_json, session, today
from . import web

FERC_API = "https://elibrary.ferc.gov/eLibrarywebapi/api"
FERC = "https://elibrary.ferc.gov/eLibrary"
PJM_XML = "https://www.pjm.com/pjmfiles/media/planning/gen-deactivation-data/GenDeactivationUnits.xml"
PJM_NOTICES = "https://www.pjm.com/planning/service-requests/gen-deactivations/generator-deactivation-notices"
SD = "https://puc.sd.gov/dockets"
IL = "https://icc.illinois.gov/meetings"

SOURCES = {
    "ferc_elibrary": "FERC filings and orders. Search full text or use docket, since/until (posted dates), "
                     "page and limit. Read the accession and its individual public PDF, Word (.docx) and text attachments.",
    "utility_dockets": "State commission dockets. state=SD (default): electric and gas cases by year "
                       "(default current); read the docket for deadlines and orders. state=IL: hearings in the "
                       "seven-day commission calendar from week_start (default today), searchable by case or party.",
    "pjm_deactivations": "PJM generating-unit deactivation requests and outcomes. Search unit, state, zone or "
                         "fuel; filter by status (PJM's exact label). Set records=notices for owner letters, "
                         "PJM responses and revisions. Transmission owner zone identifies the grid zone.",
}
FILTERS = {
    "ferc_elibrary": {"docket", "since", "until", "page", "limit"},
    "utility_dockets": {"state", "year", "sector", "week_start", "page", "limit"},
    "pjm_deactivations": {"status", "records", "page", "limit"},
}


def _text(node):
    return " ".join(node.stripped_strings)


def _matches(query, *values):
    haystack = " ".join(str(v) for v in values).casefold()
    return all(token in haystack for token in query.casefold().split())


def _get(url, **params):
    response = session().get(url, params=params or None, timeout=45)
    response.raise_for_status()
    return response


def _page(rows, page, limit):
    start = (page - 1) * limit
    complete = start + limit >= len(rows)
    return {"results": rows[start:start + limit], "total": len(rows), "complete": complete,
            "next_page": None if complete else page + 1}


def _ferc_search(query, page=1, limit=30, docket=None, since=None, until=None, accession=None):
    dates = []
    if since or until:
        start = dt.date.fromisoformat(since) if since else dt.date(1900, 1, 1)
        end = dt.date.fromisoformat(until) if until else today()
        if start > end:
            raise SourceError("since must be on or before until")
        dates = [{"dateType": "posted_date", "startDate": start.strftime("%m/%d/%Y"),
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


def _ferc_record(hit):
    accession = hit.get("acesssionNumber") or hit.get("accessionNumber")
    if not accession:
        raise SourceError("FERC returned a filing without its accession number")
    prefix = "ferc_elibrary:" + accession
    files = []
    for attachment in hit.get("transmittals") or []:
        fid = attachment.get("fileId")
        if not fid:
            continue
        file = {**attachment, "url": f"{FERC}/filedownload?fileid={quote(fid)}"}
        if str(file.get("fileType")).upper() in {"PDF", "DOCX", "TXT", "TEXT"}:
            file["record_id"] = prefix + "/" + fid
        files.append(file)
    return {"record_id": prefix, "url": f"{FERC}/filelist?accession_number={accession}",
            "title": hit.get("description") or accession, "accession": accession, "dockets": hit.get("docketNumbers"),
            "posted": hit.get("postedDate"), "filed": hit.get("filedDate"), "issued": hit.get("issuedDate"),
            "affiliations": hit.get("affiliations"), "category": hit.get("category"), "class_types": hit.get("classTypes"),
            "files": files}


def _docx_text(content):
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    with ZipFile(io.BytesIO(content)) as package:
        document = package.getinfo("word/document.xml")
        if document.file_size > web.MAX_BYTES:
            raise SourceError("The Word document exceeds the extraction limit")
        root = ET.fromstring(package.read(document))
    return "\n".join("".join(node.text or "" for node in p.findall(".//w:t", ns))
                     for p in root.findall(".//w:p", ns))


def _ferc_read(native):
    accession, sep, fid = native.partition("/")
    if not re.fullmatch(r"\d{8}-\d{4}", accession):
        raise SourceError("Use the accession number returned by ferc_elibrary")
    hits = _ferc_search("", accession=accession)["searchHits"]
    rows = [_ferc_record(hit) for hit in hits]
    row = next((r for r in rows if r["accession"] == accession), None)
    if row is None:
        raise SourceError(f"No public FERC filing for {accession}")
    text = json.dumps(row, ensure_ascii=False, indent=2)
    url = row["url"]
    if sep:
        file = next((f for f in row["files"] if f.get("record_id") == "ferc_elibrary:" + native), None)
        if file is None:
            raise SourceError("The requested readable attachment is not listed on this accession")
        with session().post(f"{FERC_API}/File/DownloadP8File", json={"fileidLst": [fid]},
                            timeout=60, stream=True) as response:
            response.raise_for_status()
            content = response.raw.read(web.MAX_BYTES + 1, decode_content=True)
        if len(content) > web.MAX_BYTES:
            raise SourceError(f"Attachment exceeds {web.MAX_BYTES} bytes; open {file['url']}")
        if content.startswith(b"%PDF-"):
            text = web._pdf(content)[0]
            if not re.sub(r"(?m)^\[page \d+\]$", "", text).strip():
                raise SourceError(f"The PDF contains no extractable text. Read the original at {file['url']}")
        elif file["fileType"].upper() == "DOCX":
            text = _docx_text(content)
        elif file["fileType"].upper() in {"TXT", "TEXT"}:
            text = content.decode("utf-8-sig", errors="replace")
        else:
            raise SourceError("FERC did not return the requested PDF")
        url = file["url"]
        row = {**file, "title": file["fileName"], "accession": accession, "posted": row["posted"]}
    return {"kind": "filing", "source": "ferc_elibrary", "native_id": native, "url": url,
            "published": _iso_us(row["posted"]), "text": text, "data": row}


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


def search(source, query, *, page=1, limit=30, **filters):
    page, limit = int(page), int(limit)
    if page < 1 or not 1 <= limit <= 100:
        raise SourceError("page starts at 1; limit must be 1 to 100")
    if source == "ferc_elibrary":
        data = _ferc_search(query, page, limit, **filters)
        total = int(data["totalHits"])
        complete = page * limit >= total
        return {"results": [_ferc_record(x) for x in data["searchHits"]], "total": total,
                "complete": complete, "next_page": None if complete else page + 1}
    if source == "utility_dockets":
        state = str(filters.pop("state", "SD")).upper()
        if state == "IL" and not (set(filters) - {"week_start"}):
            return {**_page(_illinois(query, **filters), page, limit), "scope": "Illinois seven-day hearing calendar"}
        if state == "SD" and "week_start" not in filters:
            return {**_page(_sd(query, **filters), page, limit),
                    "scope": f"South Dakota {filters.get('sector') or 'electric and gas'} dockets, "
                             f"{filters.get('year') or today().year}"}
        raise SourceError("Use state=SD with year and sector, or state=IL with week_start")
    records = filters.get("records", "units")
    status = filters.get("status")
    if records == "notices" and not status:
        return _page(_pjm_notices(query), page, limit)
    if records != "units":
        raise SourceError("records is units or notices; status applies to units")
    rows = []
    for unit in _units().values():
        entries = [e for e in unit["entries"] if (not status or (e["Status"] or "").casefold() == status.casefold())
                   and _matches(query, e["UnitName"], e["State"], e["TransmissionOwnerZone"], e["FuelType"])]
        if entries:
            rows.append({**unit, "entries": entries})
    return _page(rows, page, limit)


def read(source, native_id):
    if source == "ferc_elibrary":
        return _ferc_read(native_id)
    if source == "pjm_deactivations":
        row = _units().get(native_id)
        if not row:
            raise SourceError("The PJM export no longer contains this state/unit pair")
        data = {**row, "links": [{"text": "Official notices and PJM responses", "url": PJM_NOTICES}]}
        return {"kind": "generator_deactivation", "source": source, "native_id": native_id, "url": PJM_XML,
                "published": None, "data": data, "text": json.dumps(row["entries"], ensure_ascii=False, indent=2)}
    raise SourceError("Read the commission's docket or hearing URL returned by search")
