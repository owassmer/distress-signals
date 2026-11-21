"""Six series of the record of Congress.

  bills                     GPO publishes every bill's status as a file, and each kind of bill's files as one archive.
                            The current Congress's archives are downloaded when they change and indexed in
                            out/sources/bills.db: each bill's fields, and its titles, summary, subjects and actions for
                            full-text search. A search is answered from the index; a read fetches the bill's own file.
  bill_text_versions,       GovInfo's own search finds the packages; each package's fields are read from its
  committee_reports,        descriptive metadata (MODS), kept in out/sources/govinfo/.
  congressional_hearings
  house_committee_meetings  The Clerk's Committee Repository lists meetings by week; a meeting's title, witnesses and
                            documents are on its own page, kept in out/sources/house_meetings/.
  senate_committee_meetings The Senate's file of the committee meetings now scheduled.
"""

import datetime as dt
import functools
import json
import os
import re
import sqlite3
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from xml.etree import ElementTree
from zipfile import ZipFile

import requests
from bs4 import BeautifulSoup

from .. import names
from ..util import OUT, SourceError, session, today
from . import F, Series, newest, outline, page, web

GOVINFO = "https://www.govinfo.gov"
BILLS_DB = OUT / "sources" / "bills.db"
MODS_KEPT = OUT / "sources" / "govinfo"
HOUSE = "https://docs.house.gov/Committee/Calendar/"
HOUSE_KEPT = OUT / "sources" / "house_meetings"
HOUSE_FIRST = dt.date(2012, 1, 1)
SENATE = "https://www.senate.gov/general/committee_schedules/hearings.xml"
CITE = {"HR": "H.R.", "S": "S.", "HJRES": "H.J. Res.", "SJRES": "S.J. Res.", "HCONRES": "H. Con. Res.",
        "SCONRES": "S. Con. Res.", "HRES": "H. Res.", "SRES": "S. Res."}
_lock = threading.Lock()


def _clean(value):
    return " ".join((value or "").split())


def _get(url, **options):
    try:
        r = session().get(url, timeout=options.pop("timeout", 90), **options)
        r.raise_for_status()
        return r
    except requests.RequestException as e:
        status = e.response.status_code if getattr(e, "response", None) is not None else type(e).__name__
        raise SourceError(f"{url.split('/')[2]} did not answer ({status})")


def _cite(kind, number):
    return f"{CITE.get((kind or '').upper(), kind)} {number}"


def _cite_key(value):
    """A bill number as it is compared: 'H.R. 5371', 'HR5371' and 'h.r.5371' are one number."""
    return "".join(ch for ch in str(value).lower() if ch.isalnum())


def _current_congress():
    day = today()
    return (day.year - 1789) // 2 + 1 - (1 if day.year % 2 == 1 and (day.month, day.day) < (1, 3) else 0)


# ---------- bills ----------

def _plain(html):
    return _clean(BeautifulSoup(html, "html.parser").get_text(" ")) if html else ""


def _bill(root):
    """(a bill's fields, the words it is found by) from its status file."""
    b = root.find("bill")
    kind, number, congress = b.findtext("type"), b.findtext("number"), b.findtext("congress")
    sponsor, latest = b.find("sponsors/item"), b.find("latestAction")
    summaries = sorted(b.findall("summaries/summary"), key=lambda x: x.findtext("updateDate") or "")
    titles = list(dict.fromkeys(_clean(t.findtext("title")) for t in b.findall("titles/item") if t.findtext("title")))
    subjects = [x.findtext("name") for x in b.findall("subjects/legislativeSubjects/item") if x.findtext("name")]
    actions = [_clean(a.findtext("text")) for a in b.findall("actions/item")]
    laws = [x.findtext("number") for x in b.findall("laws/item") if (x.findtext("type") or "").startswith("Public")]
    fields = {"id": f"{congress}-{kind.lower()}-{number}", "url": b.findtext("legislationUrl"),
              "congress": int(congress), "bill_number": _cite(kind, number), "bill_type": kind, "title": _clean(b.findtext("title")),
              "sponsor": _clean(sponsor.findtext("fullName")) if sponsor is not None else None,
              "cosponsors": len(b.findall("cosponsors/item")), "policy_area": b.findtext("policyArea/name"),
              "committees": [x.findtext("name") for x in b.findall("committees/item") if x.findtext("name")],
              "introduced_date": b.findtext("introducedDate"),
              "latest_action_date": latest.findtext("actionDate") if latest is not None else None,
              "latest_action": _clean(latest.findtext("text")) if latest is not None else None,
              "public_law": laws[0] if laws else None, "update_date": (b.findtext("updateDate") or "")[:10] or None}
    words = " \n".join(titles + subjects + [_plain(summaries[-1].findtext("text")) if summaries else ""] + actions)
    return fields, words


def _bills_index():
    """The index of the current Congress's bill status files, checked against GPO's archives every six hours."""
    congress = _current_congress()
    with _lock:
        BILLS_DB.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(BILLS_DB)
        try:
            con.executescript("CREATE TABLE IF NOT EXISTS bills (id TEXT PRIMARY KEY, bill_type TEXT, fields TEXT);"
                              "CREATE VIRTUAL TABLE IF NOT EXISTS words USING fts5(id UNINDEXED, bill_type UNINDEXED, title, text);"
                              "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);")
            meta = dict(con.execute("SELECT key, value FROM meta"))
            if meta.get("congress") != str(congress):
                con.executescript("DELETE FROM bills; DELETE FROM words; DELETE FROM meta;")
                meta = {}
            if time.time() - float(meta.get("checked") or 0) < 21600:
                return BILLS_DB
            for kind in CITE:
                kind = kind.lower()
                url = f"{GOVINFO}/bulkdata/BILLSTATUS/{congress}/{kind}/BILLSTATUS-{congress}-{kind}.zip"
                head = session().head(url, timeout=60)
                if head.status_code == 404:
                    continue  # a Congress that has no bill of this kind yet
                head.raise_for_status()
                modified = head.headers.get("Last-Modified") or head.headers.get("ETag") or ""
                if modified and meta.get(f"modified_{kind}") == modified:
                    continue
                with tempfile.TemporaryFile() as archive:
                    with _get(url, stream=True, timeout=300) as response:
                        for chunk in response.iter_content(1 << 20):
                            archive.write(chunk)
                    rows = []
                    with ZipFile(archive) as z:
                        for member in z.namelist():
                            if member.endswith(".xml"):
                                rows.append(_bill(ElementTree.fromstring(z.read(member))))
                con.execute("DELETE FROM bills WHERE bill_type=?", (kind,))
                con.execute("DELETE FROM words WHERE bill_type=?", (kind,))
                con.executemany("INSERT OR REPLACE INTO bills VALUES (?,?,?)", [(f["id"], kind, json.dumps(f)) for f, _ in rows])
                con.executemany("INSERT INTO words VALUES (?,?,?,?)", [(f["id"], kind, f["title"], w) for f, w in rows])
                con.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (f"modified_{kind}", modified))
            con.execute("INSERT OR REPLACE INTO meta VALUES ('congress', ?)", (str(congress),))
            con.execute("INSERT OR REPLACE INTO meta VALUES ('checked', ?)", (str(time.time()),))
            con.commit()
        except (requests.RequestException, sqlite3.Error, OSError, ElementTree.ParseError) as e:
            raise SourceError(f"GPO's bill status files could not be read: {type(e).__name__}: {str(e)[:150]}")
        finally:
            con.close()
    return BILLS_DB


@functools.lru_cache(maxsize=1)
def _bill_rows(stamp):
    con = sqlite3.connect(f"file:{BILLS_DB}?mode=ro", uri=True)
    try:
        rows = [json.loads(r[0]) for r in con.execute("SELECT fields FROM bills")]
    finally:
        con.close()
    return sorted(rows, key=lambda r: (r["latest_action_date"] or "", r["id"]), reverse=True)


def _match(query):
    """The query as full-text search takes it: every word, and each quoted phrase as a phrase."""
    phrases = re.findall(r'"([^"]+)"', query)
    words = names.tokens(re.sub(r'"[^"]+"', " ", query))
    return " AND ".join(['"' + " ".join(names.tokens(p)) + '"' for p in phrases if names.tokens(p)] + [f'"{w}"' for w in words])


def _search_bills(s):
    db = _bills_index()
    rows = _bill_rows(db.stat().st_mtime)
    numbers = {_cite_key(v) for v in s.any("bill_number")}
    if numbers:
        rows = [r for r in rows if _cite_key(r["bill_number"]) in numbers]
    words = s.query()
    if words and _match(words):
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            found = {r[0] for r in con.execute("SELECT id FROM words WHERE words MATCH ?", (_match(words),))}
        finally:
            con.close()
        rows = [r for r in rows if r["id"] in found]
    return page(rows, order="latest action first")


def _read_bill(key):
    m = re.fullmatch(r"(\d+)-([a-z]+)-(\d+)", key)
    if not m or m[2].upper() not in CITE:
        raise SourceError("A bills record_id is the one a search returned")
    congress, kind, number = m.groups()
    root = ElementTree.fromstring(_get(f"{GOVINFO}/bulkdata/BILLSTATUS/{congress}/{kind}/BILLSTATUS-{congress}{kind}{number}.xml").content)
    fields, _ = _bill(root)
    b = root.find("bill")
    item = lambda e, *tags: {t: _clean(e.findtext(t)) for t in tags if _clean(e.findtext(t))}  # noqa: E731
    summaries = sorted(b.findall("summaries/summary"), key=lambda x: x.findtext("updateDate") or "")
    record = {
        "Titles": list(dict.fromkeys(f"{_clean(t.findtext('titleType'))}: {_clean(t.findtext('title'))}" for t in b.findall("titles/item"))),
        "Sponsors": [_clean(x.findtext("fullName")) for x in b.findall("sponsors/item")],
        "Cosponsors": [_clean(x.findtext("fullName")) for x in b.findall("cosponsors/item")],
        "Committees": [{"committee": _clean(c.findtext("name")),
                        "activities": [f"{(a.findtext('date') or '')[:10]} {_clean(a.findtext('name'))}" for a in c.findall("activities/item")]}
                       for c in b.findall("committees/item")],
        "Actions": [f"{a.findtext('actionDate')} {_clean(a.findtext('text'))}" for a in b.findall("actions/item")],
        "Laws": [f"{x.findtext('type')} {x.findtext('number')}" for x in b.findall("laws/item")],
        "Summary": ({"as of": summaries[-1].findtext("actionDate"), "version": _clean(summaries[-1].findtext("actionDesc")),
                     "text": _plain(summaries[-1].findtext("text"))} if summaries else None),
        "Subjects": [x.findtext("name") for x in b.findall("subjects/legislativeSubjects/item")],
        "Related bills": [f"{_cite(x.findtext('type'), x.findtext('number'))} ({x.findtext('congress')}th Congress): {_clean(x.findtext('title'))}"
                          for x in b.findall("relatedBills/item")],
        "Cost estimates": [item(x, "pubDate", "title", "description", "url") for x in b.findall("cboCostEstimates/item")],
        "Text versions": [f"{(x.findtext('date') or '')[:10]} {_clean(x.findtext('type'))}".strip() for x in b.findall("textVersions/item")],
    }
    links = [{"text": f"Text: {_clean(x.findtext('type'))}", "url": x.findtext("formats/item/url")}
             for x in b.findall("textVersions/item") if x.findtext("formats/item/url")]
    links += [{"text": _clean(x.findtext("title")), "url": x.findtext("url")} for x in b.findall("cboCostEstimates/item") if x.findtext("url")]
    return {**fields, "document": "\n".join(outline(record)), "links": links}


def _bills_span(series):
    rows = _bill_rows(_bills_index().stat().st_mtime)
    return (min((r["introduced_date"] for r in rows if r["introduced_date"]), default=None),
            max((r["latest_action_date"] for r in rows if r["latest_action_date"]), default=None))


# ---------- GovInfo's collections ----------

def _mods(package):
    """A package's descriptive metadata: its own elements, without those of the parts and items related to it."""
    kept = MODS_KEPT / f"{package}.xml"
    if not kept.is_file():
        for attempt in range(4):  # the server now and then answers with an empty body
            content = _get(f"{GOVINFO}/metadata/pkg/{package}/mods.xml").content
            try:
                ElementTree.fromstring(content)
                break
            except ElementTree.ParseError:
                time.sleep(0.5 * (attempt + 1))
        else:
            raise SourceError(f"GovInfo did not return the description of {package}")
        MODS_KEPT.mkdir(parents=True, exist_ok=True)
        kept.write_bytes(content)
    root = ElementTree.parse(kept).getroot()
    found = []
    for child in root:
        if child.tag.rpartition("}")[2] != "relatedItem":
            found += [(e.tag.rpartition("}")[2], _clean(e.text), e.attrib) for e in child.iter()]
    return found


def _package(package):
    """The fields a package's metadata states, for whichever of the collections it belongs to."""
    mods = _mods(package)
    texts = lambda tag, **attrs: [t for g, t, a in mods if g == tag and t and all(a.get(k) == v for k, v in attrs.items())]  # noqa: E731
    first = lambda tag, **attrs: next(iter(texts(tag, **attrs)), None)  # noqa: E731
    bills = list(dict.fromkeys(_cite(a.get("type"), a.get("number")) for g, _, a in mods if g == "bill" and a.get("context") == "PRIMARY"))
    kind, number, version = first("docClass"), first("billNumber"), first("billVersion")
    return {"id": package, "url": f"{GOVINFO}/app/details/{package}", "package_id": package,
            "congress": int(first("congress")) if (first("congress") or "").isdigit() else None,
            "chamber": (first("chamber") or first("originChamber") or "").capitalize() or None,
            "citation": first("identifier", type="preferred citation"),
            "bill_number": [_cite(kind, number)] if number else bills, "version": (version or "").upper() or None,
            "title": first("officialTitle") or first("title"), "short_title": first("shortTitle"),
            "committee": list(dict.fromkeys(texts("name", type="authority-standard"))),
            "date_issued": first("dateIssued"), "held_date": texts("heldDate"), "witness": texts("witness"),
            "appropriation": {"true": "yes", "false": "no"}.get(first("isAppropriation") or "")}


WINDOW = 100          # the packages of one request, which is the most read as one span of dates
_windows = {}


def _found(terms, lo, hi, index=0):
    """(the count, one page of [(date shown, package)]) of GovInfo's search between two dates of issue."""
    body = {"query": " AND ".join(terms + [f"publishdate:range({lo},{hi})"]), "offset": index, "pageSize": 100, "sortBy": "2",
            "historical": True}
    try:
        r = session().post(f"{GOVINFO}/wssearch/search", json=body, timeout=90)
        r.raise_for_status()
        got = r.json()
    except (requests.RequestException, ValueError) as e:
        raise SourceError(f"GovInfo's search did not answer ({type(e).__name__})")
    return int(got.get("iTotalCount") or 0), [(_shown_date(h.get("line2")), h["fieldMap"]["packageid"]) for h in got.get("resultSet") or []]


def _shown_date(line):
    """The date a result line ends with ('... Wednesday, November 18, 2026.'), which orders the packages of a span."""
    try:
        return dt.datetime.strptime(line.rstrip(". ").rpartition(". ")[2], "%A, %B %d, %Y").date().isoformat()
    except (ValueError, AttributeError):
        return ""


def _window(terms, lo, hi):
    """(the count, the packages latest first) between two dates; the packages are None where a span of more than
    one day holds more than one request returns. GovInfo's search orders the packages of one date differently
    from one request to the next, and its count includes the parts of a package, so a span is read in one
    request and ordered here. A single day that holds more is read page by page until a pass adds nothing."""
    key = (tuple(terms), lo, hi)
    hit = _windows.get(key)
    if hit and time.time() - hit[0] < 900:
        return hit[1]
    count, hits = _found(terms, lo, hi)
    found = {p: d for d, p in hits}
    if count > WINDOW and lo != hi:
        found = None
    elif count > WINDOW:
        for _ in range(4):
            before = len(found)
            for index in range(min(100, -(-count // 100))):
                found |= {p: d for d, p in _found(terms, lo, hi, index)[1]}
            if len(found) == before:
                break
    packages = None if found is None else [p for p, d in sorted(found.items(), key=lambda x: (x[1], x[0]), reverse=True)]
    _windows[key] = (time.time(), (count, packages))
    return count, packages


def _asked(query):
    """The query as GovInfo's search takes it: each word, and each quoted phrase as a phrase. A single word is
    left unquoted, because a quoted word is matched exactly and misses the word printed inside quotation marks."""
    phrases = [names.tokens(p) for p in re.findall(r'"([^"]+)"', query)]
    words = names.tokens(re.sub(r'"[^"]+"', " ", query))
    return ['"' + " ".join(p) + '"' if len(p) > 1 else p[0] for p in phrases if p] + words


def _search_packages(collection):
    def search(s):
        given = s.any("package_id")
        if given:
            return page([_package(p) for p in given])
        terms = [f"collection:({collection})"] + (_asked(s.query()) if s.words() else [])
        number = s.peek("congress")
        if number.get("lo") is not None and number.get("lo") == number.get("hi"):
            terms.append(f"congress:{int(s.between('congress')[0])}")
        lo, hi = s.between("date_issued")
        first, last = dt.date.fromisoformat(lo or "1789-01-01"), dt.date.fromisoformat(hi) if hi else today() + dt.timedelta(days=1)
        at = s.cursor or {"hi": last.isoformat(), "span": (last - first).days + 1, "o": 0}
        end, span, total = dt.date.fromisoformat(at["hi"]), at["span"], None
        while True:
            start = max(first, end - dt.timedelta(days=span - 1))
            count, packages = _window(terms, start.isoformat(), end.isoformat())
            if s.cursor is None and total is None:
                total = count
            if packages is None:
                span = (span + 1) // 2
            elif not packages and start > first:  # nothing issued in this span: step back, in longer strides
                end, span = start - dt.timedelta(days=1), span * 4
            else:
                break
        part = packages[at["o"]:at["o"] + s.size]
        with ThreadPoolExecutor(6) as ex:
            records = list(ex.map(_package, part))
        if at["o"] + len(part) < len(packages):
            nxt = {"hi": end.isoformat(), "span": span, "o": at["o"] + len(part)}
        elif start > first:
            nxt = {"hi": (start - dt.timedelta(days=1)).isoformat(), "span": span * 2 if count < WINDOW // 4 else span, "o": 0}
        else:
            nxt = None
        return page(records, nxt, total, "latest issued first")
    return search


def _read_package(key):
    if not re.fullmatch(r"[A-Z]+-[A-Za-z0-9-]+", key):
        raise SourceError("A GovInfo record_id is the package ID a search returned")
    (MODS_KEPT / f"{key}.xml").unlink(missing_ok=True)
    record = _package(key)
    base = f"{GOVINFO}/content/pkg/{key}"
    links = [{"text": "PDF", "url": f"{base}/pdf/{key}.pdf"}, {"text": "Text", "url": f"{base}/html/{key}.htm"}]
    for url in (links[1]["url"], links[0]["url"]):
        try:
            text = web.read(url)["text"]
            break
        except Exception:  # a package published in one of the two forms
            text = None
    if not text:
        raise SourceError(f"GovInfo did not return the text of {key}")
    return {**record, "document": text, "links": links, "published": record["date_issued"]}


# ---------- House committee meetings ----------

def _week(day):
    """The Sunday that begins a day's week, as the Committee Repository keeps weeks."""
    return day - dt.timedelta(days=(day.weekday() + 1) % 7)


def _house_week(sunday):
    """[(event ID, date)] for a week, latest first."""
    span = f"{sunday:%m%d%Y}_{sunday + dt.timedelta(days=6):%m%d%Y}"
    soup = BeautifulSoup(_get(f"{HOUSE}ByWeek.aspx", params={"WeekOf": span}).content, "html.parser")
    out = {}
    for cell in soup.select("td[class^=calendarRow]"):
        day = re.search(r"DayID=(\d{2})(\d{2})(\d{4})", str(cell.select_one("a[id*=LinkButton]") or ""))
        if not day:
            continue
        date = f"{day[3]}-{day[1]}-{day[2]}"
        for p in cell.find_all("p"):
            link = p.select_one('a[href*="EventID="]')
            if link:
                event = link["href"].rpartition("EventID=")[2]
                out[event] = (event, date)
    return sorted(out.values(), key=lambda r: (r[1], r[0]), reverse=True)


def _house_meeting(event, date=None):
    """A meeting from its own page: its kind and title, committee, time and place, witnesses and documents."""
    kept = HOUSE_KEPT / f"{event}.json"
    if kept.is_file():
        found = json.loads(kept.read_text())
        settled = (found.get("meeting_date") or "9") < (today() - dt.timedelta(days=14)).isoformat()
        if settled or time.time() - kept.stat().st_mtime < 10800:
            return found
    url = f"{HOUSE}ByEvent.aspx?EventID={event}"
    soup = BeautifulSoup(_get(url).content, "html.parser")
    h1 = soup.select_one("h1")
    if not h1 or not soup.select_one(".meetingTime"):
        raise SourceError(f"The Committee Repository has no meeting {event}")
    committee = _clean(h1.select_one("small").get_text(" ", strip=True)) if h1.select_one("small") else None
    heading = _clean("".join(h1.find_all(string=True, recursive=False)))
    kind, _, title = heading.partition(":")
    when = _clean(soup.select_one(".meetingTime").get_text(" ", strip=True))
    held = None
    try:
        held = dt.datetime.strptime(when.partition(" (")[0], "%A, %B %d, %Y").date().isoformat()
    except ValueError:
        pass
    witnesses = []
    for p in soup.select(".witnessPanel p"):
        name, role = p.select_one("strong"), p.select_one("small")
        if name and _clean(name.get_text(" ", strip=True)):
            witnesses.append(", ".join(x for x in (_clean(name.get_text(" ", strip=True)), _clean(role.get_text(" ", strip=True)) if role else "") if x))
    links = []
    for a in soup.select('a[href*="/meetings/"]'):
        item = a.find_parent("li")
        label = _clean(item.get_text(" ", strip=True).split("[")[0]) if item else "Document"
        panel = a.find_parent(class_="witnessPanel")
        who = _clean(panel.select_one("strong").get_text(" ", strip=True)) if panel and panel.select_one("strong") else None
        links.append({"text": f"{label}: {who}" if who else label, "url": a["href"].replace("http://", "https://")})
    body = h1.find_parent("div").parent
    for x in body.select("script, style"):
        x.decompose()
    found = {"id": event, "url": url, "event_id": event, "meeting_date": held or date, "meeting_type": _clean(kind) if title else None,
             "committee": committee, "title": _clean(title or heading), "witness": witnesses,
             "document": "\n".join(_clean(line) for line in body.get_text("\n", strip=True).splitlines() if _clean(line)), "links": links}
    HOUSE_KEPT.mkdir(parents=True, exist_ok=True)
    kept.write_text(json.dumps(found))
    return found


def _listed(m):
    return {k: v for k, v in m.items() if k not in ("document", "links")}


def _search_house(s):
    if s.peek("event_id"):
        return page([_listed(_house_meeting(e)) for e in s.any("event_id")])
    lo, hi = s.between("meeting_date")
    first = _week(dt.date.fromisoformat(lo) if lo else HOUSE_FIRST)
    last = _week(dt.date.fromisoformat(hi) if hi else today() + dt.timedelta(days=56))
    at = s.cursor or {"w": last.isoformat(), "o": 0}
    sunday = dt.date.fromisoformat(at["w"])
    if sunday < first:
        return page([])
    rows = [r for r in _house_week(sunday) if (not lo or r[1] >= lo) and (not hi or r[1] <= hi)]
    part = rows[at["o"]:at["o"] + s.size]
    with ThreadPoolExecutor(8) as ex:
        records = [_listed(m) for m in ex.map(lambda r: _house_meeting(r[0], r[1]), part)]
    if at["o"] + len(part) < len(rows):
        nxt = {"w": at["w"], "o": at["o"] + len(part)}
    else:
        before = sunday - dt.timedelta(days=7)
        nxt = {"w": before.isoformat(), "o": 0} if before >= first else None
    return page(records, nxt, order="latest meeting first")


def _read_house(key):
    if not key.isdigit():
        raise SourceError("A house_committee_meetings record_id is the one a search returned")
    (HOUSE_KEPT / f"{key}.json").unlink(missing_ok=True)
    return _house_meeting(key)


# ---------- Senate committee meetings ----------

_senate = None


def _senate_meetings():
    global _senate
    if _senate and time.time() - _senate[0] < 1800:
        return _senate[1]
    root = ElementTree.fromstring(_get(SENATE).content)
    out = []
    for m in root.iter("meeting"):
        raw = {e.tag: _clean(e.text) for e in m if _clean(e.text)}
        if not raw.get("identifier"):
            continue  # the file's line for a day with nothing scheduled
        out.append({"id": raw["identifier"], "url": "https://www.senate.gov/committees/hearings_meetings.htm", "meeting_id": raw["identifier"],
                    "meeting_date": raw.get("date_iso_8601"), "committee": raw.get("committee"), "subcommittee": raw.get("sub_cmte"),
                    "meeting_type": raw.get("type"), "matter": raw.get("matter"), "_raw": raw})
    _senate = (time.time(), sorted(out, key=lambda r: (r["meeting_date"] or "", r["id"])))
    return _senate[1]


def _search_senate(s):
    return page([{k: v for k, v in m.items() if k != "_raw"} for m in _senate_meetings()], order="soonest first")


def _read_senate(key):
    m = next((m for m in _senate_meetings() if m["id"] == key), None)
    if not m:
        raise SourceError(f"The Senate's schedule no longer lists meeting {key}")
    links = [{"text": "Video", "url": m["_raw"]["video_url"]}] if m["_raw"].get("video_url") else []
    return {**{k: v for k, v in m.items() if k != "_raw"}, "links": links,
            "document": "\n".join(outline({k.replace("_", " "): v for k, v in m["_raw"].items() if not k.endswith("iso_8601")}))}


def _senate_span(series):
    dates = [m["meeting_date"] for m in _senate_meetings() if m["meeting_date"]]
    return (min(dates), max(dates)) if dates else (None, None)


GPO = "Government Publishing Office"
PACKAGE = (F("package_id", "identifier"), F("congress", "number"))
SERIES = [
    Series(
        id="bills", title="bills in Congress",
        record="a bill or resolution of the current Congress, with its sponsor, committees and latest action; its status file "
               "adds every action, its summary, cost estimates and related bills",
        creator="U.S. Congress", publisher=GPO, jurisdiction="federal", coverage=_bills_span,
        fields=(F("congress", "number"), F("bill_number", "identifier"), F("bill_type", tuple(CITE)), F("title", "text"),
                F("sponsor", "name"), F("cosponsors", "number"), F("policy_area", "text"), F("committees", "name", many=True),
                F("introduced_date", "date"), F("latest_action_date", "date"), F("latest_action", "text"),
                F("public_law", "identifier"), F("update_date", "date"), F("full_text", "full text")),
        search=_search_bills, read=_read_bill),
    Series(
        id="bill_text_versions", title="bill texts",
        record="a version of the text of a bill or resolution: as introduced, reported, engrossed, enrolled or another stage",
        creator="U.S. Congress", publisher=GPO, jurisdiction="federal", since="1993", coverage=newest("date_issued"),
        fields=PACKAGE + (F("bill_number", "identifier", many=True), F("version", "identifier"), F("title", "text"),
                          F("short_title", "text"), F("committee", "name", many=True), F("appropriation", ("yes", "no")),
                          F("date_issued", "date"), F("full_text", "full text")),
        search=_search_packages("BILLS"), read=_read_package),
    Series(
        id="committee_reports", title="congressional committee reports",
        record="a report of a House, Senate or conference committee to its chamber, most often on a bill it has reported",
        creator="committees of Congress", publisher=GPO, jurisdiction="federal", since="1995", coverage=newest("date_issued"),
        fields=PACKAGE + (F("citation", "identifier"), F("chamber", ("House", "Senate")), F("committee", "name", many=True),
                          F("bill_number", "identifier", many=True), F("title", "text"), F("date_issued", "date"),
                          F("full_text", "full text")),
        search=_search_packages("CRPT"), read=_read_package),
    Series(
        id="congressional_hearings", title="congressional hearing transcripts",
        record="the published transcript of a hearing before a committee of Congress, with its witnesses and their affiliations",
        creator="committees of Congress", publisher=GPO, jurisdiction="federal", since="1985", coverage=newest("date_issued"),
        fields=PACKAGE + (F("chamber", ("House", "Senate")), F("committee", "name", many=True), F("title", "text"),
                          F("held_date", "date", many=True), F("witness", "name", many=True), F("date_issued", "date"),
                          F("full_text", "full text")),
        search=_search_packages("CHRG"), read=_read_package),
    Series(
        id="house_committee_meetings", title="House committee meetings",
        record="a hearing, markup or other meeting of a House committee or subcommittee, held or scheduled, with its witnesses "
               "and the documents filed for it",
        creator="committees of the House of Representatives", publisher="Clerk of the House (Committee Repository)",
        jurisdiction="federal", since=str(HOUSE_FIRST.year), coverage=newest("meeting_date"),
        fields=(F("event_id", "identifier"), F("meeting_date", "date"), F("meeting_type", "text"), F("committee", "name"),
                F("title", "text"), F("witness", "name", many=True)),
        search=_search_house, read=_read_house),
    Series(
        id="senate_committee_meetings", title="Senate committee meetings",
        record="a hearing or meeting of a Senate committee or subcommittee now on the Senate's schedule",
        creator="committees of the Senate", publisher="Secretary of the Senate", jurisdiction="federal", coverage=_senate_span,
        fields=(F("meeting_id", "identifier"), F("meeting_date", "date"), F("committee", "name"), F("subcommittee", "name"),
                F("meeting_type", "text"), F("matter", "text")),
        search=_search_senate, read=_read_senate),
]
