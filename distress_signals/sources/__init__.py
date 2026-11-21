"""The series of public records that search_records and read_record reach.

A series is one kind of record: every Federal Register document, every WARN notice, every federal award.
The modules in this package fetch records; each declares its series in SERIES, where its code lives, and
returns records in the series' own fields. Everything else is here, once, for every series: the catalog the
researcher reads, the search grammar, record references, what is stored when a record is read, and the table
each series fills in the workspace.

A series is described the way archives and data catalogs describe records (ISAD(G), DCAT):
  record        what one record is
  creator       the body that creates the records in doing its work
  publisher     who makes them available, where that is another body
  jurisdiction  the governments whose records they are
  coverage      the first and last dates held, read from the data where the publisher exposes them
  fields        the record's data dictionary: each field's name and type

A field's type is how it is searched, and the same for every series:
  name, place, text   words, each found in the value                 FIELD
  identifier          the value itself                               FIELD
  listed values       one of them                                    FIELD
  date                a range                                        FIELD_from, FIELD_until
  amount, number      a range                                        FIELD_min, FIELD_max
  full text           the record's document: matched by the query, returned when the record is read
A field holds one value, or several where it is marked many. A series kept by its parent (a filer's reported
figures, a docket's entries) marks the parent's field required, and a search gives it. The fields are what a
search returns for every record; reading a record adds its document.

An adapter's search takes a Search and returns a page of records. It takes from the Search the filters its
publisher applies; whatever it leaves, the registry applies to the records it returns, and keeps asking for
pages until it has what was asked for. A search that has not reached the end returns a cursor, however the
filtering was done.
"""

import base64
import importlib
import json
import os
import pkgutil
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable
from urllib.parse import parse_qsl, urlencode

from .. import names
from ..util import OUT, SourceError, now_iso

CARD_CHARS = 1000        # the most a series' entry in the catalog runs to, apart from what is read from its data
LIMIT, LIMIT_MAX = 25, 200
PAGE = 100               # records asked of a publisher at once when the registry filters them itself
SET_PAGE = 1000          # and when every record of a result set is being read
SEARCH_SECONDS = 45      # how long one search fetches before it returns what it has, with a cursor
SET_RECORDS = 25000      # the most records one result set keeps
SET_SECONDS = 420        # how long reading a result set may take
FACTS = OUT / "series.json"   # coverage and jurisdictions read from the data, kept for a day
TYPES = ("name", "place", "identifier", "date", "amount", "number", "text", "full text")
RESERVED = {"observation_id", "record_id", "url", "id", "document", "links", "published"}


@dataclass(frozen=True)
class Field:
    name: str
    type: object  # one of TYPES, or a tuple of the values the field takes
    many: bool = False  # a record can hold several
    required: bool = False  # the series is read by this field: a search gives it

    def __post_init__(self):
        listed = isinstance(self.type, tuple)
        if not re.fullmatch(r"[a-z][a-z0-9_]*", self.name) or self.name in RESERVED:
            raise ValueError(f"field name {self.name!r}")
        if not listed and self.type not in TYPES:
            raise ValueError(f"field {self.name}: type {self.type!r}")

    @property
    def listed(self):
        return self.type if isinstance(self.type, tuple) else ()

    @property
    def ranged(self):
        return self.type in ("date", "amount", "number")

    def card(self):
        need = ", required" if self.required else ""
        if self.listed:
            return f"{self.name} ({' | '.join(self.listed)}{', several' if self.many else ''}{need})"
        return f"{self.name} ({self.type}{'s' if self.many else ''}{need})"

    def keys(self):
        """The filter keys this field takes: {key: 'is' | 'lo' | 'hi'}."""
        if self.type == "full text":
            return {}
        if self.type == "date":
            return {f"{self.name}_from": "lo", f"{self.name}_until": "hi"}
        if self.ranged:
            return {f"{self.name}_min": "lo", f"{self.name}_max": "hi"}
        return {self.name: "is"}


def F(name, type, many=False, required=False):
    return Field(name, tuple(type) if isinstance(type, (list, tuple)) else type, many, required)


@dataclass(frozen=True)
class Series:
    id: str  # the name search_records takes, and its table in the workspace
    title: str  # what the run page calls it: "the Federal Register"
    record: str  # what one record is
    creator: str
    jurisdiction: str
    fields: tuple
    search: Callable  # search(Search) -> page(...)
    read: Callable  # read(id) -> the record: its fields, "id", "url", and its "document", "links", "published"
    publisher: str | None = None
    since: str | None = None  # the first date held, as its publisher states it
    coverage: Callable | None = None  # coverage(series) -> (first, last), read from the data; None keeps since
    jurisdictions: Callable | None = None  # jurisdictions(series) -> the jurisdictions in the data, replacing jurisdiction
    requires: tuple = ()  # environment variables it needs; without them it is not in a run's catalog

    def __post_init__(self):
        if not re.fullmatch(r"[a-z][a-z0-9_]*", self.id):
            raise ValueError(f"series id {self.id!r}")
        seen = [f.name for f in self.fields]
        if len(seen) != len(set(seen)):
            raise ValueError(f"{self.id}: a field is declared twice")
        if len(self.card({})) > CARD_CHARS:
            raise ValueError(f"the {self.id} entry is {len(self.card({}))} characters; an entry is at most {CARD_CHARS}")

    def field(self, name):
        return next((f for f in self.fields if f.name == name), None)

    def keys(self):
        """{filter key: (field, 'is' | 'lo' | 'hi')}."""
        return {k: (f, part) for f in self.fields for k, part in f.keys().items()}

    def card(self, known):
        """The series' entry in the catalog. known is what was read from its data: {coverage, jurisdiction}."""
        first, last = known.get("coverage") or (self.since, None)
        span = f"{first} to {last}" if first and last else f"from {first}" if first else f"to {last}" if last else None
        lines = [f"- {self.id}: {self.record}", f"  Creator: {self.creator}"]
        if self.publisher:
            lines.append(f"  Publisher: {self.publisher}")
        lines.append(f"  Jurisdiction: {known.get('jurisdiction') or self.jurisdiction}")
        if span:
            lines.append(f"  Coverage: {span}")
        lines.append("  Fields: " + ", ".join(f.card() for f in self.fields))
        return "\n".join(lines)


def page(records, cursor=None, total=None, order=None):
    """What an adapter's search returns: records (each its fields, with "id" and "url"), the cursor that
    continues after them (None at the end), the publisher's count of matches where it gives one, and the order
    the records are in."""
    return {"records": records, "cursor": cursor, "total": total, "order": order}


class Search:
    """One search of a series, as its adapter sees it. Taking a filter, or the query, says the publisher
    applies it; what the adapter leaves, the registry applies to the records returned.

      size        how many records to fetch
      cursor      where to continue: the cursor a previous page returned, or None. A cursor is a position,
                  and means the same whatever size the next call asks for
      everything  every match is being read, so order by date rather than relevance where that pages further
    """

    def __init__(self, series, spec, size, cursor=None, everything=False):
        self.series, self.size, self.cursor, self.everything = series, size, cursor, everything
        self._query, self._filters, self._taken = spec["q"], spec["f"], set()

    def query(self):
        """The words to find in the record."""
        self._taken.add("?")
        return self._query

    def any(self, field):
        """The values given for a field; a record matches any of them. [] when none was given."""
        self._taken.add(field)
        return list((self._filters.get(field) or {}).get("is") or [])

    def one(self, field):
        """The value given for a field when exactly one was; otherwise None, and the field is left."""
        given = (self._filters.get(field) or {}).get("is") or []
        if len(given) != 1:
            return None
        self._taken.add(field)
        return given[0]

    def between(self, field):
        """(from, until) for a date, (min, max) for an amount or number; None where no bound was given."""
        self._taken.add(field)
        got = self._filters.get(field) or {}
        return got.get("lo"), got.get("hi")

    def peek(self, field):
        """What was given for a field, without taking it: {'is': [...]} or {'lo': ..., 'hi': ...}, or {}."""
        return dict(self._filters.get(field) or {})

    def words(self):
        """The query, without taking it."""
        return self._query

    def left(self):
        """(whether the query is left, the filters left) for the registry to apply."""
        return bool(self._query) and "?" not in self._taken, {k: v for k, v in self._filters.items() if k not in self._taken}


# ---------- the registry ----------

_all = None


def declared():
    """{id: Series}, every series declared in this package."""
    global _all
    if _all is None:
        found = {}
        for m in pkgutil.iter_modules(__path__):
            for s in getattr(importlib.import_module(f"{__name__}.{m.name}"), "SERIES", ()):
                if s.id in found:
                    raise ValueError(f"series {s.id} is declared twice")
                found[s.id] = s
        _all = found
    return _all


def get(series_id):
    s = declared().get(series_id)
    if not s:
        raise SourceError(f"series is one of {sorted(available())}")
    return s


def available():
    """The series this run can reach: those whose keys are configured."""
    return {i: s for i, s in declared().items() if not [k for k in s.requires if not os.environ.get(k)]}


def order(run_id=None):
    """The order a run's catalog lists the series in: drawn from the run's ID, so every run sees its own and
    the same run always sees the same. Without a run, alphabetical."""
    ids = sorted(available())
    if run_id:
        random.Random(run_id).shuffle(ids)
    return ids


def catalog(ids):
    known = facts()
    return "\n".join(declared()[i].card(known.get(i) or {}) for i in ids)


def tables():
    """{series ID: [(column, SQLite type)]}: the table each series fills, a column for each field."""
    return {i: [(f.name, "REAL" if f.type in ("amount", "number") else "TEXT") for f in s.fields if f.type != "full text"]
            for i, s in declared().items()}


def views():
    """SQL for the views that cross series, generated from the field types:
    record_dates, record_names and record_amounts, each (series, record_id, field, value, observation_id)."""
    out = {}
    for view, column, kinds in (("record_dates", "date", ("date",)), ("record_names", "name", ("name",)),
                                ("record_amounts", "amount", ("amount",))):
        arms = []
        for i, s in sorted(declared().items()):
            for f in s.fields:
                if f.type not in kinds:
                    continue
                if f.many:
                    arms.append(f"SELECT '{i}' AS series, t.record_id, '{f.name}' AS field, j.value AS {column}, "
                                f"t.observation_id FROM \"{i}\" t, json_each(t.\"{f.name}\") j")
                else:
                    arms.append(f"SELECT '{i}' AS series, record_id, '{f.name}' AS field, \"{f.name}\" AS {column}, "
                                f"observation_id FROM \"{i}\" WHERE \"{f.name}\" IS NOT NULL")
        if arms:
            out[view] = "\nUNION ALL ".join(arms)
    return out


# ---------- what is read from the data ----------

def facts():
    """{series ID: {coverage: [first, last], jurisdiction, at}} as last read from each series' data."""
    try:
        return json.loads(FACTS.read_text())
    except (OSError, ValueError):
        return {}


def newest(field):
    """A coverage reader for a series whose search lists the newest record first: the first date as the
    publisher states it, and the newest record's date."""
    def read(series):
        got = search(series.id, limit=1)["records"]
        return series.since, (str(got[0].get(field) or "")[:10] or None) if got else None
    return read


def observe(max_age=86400, only=None):
    """Read coverage and jurisdictions from the data of each series that has a reader, where the last reading
    is older than max_age seconds. A series that can't be read now keeps what was read before."""
    known, now = facts(), time.time()
    due = [s for i, s in available().items() if (s.coverage or s.jurisdictions) and (only is None or i in only)
           and now - (known.get(i) or {}).get("read", 0) > max_age]

    def one(s):
        got = {"read": now, "at": now_iso()}
        try:
            if s.coverage:
                first, last = s.coverage(s)
                got["coverage"] = [first, last]
            if s.jurisdictions:
                got["jurisdiction"] = s.jurisdictions(s)
        except Exception as e:  # the catalog still stands on what the series declares
            return s.id, None, f"{type(e).__name__}: {str(e)[:160]}"
        return s.id, got, None

    failed = {}
    if due:
        with ThreadPoolExecutor(8) as ex:
            for i, got, err in ex.map(one, due):
                if got:
                    known[i] = got
                else:
                    failed[i] = err
        OUT.mkdir(exist_ok=True)
        FACTS.write_text(json.dumps(known, indent=1, sort_keys=True))
    return failed


# ---------- the search grammar ----------

DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _spec(series, query, filters):
    """The search as given, checked against the series' fields: {q, f: {field: {is: [...]} | {lo, hi}}}."""
    keys, out = series.keys(), {}
    for key, value in (filters or {}).items():
        if value in (None, "", []):
            continue
        if key not in keys:
            raise SourceError(f"{series.id} has no filter {key!r}. Its filters: {', '.join(keys) or 'none'}.")
        f, part = keys[key]
        if part == "is":
            given = [str(v).strip() for v in (value if isinstance(value, list) else [value]) if str(v).strip()]
            if f.listed:
                by = {v.casefold(): v for v in f.listed}
                bad = [v for v in given if v.casefold() not in by]
                if bad:
                    raise SourceError(f"{series.id}.{f.name} is one of: {' | '.join(f.listed)}; not {bad[0]!r}")
                given = [by[v.casefold()] for v in given]
            out.setdefault(f.name, {})["is"] = given
        elif f.type == "date":
            if not DATE.fullmatch(str(value)):
                raise SourceError(f"{key} is a date, YYYY-MM-DD; not {value!r}")
            out.setdefault(f.name, {})[part] = str(value)
        else:
            try:
                out.setdefault(f.name, {})[part] = float(value)
            except (TypeError, ValueError):
                raise SourceError(f"{key} is a number; not {value!r}")
    missing = [f.name for f in series.fields if f.required and not (out.get(f.name) or {}).get("is")]
    if missing:
        raise SourceError(f"{series.id} is read by {' and '.join(missing)}: give {'it' if len(missing) == 1 else 'them'} in filters.")
    return {"q": " ".join((query or "").split()), "f": out}


def _given(series, spec):
    """The search in the form it was given: [(key, value)], the query first, in a fixed order."""
    items = [("q", spec["q"])] if spec["q"] else []
    for name in sorted(spec["f"]):
        f, got = series.field(name), spec["f"][name]
        lo, hi = [k for k, p in f.keys().items() if p == "lo"], [k for k, p in f.keys().items() if p == "hi"]
        items += [(name, v) for v in got.get("is") or []]
        items += [(lo[0], _plain(got["lo"]))] if got.get("lo") is not None else []
        items += [(hi[0], _plain(got["hi"]))] if got.get("hi") is not None else []
    return items


def _plain(x):
    return int(x) if isinstance(x, float) and x.is_integer() else x


def _phrases(query):
    """(the quoted phrases, the other words) of a query, as word lists."""
    quoted = re.findall(r'"([^"]+)"', query)
    return [names.tokens(p) for p in quoted if names.tokens(p)], names.tokens(re.sub(r'"[^"]+"', " ", query))


def _has(words, value):
    """Every word is a whole word of the value (or of one of its values)."""
    return any(set(words) <= set(names.tokens(str(v))) for v in (value if isinstance(value, list) else [value]) if v is not None)


def _keep(series, record, query, filters):
    """Whether a record meets the query and the filters the publisher did not apply."""
    for name, got in filters.items():
        f, value = series.field(name), record.get(name)
        if value in (None, "", []):
            return False
        if "is" in got:
            many = value if isinstance(value, list) else [value]
            if f.type == "identifier" or f.listed:
                if not {str(v).strip().casefold() for v in many} & {g.casefold() for g in got["is"]}:
                    return False
            elif not any(_has(names.core_tokens(g) or names.tokens(g), many) for g in got["is"]):
                return False
        else:
            lo, hi = got.get("lo"), got.get("hi")
            for v in value if isinstance(value, list) else [value]:
                v = str(v)[:10] if f.type == "date" else v
                try:
                    v = v if f.type == "date" else float(v)
                except (TypeError, ValueError):
                    continue
                if (lo is None or v >= lo) and (hi is None or v <= hi):
                    break
            else:
                return False
    if query:
        flat = [x for f in series.fields if f.type != "full text"
                for x in ((record.get(f.name) if isinstance(record.get(f.name), list) else [record.get(f.name)]))
                if x is not None]
        have = names.tokens(" ".join(str(x) for x in flat))
        phrases, words = _phrases(query)
        if not set(words) <= set(have):
            return False
        if any(not any(have[i:i + len(p)] == p for i in range(len(have) - len(p) + 1)) for p in phrases):
            return False
    return True


def _encode(state):
    return base64.urlsafe_b64encode(json.dumps(state, separators=(",", ":")).encode()).decode().rstrip("=")


def _decode(cursor):
    try:
        return json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
    except (ValueError, TypeError):
        raise SourceError("cursor is the cursor a search returned, for the same search")


def _fetch(series, spec, state, want, everything, seconds):
    """Records meeting the search, from the position in state, until `want` of them are in hand, the end is
    reached, or the time is up. Returns (records, the state to continue from or None, what the search reports).
    The total is the publisher's count where it applied every filter, and the count in hand where the adapter
    returned every record at once or the search was read from its start to its end."""
    out, examined, filtered, total, order_, whole = [], 0, False, None, None, None
    started, from_start = time.monotonic(), state["c"] is None and not state["skip"]
    c, skip, n = state["c"], state["skip"], state["n"]
    while True:
        s = Search(series, spec, n, c, everything)
        got = series.search(s)
        q_left, f_left = s.left()
        records = got["records"]
        if q_left or f_left:
            filtered = True
            records = [r for r in records if _keep(series, r, spec["q"] if q_left else "", f_left)]
        examined += len(got["records"])
        if c is None and got.get("cursor") is None:
            whole = len(records)  # the adapter returned every record at once: the count that match is in hand
        order_ = order_ or got.get("order")
        total = got.get("total") if total is None else total
        rest = records[skip:]
        room = want - len(out)
        out += rest[:room]
        if len(rest) > room:
            nxt = {"c": c, "skip": skip + room, "n": n}
            break
        if got.get("cursor") is None:
            nxt = None
            break
        c, skip = got["cursor"], 0
        if filtered:
            n = max(n, SET_PAGE if everything else PAGE)
        if len(out) >= want or time.monotonic() - started > seconds:
            nxt = {"c": c, "skip": 0, "n": n}
            break
    if whole is not None:
        total = whole
    elif filtered or total is None:
        total = len(out) if nxt is None and from_start else None
    return out, nxt, {"total": total, "examined": examined if filtered else None, "order": order_}


def _shown(series, record):
    """A record as a search returns it: its reference, the fields it has, its address."""
    out = {"record_id": f"{series.id}:{record['id']}"}
    out.update({f.name: record[f.name] for f in series.fields
                if f.type != "full text" and record.get(f.name) not in (None, "", [])})
    if record.get("url"):
        out["url"] = record["url"]
    return out


def search(series_id, query="", filters=None, limit=LIMIT, cursor=None):
    """{series, count, records, cursor?, total?, examined?, order?, result_set}."""
    series = get(series_id)
    spec = _spec(series, query, filters)
    try:
        limit = max(1, min(int(limit or LIMIT), LIMIT_MAX))
    except (TypeError, ValueError):
        raise SourceError("limit is a number")
    state = _decode(cursor) if cursor else {"c": None, "skip": 0, "n": limit}
    records, nxt, told = _fetch(series, spec, state, limit, False, SEARCH_SECONDS)
    out = {"series": series.id, "count": len(records), "records": [_shown(series, r) for r in records]}
    if nxt:
        out["cursor"] = _encode(nxt)
    out.update({k: v for k, v in told.items() if v is not None})
    out["result_set"] = f"{series.id}?{urlencode(_given(series, spec))}"
    return out


# ---------- references, and what a read stores ----------

def parse(ref):
    """(series, 'record' | 'result_set', the rest) for a reference a search issued; None for anything else."""
    m = re.match(r"^([a-z][a-z0-9_]*)([:?])(.*)$", ref or "", re.S)
    if not m or m[1] not in declared():
        return None
    return declared()[m[1]], "record" if m[2] == ":" else "result_set", m[3]


def _text(value):
    return "; ".join(str(v) for v in value) if isinstance(value, list) else str(value)


def _lines(series, record):
    return [f"{f.name}: {_text(record[f.name])}" for f in series.fields
            if f.type != "full text" and record.get(f.name) not in (None, "", [])]


def _row(series, record):
    row = {"record_id": f"{series.id}:{record['id']}", "url": record.get("url")}
    for f in series.fields:
        if f.type == "full text":
            continue
        v = record.get(f.name)
        if f.many:
            v = json.dumps([x for x in (v if isinstance(v, list) else [v]) if x is not None], ensure_ascii=False) if v not in (None, "", []) else None
        elif isinstance(v, list):
            v = _text(v)
        row[f.name] = v if v != "" else None
    return row


def _heading(series, record):
    """What a record is called: its title; else its first name and its first text."""
    if record.get("title"):
        return _text(record["title"])[:160]
    parts = [next((_text(record[f.name]) for f in series.fields if f.type == kind and record.get(f.name)), None)
             for kind in ("name", "text")]
    return " · ".join(p[:100] for p in parts if p) or str(record["id"])


def read(ref):
    """The observation for a reference: one record with its document, or every record a search matches."""
    series, kind, rest = parse(ref)
    if kind == "record":
        record = series.read(rest)
        record.setdefault("id", rest)
        head = [f"{series.title}: {series.id}:{record['id']}"] + _lines(series, record)
        if record.get("url"):
            head.append(f"url: {record['url']}")
        document = (record.get("document") or "").strip()
        return {"kind": "record", "source": series.id, "native_id": str(record["id"]), "url": record.get("url"),
                "published": record.get("published"), "text": "\n".join(head) + ("\n\n" + document if document else ""),
                "rows": {series.id: [_row(series, record)]},
                "data": {"title": f"{_heading(series, record)} ({series.title})", "links": record.get("links") or [],
                         "fields": {f.name: record.get(f.name) for f in series.fields if f.type != "full text"}}}
    given = {}
    for k, v in parse_qsl(rest, keep_blank_values=False):
        given.setdefault(k, []).append(v)
    query = " ".join(given.pop("q", []))
    spec = _spec(series, query, {k: (v if len(v) > 1 else v[0]) for k, v in given.items()})
    records, nxt, told = _fetch(series, spec, {"c": None, "skip": 0, "n": SET_PAGE}, SET_RECORDS + 1, True, SET_SECONDS)
    what = describe(series, spec)
    if len(records) > SET_RECORDS:
        raise SourceError(f"More than {SET_RECORDS:,} records of {series.title} match ({what}); one read keeps up to "
                          f"{SET_RECORDS:,}. Narrow the search.")
    if nxt:
        raise SourceError(f"Reading every record of {series.title} that matches ({what}) took longer than "
                          f"{SET_SECONDS // 60} minutes, with {len(records):,} read. Narrow the search.")
    seen, unique = set(), []
    for r in records:
        if r["id"] not in seen:
            seen.add(r["id"])
            unique.append(r)
    lines = [f"{series.title}: {len(unique):,} records, {what}.", "",
             "Each line is one record: its record_id, its fields, its address."]
    lines += [" | ".join([f"{series.id}:{r['id']}"] + _lines(series, r) + ([r["url"]] if r.get("url") else [])) for r in unique]
    return {"kind": "result_set", "source": series.id, "native_id": f"{series.id}?{urlencode(_given(series, spec))}",
            "url": None, "published": None, "text": "\n".join(lines), "rows": {series.id: [_row(series, r) for r in unique]},
            "data": {"title": f"{len(unique):,} records of {series.title}, {what}", "count": len(unique),
                     "search": _given(series, spec)}}


def again(observation):
    """The reference that reads a stored observation's source again, where a series issued it."""
    if observation.get("source") in declared() and observation.get("native_id"):
        if observation["kind"] == "result_set":
            return observation["native_id"]
        return f"{observation['source']}:{observation['native_id']}"
    return None


def outline(value, depth=0, skip=()):
    """A structured value written out as indented lines of 'label: value', for a record's document: every
    stated value is on a line with its label, so it can be quoted. Empty values and the keys in skip are left out."""
    pad, lines = "  " * depth, []
    if isinstance(value, dict):
        for k, v in value.items():
            if v in (None, "", [], {}) or k in skip:
                continue
            if isinstance(v, (dict, list)):
                lines.append(f"{pad}{k}:")
                lines += outline(v, depth + 1, skip)
            else:
                lines.append(f"{pad}{k}: {' '.join(str(v).split())}")
    elif isinstance(value, list):
        for v in value:
            if isinstance(v, (dict, list)):
                inner = outline(v, depth + 1, skip)
                lines += [f"{pad}- {inner[0].strip()}"] + inner[1:] if inner else []
            elif v not in (None, ""):
                lines.append(f"{pad}- {' '.join(str(v).split())}")
    else:
        lines.append(f"{pad}{' '.join(str(value).split())}")
    return lines


# ---------- wording, for the run page and the stored text ----------

def describe(series, spec):
    """A search in words: 'matching “hemp”, type Rule, effective_date from 2026-12-01'."""
    parts = [f"matching “{spec['q']}”"] if spec["q"] else []
    for key, value in _given(series, spec):
        if key != "q":
            parts.append(f"{key} {value}")
    return ", ".join(parts) or "every record"


def label(series_id, query, filters):
    """What a search did, for the run page."""
    s = declared().get(series_id)
    out = f"Searched {s.title if s else str(series_id).replace('_', ' ')}"
    if query:
        out += f" for “{query}”"
    given = [f"{k.replace('_', ' ')} {', '.join(map(str, v)) if isinstance(v, list) else v}"
             for k, v in (filters or {}).items() if v not in (None, "", [])]
    return out + (f" ({'; '.join(given)})" if given else "")


def noun(ref):
    """What a reference is called on the run page: 'a record of the Federal Register'."""
    hit = parse(ref)
    if not hit:
        return None
    return f"a record of {hit[0].title}" if hit[1] == "record" else f"every record of {hit[0].title} matching its search"
