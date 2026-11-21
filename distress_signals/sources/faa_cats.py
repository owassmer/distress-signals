"""Two series from the FAA's Certification Activity Tracking System (CATS), where commercial service airports file
their annual financial reports under the FAA Authorization Act of 1994: the line items of FAA Form 5100-127,
the Operating and Financial Summary, and the payments to government entities reported on FAA Form 5100-126.

CATS is a Blazor application: its pages are drawn over a websocket, with no API behind them. Its public export
gives one CSV for each FAA region and year, holding every airport's filing for that year, so a year is nine
downloads, made in headless Chromium and kept in out/sources/faa_cats/: the two latest years are fetched again
after a day, earlier years after a month, since sponsors amend their filings. A search reads the years it needs,
newest first; the registry applies the filters.
"""

import csv
import datetime as dt
import functools
import threading
from concurrent.futures import ThreadPoolExecutor

from ..util import OUT, SourceError, stable_id, today
from . import F, Series, page

REPORTS = "https://cats.airports.faa.gov/reports"
REGIONS = ("Alaska", "Central", "Eastern", "Great Lakes", "New England", "Northwest Mountain", "Southern", "Southwest",
           "Western Pacific")
KEPT = OUT / "sources" / "faa_cats"
FIRST = {"127": 2009, "126": 1996}  # the revised Form 127 begins with 2009; CATS keeps Form 126 from 1996
_lock = threading.Lock()


# ---------- the exports ----------

def _path(form, year, region):
    return KEPT / f"form_{form}_{year}_{region.replace(' ', '_')}.csv"


def _stale(path, year):
    if not path.is_file():
        return True
    age = dt.datetime.now().timestamp() - path.stat().st_mtime
    return age > (86400 if year >= today().year - 1 else 30 * 86400)


def _download(form, year, regions):
    """Each region's export of a form and year, from one headless Chromium session."""
    try:
        from playwright.sync_api import Error, sync_playwright
    except ImportError:
        raise SourceError("FAA CATS is read in headless Chromium: install requirements.txt, then run "
                          "python -m playwright install chromium --only-shell") from None

    def run():
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                try:
                    pg = browser.new_page(accept_downloads=True)
                    pg.goto(REPORTS, wait_until="networkidle", timeout=60000)
                    pg.get_by_text("Continue", exact=True).click()
                    # Blazor draws the form again after each change: wait for it to settle before the next
                    pg.locator("#reporting-year-select").wait_for()
                    pg.select_option("#reporting-year-select", str(year))
                    pg.wait_for_load_state("networkidle")
                    pg.locator("#export-to-csv").check(timeout=60000)
                    pg.wait_for_load_state("networkidle")
                    for region in regions:
                        pg.select_option("#region-select", region)
                        pg.wait_for_load_state("networkidle")
                        with pg.expect_download(timeout=120000) as got:
                            pg.get_by_role("button", name=f"Search Form {form}").click()
                        KEPT.mkdir(parents=True, exist_ok=True)
                        got.value.save_as(_path(form, year, region))
                finally:
                    browser.close()
        except Error as e:
            raise SourceError(f"FAA CATS did not give its Form {form} export for {year} ({str(e).splitlines()[0][:160]})") from None

    with ThreadPoolExecutor(1) as pool:  # Playwright's sync API needs a thread without a running event loop
        pool.submit(run).result()


def _exports(form, year):
    """The paths of a form's nine regional exports for a year, fetched where missing or aged."""
    with _lock:
        due = [r for r in REGIONS if _stale(_path(form, year, r), year)]
        if due:
            _download(form, year, due)
    return tuple((_path(form, year, r), _path(form, year, r).stat().st_mtime) for r in REGIONS)


def _table(path):
    """(the header, the data rows) of an export: a title and a date stamp come before the header."""
    with path.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    start = next((i for i, r in enumerate(rows) if len(r) > 5), None)
    if start is None:
        return [], []
    head = rows[start]
    return head, [r for r in rows[start + 1:] if len(r) == len(head)]


def _value(text):
    """(amount, quantity): a value with a dollar sign is an amount, negative in parentheses; a bare number is a count.
    A hyphen is a line the airport left unpopulated, and is no value."""
    text = text.strip()
    if text in ("", "-"):
        return None
    negative = text.startswith("(") or text.startswith("-") or "($" in text
    digits = text.replace("$", "").replace(",", "").strip("()- ")
    try:
        number = float(digits)
    except ValueError:
        return None
    number = -number if negative else number
    return (number, None) if "$" in text else (None, number)


def _day(text):
    try:
        return dt.datetime.strptime(text.strip(), "%m/%d/%Y").date().isoformat()
    except ValueError:
        return None


# ---------- Form 5100-127 line items ----------

IDENT = ("Year", "State", "Hub size", "Airport name", "Location ID", "FYE", "Date filed")


@functools.lru_cache(maxsize=24)
def _lines(year, files):
    """Every reported line item of the year's Form 127 filings: {(location ID): (the filing's fields, [line records])}."""
    filings = {}
    for path, _ in files:
        head, rows = _table(path)
        if not head:
            continue
        if tuple(head[:len(IDENT)]) != IDENT:
            raise SourceError(f"FAA CATS changed its Form 127 export's columns ({', '.join(head[:7])})")
        for r in rows:
            loc = r[4].strip()
            filing = {"fiscal_year": int(r[0]) if r[0].isdigit() else year, "location_id": loc, "airport": r[3].strip(),
                      "state": r[1].strip(), "hub_size": r[2].strip() or None, "fiscal_year_end": _day(r[5]), "filing_date": _day(r[6])}
            lines = []
            for label, text in zip(head[len(IDENT):], r[len(IDENT):]):
                got = _value(text)
                if got is None:
                    continue
                lines.append({**filing, "id": f"{year}/{loc}/{label}", "url": REPORTS, "line_item": label,
                              "amount": got[0], "quantity": got[1]})
            filings[loc] = (filing, lines)
    return filings


def _years(s, form):
    lo, hi = s.between("fiscal_year")
    last, first = min(int(hi) if hi else today().year, today().year), max(int(lo) if lo else FIRST[form], FIRST[form])
    return list(range(last, first - 1, -1))


def _search_127(s):
    years = _years(s, "127")
    at = s.cursor or 0
    if at >= len(years):
        return page([])
    year = years[at]
    filings = _lines(year, _exports("127", year))
    rows = [line for _, (_, lines) in sorted(filings.items(), key=lambda x: (x[1][0]["airport"], x[0])) for line in lines]
    return page(rows, at + 1 if at + 1 < len(years) else None, order="latest year first; by airport, in the form's order")


def _read_127(key):
    year, loc, label = (key.split("/", 2) + ["", ""])[:3]
    if not year.isdigit():
        raise SourceError("A faa_form_127_line_items record_id is the one a search returned")
    filing = _lines(int(year), _exports("127", int(year))).get(loc)
    record = next((x for x in (filing[1] if filing else []) if x["line_item"] == label), None)
    if not record:
        raise SourceError(f"FAA CATS has no {year} Form 127 line {label!r} for {loc}")
    f = filing[0]
    head = [f"FAA Form 5100-127, Operating and Financial Summary: {f['airport']} ({loc}, {f['state']}), {f['hub_size'] or ''} hub",
            f"Fiscal year ending {f['fiscal_year_end']}; filed or revised {f['filing_date']}", ""]
    lines = [f"{x['line_item']}: " + (f"${x['amount']:,.2f}" if x["amount"] is not None else f"{x['quantity']:,.0f}") for x in filing[1]]
    return {**record, "document": "\n".join(head + lines), "published": f["filing_date"]}


# ---------- Form 5100-126 payments ----------

PAYEE = ("Year", "State", "Airport", "Location ID", "Govt Entity", "Govt Entity Type")


@functools.lru_cache(maxsize=24)
def _payments(year, files):
    """Every payment reported on the year's Form 126 filings: {location ID: [payment records]}. A payment type an
    airport reported as $0, or left unpopulated, is no payment."""
    out = {}
    for path, _ in files:
        head, rows = _table(path)
        if not head:
            continue
        if tuple(head[:len(PAYEE)]) != PAYEE:
            raise SourceError(f"FAA CATS changed its Form 126 export's columns ({', '.join(head[:6])})")
        for r in rows:
            loc, entity = r[3].strip(), r[4].strip()
            for kind, text in zip(head[len(PAYEE):], r[len(PAYEE):]):
                got = _value(text)
                if not got or not got[0]:
                    continue
                out.setdefault(loc, []).append({
                    "id": f"{year}/{loc}/{stable_id(entity, kind)[:12]}", "url": REPORTS, "fiscal_year": int(r[0]) if r[0].isdigit() else year,
                    "location_id": loc, "airport": r[2].strip(), "state": r[1].strip(), "government_entity": entity,
                    "entity_type": r[5].strip() or None, "payment_type": kind, "amount": got[0]})
    return out


def _search_126(s):
    years = _years(s, "126")
    at = s.cursor or 0
    if at >= len(years):
        return page([])
    year = years[at]
    found = _payments(year, _exports("126", year))
    rows = [p for loc in sorted(found, key=lambda x: (found[x][0]["airport"], x)) for p in found[loc]]
    return page(rows, at + 1 if at + 1 < len(years) else None, order="latest year first; by airport")


def _read_126(key):
    year, loc, _ = (key.split("/", 2) + ["", ""])[:3]
    if not year.isdigit():
        raise SourceError("A faa_form_126_payments record_id is the one a search returned")
    found = _payments(int(year), _exports("126", int(year))).get(loc) or []
    record = next((p for p in found if p["id"] == key), None)
    if not record:
        raise SourceError(f"FAA CATS has no such {year} Form 126 payment for {loc}")
    lines = [f"FAA Form 5100-126, Financial Government Payments: {record['airport']} ({loc}, {record['state']}), {year}", ""]
    lines += [f"{p['government_entity']} ({p['entity_type']}): {p['payment_type']}: ${p['amount']:,.2f}" for p in found]
    return {**record, "document": "\n".join(lines)}


FAA = "Federal Aviation Administration (CATS)"
HUBS = ("Large", "Medium", "Small", "NonHub")
AIRPORT = (F("fiscal_year", "number"), F("location_id", "identifier"), F("airport", "name"), F("state", "place"))
SERIES = [
    Series(
        id="faa_form_127_line_items", title="FAA Form 127 line items",
        record="a line item a commercial service airport reported for one fiscal year on FAA Form 5100-127, its Operating and "
               "Financial Summary: a revenue, expense, debt or capital figure, or a count such as enplanements",
        creator="airport sponsors", publisher=FAA, jurisdiction="federal", since=str(FIRST["127"]),
        fields=AIRPORT + (F("hub_size", HUBS), F("fiscal_year_end", "date"), F("filing_date", "date"), F("line_item", "text"),
                          F("amount", "amount"), F("quantity", "number")),
        search=_search_127, read=_read_127),
    Series(
        id="faa_form_126_payments", title="FAA Form 126 payments",
        record="a payment a commercial service airport reported making to a government entity in one fiscal year, by type "
               "of payment, on FAA Form 5100-126, its Financial Government Payment Report",
        creator="airport sponsors", publisher=FAA, jurisdiction="federal", since=str(FIRST["126"]),
        fields=AIRPORT + (F("government_entity", "name"), F("entity_type", "text"), F("payment_type", "text"), F("amount", "amount")),
        search=_search_126, read=_read_126),
]
