"""Three series from state health agencies' own pages: Medicaid managed-care contracts (Iowa), Medicaid
managed-care procurements (Texas), and health insurers' rate filings (New York and Oregon).

Each page is read whole and its records filtered here. Rate filings are published a year at a time; a search
reads the years it needs, newest first.
"""

import datetime as dt
import functools
import json
import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from ..util import SourceError, session, to_date, to_float
from . import F, Series, page, web

IOWA = "https://hhs.iowa.gov/medicaid/about-medicaid/policies-rules-regulations/contracts-rates"
TEXAS = "https://www.hhs.texas.gov/business/contracting-hhs/procurement-opportunities"
OREGON = "https://www4.cbs.state.or.us/exs/ins/rates2/index.cfm?fuseaction=home.show_table"
NEW_YORK = "https://myportal.dfs.ny.gov/web/prior-approval/"
NY_MARKETS = ("ind-and-sg-medical", "lg-hmo", "medsupp", "ltc")
NY_MARKET_NAMES = {"ind-and-sg-medical": "Individual and small group medical", "lg-hmo": "Large group HMO",
                   "medsupp": "Medicare supplement", "ltc": "Long-term care"}


def _get(url):
    r = session().get(url, timeout=45)
    r.raise_for_status()
    return r


def _text(tag):
    return " ".join(tag.stripped_strings)


@functools.lru_cache(maxsize=4)
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


@functools.lru_cache(maxsize=4)
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


@functools.lru_cache(maxsize=1)
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


@functools.lru_cache(maxsize=4)
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


@functools.lru_cache(maxsize=64)
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


def _ask(fn, *args):
    try:
        return fn(*args)
    except (requests.RequestException, ValueError, KeyError) as e:
        raise SourceError(f"{fn.__name__.strip('_').replace('_', ' ').title()}'s page could not be read: {type(e).__name__}: {str(e)[:150]}")


def _date(value):
    """Oregon writes 'May, 16 2025 00:00:00'; the others write month/day/year."""
    if not value:
        return None
    try:
        return dt.datetime.strptime(str(value), "%B, %d %Y %H:%M:%S").date().isoformat()
    except ValueError:
        d = to_date(value)
        return d.isoformat() if d else None


# ---------- Medicaid managed-care contracts (Iowa) ----------

def _contracts():
    return [{"id": r["record_id"].split(":", 1)[1], "url": r["url"], "state": "Iowa", "plan": r["plan"], "title": r["title"]}
            for r in _ask(_iowa)]


def _read_contract(key):
    record = next((r for r in _contracts() if r["id"] == key), None)
    if not record:
        raise SourceError(f"Iowa no longer lists the contract {key!r}.")
    got = web.read(record["url"])
    return {**record, "document": got["text"], "links": got["data"].get("links") or []}


# ---------- Medicaid managed-care procurements (Texas) ----------

def _procurements():
    out = []
    for r in _ask(_texas):
        posting = (r.get("fields") or [{}])[0]
        out.append({"id": r["record_id"].split(":", 1)[1], "url": r["url"], "state": "Texas", "program": r["title"],
                    "solicitation_id": posting.get("Solicitation ID"), "status": posting.get("Status"),
                    "response_due_date": _date(posting.get("Response Due Date")),
                    "_text": "\n".join([r["procurement_record"]] + [f"{k}: {v}" for k, v in posting.items()]), "_links": r["links"]})
    return out


def _read_procurement(key):
    r = next((r for r in _procurements() if r["id"] == key), None)
    if not r:
        raise SourceError(f"Texas no longer lists the procurement {key!r}.")
    return {**{k: v for k, v in r.items() if not k.startswith("_")}, "document": r["_text"], "links": r["_links"]}


# ---------- health insurers' rate filings (New York, Oregon) ----------

def _percent(value):
    return to_float(str(value).replace("%", "").replace("+", "")) if value not in (None, "") else None


def _filings(year):
    """The rate filings with that effective year in both states."""
    out = []
    for r in _ask(_oregon):
        if str(r["year"]) != str(year):
            continue
        f = r["fields"][0]
        out.append({"id": r["record_id"].split(":", 1)[1], "url": r["url"], "state": "Oregon", "insurer": f.get("COMPANY_NAME"),
                    "serff_number": f.get("FILING_NUMBER_DISP"), "product": f.get("PRODUCT_TYPE"), "status": f.get("STATUS"),
                    "effective_year": int(year), "submission_date": _date(f.get("SUBMISSION_DATE")),
                    "effective_date": _date(f.get("EFFECTIVE_DATE")), "decision_date": _date(f.get("DECISION_DATE")),
                    "members_affected": f.get("AFFECTED_COUNT"), "percent_requested": f.get("PERCENT_CHANGE_REQUESTED"),
                    "percent_approved": f.get("PERCENT_CHANGE_APPROVED"), "_rows": r["fields"], "_links": r["links"]})
    for market in NY_MARKETS:
        if int(year) not in _ask(_ny_index, market):
            continue
        for r in _ask(_ny_rows, market, int(year)):
            f = r["fields"][0]
            pick = lambda *words: next((v for k, v in f.items() if all(w in k for w in words)), None)  # noqa: E731
            out.append({"id": r["record_id"].split(":", 1)[1], "url": r["url"], "state": "New York", "insurer": f.get("Insurer"),
                        "serff_number": r["serff_number"], "product": f.get("Market Segment and Product") or NY_MARKET_NAMES[market],
                        "status": r.get("status"), "effective_year": int(year), "submission_date": _date(f.get("Posting Date")),
                        "effective_date": _date(f.get("Effective Date")), "public_comments_due": _date(f.get("Public Comments Due Date")),
                        "members_affected": to_float(f.get("Number of Members")),
                        "percent_requested": _percent(pick("Requested", "Weighted Average")),
                        "percent_approved": _percent(pick("Approved", "Weighted Average")),
                        "_rows": r["fields"], "_links": r["links"], "_notice": r.get("publisher_notice")})
    return out


def _years():
    years = {int(r["year"]) for r in _ask(_oregon) if str(r["year"]).isdigit()}
    for market in NY_MARKETS:
        years |= set(_ask(_ny_index, market))
    return sorted(years, reverse=True)


def _search_filings(s):
    span = s.peek("effective_year")
    years = [y for y in _years() if (span.get("lo") is None or y >= span["lo"]) and (span.get("hi") is None or y <= span["hi"])]
    at = s.cursor or 0
    if at >= len(years):
        return page([])
    rows = [{k: v for k, v in r.items() if not k.startswith("_")} for r in _filings(years[at])]
    return page(rows, at + 1 if at + 1 < len(years) else None, order="latest effective year first")


def _read_filing(key):
    state = key.split(":", 1)[0]
    years = [int(key.split(":")[2])] if state == "ny" and len(key.split(":")) > 2 and key.split(":")[2].isdigit() else _years()
    for year in years:
        for r in _filings(year):
            if r["id"] == key:
                lines = [r["_notice"]] if r.get("_notice") else []
                for i, row in enumerate(r["_rows"], 1):
                    lines += ([f"Row {i} of {len(r['_rows'])}:"] if len(r["_rows"]) > 1 else []) + [f"{k}: {v}" for k, v in row.items()]
                return {**{k: v for k, v in r.items() if not k.startswith("_")}, "document": "\n".join(lines), "links": r["_links"]}
    raise SourceError(f"The regulator no longer lists the rate filing {key!r}.")


def _span(series):
    years = _years()
    return (str(min(years)), str(max(years))) if years else (None, None)


SERIES = [
    Series(
        id="medicaid_managed_care_contracts", title="Medicaid managed-care contracts",
        record="a contract, or an amendment to one, between a state Medicaid agency and a managed-care plan",
        creator="state Medicaid agencies", jurisdiction="Iowa",
        fields=(F("state", "place"), F("plan", "name"), F("title", "text")),
        search=lambda s: page(_contracts()), read=_read_contract),
    Series(
        id="medicaid_managed_care_procurements", title="Medicaid managed-care procurements",
        record="a state's procurement of managed-care plans for one Medicaid program, with its timeline and solicitation",
        creator="state Medicaid agencies", jurisdiction="Texas",
        fields=(F("state", "place"), F("program", "text"), F("solicitation_id", "identifier"), F("status", "text"),
                F("response_due_date", "date")),
        search=lambda s: page([{k: v for k, v in r.items() if not k.startswith("_")} for r in _procurements()]),
        read=_read_procurement),
    Series(
        id="insurance_rate_filings", title="health insurance rate filings",
        record="a health insurer's rate filing with its state regulator, under its SERFF tracking number",
        creator="state insurance regulators", jurisdiction="New York; Oregon", coverage=_span,
        fields=(F("state", "place"), F("insurer", "name"), F("serff_number", "identifier"), F("product", "text"),
                F("status", ("Approved", "Disapproved", "Withdrawn", "Pending", "Decision published")),
                F("effective_year", "number"), F("submission_date", "date"), F("effective_date", "date"),
                F("decision_date", "date"), F("public_comments_due", "date"), F("members_affected", "number"),
                F("percent_requested", "number"), F("percent_approved", "number")),
        search=_search_filings, read=_read_filing),
]
