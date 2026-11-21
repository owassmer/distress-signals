"""SEC EDGAR: find a filer's CIK by ticker or by any name it has filed under, list its filings, and read its
reported revenue by period (XBRL company facts). Filing documents themselves are read as URLs (sources/web.py).

Names come from two SEC files, each kept for a day in out/: company_tickers.json (the companies with a ticker,
about 10,000) and cik-lookup-data.txt (every name EDGAR holds for every filer, current and former, about a
million). The second is indexed with SQLite full-text search in out/sec_names.db."""

import datetime as dt
import re
import json
import os
import sqlite3
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from .. import names
from ..util import OUT, SourceError, http_json, session
from . import Source

TICKERS = "https://www.sec.gov/files/company_tickers.json"
NAMES = "https://www.sec.gov/Archives/edgar/cik-lookup-data.txt"
NAME_INDEX = OUT / "sec_names.db"
DAY = 86400
# Forms filed about a company's shares by insiders and large holders: counted, not listed
OWNERSHIP = {"3", "4", "5", "3/A", "4/A", "5/A", "144", "144/A", "SC 13D", "SC 13D/A", "SC 13G", "SC 13G/A",
             "SCHEDULE 13D", "SCHEDULE 13D/A", "SCHEDULE 13G", "SCHEDULE 13G/A"}
LISTED = 40  # filings a record lists, newest first
PROFILED = 40  # name matches without a ticker whose filings are looked up, to rank companies above people
REVENUE = ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
           "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet", "RevenuesNetOfInterestExpense"]
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


def _profile(cik):
    """What a filer without a ticker is: its current name, former names, kind, industry and what it files."""
    s = _get(f"https://data.sec.gov/submissions/CIK{str(cik).zfill(10)}.json")
    rec = s.get("filings", {}).get("recent", {})
    forms = Counter(f for f in rec.get("form", []) if f not in OWNERSHIP)
    return {"name": s.get("name"), "former_names": [f.get("name") for f in s.get("formerNames") or []][:5],
            "entity_type": s.get("entityType") or None, "industry": s.get("sicDescription") or None,
            "last_filed": (rec.get("filingDate") or [None])[0], "forms": dict(forms.most_common(6))}


def search(query, limit=15):
    """{results, note?}: filers matching a ticker exactly, then by name: every word of the name, as whole
    words, in any name EDGAR holds for them, current or former. Companies with a ticker come first; each
    result without one says what the filer is and what it files."""
    q = query.strip()
    if not q:
        raise SourceError("sec: give a ticker or a company name")
    rows = tickers()
    by_cik = {}
    for r in rows:
        by_cik.setdefault(int(r["cik_str"]), r)
    exact = [int(r["cik_str"]) for r in rows if r["ticker"].upper() == q.upper()]
    want, note, matched = names.core_tokens(q), None, {}
    if want:
        for r in rows:  # current names of companies with a ticker
            if all(t in names.tokens(r["title"]) for t in want):
                matched.setdefault(int(r["cik_str"]), r["title"])
        try:
            for name, cik in _name_matches(want):
                if all(t in names.tokens(name) for t in want):
                    matched.setdefault(cik, name)
        except SourceError as e:
            note = f"Only companies with a ticker were searched by name: the list of every EDGAR name couldn't be read ({e})."
    first = list(dict.fromkeys(exact))
    rest = [c for c in matched if c not in first]
    rest.sort(key=lambda c: (c not in by_cik, len(names.tokens(by_cik[c]["title"] if c in by_cik else matched[c])), matched[c]))
    # Insiders and large holders have CIKs too and file only ownership forms. Profile a wider set of filers
    # without a ticker, and rank those with filings of their own above them.
    candidates = (first + rest)[:PROFILED]
    no_ticker = [c for c in candidates if c not in by_cik]
    with ThreadPoolExecutor(5) as ex:  # SEC asks for no more than 10 requests a second
        profiles = dict(zip(no_ticker, ex.map(lambda c: _safe(_profile, c), no_ticker)))
    own = lambda c: c in by_cik or bool((profiles.get(c) or {}).get("forms"))  # noqa: E731
    ranked = first + sorted([c for c in candidates if c not in first], key=lambda c: (c not in by_cik, not own(c)))
    ranked = ranked[:limit]
    out = []
    for c in ranked:
        t = by_cik.get(c)
        row = {"record_id": f"sec:{c}", "cik": c, "ticker": t["ticker"] if t else None,
               "name": t["title"] if t else (profiles.get(c) or {}).get("name") or matched.get(c)}
        if matched.get(c) and names.core_tokens(matched[c]) != names.core_tokens(row["name"] or ""):
            row["matched_name"] = matched[c]
        if not t and profiles.get(c):
            row.update({k: v for k, v in profiles[c].items() if k != "name" and v})
            if not profiles[c]["forms"]:
                row["files"] = "only ownership forms, as an insider or large holder"
        out.append(row)
    return {"results": out, **({"note": note} if note else {})}


def _safe(fn, *a):
    try:
        return fn(*a)
    except SourceError:
        return None


def _revenue(facts):
    gaap = (facts or {}).get("facts", {}).get("us-gaap", {})
    best = None
    for concept in REVENUE:
        rows = (gaap.get(concept) or {}).get("units", {}).get("USD") or []
        latest = max((r["end"] for r in rows), default="")
        if rows and (best is None or latest > best[2]):
            best = (concept, rows, latest)
    if not best:
        return None
    concept, rows, _ = best
    by_period = {}
    for r in rows:
        if not r.get("start"):
            continue
        k = (r["start"], r["end"])
        if k not in by_period or r.get("filed", "") > by_period[k].get("filed", ""):
            by_period[k] = r
    quarters, years = [], []
    for (start, end), r in by_period.items():
        days = (dt.date.fromisoformat(end) - dt.date.fromisoformat(start)).days
        row = {"start": start, "end": end, "days": days, "value": r["val"], "form": r.get("form"), "filed": r.get("filed"),
               "accession": r.get("accn")}
        if 80 <= days <= 100:
            quarters.append(row)
        elif 350 <= days <= 380:
            years.append(row)
    key = lambda r: r["end"]
    quarters.sort(key=key)
    years.sort(key=key)
    # A fourth quarter is reported only inside the annual figure: derive it where the year's three
    # reported quarters are all present, and label it derived.
    have = {q["end"]: q for q in quarters}
    for y in years:
        inside = [q for q in quarters if y["start"] <= q["start"] and q["end"] <= y["end"]]
        if len(inside) == 3 and y["end"] not in have:
            last = max(inside, key=key)
            q4_start = (dt.date.fromisoformat(last["end"]) + dt.timedelta(days=1)).isoformat()
            quarters.append({"start": q4_start, "end": y["end"],
                             "days": (dt.date.fromisoformat(y["end"]) - dt.date.fromisoformat(q4_start)).days,
                             "value": y["value"] - sum(q["value"] for q in inside), "form": f"derived: {y['form']} year less three quarters",
                             "filed": y["filed"], "accession": y["accession"]})
    quarters.sort(key=key)
    return {"concept": f"us-gaap:{concept}", "unit": "USD", "quarters": quarters[-9:], "years": years[-4:],
            "all": {"quarters": quarters, "years": years}}


def company(cik):
    """The observation for one company: identity, recent filings with document URLs, revenue by period."""
    cik = int(str(cik).lstrip("CIK") or 0)
    cik10 = str(cik).zfill(10)
    s = _get(f"https://data.sec.gov/submissions/CIK{cik10}.json")
    try:
        facts = _get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik10}.json")
    except SourceError:
        facts = None
    rec = s.get("filings", {}).get("recent", {})
    filings, kinds = [], Counter(rec.get("form", []))
    for i, form in enumerate(rec.get("form", [])):
        if form in OWNERSHIP:
            continue
        accn = rec["accessionNumber"][i]
        base = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accn.replace('-', '')}"
        filings.append({"form": form, "filed": rec["filingDate"][i], "report_date": rec["reportDate"][i] or None,
                        "items": rec.get("items", [""] * (i + 1))[i] or None, "accession": accn,
                        "document": f"{base}/{rec['primaryDocument'][i]}", "index": f"{base}/{accn}-index.htm",
                        "description": rec.get("primaryDocDescription", [""] * (i + 1))[i] or None})
        if len(filings) >= LISTED:
            break
    revenue = _revenue(facts)
    former = [f"{f.get('name')} (until {(f.get('to') or '')[:10]})" for f in s.get("formerNames") or []]
    owned = sum(n for f, n in kinds.items() if f in OWNERSHIP)
    lines = [f"{s.get('name')} ({', '.join(s.get('tickers') or []) or 'no ticker'}), CIK {cik10}."
             + (f" Entity type: {s['entityType']}." if s.get("entityType") else ""),
             " ".join(x for x in (f"SIC {s['sic']}: {s.get('sicDescription')}." if s.get("sic") else "",
                                  f"Fiscal year end (MMDD): {s['fiscalYearEnd']}." if s.get("fiscalYearEnd") else "") if x)]
    if former:
        lines.append("Former names: " + "; ".join(former) + ".")
    lines.append("Forms in its recent filings: " + (", ".join(f"{f} {n}" for f, n in kinds.most_common() if f not in OWNERSHIP) or "none")
                 + (f"; and {owned} ownership filings by insiders and holders (Forms 3, 4, 5, 144, Schedules 13D and 13G)" if owned else "") + ".")
    lines.append("")
    if revenue:
        lines.append(f"Reported revenue ({revenue['concept']}, USD):")
        for label, rows in (("Quarters", revenue["quarters"]), ("Years", revenue["years"])):
            lines.append(f"{label}:")
            lines += [f"  {r['start']} to {r['end']}: ${r['value']:,.0f} ({r['form']} filed {r['filed']})" for r in rows]
        lines.append("")
    lines.append(f"Recent filings, newest first, up to {LISTED} (form, filed, report date, 8-K items, document, index):")
    lines += [f"  {f['form']} filed {f['filed']}" + (f" for {f['report_date']}" if f["report_date"] else "")
              + (f", items {f['items']}" if f["items"] else "") + f": {f['document']} | index {f['index']}"
              for f in filings]
    name = s.get("name")
    rev_rows = []
    if revenue:
        for kind, rows_ in (("quarter", revenue["all"]["quarters"]), ("year", revenue["all"]["years"])):
            rev_rows += [{"company": name, "cik": cik10, "concept": revenue["concept"], "start_date": r["start"],
                          "end_date": r["end"], "period_days": r["days"], "period_type": kind, "value": r["value"],
                          "form": r["form"], "filed": r["filed"], "accession": r["accession"]} for r in rows_]
        revenue = {k: v for k, v in revenue.items() if k != "all"}
    rows = {"revenue": rev_rows,
            "filings": [{"company": name, "cik": cik10, "form": f["form"], "filed": f["filed"], "report_date": f["report_date"],
                         "items": f["items"], "document": f["document"], "index_url": f["index"],
                         "description": f["description"]} for f in filings]}
    return {"kind": "financial_fact", "source": "sec", "native_id": cik10, "rows": rows,
            "url": f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik10}",
            "text": "\n".join(lines),
            "data": {"title": s.get("name"), "name": s.get("name"), "tickers": s.get("tickers"), "cik": cik10,
                     "former_names": [f.get("name") for f in s.get("formerNames") or []], "entity_type": s.get("entityType"),
                     "sic": s.get("sicDescription"), "fiscal_year_end": s.get("fiscalYearEnd"),
                     "filings": filings, "revenue": revenue}}


def _locate(ref):
    m = re.match(r"^sec:(?:CIK)?0*(\d+)$", ref)
    return (("sec", m[1]), lambda: company(m[1])) if m else None


SOURCE = Source(
    id="sec",
    publisher="U.S. Securities and Exchange Commission (EDGAR)",
    record="one filer: a company, fund or person that files with the SEC.",
    names="the filer, under every name it has filed with, current or former, and its ticker where it has one.",
    dates="each filing's date and report date, and the periods of its reported revenue.",
    search="query is a ticker or a name, matched by whole words. Filers with a ticker come first; one without says what "
           "kind of filer it is, its industry, when it last filed and which forms it files. No filters.",
    read="its former names, the forms it files, its recent filings with links, and its reported revenue by quarter and "
         "year (a fourth quarter is derived from the year and labeled so); fills revenue and filings.",
    coverage="every filer EDGAR holds, about a million; revenue where the filer reports it as data.",
    filters=frozenset(),
    find=lambda query, filters: search(query),
    label=lambda q, f: f"Looked up “{q}” in SEC company records",
    locate=_locate,
    again=lambda obs: f"sec:{obs['native_id']}" if obs["source"] == "sec" and obs["kind"] == "financial_fact"
    and obs.get("native_id") else None,
    noun="the company's SEC record",
    kind="financial_fact", tables=("revenue", "filings"),
)
