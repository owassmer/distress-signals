"""California CDIAC issuance reports and primary documents for municipal issuer research."""

import re

from ..util import SourceError, http_json

SITE = "https://debtwatch.treasurer.ca.gov"
PUBLISHER = "California Debt and Investment Advisory Commission (CDIAC)"
NUMBER = re.compile(r"\d{4}-\d{4}")
FIELDS = {
    "CDIACNumber": "CDIAC number", "Issuer": "Issuer", "SoldStatus": "Sale status",
    "IssueName": "Issue name", "ProjectSeriesOrName": "Project or series", "SaleDate": "Sale date",
    "ClosingOrSettlementDate": "Closing or settlement date", "FirstOptionalCallDate": "First optional call date",
    "FinalMaturityDate": "Final maturity date", "PrincipalAmount": "Principal amount",
    "SourceofRepayment": "Source of repayment", "DebtType": "Debt type", "PrimaryPurpose": "Primary purpose",
    "LeadUnderwriter": "Lead underwriter", "TrusteeOrPayingAgent": "Trustee or paying agent",
    "PurchaserOrLender": "Purchaser or lender",
}


def _search(dataset, *, number=None, query="", page=1, size=50):
    body = {"filters": {"CDIACNumber": {"type": "StringOneOf", "options": [number]}} if number else {},
            "pagination": {"pageNumber": page, "pageSize": size},
            "sorting": {"columnId": "CDIACNumber", "ascending": False}, "searchTerm": query or None}
    data = http_json(f"{SITE}/api/dataset/{dataset}/search", method="PUT", json_body=body, tries=2, timeout=45)
    if not isinstance(data, dict) or not isinstance(data.get("records"), list):
        raise SourceError("California DebtWatch did not return a record list.")
    return data


def _url(number):
    return f"{SITE}/issue-level-detail/issues?cdiacNumber={number}"


def _row(row):
    number = row["CDIACNumber"]
    return {"record_id": f"emma:california:{number}", "title": row.get("IssueName") or row.get("Issuer"),
            "issuer": row.get("Issuer"), "sale_status": row.get("SoldStatus"), "sale_date": row.get("SaleDate"),
            "final_maturity_date": row.get("FinalMaturityDate"), "publisher": PUBLISHER, "url": _url(number)}


def search(query, limit=25, offset=0):
    if type(limit) is not int or not 1 <= limit <= 200 or type(offset) is not int or offset < 0:
        raise SourceError("California DebtWatch accepts an integer limit of 1–200 and a nonnegative integer offset.")
    size = 50
    page, skip = divmod(offset, size)
    results = []
    while len(results) < limit:
        page += 1
        data = _search("issues", query=query, page=page, size=size)
        rows = data["records"]
        results.extend(_row(row) for row in rows[skip:skip + limit - len(results)])
        skip = 0
        total = data["totalMatchingRecords"]
        if page * size >= total or not rows:
            break
    end = offset + len(results)
    return {"results": results, "total_matches": total, "complete": end >= total,
            "next_offset": end if end < total else None, "publisher": PUBLISHER}


def read(native_id):
    number = native_id.removeprefix("california:")
    if not NUMBER.fullmatch(number):
        raise SourceError("Expected a California DebtWatch CDIAC number in YYYY-NNNN form.")
    matches = _search("issues", number=number, size=1)["records"]
    if not matches or matches[0].get("CDIACNumber") != number:
        raise SourceError(f"California DebtWatch has no issue with CDIAC number {number}.")
    issue = matches[0]
    fields = {label: issue[key] for key, label in FIELDS.items() if issue.get(key) not in ("(none)", "", None)}
    docs = []
    page = 1
    while True:
        response = _search("documents", number=number, page=page)
        for row in response["records"]:
            if row.get("DocumentId") and row.get("CDIACNumber") == number:
                docs.append({"type": row.get("DocumentType"), "filename": row.get("DocumentFileName"),
                             "url": f"{SITE}/api/document/{row['DocumentId']}/download?inline=true"})
        if page * 50 >= response["totalMatchingRecords"] or not response["records"]:
            break
        page += 1
    title = _row(issue)["title"]
    lines = [PUBLISHER, *(f"{label}: {value}" for label, value in fields.items()),
             *(f"{doc['type']}: {doc['filename']} — {doc['url']}" for doc in docs)]
    return {"kind": "record", "source": "emma", "native_id": native_id, "url": _url(number),
            "published": None, "text": "\n".join(lines),
            "data": {"title": title, "publisher": PUBLISHER, **fields, "documents": docs}}
