"""Public state Medicaid contract records and regulator-published insurance rate filings."""

import json
import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from ..util import SourceError, session
from . import web

IOWA = "https://hhs.iowa.gov/medicaid/about-medicaid/policies-rules-regulations/contracts-rates"
TEXAS = "https://www.hhs.texas.gov/business/contracting-hhs/procurement-opportunities"
OREGON = "https://www4.cbs.state.or.us/exs/ins/rates2/index.cfm?fuseaction=home.show_table"
NEW_YORK = "https://myportal.dfs.ny.gov/web/prior-approval/"
NY_MARKETS = ("ind-and-sg-medical", "lg-hmo", "medsupp", "ltc")
FILTERS = {"medicaid_contracts": {"state", "offset", "max_results"}, "serff": {"state", "year", "status", "offset", "max_results"}}


def _get(url):
    r = session().get(url, timeout=45)
    r.raise_for_status()
    return r


def _text(tag):
    return " ".join(tag.stripped_strings)


def _iowa():
    soup = BeautifulSoup(_get(IOWA).content, "html.parser")
    heading = soup.select_one("#managed-care-plan-contracts-amp-amendments")
    if not heading:
        raise SourceError("Iowa changed the Medicaid contracts directory.")
    rows = []
    for block in heading.find_next_siblings():
        if block.name == "h2":
            break
        for panel in block.select(".accordion__items-panel"):
            plan = _text(panel.find_previous("h3"))
            for a in panel.select('a[href][data-entity-file-mimetype="application/pdf"]'):
                url = urljoin(IOWA, a["href"])
                matched = re.fullmatch(r"https://hhs\.iowa\.gov/media/(\d+)/download(?:\?.*)?", url)
                if not matched:
                    raise SourceError(f"Unrecognized Iowa contract link: {url}")
                title = " ".join(a.find_all(string=True, recursive=False)).strip()
                rows.append({"record_id": f"medicaid_contracts:ia:{matched[1]}", "state": "IA", "title": title,
                             "plan": plan, "url": url, "directory": IOWA})
    if not rows:
        raise SourceError("Iowa returned a contracts directory without any contract records.")
    return rows


def _texas():
    soup = BeautifulSoup(_get(TEXAS).content, "html.parser")
    heading = next((h for h in soup.select("h2,h3") if _text(h) == "Medicaid Managed Care Procurement Timelines"), None)
    rows = []
    if heading:
        for section in heading.find_next_siblings():
            if section.name in {"h2", "h3"}:
                break
            if section.name != "details":
                continue
            program = _text(section.summary)
            body = section.select_one(".details-wrapper")
            text = "\n".join(_text(el) for el in body.select("h3,p,li"))
            links = [{"text": _text(a), "url": urljoin(TEXAS, a["href"])} for a in body.select("a[href]")]
            row = {"record_id": f"medicaid_contracts:tx:{program}", "title": program, "state": "TX",
                   "url": TEXAS, "procurement_record": text, "links": links}
            for a in links:
                loc = urlparse(a["url"])
                if loc.hostname in {"www.txsmartbuy.gov", "www.txsmartbuy.com"} and loc.path.startswith("/esbd/"):
                    posting = BeautifulSoup(_get(a["url"]).content, "html.parser")
                    fields = {_text(cell.strong).strip().rstrip(":"): _text(cell.p)
                              for cell in posting.select(".esbd-result-cell") if cell.strong and cell.p}
                    if not fields:
                        raise SourceError(f"Texas returned no solicitation details at {a['url']}.")
                    row.update({"url": a["url"], "directory": TEXAS, "fields": [fields]})
            rows.append(row)
    if not rows:
        raise SourceError("Texas returned no managed-care procurement records in its timetable.")
    return rows


def _oregon(year=None):
    # This is the JSON that Oregon publishes to render its public rate-request table.
    html = _get(OREGON).text
    marker = re.search(r"\bvar\s+dataSet_raw\s*=\s*", html)
    if not marker:
        raise SourceError("Oregon's rate-request table has changed.")
    data, _ = json.JSONDecoder().raw_decode(html[marker.end():])
    records = {}
    for values in data["DATA"]:
        row = dict(zip(data["COLUMNS"], values, strict=True))
        if year and str(row["EFFECTIVE_YEAR"]) != str(year):
            continue
        links = []
        for key, name in (("SUB", "Rate Request"), ("APP", "Approved Document"), ("CORR", "Filing Correspondence"),
                          ("TBLFACT", "Rate Tables and Factors"), ("PRELIMDEC", "Preliminary Decision")):
            if row.get("TK_UR_DOCUMENT_DESC_" + key) and row.get("URL_UR_DOCUMENT_DESC_" + key):
                links.append({"text": name, "url": urljoin(OREGON, row["URL_UR_DOCUMENT_DESC_" + key])})
        fields = {k: row[k] for k in ("COMPANY_NAME", "FILING_NUMBER_DISP", "PRODUCT_TYPE", "SUBMISSION_DATE",
                  "EFFECTIVE_DATE", "EFFECTIVE_YEAR", "STATUS", "DECISION_DATE", "PERCENT_CHANGE_REQUESTED",
                  "PERCENT_CHANGE_APPROVED", "AFFECTED_COUNT") if row[k] is not None}
        key = row["RATE_KEY"]
        record = records.setdefault(key, {"record_id": f"serff:or:{key}", "title": row["COMPANY_NAME"], "state": "OR",
                                    "serff_number": row["FILING_NUMBER_DISP"], "status": row["STATUS"],
                                    "year": row["EFFECTIVE_YEAR"], "fields": [], "directory": OREGON,
                                    "url": links[0]["url"] if links else OREGON, "links": []})
        if fields not in record["fields"]:
            record["fields"].append(fields)
        record["links"].extend(a for a in links if a not in record["links"])
    return sorted(records.values(), key=lambda r: str(r["year"]), reverse=True)


def _ny_index(market):
    url = NEW_YORK + market
    soup = BeautifulSoup(_get(url).content, "html.parser")
    years = {}
    for tr in soup.select("table tr"):
        cells = tr.find_all("td", recursive=False)
        if not cells or not re.fullmatch(r"\d{4}", _text(cells[0])):
            continue
        label = "summaries" if market == "ltc" else "additional information"
        for a in tr.select("a[href]"):
            if _text(a).casefold() == label:
                years[int(_text(cells[0]))] = urljoin(url, a["href"])
    if not years:
        raise SourceError(f"NY DFS listed no filing tables for {market}.")
    return years


def _ny_rows(market, year=None):
    years = _ny_index(market)
    year = year or max(years)
    if year not in years:
        return []
    url = years[year]
    soup = BeautifulSoup(_get(url).content, "html.parser")
    # DFS labels a proposal-only table explicitly. A missing decision cell alone cannot establish pending status.
    notices = [_text(p) for p in soup.select("p") if "have not been approved by dfs" in _text(p).casefold()]
    filings = {}
    tables_found = 0
    for table in soup.select("table"):
        trs = table.select("tr")
        if not trs:
            continue
        headers = []
        subheads = iter(_text(c) for c in trs[1].select("th")) if len(trs) > 1 else iter(())
        for cell in trs[0].find_all(["td", "th"], recursive=False):
            n, label = int(cell.get("colspan", 1)), _text(cell)
            headers.extend([label] if n == 1 else [f"{label} - {next(subheads, i + 1)}" for i in range(n)])
        if "SERFF Number" not in headers:
            continue
        tables_found += 1
        for tr in trs[1:]:
            cells = tr.find_all("td", recursive=False)
            if not cells:
                continue
            if len(cells) != len(headers):
                if any(re.search(r"[A-Z]{4}-\d{9}", _text(cell)) for cell in cells):
                    raise SourceError(f"NY DFS filing row has unexpected columns at {url}")
                continue
            fields = {key: _text(cell) for key, cell in zip(headers, cells) if _text(cell)}
            tracking = fields.get("SERFF Number")
            if not tracking:
                continue
            filer = fields["Insurer"]
            links = [{"text": _text(a), "url": urljoin(url, a["href"])} for a in tr.select("a[href]")]
            application = next((a["url"] for a in links if a["text"].casefold() in ("rate application", "application summary")), url)
            filing = filings.setdefault(tracking, {"record_id": f"serff:ny:{market}:{year}:{tracking}", "title": filer,
                                        "state": "NY", "year": year, "market": market, "serff_number": tracking,
                                        "fields": [], "url": application, "directory": url, "links": []})
            # One tracking number can have multiple products or effective-date rows.
            filing["fields"].append(fields)
            filing["links"].extend(a for a in links if a not in filing["links"])
            approved = any("approved" in k.casefold() and "%" in v for k, v in fields.items())
            if approved:
                filing["status"] = "Approved"
            elif notices:
                filing["publisher_notice"] = notices[0]
                filing.setdefault("status", "Pending")
            elif any("decision summary" in a["text"].casefold() for a in links):
                filing.setdefault("status", "Decision published")
    if not tables_found:
        raise SourceError(f"NY DFS returned no SERFF filing table at {url}")
    return list(filings.values())


def _load(source, state, year):
    tasks = []
    if source == "medicaid_contracts":
        tasks = [("IA", _iowa, ()), ("TX", _texas, ())]
    elif source == "serff":
        tasks = [("OR", _oregon, (year,))] + [(f"NY {market}", _ny_rows, (market, year)) for market in NY_MARKETS]
    else:
        raise SourceError(f"Unknown state health source {source!r}")
    if state:
        tasks = [task for task in tasks if task[0].split()[0] == str(state).upper()]
        if not tasks:
            raise SourceError(f"{source} supports " + ("IA and TX" if source == "medicaid_contracts" else "NY and OR"))
    rows, errors = [], {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [(name, pool.submit(fn, *args)) for name, fn, args in tasks]
        for name, future in futures:
            try:
                rows.extend(future.result())
            except (requests.RequestException, SourceError, ValueError, KeyError) as exc:
                errors[name] = str(exc)
    if len(errors) == len(tasks):
        raise SourceError(f"{source}: {errors}")
    return rows, errors


def search(source, query="", state=None, year=None, status=None, offset=0, max_results=25):
    if not str(offset).isdigit() or not str(max_results).isdigit() or not 1 <= int(max_results) <= 200:
        raise SourceError("Give a nonnegative offset and max_results from 1 to 200.")
    if year is not None and not re.fullmatch(r"\d{4}", str(year)):
        raise SourceError("Give a four-digit year.")
    rows, errors = _load(source, state, int(year) if year else None)
    query = query.strip().casefold()
    found = []
    for row in rows:
        if status and row.get("status", "").casefold() != str(status).casefold():
            continue
        terms = [row.get(k, "") for k in ("record_id", "title", "state", "plan", "serff_number", "status", "procurement_record")]
        for fields in row.get("fields", []):
            terms.extend(str(value) for value in fields.values())
        if any(query in term.casefold() for term in terms):
            found.append(row)
    offset, end = int(offset), int(offset) + int(max_results)
    return {"results": found[offset:end], "total_matches_in_retrieved_records": len(found),
            "complete": not errors and end >= len(found), **({"next_offset": end} if end < len(found) else {}),
            **({"source_errors": errors} if errors else {})}


def read(source, native_id):
    state, _, ident = native_id.partition(":")
    try:
        if source == "medicaid_contracts" and state == "ia" and ident.isdigit():
            obs = web.read(f"https://hhs.iowa.gov/media/{ident}/download?inline")
            if obs["data"].get("content_type") != "application/pdf":
                raise SourceError(f"Iowa media record {ident} no longer returns a contract PDF.")
            return {**obs, "kind": "public_record", "source": source, "native_id": native_id}
        if source == "medicaid_contracts" and state == "tx":
            rows = _texas()
        elif source == "serff" and state == "or" and ident.isdigit():
            rows = _oregon()
        elif source == "serff" and state == "ny":
            market, year, _tracking = ident.split(":", 2)
            if market not in NY_MARKETS or not re.fullmatch(r"\d{4}", year):
                raise SourceError("Use a NY SERFF record ID from search_records.")
            rows = _ny_rows(market, int(year))
        else:
            raise SourceError(f"Unknown record {source}:{native_id}")
        row = next((r for r in rows if r["record_id"] == f"{source}:{native_id}"), None)
        if not row:
            raise SourceError(f"The publisher no longer lists {source}:{native_id}.")
        parts = [row["title"]]
        if notes := row.get("publisher_notice"):
            parts.append(notes)
        if procurement := row.get("procurement_record"):
            parts.append(procurement)
        fields = row.get("fields", [])
        for item in ([fields] if isinstance(fields, dict) else fields):
            parts.extend(f"{key}: {value}" for key, value in item.items())
        parts.extend(f"{a['text']}: {a['url']}" for a in row.get("links", []))
        return {"kind": "public_record", "source": source, "native_id": native_id, "url": row["url"],
                "published": None, "text": "\n".join(parts), "data": row}
    except (requests.RequestException, ValueError, KeyError) as exc:
        raise SourceError(f"Could not read {source}:{native_id}: {exc}") from exc
