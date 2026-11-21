"""SEC EDGAR, three series: filers, the documents of their filings, and the figures they report as data.

  edgar_filers            every company, fund or person with a CIK. Names come from two SEC files, each kept
                          for a day in out/: company_tickers.json (the companies with a ticker) and
                          cik-lookup-data.txt (every name EDGAR holds for every filer, current and former, about a
                          million), indexed with SQLite full-text search in out/sec_names.db. A search by SIC
                          code or state goes through EDGAR's company browser. Each filer's fields come from its
                          submissions record.
  edgar_filing_documents  EDGAR full-text search: the documents of filings since 2001, by words, filer, form,
                          date, SIC code and state. It returns 100 at a time and at most 10,000 for one search,
                          so a search read to its end is split into date windows that each come back whole.
  edgar_xbrl_facts        the values a filer reported in its financial statements (XBRL company facts), read
                          for one filer at a time.
"""

import datetime as dt
import json
import os
import re
import sqlite3
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlencode

from bs4 import BeautifulSoup

from .. import names
from ..util import OUT, SourceError, http_json, session, today
from . import F, Series, page, web

TICKERS = "https://www.sec.gov/files/company_tickers.json"
NAMES = "https://www.sec.gov/Archives/edgar/cik-lookup-data.txt"
NAME_INDEX = OUT / "sec_names.db"
DAY = 86400
# Forms filed about a company's shares by insiders and large holders: counted, not listed
OWNERSHIP = {"3", "4", "5", "3/A", "4/A", "5/A", "144", "144/A", "SC 13D", "SC 13D/A", "SC 13G", "SC 13G/A",
             "SCHEDULE 13D", "SCHEDULE 13D/A", "SCHEDULE 13G", "SCHEDULE 13G/A"}
LISTED = 40  # filings a filer's document lists, newest first
RANKED = 40  # name matches whose filings are looked up, to list companies above people who only file as insiders
EFTS = "https://efts.sec.gov/LATEST/search-index"
EFTS_PAGE, EFTS_LAST = 100, 9900  # full-text search returns 100 documents at a time, the last starting at 9,900
DISPLAY = re.compile(r"^(.*?)\s+(?:\(([^()]*)\)\s+)?\(CIK (\d+)\)\s*$")
KEEP = 600  # seconds a filer's record is kept in memory
_kept = {}
_tickers = None


def _get(url):
    ua = os.environ.get("SEC_USER_AGENT")
    if not ua:
        raise SourceError("SEC requires SEC_USER_AGENT (a name and contact email) in .env")
    try:
        return http_json(url, headers={"User-Agent": ua}, timeout=60, tries=3)
    except Exception as e:
        raise SourceError(f"SEC {url}: {type(e).__name__}: {str(e)[:150]}")


def tickers():
    global _tickers
    if _tickers is None:
        cache = OUT / "sec_company_tickers.json"
        if cache.exists() and time.time() - cache.stat().st_mtime < 86400:
            _tickers = json.loads(cache.read_text())
        else:
            _tickers = list(_get(TICKERS).values())
            OUT.mkdir(exist_ok=True)
            cache.write_text(json.dumps(_tickers))
    return _tickers


def _build_names():
    """Download every name EDGAR holds and index it for whole-word search. Written to a temporary file and
    moved into place, so a search running meanwhile keeps the old index."""
    ua = os.environ.get("SEC_USER_AGENT")
    if not ua:
        raise SourceError("SEC requires SEC_USER_AGENT (a name and contact email) in .env")
    try:
        r = session().get(NAMES, headers={"User-Agent": ua}, timeout=180)
        r.raise_for_status()
    except Exception as e:
        raise SourceError(f"SEC {NAMES}: {type(e).__name__}: {str(e)[:150]}")
    OUT.mkdir(exist_ok=True)
    tmp = NAME_INDEX.with_name(f"sec_names.{os.getpid()}.tmp")
    tmp.unlink(missing_ok=True)
    con = sqlite3.connect(tmp)
    try:
        con.execute("CREATE VIRTUAL TABLE names USING fts5(toks, name UNINDEXED, cik UNINDEXED)")
        rows = []
        for line in r.content.decode("latin-1").splitlines():
            name, _, cik = line.rpartition(":")[0].rpartition(":")  # NAME:CIK: (a name can hold colons)
            name = name.strip()
            if name and cik.isdigit():
                rows.append((" ".join(names.tokens(name)), name, int(cik)))
        con.executemany("INSERT INTO names VALUES (?,?,?)", rows)
        con.commit()
    finally:
        con.close()
    os.replace(tmp, NAME_INDEX)


def _name_matches(want, limit=5000):
    """[(name, cik)] whose name holds every word of `want` as a whole word."""
    if not NAME_INDEX.exists() or time.time() - NAME_INDEX.stat().st_mtime > DAY:
        _build_names()
    con = sqlite3.connect(f"file:{NAME_INDEX}?mode=ro", uri=True)
    try:
        q = " AND ".join(f'"{t}"' for t in want)
        return [(n, int(c)) for n, c in con.execute("SELECT name, cik FROM names WHERE names MATCH ? LIMIT ?", (q, limit))]
    finally:
        con.close()


def _keeping(key, fetch):
    """fetch(), kept for a few minutes: a search reads each filer's record, and a read usually follows."""
    hit = _kept.get(key)
    if not hit or time.time() - hit[0] > KEEP:
        hit = _kept[key] = (time.time(), fetch())
    return hit[1]


def _cik(given):
    digits = re.sub(r"\D", "", str(given))
    return int(digits) if digits else 0


def _submissions(cik):
    return _keeping(("sub", cik), lambda: _get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json"))


# ---------- filers ----------

def _filer(sub):
    cik, recent = int(sub["cik"]), sub.get("filings", {}).get("recent", {})
    forms = Counter(f for f in recent.get("form", []) if f not in OWNERSHIP)
    return {"id": str(cik), "url": f"https://www.sec.gov/edgar/browse/?CIK={cik}", "name": sub.get("name"),
            "former_name": [f.get("name") for f in sub.get("formerNames") or [] if f.get("name")], "cik": str(cik),
            "ticker": sub.get("tickers") or [], "sic": sub.get("sic") or None, "industry": sub.get("sicDescription") or None,
            "state": ((sub.get("addresses") or {}).get("business") or {}).get("stateOrCountry") or None,
            "incorporated_in": sub.get("stateOfIncorporation") or None, "entity_type": sub.get("entityType") or None,
            "fiscal_year_end": sub.get("fiscalYearEnd") or None, "last_filed": (recent.get("filingDate") or [None])[0],
            "form": [f for f, _ in forms.most_common()]}


def _filers(ciks):
    """{cik: its record}, for the CIKs EDGAR has a submissions record for."""
    def one(cik):
        try:
            return cik, _filer(_submissions(cik))
        except SourceError:
            return cik, None
    with ThreadPoolExecutor(5) as ex:  # SEC asks for no more than 10 requests a second
        return {c: r for c, r in ex.map(one, ciks) if r}


def _browse(sic, state, start, count):
    """(CIKs from EDGAR's company browser, by SIC code and state, up to `count` from `start`; whether more follow)."""
    ua = os.environ.get("SEC_USER_AGENT")
    if not ua:
        raise SourceError("SEC requires SEC_USER_AGENT (a name and contact email) in .env")
    try:
        r = session().get("https://www.sec.gov/cgi-bin/browse-edgar", headers={"User-Agent": ua}, timeout=60, params={
            "action": "getcompany", "SIC": sic or "", "State": state or "", "type": "", "dateb": "", "owner": "include",
            "start": start, "count": 100, "output": "atom"})  # the browser takes a count of 10, 20, 40, 80 or 100
        r.raise_for_status()
    except Exception as e:
        raise SourceError(f"SEC company browser: {type(e).__name__}: {str(e)[:150]}")
    found = [int(c) for c in re.findall(r"<cik>(\d+)</cik>", r.text)]
    return found[:count], len(found) > count or len(found) == 100


def _index_ciks(start, count):
    """Every CIK EDGAR holds, in CIK order, and how many there are."""
    if not NAME_INDEX.exists() or time.time() - NAME_INDEX.stat().st_mtime > DAY:
        _build_names()
    con = sqlite3.connect(f"file:{NAME_INDEX}?mode=ro", uri=True)
    try:
        total = _keeping("ciks", lambda: con.execute("SELECT COUNT(DISTINCT cik) FROM names").fetchone()[0])
        return [int(r[0]) for r in con.execute("SELECT DISTINCT CAST(cik AS INTEGER) c FROM names ORDER BY c LIMIT ? OFFSET ?",
                                               (count, start))], total
    finally:
        con.close()


def _search_filers(s):
    start, size = (s.cursor or {}).get("o", 0), max(1, min(s.size, 200))
    ciks, tickers_given, named = s.any("cik"), s.any("ticker"), s.any("name")
    words = named or ([s.query()] if s.words() else []) or s.peek("former_name").get("is", [])
    if ciks:
        found, total = [_cik(c) for c in ciks], len(ciks)
    elif tickers_given:
        want = {t.upper() for t in tickers_given}
        found = list(dict.fromkeys(int(r["cik_str"]) for r in tickers() if r["ticker"].upper() in want))
        total = len(found)
    elif words:
        by_cik, hit = {}, {}
        for r in tickers():
            by_cik.setdefault(int(r["cik_str"]), r["title"])
        for given in words:  # any of the names given
            want = names.core_tokens(given) or names.tokens(given)
            for cik, title in by_cik.items():
                if want and all(t in names.tokens(title) for t in want):
                    hit.setdefault(cik, title)
            for name, cik in _name_matches(want) if want else []:
                if all(t in names.tokens(name) for t in want):
                    hit.setdefault(cik, name)
        found = sorted(hit, key=lambda c: (c not in by_cik, len(names.tokens(by_cik.get(c) or hit[c])), hit[c]))
        # People have CIKs too and file only ownership forms: look up the first matches, and list those with
        # a ticker or filings of their own first.
        head = _filers(found[:RANKED])
        found[:RANKED] = sorted(found[:RANKED], key=lambda c: (not (head.get(c) or {}).get("ticker"), not (head.get(c) or {}).get("form")))
        total = len(found)
    else:
        sic, state = s.one("sic"), s.one("state")
        if sic or state:
            part, more = _browse(sic, state, start, min(size, 100))
            got = _filers(part)
            return page([got[c] for c in part if c in got], {"o": start + len(part)} if more else None)
        part, total = _index_ciks(start, size)
        got = _filers(part)
        return page([got[c] for c in part if c in got], {"o": start + size} if start + size < total else None, total)
    part = found[start:start + size]
    got = _filers(part)
    return page([got[c] for c in part if c in got], {"o": start + size} if start + size < len(found) else None, total)


def _read_filer(given):
    cik = _cik(given)
    sub = _get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json")
    _kept[("sub", cik)] = (time.time(), sub)
    recent, record = sub.get("filings", {}).get("recent", {}), _filer(sub)
    kinds, listed, links = Counter(recent.get("form", [])), [], []
    for i, form in enumerate(recent.get("form", [])):
        if form in OWNERSHIP or len(listed) >= LISTED:
            continue
        accn = recent["accessionNumber"][i]
        base = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accn.replace('-', '')}"
        items = (recent.get("items") or [""] * (i + 1))[i]
        listed.append(f"  {form} filed {recent['filingDate'][i]}" + (f" for {recent['reportDate'][i]}" if recent["reportDate"][i] else "")
                      + (f", items {items}" if items else "") + f": {base}/{recent['primaryDocument'][i]}")
        links.append({"text": f"{form} filed {recent['filingDate'][i]}", "url": f"{base}/{recent['primaryDocument'][i]}"})
    former = [f"{f.get('name')} (until {(f.get('to') or '')[:10]})" for f in sub.get("formerNames") or []]
    owned = sum(n for f, n in kinds.items() if f in OWNERSHIP)
    lines = ["Former names: " + "; ".join(former) + "."] if former else []
    lines.append("Forms in its recent filings: " + (", ".join(f"{f} {n}" for f, n in kinds.most_common() if f not in OWNERSHIP) or "none")
                 + (f"; and {owned} ownership filings by insiders and holders (Forms 3, 4, 5, 144, Schedules 13D and 13G)" if owned else "") + ".")
    if sub.get("category"):
        lines.append("Filer category: " + re.sub(r"<br\s*/?>", "; ", sub["category"]) + ".")
    lines += ["", f"Recent filings, newest first, up to {LISTED} (form, filed, report date, 8-K items, document):"] + listed
    return {**record, "document": "\n".join(lines), "links": links}


# ---------- the documents of filings (EDGAR full-text search) ----------

def _document(hit):
    src = hit.get("_source") or {}
    adsh, _, fname = (hit.get("_id") or "").partition(":")
    ciks = [str(_cik(c)) for c in src.get("ciks") or [] if _cik(c)]
    filers = [(m.group(1).strip() if (m := DISPLAY.match(d)) else d) for d in src.get("display_names") or []]
    cik = ciks[0] if ciks else "0"
    return {"id": f"{cik}:{adsh}:{fname}", "url": f"https://www.sec.gov/Archives/edgar/data/{cik}/{adsh.replace('-', '')}/{fname}",
            "filer": filers, "cik": ciks, "form": src.get("form"), "document_type": src.get("file_type"),
            "filing_date": src.get("file_date"), "period": src.get("period_ending"), "item": [x for x in src.get("items") or [] if x],
            "sic": [x for x in src.get("sics") or [] if x], "state": [x for x in src.get("biz_states") or [] if x],
            "description": src.get("file_description"), "accession_number": adsh}


def _search_documents(s):
    words = s.query()
    ciks, forms, sics, states, filer = s.any("cik"), s.any("form"), s.any("sic"), s.any("state"), s.one("filer")
    lo, hi = s.between("filing_date")
    params = {"q": words}
    if ciks:
        params["ciks"] = ",".join(f"{_cik(c):010d}" for c in ciks)
    if filer:
        params["entityName"] = filer
    if sics:
        params["sics"] = ",".join(sics)
    if states:
        params |= {"locationCodes": ",".join(x.upper() for x in states), "locationType": "located"}
    if forms:
        params["forms"] = ",".join(forms)
    elif not (words or ciks or filer or states):
        params["forms"] = "-0"  # EDGAR refuses a search that names nothing; this excludes no form

    def ask(since, until, start):
        dated = {"dateRange": "custom", "startdt": since or "2001-01-01", "enddt": until or today().isoformat()} if since or until else {}
        hits = _get(f"{EFTS}?{urlencode(params | dated | {'from': start})}").get("hits") or {}
        total = hits.get("total") or {}
        return hits.get("hits") or [], total.get("value") or 0, total.get("relation") == "eq"

    if not s.everything:
        start = (s.cursor or {}).get("from", 0)
        hits, total, exact = ask(lo, hi, start)
        more = len(hits) == EFTS_PAGE and start + EFTS_PAGE <= EFTS_LAST
        return page([_document(h) for h in hits], {"from": start + EFTS_PAGE} if more else None, total if exact else None,
                    "most relevant first" if words else "newest first")
    # Every match: date windows, newest first, each small enough for full-text search to return whole.
    at = s.cursor or {"windows": [[lo or "2001-01-01", hi or today().isoformat()]], "from": 0}
    (since, until), rest = at["windows"][0], at["windows"][1:]
    hits, total, exact = ask(since, until, at["from"])
    if at["from"] == 0 and (not exact or total > EFTS_LAST + EFTS_PAGE) and since < until:
        a, b = dt.date.fromisoformat(since), dt.date.fromisoformat(until)
        mid = a + (b - a) // 2
        return page([], {"windows": [[(mid + dt.timedelta(days=1)).isoformat(), until], [since, mid.isoformat()]] + rest, "from": 0})
    if len(hits) == EFTS_PAGE and at["from"] + EFTS_PAGE <= EFTS_LAST:
        nxt = {"windows": at["windows"], "from": at["from"] + EFTS_PAGE}
    else:
        nxt = {"windows": rest, "from": 0} if rest else None
    return page([_document(h) for h in hits], nxt)


def _read_document(given):
    cik, adsh, fname = (given.split(":", 2) + ["", ""])[:3]
    cik = _cik(cik)
    if not (cik and adsh and fname):
        raise SourceError("an edgar_filing_documents record_id is the one a search returned")
    base = f"https://www.sec.gov/Archives/edgar/data/{cik}/{adsh.replace('-', '')}"
    got = web.read(f"{base}/{fname}")
    record = {"id": given, "url": f"{base}/{fname}", "cik": [str(cik)], "accession_number": adsh}
    try:
        sub = _submissions(cik)
        recent = sub.get("filings", {}).get("recent", {})
        i = recent.get("accessionNumber", []).index(adsh)
        record |= {"filer": [sub.get("name")], "form": recent["form"][i], "filing_date": recent["filingDate"][i],
                   "period": recent["reportDate"][i] or None, "sic": [sub["sic"]] if sub.get("sic") else [],
                   "item": [x.upper() for x in (recent.get("items") or [""] * (i + 1))[i].split(",") if x],
                   "state": [x for x in [((sub.get("addresses") or {}).get("business") or {}).get("stateOrCountry")] if x]}
        if recent["primaryDocument"][i] == fname:
            record |= {"document_type": recent["form"][i], "description": recent["primaryDocDescription"][i] or None}
    except (SourceError, ValueError, KeyError, IndexError):
        pass  # an older filing, or one made under another filer's CIK: the document itself is still read
    if "document_type" not in record:
        try:  # an exhibit: the filing's index names each document's type
            index = BeautifulSoup(session().get(f"{base}/{adsh}-index.htm", timeout=60, headers={
                "User-Agent": os.environ.get("SEC_USER_AGENT", "")}).text, "html.parser")
            for row in index.select("table.tableFile tr"):
                cells = [c.get_text(" ", strip=True) for c in row.find_all("td")]
                if len(cells) >= 4 and row.find("a") and row.find("a").get("href", "").endswith("/" + fname):
                    record |= {"description": cells[1] or None, "document_type": cells[3] or None}
        except Exception:
            pass
    return {**record, "document": got["text"], "links": got["data"].get("links") or [], "published": record.get("filing_date")}


# ---------- reported figures (XBRL company facts) ----------

def _facts(cik):
    def fetch():
        j = _get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json")
        out = []
        for taxonomy, concepts in (j.get("facts") or {}).items():
            for concept, body in concepts.items():
                for unit, rows in (body.get("units") or {}).items():
                    for r in rows:
                        accn = r.get("accn") or ""
                        out.append({"id": f"{cik}:{taxonomy}:{concept}:{unit}:{r.get('start') or ''}:{r.get('end')}:{accn}",
                                    "url": f"https://www.sec.gov/Archives/edgar/data/{cik}/{accn.replace('-', '')}/{accn}-index.htm" if accn else None,
                                    "filer": j.get("entityName"), "cik": str(cik), "taxonomy": taxonomy, "concept": concept,
                                    "label": body.get("label"), "unit": unit, "period_start": r.get("start"),
                                    "period_end": r.get("end"), "value": r.get("val"), "form": r.get("form"),
                                    "filed": r.get("filed"), "fiscal_year": r.get("fy"), "fiscal_period": r.get("fp"),
                                    "accession_number": accn or None, "frame": r.get("frame")})
        out.sort(key=lambda r: (r["period_end"] or "", r["filed"] or ""), reverse=True)
        return out
    return _keeping(("facts", cik), fetch)


def _search_facts(s):
    out = [r for c in s.any("cik") for r in _facts(_cik(c))]
    return page(out, order="newest period first")


def _read_fact(given):
    for r in _facts(_cik(given.split(":", 1)[0])):
        if r["id"] == given:
            return dict(r)
    raise SourceError(f"edgar_xbrl_facts has no record {given!r}")


SEC = "U.S. Securities and Exchange Commission"
SERIES = [
    Series(
        id="edgar_filers", title="SEC filers",
        record="a company, fund or person that files with the SEC, under its Central Index Key (CIK)",
        creator=SEC, jurisdiction="federal", since="1993",
        fields=(F("name", "name"), F("former_name", "name", many=True), F("cik", "identifier"),
                F("ticker", "identifier", many=True), F("sic", "identifier"), F("industry", "text"), F("state", "place"),
                F("incorporated_in", "place"), F("entity_type", "identifier"), F("fiscal_year_end", "identifier"),
                F("last_filed", "date"), F("form", "identifier", many=True)),
        search=_search_filers, read=_read_filer),
    Series(
        id="edgar_filing_documents", title="SEC filings",
        record="a document of a filing made with the SEC: the form itself, or one of its exhibits",
        creator="SEC filers", publisher=f"{SEC} (EDGAR full-text search)", jurisdiction="federal", since="2001",
        coverage=lambda series: ("2001-01-01", (_search_documents_newest() or None)),
        fields=(F("filer", "name", many=True), F("cik", "identifier", many=True), F("form", "identifier"),
                F("document_type", "identifier"), F("filing_date", "date"), F("period", "date"),
                F("item", "identifier", many=True), F("sic", "identifier", many=True), F("state", "place", many=True),
                F("description", "text"), F("accession_number", "identifier"), F("full_text", "full text")),
        search=_search_documents, read=_read_document),
    Series(
        id="edgar_xbrl_facts", title="SEC filers' reported figures",
        record="a value a filer reported in its financial statements for one period, as XBRL data",
        creator="SEC filers", publisher=SEC, jurisdiction="federal", since="2009",
        fields=(F("filer", "name"), F("cik", "identifier", required=True), F("taxonomy", "identifier"),
                F("concept", "identifier"), F("label", "text"), F("unit", "identifier"), F("period_start", "date"),
                F("period_end", "date"), F("value", "number"), F("form", "identifier"), F("filed", "date"),
                F("fiscal_year", "number"), F("fiscal_period", "identifier"), F("accession_number", "identifier"),
                F("frame", "identifier")),
        search=_search_facts, read=_read_fact),
]


def _search_documents_newest():
    hits = _get(f"{EFTS}?{urlencode({'q': '', 'forms': '-0'})}").get("hits") or {}
    return ((hits.get("hits") or [{}])[0].get("_source") or {}).get("file_date")
