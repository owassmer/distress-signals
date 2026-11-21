"""Four series: federal court dockets and their entries (the RECAP archive), the Supreme Court's docket, and
GAO bid protests.

  federal_court_dockets,          RECAP holds the dockets and filings its users have bought from PACER, in two copies.
  federal_court_docket_entries    Free Law Project deposits them at the Internet Archive, searched by caption, court,
                                  PACER case number and the date deposited, without limit; a docket's entries are read
                                  from its deposited docket sheet. With a CourtListener token (COURTLISTENER_TOKEN),
                                  searches by words (the text of filings), docket number or filing dates go to
                                  CourtListener's copy, which also holds dockets known only from PACER's feeds, and a
                                  docket the Internet Archive lacks is read there. A free CourtListener account may make
                                  5 requests a minute and 125 a day; a search refused for that says so.
  supreme_court_dockets           the Court publishes each docket as a JSON file. Its own search form finds cases by
                                  words. An open search walks the current term's three sequences of case numbers
                                  (paid, in forma pauperis, applications) downward and merges them by docketed date.
  gao_bid_protests                GAO's own docket pages refuse scripted readers, so protests are read from GovCon
                                  API's index of them, which needs a key (GOVCON_API_KEY).
"""

import datetime as dt
import json
import os
import re
import time
from urllib.parse import parse_qs, urljoin, urlsplit

import requests
from bs4 import BeautifulSoup

from ..util import OUT, Missing, SourceError, Throttled, Unreadable, session, today
from . import F, Series, page, web

COURT = "https://www.courtlistener.com"
CL = f"{COURT}/api/rest/v4"
IA = "https://archive.org"
SC = "https://www.supremecourt.gov"
SC_FORM = f"{SC}/docket/docket.aspx"
SC_KEPT = OUT / "sources" / "supreme_court"
GAO = "https://www.gao.gov"
GOVCON = "https://govconapi.com/api/v1/protests"
ARCHIVE_ID = re.compile(r"gov\.uscourts\.[a-z0-9]+\.\d+")
SC_ID = re.compile(r"(?:\d{2}(?:-\d+|[a-z]\d+)|\d+o\d+)", re.I)
LIVE = bool(os.environ.get("COURTLISTENER_TOKEN"))  # whether this run can also read CourtListener's copy of RECAP
IA_LAST = 10000  # the Internet Archive's search reads no further than this into one query


def _request(url, *, data=None, params=None, headers=None):
    try:
        if data is None:
            response = session().get(url, params=params, headers=headers, timeout=45)
        else:
            response = session().post(url, data=data, timeout=45)
        response.raise_for_status()
    except requests.RequestException as exc:
        code = exc.response.status_code if exc.response is not None else type(exc).__name__
        raise SourceError(f"Could not read {url} ({code}).") from None
    return response


def _json(url, **kwargs):
    try:
        return _request(url, **kwargs).json()
    except ValueError:
        raise SourceError(f"The publisher did not return JSON at {url}.") from None


def _entry_text(entries):
    lines, links = [], []
    for entry in entries:
        lines.append(f"Entry {entry.get('entry_number') or ''} filed {entry.get('date_filed') or ''}")
        if entry.get("description"):
            lines.append(entry["description"])
        for document in entry.get("recap_documents") or []:
            if not isinstance(document, dict):
                continue
            if document.get("description"):
                lines.append(document["description"])
            # Only link to files already contributed to the free archive.
            path = document.get("filepath_local")
            if path and document.get("is_available"):
                url = urljoin("https://storage.courtlistener.com/", path)
                parsed = urlsplit(url)
                if parsed.scheme == "https" and parsed.hostname == "storage.courtlistener.com" and parsed.path.startswith("/recap/"):
                    links.append({"text": document.get("description") or f"Entry {entry.get('entry_number')}",
                                  "url": url})
    return lines, links



# ---------- federal court dockets ----------
#
# One RECAP archive, two copies. The Internet Archive's holds the dockets and filings deposited there, and is searched
# by caption, court, PACER case number and the date deposited, without limit. CourtListener's (with a token) also
# holds dockets it has only from PACER's feeds, and searches the text of filings, docket numbers and filing dates;
# a free account may make 5 requests a minute and 125 a day. A search goes to the copy that answers what it asks.
# A docket is named the Internet Archive's way, by court and PACER case number, wherever it is read from; a
# docket CourtListener holds without a PACER case number is named by CourtListener's ID.

CL_ONLY = ("docket_number", "date_filed", "date_terminated")  # what only CourtListener searches, besides the query
CL_ID = re.compile(r"courtlistener\.(\d+)")


def _ident(court, pacer, docket_id):
    return f"gov.uscourts.{court}.{pacer}" if court and str(pacer or "").isdigit() else f"courtlistener.{docket_id}"


def _live(path, params=None):
    url = f"{CL}/{path}/"
    try:
        r = session().get(url, params=params, headers={"Authorization": f"Token {os.environ['COURTLISTENER_TOKEN']}"}, timeout=45)
    except requests.RequestException as e:
        raise SourceError(f"CourtListener did not answer ({type(e).__name__}).") from None
    if r.status_code == 429:
        try:
            detail = r.json().get("detail") or ""
        except ValueError:
            detail = ""
        raise Throttled(f"CourtListener refused the request for its rate limit. {detail} It is where the query, docket_number, "
                        "date_filed and date_terminated are searched; case_name, court, pacer_case_id and archive_added are "
                        "searched in the Internet Archive's copy of the archive, which has no such limit.")
    if r.status_code in (404, 410):
        raise Missing(f"CourtListener has nothing at {url}")
    if not r.ok:
        raise SourceError(f"CourtListener answered {r.status_code} at {url}")
    try:
        return r.json()
    except ValueError:
        raise SourceError(f"CourtListener did not return JSON at {url}") from None


# the Internet Archive's copy

def _archive_record(doc):
    ident = doc["identifier"]
    return {"id": ident, "url": f"{IA}/details/{ident}", "case_name": doc.get("title"), "court": doc.get("court") or ident.split(".")[2],
            "pacer_case_id": ident.rsplit(".", 1)[-1], "archive_added": (doc.get("addeddate") or "")[:10] or None}


def _archive_search(s):
    ids = [i for i in s.any("pacer_case_id")]
    terms = ["collection:usfederalcourts", "identifier:gov.uscourts.*"]
    courts = s.any("court")
    if courts:
        terms.append("(" + " OR ".join(f"court:{c.lower()}" for c in courts if re.fullmatch(r"[A-Za-z0-9]+", c)) + ")")
    if ids:
        terms.append("(" + " OR ".join(f"identifier:gov.uscourts.*.{i}" for i in ids if i.isdigit()) + ")")
    words = " ".join([s.query()] + s.peek("case_name").get("is", [])[:1]).split()  # the registry checks case_name itself
    if words:
        terms.append("title:(" + " AND ".join('"' + w.replace('"', "") + '"' for w in words) + ")")
    lo, hi = s.between("archive_added")
    if lo or hi:
        terms.append(f"addeddate:[{lo or '1996-01-01'} TO {hi or today().isoformat()}]")
    at = s.cursor or {"page": 1, "rows": max(1, min(s.size, 500))}
    if at["page"] * at["rows"] > IA_LAST:
        raise SourceError("The Internet Archive's search reads no further than 10,000 dockets into one search. Narrow it by "
                          "court, caption or the dates added.")
    got = _json(f"{IA}/advancedsearch.php", params={"q": " AND ".join(terms), "fl[]": ["identifier", "title", "addeddate", "court"],
                                                    "sort[]": "addeddate desc", "rows": at["rows"], "page": at["page"],
                                                    "output": "json"})["response"]
    rows = [_archive_record(d) for d in got["docs"] if ARCHIVE_ID.fullmatch(d["identifier"])]
    more = got["start"] + len(got["docs"]) < got["numFound"]
    return page(rows, {"page": at["page"] + 1, "rows": at["rows"]} if more else None, got["numFound"], "latest added first")


def _archive_sheet(ident):
    """(the archive's record of the docket, its deposited docket sheet or None, its files)."""
    index = _json(f"{IA}/metadata/{ident}")
    meta = index.get("metadata") or {}
    if meta.get("identifier") != ident:
        raise Missing(f"The Internet Archive's copy of the RECAP archive has no docket {ident}.")
    files = index.get("files") or []
    sheet = _json(f"{IA}/download/{ident}/{ident}.docket.json") if any(f["name"] == f"{ident}.docket.json" for f in files) else None
    return meta, sheet, files


def _archive_read(ident):
    meta, sheet, files = _archive_sheet(ident)
    record = _archive_record({"identifier": ident, "title": meta.get("title"), "court": meta.get("court"), "addeddate": meta.get("addeddate")})
    if sheet:
        lines = [f"Docket number: {sheet.get('docket_number')}", f"Case filed: {sheet.get('date_filed')}",
                 f"Case terminated: {sheet.get('date_terminated')}", f"Last filing: {sheet.get('date_last_filing')}",
                 f"Docket sheet deposited: {sheet.get('date_modified')}", "", "Parties:"]
        lines += [f"  {p['name']} ({', '.join(t['name'] for t in p.get('party_types', []))})" for p in sheet.get("parties", [])]
        entries, links = _entry_text(sheet.get("docket_entries") or [])
        return {**record, "case_name": sheet.get("case_name_full") or sheet.get("case_name") or record["case_name"],
                "docket_number": sheet.get("docket_number"), "date_filed": sheet.get("date_filed"),
                "date_terminated": sheet.get("date_terminated"),
                "document": "\n".join(lines + ["", "Entries:"] + entries), "links": links, "published": sheet.get("date_last_filing")}
    found = sorted(((int(m[1]), int(m[2]), f["name"]) for f in files
                    for m in [re.fullmatch(re.escape(ident) + r"\.(\d+)\.(\d+)(?:_[\w-]+)?\.pdf", f["name"], re.I)] if m), reverse=True)
    if not found:
        raise Missing(f"The Internet Archive's copy holds {ident} without a docket sheet or filings.")
    links = [{"text": f"Entry {n}, attachment {a}", "url": f"{IA}/download/{ident}/{name}"} for n, a, name in found]
    lines = ["The archive holds the filings below, without a docket sheet."] + [f"{x['text']}: {x['url']}" for x in links]
    try:
        lines += ["", links[0]["text"], web.read(links[0]["url"])["text"]]
    except (Missing, Unreadable) as e:
        lines.append(f"The latest filing has no text to read here ({e}); its file is linked.")
    return {**record, "document": "\n".join(lines), "links": links}


# CourtListener's copy

def _live_record(row, ident=None):
    path = row.get("docket_absolute_url") or row.get("absolute_url") or ""
    m = re.search(r"/docket/(\d+)/", path)
    docket_id = str(row.get("docket_id") or (m[1] if m else row.get("id")))
    court = row.get("court_id") or (row.get("court") or "").rstrip("/").rsplit("/", 1)[-1] or None
    pacer = row.get("pacer_case_id")
    return {"id": ident or _ident(court, pacer, docket_id), "url": urljoin(COURT, path or f"/docket/{docket_id}/"),
            "case_name": row.get("caseName") or row.get("case_name"), "court": court, "pacer_case_id": str(pacer) if pacer else None,
            "docket_number": row.get("docketNumber") or row.get("docket_number"), "date_filed": row.get("dateFiled") or row.get("date_filed"),
            "date_terminated": row.get("dateTerminated") or row.get("date_terminated"), "_docket_id": docket_id}


def _shown(record):
    return {k: v for k, v in record.items() if not k.startswith("_")}


def _live_search(s):
    if s.peek("archive_added"):
        raise SourceError("archive_added is when the Internet Archive's copy received a docket, and the query, docket_number, "
                          "date_filed and date_terminated are searched in CourtListener's: one search can't give both.")
    params = {"type": "r", "order_by": "dateFiled desc"}
    for key, value in (("q", s.query()), ("court", " ".join(s.any("court"))), ("docket_number", s.one("docket_number")),
                       ("case_name", (s.peek("case_name").get("is") or [None])[0])):
        if value:
            params[key] = value
    lo, hi = s.between("date_filed")
    params |= {"filed_after": lo} if lo else {}
    params |= {"filed_before": hi} if hi else {}
    params |= {"cursor": s.cursor} if s.cursor else {}
    got = _live("search", params)
    nxt = parse_qs(urlsplit(got.get("next") or "").query).get("cursor", [None])[0]
    return page([_shown(_live_record(r)) for r in got.get("results") or []], nxt, got.get("count"), "latest filed first")


def _live_docket(ident):
    """CourtListener's ID for a docket."""
    m = CL_ID.fullmatch(ident)
    if m:
        return m[1]
    court, pacer = ident.split(".")[2:]
    found = _live("dockets", {"court": court, "pacer_case_id": pacer}).get("results") or []
    if not found:
        raise Missing(f"Neither copy of the RECAP archive has the docket {ident}.")
    return str(found[0]["id"])


def _live_read(ident):
    docket_id = _live_docket(ident)
    docket = _live(f"dockets/{docket_id}")
    batch = _live("docket-entries", {"docket": docket_id, "order_by": "-date_filed"})
    lines, links = _entry_text(batch.get("results") or [])
    record = _live_record({**docket, "docket_id": docket_id}, ident)
    head = [f"Last filing: {docket.get('date_last_filing')}", "", "Entries, latest first:"]
    more = ["", "The docket has more entries; federal_court_docket_entries lists them all."] if batch.get("next") else []
    return {**_shown(record), "case_name": docket.get("case_name_full") or record["case_name"], "document": "\n".join(head + lines + more),
            "links": links, "published": docket.get("date_last_filing")}


def _search_dockets(s):
    if LIVE and (s.words() or any(s.peek(f) for f in CL_ONLY)):
        return _live_search(s)
    return _archive_search(s)


def _read_docket(ident):
    if ARCHIVE_ID.fullmatch(ident):
        try:
            return _archive_read(ident)
        except Missing:
            if not LIVE:
                raise
    elif not (CL_ID.fullmatch(ident) and LIVE):
        raise SourceError("A federal_court_dockets record_id is one a search returned")
    return _live_read(ident)


# docket entries

def _archive_entry_record(ident, sheet, meta, e):
    return {"id": f"{ident}:{e.get('entry_number')}:{e.get('date_filed')}", "url": f"{IA}/details/{ident}", "docket": ident,
            "case_name": sheet.get("case_name") or meta.get("title"), "entry_number": e.get("entry_number"),
            "date_filed": e.get("date_filed"), "description": e.get("description")}


def _live_entry_record(e, ident):
    return {"id": f"{ident}:cl{e.get('id')}", "url": urljoin(COURT, e.get("absolute_url") or ""), "docket": ident,
            "entry_number": e.get("entry_number"), "date_filed": e.get("date_filed"), "description": e.get("description")}


def _deposited(ident):
    """(the archive's record, its docket sheet) where the Internet Archive's copy holds the sheet, else None."""
    if not ARCHIVE_ID.fullmatch(ident):
        return None
    try:
        meta, sheet, _ = _archive_sheet(ident)
    except Missing:
        return None
    return (meta, sheet) if sheet else None


def _search_entries(s):
    dockets = s.any("docket")
    held = {d: _deposited(d) for d in dockets}
    elsewhere = [d for d, h in held.items() if h is None]
    if elsewhere and not LIVE:
        raise Missing(f"The Internet Archive's copy holds no docket sheet for {', '.join(elsewhere)}.")
    if elsewhere and len(dockets) > 1:
        raise SourceError(f"CourtListener holds {', '.join(elsewhere)}; give one docket at a time for a docket read from it.")
    if elsewhere:
        params = {"docket": _live_docket(dockets[0]), "order_by": "-date_filed"} | ({"cursor": s.cursor} if s.cursor else {})
        got = _live("docket-entries", params)
        nxt = parse_qs(urlsplit(got.get("next") or "").query).get("cursor", [None])[0]
        return page([_live_entry_record(e, dockets[0]) for e in got.get("results") or []], nxt, order="latest filed first")
    out = [_archive_entry_record(d, sheet, meta, e) for d, (meta, sheet) in held.items() for e in sheet.get("docket_entries") or []]
    out.sort(key=lambda r: r["date_filed"] or "", reverse=True)
    return page(out, order="latest filed first")


def _read_entry(key):
    ident, _, rest = key.partition(":")
    if rest.startswith("cl") and LIVE:
        e = _live(f"docket-entries/{rest[2:]}")
        lines, links = _entry_text([e])
        return {**_live_entry_record(e, ident), "document": "\n".join(lines), "links": links, "published": e.get("date_filed")}
    held = _deposited(ident)
    for e in (held[1].get("docket_entries") or []) if held else []:
        record = _archive_entry_record(ident, held[1], held[0], e)
        if record["id"] == key:
            lines, links = _entry_text([e])
            return {**record, "document": "\n".join(lines), "links": links, "published": e.get("date_filed")}
    raise Missing(f"The deposited docket sheet of {ident} has no entry {rest}.")


# ---------- the Supreme Court's docket ----------

KINDS = {"paid": ("-", 1), "ifp": ("-", 5001), "application": ("A", 1)}  # each sequence's separator and first number
_tops = {}


_dockets = {}


def _sc_json(number, fresh=False):
    """A docket's JSON, or None where the Court has no such case. The Court's server refuses a client that asks
    for many dockets in a short time, so each is asked for once and kept in out/sources/supreme_court/: a search
    walks the kept copies (a docket's parties, lower court and dates do not change), and a read asks again."""
    hit = _dockets.get(number)
    if hit and time.time() - hit[0] < 600 and not (fresh and hit[2]):
        return hit[1]
    kept = SC_KEPT / f"{number.upper()}.json"
    if not fresh and kept.is_file():
        data = json.loads(kept.read_text())
        _dockets[number] = (time.time(), data, True)
        return data
    try:
        r = session().get(f"{SC}/rss/cases/json/{number}.json", timeout=40)
    except requests.RequestException as e:
        raise SourceError(f"The Supreme Court's docket could not be read: {type(e).__name__}")
    if r.status_code == 403:
        raise SourceError("The Supreme Court's server is refusing requests for now; it does so after many in a short time. "
                          "The same search can be made again later.")
    if r.status_code == 404:
        _dockets[number] = (time.time(), None, False)
        return None
    try:
        r.raise_for_status()
        data = r.json()
    except (requests.RequestException, ValueError):
        raise SourceError(f"The Supreme Court's docket for {number} could not be read ({r.status_code}).")
    SC_KEPT.mkdir(parents=True, exist_ok=True)
    kept.write_text(json.dumps(data))
    _dockets[number] = (time.time(), data, False)
    return data


def _sc_record(d):
    number = d["CaseNumber"].strip()
    try:
        docketed = dt.datetime.strptime((d.get("DocketedDate") or "").strip(), "%B %d, %Y").date().isoformat()
    except ValueError:
        docketed = None
    strip = lambda v: re.sub(r",?\s*(Petitioners?|Respondents?|Applicants?|Appellants?|Appellees?)\s*$", "", (v or "").strip()) or None  # noqa: E731
    return {"id": number, "url": f"{SC}/docket/docketfiles/html/public/{number.lower()}.html", "case_number": number,
            "case_type": d.get("sJsonCaseType"), "petitioner": strip(d.get("PetitionerTitle")), "respondent": strip(d.get("RespondentTitle")),
            "docketed": docketed, "lower_court": d.get("LowerCourt") or None,
            "lower_court_case_number": (d.get("LowerCourtCaseNumbers") or "").strip("() ") or None,
            "term": int(d["sJsonTerm"]) if str(d.get("sJsonTerm") or "").isdigit() else None,
            "linked_with": d.get("Links") or None, "capital_case": "yes" if d.get("bCapitalCase") else "no"}


def _sc_top(term, kind):
    """The highest case number of a term's sequence that has a docket, found by doubling then halving; 0 if none."""
    key = (term, kind, int(time.time() // 3600))
    if key not in _tops:
        sep, first = KINDS[kind]
        number = lambda n: f"{term:02d}{sep}{n}"  # noqa: E731
        if not _sc_json(number(first)):
            _tops[key] = 0
        else:
            lo, step = first, 64
            while _sc_json(number(lo + step)):  # numbers are assigned in order, with few gaps
                lo, step = lo + step, step * 2
            hi = lo + step
            while hi - lo > 1:
                mid = (lo + hi) // 2
                lo, hi = (mid, hi) if _sc_json(number(mid)) else (lo, mid)
            _tops[key] = lo
    return _tops[key]


def _sc_term():
    year = today().year % 100
    return year if _sc_top(year, "paid") else year - 1


def _sc_walk(s):
    """An open search: the newest case of each sequence in turn, merged by the date docketed. Each sequence
    runs downward from the term's highest number, then into the term before."""
    lo, hi = s.between("docketed")
    pos = {k: list(v) for k, v in (s.cursor or {k: [_sc_term(), None] for k in KINDS}).items()}

    def head(kind):
        term, n = pos[kind]
        sep, first = KINDS[kind]
        while term is not None and term >= 17:  # the Court's JSON dockets begin with its 2017 term
            n = _sc_top(term, kind) if n is None else n
            if n < first:
                term, n = term - 1, None
                continue
            found = _sc_json(f"{term:02d}{sep}{n}")
            if found:
                pos[kind] = [term, n]
                return _sc_record(found)
            n -= 1
        pos[kind] = [None, None]
        return None

    heads, out = {k: head(k) for k in KINDS}, []
    while len(out) < max(1, min(s.size, 40)) and any(heads.values()):
        kind = max((k for k in heads if heads[k]), key=lambda k: heads[k]["docketed"] or "")
        record = heads[kind]
        if lo and (record["docketed"] or "9") < lo:  # the newest case left was docketed before the range
            pos = {k: [None, None] for k in KINDS}
            break
        if not hi or (record["docketed"] or "") <= hi:
            out.append(record)
        pos[kind][1] -= 1
        heads[kind] = head(kind)
    return page(out, pos if any(v[0] is not None for v in pos.values()) else None, order="latest docketed first")


def _sc_form(query, number):
    """Case numbers from one page of the Court's own docket search, and whether another page follows."""
    doc = BeautifulSoup(_request(SC_FORM).content, "html.parser")
    control = "ctl00$ctl00$MainEditable$mainContent$"
    for current in range(1, number + 1):
        form = doc.find("form")
        if form is None or not form.select_one('input[name$="mainContent$txtQuery"]'):
            raise SourceError("The Supreme Court docket search form is unavailable.")
        fields = {tag["name"]: tag.get("value", "") for tag in form.select('input[type="hidden"][name]')}
        fields[control + "txtQuery"] = query
        if current == 1:
            fields[control + "cmdSearch"] = "Search"
        else:
            if not doc.select_one('a[id$="cmdNext"][href]'):
                return [], False
            fields["__EVENTTARGET"] = control + "cmdNext"
        doc = BeautifulSoup(_request(SC_FORM, data=fields).content, "html.parser")
    numbers = []
    for link in doc.select('fieldset a[href*="/docket/docketfiles/html/public/"]'):
        m = re.search(r"/public/([^/?#]+)\.html", link["href"], re.I)
        if m and SC_ID.fullmatch(m[1]):
            numbers.append(m[1].upper())
    return list(dict.fromkeys(numbers)), bool(doc.select_one('a[id$="cmdNext"][href]'))


def _sc_search(s):
    given = s.any("case_number")
    if given:
        found = [_sc_json(n.strip()) for n in given if SC_ID.fullmatch(n.strip())]
        return page([_sc_record(d) for d in found if d])
    if not s.words():
        return _sc_walk(s)
    words = s.query()
    if len(words) < 3:
        raise SourceError("The Court's docket search takes at least three characters.")
    at = s.cursor or 1
    numbers, more = _sc_form(words, at)
    found = [_sc_json(n) for n in numbers]
    return page([_sc_record(d) for d in found if d], at + 1 if more else None)


def _sc_read(number):
    d = _sc_json(number, fresh=True) if SC_ID.fullmatch(number) else None
    if not d:
        raise SourceError(f"The Supreme Court has no docket {number!r}")
    lines = list(dict.fromkeys(f"{role}: {p['PartyName']}" for role in ("Petitioner", "Respondent", "Other")
                               for p in d.get(role) or [] if p.get("PartyName")))
    lines += ["", "Proceedings and orders:"]
    links = []
    for entry in d.get("ProceedingsandOrder") or []:
        lines.append(f"{entry.get('Date') or ''}: {entry.get('Text') or ''}")
        links += [{"text": x.get("Description") or "Filed document", "url": x["DocumentUrl"]} for x in entry.get("Links") or [] if x.get("DocumentUrl")]
    return {**_sc_record(d), "document": "\n".join(lines), "links": links}


# ---------- GAO bid protests (GovCon API's index of GAO's docket) ----------

def _protest(r):
    number = str(r.get("b_number") or "").upper()
    return {"id": number, "url": f"{GAO}/docket/{number.lower()}", "b_number": number, "protester": r.get("protester"),
            "agency": r.get("agency"), "solicitation_number": r.get("solicitation_number"), "status": r.get("docket_status"),
            "filed_date": r.get("filed_date"), "due_date": r.get("due_date"), "decision_date": r.get("decision_date"),
            "outcome": r.get("outcome")}


def _govcon(params):
    return _json(GOVCON, params=params, headers={"Authorization": f"Bearer {os.environ['GOVCON_API_KEY']}"})


def _search_protests(s):
    at = s.cursor or 0
    params = {"limit": max(1, min(s.size, 100)), "offset": at, "sort": "filed"}
    words = " ".join([s.query()] + s.any("b_number")[:1] + s.peek("protester").get("is", [])[:1]).strip()
    for key, value in (("search", words), ("agency", s.one("agency")), ("status", s.one("status"))):
        if value:
            params[key] = value
    data = _govcon(params)
    rows = [_protest(r) for r in data.get("data") or []]
    return page(rows, at + len(rows) if (data.get("pagination") or {}).get("has_next") else None, order="latest filed first")


def _read_protest(number):
    rows = [_protest(r) for r in _govcon({"search": number, "limit": 5}).get("data") or []]
    record = next((r for r in rows if r["b_number"] == number.upper()), None)
    if not record:
        raise SourceError(f"The index has no protest {number}")
    return {**record, "document": "", "links": [{"text": "GAO's docket page", "url": record["url"]}]}


RECAP = "Free Law Project's RECAP archive"
DOCKET_ARCHIVE = (F("case_name", "text"), F("court", "identifier"), F("pacer_case_id", "identifier"), F("archive_added", "date"))
DOCKET_LIVE = DOCKET_ARCHIVE + (F("docket_number", "identifier"), F("date_filed", "date"), F("date_terminated", "date"),
                                F("full_text", "full text"))
COPIES = ", as deposited at the Internet Archive" + (" and as CourtListener holds it" if LIVE else "")
SERIES = [
    Series(
        id="federal_court_dockets", title="federal court dockets",
        record="the docket of a case in a U.S. district, bankruptcy or appellate court, as far as the RECAP archive holds it",
        creator="federal courts", jurisdiction="federal", publisher=RECAP + COPIES,
        fields=DOCKET_LIVE if LIVE else DOCKET_ARCHIVE, search=_search_dockets, read=_read_docket),
    Series(
        id="federal_court_docket_entries", title="federal court docket entries",
        record="an entry on a federal court docket: a filing or an order, with the date it was filed",
        creator="federal courts", jurisdiction="federal", publisher=RECAP + COPIES,
        fields=(F("docket", "identifier", required=True), F("entry_number", "number"), F("date_filed", "date"),
                F("description", "text")),
        search=_search_entries, read=_read_entry),
    Series(
        id="supreme_court_dockets", title="the Supreme Court's docket",
        record="the docket of a case or an application before the Supreme Court of the United States",
        creator="Supreme Court of the United States", jurisdiction="federal", since="2017",
        fields=(F("case_number", "identifier"), F("case_type", "text"), F("petitioner", "text"), F("respondent", "text"),
                F("docketed", "date"), F("lower_court", "name"), F("lower_court_case_number", "identifier"),
                F("term", "number"), F("linked_with", "text"), F("capital_case", ("yes", "no"))),
        search=_sc_search, read=_sc_read),
    Series(
        id="gao_bid_protests", title="GAO bid protests",
        record="a bid protest filed at the Government Accountability Office against a federal contract award or solicitation",
        creator="U.S. Government Accountability Office", publisher="GovCon API (govconapi.com), which indexes GAO's docket",
        jurisdiction="federal", requires=("GOVCON_API_KEY",),
        fields=(F("b_number", "identifier"), F("protester", "name"), F("agency", "name"),
                F("solicitation_number", "identifier"), F("status", ("Open", "Closed")), F("filed_date", "date"),
                F("due_date", "date"), F("decision_date", "date"), F("outcome", "text")),
        search=_search_protests, read=_read_protest),
]
