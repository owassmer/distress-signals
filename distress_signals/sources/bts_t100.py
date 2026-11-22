"""Two series from the Bureau of Transportation Statistics' T-100 Domestic Market (U.S. Carriers) table: what each
U.S. airline reports carrying between U.S. airports each month on Form 41 Schedule T-100.

  t100_domestic_markets       each row as BTS publishes it: one airline's passengers, freight and mail enplaned at
                              one airport and deplaned at another (an on-flight market) in one month and class of
                              service.
  t100_domestic_enplanements  the same rows summed by airline, airport of enplanement, month and class of service:
                              the passengers each airline boarded at each airport, as BTS's airport profiles count
                              them. The airline is the carrier that operated the flights; a regional airline flying
                              for a mainline brand reports under its own name.

BTS's bulk files on bts.gov refuse scripted readers; its TranStats download form gives a year of the table, every
month, as one zipped CSV. A year is downloaded when first asked for and kept in out/sources/t100.db: the two latest
years are asked for again after a week (BTS adds a month at a time and revises), earlier years after three months.
Rows that carry no passengers, freight or mail are left out. The U.S.-carriers table is used because the table of
all carriers holds back its newest three months, and foreign airlines may not carry domestic traffic.
"""

import csv
import datetime as dt
import io
import re
import sqlite3
import threading
import time
import zipfile

import requests
from bs4 import BeautifulSoup

from ..util import OUT, SourceError, session, today
from . import F, Series, page

FORM = "https://www.transtats.bts.gov/DL_SelectFields.aspx?gnoyr_VQ=FIL&QO_fu146_anzr=Nv4%20Pn44vr45"
TABLE = "https://www.transtats.bts.gov/Fields.asp?gnoyr_VQ=FIL"
COLUMNS = ("YEAR", "MONTH", "AIRLINE_ID", "UNIQUE_CARRIER", "UNIQUE_CARRIER_NAME", "ORIGIN", "ORIGIN_CITY_NAME",
           "ORIGIN_STATE_ABR", "DEST", "DEST_CITY_NAME", "DEST_STATE_ABR", "PASSENGERS", "FREIGHT", "MAIL", "DISTANCE", "CLASS")
CLASSES = {"F": "Scheduled Passenger/Cargo", "G": "Scheduled All Cargo", "L": "Non-Scheduled Passenger/Cargo",
           "P": "Non-Scheduled All Cargo"}
CODES = {v: k for k, v in CLASSES.items()}
DB = OUT / "sources" / "t100.db"
FIRST = 1990
_lock = threading.Lock()


# ---------- the yearly downloads ----------

_latest = None


def _latest_month():
    """The newest month TranStats holds, as (year, month), from its form's 'Latest Available Data'."""
    global _latest
    if _latest and time.time() - _latest[0] < 86400:
        return _latest[1]
    try:
        r = session().get(FORM, timeout=60)
        r.raise_for_status()
    except requests.RequestException as e:
        raise SourceError(f"BTS TranStats did not answer ({type(e).__name__})") from None
    m = re.search(r"Latest Available Data:\s*([A-Z][a-z]+)\s+(\d{4})", r.text)
    if not m:
        raise SourceError("BTS TranStats did not state its latest month")
    _latest = (time.time(), (int(m[2]), dt.datetime.strptime(m[1], "%B").month))
    return _latest[1]


def _download(year):
    """A year of the table, every month, as CSV rows."""
    s = requests.Session()
    s.headers["User-Agent"] = session().headers["User-Agent"]
    try:
        form_page = s.get(FORM, timeout=60)
        form_page.raise_for_status()
        form = next(f for f in BeautifulSoup(form_page.content, "html.parser").find_all("form") if f.find("input", {"name": "__VIEWSTATE"}))
        data = {i["name"]: i.get("value", "") for i in form.find_all("input", {"type": "hidden"}) if i.get("name")}
        data |= {"cboGeography": "All", "cboYear": str(year), "cboPeriod": "All", "chkDownloadZip": "on", "btnDownload": "Download"}
        data |= {c: "on" for c in COLUMNS}
        r = s.post(requests.compat.urljoin(FORM, form.get("action") or FORM), data=data, timeout=900)
        r.raise_for_status()
    except (requests.RequestException, StopIteration) as e:
        raise SourceError(f"BTS TranStats did not give the T-100 Domestic Market table for {year} ({type(e).__name__})") from None
    if r.content[:2] != b"PK":
        raise SourceError(f"BTS TranStats answered with something other than the {year} table")
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        name = next((n for n in z.namelist() if not n.lower().startswith("documentation")), None)
        text = z.read(name).decode("utf-8-sig", errors="replace")
    rows = list(csv.DictReader(io.StringIO(text)))
    if rows and not set(COLUMNS) <= set(rows[0]):
        raise SourceError(f"BTS TranStats changed the T-100 table's columns ({', '.join(rows[0])})")
    return rows


def _connect():
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB, timeout=120)
    con.executescript("""
        CREATE TABLE IF NOT EXISTS markets (year INT, month INT, airline_id TEXT, carrier_code TEXT, airline TEXT,
            origin TEXT, origin_city TEXT, origin_state TEXT, dest TEXT, dest_city TEXT, dest_state TEXT, class TEXT,
            passengers REAL, freight REAL, mail REAL, distance REAL);
        CREATE INDEX IF NOT EXISTS markets_origin ON markets (year, origin);
        CREATE INDEX IF NOT EXISTS markets_dest ON markets (year, dest);
        CREATE INDEX IF NOT EXISTS markets_airline ON markets (year, airline_id);
        CREATE TABLE IF NOT EXISTS enplanements (year INT, month INT, airline_id TEXT, carrier_code TEXT, airline TEXT,
            airport TEXT, city TEXT, state TEXT, class TEXT, passengers REAL, freight REAL, mail REAL, markets INT);
        CREATE INDEX IF NOT EXISTS enplanements_airport ON enplanements (year, airport);
        CREATE INDEX IF NOT EXISTS enplanements_airline ON enplanements (year, airline_id);
        CREATE TABLE IF NOT EXISTS years (year INT PRIMARY KEY, fetched REAL, months INT);""")
    return con


def _number(value):
    try:
        return float(value) if value not in ("", None) else 0.0
    except ValueError:
        return 0.0


def _year(year):
    """Make sure a year is in the index and not aged; returns how many months it holds."""
    latest = _latest_month()
    if year > latest[0] or year < FIRST:
        return 0
    with _lock:
        con = _connect()
        try:
            held = con.execute("SELECT fetched, months FROM years WHERE year=?", (year,)).fetchone()
            age = 7 * 86400 if year >= today().year - 1 else 90 * 86400
            if held and time.time() - held[0] < age:
                return held[1]
            rows = _download(year)
            out = {}
            for r in rows:
                p, f, m = _number(r["PASSENGERS"]), _number(r["FREIGHT"]), _number(r["MAIL"])
                if not (p or f or m):
                    continue
                key = (int(r["MONTH"]), r["AIRLINE_ID"], r["ORIGIN"], r["DEST"], r["CLASS"])
                if key in out:  # the same market reported twice: the traffic is the sum
                    row = out[key]
                    row[12], row[13], row[14] = row[12] + p, row[13] + f, row[14] + m
                    continue
                out[key] = [year, key[0], r["AIRLINE_ID"], r["UNIQUE_CARRIER"], r["UNIQUE_CARRIER_NAME"], r["ORIGIN"],
                            r["ORIGIN_CITY_NAME"], r["ORIGIN_STATE_ABR"], r["DEST"], r["DEST_CITY_NAME"], r["DEST_STATE_ABR"],
                            r["CLASS"], p, f, m, _number(r["DISTANCE"])]
            with con:
                con.execute("DELETE FROM markets WHERE year=?", (year,))
                con.execute("DELETE FROM enplanements WHERE year=?", (year,))
                con.executemany(f"INSERT INTO markets VALUES ({','.join('?' * 16)})", out.values())
                con.execute("""INSERT INTO enplanements SELECT year, month, airline_id, MAX(carrier_code), MAX(airline), origin,
                               MAX(origin_city), MAX(origin_state), class, SUM(passengers), SUM(freight), SUM(mail), COUNT(*)
                               FROM markets WHERE year=? GROUP BY year, month, airline_id, origin, class""", (year,))
                months = con.execute("SELECT COUNT(DISTINCT month) FROM markets WHERE year=?", (year,)).fetchone()[0]
                con.execute("INSERT OR REPLACE INTO years VALUES (?,?,?)", (year, time.time(), months))
            return months
        finally:
            con.close()


# ---------- searching the index ----------

def _where(s, columns):
    """SQL conditions for what a search gives that the index can apply: identifiers and classes equal, numbers within
    their range. Names and places are left to the registry."""
    sql, args = [], []
    for f in s.series.fields:
        column, given = columns.get(f.name), s.peek(f.name)
        if not column or not given or f.name == "year":
            continue
        if f.type == "number":
            lo, hi = s.between(f.name)
            sql += [f"{column} >= ?"] * (lo is not None) + [f"{column} <= ?"] * (hi is not None)
            args += [x for x in (lo, hi) if x is not None]
        elif f.type == "identifier" or f.listed:
            values = [CODES.get(v, v) if f.listed else v.upper() if f.name != "airline_id" else v for v in s.any(f.name)]
            sql.append(f"{column} IN ({','.join('?' * len(values))})")
            args += values
    return sql, args


def _years(s):
    lo, hi = s.between("year")
    last = min(int(hi) if hi else _latest_month()[0], _latest_month()[0])
    return list(range(last, max(int(lo) if lo else FIRST, FIRST) - 1, -1))


def _searcher(table, columns, order, make):
    def search(s):
        years = _years(s)
        sql, args = _where(s, columns)
        at = s.cursor or {"y": 0, "o": 0}
        if at["y"] >= len(years):
            return page([])
        year = years[at["y"]]
        _year(year)
        size = max(1, min(s.size, 5000))
        con = _connect()
        try:
            rows = con.execute(f"SELECT * FROM {table} WHERE year=? {''.join(' AND ' + x for x in sql)} ORDER BY {order} "
                               f"LIMIT ? OFFSET ?", [year, *args, size, at["o"]]).fetchall()
        finally:
            con.close()
        if len(rows) == size:
            nxt = {"y": at["y"], "o": at["o"] + size}
        else:
            nxt = {"y": at["y"] + 1, "o": 0} if at["y"] + 1 < len(years) else None
        return page([make(r) for r in rows], nxt, order="latest month first; by airport, most passengers first")
    return search


def _market(r):
    year, month, aid, code, airline, origin, ocity, ostate, dest, dcity, dstate, cls, p, f, m, dist = r
    return {"id": f"{year}-{month:02d}/{aid}/{origin}-{dest}/{cls}", "url": TABLE, "year": year, "month": month, "airline": airline,
            "carrier_code": code, "airline_id": aid, "origin": origin, "origin_city": ocity, "origin_state": ostate, "dest": dest,
            "dest_city": dcity, "dest_state": dstate, "service_class": CLASSES.get(cls, cls), "passengers": p, "freight_lbs": f,
            "mail_lbs": m, "distance_miles": dist}


def _enplanement(r):
    year, month, aid, code, airline, airport, city, state, cls, p, f, m, n = r
    return {"id": f"{year}-{month:02d}/{aid}/{airport}/{cls}", "url": TABLE, "year": year, "month": month, "airline": airline,
            "carrier_code": code, "airline_id": aid, "airport": airport, "city": city, "state": state,
            "service_class": CLASSES.get(cls, cls), "passengers": p, "freight_lbs": f, "mail_lbs": m, "markets": n}


def _key(key):
    m = re.fullmatch(r"(\d{4})-(\d{2})/([^/]+)/([A-Z0-9]+)(?:-([A-Z0-9]+))?/([A-Z])", key)
    if not m:
        raise SourceError("A T-100 record_id is the one a search returned")
    return int(m[1]), int(m[2]), m[3], m[4], m[5], m[6]


HEAD = "BTS T-100 Domestic Market (U.S. Carriers), as published: year, month, airline, origin, destination, class, passengers, freight (lbs), mail (lbs), distance (miles)"


def _line(r):
    return (f"{r[0]} | {r[1]} | {r[4]} ({r[3]}, airline ID {r[2]}) | {r[5]} {r[6]} | {r[8]} {r[9]} | {CLASSES.get(r[11], r[11])} | "
            f"{r[12]:,.0f} | {r[13]:,.0f} | {r[14]:,.0f} | {r[15]:,.0f}")


def _read_market(key):
    year, month, aid, origin, dest, cls = _key(key)
    _year(year)
    con = _connect()
    try:
        r = con.execute("SELECT * FROM markets WHERE year=? AND month=? AND airline_id=? AND origin=? AND dest=? AND class=?",
                        (year, month, aid, origin, dest, cls)).fetchone()
    finally:
        con.close()
    if not r:
        raise SourceError(f"BTS's T-100 table has no {key}")
    return {**_market(r), "document": f"{HEAD}\n{_line(r)}"}


def _read_enplanement(key):
    year, month, aid, airport, _, cls = _key(key)
    _year(year)
    con = _connect()
    try:
        r = con.execute("SELECT * FROM enplanements WHERE year=? AND month=? AND airline_id=? AND airport=? AND class=?",
                        (year, month, aid, airport, cls)).fetchone()
        rows = con.execute("SELECT * FROM markets WHERE year=? AND month=? AND airline_id=? AND origin=? AND class=? "
                           "ORDER BY passengers DESC", (year, month, aid, airport, cls)).fetchall()
    finally:
        con.close()
    if not r:
        raise SourceError(f"BTS's T-100 table has no {key}")
    lines = [f"The sum of {len(rows)} markets: the passengers, freight and mail {r[4]} enplaned at {airport} in {year}-{month:02d}, "
             f"{CLASSES.get(cls, cls)}.", "", HEAD] + [_line(x) for x in rows]
    return {**_enplanement(r), "document": "\n".join(lines)}


def _coverage(series):
    year, month = _latest_month()
    return f"{FIRST}-01", f"{year}-{month:02d}"


BTS = "Bureau of Transportation Statistics (TranStats)"
CLASS = F("service_class", tuple(CLASSES.values()))
AIRLINE = (F("year", "number"), F("month", "number"), F("airline", "name"), F("carrier_code", "identifier"),
           F("airline_id", "identifier"))
TRAFFIC = (F("passengers", "number"), F("freight_lbs", "number"), F("mail_lbs", "number"))
MARKET_COLUMNS = {"month": "month", "carrier_code": "carrier_code", "airline_id": "airline_id", "origin": "origin", "dest": "dest",
                  "service_class": "class", "passengers": "passengers", "freight_lbs": "freight", "mail_lbs": "mail",
                  "distance_miles": "distance"}
ENPLANEMENT_COLUMNS = {"month": "month", "carrier_code": "carrier_code", "airline_id": "airline_id", "airport": "airport",
                       "service_class": "class", "passengers": "passengers", "freight_lbs": "freight", "mail_lbs": "mail",
                       "markets": "markets"}
SERIES = [
    Series(
        id="t100_domestic_enplanements", title="T-100 domestic enplanements",
        record="the passengers, freight and mail a U.S. airline enplaned at a U.S. airport on domestic flights in one month "
               "and class of service: the sum of its T-100 domestic market rows from that airport",
        creator="U.S. airlines, reporting on Form 41 Schedule T-100", publisher=BTS, jurisdiction="federal", coverage=_coverage,
        fields=AIRLINE + (F("airport", "identifier"), F("city", "place"), F("state", "place"), CLASS) + TRAFFIC
               + (F("markets", "number"),),
        search=_searcher("enplanements", ENPLANEMENT_COLUMNS, "month DESC, airport, passengers DESC, airline_id", _enplanement),
        read=_read_enplanement),
    Series(
        id="t100_domestic_markets", title="T-100 domestic markets",
        record="the passengers, freight and mail a U.S. airline carried from one U.S. airport to another on its flights in "
               "one month and class of service (an on-flight market), as reported on Form 41 Schedule T-100",
        creator="U.S. airlines, reporting on Form 41 Schedule T-100", publisher=BTS, jurisdiction="federal", coverage=_coverage,
        fields=AIRLINE + (F("origin", "identifier"), F("origin_city", "place"), F("origin_state", "place"), F("dest", "identifier"),
                          F("dest_city", "place"), F("dest_state", "place"), CLASS) + TRAFFIC + (F("distance_miles", "number"),),
        search=_searcher("markets", MARKET_COLUMNS, "month DESC, origin, passengers DESC, dest, airline_id", _market),
        read=_read_market),
]
