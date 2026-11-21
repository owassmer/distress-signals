"""Two series: WARN notices of plant closings and mass layoffs, and legislation before city and county councils.

  warn_notices        WARN Index (warnindex.com) compiles the notices each state posts. Its API applies the
                      state, the employer and a range of notice dates, and counts the matches. A read returns
                      the notice's fields and, where the state posts the employer's letter, the letter's text.
  local_legislation   Legistar's web API, one client per council (config/legistar.yaml). Each client applies
                      every filter itself; a search asks them all at once and merges their matters by the time
                      each was last modified.
"""

import os
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from .. import names
from ..util import SourceError, config, http_json
from . import F, Series, page, web

WARN_INDEX = "https://warnindex.com"
LEGISTAR = "https://webapi.legistar.com/v1"
STATES = {"Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR", "California": "CA", "Colorado": "CO",
          "Connecticut": "CT", "Delaware": "DE", "District of Columbia": "DC", "Florida": "FL", "Georgia": "GA", "Hawaii": "HI",
          "Idaho": "ID", "Illinois": "IL", "Indiana": "IN", "Iowa": "IA", "Kansas": "KS", "Kentucky": "KY", "Louisiana": "LA",
          "Maine": "ME", "Maryland": "MD", "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN", "Mississippi": "MS",
          "Missouri": "MO", "Montana": "MT", "Nebraska": "NE", "Nevada": "NV", "New Hampshire": "NH", "New Jersey": "NJ",
          "New Mexico": "NM", "New York": "NY", "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK",
          "Oregon": "OR", "Pennsylvania": "PA", "Puerto Rico": "PR", "Rhode Island": "RI", "South Carolina": "SC",
          "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX", "Utah": "UT", "Vermont": "VT", "Virginia": "VA",
          "Washington": "WA", "West Virginia": "WV", "Wisconsin": "WI", "Wyoming": "WY"}
CODES = {v: k for k, v in STATES.items()}


# ---------- WARN notices ----------

def _index(path, **params):
    try:
        return http_json(f"{WARN_INDEX}/api/{path}", params=params or None, tries=3, timeout=45)
    except Exception as e:
        raise SourceError(f"WARN Index: {type(e).__name__}: {str(e)[:160]}")


def _notice(row):
    return {"id": row["notice_id"], "url": f"{WARN_INDEX}/notices/{row['notice_id']}", "employer": row.get("employer"),
            "state": row.get("state"), "location": row.get("location"), "category": row.get("closure_category"),
            "notice_type": row.get("closure_type"), "notice_date": row.get("notice_date"),
            "layoff_date": row.get("effective_date"), "affected_employees": row.get("layoff_count")}


def _code(given):
    """A state's two-letter code, from the code or the state's name."""
    given = given.strip()
    return given.upper() if given.upper() in CODES else next((c for n, c in STATES.items() if n.casefold() == given.casefold()), None)


def _search_notices(s):
    params = {"offset": s.cursor or 0, "limit": max(1, min(s.size, 500)), "sort_by": "notice_date", "sort_dir": "desc"}
    state, employer = s.one("state"), s.one("employer")
    if state:
        if not _code(state):
            return page([], total=0)
        params["state"] = _code(state)
    if employer:
        params["employer"] = employer
    lo, hi = s.between("notice_date")
    params |= {"after": lo} if lo else {}
    params |= {"before": hi} if hi else {}
    data = _index("notices", **params)
    rows = data.get("items") or []
    end = params["offset"] + len(rows)
    return page([_notice(r) for r in rows], end if rows and end < (data.get("total") or 0) else None, data.get("total"),
                "latest notice first")


def _read_notice(key):
    row = _index(f"notices/{key}")
    lines, links = [], []
    if row.get("source_url"):
        links.append({"text": "The state's posting", "url": row["source_url"]})
        lines.append(f"The state's posting: {row['source_url']}")
    if row.get("raw_notice_url"):
        links.append({"text": "The notice as filed", "url": row["raw_notice_url"]})
        try:
            lines += ["", "The notice as filed:", web.read(row["raw_notice_url"])["text"]]
        except Exception:  # the state posts no readable letter: the fields stand as the index gives them
            lines.append(f"The notice as filed: {row['raw_notice_url']}")
    return {**_notice(row), "document": "\n".join(lines), "links": links, "published": row.get("notice_date")}


def _notice_span(series):
    edge = lambda way: ((_index("notices", offset=0, limit=1, sort_by="notice_date", sort_dir=way).get("items") or [{}])[0]  # noqa: E731
                        .get("notice_date"))
    return edge("asc"), edge("desc")


def _notice_states(series):
    def has(code):
        return code if (_index("notices", offset=0, limit=1, state=code).get("total") or 0) > 0 else None
    with ThreadPoolExecutor(8) as ex:
        found = [c for c in ex.map(has, sorted(CODES)) if c]
    return ", ".join(found)


# ---------- local legislation (Legistar) ----------

def _clients():
    """[(client, jurisdiction)] for the councils this run can read."""
    return [(c["client"], c["jurisdiction"]) for c in config("legistar.yaml")["clients"]
            if not c.get("token") or os.environ.get(_token_name(c["client"]))]


def _token_name(client):
    return "LEGISTAR_TOKEN_" + client.upper().replace("-", "_")


def _legistar(client, path, params=None):
    params = dict(params or {})
    token = os.environ.get(_token_name(client))
    if token:
        params["token"] = token
    try:
        return http_json(f"{LEGISTAR}/{client}/{path}", params=params, tries=2, timeout=40)
    except (requests.RequestException, ValueError) as e:
        # The publisher takes its access token in the URL. Keep it out of tool logs.
        status = getattr(getattr(e, "response", None), "status_code", None)
        raise SourceError(f"Legistar could not read {client}/{path} (HTTP {status or 'unavailable'}).") from None


def _matter(client, jurisdiction, m):
    day = lambda v: (v or "")[:10] or None  # noqa: E731
    return {"id": f"{client}:{m['MatterId']}",
            "url": f"https://{client}.legistar.com/LegislationDetail.aspx?ID={m['MatterId']}&GUID={m.get('MatterGuid')}",
            "jurisdiction": jurisdiction, "body": m.get("MatterBodyName"), "file_number": m.get("MatterFile"),
            "type": m.get("MatterTypeName"), "status": m.get("MatterStatusName"), "title": m.get("MatterTitle"),
            "name": m.get("MatterName"), "introduced": day(m.get("MatterIntroDate")), "agenda_date": day(m.get("MatterAgendaDate")),
            "passed_date": day(m.get("MatterPassedDate")), "enactment_number": m.get("MatterEnactmentNumber"),
            "last_modified": day(m.get("MatterLastModifiedUtc")), "_modified": m.get("MatterLastModifiedUtc") or ""}


TEXT = {"title": ("MatterTitle",), "name": ("MatterName",), "body": ("MatterBodyName",), "type": ("MatterTypeName",),
        "status": ("MatterStatusName",)}
EXACT = {"file_number": "MatterFile", "enactment_number": "MatterEnactmentNumber"}
DATED = {"introduced": "MatterIntroDate", "agenda_date": "MatterAgendaDate", "passed_date": "MatterPassedDate",
         "last_modified": "MatterLastModifiedUtc"}


def _quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def _filter(s):
    """The OData filter every client applies: each word found in the field, each date within its range."""
    def words(given, columns):
        return " and ".join("(" + " or ".join(f"substringof({_quote(w)}, {c})" for c in columns) + ")" for w in given.split())
    parts = [words(s.query(), ("MatterTitle", "MatterName"))] if s.words() else []
    for field, columns in TEXT.items():
        given = s.any(field)
        if given:
            parts.append("(" + " or ".join(f"({words(g, columns)})" for g in given) + ")")
    for field, column in EXACT.items():
        given = s.any(field)
        if given:
            parts.append("(" + " or ".join(f"{column} eq {_quote(g)}" for g in given) + ")")
    for field, column in DATED.items():
        lo, hi = s.between(field)
        parts += [f"{column} ge datetime'{lo}'"] if lo else []
        parts += [f"{column} le datetime'{hi}T23:59:59'"] if hi else []
    return " and ".join(p for p in parts if p)


def _search_matters(s):
    given = s.any("jurisdiction")
    chosen = [(c, j) for c, j in _clients() if not given or any(set(names.tokens(g)) <= set(names.tokens(j)) for g in given)]
    where, size = _filter(s), max(1, min(s.size, 1000))
    at = s.cursor or {c: 0 for c, _ in chosen}

    def one(client, jurisdiction):
        params = {"$orderby": "MatterLastModifiedUtc desc,MatterId desc", "$top": size, "$skip": at[client]}
        params |= {"$filter": where} if where else {}
        return [_matter(client, jurisdiction, m) for m in _legistar(client, "matters", params)]

    live, got = [(c, j) for c, j in chosen if at.get(c, -1) >= 0], {}
    with ThreadPoolExecutor(8) as ex:
        futures = {ex.submit(one, c, j): (c, j) for c, j in live}
        for fut in as_completed(futures):
            client, jurisdiction = futures[fut]
            try:
                got[client] = fut.result()
            except SourceError as e:
                raise SourceError(f"{jurisdiction}'s legislation did not answer ({e}). The same search can be made again, "
                                  "or with jurisdiction set to the others.")
    merged = sorted((r for rows in got.values() for r in rows), key=lambda r: r["_modified"], reverse=True)[:size]
    used = {}
    for r in merged:
        client = r["id"].split(":", 1)[0]
        used[client] = used.get(client, 0) + 1
    nxt = dict(at)
    for client, _ in live:  # a council is finished when it returned fewer matters than asked and all were used
        n = used.get(client, 0)
        nxt[client] = -1 if len(got[client]) < size and n == len(got[client]) else at[client] + n
    return page([{k: v for k, v in r.items() if not k.startswith("_")} for r in merged],
                nxt if any(v >= 0 for v in nxt.values()) else None, order="latest modified first")


def _read_matter(key):
    client, _, number = key.partition(":")
    jurisdiction = next((j for c, j in _clients() if c == client), None)
    if not jurisdiction or not number.isdigit():
        raise SourceError(f"local_legislation has no record {key!r}")
    record = _matter(client, jurisdiction, _legistar(client, f"matters/{number}"))
    lines, links = [], []
    try:
        history = _legistar(client, f"matters/{number}/histories")
        lines.append("History (date, action, body, result):")
        lines += [f"  {(h.get('MatterHistoryActionDate') or '')[:10]} | {h.get('MatterHistoryActionName') or ''} | "
                  f"{h.get('MatterHistoryActionBodyName') or ''} | {h.get('MatterHistoryPassedFlagName') or ''}"
                  + (f" | {' '.join(str(h['MatterHistoryActionText']).split())}" if h.get("MatterHistoryActionText") else "")
                  for h in history]
        links = [{"text": a.get("MatterAttachmentName") or "Attachment", "url": a["MatterAttachmentHyperlink"]}
                 for a in _legistar(client, f"matters/{number}/attachments") if a.get("MatterAttachmentHyperlink")]
        versions = _legistar(client, f"matters/{number}/versions")
        if versions:
            text = _legistar(client, f"matters/{number}/texts/{versions[-1]['Key']}").get("MatterTextPlain")
            if text:
                lines += ["", "Text:", text]
    except SourceError:
        pass  # the council publishes the matter without its history or text
    return {**{k: v for k, v in record.items() if not k.startswith("_")}, "document": "\n".join(lines), "links": links,
            "published": record["last_modified"]}


SERIES = [
    Series(
        id="warn_notices", title="WARN notices",
        record="an employer's notice of a plant closing or mass layoff, given to its state under the WARN Act",
        creator="state dislocated worker units", publisher="WARN Index (warnindex.com), which compiles the states' postings",
        jurisdiction="the states", jurisdictions=_notice_states, coverage=_notice_span,
        fields=(F("employer", "name"), F("state", "place"), F("location", "place"), F("category", "text"),
                F("notice_type", "text"), F("notice_date", "date"), F("layoff_date", "date"),
                F("affected_employees", "number")),
        search=_search_notices, read=_read_notice),
    Series(
        id="local_legislation", title="city and county legislation",
        record="a matter before a city or county legislative body: an ordinance, resolution, contract approval or other "
               "file, with the dates it was introduced, placed on an agenda and passed",
        creator="city and county legislative bodies", publisher="Granicus (Legistar)",
        jurisdiction="; ".join(j for _, j in _clients()),
        fields=(F("jurisdiction", "place"), F("body", "name"), F("file_number", "identifier"), F("type", "text"),
                F("status", "text"), F("title", "text"), F("name", "text"), F("introduced", "date"), F("agenda_date", "date"),
                F("passed_date", "date"), F("enactment_number", "identifier"), F("last_modified", "date")),
        search=_search_matters, read=_read_matter),
]
