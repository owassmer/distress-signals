"""Four series of what issuers of securities file outside the SEC and EMMA.

  california_debt_issues,       The California Debt and Investment Advisory Commission's DebtWatch: every debt issue a
  california_debt_documents     California public agency reported, and the documents filed for each (official
                                statements, indentures, resolutions and others). Its API applies identifiers, the values
                                a column lists, and the ranges a column accepts, all read from its own column metadata,
                                and finds one word or phrase; the registry applies the rest.
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

import requests

from .. import names
from ..util import SourceError, http_json, session, stable_id
from . import F, Series, outline, page, web

DEBTWATCH = "https://debtwatch.treasurer.ca.gov"
CDIAC = "California Debt and Investment Advisory Commission"
STEP = 500  # records asked of DebtWatch at once


# ---------- California debt issues ----------

ISSUE = {"cdiac_number": "CDIACNumber", "issuer": "Issuer", "issuer_type": "IssuerType", "county": "IssuerCounty",
         "issue_name": "IssueName", "project": "ProjectSeriesOrName", "sold_status": "SoldStatus", "debt_type": "DebtType",
         "purpose": "PrimaryPurpose", "source_of_repayment": "SourceofRepayment", "sale_date": "SaleDate",
         "closing_date": "ClosingOrSettlementDate", "first_optional_call_date": "FirstOptionalCallDate",
         "final_maturity_date": "FinalMaturityDate", "principal_amount": "PrincipalAmount", "lead_underwriter": "LeadUnderwriter",
         "purchaser": "PurchaserOrLender"}
DOCUMENT = {"cdiac_number": "CDIACNumber", "issuer": "Issuer", "document_type": "DocumentType", "project": "ProjectSeriesOrName",
            "sale_date": "SaleDate", "principal_amount": "PrincipalAmount", "file_name": "DocumentFileName"}
NAMED = {"issues": ("issuer", "lead_underwriter", "purchaser", "issue_name"), "documents": ("issuer", "file_name")}


def _debtwatch(dataset, body):
    try:
        data = http_json(f"{DEBTWATCH}/api/dataset/{dataset}/search", method="PUT", json_body=body, tries=3, timeout=60)
    except requests.RequestException as e:
        raise SourceError(f"California DebtWatch did not answer ({type(e).__name__})")
    if not isinstance(data, dict) or not isinstance(data.get("records"), list):
        raise SourceError("California DebtWatch did not return a list of records")
    return data


@functools.lru_cache(maxsize=4)
def _columns(dataset, day):
    """{column: its metadata}: its type, the filters it accepts, its values where it lists them, its range."""
    return {c["columnId"]: c for c in http_json(f"{DEBTWATCH}/api/dataset/{dataset}", tries=3, timeout=60)["columns"]}


def _meta(dataset):
    return _columns(dataset, dt.date.today().isoformat())


def _filters(s, dataset, columns):
    """DebtWatch's filters for a search: each field whose column accepts what was given."""
    meta, out = _meta(dataset), {}
    for f in s.series.fields:
        column, given = columns.get(f.name), s.peek(f.name)
        if not column or not given:
            continue
        accepts = set(meta.get(column, {}).get("availableFilters") or [])
        if f.ranged:
            kind = "Date" if f.type == "date" else "Number"
            lo, hi = given.get("lo"), given.get("hi")
            if lo is not None and hi is not None and f"{kind}Between" in accepts:
                out[column] = {"type": f"{kind}Between", "minimum": lo, "maximum": hi}
            elif lo is not None and hi is None and {"Date": "DateOnOrAfter", "Number": "NumberGreaterThanOrEqualTo"}[kind] in accepts:
                out[column] = {"type": {"Date": "DateOnOrAfter", "Number": "NumberGreaterThanOrEqualTo"}[kind], "minimum": lo}
            elif hi is not None and lo is None and {"Date": "DateOnOrBefore", "Number": "NumberLessThanOrEqualTo"}[kind] in accepts:
                out[column] = {"type": {"Date": "DateOnOrBefore", "Number": "NumberLessThanOrEqualTo"}[kind], "maximum": hi}
            else:
                continue
            s.between(f.name)
        elif "StringOneOf" in accepts:
            listed = {v["id"].casefold(): v["id"] for v in (((meta[column].get("filter") or {}).get("values") or {}).get("all") or [])}
            values = given.get("is") or []
            if f.type == "identifier":
                out[column] = {"type": "StringOneOf", "options": s.any(f.name)}
            elif listed and all(v.casefold() in listed for v in values):
                s.any(f.name)
                out[column] = {"type": "StringOneOf", "options": [listed[v.casefold()] for v in values]}
    return out


def _term(s, dataset, filters):
    """DebtWatch finds one word or phrase, anywhere in a record. A query that is one is the publisher's to apply; of a
    longer query, or a name asked for, it is given the rarest word, and the registry applies the whole."""
    query = s.words()
    phrase = re.fullmatch(r'"([^"]+)"', query)
    if phrase or (query and len(names.tokens(query)) == 1):
        s.query()
        return phrase[1] if phrase else query
    asked = names.tokens(query) or [w for f in NAMED[dataset] for v in s.peek(f).get("is", [])[:1] for w in names.core_tokens(v)]
    asked = [w for w in asked if len(w) >= 3 and any(ch.isalpha() for ch in w)]  # what DebtWatch indexes as a word
    return _rarest(dataset, tuple(sorted(set(asked), key=len, reverse=True)[:5]), json.dumps(filters, sort_keys=True)) if asked else None


@functools.lru_cache(maxsize=256)
def _rarest(dataset, words, filters):
    """The word DebtWatch finds in the fewest records."""
    if len(words) == 1:
        return words[0]
    count = lambda w: _debtwatch(dataset, {"filters": json.loads(filters), "pagination": {"pageNumber": 1, "pageSize": 1},  # noqa: E731
                                           "sorting": {"columnId": "CDIACNumber", "ascending": False}, "searchTerm": w})["totalMatchingRecords"]
    with ThreadPoolExecutor(5) as ex:
        return min(zip(ex.map(count, words), words))[1]


def _value(field, value):
    if field.endswith("_date"):
        return (value or "")[:10] or None
    return None if value in ("(none)", "N/A", "") else value


def _issue(row):
    number = row["CDIACNumber"]
    return {"id": number, "url": _issue_page(number), **{field: _value(field, row.get(column)) for field, column in ISSUE.items()}}


def _issue_page(number):
    return f"{DEBTWATCH}/issue-level-detail/issues?cdiacNumber={number}"


def _document(row):
    number, doc = row["CDIACNumber"], row["DocumentId"]
    return {"id": f"{number}/{doc}", "url": _issue_page(number), "document_id": str(doc),
            **{field: _value(field, row.get(column)) for field, column in DOCUMENT.items()}}


def _search(dataset, columns, make):
    def search(s):
        filters = _filters(s, dataset, columns)
        term = _term(s, dataset, filters)
        at = s.cursor or 0
        data = _debtwatch(dataset, {"filters": filters, "pagination": {"pageNumber": at // STEP + 1, "pageSize": STEP},
                                    "sorting": {"columnId": "SaleDate", "ascending": False}, "searchTerm": term})
        rows, total = data["records"], data["totalMatchingRecords"]
        part = rows[at % STEP:at % STEP + s.size]
        return page([make(r) for r in part], at + len(part) if at + len(part) < total and part else None,
                    total if s.cursor is None else None, "latest sale date first")
    return search


def _documents_of(number):
    out, at = [], 1
    while True:
        got = _debtwatch("documents", {"filters": {"CDIACNumber": {"type": "StringOneOf", "options": [number]}},
                                       "pagination": {"pageNumber": at, "pageSize": STEP},
                                       "sorting": {"columnId": "DocumentId", "ascending": True}, "searchTerm": None})
        out += [d for d in got["records"] if d.get("DocumentId")]
        if at * STEP >= got["totalMatchingRecords"] or not got["records"]:
            return out
        at += 1


def _read_issue(key):
    if not re.fullmatch(r"\d{4}-\d{4}", key):
        raise SourceError("A california_debt_issues record_id is the CDIAC number a search returned")
    rows = _debtwatch("issues", {"filters": {"CDIACNumber": {"type": "StringOneOf", "options": [key]}},
                                 "pagination": {"pageNumber": 1, "pageSize": 1}, "sorting": {"columnId": "CDIACNumber", "ascending": False},
                                 "searchTerm": None})["records"]
    if not rows or rows[0].get("CDIACNumber") != key:
        raise SourceError(f"California DebtWatch has no issue {key}")
    labels = {i: c["name"] for i, c in _meta("issues").items()}
    stated = {labels.get(k, k): (v[:10] if isinstance(v, str) and re.fullmatch(r"\d{4}-\d\d-\d\dT00:00:00", v) else v)
              for k, v in rows[0].items() if v not in (None, "", "(none)", "N/A", [])}
    documents = [_document(d) for d in _documents_of(key)]
    lines = outline(stated)
    if documents:
        lines += ["", "Documents filed (each a record of california_debt_documents):"]
        lines += [f"- california_debt_documents:{d['id']}  {d['document_type']}: {d['file_name']}" for d in documents]
    return {**_issue(rows[0]), "document": "\n".join(lines)}


def _file(doc):
    """A document's file. DebtWatch serves each file from an address it signs on request and that expires within the
    hour, so the address is asked for at each read and not kept."""
    try:
        urls = http_json(f"{DEBTWATCH}/api/document-url/{int(doc)}", tries=3, timeout=60)
    except requests.RequestException as e:
        raise SourceError(f"California DebtWatch did not give the file of document {doc} ({type(e).__name__})")
    if not isinstance(urls, list) or len(urls) != 1 or not str(urls[0]).startswith("https://stafunccdiaccofiprod.blob.core.windows.net/"):
        raise SourceError(f"California DebtWatch did not give the file of document {doc}")
    return urls[0]


def _read_document(key):
    number, _, doc = key.partition("/")
    row = next((d for d in _documents_of(number) if str(d["DocumentId"]) == doc), None) if re.fullmatch(r"\d{4}-\d{4}", number) else None
    if not row:
        raise SourceError(f"California DebtWatch has no document {key}")
    try:
        text = web.read(_file(doc))["text"]
    except SourceError:  # a scanned file, or one past the size a read takes
        text = "The file's text could not be extracted."
    return {**_document(row), "document": text}


def _span(dataset):
    def span(series):
        held = _meta(dataset)["SaleDate"]["filter"]["values"]
        return held["minimum"][:10], held["maximum"][:10]
    return span


# ---------- SEDAR+ documents from the data distribution service ----------

def _inside(path, root):
    path = path.resolve()
    if not path.is_relative_to(root):
        raise SourceError("The catalog names a file outside its directory")
    return path


def _pdf_text(path):
    if not path.is_file():
        raise SourceError("The document's file is not beside the catalog")
    content = path.read_bytes()[:web.MAX_BYTES + 1]
    if len(content) > web.MAX_BYTES or not content.startswith(b"%PDF-"):
        raise SourceError("The document's file is not a PDF within the size a read takes")
    return web._pdf(content)[0]


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
               "Commission, proposed or sold",
        creator="California public agencies that issue debt", publisher=CDIAC, jurisdiction="California", coverage=_span("issues"),
        fields=(F("cdiac_number", "identifier"), F("issuer", "name"), F("issuer_type", "text"), F("county", "place"),
                F("issue_name", "text"), F("project", "text"), F("sold_status", "text"), F("debt_type", "text"), F("purpose", "text"),
                F("source_of_repayment", "text"), F("sale_date", "date"), F("closing_date", "date"),
                F("first_optional_call_date", "date"), F("final_maturity_date", "date"), F("principal_amount", "amount"),
                F("lead_underwriter", "name"), F("purchaser", "name")),
        search=_search("issues", ISSUE, _issue), read=_read_issue),
    Series(
        id="california_debt_documents", title="California public debt documents",
        record="a document filed with the California Debt and Investment Advisory Commission for a public agency's debt issue: "
               "its official statement, indenture, resolution, lease or another of the issue's documents",
        creator="California public agencies that issue debt", publisher=CDIAC, jurisdiction="California", coverage=_span("documents"),
        fields=(F("document_id", "identifier"), F("cdiac_number", "identifier"), F("issuer", "name"), F("document_type", "text"),
                F("project", "text"), F("sale_date", "date"), F("principal_amount", "amount"), F("file_name", "text")),
        search=_search("documents", DOCUMENT, _document), read=_read_document),
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
