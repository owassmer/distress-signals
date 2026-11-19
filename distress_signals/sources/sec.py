"""SEC EDGAR: find a company's CIK, list its filings, and read its reported revenue by period
(XBRL company facts). Filing documents themselves are read as URLs (sources/web.py)."""

import datetime as dt
import json
import os
import time

from .. import names
from ..util import OUT, SourceError, http_json

TICKERS = "https://www.sec.gov/files/company_tickers.json"
FORMS = {"10-K", "10-K/A", "10-Q", "10-Q/A", "8-K", "8-K/A", "20-F", "6-K", "40-F", "S-1", "DEF 14A"}
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


def search(query):
    q = query.strip()
    rows = tickers()
    exact = [r for r in rows if r["ticker"].upper() == q.upper()]
    want = names.core_tokens(q)
    by_name = [r for r in rows if want and all(t in names.tokens(r["title"]) for t in want) and r not in exact]
    by_name.sort(key=lambda r: len(names.tokens(r["title"])))
    return [{"record_id": f"sec:{r['cik_str']}", "cik": r["cik_str"], "ticker": r["ticker"], "name": r["title"]}
            for r in (exact + by_name)[:15]]


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
    filings = []
    for i, form in enumerate(rec.get("form", [])):
        if form not in FORMS:
            continue
        accn = rec["accessionNumber"][i]
        base = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accn.replace('-', '')}"
        filings.append({"form": form, "filed": rec["filingDate"][i], "report_date": rec["reportDate"][i] or None,
                        "items": rec.get("items", [""] * (i + 1))[i] or None, "accession": accn,
                        "document": f"{base}/{rec['primaryDocument'][i]}", "index": f"{base}/{accn}-index.htm",
                        "description": rec.get("primaryDocDescription", [""] * (i + 1))[i] or None})
        if len(filings) >= 40:
            break
    revenue = _revenue(facts)
    lines = [f"{s.get('name')} ({', '.join(s.get('tickers') or []) or 'no ticker'}), CIK {cik10}.",
             f"SIC {s.get('sic')}: {s.get('sicDescription')}. Fiscal year end (MMDD): {s.get('fiscalYearEnd')}.", ""]
    if revenue:
        lines.append(f"Reported revenue ({revenue['concept']}, USD):")
        for label, rows in (("Quarters", revenue["quarters"]), ("Years", revenue["years"])):
            lines.append(f"{label}:")
            lines += [f"  {r['start']} to {r['end']}: ${r['value']:,.0f} ({r['form']} filed {r['filed']})" for r in rows]
        lines.append("")
    lines.append("Recent filings (form, filed, report date, 8-K items, document, index):")
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
                     "sic": s.get("sicDescription"), "fiscal_year_end": s.get("fiscalYearEnd"),
                     "filings": filings, "revenue": revenue}}
