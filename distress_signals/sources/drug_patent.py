"""Eight series on drug approvals and patents: the Orange Book's products, patents and exclusivities; Drugs@FDA
applications; FDA advisory committee meetings; and the Patent Trial and Appeal Board's proceedings, documents
and hearings.

  orange_book_products,        FDA publishes the Orange Book as three data files in one small zip, updated
  orange_book_patents,         monthly. They are read whole; each patent and exclusivity carries its product's
  orange_book_exclusivities    applicant, trade name and ingredient.
  drugsfda_applications        openFDA's copy of Drugs@FDA, which applies names, numbers and action dates, and
                               reads no further than 25,000 records into one search.
  fda_advisory_committee_meetings   FDA's advisory committee calendar, read whole.
  ptab_hearings                the Board's published hearing schedules (PDF tables), read whole.
  ptab_proceedings,            the USPTO Open Data Portal, which needs a key (USPTO_API_KEY).
  ptab_documents
"""

import datetime as dt
import functools
import io
import os
import re
import time
import zipfile
from urllib.parse import quote, urlsplit

import requests

from ..util import SourceError, http_json, session, stable_id
from . import F, Series, outline, page, as_rows, web

FDA = "https://api.fda.gov/drug"
ORANGE_BOOK = "https://www.fda.gov/media/76860/download?attachment"
PRODUCT_PAGE = "https://www.accessdata.fda.gov/scripts/cder/ob/results_product.cfm"
CALENDAR = "https://www.fda.gov/datatables-json/advisory-committee-calendar-json"
PTAB = "https://api.uspto.gov/api/v1/patent/trials"
HEARINGS = "https://www.uspto.gov/patents/ptab/hearings/hearing-schedules-ptab"
HEARING_COLUMNS = ["Proceeding No.", "Serial No.", "PARTY", "Date", "Time", "Location", "Room"]
PTAB_BAG = {"proceedings": "patentTrialProceedingDataBag", "documents": "patentTrialDocumentDataBag"}
FDA_LAST = 25000  # openFDA reads no further than this into one search
YES = ("yes", "no")


def _request(url, **kwargs):
    # openFDA represents a valid empty search with a specific 404 response.
    try:
        return http_json(url, tries=3, timeout=45, **kwargs)
    except requests.HTTPError as exc:
        response = exc.response
        if response is not None:
            if response.status_code == 404 and url.startswith(FDA + "/"):
                try:
                    error = response.json().get("error", {})
                except ValueError:
                    error = {}
                if error.get("code") == "NOT_FOUND" and error.get("message") == "No matches found!":
                    return {"meta": {"results": {"total": 0}}, "results": []}
            raise SourceError(f"{url} returned HTTP {response.status_code}") from None
        raise SourceError(f"Request failed for {url}") from None
    except (requests.RequestException, ValueError) as exc:
        raise SourceError(f"Unable to read {url} ({type(exc).__name__})") from None



# ---------- the Orange Book ----------

_book = None


def _date(text):
    """'Aug 24, 2026' as a date; None for the book's 'Approved Prior to Jan 1, 1982'."""
    try:
        return dt.datetime.strptime((text or "").strip(), "%b %d, %Y").date().isoformat()
    except ValueError:
        return None


def _orange_book():
    """{products, patents, exclusivities, edition}: the three files, each patent and exclusivity joined to its
    product. Kept for six hours."""
    global _book
    if _book and time.time() - _book[0] < 6 * 3600:
        return _book[1]
    try:
        response = session().get(ORANGE_BOOK, timeout=90)
        response.raise_for_status()
        archive = zipfile.ZipFile(io.BytesIO(response.content))
        table = lambda name: [dict(zip(rows[0], r)) for rows in [[line.split("~") for line in  # noqa: E731
                              archive.read(name).decode("latin-1").splitlines() if line.strip()]] for r in rows[1:]]
        raw = {name: table(name) for name in ("products.txt", "patent.txt", "exclusivity.txt")}
    except Exception as e:
        raise SourceError(f"FDA's Orange Book data files could not be read: {type(e).__name__}: {str(e)[:150]}")
    flag = lambda v: "yes" if (v or "").strip().upper() in ("Y", "YES") else "no"  # noqa: E731
    kinds = {"N": "NDA", "A": "ANDA"}
    products, by_key = [], {}
    for r in raw["products.txt"]:
        key = (r["Appl_Type"], r["Appl_No"], r["Product_No"])
        row = {"id": f"{r['Appl_Type']}{r['Appl_No']}-{r['Product_No']}",
               "url": f"{PRODUCT_PAGE}?Appl_Type={r['Appl_Type']}&Appl_No={r['Appl_No']}",
               "ingredient": r["Ingredient"], "trade_name": r["Trade_Name"], "applicant": r.get("Applicant_Full_Name") or r["Applicant"],
               "dosage_form_route": r["DF;Route"], "strength": r["Strength"], "application_type": kinds.get(r["Appl_Type"], r["Appl_Type"]),
               "application_number": r["Appl_No"], "product_number": r["Product_No"], "te_code": r.get("TE_Code") or None,
               "approval_date": _date(r["Approval_Date"]), "reference_listed_drug": flag(r.get("RLD")),
               "reference_standard": flag(r.get("RS")), "marketing_status": r.get("Type") or None}
        products.append(row)
        by_key[key] = row
    shared = lambda r: {k: (by_key.get((r["Appl_Type"], r["Appl_No"], r["Product_No"])) or {}).get(k)  # noqa: E731
                        for k in ("trade_name", "ingredient", "applicant", "url")}
    patents = [{"id": f"{r['Appl_Type']}{r['Appl_No']}-{r['Product_No']}-{r['Patent_No']}-{r.get('Patent_Use_Code') or ''}",
                **shared(r), "patent_number": r["Patent_No"], "patent_expiration": _date(r["Patent_Expire_Date_Text"]),
                "application_number": r["Appl_No"], "product_number": r["Product_No"], "drug_substance": flag(r.get("Drug_Substance_Flag")),
                "drug_product": flag(r.get("Drug_Product_Flag")), "use_code": r.get("Patent_Use_Code") or None,
                "delist_requested": flag(r.get("Delist_Flag")), "submission_date": _date(r.get("Submission_Date"))}
               for r in raw["patent.txt"]]
    exclusivities = [{"id": f"{r['Appl_Type']}{r['Appl_No']}-{r['Product_No']}-{r['Exclusivity_Code']}-{_date(r['Exclusivity_Date'])}",
                      **shared(r), "exclusivity_code": r["Exclusivity_Code"], "exclusivity_expiration": _date(r["Exclusivity_Date"]),
                      "application_number": r["Appl_No"], "product_number": r["Product_No"]} for r in raw["exclusivity.txt"]]
    products.sort(key=lambda r: r["approval_date"] or "", reverse=True)
    patents.sort(key=lambda r: r["patent_expiration"] or "")
    exclusivities.sort(key=lambda r: r["exclusivity_expiration"] or "")
    got = {"products": products, "patents": patents, "exclusivities": exclusivities,
           "edition": response.headers.get("Last-Modified")}
    _book = (time.time(), got)
    return got


def _orange_series(kind, dated):
    def read(key):
        book = _orange_book()
        record = next((r for r in book[kind] if r["id"] == key), None)
        if not record:
            raise SourceError(f"The current Orange Book has no such record: {key}")
        same = lambda r: r["application_number"] == record["application_number"] and r["product_number"] == record["product_number"]  # noqa: E731
        product = next((r for r in book["products"] if same(r)), None)
        lines = [f"Orange Book data files, edition of {book['edition']}."] if book["edition"] else []
        if product and kind != "products":
            lines += ["", "Product:"] + [f"  {k}: {v}" for k, v in product.items() if k not in ("id", "url") and v]
        lines += ["", "Patents listed for the product (number, expiration, use code, delisting requested):"]
        lines += [f"  {r['patent_number']} | {r['patent_expiration']} | {r['use_code'] or ''} | {r['delist_requested']}"
                  for r in book["patents"] if same(r)] or ["  none"]
        lines += ["", "Exclusivities for the product (code, expiration):"]
        lines += [f"  {r['exclusivity_code']} | {r['exclusivity_expiration']}" for r in book["exclusivities"] if same(r)] or ["  none"]
        return {**record, "document": "\n".join(lines)}

    def span(series):
        dates = [r[dated] for r in _orange_book()[kind] if r.get(dated)]
        return (min(dates), max(dates)) if dates else (None, None)
    return (lambda s: page(_orange_book()[kind])), read, span


# ---------- Drugs@FDA applications ----------

def _literal(text):
    return '"' + str(text).replace('\\', '\\\\').replace('"', '\\"') + '"'


def _fda_records(dataset, clauses, limit, offset):
    params = {"limit": limit, "skip": offset}
    if clauses:
        params["search"] = " AND ".join(clauses)
    if offset > 25000:
        raise SourceError("openFDA allows skip up to 25000. Narrow the search for further matches.")
    return _request(f"{FDA}/{dataset}.json", params=params)


def _words(query, fields):
    # openFDA indexes some name fields as case-sensitive keywords and others as analyzed text.
    # Preserve phrase matching and include a case-insensitive Lucene term for mixed-case names.
    terms = [_literal(form) for form in dict.fromkeys((query, query.upper(), query.lower(), query.title()))]
    if any(c.isascii() and c.isalpha() for c in query):
        escaped = "".join(f"[{c.lower()}{c.upper()}]" if c.isascii() and c.isalpha()
                          else "\\" + c if c in r'\/.?+*|{}[]()"#@&<>~' else c for c in query)
        terms.append(f"/{escaped}/")
    names = "(" + " OR ".join(terms) + ")"
    return "(" + " OR ".join(f"{field}:{names}" for field in fields) + ")"



def _day(value):
    value = str(value or "")
    return f"{value[:4]}-{value[4:6]}-{value[6:8]}" if re.fullmatch(r"\d{8}", value) else None


def _application(item):
    app = item["application_number"]
    actions = sorted((a for a in item.get("submissions") or [] if a.get("submission_status_date")), key=lambda a: a["submission_status_date"])
    first = next((a for a in actions if a.get("submission_type") == "ORIG" and a.get("submission_status") == "AP"), None)
    products = item.get("products") or []
    return {"id": app, "url": f"https://www.accessdata.fda.gov/scripts/cder/daf/index.cfm?event=overview.process&ApplNo={app[-6:]}",
            "application_number": app, "sponsor": item.get("sponsor_name"),
            "brand_name": sorted({p["brand_name"] for p in products if p.get("brand_name")}),
            "ingredient": sorted({i["name"] for p in products for i in p.get("active_ingredients") or [] if i.get("name")}),
            "original_approval_date": _day(first["submission_status_date"]) if first else None,
            "latest_action_date": _day(actions[-1]["submission_status_date"]) if actions else None,
            "latest_action": " ".join(str(actions[-1].get(k) or "") for k in ("submission_type", "submission_number", "submission_status")).strip() if actions else None}


def _search_applications(s):
    clauses = []
    for field, columns in (("sponsor", ("sponsor_name",)), ("brand_name", ("products.brand_name",)),
                           ("ingredient", ("products.active_ingredients.name",))):
        given = s.any(field)
        if given:
            clauses.append("(" + " OR ".join(_words(g, columns) for g in given) + ")")
    numbers = s.any("application_number")
    if numbers:
        clauses.append("(" + " OR ".join(f"application_number:{_literal(n.upper())}" for n in numbers) + ")")
    if s.words():
        clauses.append(_words(s.query(), ("application_number", "sponsor_name", "products.brand_name", "products.active_ingredients.name")))
    span = s.peek("latest_action_date")  # an application with any action in the range; the registry checks the latest
    if span:
        clauses.append(f"submissions.submission_status_date:[{(span.get('lo') or '1900-01-01').replace('-', '')} TO "
                       f"{(span.get('hi') or '2999-12-31').replace('-', '')}]")
    skip = s.cursor or 0
    if skip > FDA_LAST:
        raise SourceError("openFDA reads no further than 25,000 records into one search. Narrow the search.")
    params = {"limit": max(1, min(s.size, 1000)), "skip": skip, "sort": "submissions.submission_status_date:desc"}
    params |= {"search": " AND ".join(clauses)} if clauses else {}
    data = _request(f"{FDA}/drugsfda.json", params=params)
    rows, total = data["results"], data["meta"]["results"]["total"]
    return page([_application(x) for x in rows], skip + len(rows) if rows and skip + len(rows) < total else None, total,
                "latest action first")


def _read_application(app):
    data = _request(f"{FDA}/drugsfda.json", params={"search": f"application_number:{_literal(app)}", "limit": 1})
    if len(data["results"]) != 1 or data["results"][0]["application_number"] != app:
        raise SourceError(f"Drugs@FDA has no application {app}")
    item = data["results"][0]
    record = _application(item)
    return {**record, "document": as_rows(outline({"products": item.get("products"), "submissions": item.get("submissions")})),
            "published": record["latest_action_date"]}


# ---------- FDA advisory committee meetings ----------

def _meetings():
    rows = []
    for item in _request(CALENDAR):
        parser = web._Text(CALENDAR)
        parser.feed(item["title"])
        if len(parser.links) != 1:
            continue
        url = parser.links[0]["url"]
        day = lambda v: dt.datetime.strptime(v.split()[0], "%m/%d/%Y").date().isoformat() if v else None  # noqa: E731
        rows.append({"id": url.rstrip("/").rsplit("/", 1)[-1], "url": url, "title": "".join(parser.out).strip(),
                     "center": item.get("field_center") or None, "office": item.get("field_contributing_office") or None,
                     "start_date": day(item.get("field_start_date")), "end_date": day(item.get("field_end_date"))})
    rows.sort(key=lambda r: r["start_date"] or "", reverse=True)
    return rows


def _read_meeting(key):
    record = next((r for r in _meetings() if r["id"] == key), None)
    if not record:
        raise SourceError("FDA's calendar no longer lists this meeting.")
    got = web.read(record["url"])
    return {**record, "document": got["text"], "links": got["data"].get("links") or []}


# ---------- PTAB hearings ----------

def _hearing_pdf(url):
    parts = urlsplit(url)
    return (parts.scheme == "https" and parts.netloc == "www.uspto.gov"
            and parts.path.startswith("/sites/default/files/") and parts.path.lower().endswith(".pdf")
            and not parts.query and not parts.fragment and ".." not in parts.path)


def _hearing_rows(url):
    import pdfplumber

    if not _hearing_pdf(url):
        raise SourceError("Invalid USPTO hearing schedule URL")
    with session().get(url, timeout=45, stream=True) as response:
        response.raise_for_status()
        content = response.raw.read(web.MAX_BYTES + 1, decode_content=True)
    if len(content) > web.MAX_BYTES:
        raise SourceError("USPTO hearing schedule exceeds the PDF size limit")
    rows = []
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        for page in pdf.pages:
            table = page.extract_table() or []
            if HEARING_COLUMNS not in table:
                raise SourceError(f"Unrecognized USPTO schedule table on page {page.page_number} of {url}")
            for cells in table[table.index(HEARING_COLUMNS) + 1:]:
                if not any(cells):
                    continue
                if len(cells) != len(HEARING_COLUMNS) or not all(cells[i] for i in (0, 2, 3)):
                    raise SourceError(f"Incomplete hearing row on page {page.page_number} of {url}")
                proceedings, serial, parties, date, time, location, room = cells
                try:
                    day = dt.datetime.strptime(date.strip(), "%A, %B %d, %Y").date().isoformat()
                except ValueError:
                    raise SourceError(f"Unrecognized USPTO hearing date {date!r} in {url}") from None
                ids = ["".join(x.split()) for x in proceedings.splitlines() if x.strip()]
                hearing = {"proceedings": ids, "serial_number": serial, "parties": " ".join(parties.split()),
                           "hearing_date": day, "time": time, "location": location, "room": room}
                key = stable_id(*ids, day, time)
                rows.append({"id": f"{key}:{url}", "url": f"{url}#page={page.page_number}", "proceeding": ids,
                             "serial_number": serial, "parties": hearing["parties"], "hearing_date": day,
                             "time": " ".join((time or "").split()) or None, "location": " ".join((location or "").split()) or None,
                             "room": " ".join((room or "").split()) or None})
    return rows



@functools.lru_cache(maxsize=1)
def _hearings(stamp):
    index = web.read(HEARINGS)
    links = [link["url"] for link in index["data"]["links"]
             if "hearing schedule" in link["text"].lower() and _hearing_pdf(link["url"])]
    if not links:
        raise SourceError(f"USPTO has no linked hearing schedule PDF at {HEARINGS}")
    rows = [row for url in dict.fromkeys(links) for row in _hearing_rows(url)]
    rows.sort(key=lambda r: r["hearing_date"])
    return rows


def _all_hearings():
    return _hearings(int(time.time() // 3600))  # read again each hour


def _read_hearing(key):
    record = next((r for r in _all_hearings() if r["id"] == key), None)
    if not record:
        raise SourceError("The Board's current schedules no longer list this hearing.")
    return dict(record)


# ---------- PTAB proceedings and documents (USPTO Open Data Portal) ----------

def _odp(path, params=None):
    return _request(f"{PTAB}/{path}", params=params, headers={"X-API-KEY": os.environ["USPTO_API_KEY"]})


def _parties(item):
    owner, petitioner = item.get("patentOwnerData") or {}, item.get("regularPetitionerData") or item.get("derivationPetitionerData") or {}
    return {"petitioner": petitioner.get("realPartyInInterestName"),
            "patent_owner": owner.get("patentOwnerName") or owner.get("realPartyInInterestName"),
            "patent_number": owner.get("patentNumber")}


def _proceeding(item):
    meta, trial = item.get("trialMetaData") or {}, item["trialNumber"]
    day = lambda k: (meta.get(k) or "")[:10] or None  # noqa: E731
    return {"id": trial, "url": f"https://ptab.uspto.gov/#/login?caseNumber={quote(trial, safe='')}", "trial_number": trial,
            "trial_type": meta.get("trialTypeCode"), "status": meta.get("trialStatusCategory"), **_parties(item),
            "petition_filing_date": day("petitionFilingDate"), "institution_decision_date": day("institutionDecisionDate"),
            "latest_decision_date": day("latestDecisionDate"), "termination_date": day("terminationDate")}


def _document(item):
    doc = item.get("documentData") or {}
    return {"id": doc.get("documentIdentifier"), "url": doc.get("fileDownloadURI") or doc.get("downloadURI"),
            "document_id": doc.get("documentIdentifier"), "trial_number": item.get("trialNumber"),
            "title": doc.get("documentTitleText") or doc.get("documentName"), "category": doc.get("documentCategory"),
            "document_type": doc.get("documentTypeDescriptionText"), "filing_party": doc.get("filingPartyCategory"),
            "filing_date": (doc.get("documentFilingDate") or "")[:10] or None, **_parties(item)}


def _ptab_series(kind, shape):
    def search(s):
        terms = [f"({s.query()})"] if s.words() else []
        trial = s.one("trial_number")
        terms += ["trialNumber:" + _literal(trial)] if trial else []
        offset = s.cursor or 0
        params = {"limit": max(1, min(s.size, 100)), "offset": offset}
        params |= {"q": " AND ".join(terms)} if terms else {}
        got = _odp(kind + "/search", params)
        rows = [shape(x) for x in got.get(PTAB_BAG[kind]) or []]
        return page(rows, offset + len(rows) if rows and offset + len(rows) < (got.get("count") or 0) else None, got.get("count"))

    def read(key):
        got = (_odp(f"{kind}/{quote(key, safe='')}").get(PTAB_BAG[kind])) or []
        if len(got) != 1:
            raise SourceError(f"The Open Data Portal has no PTAB {kind[:-1]} {key}")
        record = shape(got[0])
        text = (got[0].get("documentData") or {}).get("documentOCRText") or "\n".join(outline(got[0]))
        return {**record, "document": text, "published": record.get("filing_date") or record.get("petition_filing_date")}
    return search, read


FDA_NAME, USPTO = "U.S. Food and Drug Administration", "U.S. Patent and Trademark Office, Patent Trial and Appeal Board"
PRODUCT = (F("trade_name", "name"), F("ingredient", "name"), F("applicant", "name"), F("application_number", "identifier"),
           F("product_number", "identifier"))
_products, _patents, _exclusivities = (_orange_series("products", "approval_date"), _orange_series("patents", "patent_expiration"),
                                       _orange_series("exclusivities", "exclusivity_expiration"))
_proceedings, _documents = _ptab_series("proceedings", _proceeding), _ptab_series("documents", _document)
PARTIES = (F("petitioner", "name"), F("patent_owner", "name"), F("patent_number", "identifier"))
SERIES = [
    Series(
        id="orange_book_products", title="Orange Book products",
        record="a drug product approved under a new drug application (NDA) or an abbreviated one (ANDA), as the Orange Book lists it",
        creator=FDA_NAME, jurisdiction="federal", coverage=_products[2],
        fields=(F("ingredient", "name"), F("trade_name", "name"), F("applicant", "name"), F("dosage_form_route", "text"),
                F("strength", "text"), F("application_type", ("NDA", "ANDA")), F("application_number", "identifier"),
                F("product_number", "identifier"), F("te_code", "identifier"), F("approval_date", "date"),
                F("reference_listed_drug", YES), F("reference_standard", YES), F("marketing_status", ("RX", "OTC", "DISCN"))),
        search=_products[0], read=_products[1]),
    Series(
        id="orange_book_patents", title="Orange Book patents",
        record="a patent listed in the Orange Book for an approved drug product, with its expiration date",
        creator=FDA_NAME, jurisdiction="federal", coverage=_patents[2],
        fields=(F("patent_number", "identifier"), F("patent_expiration", "date"), *PRODUCT, F("drug_substance", YES),
                F("drug_product", YES), F("use_code", "identifier"), F("delist_requested", YES), F("submission_date", "date")),
        search=_patents[0], read=_patents[1]),
    Series(
        id="orange_book_exclusivities", title="Orange Book exclusivities",
        record="a period of marketing exclusivity the Orange Book lists for an approved drug product, with its code and expiration date",
        creator=FDA_NAME, jurisdiction="federal", coverage=_exclusivities[2],
        fields=(F("exclusivity_code", "identifier"), F("exclusivity_expiration", "date"), *PRODUCT),
        search=_exclusivities[0], read=_exclusivities[1]),
    Series(
        id="drugsfda_applications", title="Drugs@FDA",
        record="an application to market a drug in the United States (NDA, ANDA or BLA), with its products and FDA's actions on it",
        creator=FDA_NAME, jurisdiction="federal", since="1939",
        fields=(F("application_number", "identifier"), F("sponsor", "name"), F("brand_name", "name", many=True),
                F("ingredient", "name", many=True), F("original_approval_date", "date"), F("latest_action_date", "date"),
                F("latest_action", "text")),
        search=_search_applications, read=_read_application),
    Series(
        id="fda_advisory_committee_meetings", title="FDA advisory committee meetings",
        record="a meeting of an FDA advisory committee, as announced on FDA's calendar",
        creator=FDA_NAME, jurisdiction="federal",
        coverage=lambda series: (lambda d: (min(d), max(d)))([r["start_date"] for r in _meetings() if r["start_date"]]),
        fields=(F("title", "text"), F("center", "text"), F("office", "text"), F("start_date", "date"), F("end_date", "date")),
        search=lambda s: page(_meetings(), order="latest meeting first"), read=_read_meeting),
    Series(
        id="ptab_hearings", title="PTAB hearings",
        record="an oral hearing on the Patent Trial and Appeal Board's published schedule",
        creator=USPTO, jurisdiction="federal",
        coverage=lambda series: (lambda d: (min(d), max(d)))([r["hearing_date"] for r in _all_hearings()]),
        fields=(F("proceeding", "identifier", many=True), F("serial_number", "identifier"), F("parties", "text"),
                F("hearing_date", "date"), F("time", "text"), F("location", "text"), F("room", "text")),
        search=lambda s: page(_all_hearings(), order="soonest hearing first"), read=_read_hearing),
    Series(
        id="ptab_proceedings", title="PTAB proceedings",
        record="a trial before the Patent Trial and Appeal Board challenging a patent: an inter partes review, post-grant "
               "review, covered business method review or derivation",
        creator=USPTO, jurisdiction="federal", since="2012", requires=("USPTO_API_KEY",),
        fields=(F("trial_number", "identifier"), F("trial_type", "identifier"), F("status", "text"), *PARTIES,
                F("petition_filing_date", "date"), F("institution_decision_date", "date"), F("latest_decision_date", "date"),
                F("termination_date", "date")),
        search=_proceedings[0], read=_proceedings[1]),
    Series(
        id="ptab_documents", title="PTAB documents",
        record="a document filed in a Patent Trial and Appeal Board trial: a petition, response, order or decision",
        creator="parties to PTAB trials and the Board", publisher=USPTO, jurisdiction="federal", since="2012",
        requires=("USPTO_API_KEY",),
        fields=(F("document_id", "identifier"), F("trial_number", "identifier"), F("title", "text"), F("category", "text"),
                F("document_type", "text"), F("filing_party", "text"), F("filing_date", "date"), *PARTIES,
                F("full_text", "full text")),
        search=_documents[0], read=_documents[1]),
]
