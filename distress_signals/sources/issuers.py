"""Four series of what issuers of securities file outside the SEC.

  california_debt_issues        The California Debt and Investment Advisory Commission's DebtWatch: every debt issue a
                                California public agency reported, with the documents filed for it. Its API applies
                                date and amount ranges and finds one word or phrase; the registry applies the rest.
  emma_continuing_disclosures   The MSRB's continuing-disclosure subscription delivery, unpacked in EMMA_DISCLOSURE_DIR:
                                its XML transactions, of which the latest for a submission stands, and the PDFs.
  sedar_filings                 A catalog of SEDAR+ documents from the Canadian Securities Administrators' data
                                distribution service: the CSV at SEDAR_DDS_INDEX, with the PDFs beside it.
  cse_sedar_filings             The SEDAR+ filings the Canadian Securities Exchange republishes for each company it
                                lists, read by listing. The Exchange's terms ask permission for commercial reuse;
                                CSE_SEDAR_LICENSED says the deployment has it.
"""

import csv
import datetime as dt
import functools
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import parse_qsl, quote, urlsplit
from xml.etree import ElementTree as ET

import requests

from .. import names
from ..util import SourceError, http_json, session, stable_id
from . import F, Series, outline, page, web

DEBTWATCH = "https://debtwatch.treasurer.ca.gov"
CDIAC = "California Debt and Investment Advisory Commission"
STEP = 500  # issues asked of DebtWatch at once


# ---------- California debt issues ----------

# field: (DebtWatch's column, whether it is a date)
ISSUE = {"cdiac_number": "CDIACNumber", "issuer": "Issuer", "issuer_type": "IssuerType", "county": "IssuerCounty",
         "issue_name": "IssueName", "project": "ProjectSeriesOrName", "sold_status": "SoldStatus", "debt_type": "DebtType",
         "purpose": "PrimaryPurpose", "source_of_repayment": "SourceofRepayment", "sale_date": "SaleDate",
         "closing_date": "ClosingOrSettlementDate", "first_optional_call_date": "FirstOptionalCallDate",
         "final_maturity_date": "FinalMaturityDate", "principal_amount": "PrincipalAmount", "lead_underwriter": "LeadUnderwriter",
         "purchaser": "PurchaserOrLender"}


def _debtwatch(dataset, body):
    try:
        data = http_json(f"{DEBTWATCH}/api/dataset/{dataset}/search", method="PUT", json_body=body, tries=3, timeout=60)
    except requests.RequestException as e:
        raise SourceError(f"California DebtWatch did not answer ({type(e).__name__})")
    if not isinstance(data, dict) or not isinstance(data.get("records"), list):
        raise SourceError("California DebtWatch did not return a list of records")
    return data


def _issue(row):
    number = row["CDIACNumber"]
    out = {"id": number, "url": f"{DEBTWATCH}/issue-level-detail/issues?cdiacNumber={number}"}
    for field, column in ISSUE.items():
        value = row.get(column)
        out[field] = (value or "")[:10] or None if field.endswith("_date") else None if value in ("(none)", "N/A", "") else value
    return out


def _search_issues(s):
    filters = {}
    numbers = s.any("cdiac_number")
    if numbers:
        filters["CDIACNumber"] = {"type": "StringOneOf", "options": numbers}
    for f in s.series.fields:
        if not f.ranged or not s.peek(f.name):
            continue
        lo, hi = s.between(f.name)
        kind = "Date" if f.type == "date" else "Number"
        filters[ISSUE[f.name]] = ({"type": f"{kind}Between", "minimum": lo, "maximum": hi} if lo is not None and hi is not None
                                  else {"type": "DateOnOrAfter" if kind == "Date" else "NumberGreaterThanOrEqualTo", "minimum": lo} if lo is not None
                                  else {"type": "DateOnOrBefore" if kind == "Date" else "NumberLessThanOrEqualTo", "maximum": hi})
    # DebtWatch finds one word or phrase, anywhere in an issue. A query that is one is the publisher's to apply;
    # of a longer query, or a name asked for, it is given the rarest word, and the registry applies the whole.
    query = s.words()
    phrase = re.fullmatch(r'"([^"]+)"', query)
    if phrase or (query and len(names.tokens(query)) == 1):
        term = phrase[1] if phrase else s.query()
        s.query()
    else:
        asked = names.tokens(query) or [w for f in ("issuer", "lead_underwriter", "purchaser", "issue_name")
                                        for v in s.peek(f).get("is", [])[:1] for w in names.core_tokens(v)]
        term = _rarest(tuple(sorted(set(asked), key=len, reverse=True)[:5]), json.dumps(filters, sort_keys=True)) if asked else None
    at = s.cursor or 0
    data = _debtwatch("issues", {"filters": filters, "pagination": {"pageNumber": at // STEP + 1, "pageSize": STEP},
                                 "sorting": {"columnId": "SaleDate", "ascending": False}, "searchTerm": term})
    rows, total = data["records"], data["totalMatchingRecords"]
    part = rows[at % STEP:at % STEP + s.size]
    return page([_issue(r) for r in part], at + len(part) if at + len(part) < total and part else None, total if s.cursor is None else None,
                "latest sale date first")


@functools.lru_cache(maxsize=256)
def _rarest(words, filters):
    """The word DebtWatch finds in the fewest issues."""
    if len(words) == 1:
        return words[0]
    count = lambda w: _debtwatch("issues", {"filters": json.loads(filters), "pagination": {"pageNumber": 1, "pageSize": 1},  # noqa: E731
                                            "sorting": {"columnId": "CDIACNumber", "ascending": False}, "searchTerm": w})["totalMatchingRecords"]
    with ThreadPoolExecutor(5) as ex:
        return min(zip(ex.map(count, words), words))[1]


@functools.lru_cache(maxsize=1)
def _issue_columns(day):
    return {c["columnId"]: c for c in http_json(f"{DEBTWATCH}/api/dataset/issues", tries=3, timeout=60)["columns"]}


def _read_issue(key):
    if not re.fullmatch(r"\d{4}-\d{4}", key):
        raise SourceError("A california_debt_issues record_id is the CDIAC number a search returned")
    rows = _debtwatch("issues", {"filters": {"CDIACNumber": {"type": "StringOneOf", "options": [key]}},
                                 "pagination": {"pageNumber": 1, "pageSize": 1}, "sorting": {"columnId": "CDIACNumber", "ascending": False},
                                 "searchTerm": None})["records"]
    if not rows or rows[0].get("CDIACNumber") != key:
        raise SourceError(f"California DebtWatch has no issue {key}")
    labels = {i: c["name"] for i, c in _issue_columns(dt.date.today().isoformat()).items()}
    stated = {labels.get(k, k): (v[:10] if isinstance(v, str) and re.fullmatch(r"\d{4}-\d\d-\d\dT00:00:00", v) else v)
              for k, v in rows[0].items() if v not in (None, "", "(none)", "N/A", [])}
    documents, at = [], 1
    while True:
        got = _debtwatch("documents", {"filters": {"CDIACNumber": {"type": "StringOneOf", "options": [key]}},
                                       "pagination": {"pageNumber": at, "pageSize": STEP},
                                       "sorting": {"columnId": "CDIACNumber", "ascending": False}, "searchTerm": None})
        documents += [d for d in got["records"] if d.get("DocumentId")]
        if at * STEP >= got["totalMatchingRecords"] or not got["records"]:
            break
        at += 1
    links = [{"text": f"{d.get('DocumentType')}: {d.get('DocumentFileName')}", "url": f"{DEBTWATCH}/api/document/{d['DocumentId']}/download?inline=true"}
             for d in documents]
    lines = outline(stated) + (["", "Documents filed:"] + [f"- {x['text']}" for x in links] if links else [])
    return {**_issue(rows[0]), "document": "\n".join(lines), "links": links}


def _issues_span(series):
    held = _issue_columns(dt.date.today().isoformat())["SaleDate"]["filter"]["values"]
    return held["minimum"][:10], held["maximum"][:10]


# ---------- EMMA continuing disclosures ----------

def _inside(path, root):
    path = path.resolve()
    if not path.is_relative_to(root):
        raise SourceError("The delivery names a file outside its directory")
    return path


def _pdf_text(path):
    if not path.is_file():
        raise SourceError("The document's file is not in the delivery")
    content = path.read_bytes()[:web.MAX_BYTES + 1]
    if len(content) > web.MAX_BYTES or not content.startswith(b"%PDF-"):
        raise SourceError("The document's file is not a PDF within the size a read takes")
    text, _ = web._pdf(content)
    return text


def _emma():
    """(the disclosure files that stand, {file ID: its PDF}) in the delivery."""
    root = Path(os.environ["EMMA_DISCLOSURE_DIR"]).resolve()
    if not root.is_dir():
        raise SourceError("EMMA_DISCLOSURE_DIR is not a directory")
    indexes, files = [], {}
    for path in root.rglob("*"):
        suffix = path.suffix.casefold()
        if suffix == ".xml":
            stat = _inside(path, root).stat()
            indexes.append((path.resolve(), stat.st_mtime_ns, stat.st_size))
        elif suffix == ".pdf":
            files[path.stem] = _inside(path, root)
    return _emma_index(tuple(sorted(indexes))), files


@functools.lru_cache(maxsize=1)
def _emma_index(indexes):
    latest = {}
    for path, _, _ in indexes:
        try:
            for sub in ET.parse(path).getroot().iter():
                if sub.tag.rsplit("}", 1)[-1] != "ContinuingDisclosureSubmission":
                    continue
                sid = sub.attrib["SubmissionIdentifier"]
                stamp = dt.datetime.fromisoformat(sub.attrib["SubmissionTransactionDateTime"])
                if sid not in latest or stamp > latest[sid][0]:
                    latest[sid] = stamp, sub
        except (OSError, ET.ParseError, KeyError, TypeError, ValueError) as e:
            raise SourceError(f"The MSRB transaction file {path.name} could not be read: {e}")
    rows = []
    for sid, (_, sub) in latest.items():
        # each transaction replaces the submission whole, removals and archived files included
        if sub.get("SubmissionStatus") != "Publish":
            continue
        securities = [x.attrib for x in sub.findall(".//{*}Security")]
        contacts = [c.attrib for c in sub.findall("{*}ContactInformation")]
        issuers = {x["IssuerName"] for x in securities if x.get("IssuerName")}
        issuers |= {c["ContactOrganizationName"] for c in contacts if c.get("ContactType") == "Issuer" and c.get("ContactOrganizationName")}
        obligors = {c["ContactOrganizationName"] for c in contacts if c.get("ContactType") == "Obligor" and c.get("ContactOrganizationName")}
        disclosures = [{**d.attrib, "periods": [p.attrib for p in d.findall("{*}DisclosurePeriod")],
                        "subcategories": [c.get("Name") for c in d.findall("{*}DisclosureSubCategory")]}
                       for d in sub.findall(".//{*}Disclosure")]
        categories = list(dict.fromkeys(v for d in disclosures for k, v in d.items() if k.endswith("Category")))
        for doc in sub.findall("{*}Document"):
            if doc.get("DocumentStatus") != "Current":
                continue
            for file in doc.findall(".//{*}File"):
                if file.get("FileStatus") != "Current":
                    continue
                fid, did = file.attrib["FileIdentifier"], doc.attrib["DocumentIdentifier"]
                rows.append({"id": f"{sid}/{did}/{fid}", "url": f"https://emma.msrb.org/MarketActivity/ContinuingDisclosureDetails/{quote(sid, safe='')}",
                             "submission_id": sid, "issuer": sorted(issuers), "obligor": sorted(obligors), "category": categories,
                             "cusip": sorted({x["CUSIP9"] for x in securities if x.get("CUSIP9")}),
                             "posting_date": (file.get("FilePostingDate") or doc.get("DocumentPostingDate") or "")[:10] or None,
                             "_file": fid, "_stated": {"disclosures": disclosures, "securities": securities,
                                                       "transaction": sub.get("SubmissionTransactionDateTime")}})
    return sorted(rows, key=lambda r: (r["posting_date"] or "", r["id"]), reverse=True)


def _plain(row):
    return {k: v for k, v in row.items() if not k.startswith("_")}


def _search_emma(s):
    return page([_plain(r) for r in _emma()[0]], order="latest posted first")


def _read_emma(key):
    rows, files = _emma()
    row = next((r for r in rows if r["id"] == key), None)
    if not row:
        raise SourceError("This file is not in the latest submission that stands in the delivery")
    lines = outline(row["_stated"])
    if row["_file"] in files:
        lines += ["", _pdf_text(files[row["_file"]])]
    return {**_plain(row), "document": "\n".join(lines), "published": row["posting_date"]}


def _emma_span(series):
    dates = [r["posting_date"] for r in _emma()[0] if r["posting_date"]]
    return (min(dates), max(dates)) if dates else (None, None)


# ---------- SEDAR+ documents from the data distribution service ----------

DDS_COLUMNS = {"document_id", "issuer", "title", "published", "url", "file"}
# what names a public document, a version or a rendering of it in an address; any other parameter may be a credential
PUBLIC_QUERY = {"id", "docid", "documentid", "documentreference", "filingid", "fileid", "attachmentid", "version", "versionid",
                "lang", "language", "locale", "format", "inline", "download"}


def _public_document_url(url):
    link = urlsplit(url)
    if (link.scheme != "https" or not link.hostname or link.username is not None or link.password is not None
            or not link.path.strip("/") or (link.fragment and not re.fullmatch(r"page=\d+", link.fragment))):
        return False
    parameters = parse_qsl(link.query, keep_blank_values=True)
    for key, value in parameters:
        if key.replace("_", "").lower() not in PUBLIC_QUERY or not re.fullmatch(r"[\w .~:/+-]*={0,2}", value):
            return False
    if link.hostname.removeprefix("www.") in {"sedarplus.ca", "sedarplus.com"}:
        return "/records/document" in link.path and any(k.lower() in {"id", "documentid"} and v for k, v in parameters)
    return True


def _dds():
    """{document ID: (its fields, its PDF)} from the catalog."""
    path = Path(os.environ["SEDAR_DDS_INDEX"]).resolve()
    rows = {}
    try:
        with path.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            if not DDS_COLUMNS.issubset(reader.fieldnames or []):
                raise SourceError("The SEDAR+ catalog has the columns " + ", ".join(sorted(DDS_COLUMNS)))
            for line in reader:
                if any(not (line.get(k) or "").strip() for k in DDS_COLUMNS):
                    raise SourceError(f"The SEDAR+ catalog's line {reader.line_num} is missing a field")
                if line["document_id"] in rows:
                    raise SourceError(f"The SEDAR+ catalog's line {reader.line_num} repeats a document")
                if not _public_document_url(line["url"]):
                    raise SourceError(f"The SEDAR+ catalog's line {reader.line_num} does not give a public document URL")
                rows[line["document_id"]] = ({"id": line["document_id"], "url": line["url"], "document_id": line["document_id"],
                                              "issuer": line["issuer"], "title": line["title"],
                                              "filing_date": dt.date.fromisoformat(line["published"][:10]).isoformat()},
                                             _inside(path.parent / line["file"], path.parent))
    except (OSError, csv.Error, UnicodeError, ValueError) as e:
        raise SourceError(f"The SEDAR+ catalog could not be read: {e}")
    return rows


def _search_dds(s):
    return page(sorted((r for r, _ in _dds().values()), key=lambda r: (r["filing_date"], r["id"]), reverse=True), order="latest filed first")


def _read_dds(key):
    entry = _dds().get(key)
    if not entry:
        raise SourceError("This document is not in the SEDAR+ catalog")
    return {**entry[0], "document": _pdf_text(entry[1]), "published": entry[0]["filing_date"]}


def _dds_span(series):
    dates = [r["filing_date"] for r, _ in _dds().values()]
    return (min(dates), max(dates)) if dates else (None, None)


# ---------- SEDAR+ filings the Canadian Securities Exchange republishes ----------

_cse = {}


def _cse_filings(listing):
    """A listed company's SEDAR+ filings, latest first, kept for a quarter of an hour."""
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", listing):
        raise SourceError("listing is the company's name in its address on thecse.com: thecse.com/listings/<listing>/")
    hit = _cse.get(listing)
    if hit and time.time() - hit[0] < 900:
        return hit[1]
    index = f"https://thecse.com/listings/{listing}/sedar-filings/"
    try:
        response = session().get(index, timeout=45)
        response.raise_for_status()
        data = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', response.text, re.S)
        company = json.loads(data[1])["props"]["pageProps"].get("staticCompanyData") if data else None
        if not company:
            raise SourceError(f"The Exchange lists no company at thecse.com/listings/{listing}/")
        meta = company["metadata"]
        feed = meta.get("sedar_filings")
        if not feed:
            return []
        if not re.fullmatch(r"https://webapi(?:-backup)?\.thecse\.com/trading/listed/sedar_filings/\d+\.json", feed):
            raise SourceError(f"The Exchange's page for {listing} names a filing list this reader does not know")
        response = session().get(feed, timeout=45)
        response.raise_for_status()
        rows = []
        for entry in response.json()["list"]:
            if entry.get("status") != "available":
                continue
            key = stable_id(entry.get("accession_number") or urlsplit(entry["url"]).path)
            rows.append({"id": f"{listing}/{key}", "url": entry["url"], "listing": listing, "issuer": meta.get("security_name"),
                         "title": entry.get("document_description"), "category": entry.get("document_category"),
                         "filing": entry.get("filing_description"), "accession_number": entry.get("accession_number"),
                         "public_date": (entry.get("public_date") or "")[:10] or None})
    except (requests.RequestException, ValueError, KeyError, TypeError) as e:
        raise SourceError(f"The Exchange's filings for {listing} could not be read ({type(e).__name__})")
    rows.sort(key=lambda r: (r["public_date"] or "", r["id"]), reverse=True)
    _cse[listing] = (time.time(), rows)
    return rows


def _search_cse(s):
    return page([r for listing in s.any("listing") for r in _cse_filings(listing)], order="latest filed first")


def _read_cse(key):
    listing, _, _ = key.partition("/")
    row = next((r for r in _cse_filings(listing) if r["id"] == key), None)
    if not row:
        raise SourceError("The Exchange no longer lists this filing")
    return {**row, "document": web.read(row["url"])["text"], "published": row["public_date"]}


SERIES = [
    Series(
        id="california_debt_issues", title="California public debt issues",
        record="a debt issue a California state or local public agency reported to the California Debt and Investment Advisory "
               "Commission, proposed or sold, with the official statement and other documents filed for it",
        creator="California public agencies that issue debt", publisher=CDIAC, jurisdiction="California", coverage=_issues_span,
        fields=(F("cdiac_number", "identifier"), F("issuer", "name"), F("issuer_type", "text"), F("county", "place"),
                F("issue_name", "text"), F("project", "text"), F("sold_status", "text"), F("debt_type", "text"), F("purpose", "text"),
                F("source_of_repayment", "text"), F("sale_date", "date"), F("closing_date", "date"),
                F("first_optional_call_date", "date"), F("final_maturity_date", "date"), F("principal_amount", "amount"),
                F("lead_underwriter", "name"), F("purchaser", "name")),
        search=_search_issues, read=_read_issue),
    Series(
        id="emma_continuing_disclosures", title="EMMA continuing disclosures",
        record="a file of a continuing disclosure an issuer or obligated person submitted to the MSRB's EMMA: annual financial "
               "information, audited statements, or notice of an event affecting the bonds",
        creator="issuers of municipal securities and the obligated persons behind them", publisher="Municipal Securities Rulemaking Board",
        jurisdiction="U.S. states and local governments", coverage=_emma_span, requires=("EMMA_DISCLOSURE_DIR",),
        fields=(F("submission_id", "identifier"), F("issuer", "name", many=True), F("obligor", "name", many=True),
                F("category", "text", many=True), F("cusip", "identifier", many=True), F("posting_date", "date")),
        search=_search_emma, read=_read_emma),
    Series(
        id="sedar_filings", title="SEDAR+ filings",
        record="a document an issuer filed with Canadian securities regulators on SEDAR+",
        creator="issuers of securities in Canada", publisher="Canadian Securities Administrators", jurisdiction="Canada",
        coverage=_dds_span, requires=("SEDAR_DDS_INDEX",),
        fields=(F("document_id", "identifier"), F("issuer", "name"), F("title", "text"), F("filing_date", "date")),
        search=_search_dds, read=_read_dds),
    Series(
        id="cse_sedar_filings", title="SEDAR+ filings of CSE-listed companies",
        record="a document a company listed on the Canadian Securities Exchange filed on SEDAR+, as the Exchange republishes it",
        creator="companies listed on the Canadian Securities Exchange", publisher="Canadian Securities Exchange", jurisdiction="Canada",
        requires=("CSE_SEDAR_LICENSED",),
        fields=(F("listing", "identifier", required=True), F("issuer", "name"), F("title", "text"), F("category", "text"),
                F("filing", "text"), F("accession_number", "identifier"), F("public_date", "date")),
        search=_search_cse, read=_read_cse),
]
