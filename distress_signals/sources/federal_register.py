"""The Federal Register (federalregister.gov): every rule, proposed rule, notice and presidential document
since 1994.

The Register returns, and counts, at most 10,000 documents for one query. A search by words comes back by
relevance and stops there. A search without words, and any search read to its end, comes back newest first
and continues by publication date, which has no such limit; its count then comes from the Register's count
of documents by type, which is not capped.
"""

import functools
import html
import re

from .. import names
from ..util import SourceError, http_json
from . import F, Series, page, web

BASE = "https://www.federalregister.gov/api/v1"
TYPES = {"Rule": "RULE", "Proposed Rule": "PRORULE", "Notice": "NOTICE", "Presidential Document": "PRESDOCU"}
FIELDS = ["title", "type", "abstract", "agencies", "action", "dates", "publication_date", "effective_on",
          "comments_close_on", "html_url", "document_number", "significant", "regulation_id_numbers", "docket_ids",
          "cfr_references"]
CAP = 10000  # the most documents the Register returns, or counts, for one query
PER = 1000   # the most it returns at once


def _get(path, params=None):
    try:
        return http_json(f"{BASE}/{path}", params=params, timeout=90, tries=3)
    except Exception as e:
        raise SourceError(f"Federal Register: {type(e).__name__}: {str(e)[:200]}")


@functools.lru_cache(maxsize=1)
def _agencies():
    """[(slug, the words of its name, its short name)] for every agency the Register lists."""
    return [(a["slug"], set(names.tokens(a.get("name") or "")), (a.get("short_name") or "").casefold())
            for a in _get("agencies") if a.get("slug")]


def _slugs(given):
    """The agencies whose name holds every word given, or whose short name is what was given."""
    want = set(names.tokens(given))
    return [slug for slug, words, short in _agencies() if want and (want <= words or given.casefold() == short)]


def _record(d):
    return {"id": d.get("document_number"), "url": d.get("html_url"),
            "agency": [a.get("name") or a.get("raw_name") for a in d.get("agencies") or [] if a.get("name") or a.get("raw_name")],
            "type": d.get("type"), "publication_date": d.get("publication_date"), "effective_date": d.get("effective_on"),
            "comments_close": d.get("comments_close_on"), "document_number": d.get("document_number"),
            "docket_id": d.get("docket_ids") or [], "rin": d.get("regulation_id_numbers") or [],
            "cfr": [f"{c['title']} CFR {c['part']}" for c in d.get("cfr_references") or [] if c.get("title") and c.get("part")],
            "significant": None if d.get("significant") is None else "yes" if d["significant"] else "no",
            "action": d.get("action"), "title": d.get("title"), "abstract": d.get("abstract"), "dates": d.get("dates")}


def _search(s):
    numbers = s.any("document_number")
    if numbers:
        got = _get(f"documents/{','.join(numbers)}.json", [("fields[]", x) for x in FIELDS])
        return page([_record(d) for d in (got.get("results") if "results" in got else [got])])
    cond = []
    for kind in s.any("type"):
        cond.append(("conditions[type][]", TYPES[kind]))
    agencies = s.any("agency")
    if agencies:
        slugs = sorted({slug for a in agencies for slug in _slugs(a)})
        if not slugs:
            return page([], total=0)
        cond += [("conditions[agencies][]", slug) for slug in slugs]
    for field, key in (("effective_date", "effective_date"), ("comments_close", "comment_date")):
        lo, hi = s.between(field)
        cond += [(f"conditions[{key}][gte]", lo)] if lo else []
        cond += [(f"conditions[{key}][lte]", hi)] if hi else []
    docket, rin = s.one("docket_id"), s.one("rin")
    cond += [("conditions[docket_id]", docket)] if docket else []
    cond += [("conditions[regulation_id_number]", rin)] if rin else []
    significant = s.one("significant")
    cond += [("conditions[significant]", "1" if significant == "yes" else "0")] if significant else []
    part = re.fullmatch(r"(\d+)\s*CFR\s*(\d+)", (s.peek("cfr").get("is") or [""])[0].strip(), re.I)
    if part and len(s.peek("cfr")["is"]) == 1:
        s.any("cfr")
        cond += [("conditions[cfr][title]", part[1]), ("conditions[cfr][part]", part[2])]
    # Words given for one text field narrow the Register's full-text search; the registry then checks the field.
    words = s.query()
    term = " ".join([words] + [w for f in ("action", "title", "abstract", "dates") for w in s.peek(f).get("is", [])[:1]]).strip()
    cond += [("conditions[term]", term)] if term else []
    lo, hi = s.between("publication_date")
    cond += [("conditions[publication_date][gte]", lo)] if lo else []
    fields = [("fields[]", x) for x in FIELDS]

    if words and not s.everything:  # by relevance, page by page, as far as the Register goes
        at = s.cursor or {"page": 1, "per": max(1, min(s.size, PER))}
        got = _get("documents.json", [("per_page", at["per"]), ("page", at["page"]), ("order", "relevance")] + fields + cond
                   + ([("conditions[publication_date][lte]", hi)] if hi else []))
        docs = got.get("results") or []
        more = len(docs) == at["per"] and (at["page"] + 1) * at["per"] <= CAP
        return page([_record(d) for d in docs], {"page": at["page"] + 1, "per": at["per"]} if more else None,
                    _total(got, cond, hi), "most relevant first")

    # newest first, continuing from a date: the documents of that date already returned are skipped
    at = s.cursor or {"until": hi, "skip": 0}
    per = min(PER, at["skip"] + max(1, s.size))
    got = _get("documents.json", [("per_page", per), ("page", 1), ("order", "newest")] + fields + cond
               + ([("conditions[publication_date][lte]", at["until"])] if at["until"] else []))
    docs = (got.get("results") or [])
    fresh = docs[at["skip"]:]
    if len(docs) < per or not fresh:
        nxt = None
    else:
        last = fresh[-1]["publication_date"]
        same = sum(1 for d in fresh if d["publication_date"] == last)
        nxt = {"until": last, "skip": same + (at["skip"] if last == at["until"] else 0)}
    return page([_record(d) for d in fresh], nxt, _total(got, cond, hi) if s.cursor is None else None, "newest first")


def _total(got, cond, hi):
    """The count of matching documents: the Register's own, or past its cap the sum of its counts by type."""
    count = got.get("count") or 0
    if count < CAP:
        return count
    by_type = _get("documents/facets/type", cond + ([("conditions[publication_date][lte]", hi)] if hi else []))
    return sum(v.get("count") or 0 for v in (by_type or {}).values())


def _read(number):
    d = _get(f"documents/{number}.json")
    record = _record(d)
    body, links = "", []
    if d.get("body_html_url"):
        got = web.read(d["body_html_url"])
        body, links = got["text"], got["data"].get("links") or []
    elif d.get("raw_text_url"):
        body = html.unescape(web.read(d["raw_text_url"])["text"])
    return {**record, "document": body, "links": links, "published": d.get("publication_date")}


def _coverage(series):
    def edge(order):
        got = _get("documents.json", [("per_page", 1), ("order", order), ("fields[]", "publication_date")])
        return (got.get("results") or [{}])[0].get("publication_date")
    return edge("oldest"), edge("newest")


SERIES = [Series(
    id="federal_register",
    title="the Federal Register",
    record="a rule, proposed rule, notice or presidential document",
    creator="federal agencies and the President",
    publisher="Office of the Federal Register",
    jurisdiction="federal",
    since="1994",
    coverage=_coverage,
    fields=(F("agency", "name", many=True), F("type", tuple(TYPES)), F("publication_date", "date"),
            F("effective_date", "date"), F("comments_close", "date"), F("document_number", "identifier"),
            F("docket_id", "identifier", many=True), F("rin", "identifier", many=True), F("cfr", "identifier", many=True),
            F("significant", ("yes", "no")), F("action", "text"), F("title", "text"), F("abstract", "text"),
            F("dates", "text"), F("full_text", "full text")),
    search=_search, read=_read)]
