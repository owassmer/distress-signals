"""FCC public docket filings and auction reports; NRC ADAMS document search and retrieval."""

import base64
import csv
import datetime as dt
import io
import os
import re
from urllib.parse import urlencode

import requests

from ..util import SourceError, http_json, session, stable_id
from . import federal_register, web

ECFS = "https://publicapi.fcc.gov/ecfs/filings"
PRS = "https://auctiondata.fcc.gov/public/projects"
ADAMS = "https://adams-api.nrc.gov/aps/api/search"
AUCTION_REPORTS = {"announcements", "bidder_status"}

SOURCES = {
    "fcc": "FCC docket filings by words, docket or filer. For an auction use auction_number, with report "
           "filings (default), announcements or bidder_status. The latter two search the auction's published CSV.",
    "nrc_adams": "ADAMS documents by text, docket, document_type or organization; since/until filter the date added. "
                 "Requires NRC_ADAMS_API_KEY. For keyless search set collection=notices: NRC Federal Register notices "
                 "by words and publication dates, with GPO text, agency deadlines and cited ADAMS accessions.",
}
FILTERS = {
    "fcc": {"docket", "filer", "auction_number", "report", "limit", "offset"},
    "nrc_adams": {"collection", "docket", "document_type", "organization", "since", "until", "limit", "offset"},
}


def _json(label, url, **options):
    try:
        return http_json(url, timeout=45, tries=2, **options)
    except requests.RequestException as exc:
        status = exc.response.status_code if exc.response is not None else type(exc).__name__
        raise SourceError(f"{label} request failed ({status})") from exc
    except ValueError as exc:
        raise SourceError(f"{label} returned invalid JSON") from exc


def _fcc_json(url, **options):
    key = os.getenv("FCC_API_KEY") or "DEMO_KEY"
    return _json("FCC ECFS (set FCC_API_KEY for your own quota)", url, headers={"X-Api-Key": key}, **options)


def _adams_json(url, **options):
    key = os.getenv("NRC_ADAMS_API_KEY")
    if not key:
        raise SourceError("Set NRC_ADAMS_API_KEY from https://adams-api-developer.nrc.gov/ for the ADAMS library, "
                          "or search nrc_adams with collection=notices for keyless NRC Federal Register notices.")
    return _json("NRC ADAMS", url, headers={"Ocp-Apim-Subscription-Key": key}, **options)


def _ecfs_row(record):
    sid = str(record["id_submission"])
    documents = [d for d in (record.get("documents") or []) if d.get("src")]
    proceedings = [{"number": p.get("name"), "description": p.get("description")}
                   for p in record.get("proceedings") or []]
    return {
        "record_id": f"fcc:{sid}", "url": f"https://www.fcc.gov/ecfs/search/search-filings/filing/{sid}",
        "title": (documents and documents[0].get("filename")) or
                 (proceedings and proceedings[0]["description"]) or f"ECFS filing {sid}",
        "filers": [f["name"] for f in record.get("filers") or [] if f.get("name")],
        "proceedings": proceedings, "submission_type": (record.get("submissiontype") or {}).get("description"),
        "published": record.get("date_disseminated"), "received": record.get("date_received"),
        "comment_due": record.get("date_comment_period"), "reply_comment_due": record.get("date_reply_comment"),
        "documents": [{"text": d.get("description") or d.get("filename") or "Document", "url": d["src"]}
                      for d in documents],
    }


def _ecfs(query, limit, offset, docket=None, filer=None, auction_number=None):
    def phrase(value):
        return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"') + '"'

    terms = [f"({query})"] if query else []
    if docket:
        terms.append(f"proceedings.name:({phrase(docket)})")
    if filer:
        terms.append(f"filers.name:({phrase(filer)})")
    if auction_number:
        terms.append(phrase(f"Auction {auction_number}"))
    params = {"limit": limit + 1, "offset": offset, "sort": "date_disseminated,DESC"}
    if terms:
        params["q"] = " AND ".join(terms)
    data = _fcc_json(ECFS, params=params)
    if not isinstance(data.get("filing"), list):
        raise SourceError("FCC ECFS response is missing its filing list")
    records = data["filing"]
    rows = [_ecfs_row(r) for r in records[:limit]]
    more = len(records) > limit
    return {"results": rows, "complete": not more, "next_offset": offset + limit if more else None}


def _auction_csv(auction, report):
    url = f"{PRS}/auction{auction}/reports/{report}/download"
    # The bidder report has one row per round and bidder. Bound a large download before parsing.
    maximum = 32 * 1024 * 1024
    try:
        with session().get(url, stream=True, timeout=45) as response:
            response.raise_for_status()
            data = response.raw.read(maximum + 1, decode_content=True)
    except requests.RequestException as exc:
        raise SourceError(f"FCC auction {auction} could not supply {report}: {exc}") from exc
    if len(data) > maximum:
        raise SourceError(f"FCC auction {auction} {report} CSV exceeds 32 MiB")
    try:
        records = csv.DictReader(io.StringIO(data.decode("utf-8-sig"), newline=""))
        identifiers = ("announcement_time", "subject") if report == "announcements" else ("round", "frn")
        if not set(identifiers).issubset(records.fieldnames or []):
            raise SourceError(f"FCC auction {auction} does not supply {report} with fields {identifiers}")
        for record in records:
            if not all(record.get(k) for k in identifiers):
                raise SourceError(f"FCC auction {auction} returned a row without its identifiers")
            native = f"auction/{auction}/{report}/" + stable_id(*(record[k] for k in identifiers))
            title = record.get("subject") or f"{record.get('bidder') or record['frn']}, round {record['round']}"
            yield {"record_id": f"fcc:{native}", "url": url, "title": title,
                   "published": record.get("announcement_time"), "fields": record}
    except (UnicodeError, csv.Error) as exc:
        raise SourceError(f"FCC auction {auction} returned an unreadable {report} CSV") from exc


def _adams_row(doc):
    accession = doc["AccessionNumber"]
    permalink = "https://adamswebsearch2.nrc.gov/webSearch2/main.jsp?" + urlencode({"AccessionNumber": accession})
    return {
        "record_id": f"nrc_adams:{accession}", "url": doc.get("Url") or permalink,
        "title": doc.get("DocumentTitle") or doc.get("Name") or accession,
        "published": doc.get("DateAddedTimestamp") or doc.get("DateAdded"),
        "document_date": doc.get("DocumentDate"), "document_types": doc.get("DocumentType"),
        "author_organizations": doc.get("AuthorAffiliation"), "addressee_organizations": doc.get("AddresseeAffiliation"),
        "docket_numbers": doc.get("DocketNumber"), "license_numbers": doc.get("LicenseNumber"),
    }


def _adams(query, limit, offset, docket=None, document_type=None, organization=None, since=None, until=None):
    filters = [{"field": field, "value": value, "operator": "equals"}
               for field, value in (("DocketNumber", docket), ("DocumentType", document_type)) if value]
    for operator, value in (("ge", since), ("le", until)):
        if value:
            try:
                date = dt.date.fromisoformat(value).isoformat()
            except (TypeError, ValueError) as exc:
                raise SourceError("ADAMS dates use YYYY-MM-DD") from exc
            filters.append({"field": "DateAddedTimestamp", "value": f"(DateAddedTimestamp {operator} '{date}')"})
    any_filters = [{"field": field, "value": organization, "operator": "contains"}
                   for field in ("AddresseeAffiliation", "AuthorAffiliation")] if organization else []
    body = {"q": query, "filters": filters, "anyFilters": any_filters, "mainLibFilter": True,
            "legacyLibFilter": True, "content": True, "skip": offset, "sort": "DateAddedTimestamp", "sortDirection": 1}
    data = _adams_json(ADAMS, method="POST", json_body=body)
    if not isinstance(data.get("results"), list) or not isinstance(data.get("count"), int):
        raise SourceError("NRC ADAMS response is missing its results or count")
    rows = [_adams_row(r["document"]) for r in data["results"][:limit]]
    next_offset = offset + len(rows)
    if not rows and next_offset < data["count"]:
        raise SourceError("NRC ADAMS returned an empty page before its reported end")
    more = next_offset < data["count"]
    return {"results": rows, "total_matches": data["count"], "complete": not more,
            "next_offset": next_offset if more else None}


def _notice_row(doc):
    number, published = doc["document_number"], doc["publication_date"]
    return {"record_id": f"nrc_adams:notice/{number}", "title": doc["title"],
            "url": f"https://www.govinfo.gov/content/pkg/FR-{published}/html/{number}.htm",
            "collection": "NRC Federal Register notices", "published": published, "action": doc.get("action"),
            "dates": doc.get("dates"), "docket_ids": doc.get("docket_ids"), "comments_close": doc.get("comments_close_on")}


def _notices(query, limit, offset, since=None, until=None, **other):
    if other:
        raise SourceError("For collection=notices search by words in query and publication dates in since/until")
    if offset >= federal_register.CAP:
        raise SourceError("Federal Register result windows end at 10,000; narrow the query or publication dates")
    params = {"conditions[agencies][]": "nuclear-regulatory-commission", "conditions[type][]": "NOTICE",
              "per_page": 100, "order": "newest",
              "fields[]": ["document_number", "publication_date", "title", "action", "dates", "docket_ids", "comments_close_on"]}
    if query:
        params["conditions[term]"] = query
    for side, value in (("gte", since), ("lte", until)):
        if value:
            try:
                params[f"conditions[publication_date][{side}]"] = dt.date.fromisoformat(value).isoformat()
            except (TypeError, ValueError) as exc:
                raise SourceError("Notice publication dates use YYYY-MM-DD") from exc
    if since and until and since > until:
        raise SourceError("since must be on or before until")
    page, skip = divmod(offset, 100)
    data = _json("NRC Federal Register notices", federal_register.API, params={**params, "page": page + 1})
    count = data["count"]
    docs = data["results"][skip:skip + limit]
    if len(docs) < limit and offset + len(docs) < min(count, federal_register.CAP):
        extra = _json("NRC Federal Register notices", federal_register.API, params={**params, "page": page + 2})
        docs += extra["results"][:limit - len(docs)]
    end = offset + len(docs)
    result = {"results": [_notice_row(doc) for doc in docs], "total_matches": count,
              "complete": end >= count and count < federal_register.CAP,
              "next_offset": end if docs and end < min(count, federal_register.CAP) else None}
    if count >= federal_register.CAP:
        result["note"] = "The Federal Register caps a search at 10,000 matches. Narrow the publication dates for the rest."
    return result


def _read_notice(number):
    if not re.fullmatch(r"[A-Za-z0-9-]+", number):
        raise SourceError("Invalid Federal Register document number")
    doc = _json("NRC Federal Register notice", f"{federal_register.BASE}/documents/{number}.json")
    if (doc.get("document_number") != number or doc.get("type") != "Notice"
            or not any(a.get("slug") == "nuclear-regulatory-commission" for a in doc.get("agencies", []))):
        raise SourceError("The document is not an NRC Federal Register notice")
    row = _notice_row(doc)
    notice = web.read(row["url"])
    # Accessions identify cited material; the notice supplies the surrounding text and dates.
    accessions = list(dict.fromkeys(re.findall(r"\bML[0-9]{5}[A-Z0-9][0-9]{3}\b", notice["text"])))
    row["links"] = [{"text": f"ADAMS {acc}", "url": "https://adamswebsearch2.nrc.gov/webSearch2/main.jsp?"
                     + urlencode({"AccessionNumber": acc})} for acc in accessions]
    row["adams_accessions"] = accessions
    return {"source": "nrc_adams", "kind": "nrc_notice", "native_id": "notice/" + number, "url": row["url"],
            "published": row["published"], "data": row, "text": notice["text"]}


def search(source, query, limit=25, offset=0, **filters):
    if type(limit) is not int or not 1 <= limit <= 99 or type(offset) is not int or offset < 0:
        raise SourceError("limit must be an integer from 1 to 99 and offset a nonnegative integer")
    if source == "nrc_adams":
        collection = filters.pop("collection", "adams")
        if collection == "notices":
            return _notices(query or "", limit, offset, **filters)
        if collection != "adams":
            raise SourceError("NRC collection is adams or notices")
        return _adams(query or "", limit, offset, **filters)
    report = filters.pop("report", "filings")
    auction = filters.get("auction_number")
    if auction is not None and not re.fullmatch(r"[1-9][0-9]{0,5}", str(auction)):
        raise SourceError("auction_number must be a positive FCC auction number")
    if report == "filings":
        return _ecfs(query, limit, offset, **filters)
    if report not in AUCTION_REPORTS or auction is None or filters.get("docket") or filters.get("filer"):
        raise SourceError("For announcements or bidder_status, give auction_number and use query to search report rows")
    terms = (query or "").casefold().split()
    results, total = [], 0
    for row in _auction_csv(auction, report):
        fields = " ".join(row["fields"].values()).casefold()
        if all(term in fields for term in terms):
            if offset <= total < offset + limit:
                results.append(row)
            total += 1
    more = offset + limit < total
    return {"results": results, "total_matches": total, "complete": not more,
            "next_offset": offset + limit if more else None}


def _adams_text(content):
    if not isinstance(content, str) or not content:
        return ""
    try:
        decoded = base64.b64decode(content, validate=True).decode("utf-8")
    except ValueError:
        return content
    return decoded if decoded and all(c.isprintable() or c.isspace() for c in decoded) else content


def read(source, native_id):
    if source == "nrc_adams" and native_id.startswith("notice/"):
        return _read_notice(native_id.removeprefix("notice/"))
    if source == "nrc_adams":
        if not re.fullmatch(r"[A-Za-z0-9-]+", native_id):
            raise SourceError("Invalid ADAMS accession number")
        data = _adams_json(f"{ADAMS}/{native_id}")
        doc = data.get("document") or {}
        if doc.get("AccessionNumber") != native_id:
            raise SourceError("NRC ADAMS returned no matching document")
        row = _adams_row(doc)
        body = _adams_text(doc.get("content"))
        kind = "nrc_document"
    elif native_id.isascii() and native_id.isdigit():
        doc = _fcc_json(f"{ECFS}/{native_id}")
        if str(doc.get("id_submission")) != native_id:
            raise SourceError("FCC ECFS returned no matching filing")
        row = _ecfs_row(doc)
        row["links"] = row["documents"]
        kind, body = "fcc_filing", doc.get("text_data") or ""
    else:
        match = re.fullmatch(r"auction/([1-9][0-9]{0,5})/(announcements|bidder_status)/([0-9a-f]{20})", native_id)
        if not match:
            raise SourceError("Invalid FCC record ID")
        row = next((r for r in _auction_csv(match[1], match[2]) if r["record_id"] == f"fcc:{native_id}"), None)
        if row is None:
            raise SourceError("The row is absent from the current FCC auction report")
        kind, body = "fcc_auction_record", ""

    fields = row.get("fields", row)
    lines = []
    for key, value in fields.items():
        if key in ("record_id", "links", "url") or value is None or value == []:
            continue
        if isinstance(value, list):
            for item in value:
                words = " | ".join(str(v) for v in item.values() if v) if isinstance(item, dict) else str(item)
                lines.append(f"{key}: {words}")
        else:
            lines.append(f"{key}: {value}")
    if body:
        lines.extend(("", body))
    return {"kind": kind, "source": source, "native_id": native_id, "url": row["url"],
            "published": row.get("published"), "text": "\n".join(lines), "data": row}
