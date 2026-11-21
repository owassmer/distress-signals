"""Two series: contract opportunity notices active on SAM.gov, and the Department of War's daily contract
announcements.

  sam_contract_opportunities   GSA publishes every active notice in one daily file (about 225 MB). It is downloaded
                               when it changes and indexed in out/sources/sam_notices.db: each notice's fields, and
                               its title and description for full-text search. A search is answered from the index.
  dod_contract_announcements   The Department lists its daily releases in a feed (the last 500), but its release
                               pages refuse scripted readers. Each release's text is read from the Internet
                               Archive's copy of the page, kept in out/sources/dod_contracts/, and split into its
                               announcements: one paragraph for each contract action, under its military
                               department's heading.
"""

import csv
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
from email.utils import formatdate, parsedate_to_datetime
from pathlib import Path
from xml.etree import ElementTree

from bs4 import BeautifulSoup

from .. import names
from ..util import OUT, SourceError, http_json, session, to_float
from . import F, Series, page

SAM_CSV = ("https://sam.gov/api/prod/fileextractservices/v1/api/download/"
           "Contract%20Opportunities/datagov/ContractOpportunitiesFullCSV.csv")
SAM_DB = OUT / "sources" / "sam_notices.db"
SAM_HEADERS = {"NoticeId", "Title", "Type", "PostedDate", "ResponseDeadLine", "AwardNumber", "Awardee", "Description"}
DOD_FEED = "https://www.war.gov/DesktopModules/ArticleCS/RSS.ashx?ContentType=400&Site=945&max=500"
DOD_KEPT = OUT / "sources" / "dod_contracts"
WAYBACK = "https://archive.org/wayback/available"
_cache_lock = threading.Lock()


# ---------- SAM.gov contract opportunities ----------

def _csv_path():
    folder = OUT / "sources"
    folder.mkdir(parents=True, exist_ok=True)
    cache = folder / "sam_active_notices.csv"
    with _cache_lock:
        headers = {"If-Modified-Since": formatdate(cache.stat().st_mtime, usegmt=True)} if cache.is_file() else {}
        with session().get(SAM_CSV, headers=headers, timeout=90, stream=True) as response:
            if response.status_code == 304 and cache.is_file():
                return cache
            response.raise_for_status()
            modified = response.headers.get("Last-Modified")
            # Keep the previous copy intact on a failed or interrupted transfer.
            with tempfile.NamedTemporaryFile(dir=folder, delete=False) as temp:
                name = Path(temp.name)
                try:
                    for chunk in response.iter_content(1024 * 1024):
                        temp.write(chunk)
                    temp.close()
                    with name.open("rb") as handle:
                        header = next(csv.reader([handle.readline().decode("utf-8-sig")]), [])
                    if not SAM_HEADERS.issubset(header):
                        raise SourceError("GSA did not return the active SAM notice CSV")
                    if modified:
                        stamp = parsedate_to_datetime(modified).timestamp()
                        os.utime(name, (stamp, stamp))
                    os.replace(name, cache)
                finally:
                    name.unlink(missing_ok=True)
    return cache


def _lines(handle):
    # GSA's CSV mixes UTF-8 with Windows-1252 bytes in notice text.
    replacements = {0xDC00 + i: bytes([i]).decode("cp1252", errors="replace") for i in range(128, 256)}
    for index, line in enumerate(handle):
        if index == 0:
            line = line.removeprefix(b"\xef\xbb\xbf")
        try:
            yield line.decode("utf-8")
        except UnicodeDecodeError:
            yield line.decode("utf-8", errors="surrogateescape").translate(replacements)


def _notice(r):
    day = lambda v: (v or "")[:10] or None  # noqa: E731
    return {"id": r["NoticeId"], "url": r.get("Link") or f"https://sam.gov/opp/{r['NoticeId']}/view",
            "notice_id": r["NoticeId"], "title": r.get("Title"), "notice_type": r.get("Type"),
            "solicitation_number": r.get("Sol#") or None, "department": r.get("Department/Ind.Agency") or None,
            "sub_tier": r.get("Sub-Tier") or None, "office": r.get("Office") or None, "posted_date": day(r.get("PostedDate")),
            "response_deadline": day(r.get("ResponseDeadLine")), "archive_date": day(r.get("ArchiveDate")),
            "set_aside": r.get("SetASide") or None, "naics": r.get("NaicsCode") or None, "psc": r.get("ClassificationCode") or None,
            "place_of_performance_state": r.get("PopState") or None, "award_number": r.get("AwardNumber") or None,
            "award_date": day(r.get("AwardDate")), "award_amount": to_float(r.get("Award$")), "awardee": r.get("Awardee") or None}


def _index():
    """The index of GSA's file, built again when the file changes."""
    path = _csv_path()
    stamp = str(int(path.stat().st_mtime))
    with _cache_lock:
        if SAM_DB.exists():
            con = sqlite3.connect(SAM_DB)
            try:
                if (con.execute("SELECT value FROM meta WHERE key='stamp'").fetchone() or [None])[0] == stamp:
                    return SAM_DB
            except sqlite3.Error:
                pass
            finally:
                con.close()
        tmp = SAM_DB.with_name(f"sam_notices.{os.getpid()}.tmp")
        tmp.unlink(missing_ok=True)
        con = sqlite3.connect(tmp)
        try:
            con.executescript("CREATE TABLE notices (notice_id TEXT PRIMARY KEY, fields TEXT);"
                              "CREATE VIRTUAL TABLE words USING fts5(notice_id UNINDEXED, title, description);"
                              "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);")
            csv.field_size_limit(1 << 30)
            with path.open("rb") as handle:
                batch = []
                for r in csv.DictReader(_lines(handle)):
                    if not r.get("NoticeId"):
                        continue
                    batch.append((r["NoticeId"], json.dumps(_notice(r)), r.get("Description") or ""))
                    if len(batch) >= 5000:
                        _insert(con, batch)
                        batch = []
                _insert(con, batch)
            con.execute("INSERT INTO meta VALUES ('stamp', ?)", (stamp,))
            con.commit()
        finally:
            con.close()
        os.replace(tmp, SAM_DB)
        _notices.cache_clear()
    return SAM_DB


def _insert(con, batch):
    con.executemany("INSERT OR REPLACE INTO notices VALUES (?,?)", [(i, f) for i, f, _ in batch])
    con.executemany("INSERT INTO words VALUES (?,?,?)", [(i, json.loads(f).get("title") or "", d) for i, f, d in batch])


@functools.lru_cache(maxsize=1)
def _notices(stamp=None):
    con = sqlite3.connect(f"file:{SAM_DB}?mode=ro", uri=True)
    try:
        rows = [json.loads(r[0]) for r in con.execute("SELECT fields FROM notices")]
    finally:
        con.close()
    rows.sort(key=lambda r: r["posted_date"] or "", reverse=True)
    return rows


def _match(query):
    """The query as full-text search takes it: every word, and each quoted phrase as a phrase."""
    phrases = re.findall(r'"([^"]+)"', query)
    words = names.tokens(re.sub(r'"[^"]+"', " ", query))
    return " AND ".join(['"' + " ".join(names.tokens(p)) + '"' for p in phrases if names.tokens(p)] + [f'"{w}"' for w in words])


def _search_notices(s):
    try:
        db = _index()
    except (OSError, csv.Error, sqlite3.Error) as e:
        raise SourceError(f"GSA's file of active notices could not be read: {type(e).__name__}: {str(e)[:150]}")
    rows = _notices(db.stat().st_mtime)
    words = s.query()
    if words and _match(words):
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            found = {r[0] for r in con.execute("SELECT notice_id FROM words WHERE words MATCH ?", (_match(words),))}
        finally:
            con.close()
        rows = [r for r in rows if r["id"] in found]
    return page(rows, order="latest posted first")


def _read_notice(key):
    con = sqlite3.connect(f"file:{_index()}?mode=ro", uri=True)
    try:
        row = con.execute("SELECT n.fields, w.description FROM notices n JOIN words w ON w.notice_id = n.notice_id "
                          "WHERE n.notice_id=?", (key,)).fetchone()
    finally:
        con.close()
    if not row:
        raise SourceError("This notice is no longer in GSA's file of active notices.")
    record = json.loads(row[0])
    text = BeautifulSoup(row[1], "html.parser").get_text("\n", strip=True) if "<" in row[1] else row[1]
    return {**record, "document": text, "published": record["posted_date"]}


def _notices_span(series):
    dates = [r["posted_date"] for r in _notices(_index().stat().st_mtime) if r["posted_date"]]
    return (min(dates), max(dates)) if dates else (None, None)


# ---------- Department of War contract announcements ----------

_feed = None


def _releases():
    """[(article, title, the release's address, its date)], newest first, from the Department's feed."""
    global _feed
    if _feed and time.time() - _feed[0] < 1800:
        return _feed[1]
    try:
        response = session().get(DOD_FEED, timeout=90)
        response.raise_for_status()
        items = ElementTree.fromstring(response.content).iter("item")
        out = []
        for item in items:
            link = (item.findtext("link") or "").strip()
            m = re.search(r"/Article/(\d+)/", link)
            if m and (item.findtext("title") or "").strip().casefold().startswith("contracts for"):
                out.append((m[1], item.findtext("title").strip(), link,
                            parsedate_to_datetime(item.findtext("pubDate")).date().isoformat()))
    except Exception as e:
        raise SourceError(f"The Department's contracts feed could not be read: {type(e).__name__}: {str(e)[:150]}")
    _feed = (time.time(), out)
    return out


def _release(article, link, date):
    """A release's announcements, [{military_department, text}], from the Internet Archive's copy of its page;
    None where the Archive has no copy yet."""
    kept = DOD_KEPT / f"{article}.json"
    if kept.exists():
        return json.loads(kept.read_text())
    try:
        closest = (http_json(WAYBACK, params={"url": link, "timestamp": date.replace("-", "")}, tries=2, timeout=40)
                   .get("archived_snapshots") or {}).get("closest") or {}
        if not closest.get("available"):
            return None
        copy = re.sub(r"/web/(\d+)/", r"/web/\1id_/", closest["url"]).replace("http://", "https://", 1)
        response = session().get(copy, timeout=60)
        response.raise_for_status()
    except Exception as e:
        raise SourceError(f"The Internet Archive's copy of {link} could not be read: {type(e).__name__}: {str(e)[:120]}")
    body = BeautifulSoup(response.text, "html.parser").select_one("div.body")
    if not body:
        return None
    out, heading = [], None
    for para in body.find_all("p"):
        text = " ".join(para.get_text(" ", strip=True).split())
        if not text:
            continue
        if para.find("strong") and len(text) < 80 and text.upper() == text:  # a centered heading: ARMY, NAVY, ...
            heading = text
        else:
            out.append({"military_department": heading, "text": text})
    if out:
        DOD_KEPT.mkdir(parents=True, exist_ok=True)
        kept.write_text(json.dumps({"copy": copy, "announcements": out}))
    return {"copy": copy, "announcements": out}


def _announcements(article, link, date):
    got = _release(article, link, date)
    return [{"id": f"{article}:{i}", "url": link, "text": a["text"], "military_department": a["military_department"],
             "publication_date": date} for i, a in enumerate((got or {}).get("announcements") or [])]


def _search_announcements(s):
    lo, hi = s.between("publication_date")
    releases = [r for r in _releases() if (not lo or r[3] >= lo) and (not hi or r[3] <= hi)]
    at = s.cursor or 0
    part = releases[at:at + 8]
    with ThreadPoolExecutor(4) as ex:
        rows = [a for found in ex.map(lambda r: _announcements(r[0], r[2], r[3]), part) for a in found]
    return page(rows, at + 8 if at + 8 < len(releases) else None, order="latest release first")


def _read_announcement(key):
    article, _, n = key.partition(":")
    release = next((r for r in _releases() if r[0] == article), None)
    if not release:
        raise SourceError("The Department's feed no longer lists this release.")
    record = next((a for a in _announcements(release[0], release[2], release[3]) if a["id"] == key), None)
    if not record:
        raise SourceError("The release has no such announcement.")
    copy = (_release(release[0], release[2], release[3]) or {}).get("copy")
    return {**record, "document": f"From the release “{release[1]}”.", "published": release[3],
            "links": [{"text": release[1], "url": release[2]}] + ([{"text": "The Internet Archive's copy", "url": copy}] if copy else [])}


def _releases_span(series):
    dates = [r[3] for r in _releases()]
    return (min(dates), max(dates)) if dates else (None, None)


SERIES = [
    Series(
        id="sam_contract_opportunities", title="SAM.gov contract opportunities",
        record="a federal contract opportunity notice active on SAM.gov: a presolicitation, solicitation, sources-sought "
               "or award notice, or another notice of a procurement",
        creator="federal contracting offices", publisher="General Services Administration (SAM.gov)", jurisdiction="federal",
        coverage=_notices_span,
        fields=(F("notice_id", "identifier"), F("title", "text"), F("notice_type", "text"),
                F("solicitation_number", "identifier"), F("department", "name"), F("sub_tier", "name"), F("office", "name"),
                F("posted_date", "date"), F("response_deadline", "date"), F("archive_date", "date"), F("set_aside", "text"),
                F("naics", "identifier"), F("psc", "identifier"), F("place_of_performance_state", "place"),
                F("award_number", "identifier"), F("award_date", "date"), F("award_amount", "amount"), F("awardee", "name"),
                F("description", "full text")),
        search=_search_notices, read=_read_notice),
    Series(
        id="dod_contract_announcements", title="Department of War contract announcements",
        record="an announcement of one contract action valued at $7.5 million or more, from the Department of War's "
               "daily release",
        creator="U.S. Department of War", jurisdiction="federal", coverage=_releases_span,
        fields=(F("text", "text"), F("military_department", "text"), F("publication_date", "date")),
        search=_search_announcements, read=_read_announcement),
]
