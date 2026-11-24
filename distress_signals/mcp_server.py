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

from . import db, sources, tools
from .models import Case, Draft, Finding, Proposition
from .util import load_env

logging.basicConfig(stream=sys.stderr, level=logging.WARNING)

# No server instructions: the analyst would read them. config/system.md says what a case is and why; each tool's
# description says what the call takes, does, returns and refuses, once.
server = MCPServer("ds")


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


# The catalog is the whole of what the researcher is told about the series: each one's entry, in the order
# drawn for this run (sources.order), which the launcher stores with the run.
ORDER = sources.order(os.environ.get("DISTRESS_RUN_ID"))
SEARCH = """Search a series of public records. A series is one kind of record. Each entry below gives what one
record is, the body that creates the records, the publisher where another body makes them available, the
jurisdiction, the dates covered, and the record's fields with their types. A type in the plural (names, dates)
is a field that holds several values. A field marked required is given in every search of its series.

- query: words to find in a record's text, and in its whole document where the series has a full text field.
  A phrase goes in double quotes.
- filters: an object keyed by field. A name, place or text field takes words; an identifier takes its value; a
  field with listed values takes one of them. Each of these also takes a list, and a record matches any item.
  A date field takes FIELD_from and FIELD_until (YYYY-MM-DD). An amount or number field takes FIELD_min and
  FIELD_max.
- limit: how many records to return: 25 by default, at most 200.
- cursor: the cursor a result returned, to continue that search. A result without one reached the end.

A result gives each record's fields, its url and a record_id for read_record. total is the count of matching
records where it is known. examined is how many records were checked where the publisher could not apply a
filter itself. result_set is an ID for every record the search matches. Nothing is stored until it is read.

The series:

""" + sources.catalog(ORDER)


def search_records(series: str, query: str = "", filters: dict | None = None, limit: int = sources.LIMIT,
                   cursor: str | None = None) -> dict:
    return _call("search_records", tools.search_records, series, query, filters, limit, cursor)


search_records.__annotations__["series"] = Literal[tuple(ORDER)]  # the same order as the catalog
server.tool(description=SEARCH)(search_records)


@server.tool()
def read_record(id_or_url: str, find: str | None = None, offset: int = 0, fresh: bool = False) -> dict:
    """Read a record and store it, so that its passages can be quoted and checked. Takes a record_id or a
    result_set from search_records, a web address (HTML or PDF), or the observation_id of a record already read.

    A record_id returns the record's fields and its document. A result_set returns every record the search
    matches, one line each, up to 25,000 records. Both fill the series' table in the workspace. The text
    comes 12,000 characters at a time (next_offset continues it), with the document's links. find takes terms
    separated by '|' and returns the passages around each match instead (plain text, any letter case). A
    stored record is reused; fresh reads its source again and updates the same record."""
    return _call("read_record", tools.read_record, id_or_url, find, offset, fresh)


COMPUTE = """Run one read-only SQLite query over the records read with read_record and the findings and results
recorded with record_finding and compute. Returns the columns and up to 200 rows. save_as stores the result (up
to 2,000 rows) under a result_id. A figure of a case can be read from one cell of a saved result, which the
store reads, and an assumption or a finding names the saved results it rests on. SQLite's date functions (date,
julianday, strftime) work on the date columns. A query stops after 15 seconds.

Each series is a table of the same name, with one row for each of its records read: a column for each field in
its entry under search_records, and record_id, url and observation_id (the read that holds the record's
text). A field that holds several values is a JSON array; json_each lists them. Three views cross the series:
record_dates(series, record_id, field, date, observation_id), record_names(series, record_id, field, name,
observation_id) and record_amounts(series, record_id, field, amount, observation_id). Also in the workspace:
observations(id, kind, source, native_id, url, published, retrieved_at, text, data), findings and
workspace_results."""


@server.tool(description=COMPUTE)
def compute(sql: str, save_as: str | None = None, note: str | None = None) -> dict:
    return _call("compute", tools.compute, sql, save_as, note)


PROPOSITIONS = """propositions gives in full each proposition the call introduces or revises, its ways nested in it.
One given in full takes the place of the saved one with the same key, its ways with it."""


@server.tool(description="""Keep something the research established apart from a case's own figures: a fact a record
states, or an inference or a conclusion with the passages and saved results it rests on. A finding concerns a
company, a proposition or a case, and looking up any of those returns it.

With withdraw_case, the call also withdraws that case, and the finding is the record of why the revenue no longer
depends on the event as the case states.

""" + PROPOSITIONS + """ Each other active case that reads a proposition the call revises is computed again.""")
def record_finding(finding: Finding, propositions: list[Proposition] = []) -> dict:
    return _call("record_finding", tools.record_finding, finding.model_dump(mode="json"),
                 [p.model_dump(mode="json", exclude_none=True) for p in propositions])


@server.tool()
def lookup(id_or_query: str) -> dict:
    """Return what is saved under an ID, a key or a name: a case, a finding or a saved result by its ID; a
    proposition by its key or its exact title; a company by its ID, name, alias or domain.

    A case's ID returns the case in the form save_case takes, with its status, its company, the propositions it
    reads, its worlds and their figures, the passages it cites with their sources, its findings and its revisions.
    A proposition's key, or its exact title, returns it in full with its ways and links, what its level fields say,
    its findings, and each case that reads it: the case's formula, given values and worlds. A company returns
    with its cases and findings."""
    return _call("lookup", tools.lookup, id_or_query)


DEFINED = "'A case' in your instructions defines each part."


@server.tool(description="""Compute a draft of a case's model: its worlds, each input's value in each world, and every
figure, as save_case computes them, with the store left as it was. It takes the model part of a case: events, the formulas,
inputs, given values and denominator.

""" + DEFINED + """ A formula is an expression over input names: numbers, + - * /, parentheses, min() and max(). A
figure read from a result cell is read by the store. Quotations are checked when the case is saved.

It returns the propositions the draft reads, the baseline, the condition, and each world with the propositions true
and false in it, the inputs that take another value there, the revenue within the period, the shortfall and its share
of the denominator, revenue deferred past the period and the revenue lost (both where the deferral's formula is
given), the contribution loss (where the contribution formula is given) and cash delayed. It refuses what save_case
refuses of a model, with the reason.

""" + PROPOSITIONS + " A draft uses them for the calculation alone.")
def calculate_exposure(case: Draft, propositions: list[Proposition] = []) -> dict:
    return _call("calculate_exposure", tools.calculate_exposure, case.model_dump(mode="json"),
                 [p.model_dump(mode="json", exclude_none=True) for p in propositions])


@server.tool(description="""Save a case: the analysis of one company, one revenue stream, and the event or events the
stream depends on.

""" + DEFINED + """ The company is given in full or by ID (with its id, the object edits that company). A formula is
an expression over input names: numbers, + - * /, parentheses, min() and max().

The store saves the propositions given with the case, checks each basis (a quotation is in its record, word for
word; a result cell picks out one row, and the store reads the figure from it), computes the worlds and their
figures, saves the company and the case, and writes a revision where the case changed. Each other active case that
reads a proposition the call revises is computed again and gets a revision of its own.

It returns the case as computed: its ID and revision, the propositions it reads, the baseline, the condition, each
world with the propositions true and false in it, the inputs that take another value there, its revenue, its
shortfall and the other figures calculate_exposure returns, any warnings, and the other cases the call moved.

A call the store refuses says why, and saves nothing. A proposition given in full keeps every proposition an active
case reads and every one another proposition is linked to. Some companies are excluded, and a session assigned one
company saves cases for that company.

case_id revises that case. Without it, a case for the same company, events and stream_key revises the saved one,
and any other combination is a new case.

""" + PROPOSITIONS)
def save_case(case: Case, propositions: list[Proposition] = []) -> dict:
    return _call("save_case", tools.save_case, case.model_dump(mode="json"),
                 [p.model_dump(mode="json", exclude_none=True) for p in propositions])


if __name__ == "__main__":
    load_env()
    server.run()
