"""Three series from the MSRB's EMMA subscriptions: continuing disclosures, and the official statements and advance
refunding documents of the primary market.

The MSRB delivers each subscription as zip files of XML transactions ("Index Data") and the PDFs they describe
("Doc"). MSRB_CONTINUING_DISCLOSURE_DIR and MSRB_PRIMARY_MARKET_DIR are the directories the deliveries are put
in, zipped as delivered or unpacked; they are read where they lie, and read again when a file in them changes.
Each transaction replaces its submission whole, so the latest transaction for a submission stands: a withdrawn
submission, or a document or file no longer current in it, is no longer a record.
"""

import datetime as dt
import functools
import os
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile

from ..util import SourceError
from . import F, Series, outline, page, web

CONTINUING = "MSRB_CONTINUING_DISCLOSURE_DIR"
PRIMARY = "MSRB_PRIMARY_MARKET_DIR"
EMMA = "https://emma.msrb.org"
MSRB = "Municipal Securities Rulemaking Board (EMMA)"


def _tag(e):
    return e.tag.rpartition("}")[2]


def _files(setting):
    """Every file in the delivery directory, as (path, modified, size)."""
    root = Path(os.environ[setting]).resolve()
    if not root.is_dir():
        raise SourceError(f"{setting} is not a directory")
    return tuple(sorted((p, p.stat().st_mtime_ns, p.stat().st_size) for p in root.rglob("*")
                        if p.is_file() and p.suffix.casefold() in (".zip", ".xml", ".pdf")))


@functools.lru_cache(maxsize=2)
def _delivery(files):
    """(the submissions that stand, {file ID: where its PDF is}) in a delivery: each submission's latest transaction,
    and each PDF as (path) or (zip path, member)."""
    latest, pdfs = {}, {}

    def transactions(source, content):
        try:
            root = ET.fromstring(content)
        except ET.ParseError as e:
            raise SourceError(f"The MSRB index file {source} could not be read: {e}")
        for sub in root:
            if _tag(sub) not in ("Submission", "ContinuingDisclosureSubmission"):
                continue
            sid = sub.attrib["SubmissionIdentifier"]
            stamp = dt.datetime.fromisoformat(sub.attrib["SubmissionTransactionDateTime"])
            if sid not in latest or stamp > latest[sid][0]:
                latest[sid] = stamp, sub

    for path, _, _ in files:
        suffix = path.suffix.casefold()
        if suffix == ".xml" and not path.name.casefold().endswith(".xsd"):
            transactions(path.name, path.read_bytes())
        elif suffix == ".pdf":
            pdfs[path.stem] = (path,)
        elif suffix == ".zip":
            try:
                with ZipFile(path) as z:
                    for member in z.namelist():
                        name = PurePosixPath(member)
                        if name.suffix.casefold() == ".xml":
                            transactions(f"{path.name}:{member}", z.read(member))
                        elif name.suffix.casefold() == ".pdf":
                            pdfs[name.stem] = (path, member)
            except BadZipFile as e:
                raise SourceError(f"The MSRB delivery file {path.name} is not a whole zip file: {e}")
    return {sid: sub for sid, (_, sub) in latest.items() if sub.get("SubmissionStatus") == "Publish"}, pdfs


def _current_files(sub):
    """(document, file) for each current file of each current document of a submission."""
    for doc in sub.findall("{*}Document"):
        if doc.get("DocumentStatus") == "Current":
            for f in doc.findall(".//{*}File"):
                if f.get("FileStatus") == "Current":
                    yield doc, f


def _day(value):
    return (value or "")[:10] or None


def _pdf(where):
    if len(where) == 1:
        content = where[0].read_bytes()
    else:
        with ZipFile(where[0]) as z:
            content = z.read(where[1])
    if len(content) > web.MAX_BYTES or not content.startswith(b"%PDF-"):
        raise SourceError("The file is not a PDF within the size a read takes")
    return web._pdf(content)[0]


def _plain(row):
    return {k: v for k, v in row.items() if not k.startswith("_")}


@functools.lru_cache(maxsize=6)
def _rows(records, files):
    """A series' records in a reading of its delivery, latest posted first."""
    subs, _ = _delivery(files)
    out = [r for sid, sub in subs.items() for r in records(sid, sub)]
    return sorted(out, key=lambda r: (r["posting_date"] or "", r["id"]), reverse=True)


def _series_of(setting, records):
    """search, read and coverage for a series whose records come from a delivery."""
    def search(s):
        return page([_plain(r) for r in _rows(records, _files(setting))], order="latest posted first")

    def read(key):
        files = _files(setting)
        row = next((r for r in _rows(records, files) if r["id"] == key), None)
        if not row:
            raise SourceError("This file is not current in the latest transaction of its submission in the delivery")
        pdfs = _delivery(files)[1]
        lines = outline(row["_stated"]) + ["", _pdf(pdfs[row["_file"]]) if row["_file"] in pdfs else "The delivery does not hold this file's PDF."]
        return {**_plain(row), "document": "\n".join(lines), "published": row["posting_date"]}

    def coverage(series):
        dates = [r["posting_date"] for r in _rows(records, _files(setting)) if r["posting_date"]]
        return (min(dates), max(dates)) if dates else (None, None)
    return search, read, coverage


def _page(cusips):
    """EMMA's page for a security, where its documents are viewed: the MSRB gives its address by CUSIP."""
    return f"{EMMA}/Security/Details/{cusips[0]}" if cusips else None


# ---------- continuing disclosures ----------

def _disclosures(sid, sub):
    if _tag(sub) != "ContinuingDisclosureSubmission":
        return []
    securities = [x.attrib for x in sub.findall(".//{*}Security")]
    contacts = [c.attrib for c in sub.findall("{*}ContactInformation")]
    issuers = {e.get("IssuerName") for e in sub.iter() if e.get("IssuerName")}
    issuers |= {c["ContactOrganizationName"] for c in contacts if c.get("ContactType") == "Issuer" and c.get("ContactOrganizationName")}
    obligors = {c["ContactOrganizationName"] for c in contacts if c.get("ContactType") == "Obligor" and c.get("ContactOrganizationName")}
    disclosures = [{**d.attrib, "periods": [p.attrib for p in d.findall("{*}DisclosurePeriod")],
                    "subcategories": [c.get("Name") for c in d.findall("{*}DisclosureSubCategory")]}
                   for d in sub.findall(".//{*}Disclosure")]
    out = []
    for doc, f in _current_files(sub):
        out.append({"id": f"{sid}/{doc.attrib['DocumentIdentifier']}/{f.attrib['FileIdentifier']}",
                    "url": _page(sorted({x["CUSIP9"] for x in securities if x.get("CUSIP9")})),
                    "submission_id": sid, "issuer": sorted(issuers), "obligor": sorted(obligors),
                    "disclosure_type": list(dict.fromkeys(d.get("DisclosureType") for d in disclosures if d.get("DisclosureType"))),
                    "category": list(dict.fromkeys(v for d in disclosures for k, v in d.items() if k.endswith("Category"))),
                    "as_of_date": sorted({_day(p.get("AsOfDate")) for d in disclosures for p in d["periods"] if p.get("AsOfDate")}),
                    "cusip": sorted({x["CUSIP9"] for x in securities if x.get("CUSIP9")}),
                    "posting_date": _day(f.get("FilePostingDate") or doc.get("DocumentPostingDate")), "_file": f.attrib["FileIdentifier"],
                    "_stated": {"disclosures": disclosures, "securities": securities, "transaction": sub.get("SubmissionTransactionDateTime")}})
    return out


# ---------- the primary market ----------

def _issues(sub):
    return [i for i in sub.iter() if _tag(i) == "Issue"]


def _securities(sub):
    return [x.attrib for x in sub.iter() if _tag(x) == "Security"]


def _offering(sid, sub, kind):
    """The current files of a primary-market submission of one kind."""
    if _tag(sub) != "Submission" or sub.get("SubmissionType") != kind:
        return []
    issues, securities = _issues(sub), _securities(sub)
    principal = [float(x["MaturityPrincipalAmount"]) for x in securities if x.get("MaturityPrincipalAmount")]
    common = {"submission_id": sid,
              "issuer": sorted({" ".join(i.get("IssuerName").split()) for i in issues if i.get("IssuerName")}),
              "state": sorted({i.get("IssuerState") for i in issues if i.get("IssuerState")}),
              "issue": [" ".join(i.get("IssueDescription").split()) for i in issues if i.get("IssueDescription")],
              "cusip": sorted({x["CUSIP9"] for x in securities if x.get("CUSIP9")}),
              "refunded_cusip": sorted({x["OriginalCUSIP9OfRefundedSecurity"] for x in securities if x.get("OriginalCUSIP9OfRefundedSecurity")}),
              "dated_date": sorted({_day(i.get("IssueDatedDate")) for i in issues if i.get("IssueDatedDate")}),
              "closing_date": sorted({_day(i.get("IssueClosingDate")) for i in issues if i.get("IssueClosingDate")}),
              "final_maturity_date": max((_day(x.get("MaturityDate")) for x in securities if x.get("MaturityDate")), default=None),
              "principal_amount": sum(principal) if principal else None}
    stated = {"submission": {k: v for k, v in sub.attrib.items()},
              "issues": [{**i.attrib, "securities": [x.attrib for x in i.iter() if _tag(x) == "Security"]} for i in issues]}
    out = []
    for doc, f in _current_files(sub):
        out.append({"id": f"{sid}/{doc.attrib['DocumentIdentifier']}/{f.attrib['FileIdentifier']}",
                    "url": _page(common["cusip"] or common["refunded_cusip"]), **common, "document_type": doc.get("DocumentType"), "description": doc.get("DocumentDescription"),
                    "posting_date": _day(f.get("FilePostingDate") or doc.get("DocumentPostingDate")), "_file": f.attrib["FileIdentifier"],
                    "_stated": stated})
    return out


def _official_statements(sid, sub):
    return [{k: v for k, v in r.items() if k != "refunded_cusip"} for r in _offering(sid, sub, "PrimaryMarketDisclosure")]


def _advance_refundings(sid, sub):
    keep = ("id", "url", "submission_id", "cusip", "refunded_cusip", "description", "posting_date", "_file", "_stated")
    return [{k: r[k] for k in keep} for r in _offering(sid, sub, "AdvanceRefundingDisclosure")]


_cd_search, _cd_read, _cd_span = _series_of(CONTINUING, _disclosures)
_os_search, _os_read, _os_span = _series_of(PRIMARY, _official_statements)
_ar_search, _ar_read, _ar_span = _series_of(PRIMARY, _advance_refundings)

MUNICIPAL = "U.S. states and local governments"
SERIES = [
    Series(
        id="emma_continuing_disclosures", title="EMMA continuing disclosures",
        record="a file of a continuing disclosure an issuer or obligated person submitted to EMMA: annual financial information, "
               "audited statements, or notice of an event affecting the bonds",
        creator="issuers of municipal securities and the obligated persons behind them", publisher=MSRB, jurisdiction=MUNICIPAL,
        coverage=_cd_span, requires=(CONTINUING,),
        fields=(F("submission_id", "identifier"), F("issuer", "name", many=True), F("obligor", "name", many=True),
                F("disclosure_type", "text", many=True), F("category", "text", many=True), F("as_of_date", "date", many=True),
                F("cusip", "identifier", many=True), F("posting_date", "date")),
        search=_cd_search, read=_cd_read),
    Series(
        id="emma_official_statements", title="EMMA official statements",
        record="an official statement for a new issue of municipal securities, as submitted to EMMA, with the issue's "
               "securities and their maturities",
        creator="issuers of municipal securities and their underwriters", publisher=MSRB, jurisdiction=MUNICIPAL,
        coverage=_os_span, requires=(PRIMARY,),
        fields=(F("submission_id", "identifier"), F("issuer", "name", many=True), F("state", "place", many=True),
                F("issue", "text", many=True), F("cusip", "identifier", many=True), F("dated_date", "date", many=True),
                F("closing_date", "date", many=True), F("final_maturity_date", "date"), F("principal_amount", "amount"),
                F("document_type", "text"), F("description", "text"), F("posting_date", "date")),
        search=_os_search, read=_os_read),
    Series(
        id="emma_advance_refunding_documents", title="EMMA advance refunding documents",
        record="a document submitted to EMMA for an advance refunding: the escrow for outstanding municipal securities to be "
               "paid off before they can be called, with the securities refunded",
        creator="issuers of municipal securities and their agents", publisher=MSRB, jurisdiction=MUNICIPAL,
        coverage=_ar_span, requires=(PRIMARY,),
        fields=(F("submission_id", "identifier"), F("refunded_cusip", "identifier", many=True), F("cusip", "identifier", many=True),
                F("description", "text"), F("posting_date", "date")),
        search=_ar_search, read=_ar_read),
]
