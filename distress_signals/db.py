"""The store: one SQLite file (distress.db, or $DISTRESS_DB). WAL mode, a 5-second busy timeout,
one connection per tool call and short write transactions, so several researchers can share it.
Columns hold what is filtered or joined; JSON holds the rest. Domain logic lives in tools.py."""

import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .util import ROOT, now_iso

DDL = """
CREATE TABLE IF NOT EXISTS companies (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, domain TEXT UNIQUE, data TEXT NOT NULL,
  created TEXT NOT NULL, updated TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS company_keys (
  key TEXT NOT NULL, company_id TEXT NOT NULL REFERENCES companies(id), PRIMARY KEY (key, company_id));
CREATE TABLE IF NOT EXISTS events (
  id TEXT PRIMARY KEY, key TEXT NOT NULL UNIQUE, title TEXT NOT NULL, data TEXT NOT NULL,
  created TEXT NOT NULL, updated TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS opportunities (
  id TEXT PRIMARY KEY, company_id TEXT NOT NULL REFERENCES companies(id),
  event_id TEXT NOT NULL REFERENCES events(id), stream_key TEXT NOT NULL,
  priority INTEGER, key_date TEXT, max_loss REAL, currency TEXT,
  case_json TEXT NOT NULL, results TEXT NOT NULL, progress TEXT NOT NULL DEFAULT '{}',
  revision INTEGER NOT NULL, created TEXT NOT NULL, updated TEXT NOT NULL,
  UNIQUE (company_id, event_id, stream_key));
CREATE TABLE IF NOT EXISTS revisions (
  opportunity_id TEXT NOT NULL REFERENCES opportunities(id), revision INTEGER NOT NULL,
  saved_at TEXT NOT NULL, hash TEXT NOT NULL, change_note TEXT, snapshot TEXT NOT NULL, run_id TEXT,
  PRIMARY KEY (opportunity_id, revision));
CREATE TABLE IF NOT EXISTS observations (
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, source TEXT NOT NULL, native_id TEXT, url TEXT,
  published TEXT, retrieved_at TEXT NOT NULL, text TEXT, data TEXT NOT NULL DEFAULT '{}');
CREATE INDEX IF NOT EXISTS observations_url ON observations(url);
CREATE TABLE IF NOT EXISTS citations (
  opportunity_id TEXT NOT NULL REFERENCES opportunities(id),
  observation_id TEXT NOT NULL REFERENCES observations(id), PRIMARY KEY (opportunity_id, observation_id));
CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY, seed_kind TEXT NOT NULL, seed TEXT NOT NULL, question TEXT, model TEXT,
  started TEXT NOT NULL, finished TEXT, opportunity_ids TEXT NOT NULL DEFAULT '[]', summary TEXT, error TEXT,
  stats TEXT, log TEXT);
CREATE TABLE IF NOT EXISTS leads (
  id TEXT PRIMARY KEY, run_id TEXT REFERENCES runs(id), subject TEXT NOT NULL, reason TEXT,
  urls TEXT NOT NULL DEFAULT '[]', status TEXT NOT NULL DEFAULT 'open', created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS findings (
  id TEXT PRIMARY KEY, run_id TEXT, kind TEXT NOT NULL, statement TEXT NOT NULL, company_id TEXT, event_id TEXT,
  opportunity_id TEXT, evidence TEXT NOT NULL DEFAULT '[]', results TEXT NOT NULL DEFAULT '[]', supersedes TEXT,
  status TEXT NOT NULL DEFAULT 'current', created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS workspace_results (
  id TEXT PRIMARY KEY, name TEXT, sql TEXT NOT NULL, columns TEXT NOT NULL, rows TEXT NOT NULL, row_count INTEGER,
  note TEXT, run_id TEXT, created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS run_reads (
  run_id TEXT NOT NULL, observation_id TEXT NOT NULL, PRIMARY KEY (run_id, observation_id));
CREATE TABLE IF NOT EXISTS deliveries (
  destination TEXT NOT NULL, entity_type TEXT NOT NULL, entity_id TEXT NOT NULL, external_id TEXT,
  hash TEXT, delivered_at TEXT NOT NULL, PRIMARY KEY (destination, entity_type, entity_id));
"""

# Records the workspace queries: the rows behind each observation, at the source's own grain. A
# re-read replaces an observation's rows.
RECORD_TABLES = {
    "awards": ["award_id", "generated_id", "award_type", "recipient", "recipient_uei", "parent_recipient",
               "awarding_agency", "awarding_sub_agency", "funding_agency", "funding_sub_agency", "start_date",
               "current_end_date", "potential_end_date", "date_signed", "total_obligation", "base_exercised_options",
               "base_and_all_options", "total_outlays", "parent_award", "description"],
    "award_funding": ["award_id", "federal_account", "account_title", "funding_agency", "program_activity_code",
                      "program_activity_name", "object_class", "object_class_name", "disaster_code", "fiscal_year",
                      "fiscal_period", "obligated", "outlays_fytd"],
    "award_modifications": ["award_id", "action_date", "modification", "action", "obligation", "description"],
    "payments": ["government", "payee", "paid_date", "department", "amount", "invoice_date", "invoice_id", "description"],
    "revenue": ["company", "cik", "concept", "start_date", "end_date", "period_days", "period_type", "value", "form",
                "filed", "accession"],
    "filings": ["company", "cik", "form", "filed", "report_date", "items", "document", "index_url", "description"],
}
DDL += "".join(f"CREATE TABLE IF NOT EXISTS {t} (observation_id TEXT NOT NULL, {', '.join(cols)});\n"
               f"CREATE INDEX IF NOT EXISTS {t}_obs ON {t}(observation_id);\n" for t, cols in RECORD_TABLES.items())

# Columns added after a table was first created: (table, column, type).
ADDED = [("runs", "principal", "TEXT"), ("runs", "system", "TEXT"), ("runs", "prompt", "TEXT"),
         ("opportunities", "status", "TEXT NOT NULL DEFAULT 'active'")]


def path():
    return Path(os.environ.get("DISTRESS_DB") or ROOT / "distress.db")


def connect(p=None):
    con = sqlite3.connect(p or path(), timeout=5, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=5000")
    con.execute("PRAGMA foreign_keys=ON")
    con.executescript(DDL)
    for table, col, typ in ADDED:
        if col not in {r[1] for r in con.execute(f"PRAGMA table_info({table})")}:
            con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
    return con


@contextmanager
def write(con):
    """One short write transaction; IMMEDIATE takes the write lock up front."""
    con.execute("BEGIN IMMEDIATE")
    try:
        yield con
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise


def make_id(prefix, *parts):
    return prefix + hashlib.sha1("\x1f".join(str(p) for p in parts).encode()).hexdigest()[:12]


def dumps(x):
    return json.dumps(x, sort_keys=True, ensure_ascii=False, default=str)


def _row(r, *json_cols):
    if r is None:
        return None
    d = dict(r)
    for c in json_cols:
        d[c] = json.loads(d[c]) if d.get(c) is not None else None
    return d


# ---------- companies ----------

def get_company(con, cid):
    return _row(con.execute("SELECT * FROM companies WHERE id=?", (cid,)).fetchone(), "data")


def companies_by_domain(con, domain):
    return _row(con.execute("SELECT * FROM companies WHERE domain=?", (domain,)).fetchone(), "data")


def companies_by_key(con, key):
    return [_row(r, "data") for r in con.execute(
        "SELECT c.* FROM companies c JOIN company_keys k ON k.company_id=c.id WHERE k.key=?", (key,))]


def put_company(con, cid, name, domain, data, keys):
    ts = now_iso()
    con.execute("INSERT INTO companies VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                "name=excluded.name, domain=excluded.domain, data=excluded.data, updated=excluded.updated",
                (cid, name, domain, dumps(data), ts, ts))
    con.executemany("INSERT OR IGNORE INTO company_keys VALUES (?,?)", [(k, cid) for k in keys if k])


# ---------- events ----------

def get_event(con, id_or_key):
    return _row(con.execute("SELECT * FROM events WHERE id=? OR key=?", (id_or_key, id_or_key)).fetchone(), "data")


def put_event(con, eid, key, title, data):
    ts = now_iso()
    con.execute("INSERT INTO events VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                "title=excluded.title, data=excluded.data, updated=excluded.updated",
                (eid, key, title, dumps(data), ts, ts))


# ---------- opportunities ----------

OPP_JSON = ("case_json", "results", "progress")


def get_opportunity(con, oid):
    return _row(con.execute("SELECT * FROM opportunities WHERE id=?", (oid,)).fetchone(), *OPP_JSON)


def opportunity_for(con, company_id, event_id, stream_key):
    return _row(con.execute("SELECT * FROM opportunities WHERE company_id=? AND event_id=? AND stream_key=?",
                            (company_id, event_id, stream_key)).fetchone(), *OPP_JSON)


def set_status(con, oid, status):
    con.execute("UPDATE opportunities SET status=?, updated=? WHERE id=?", (status, now_iso(), oid))


def opportunities(con, where="1=1", args=()):
    return [_row(r, *OPP_JSON) for r in con.execute(
        f"SELECT * FROM opportunities WHERE {where} ORDER BY priority, key_date", args)]


def put_opportunity(con, row):
    """row: id, company_id, event_id, stream_key, priority, key_date, max_loss, currency, case_json,
    results, revision. Progress is kept as it was."""
    ts = now_iso()
    con.execute("INSERT INTO opportunities (id, company_id, event_id, stream_key, priority, key_date, max_loss, "
                "currency, case_json, results, revision, created, updated) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET company_id=excluded.company_id, event_id=excluded.event_id, "
                "stream_key=excluded.stream_key, priority=excluded.priority, key_date=excluded.key_date, "
                "max_loss=excluded.max_loss, currency=excluded.currency, case_json=excluded.case_json, "
                "results=excluded.results, revision=excluded.revision, updated=excluded.updated",
                (row["id"], row["company_id"], row["event_id"], row["stream_key"], row["priority"], row["key_date"],
                 row["max_loss"], row["currency"], dumps(row["case_json"]), dumps(row["results"]),
                 row["revision"], ts, ts))


def set_progress(con, oid, progress):
    con.execute("UPDATE opportunities SET progress=?, updated=? WHERE id=?", (dumps(progress), now_iso(), oid))


def latest_revision(con, oid):
    return _row(con.execute("SELECT * FROM revisions WHERE opportunity_id=? ORDER BY revision DESC LIMIT 1",
                            (oid,)).fetchone(), "snapshot")


def add_revision(con, oid, revision, digest, change_note, snapshot):
    """Stamped with the research run that made it ($DISTRESS_RUN_ID), if any."""
    con.execute("INSERT INTO revisions VALUES (?,?,?,?,?,?,?)",
                (oid, revision, now_iso(), digest, change_note, dumps(snapshot), os.environ.get("DISTRESS_RUN_ID")))


def revisions(con, oid):
    return [dict(r) for r in con.execute("SELECT opportunity_id, revision, saved_at, change_note, run_id FROM revisions "
                                         "WHERE opportunity_id=? ORDER BY revision", (oid,))]


# ---------- observations ----------

def get_observation(con, oid):
    return _row(con.execute("SELECT * FROM observations WHERE id=?", (oid,)).fetchone(), "data")


def put_observation(con, obs):
    """obs: id, kind, source, retrieved_at, and optionally native_id, url, published, text, data, and rows
    ({record table: [row dicts]}) for the workspace. A re-read updates the record in place and replaces
    its rows; revisions keep the passages their figures used."""
    for table, rows in (obs.get("rows") or {}).items():
        cols = RECORD_TABLES[table]
        con.execute(f"DELETE FROM {table} WHERE observation_id=?", (obs["id"],))
        con.executemany(f"INSERT INTO {table} (observation_id, {', '.join(cols)}) VALUES ({', '.join('?' * (len(cols) + 1))})",
                        [(obs["id"], *[r.get(c) for c in cols]) for r in rows])
    con.execute("INSERT INTO observations VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                "kind=excluded.kind, source=excluded.source, native_id=excluded.native_id, url=excluded.url, "
                "published=excluded.published, retrieved_at=excluded.retrieved_at, text=excluded.text, "
                "data=excluded.data",
                (obs["id"], obs["kind"], obs["source"], obs.get("native_id"), obs.get("url"), obs.get("published"),
                 obs["retrieved_at"], obs.get("text"), dumps(obs.get("data") or {})))
    return obs["id"]


def set_citations(con, oid, observation_ids):
    con.execute("DELETE FROM citations WHERE opportunity_id=?", (oid,))
    con.executemany("INSERT OR IGNORE INTO citations VALUES (?,?)", [(oid, o) for o in sorted(set(observation_ids))])


def cited(con, oid):
    return [_row(r, "data") for r in con.execute(
        "SELECT o.id, o.kind, o.source, o.native_id, o.url, o.published, o.retrieved_at, o.data, "
        "o.text IS NOT NULL AS has_text FROM observations o JOIN citations c ON c.observation_id=o.id "
        "WHERE c.opportunity_id=? ORDER BY o.kind, o.id", (oid,))]


# ---------- runs and leads ----------

def start_run(con, rid, seed_kind, seed, question, model, log, principal=None, system=None, prompt=None):
    """Records the assignment as given: the system prompt and the prompt."""
    with write(con):
        con.execute("INSERT INTO runs (id, seed_kind, seed, question, model, started, log, principal, system, prompt) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)", (rid, seed_kind, seed, question, model, now_iso(), log, principal,
                                                     system, prompt))


def finish_run(con, rid, opportunity_ids=(), summary=None, error=None, stats=None):
    with write(con):
        con.execute("UPDATE runs SET finished=?, opportunity_ids=?, summary=?, error=?, stats=? WHERE id=?",
                    (now_iso(), dumps(list(opportunity_ids)), summary, error, dumps(stats or {}), rid))


def saved_by_run(con, rid):
    """Opportunities a run saved itself (not the ones recomputed after it changed a shared event)."""
    return [r[0] for r in con.execute(
        "SELECT DISTINCT opportunity_id FROM revisions WHERE run_id=? AND change_note NOT LIKE 'Recomputed after event%' "
        "ORDER BY opportunity_id", (rid,))]


def add_leads(con, rid, leads):
    with write(con):
        for x in leads:
            con.execute("INSERT OR IGNORE INTO leads VALUES (?,?,?,?,?,?,?)",
                        (make_id("lead_", x["company_or_event"].casefold()), rid, x["company_or_event"], x.get("reason"),
                         dumps(x.get("source_urls") or []), "open", now_iso()))


def get_lead(con, lid):
    return _row(con.execute("SELECT * FROM leads WHERE id=?", (lid,)).fetchone(), "urls")


def open_leads(con):
    return [_row(r, "urls") for r in con.execute("SELECT * FROM leads WHERE status='open' ORDER BY created")]


def set_lead(con, lid, status):
    with write(con):
        con.execute("UPDATE leads SET status=? WHERE id=?", (status, lid))


# ---------- findings and workspace results ----------

def put_finding(con, f):
    con.execute("INSERT OR REPLACE INTO findings VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (f["id"], os.environ.get("DISTRESS_RUN_ID"), f["kind"], f["statement"], f.get("company_id"),
                 f.get("event_id"), f.get("opportunity_id"), dumps(f.get("evidence") or []), dumps(f.get("results") or []),
                 f.get("supersedes"), "current", now_iso()))
    if f.get("supersedes"):
        con.execute("UPDATE findings SET status='superseded' WHERE id=?", (f["supersedes"],))


def findings(con, where="1=1", args=()):
    return [_row(r, "evidence", "results") for r in con.execute(
        f"SELECT * FROM findings WHERE status='current' AND ({where}) ORDER BY created", args)]


def get_finding(con, fid):
    return _row(con.execute("SELECT * FROM findings WHERE id=?", (fid,)).fetchone(), "evidence", "results")


def put_result(con, r):
    con.execute("INSERT OR REPLACE INTO workspace_results VALUES (?,?,?,?,?,?,?,?,?)",
                (r["id"], r.get("name"), r["sql"], dumps(r["columns"]), dumps(r["rows"]), r["row_count"], r.get("note"),
                 os.environ.get("DISTRESS_RUN_ID"), now_iso()))


def get_result(con, rid):
    return _row(con.execute("SELECT * FROM workspace_results WHERE id=?", (rid,)).fetchone(), "columns", "rows")


def mark_read(con, observation_id):
    """Record that the current run read an observation; the workspace sees what a run has read."""
    run = os.environ.get("DISTRESS_RUN_ID")
    if run:
        con.execute("INSERT OR IGNORE INTO run_reads VALUES (?,?)", (run, observation_id))


def run_observations(con):
    """The observations the current run has read; outside a run, every observation."""
    run = os.environ.get("DISTRESS_RUN_ID")
    if not run:
        return [r[0] for r in con.execute("SELECT id FROM observations")]
    return [r[0] for r in con.execute("SELECT observation_id FROM run_reads WHERE run_id=?", (run,))]


# ---------- delivery state ----------

def get_delivery(con, destination, entity_type, entity_id):
    return _row(con.execute("SELECT * FROM deliveries WHERE destination=? AND entity_type=? AND entity_id=?",
                            (destination, entity_type, entity_id)).fetchone())


def put_delivery(con, destination, entity_type, entity_id, external_id, digest=None):
    con.execute("INSERT INTO deliveries VALUES (?,?,?,?,?,?) ON CONFLICT(destination, entity_type, entity_id) DO UPDATE "
                "SET external_id=excluded.external_id, hash=excluded.hash, delivered_at=excluded.delivered_at",
                (destination, entity_type, entity_id, external_id, digest, now_iso()))


def deliveries(con, destination, entity_type):
    return [dict(r) for r in con.execute("SELECT * FROM deliveries WHERE destination=? AND entity_type=? "
                                         "ORDER BY delivered_at", (destination, entity_type))]
