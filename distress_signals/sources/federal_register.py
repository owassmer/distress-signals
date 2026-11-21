"""The Federal Register (federalregister.gov): every federal rule, proposed rule, notice and presidential
document, searched by words, type, agency and dates (publication, effective, comments close).

  search()    one page of results, the most relevant first, with each document's title, agencies, action,
              dates and address; when more match than it returns, how they divide by type and by agency.
              Its read_all is a record ID for the whole result set.
  read_all()  every document a search matches, as one record that fills register_documents. The Register
              returns at most CAP documents for one query, so a larger set is read in publication-date
              windows small enough to come back whole.
"""

import datetime as dt
import re
from urllib.parse import parse_qsl, urlencode

from ..util import SourceError, http_json, today
from . import Source

BASE = "https://www.federalregister.gov/api/v1"
API = f"{BASE}/documents.json"
TYPES = {"rule": "RULE", "proposed_rule": "PRORULE", "notice": "NOTICE", "presidential_document": "PRESDOCU"}
TYPE_NAMES = {"Rule": "rule", "Proposed Rule": "proposed rule", "Notice": "notice", "Presidential Document": "presidential document"}
FIELDS = ["title", "type", "abstract", "agencies", "action", "dates", "publication_date", "effective_on",
          "comments_close_on", "html_url", "document_number"]
TABLE_FIELDS = FIELDS + ["significant", "regulation_id_numbers", "docket_ids", "cfr_references"]
DATES = {"effective_from": ("effective_date", "gte"), "effective_until": ("effective_date", "lte"),
         "comments_close_from": ("comment_date", "gte"), "comments_close_until": ("comment_date", "lte"),
         "since": ("publication_date", "gte"), "until": ("publication_date", "lte")}
PREFIX = "federal_register:search?"
CAP = 10000  # the most documents the Register returns, or counts, for one query
PAGE = 1000  # documents per request when reading a whole set
MAX_DOCUMENTS = 25000  # the most one read_all keeps
FIRST = "1994-01-01"  # the Register's documents online begin in 1994


def _list(x):
    return [x] if isinstance(x, str) else list(x or [])


def _slug(agency):
    return re.sub(r"[^a-z0-9]+", "-", agency.lower()).strip("-")


def type_name(e):
    return e.__class__.__name__


def _filters(query=None, type=None, agency=None, **dates):
    """The search, in the form its record ID keeps. Without any date, it is the last year's documents, and
    that start date is written into the filters so the record reads the same set later."""
    f = {"q": (query or "").strip() or None, "type": _list(type), "agency": _list(agency)}
    for t in f["type"]:
        if t not in TYPES:
            raise SourceError(f"federal_register: type is one of {sorted(TYPES)}")
    f.update({k: dates.get(k) for k in DATES})
    if not any(f[k] for k in DATES):
        f["since"] = (today() - dt.timedelta(days=365)).isoformat()
    return f


def _conditions(f, since=None, until=None):
    """The API's conditions for the filters; since and until replace the publication dates."""
    p = [("conditions[term]", f["q"])] if f.get("q") else []
    p += [("conditions[type][]", TYPES[t]) for t in f.get("type") or []]
    p += [("conditions[agencies][]", _slug(a)) for a in f.get("agency") or []]
    over = {"since": since, "until": until}
    for k, (key, side) in DATES.items():
        v = over[k] if k in over and over[k] else f.get(k)
        if v:
            p.append((f"conditions[{key}][{side}]", v))
    return p


def _get(url, params):
    try:
        return http_json(url, params=params, timeout=90, tries=3)
    except Exception as e:
        raise SourceError(f"Federal Register: {type_name(e)}: {str(e)[:200]}")


def record_id(f):
    """The record ID of a search's whole result set: its filters, as a query string."""
    items = [(k, v) for k, v in f.items() if v and not isinstance(v, list)] + [(k, x) for k, v in f.items() if isinstance(v, list) for x in v]
    return PREFIX + urlencode(sorted(items))


def _parse(rid):
    if not rid.startswith(PREFIX):
        raise SourceError(f"not a Federal Register search: {rid[:80]}")
    f = {"q": None, "type": [], "agency": [], **{k: None for k in DATES}}
    for k, v in parse_qsl(rid[len(PREFIX):]):
        if k in ("type", "agency"):
            f[k].append(v)
        elif k in f:
            f[k] = v
        else:
            raise SourceError(f"federal_register search: unknown filter {k!r}")
    for t in f["type"]:
        if t not in TYPES:
            raise SourceError(f"federal_register: type is one of {sorted(TYPES)}")
    return f


def describe(f):
    """The search in words: 'rules matching “hemp”, from the Agriculture Department, effective ...'."""
    kinds = " and ".join({"rule": "rules", "proposed_rule": "proposed rules", "notice": "notices",
                          "presidential_document": "presidential documents"}[t] for t in f.get("type") or []) or "documents"
    parts = [kinds + (f" matching “{f['q']}”" if f.get("q") else "")]
    if f.get("agency"):
        parts.append("from " + " or ".join(f["agency"]))
    for label, lo, hi in (("effective", "effective_from", "effective_until"), ("comments closing", "comments_close_from",
                          "comments_close_until"), ("published", "since", "until")):
        if f.get(lo) or f.get(hi):
            parts.append(f"{label} " + (f"{f[lo]} to {f[hi]}" if f.get(lo) and f.get(hi) else
                                        f"from {f[lo]}" if f.get(lo) else f"through {f[hi]}"))
    return ", ".join(parts)


def _facets(f):
    """How the matches divide by document type and by agency (a document can name several agencies)."""
    out = {}
    for facet, key in (("type", "by_type"), ("agency", "by_agency")):
        j = _get(f"{BASE}/documents/facets/{facet}", _conditions(f))
        top = sorted(((v.get("name") or k, v.get("count") or 0) for k, v in (j or {}).items()), key=lambda x: -x[1])
        out[key] = dict(top[:12])
    return out


def search(query=None, type=None, agency=None, effective_from=None, effective_until=None, comments_close_from=None,
           comments_close_until=None, since=None, until=None, max_results=50):
    f = _filters(query, type, agency, effective_from=effective_from, effective_until=effective_until,
                 comments_close_from=comments_close_from, comments_close_until=comments_close_until, since=since, until=until)
    params = [("per_page", min(int(max_results or 50), 200)), ("order", "relevance" if f["q"] else "newest")]
    params += [("fields[]", x) for x in FIELDS] + _conditions(f)
    j = _get(API, params)
    rows = [{"record_id": d.get("html_url"), "title": d.get("title"), "type": d.get("type"),
             "agencies": [a.get("name") or a.get("raw_name") for a in d.get("agencies") or []], "action": d.get("action"),
             "published": d.get("publication_date"), "effective": d.get("effective_on"),
             "comments_close": d.get("comments_close_on"), "dates": d.get("dates"),
             "abstract": (d.get("abstract") or "")[:700] or None, "document_number": d.get("document_number")}
            for d in j.get("results") or []]
    count = j.get("count") or 0
    out = {"results": rows, "total_matches": count, "read_all": record_id(f)}
    if count >= CAP:
        out["total_matches_note"] = (f"The Federal Register counts at most {CAP:,} matches for one search, so there may "
                                     "be more; reading read_all counts and reads them all.")
    if count > len(rows):
        out.update(_facets(f))
    return out


def _count(f, since, until):
    return (_get(API, [("per_page", 1), ("fields[]", "document_number")] + _conditions(f, since, until)).get("count") or 0)


def _windows(f, since, until):
    """Publication-date windows covering since..until, each with fewer than CAP matches: [(since, until, count)]."""
    n = _count(f, since, until)
    if n < CAP:
        return [(since, until, n)] if n else []
    a, b = dt.date.fromisoformat(since), dt.date.fromisoformat(until)
    if a >= b:
        raise SourceError(f"Federal Register: {n:,} documents on {since} alone; narrow the search")
    mid = a + (b - a) // 2
    return _windows(f, since, mid.isoformat()) + _windows(f, (mid + dt.timedelta(days=1)).isoformat(), until)


def _row(d):
    return {"document_number": d.get("document_number"), "type": TYPE_NAMES.get(d.get("type"), d.get("type")),
            "title": d.get("title"), "agencies": "; ".join(a.get("name") or a.get("raw_name") or "" for a in d.get("agencies") or []),
            "action": d.get("action"), "publication_date": d.get("publication_date"), "effective_on": d.get("effective_on"),
            "comments_close_on": d.get("comments_close_on"),
            "significant": None if d.get("significant") is None else int(bool(d.get("significant"))),
            "rins": ", ".join(d.get("regulation_id_numbers") or []) or None,
            "docket_ids": ", ".join(d.get("docket_ids") or []) or None,
            "cfr": "; ".join(f"{c.get('title')} CFR {c.get('part')}" for c in d.get("cfr_references") or [] if c.get("part")) or None,
            "dates": d.get("dates"), "abstract": d.get("abstract"), "url": d.get("html_url")}


def read_all(rid):
    """The observation for a search's whole result set: every matching document, one line each in its text and
    one row each in register_documents."""
    f = _parse(rid)
    lo, hi = f.get("since") or FIRST, f.get("until") or today().isoformat()
    windows = _windows(f, lo, hi)
    total = sum(n for _, _, n in windows)
    if total > MAX_DOCUMENTS:
        raise SourceError(f"This search matches {total:,} Federal Register documents ({describe(f)}); one read keeps "
                          f"up to {MAX_DOCUMENTS:,}. Narrow it by type, agency, words or dates.")
    rows, seen = [], set()
    for a, b, n in windows:
        for page in range(1, -(-n // PAGE) + 1):
            j = _get(API, [("per_page", PAGE), ("page", page), ("order", "oldest")] + [("fields[]", x) for x in TABLE_FIELDS]
                     + _conditions(f, a, b))
            for d in j.get("results") or []:
                if d.get("document_number") not in seen:
                    seen.add(d.get("document_number"))
                    rows.append(_row(d))
    rows.sort(key=lambda r: (r["publication_date"] or "", r["document_number"] or ""))
    what = describe(f)
    kinds, agencies = {}, {}
    for r in rows:
        kinds[r["type"]] = kinds.get(r["type"], 0) + 1
        for a in filter(None, (r["agencies"] or "").split("; ")):
            agencies[a] = agencies.get(a, 0) + 1
    top = sorted(agencies.items(), key=lambda x: -x[1])[:12]
    lines = [f"Federal Register: {len(rows):,} documents, {what}.",
             "By type: " + (", ".join(f"{k} {v:,}" for k, v in sorted(kinds.items(), key=lambda x: -x[1])) or "none") + ".",
             "By agency (a document can name several): " + (", ".join(f"{k} {v:,}" for k, v in top) or "none") + ".", "",
             "Each line: published | type | effective | comments close | agencies | title | address"]
    lines += [f"{r['publication_date']} | {r['type']} | {r['effective_on'] or '-'} | {r['comments_close_on'] or '-'} | "
              f"{r['agencies']} | {r['title']} | {r['url']}" for r in rows]
    web = "https://www.federalregister.gov/documents/search?" + urlencode(_conditions(f))
    return {"kind": "register_search", "source": "federal_register", "native_id": rid, "url": web, "published": None,
            "text": "\n".join(lines), "rows": {"register_documents": rows},
            "data": {"title": f"Federal Register search: {len(rows):,} {what}", "filters": f, "count": len(rows),
                     "windows": [[a, b, n] for a, b, n in windows]}}


def _label(q, f):
    when = " taking effect " + " to ".join(x for x in (f.get("effective_from"), f.get("effective_until")) if x) \
        if f.get("effective_from") or f.get("effective_until") else ""
    kind = {"rule": "rules", "proposed_rule": "proposed rules", "notice": "notices"}.get(f.get("type"), "documents")
    return f"Searched the Federal Register for {kind}{when}" + (f" matching “{q}”" if q else "")


SOURCE = Source(
    id="federal_register",
    publisher="Office of the Federal Register (federalregister.gov)",
    record="one rule, proposed rule, notice or presidential document; or, through read_all, every document a search matches.",
    names="the issuing agencies.",
    dates="publication, effective, comments close, and the document's own DATES section.",
    search="query is words in the document. Filters: type (rule, proposed_rule, notice or presidential_document), agency "
           "(its name), effective_from and effective_until, comments_close_from and comments_close_until, since and until "
           "(publication dates; without any dates, the last year), max_results (the default is 50, at most 200). When more "
           "match than are returned, the result gives how they divide by type and by agency. read_all is a record_id for "
           "every match.",
    read=f"a document's text. A read_all returns every match, up to {MAX_DOCUMENTS:,}, one line each, and fills register_documents.",
    coverage="1994 to today.",
    filters=frozenset({"type", "agency", "effective_from", "effective_until", "comments_close_from", "comments_close_until",
                       "since", "until", "max_results"}),
    find=lambda query, filters: search(query or None, **filters),
    label=_label,
    locate=lambda ref: (("federal_register", ref), lambda: read_all(ref)) if ref.startswith(PREFIX) else None,
    again=lambda obs: obs["native_id"] if obs["source"] == "federal_register" and obs["kind"] == "register_search"
    and obs.get("native_id") else None,
    noun="every Federal Register document matching its search",
    kind="register_search", tables=("register_documents",),
)
