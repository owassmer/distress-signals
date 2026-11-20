"""The researcher's tools over stdio MCP (server name "ds"). A thin adapter over tools.py. Logs go
to stderr, and each call is appended to $DISTRESS_RUN_LOG when set; stdout carries only the protocol.

  python -m distress_signals.mcp_server        (Claude launches it from the --mcp-config file)"""

import json
import logging
import os
import sys
import time
import traceback
from typing import Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from . import db, tools
from .models import Calculation, Finding, Opportunity
from .util import load_env

logging.basicConfig(stream=sys.stderr, level=logging.WARNING)

server = MCPServer("ds")  # no server instructions: the analyst sees them, and the tool descriptions say what each does


def _log(name, args, outcome, started):
    path = os.environ.get("DISTRESS_RUN_LOG")
    if not path:
        return
    brief = json.dumps(args, default=str)[:240]
    with open(path, "a") as f:
        f.write(f"{time.strftime('%H:%M:%S')} {name} {time.time() - started:5.1f}s {outcome} {brief}\n")


def _call(name, fn, *args, **kwargs):
    started = time.time()
    try:
        out = fn(*args, **kwargs)
        _log(name, args, "ok", started)
        return out
    except tools.ToolError as e:
        _log(name, args, f"refused: {str(e)[:160]}", started)
        raise ToolError(str(e))
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        _log(name, args, f"error: {type(e).__name__}: {str(e)[:160]}", started)
        raise ToolError(f"{type(e).__name__}: {str(e)[:300]}. If it is a network error, the same call can be made again.")


TABLES = "\n".join(f"    - {t}(observation_id, {', '.join(cols)})" for t, cols in db.RECORD_TABLES.items())


@server.tool()
def search_records(source: Literal["filings", "federal_register", "usaspending", "sec", "ledgers"], query: str,
                   filters: dict | None = None) -> dict:
    """Search a public-record source. Each result carries a record_id for read_record; nothing is stored
    until it is read.

    - filings: the full text of every SEC filing, searched by words or a phrase in double quotes. Each result
      is one document of a filing (the report, or an exhibit such as a press release) with the company, form,
      filing date and address; read_record reads it, and its find returns the passages. Filters: forms (SEC
      form types: 10-K, 10-Q, 8-K and so on), since and until (filing dates, YYYY-MM-DD; the default is the
      last 120 days), max_results (the default is 100).
    - federal_register: every federal rule, proposed rule, notice and presidential document, searched by
      words. Each result gives the title, agencies, action, publication date, effective date, comment
      deadline and address. When more match than are returned, it gives how the matches divide by type and by
      agency. read_all is a record_id for every match: read_record reads them all (up to 25,000) into
      register_documents. Filters: type (rule, proposed_rule, notice or presidential_document), agency (its
      name), effective_from and effective_until, comments_close_from and comments_close_until, since and until
      (publication dates; without any dates, the last year), max_results (the default is 50, at most 200).
    - usaspending: U.S. federal awards to a recipient (query is the recipient's name). Filters: agency (the
      awarding agency's top-level name as USAspending lists it), keyword (words in the award), since and
      until (dates of award actions, YYYY-MM-DD; the default is the last three years), award_types
      (contracts, idvs, grants, loans, direct_payments or other; the default is contracts), active_on
      (only awards whose current end date is on or after this date; every such award is read, back to 2007,
      and the largest are returned with a count of how many qualified), max_results (the default is 300).
    - sec: a ticker or company name, matched against every name EDGAR holds for every filer, current or former,
      with a ticker or without one. Filers with a ticker come first; one without says what kind of filer it is,
      its industry, when it last filed and which forms it files.
    - ledgers: a company name, searched in 19 U.S. state and local government payment ledgers; returns each
      payee spelling by paying department with payment counts, totals and dates. Takes one to two minutes.
      Filters: since (YYYY-MM-DD; the record IDs it returns keep it), ledgers (ledger IDs)."""
    return _call("search_records", tools.search_records, source, query, filters)


@server.tool()
def read_record(id_or_url: str, find: str | None = None, offset: int = 0, fresh: bool = False) -> dict:
    """Read a record and store it, so its passages can be cited. Takes a web address (HTML or PDF), a
    record_id from search_records, or the observation_id of a record already read.

    Returns the text 12,000 characters at a time (next_offset continues it) and the page's links. find takes
    terms separated by '|' and returns the passages around each match instead (plain text, any letter case).
    A federal award returns its period of performance, values, funding accounts and modifications; an SEC
    record returns the filer's former names, the forms it files, its recent filings with links and its
    reported revenue by quarter and year (a fourth quarter is derived from the year and labeled so); a ledger
    record returns every payment line it keeps; a Federal Register read_all returns every matching document,
    one line each. Those four also fill the workspace tables. A stored record is reused; fresh reads its
    source again and updates the same record."""
    return _call("read_record", tools.read_record, id_or_url, find, offset, fresh)


COMPUTE = """Run one read-only SQLite query over the records read with read_record and the findings and results
recorded with record_finding and compute. Returns the columns and up to 200 rows. save_as stores the result (up to 2,000 rows) under a result_id; an input whose
value comes from it cites it as derived, and saving checks the value is in it. SQLite's date functions
(date, julianday, strftime) work on the date columns. A query stops after 15 seconds.

Tables filled when a record is read:
{tables}
In award_funding, obligated is the amount obligated in that fiscal period; outlays_fytd is outlays from the
start of the fiscal year to that period; one period can have several rows for the same account. In
register_documents, significant is 1 where the document is a significant regulatory action, dates is the
document's own DATES section, and cfr lists the parts of the Code of Federal Regulations it changes. Also in
the workspace: observations(id, kind, source, native_id, url, published, retrieved_at, text, data), findings
and workspace_results."""


@server.tool(description=COMPUTE.replace("{tables}", TABLES))
def compute(sql: str, save_as: str | None = None, note: str | None = None) -> dict:
    return _call("compute", tools.compute, sql, save_as, note)


@server.tool()
def record_finding(finding: Finding) -> dict:
    """Record what the research has established or set aside: a fact (with the passage that states it), an
    inference or conclusion (with the passages or workspace results it rests on), an open question, or a
    company, dependency or lead dropped and why. It can concern a company, an event or a case. With
    withdraw_case, it also withdraws that case from the queue; the finding is the record of why."""
    return _call("record_finding", tools.record_finding, finding.model_dump(mode="json"))


@server.tool()
def lookup(id_or_query: str) -> dict:
    """Look up a record by its ID or name. A case's ID returns the case in the form save_opportunity accepts,
    with its status, company, event, results, the passages it cites and their sources, its findings, its
    revisions, and its deal's stage and outcome in the operator's CRM. A company's ID, name, alias or domain
    returns the company with its cases and findings, and its deals in the operator's CRM with their stages
    and outcomes. An event's ID, key or title returns the event with its cases and findings. A finding's or
    workspace result's ID returns that record."""
    return _call("lookup", tools.lookup, id_or_query)


@server.tool()
def calculate_exposure(case: Calculation) -> dict:
    """Calculate the revenue at stake without saving: inputs (each with a value, a unit, and exactly one of
    source, derived or assumption), a baseline, one or more affected versions of the condition, and an
    optional denominator. Formulas use input names, numbers, + - * /, parentheses, min() and max().

    For each affected version it returns the revenue within the period, the shortfall against the baseline,
    the shortfall as a share of the denominator, revenue deferred past the period and the revenue lost (both
    only where the deferral is given), the contribution loss (where both baseline and version give
    contribution), and cash delayed. save_opportunity runs the same calculation."""
    return _call("calculate_exposure", tools.calculate_exposure, case.model_dump(mode="json"))


@server.tool()
def save_opportunity(opportunity: Opportunity) -> dict:
    """Save a case for contacting a company: one company, one event, one affected revenue stream. The company
    is given in full or by ID (with its id, the object edits that company); the event by key or ID, or in full,
    which saves its definition. Each input's provenance is checked: a source passage must be the section of
    text that states the figure, never the number alone, and appear word for word in the stored record; a
    derived value must appear in the workspace result. The results are computed
    from the inputs and formulas. opportunity_id revises a case; a different company, event or stream_key is
    a different case. A saved case goes in front of the operator in their CRM at once, and a revision or a
    withdrawal updates it there. Returns the IDs, the revision, the results and any warnings."""
    return _call("save_opportunity", tools.save_opportunity, opportunity.model_dump(mode="json"))


if __name__ == "__main__":
    load_env()
    server.run()
