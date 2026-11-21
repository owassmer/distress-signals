"""Four series: filings in FCC proceedings (ECFS), the announcements and bidder status of FCC spectrum auctions,
and documents in the NRC's ADAMS library.

  fcc_ecfs_filings            ECFS applies the words, a proceeding, a filer and a range of received dates. It
                              reads no further than 10,000 records into one query, so a search continues from
                              the last record's received time, which has no such limit. It takes an api.data.gov
                              key (DATA_GOV_API_KEY); the shared demo key allows ten requests a day.
  fcc_auction_announcements,  the FCC's public reporting system publishes each auction's reports as one file,
  fcc_auction_bidder_status   read for one auction at a time.
  nrc_adams_documents         the ADAMS public search API, which needs a subscription key (NRC_ADAMS_API_KEY).
"""

import base64
import csv
import functools
import io
import os
import re
from urllib.parse import urlencode

import requests

from ..util import SourceError, http_json, session, stable_id, to_float
from . import F, Series, page

ECFS = "https://publicapi.fcc.gov/ecfs/filings"
PRS = "https://auctiondata.fcc.gov/public/projects"
ADAMS = "https://adams-api.nrc.gov/aps/api/search"
ECFS_PER, ECFS_LAST = 250, 9750  # the most ECFS returns at once, and the last offset it reads


def _json(label, url, **options):
    try:
        return http_json(url, timeout=45, tries=2, **options)
    except requests.RequestException as exc:
        status = exc.response.status_code if exc.response is not None else type(exc).__name__
        raise SourceError(f"{label} request failed ({status})") from exc
    except ValueError as exc:
        raise SourceError(f"{label} returned invalid JSON") from exc


def _fcc_json(url, **options):
    return _json("FCC ECFS", url, headers={"X-Api-Key": os.environ["DATA_GOV_API_KEY"]}, **options)


# ---------- ECFS filings ----------

def _ecfs(record):
    sid = str(record["id_submission"])
    documents = [d for d in (record.get("documents") or []) if d.get("src")]
    return {"id": sid, "url": f"https://www.fcc.gov/ecfs/search/search-filings/filing/{sid}", "submission_id": sid,
            "filer": [f["name"] for f in record.get("filers") or [] if f.get("name")],
            "law_firm": [f["name"] for f in record.get("lawfirms") or [] if f.get("name")],
            "proceeding": [p["name"] for p in record.get("proceedings") or [] if p.get("name")],
            "proceeding_description": [p["description"] for p in record.get("proceedings") or [] if p.get("description")],
            "submission_type": (record.get("submissiontype") or {}).get("description"),
            "received_date": (record.get("date_received") or "")[:10] or None,
            "disseminated_date": (record.get("date_disseminated") or "")[:10] or None,
            "file_name": [d.get("filename") for d in documents if d.get("filename")],
            "_documents": [{"text": d.get("description") or d.get("filename") or "Document", "url": d["src"]} for d in documents],
            "_received": record.get("date_received")}


def _search_ecfs(s):
    ids = s.any("submission_id")
    if ids:
        found = []
        for i in ids:
            doc = _fcc_json(f"{ECFS}/{i}")
            doc = doc.get("filing") if isinstance(doc.get("filing"), dict) else doc
            found.append({k: v for k, v in _ecfs(doc).items() if not k.startswith("_")})
        return page(found)
    lo, hi = s.between("received_date")
    at = s.cursor or {"until": None, "skip": 0}
    params = {"sort": "date_received,DESC", "limit": max(1, min(s.size, ECFS_PER)), "offset": at["skip"]}
    for key, value in (("q", s.query()), ("proceedings.name", s.one("proceeding")), ("filers.name", s.one("filer")),
                       ("lawfirms.name", s.one("law_firm"))):
        if value:
            params[key] = value
    # ECFS takes a range of received dates only with both ends
    upper = at["until"] or (hi + "T23:59:59.999Z" if hi else "9999-12-31T23:59:59.999Z")
    params["date_received"] = f"[gte]{lo or '1900-01-01'}[lte]{upper}"
    data = _fcc_json(ECFS, params=params)
    if not isinstance(data.get("filing"), list):
        raise SourceError("FCC ECFS response is missing its filing list")
    rows = [_ecfs(r) for r in data["filing"]]
    nxt = None
    if len(rows) == params["limit"]:
        last = rows[-1]["_received"]
        same = sum(1 for r in rows if r["_received"] == last)
        nxt = {"until": last, "skip": same + (at["skip"] if last == at["until"] else 0)}
        if nxt["skip"] > ECFS_LAST:
            raise SourceError("More than 10,000 ECFS filings share one received time; narrow the search.")
    return page([{k: v for k, v in r.items() if not k.startswith("_")} for r in rows], nxt, order="latest received first")


def _read_ecfs(sid):
    doc = _fcc_json(f"{ECFS}/{sid}")
    doc = doc.get("filing") if isinstance(doc.get("filing"), dict) else doc
    if str(doc.get("id_submission")) != str(sid):
        raise SourceError("FCC ECFS returned no matching filing")
    row = _ecfs(doc)
    return {**{k: v for k, v in row.items() if not k.startswith("_")}, "document": doc.get("text_data") or "",
            "links": row["_documents"], "published": row["disseminated_date"]}


def _ecfs_newest(series):
    rows = _fcc_json(ECFS, params={"sort": "date_received,DESC", "limit": 1}).get("filing") or [{}]
    return series.since, (rows[0].get("date_received") or "")[:10] or None


# ---------- auction reports ----------

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
            yield {"id": f"{auction}:" + stable_id(*(record[k] for k in identifiers)), "url": url, "fields": record}
    except (UnicodeError, csv.Error) as exc:
        raise SourceError(f"FCC auction {auction} returned an unreadable {report} CSV") from exc


@functools.lru_cache(maxsize=8)
def _report(auction, report):
    if not re.fullmatch(r"[1-9][0-9]{0,5}", str(auction)):
        raise SourceError("auction_number is the FCC's number for the auction, such as 108")
    return list(_auction_csv(auction, report))


def _announcement(row):
    f = row["fields"]
    return {"id": row["id"], "url": row["url"], "auction_number": f.get("auction_id"),
            "announcement_time": (f.get("announcement_time") or "")[:19].replace(" ", "T") or None,
            "subject": f.get("subject"), "announcement": f.get("announcement")}


def _bidder(row):
    f = row["fields"]
    return {"id": row["id"], "url": row["url"], "auction_number": f.get("auction_id"), "round": to_float(f.get("round")),
            "bidder": f.get("bidder"), "frn": f.get("frn"), "bidding_credit_type": f.get("bidding_credit_type") or None,
            "eligibility": to_float(f.get("eligibility")), "activity": to_float(f.get("activity")),
            "requested_commitment": to_float(f.get("requested_commitment")), "commitment": to_float(f.get("commitment")),
            "_fields": f}


def _auction_series(report, shape):
    def search(s):
        rows = [shape(r) for a in s.any("auction_number") for r in _report(str(a), report)]
        return page([{k: v for k, v in r.items() if not k.startswith("_")} for r in rows])

    def read(key):
        for r in _report(key.split(":", 1)[0], report):
            if r["id"] == key:
                got = shape(r)
                return {**{k: v for k, v in got.items() if not k.startswith("_")},
                        "document": "\n".join(f"{k}: {v}" for k, v in r["fields"].items() if v)}
        raise SourceError("The row is not in the auction's current report")
    return search, read


# ---------- NRC ADAMS ----------

def _adams_json(url, **options):
    return _json("NRC ADAMS", url, headers={"Ocp-Apim-Subscription-Key": os.environ["NRC_ADAMS_API_KEY"]}, **options)


def _adams(doc):
    accession = doc["AccessionNumber"]
    many = lambda v: v if isinstance(v, list) else [x.strip() for x in str(v).split(",") if x.strip()] if v else []  # noqa: E731
    return {"id": accession, "accession_number": accession,
            "url": doc.get("Url") or "https://adamswebsearch2.nrc.gov/webSearch2/main.jsp?" + urlencode({"AccessionNumber": accession}),
            "title": doc.get("DocumentTitle") or doc.get("Name"), "document_type": many(doc.get("DocumentType")),
            "author_affiliation": many(doc.get("AuthorAffiliation")), "addressee_affiliation": many(doc.get("AddresseeAffiliation")),
            "docket_number": many(doc.get("DocketNumber")), "license_number": many(doc.get("LicenseNumber")),
            "document_date": (doc.get("DocumentDate") or "")[:10] or None,
            "date_added": (doc.get("DateAddedTimestamp") or doc.get("DateAdded") or "")[:10] or None}


def _search_adams(s):
    filters = [{"field": field, "value": value, "operator": "equals"}
               for field, value in (("DocketNumber", s.one("docket_number")), ("DocumentType", s.one("document_type"))) if value]
    lo, hi = s.between("date_added")
    filters += [{"field": "DateAddedTimestamp", "value": f"(DateAddedTimestamp {op} '{v}')"} for op, v in (("ge", lo), ("le", hi)) if v]
    skip = s.cursor or 0
    data = _adams_json(ADAMS, method="POST", json_body={
        "q": s.query(), "filters": filters, "anyFilters": [], "mainLibFilter": True, "legacyLibFilter": True, "content": True,
        "skip": skip, "sort": "DateAddedTimestamp", "sortDirection": 1})
    if not isinstance(data.get("results"), list) or not isinstance(data.get("count"), int):
        raise SourceError("NRC ADAMS response is missing its results or count")
    rows = [_adams(r["document"]) for r in data["results"]]
    return page(rows, skip + len(rows) if rows and skip + len(rows) < data["count"] else None, data["count"], "latest added first")


def _read_adams(accession):
    data = _adams_json(f"{ADAMS}/{accession}")
    doc = data.get("document") or {}
    if doc.get("AccessionNumber") != accession:
        raise SourceError("NRC ADAMS returned no matching document")
    content = doc.get("content") or ""
    try:
        decoded = base64.b64decode(content, validate=True).decode("utf-8")
        content = decoded if decoded and all(c.isprintable() or c.isspace() for c in decoded) else content
    except ValueError:
        pass
    record = _adams(doc)
    return {**record, "document": content, "published": record["date_added"]}


FCC = "Federal Communications Commission"
_announcements, _bidders = _auction_series("announcements", _announcement), _auction_series("bidder_status", _bidder)
SERIES = [
    Series(
        id="fcc_ecfs_filings", title="FCC filings",
        record="a filing in an FCC proceeding: a comment, reply, ex parte notice, petition or other submission",
        creator="parties to FCC proceedings and the Commission", publisher=f"{FCC} (Electronic Comment Filing System)",
        jurisdiction="federal", since="1992", coverage=_ecfs_newest, requires=("DATA_GOV_API_KEY",),
        fields=(F("submission_id", "identifier"), F("filer", "name", many=True), F("law_firm", "name", many=True),
                F("proceeding", "identifier", many=True), F("proceeding_description", "text", many=True),
                F("submission_type", "text"), F("received_date", "date"), F("disseminated_date", "date"),
                F("file_name", "text", many=True), F("full_text", "full text")),
        search=_search_ecfs, read=_read_ecfs),
    Series(
        id="fcc_auction_announcements", title="FCC auction announcements",
        record="an announcement the FCC made to bidders during a spectrum auction",
        creator=FCC, jurisdiction="federal",
        fields=(F("auction_number", "identifier", required=True), F("announcement_time", "date"), F("subject", "text"),
                F("announcement", "text")),
        search=_announcements[0], read=_announcements[1]),
    Series(
        id="fcc_auction_bidder_status", title="FCC auction bidder status",
        record="a bidder's eligibility, activity and commitments in one round of an FCC spectrum auction",
        creator=FCC, jurisdiction="federal",
        fields=(F("auction_number", "identifier", required=True), F("round", "number"), F("bidder", "name"),
                F("frn", "identifier"), F("bidding_credit_type", "text"), F("eligibility", "number"), F("activity", "number"),
                F("requested_commitment", "amount"), F("commitment", "amount")),
        search=_bidders[0], read=_bidders[1]),
    Series(
        id="nrc_adams_documents", title="the NRC's ADAMS library",
        record="a document in the Nuclear Regulatory Commission's public library: a licensing application, inspection "
               "report, order, letter or other record of a licensee's docket",
        creator="the NRC and its licensees and applicants", publisher="U.S. Nuclear Regulatory Commission",
        jurisdiction="federal", since="1999", requires=("NRC_ADAMS_API_KEY",),
        fields=(F("accession_number", "identifier"), F("title", "text"), F("document_type", "text", many=True),
                F("author_affiliation", "name", many=True), F("addressee_affiliation", "name", many=True),
                F("docket_number", "identifier", many=True), F("license_number", "identifier", many=True),
                F("document_date", "date"), F("date_added", "date"), F("full_text", "full text")),
        search=_search_adams, read=_read_adams),
]
