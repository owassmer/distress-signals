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
    """Read a record and store it, so its passages can be cited. Takes a record_id or a result_set from
    search_records, a web address (HTML or PDF), or the observation_id of a record already read.

    A record_id returns the record's fields and its document. A result_set returns every record the search
    matches, one line each, up to 25,000 records. Both fill the series' table in the workspace. The text
    comes 12,000 characters at a time (next_offset continues it), with the document's links. find takes terms
    separated by '|' and returns the passages around each match instead (plain text, any letter case). A
    stored record is reused; fresh reads its source again and updates the same record."""
    return _call("read_record", tools.read_record, id_or_url, find, offset, fresh)


COMPUTE = """Run one read-only SQLite query over the records read with read_record and the findings and results
recorded with record_finding and compute. Returns the columns and up to 200 rows. save_as stores the result (up
to 2,000 rows) under a result_id; an input whose value comes from it cites it as derived, and saving checks the
value is in it. SQLite's date functions (date, julianday, strftime) work on the date columns. A query stops
after 15 seconds.

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


PROPOSITIONS = """propositions gives in full each proposition the call introduces or revises: a claim that will turn out
true or false, with its window and, where a record will show it, its resolution source; its ways nested in it; and
the keys of the propositions it implies or implies not. A proposition given in full takes the place of the saved one
with the same key, its ways with it, and keeps every proposition a saved case reads."""


@server.tool(description="""Record what the research has established or set aside: a fact (with the passage that states
it), an inference or conclusion (with the passages or workspace results it rests on), an open question, or a company,
dependency or lead dropped and why. It can concern a company, a proposition or a case. With withdraw_case, it also
withdraws that case from the queue; the finding is the record of why.

""" + PROPOSITIONS)
def record_finding(finding: Finding, propositions: list[Proposition] = []) -> dict:
    return _call("record_finding", tools.record_finding, finding.model_dump(mode="json"),
                 [p.model_dump(mode="json", exclude_none=True) for p in propositions])


@server.tool()
def lookup(id_or_query: str) -> dict:
    """Look up a record by its ID, key or name. A case's ID returns the case in the form save_case accepts, with
    its status, company, the propositions it reads, its worlds and their figures, the passages it cites and their
    sources, its findings, its revisions, and its deal's stage and outcome in the operator's CRM. A proposition's
    key or title returns it in full with its ways and links, what its level fields say, its findings, and each
    case that reads it: the case's formula, given values and worlds. A company's ID, name, alias or domain returns
    the company with its cases and findings, and its deals in the operator's CRM with their stages and outcomes. A
    finding's or workspace result's ID returns that record."""
    return _call("lookup", tools.lookup, id_or_query)


MODEL = """The model is one revenue formula over input names: numbers, + - * /, parentheses, min() and max(). Each input
has a unit, its value over the period in the baseline, and one basis: source (the passage that states it), result
(the cell of a saved compute result, which the store reads) or assumption (the reasoning, with the passages and saved
results it rests on). Each of given_values says: given these propositions true (given) and these false (given_not),
these inputs take these values, each with what it rests on.

events names the proposition the case is about. The case's worlds are the combinations of its events and of the
propositions its given values name that the links allow: a way is true only where what it divides is true, exactly
one of the ways beside one another is true there, and a proposition that implies another is true only where that one
is. A way named brings the ways beside it. What a company or its counterparties do once an outcome is known is a
proposition of its own that implies that outcome, so it divides the outcome for this case and leaves the event's
ways as they are.

The baseline is the formula at the inputs' values. In each world an input takes the value of the one given value
that holds there and whose worlds lie strictly within those of every other that holds there and sets it; an input
none sets stands at its own value. For each world the store returns the propositions true and false in it, the inputs
that take another value there, the revenue within the period, the shortfall against the baseline, the shortfall as a
share of the denominator, revenue deferred past the period and the revenue lost (both only where the deferral's
formula is given), the contribution loss (where the contribution formula is given), and cash delayed."""


@server.tool(description="Calculate each world's figures without saving, from a draft of a case's model. save_case runs "
                         "the same calculation.\n\n" + MODEL + "\n\n" + PROPOSITIONS)
def calculate_exposure(case: Draft, propositions: list[Proposition] = []) -> dict:
    return _call("calculate_exposure", tools.calculate_exposure, case.model_dump(mode="json"),
                 [p.model_dump(mode="json", exclude_none=True) for p in propositions])


@server.tool(description="""Save a case for contacting a company: one company, one revenue stream, and the event or
events the stream depends on. The company is given in full or by ID (with its id, the object edits that company).

""" + MODEL + """

""" + PROPOSITIONS + """

Each basis is checked: a passage is the section of text that states the figure, never the number alone, and appears
word for word in the stored record; a result cell is one row of a saved result. case_id revises a case; a different
company, events or stream_key is a different case. A saved case goes in front of the operator in their CRM at once,
and a revision or a withdrawal updates it there. Returns the IDs, the revision, the condition, the worlds with their
figures, any warnings, and the other saved cases a restated proposition moved.""")
def save_case(case: Case, propositions: list[Proposition] = []) -> dict:
    return _call("save_case", tools.save_case, case.model_dump(mode="json"),
                 [p.model_dump(mode="json", exclude_none=True) for p in propositions])


if __name__ == "__main__":
    load_env()
    server.run()
