"""The Federal Register (federalregister.gov): every federal rule, proposed rule, notice and presidential
document, searched by words, type, agency and dates (publication, effective, comments close). Each result
gives the document's title, agencies, action, dates and address; read_record reads it."""

import datetime as dt
import re

from ..util import SourceError, http_json, today

API = "https://www.federalregister.gov/api/v1/documents.json"
TYPES = {"rule": "RULE", "proposed_rule": "PRORULE", "notice": "NOTICE", "presidential_document": "PRESDOCU"}
FIELDS = ["title", "type", "abstract", "agencies", "action", "dates", "publication_date", "effective_on",
          "comments_close_on", "html_url", "document_number"]


def _list(x):
    return [x] if isinstance(x, str) else list(x or [])


def _slug(agency):
    return re.sub(r"[^a-z0-9]+", "-", agency.lower()).strip("-")


def search(query=None, type=None, agency=None, effective_from=None, effective_until=None, comments_close_from=None,
           comments_close_until=None, since=None, until=None, max_results=50):
    params = [("per_page", min(int(max_results or 50), 200)), ("order", "relevance" if query else "newest")]
    params += [("fields[]", f) for f in FIELDS]
    if query:
        params.append(("conditions[term]", query))
    for t in _list(type):
        if t not in TYPES:
            raise SourceError(f"federal_register: type is one of {sorted(TYPES)}")
        params.append(("conditions[type][]", TYPES[t]))
    params += [("conditions[agencies][]", _slug(a)) for a in _list(agency)]
    dated = False
    for key, lo, hi in (("effective_date", effective_from, effective_until), ("comment_date", comments_close_from, comments_close_until),
                        ("publication_date", since, until)):
        if lo:
            params.append((f"conditions[{key}][gte]", lo))
            dated = True
        if hi:
            params.append((f"conditions[{key}][lte]", hi))
            dated = True
    if not dated:  # without dates, the last year's documents
        params.append(("conditions[publication_date][gte]", (today() - dt.timedelta(days=365)).isoformat()))
    try:
        j = http_json(API, params=params, timeout=60, tries=3)
    except Exception as e:
        raise SourceError(f"Federal Register: {type_name(e)}: {str(e)[:200]}")
    rows = [{"record_id": d.get("html_url"), "title": d.get("title"), "type": d.get("type"),
             "agencies": [a.get("name") or a.get("raw_name") for a in d.get("agencies") or []], "action": d.get("action"),
             "published": d.get("publication_date"), "effective": d.get("effective_on"),
             "comments_close": d.get("comments_close_on"), "dates": d.get("dates"),
             "abstract": (d.get("abstract") or "")[:700] or None, "document_number": d.get("document_number")}
            for d in j.get("results") or []]
    return {"results": rows, "total_matches": j.get("count")}


def type_name(e):
    return e.__class__.__name__
