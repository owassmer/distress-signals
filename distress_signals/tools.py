"""The researcher's domain functions. mcp_server.py exposes them; each takes and returns plain
JSON-able values and raises ToolError with a message the researcher can act on.

  search_records, read_record     public records and stored observations
  compute                         SQL over the retrieved records, saved results a case can cite
  record_finding                  research kept before (and beside) a case; can withdraw a case
  lookup                          saved cases, companies, propositions, findings and results
  calculate_exposure              the calculation save runs, on a draft
  save_case                       validate, check each basis, compute, store, revise

The analyst owns the economic judgment; code observes and computes. Every input in a case has one basis:
a passage the store checks in its record, a cell of a saved result the store reads, or an assumption with
what it rests on. docs/model.md states the model."""

import bisect
import copy
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
import unicodedata

from pydantic import ValidationError

from . import db, exposure, forms, names, worlds
from .models import Case, Changes, Draft, Finding, Proposition, check_propositions
from . import sources
from .sources import web
from .util import SourceError, exclusions, now_iso


class ToolError(Exception):
    pass


_BRANCHES = {"str", "Proposition", "Company", "Draft", "Case", "Finding"}


def _invalid(e: ValidationError):
    """Pydantic's errors in the researcher's terms: field paths only, and no 'should be a string'
    noise from the ID-or-object fields when the object itself was given."""
    errs = e.errors()
    real = [x for x in errs if x["loc"][-1:] != ("str",)] or errs
    lines = []
    for err in real[:12]:
        loc = ".".join(str(x) for x in err["loc"] if not (isinstance(x, str) and ("[" in x or x in _BRANCHES)))
        lines.append(f"{loc}: {err['msg']}" if loc else err["msg"])
    more = len(real) - len(lines)
    return "Invalid input:\n" + "\n".join(lines) + (f"\n(and {more} more)" if more > 0 else "")


def _validate(model, payload):
    try:
        return model.model_validate(payload)
    except ValidationError as e:
        raise ToolError(_invalid(e))


# ---------- quoted passages and derived figures ----------

_MARKS = str.maketrans({"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"', "\u2013": "-",
                        "\u2014": "-", "\u2212": "-", "\u00a0": " "})


def unseen(s):
    """Text without the characters that print nothing: zero-width spaces and joiners, word joiners, soft hyphens
    (Unicode's format characters), which pages carry between words."""
    return "".join(c for c in s if unicodedata.category(c) != "Cf")


def _norm(s):
    s = unseen(unicodedata.normalize("NFKC", s or "")).translate(_MARKS)
    return re.sub(r"\s+", " ", s).strip().casefold()


def _parts(passage):
    """The parts of a passage to find, in order: '...' marks an omission between them."""
    return [p for p in (frag.strip(" \"'") for frag in re.split(r"\.\.\.", _norm(passage))) if p]


def _find(frag, t, pos):
    i = t.find(frag, pos)
    if i < 0:
        frag = frag.rstrip(".,;:")
        i = t.find(frag, pos) if frag else -1
    return i, len(frag)


def quoted(passage, text):
    """True when the passage appears in the text, in order, allowing '...' for omissions."""
    t, pos = _norm(text), 0
    for frag in _parts(passage):
        i, n = _find(frag, t, pos)
        if i < 0:
            return False
        pos = i + n
    return True


def _sentences(text, base=0):
    """[(start, end)] of the text's sentences by sentencex, each offset by `base`. sentencex keeps a list item
    whole, from its marker to the end of its paragraph, however many sentences it holds; a unit that divides
    once its first word is set aside is such an item, and its sentences are taken from the rest."""
    from sentencex import segment
    out, at = [], 0
    for s in segment("en", text):
        start = text.find(s, at) if s else -1
        if start < 0:
            continue
        at = start + len(s)
        body = s.lstrip()
        first = body.split(None, 1)
        rest = first[1] if len(first) == 2 else ""
        inner = _sentences(rest, base + at - len(rest)) if rest and len(list(segment("en", rest))) > 1 else None
        if inner:
            out.append((base + start, inner[0][1]))  # the marker stays with the item's first sentence
            out += inner[1:]
        else:
            out.append((base + start, base + at))
    return out


def _rows(text, start, end):
    """[(start, end)] of the lines of text[start:end]."""
    out, line = [], start
    for row in text[start:end].split("\n"):
        out.append((line, line + len(row)))
        line += len(row) + 1
    return out


def whole_sentences(passage, text, rows_until=0, blocks=None):
    """The passage as the whole of what it falls in, by the structure the text's reader recorded. The first
    `rows_until` characters are rows (the fields of a record, the records of a result set, a document its adapter
    wrote as rows): each line whole. `blocks` says which later lines are one block of their page (a paragraph, a
    list item, a heading, a row of a table): True for every line, or [first, last] ranges of line numbers; in a
    block the passage is the whole sentences it falls in, and a sentence does not run past its line. Where the
    passage omits text between them ('...'), the pieces either side are joined by ' \u2026 '. Where no structure
    is recorded for the text it falls in, the passage stands as quoted."""
    spans, start = (_rows(text, 0, rows_until) if rows_until else []), 0
    for number, line in enumerate(text.split("\n")):
        if blocks and start >= rows_until and line.strip() and (blocks is True or any(a <= number <= b for a, b in blocks)):
            spans += _sentences(line, start)
        start += len(line) + 1
    kept, starts, normed, pos = [], [], [], 0
    for s, e in spans:
        n = _norm(text[s:e])
        if n:
            kept.append((s, e))
            starts.append(pos)
            normed.append(n)
            pos += len(n) + 1
    flat, pos, chosen = " ".join(normed), 0, []
    as_quoted = " ".join(unseen(passage).split())
    for frag in _parts(passage):
        i, n = _find(frag, flat, pos)
        if i < 0:
            return as_quoted
        first, last = bisect.bisect_right(starts, i) - 1, bisect.bisect_right(starts, i + n - 1) - 1
        chosen += [k for k in range(first, last + 1) if k not in chosen]
        pos = i + n
    out, prev = [], None
    for k in sorted(chosen):
        if prev is not None:
            out.append(" " if k == prev + 1 or not text[kept[prev][1]:kept[k][0]].strip() else " \u2026 ")
        out.append(" ".join(unseen(text[kept[k][0]:kept[k][1]]).split()))
        prev = k
    return "".join(out) or as_quoted


_WORD = re.compile(r"[^\W\d_]{2,}")  # a word: two or more letters


def has_text(passage):
    """A cited passage is the section of text that states the claim: at least three words, so never a
    number alone."""
    return len(_WORD.findall(passage or "")) >= 3


def _structure(obs):
    """(rows_until, blocks) for whole_sentences: how far an observation's text is rows by construction, and which
    of its lines its reader recorded as blocks of the page (data.blocks)."""
    return _rows_until(obs), (obs.get("data") or {}).get("blocks")


def _rows_until(obs):
    """How far into an observation's text every line is a row by construction: all of a result set, whose lines
    are its records; all of a record whose adapter wrote its document as rows (sources.as_rows); and otherwise the
    fields of a record of a series, which sources.read writes under the record's heading, one to a line, before
    the blank line its document follows."""
    text = obs["text"]
    if obs["kind"] == "result_set" or (obs.get("data") or {}).get("document_rows"):
        return len(text)
    series = sources.declared().get(obs["source"])
    if obs["kind"] == "record" and series and text.startswith(f"{series.title}: {series.id}:"):
        end = text.find("\n\n")
        return len(text) if end < 0 else end
    return 0


def _cite(con, cite, where, cited_ids):
    """Resolve {observation_id|url, passage} to a stored observation and check the passage.
    Returns {observation_id, passage, checked}."""
    passage = cite["passage"]
    if not has_text(passage):
        raise ToolError(f"{where}: the quotation {passage[:80]!r} has fewer than three words, and the store takes one "
                        "of three or more: a figure comes with the words around it, as its sentence or as its row "
                        "with the row's heading.")
    if cite.get("url"):
        url = cite["url"].strip()
        oid = db.make_id("obs_", "page", url)
        # A bulk download can be the public URL of many distinct, independently stored rows.
        obs = db.get_observation(con, oid)
        if not (obs and obs.get("text") and quoted(passage, obs["text"])):
            matches = []
            for row in con.execute("SELECT id, text FROM observations WHERE url=? AND text IS NOT NULL", (url,)):
                if quoted(passage, row["text"]):
                    matches.append(row["id"])
                elif obs is None or not obs.get("text"):
                    obs = db.get_observation(con, row["id"])
            if len(matches) > 1:
                raise ToolError(f"{where}: several records at {url} contain this passage. Cite its observation_id.")
            if matches:
                obs = db.get_observation(con, matches[0])
        if obs is None:
            raise ToolError(f"{where}: the store holds no record read from {url}. A passage is quoted from a record "
                            "that read_record has stored.")
    else:
        obs = db.get_observation(con, cite["observation_id"])
        if obs is None:
            raise ToolError(f"{where}: no observation {cite['observation_id']!r}")
    cited_ids.append(obs["id"])
    db.mark_read(con, obs["id"])
    if obs.get("text"):
        if not quoted(passage, obs["text"]):
            raise ToolError(f"{where}: passage not found word for word in {obs['id']} ({obs.get('url') or obs['source']}): "
                            f"{passage[:160]!r}. Quote it exactly, using '...' for omissions; read_record(id, find=...) "
                            "shows the stored text.")
        passage = whole_sentences(passage, obs["text"], *_structure(obs))  # stored and shown: the whole of what it falls in
        checked = True
    else:
        raise ToolError(f"{where}: {obs['id']} has no text to quote. Compute the figure from its rows with "
                        "compute(save_as=...), and give the input the cell of that result.")
    return {"observation_id": obs["id"], "passage": passage, "checked": checked}


def _cell(con, at, inp, used):
    """A figure read by query, on an input or a given value (`at` says which): the store reads it from the cell
    of the saved result it names. A value given with it is the same figure, or the save says how the two differ."""
    cell = inp["result"]
    rid, where = cell["result_id"], f"{at}.result"
    r = db.get_result(con, rid)
    if not r:
        raise ToolError(f"{where}: no saved result {rid!r}; save one with compute(save_as=...)")
    cols = r["columns"]
    missing = [c for c in (cell["column"], *cell["row"]) if c not in cols]
    if missing:
        raise ToolError(f"{where}: {rid} ({r['name']}) has no column {missing[0]!r}; its columns: {', '.join(cols)}")
    rows = [row for row in r["rows"] if all(row[cols.index(k)] == v for k, v in cell["row"].items())]
    if len(rows) != 1:
        raise ToolError(f"{where}: the row given picks out {len(rows)} of the {len(r['rows'])} rows of {rid} "
                        f"({r['name']}). Give in row the columns and values that pick out one; its first rows: "
                        f"{json.dumps([dict(zip(cols, x)) for x in r['rows'][:3]], default=str)[:400]}")
    v = rows[0][cols.index(cell["column"])]
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        raise ToolError(f"{where}: {cell['column']} in that row of {rid} is {v!r}, not a number")
    given = inp.get("value")
    if given is not None and abs(given - v) > max(abs(v) * 1e-6, 0.005):
        raise ToolError(f"{at}: the value given is {given}, and the cell it names in {rid} holds {v}. Leave the "
                        "value out for the store to read, or name the cell that holds it. A figure worked out from "
                        "the cell's is an assumption that names the result it rests on.")
    inp["value"] = float(v)
    used.append(rid)


def _assumption(con, a, where, cited_ids, used):
    """What an assumed value rests on: each passage checked in its record, each result one that is saved."""
    a["evidence"] = [_cite(con, c, f"{where}.evidence[{i}]", cited_ids) for i, c in enumerate(a.get("evidence") or [])]
    for rid in a.get("results") or []:
        if not db.get_result(con, rid):
            raise ToolError(f"{where}.results: no saved result {rid!r}")
        used.append(rid)


def _figures(case):
    """[(where, figure)] for every figure a case states with a basis: its inputs, then its given values."""
    return [(f"inputs.{name}", inp) for name, inp in case["inputs"].items()] + \
        [(f"given_values[{i}].values.{name}", v) for i, g in enumerate(case["given_values"]) for name, v in g["values"].items()]


def _provenance(con, case, cited_ids, used):
    """Check the basis of every input and of every given value, in place: the passage a figure is read from is
    in its record, the cell it is read from holds it, and what an assumption rests on is there."""
    for at, fig in _figures(case):
        if fig.get("source"):
            fig["source"] = _cite(con, fig["source"], f"{at}.source", cited_ids)
        if fig.get("result"):
            _cell(con, at, fig, used)
        if fig.get("assumption"):
            _assumption(con, fig["assumption"], f"{at}.assumption", cited_ids, used)


def _cites(case, props=()):
    """Every passage a case quotes, and those of the propositions given with it, each as stored."""
    out = []
    for _, fig in _figures(case):
        out += [fig["source"]] * bool(fig.get("source")) + list((fig.get("assumption") or {}).get("evidence") or [])
    return out + list(case["evidence"]) + [c for p in props for c in p.get("evidence") or []]


def _need_observation(con, oid, where, cited_ids):
    if db.get_observation(con, oid) is None:
        raise ToolError(f"{where}: no observation {oid!r}")
    cited_ids.append(oid)


# ---------- companies ----------

def _excluded(names_):
    for raw in names_:
        for n, reason in exclusions():
            if names.matches(raw, n):
                return f"{reason}: {n}"
    return ""


def _merge_aliases(name, *lists):
    seen, out = {name.casefold()}, []
    for a in (x for lst in lists for x in lst or []):
        if a and a.casefold() not in seen:
            seen.add(a.casefold())
            out.append(a)
    return out


def _resolve_company(con, c, warnings):
    """(company ID, company data). A saved ID alone reads it. A company given with its id edits that
    company. A company given without one matches by domain, else by an unambiguous name, keeping what
    is already known about it, or is saved as new."""
    if isinstance(c, str):
        row = db.get_company(con, c)
        if not row:
            raise ToolError(f"company: no saved company {c!r}. Give the company in full, or an ID from lookup.")
        return row["id"], row["data"]
    data = c.model_dump(mode="json")
    given = data.pop("id", None)
    domain = names.domain_key(data.get("domain")) or None
    data["domain"] = domain
    reason = _excluded([data["name"], *data["aliases"]])
    if reason:
        raise ToolError(f"company: {data['name']} is excluded by the operator ({reason}).")
    listed = lambda rows: "; ".join(f"{r['name']} ({r['domain'] or 'no domain'}, {r['id']})" for r in rows)
    if given:
        row = db.get_company(con, given)
        if not row:
            raise ToolError(f"company.id: no saved company {given!r}")
        other = db.companies_by_domain(con, domain) if domain else None
        if other and other["id"] != given:
            raise ToolError(f"company: domain {domain} belongs to {listed([other])}")
    else:
        keys = {names.vendor_key(n) for n in [data["name"], *data["aliases"]]} - {""}
        candidates = list({r["id"]: r for k in keys for r in db.companies_by_key(con, k)}.values())
        row = db.companies_by_domain(con, domain) if domain else None
        if row is None and domain:
            undomained = [r for r in candidates if not r["domain"]]
            if len(undomained) > 1:
                raise ToolError(f"company: {data['name']} matches several saved companies without a domain: "
                                f"{listed(undomained)}. Pass the right company's id.")
            row = undomained[0] if undomained else None
            others = [r for r in candidates if r["domain"] and r["domain"] != domain]
            if row is None and others:
                warnings.append(f"Saved {data['name']} at {domain} as a new company; {listed(others)} has the same name. "
                                "If it is the same company, pass its id.")
        elif row is None and len(candidates) > 1:
            raise ToolError(f"company: {data['name']} matches several saved companies: {listed(candidates)}. "
                            "Give its domain, or pass the company's id.")
        elif row is None and candidates:
            row = candidates[0]
    if row:
        old = row["data"]
        data = {**old, **{k: v for k, v in data.items() if v not in (None, [], "")},
                "aliases": _merge_aliases(data["name"], data["aliases"], [old["name"]], old.get("aliases")),
                "domain": domain or old.get("domain")}
        cid = row["id"]
        con.execute("DELETE FROM company_keys WHERE company_id=?", (cid,))
    else:
        cid = db.make_id("co_", domain or "name:" + names.vendor_key(data["name"]))
    keys = {names.vendor_key(n) for n in [data["name"], *data["aliases"]]} - {""}
    db.put_company(con, cid, data["name"], data["domain"], data, keys)
    return cid, data


def _principal_check(company):
    """A company run's cases are for its principal company; others become findings or leads."""
    principal = os.environ.get("DISTRESS_RUN_PRINCIPAL")
    if not principal:
        return
    own = [company["name"], *company.get("aliases", [])]
    if not any(names.matches(n, principal) or names.matches(principal, n) for n in own):
        raise ToolError(f"This session's assignment is {principal}, and its cases are for that company. A finding can "
                        f"concern {company['name']}, and a follow-on lead can name it.")


# ---------- propositions ----------

def _outline(props, key, depth=0):
    """A proposition and its ways, one to a line."""
    p = props[key]
    side = " (No side)" if p.get("side") == "no" else ""
    out = [f"{'  ' * depth}{key}{side}: {p['title']}"]
    for w in sorted((w for w in props.values() if w.get("parent") == key), key=lambda w: (w["side"] != "yes", w["order"])):
        out += _outline(props, w["key"], depth + 1)
    return out


def _under(props, key):
    """The keys of every way within a proposition, at any depth."""
    out = []
    for w in props.values():
        if w.get("parent") == key:
            out += [w["key"], *_under(props, w["key"])]
    return out


def _lay_over(con, given, cited_ids=None, saving=None):
    """(the saved propositions with those given in full laid over them, the keys this states anew or
    otherwise than they were saved, the keys this removes). A proposition given in full replaces the saved one
    of the same key, its ways with it. `cited_ids` checks each passage of evidence in its record; without it
    nothing is checked, for a draft."""
    saved = db.propositions(con)
    props, stated, gone = dict(saved), [], []
    for g in given:
        flat = worlds.flatten([g])
        for k, f in flat.items():
            was = saved.get(k)
            if was and was.get("parent") != f.get("parent"):
                where = f"a way of {was['parent']!r}" if was.get("parent") else "a proposition of its own"
                raise ToolError(f"propositions: the key {k!r} already names {where} ({was['title']}). A key names one "
                                "proposition in the store; give this one another key, or revise that one in its place.")
            if cited_ids is not None:
                f["evidence"] = [_cite(con, c, f"propositions.{k}.evidence[{i}]", cited_ids)
                                 for i, c in enumerate(f.get("evidence") or [])]
        dropped = [k for k in _under(saved, g["key"]) if k not in flat]
        for k in dropped:
            readers = [c for c in db.readers(con, k, active=True) if c != saving]
            if readers:
                raise ToolError(f"propositions: this statement of {g['key']!r} leaves out {k!r}, which the saved "
                                f"case{'s' if len(readers) > 1 else ''} {', '.join(readers)} "
                                f"read{'' if len(readers) > 1 else 's'}. A key stays the same when a proposition's wording "
                                "is revised, and a finer distinction is drawn by giving it ways or by linking another "
                                "proposition to it. Keep it, or revise those cases first.")
            props.pop(k, None)
        gone += dropped
        props |= flat
        stated += list(flat)
    for k, p in props.items():
        for other in [*(p.get("implies") or []), *(p.get("implies_not") or [])]:
            if other not in props and k in stated:
                raise ToolError(f"propositions.{k}: it is linked to {other!r}, and no saved proposition has that key. Give "
                                "that proposition in full as well, or name one that is saved.")
            if other not in props:
                raise ToolError(f"propositions: this statement leaves out {other!r}, which the saved proposition {k!r} "
                                f"({p['title']}) is linked to. Keep it, or give {k!r} in full as well, without that link.")
    for k in stated:
        if worlds.outside(props, k):
            way, of = props[k], props[props[k]["parent"]]
            said = (f"the way {k!r} ({way['metric']} {worlds.values_in(worlds.interval(way))}) and {of['key']!r} "
                    f"({worlds.values_in(worlds.interval(of))})")
            raise ToolError(f"propositions: by their level fields, {said} share values. A way of a proposition's No "
                            "side holds where the proposition is false, so it has none of its values."
                            if way["side"] == "no" else
                            f"propositions: by their level fields, {said}: the way has values the proposition does "
                            "not. A way holds only where what it divides does, so its values lie within that one's.")
        both = worlds.overlapping(props, k)
        if both:
            a, b = both[0]
            raise ToolError(f"propositions: the ways {a!r} and {b!r} of {props[k]['parent']!r} can both be true by their "
                            "level fields. The ways beside one another exclude one another; state their thresholds so "
                            "that no value falls in two of them.")
    return props, [k for k in stated if saved.get(k) != props[k]], gone


def _stated(propositions):
    """Propositions given in full with a call, validated together."""
    given = [_validate(Proposition, p) for p in propositions or []]
    try:
        check_propositions(given)
    except ValueError as e:
        raise ToolError(f"propositions: {e}")
    return [g.model_dump(mode="json", exclude_none=True) for g in given]


def _keep(con, props, stated, gone):
    for k in reversed(gone):
        db.delete_proposition(con, k)
    for k in stated:               # a proposition comes before its ways
        db.put_proposition(con, props[k])


def _proposition_view(props, key):
    """A proposition in full with its ways, as the save takes it, and what its level fields say."""
    top = worlds.root(props, key)
    nested = worlds.nest(props, top)

    def clean(p):
        p["evidence"] = [{k: v for k, v in c.items() if k != "checked"} for c in p.get("evidence") or []]
        if not p["evidence"]:
            del p["evidence"]
        for w in p.get("ways") or []:
            clean(w)
        if not p.get("ways"):
            p.pop("ways", None)
        return p

    keys = [top, *_under(props, top)]
    return {"proposition": clean(nested), "outline": "\n".join(_outline(props, top)),
            "level_fields": [f"{a} {rel} {b}" for a, rel, b in worlds.read_fields(props, keys)] + _uncovered(props, keys)}


def _uncovered(props, keys):
    """The values the level fields of the ways beside one another leave in none of them, in words."""
    seen, out = set(), []
    for k in keys:
        group = tuple(worlds.beside(props, k))
        if group and group not in seen:
            seen.add(group)
            side = " on its No side" if props[k]["side"] == "no" else ""
            out += [f"The ways of {props[k]['parent']}{side} leave {props[k]['metric']} {worlds.values_in(span)} in none "
                    "of them" for span in worlds.gaps(props, k)]
    return out


# ---------- the calculation ----------

def _key_date(props, case):
    return min(str(props[k]["window_end"]) for k in case["events"])


def _derive(case, props):
    """The one calculation path, on the stored forms of a case and the propositions."""
    try:
        return exposure.compute(case, props)
    except ValueError as e:
        raise ToolError(str(e))


def _digest(snapshot):
    return hashlib.sha1(db.dumps(snapshot).encode()).hexdigest()


def _events(props, case):
    for k in case["events"]:
        if k not in props:
            raise ToolError(f"events: no saved proposition {k!r}. Give it in full in propositions, or name one that is "
                            "saved.")
        if props[k].get("parent"):
            raise ToolError(f"events: {k!r} is a way of {props[k]['parent']!r}. A case's event is a proposition of its "
                            f"own; name {worlds.root(props, k)!r}, and give values for the way.")


def _bare(props, results):
    """The propositions a case reads that carry no evidence of their own."""
    return [k for k in results["reads"] if not props[k].get("evidence")]


def calculate_exposure(case: dict, propositions: list | None = None) -> dict:
    """Run the calculation save_case runs, on a draft."""
    c = _validate(Draft, case)
    given = _stated(propositions)
    draft = c.model_dump(mode="json")
    con = db.connect()
    try:
        props, _, _ = _lay_over(con, given)
        _events(props, draft)
        for at, fig in _figures(draft):
            if fig.get("result"):
                _cell(con, at, fig, [])
    finally:
        con.close()
    results, warnings = _derive(draft, props)
    return {"results": results, "warnings": warnings, "propositions_without_evidence": _bare(props, results)}


# ---------- save ----------

def save_case(case: dict, propositions: list | None = None) -> dict:
    """Validate, check each basis, compute the worlds and their figures, save the propositions given, the
    company and the case, and write a revision when the content changed."""
    c = _validate(Case, case)
    given = _stated(propositions)
    con = db.connect()
    try:
        with db.write(con):
            out = _save(con, c, given)
    finally:
        con.close()
    for cid in [out["case_id"], *out["also_revised"]]:
        _deliver(cid)
    return out


def _changed(case, changes):
    """A saved case with the parts that change laid over it. A field given replaces the saved one. An input
    named is added or replaced, and removed where it is null. A given value with the same given and given_not as
    a saved one changes that one for the inputs it names, and any other is added; one left with no input goes."""
    for field, value in changes.items():
        if field == "inputs":
            for name, inp in (value or {}).items():
                if inp is None:
                    case["inputs"].pop(name, None)
                else:
                    case["inputs"][name] = inp
        elif field == "given_values":
            for g in value or []:
                true, false = g.get("given") or [], g.get("given_not") or []
                same = lambda x: set(x["given"]) == set(true) and set(x["given_not"]) == set(false)  # noqa: E731
                saved = next((x for x in case["given_values"] if same(x)), None)
                if saved is None:
                    saved = {"given": true, "given_not": false, "values": {}}
                    case["given_values"].append(saved)
                for name, v in g["values"].items():
                    if v is None:
                        saved["values"].pop(name, None)
                    else:
                        saved["values"][name] = v
            case["given_values"] = [x for x in case["given_values"] if x["values"]]
        elif field != "change_note":
            case[field] = value
    return case


def _before_and_after(before, after):
    """Each figure of a case before a change and after it: the baseline, the revenue at stake, and each world's
    revenue and shortfall. A world the change brings has no figure before; one it removes is listed apart."""
    was = {w["label"]: w for w in before["worlds"]}
    now = {w["label"] for w in after["worlds"]}
    pair = lambda k, a, b: {k: [a, b]}  # noqa: E731
    return {**pair("baseline_revenue", before["baseline_revenue"], after["baseline_revenue"]),
            **pair("revenue_at_stake", before["max_shortfall"], after["max_shortfall"]),
            "worlds": [{"world": w["label"],
                        **pair("revenue", (was.get(w["label"]) or {}).get("revenue"), w["revenue"]),
                        **pair("shortfall", (was.get(w["label"]) or {}).get("shortfall"), w["shortfall"])}
                       for w in after["worlds"]],
            "worlds_removed": [label for label in was if label not in now]}


def revise_case(case_id: str, changes: dict, propositions: list | None = None) -> dict:
    """Change part of a saved case: lay the changes over it, then check, compute and save it as save_case does.
    Returns what save_case returns, with each figure before and after."""
    ch = _validate(Changes, changes).model_dump(mode="json", exclude_unset=True)
    given = _stated(propositions)
    con = db.connect()
    try:
        row = db.get_case(con, case_id)
        if not row:
            raise ToolError(f"case_id: no saved case {case_id!r}")
        last = db.latest_revision(con, case_id)["snapshot"]
        before = forms.read(row, last, db.propositions(con))["results"]
        saved = _editable(case_id, {"case": last["case"], "company": {"id": row["company_id"]}})
        c = _validate(Case, _changed(saved, ch) | {"change_note": ch["change_note"]})
        with db.write(con):
            out = _save(con, c, given)
    finally:
        con.close()
    for cid in [out["case_id"], *out["also_revised"]]:
        _deliver(cid)
    return out | {"before_and_after": _before_and_after(before, out["results"])}


def _deliver(case_id):
    """Put a saved or withdrawn case in front of the operator in HubSpot. A delivery problem never fails
    the save: the worker delivers changed cases again."""
    from . import hubspot
    if not hubspot.configured():
        return
    try:
        hubspot.sync_case(case_id)
    except Exception as e:
        print(f"HubSpot delivery of {case_id} failed: {type(e).__name__}: {str(e)[:300]}", file=sys.stderr)


def _snapshot(company_id, company, case, props, results):
    """A case as it stands: the company, the case, the propositions it reads with what they lie within, and
    the figures."""
    held = worlds._closure(props, results["reads"])
    return {"company": {"id": company_id, **company}, "case": case,
            "propositions": {k: props[k] for k in held}, "results": results}


def _cited(snapshot):
    """The records a case cites as it stands: under its figures and claims, and under the propositions its
    worlds rest on."""
    case = snapshot["case"]
    return [c["observation_id"] for c in _cites(case, snapshot["propositions"].values())] \
        + [case["person"]["observation_id"]] * bool(case["person"].get("observation_id"))


def _revise(con, cid, row, snapshot, change_note):
    """Write a revision when the snapshot differs from the last one. Returns (revision, changed)."""
    last = db.latest_revision(con, cid)
    digest = _digest(snapshot)
    if last and last["hash"] == digest:
        return last["revision"], False
    revision = (last["revision"] if last else 0) + 1
    case, results = snapshot["case"], snapshot["results"]
    db.put_case(con, {"id": cid, **row, "key_date": _key_date(snapshot["propositions"], case), "max_loss": results["max_shortfall"],
                      "currency": case["currency"], "case_json": case, "results": results, "revision": revision},
                results["reads"])
    db.add_revision(con, cid, revision, digest, change_note, snapshot)
    db.set_citations(con, cid, _cited(snapshot))
    return revision, True


def _others(con, props, touched, but=None):
    """The other active cases a call's statements moved, where it stated or removed a proposition. Each is
    computed again on the propositions as they now stand, since a proposition reaches a case's worlds through
    the ways beside what it reads and through links as well as by being read. One whose propositions or
    figures differ gets a revision. A statement that leaves one without a figure is refused, naming it."""
    out = []
    for row in db.cases(con, "status='active' AND id<>?", (but or "",)) if touched else []:
        cid = row["id"]
        last = db.latest_revision(con, cid)["snapshot"]
        try:
            results, _ = exposure.compute(row["case_json"], props)
        except ValueError as e:
            raise ToolError(f"propositions: as stated here, the saved case {cid} no longer has its figures: {e} "
                            "Keep what that case reads, or revise that case first.")
        snapshot = _snapshot(last["company"]["id"], {k: v for k, v in last["company"].items() if k != "id"},
                             row["case_json"], props, results)
        was, now = last["propositions"], snapshot["propositions"]
        moved = [k for k in dict.fromkeys([*was, *now]) if was.get(k) != now.get(k)]
        revision, changed = _revise(con, cid, {"company_id": row["company_id"], "events": row["events"],
                                               "stream_key": row["stream_key"]}, snapshot,
                                    "The propositions this case rests on changed: " + ", ".join(moved))
        if changed:
            out.append(cid)
    return out


def _save(con, c: Case, given):
    cited_ids, results_used, warnings = [], [], []
    company_id, company = _resolve_company(con, c.company, warnings)
    _principal_check(company)
    if not company.get("description"):
        warnings.append("The company has no description of how it earns its revenue.")
    events = sorted(c.events)
    cid = c.case_id or db.make_id("case_", company_id, *events, c.stream_key)
    existing = db.get_case(con, cid)
    if c.case_id:
        if not existing:
            raise ToolError(f"case_id: no saved case {c.case_id!r}")
        was = (existing["company_id"], existing["events"], existing["stream_key"])
        if was != (company_id, events, c.stream_key):
            co = db.get_company(con, was[0])
            raise ToolError(f"{c.case_id} is the case for {co['name']} / {', '.join(was[1])} / {was[2]}, but this save "
                            "names a different company, events or stream_key. Keep them to revise that case, or save a "
                            "different exposure without case_id.")
    elif existing:
        warnings.append(f"Revised the saved case {cid} for this company, these events and this stream_key.")
    props, stated, gone = _lay_over(con, given, cited_ids, saving=cid)

    case = c.model_dump(mode="json", exclude={"case_id", "company", "change_note"})
    case["events"] = events
    _events(props, case)
    _provenance(con, case, cited_ids, results_used)
    case["evidence"] = [_cite(con, ev, f"evidence[{i}]", cited_ids) | {"supports": ev["supports"]}
                        for i, ev in enumerate(case["evidence"])]
    if case["person"].get("observation_id"):
        _need_observation(con, case["person"]["observation_id"], "person.observation_id", cited_ids)
    elif case["person"].get("name"):
        warnings.append("person: named without the record that names them in this role (person.observation_id).")

    scaled = re.compile(r"(?i)\b(thousands?|millions?|billions?|mn|bn|mm)\b")
    money = re.compile(rf"(?i)\b{c.currency}\b|[$€£¥]")
    for name, inp in case["inputs"].items():
        if money.search(inp["unit"]) and scaled.search(inp["unit"]):
            warnings.append(f"inputs.{name}: its unit is '{inp['unit']}', but amounts of money are in whole units of "
                            f"{c.currency}, so the results read {inp['value']:,} as {c.currency} {inp['value']:,}.")
    results, calc_warnings = _derive(case, props)
    warnings += calc_warnings
    _keep(con, props, stated, gone)
    snapshot = _snapshot(company_id, company, case, props, results)
    revision, changed = _revise(con, cid, {"company_id": company_id, "events": events, "stream_key": c.stream_key},
                                snapshot, c.change_note)
    if existing and existing.get("status") == "withdrawn":
        db.set_status(con, cid, "active")
        warnings.append(f"{cid} was withdrawn; saving it returns it to the active queue.")
    also = _others(con, props, [*stated, *gone], but=cid)
    warnings += _uncovered(props, stated)
    return {"case_id": cid, "company_id": company_id, "revision": revision, "changed": changed,
            "condition": results["condition"], "results": results, "warnings": warnings,
            "workspace_results_used": sorted(set(results_used)),
            "propositions_without_evidence": _bare(props, results), "propositions_saved": stated, "also_revised": also}


# ---------- findings ----------

def record_finding(finding: dict, propositions: list | None = None) -> dict:
    f = _validate(Finding, finding)
    given = _stated(propositions)
    con = db.connect()
    try:
        with db.write(con):
            warnings, cited = [], []
            company_id = _resolve_company(con, f.company, warnings)[0] if f.company else None
            props, stated, gone = _lay_over(con, given, cited)
            _keep(con, props, stated, gone)
            also = _others(con, props, [*stated, *gone])
            if f.proposition and f.proposition not in props:
                raise ToolError(f"proposition: no saved proposition {f.proposition!r}; give it in full in propositions "
                                "to save it")
            if f.case_id and not db.get_case(con, f.case_id):
                raise ToolError(f"case_id: no saved case {f.case_id!r}")
            for rid in f.results:
                if not db.get_result(con, rid):
                    raise ToolError(f"results: no saved result {rid!r}")
            if f.supersedes and not db.get_finding(con, f.supersedes):
                raise ToolError(f"supersedes: no finding {f.supersedes!r}")
            evidence = [_cite(con, c.model_dump(mode="json"), f"evidence[{i}]", cited) for i, c in enumerate(f.evidence)]
            fid = db.make_id("fnd_", f.kind, f.statement, company_id, f.proposition, now_iso())
            db.put_finding(con, {"id": fid, "kind": f.kind, "statement": f.statement, "company_id": company_id,
                                 "proposition": f.proposition, "case_id": f.case_id, "evidence": evidence,
                                 "results": f.results, "supersedes": f.supersedes})
            if f.withdraw_case:
                db.set_status(con, f.case_id, "withdrawn")
                db.set_progress(con, f.case_id, {**(db.get_case(con, f.case_id)["progress"] or {}), "withdrawn_by": fid})
    finally:
        con.close()
    for cid in [*([f.case_id] if f.withdraw_case else []), *also]:
        _deliver(cid)
    return {"finding_id": fid, "company_id": company_id, "proposition": f.proposition,
            **({"withdrawn": f.case_id} if f.withdraw_case else {}),
            "warnings": warnings,
            "propositions_saved": stated, "also_revised": also}


# ---------- the workspace ----------

MAX_ROWS = 2000      # rows a saved result keeps
SHOW_ROWS = 200      # rows a call returns
QUERY_SECONDS = 15


def _workspace():
    """An in-memory database holding what the current run has read and recorded: its observations, a table
    for each series with one row for each record read (the latest read of it), the views that cross series,
    and its findings and workspace results. Saved cases, companies, events and earlier runs' research
    aren't in it."""
    src, mem = db.connect(), sqlite3.connect(":memory:")
    run = os.environ.get("DISTRESS_RUN_ID")
    q = lambda name: '"' + name + '"'  # noqa: E731

    def chunks(sql, keys):
        for i in range(0, len(keys), 500):
            part = keys[i:i + 500]
            yield from src.execute(sql.format(",".join("?" * len(part))), part)
    try:
        ids = db.run_observations(src)
        for t, (col, own) in {"observations": ("id", False), "findings": ("run_id", True),
                              "workspace_results": ("run_id", True)}.items():
            cols = [r[1] for r in src.execute(f"PRAGMA table_info({t})")]
            mem.execute(f"CREATE TABLE {t} ({', '.join(cols)})")
            keys = ([run] if run else None) if own else ids
            rows = src.execute(f"SELECT {', '.join(cols)} FROM {t}") if keys is None else \
                chunks(f"SELECT {', '.join(cols)} FROM {t} WHERE {col} IN ({{}})", keys)
            mem.executemany(f"INSERT INTO {t} VALUES ({','.join('?' * len(cols))})", [tuple(r) for r in rows])
        read_at = dict(mem.execute("SELECT id, retrieved_at FROM observations"))
        for t in sources.tables():
            cols = [r[1] for r in src.execute(f"PRAGMA table_info({q(t)})")]
            mem.execute(f"CREATE TABLE {q(t)} ({', '.join(q(c) + (' PRIMARY KEY' if c == 'record_id' else '') for c in cols)})")
            rows = sorted(chunks(f"SELECT {', '.join(map(q, cols))} FROM {q(t)} WHERE observation_id IN ({{}})", ids),
                          key=lambda r: read_at.get(r["observation_id"]) or "")
            mem.executemany(f"INSERT OR REPLACE INTO {q(t)} VALUES ({','.join('?' * len(cols))})", [tuple(r) for r in rows])
        for view, sql in sources.views().items():
            mem.execute(f"CREATE VIEW {view} AS {sql}")
    finally:
        src.close()
    mem.execute("PRAGMA query_only=ON")
    return mem


def compute(sql: str, save_as: str | None = None, note: str | None = None) -> dict:
    """One read-only SQL query over what this run has read and recorded."""
    q = (sql or "").strip().rstrip(";")
    if not re.match(r"(?is)^(select|with)\b", q):
        raise ToolError("compute runs one SELECT (or WITH ... SELECT) query")
    con = _workspace()
    deadline = time.monotonic() + QUERY_SECONDS
    con.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10000)
    try:
        cur = con.execute(q)
        cols = [c[0] for c in cur.description or []]
        rows = [list(r) for r in cur.fetchmany(MAX_ROWS + 1)]
    except Exception as e:
        raise ToolError(f"query failed: {e}" + (" (stopped after 15 seconds)" if time.monotonic() > deadline else ""))
    finally:
        con.close()
    more = len(rows) > MAX_ROWS
    rows = rows[:MAX_ROWS]
    out = {"columns": cols, "rows": rows[:SHOW_ROWS], "row_count": len(rows), **({"more_rows": True} if more else {})}
    if len(rows) > SHOW_ROWS:
        out["note"] = f"Showing {SHOW_ROWS} of {len(rows)} rows; aggregate, or save the result to keep them all."
    if save_as:
        rid = db.make_id("wk_", save_as, q)
        w = db.connect()
        try:
            with db.write(w):
                db.put_result(w, {"id": rid, "name": save_as, "sql": q, "columns": cols, "rows": rows,
                                  "row_count": len(rows), "note": note})
        finally:
            w.close()
        out["result_id"] = rid
    return out


# ---------- reading saved research ----------

def _editable(cid, current):
    """The saved case in the exact form save_case accepts: the company by ID, store-set fields removed."""
    case = copy.deepcopy(current["case"])
    for c in _cites(case):
        c.pop("checked", None)
    return {"case_id": cid, "company": current["company"]["id"], **case, "change_note": ""}


def _finding_view(con, f):
    return {"finding_id": f["id"], "kind": f["kind"], "statement": f["statement"], "company_id": f["company_id"],
            "proposition": f["proposition"], "case_id": f["case_id"], "results": f["results"],
            "evidence": [{"observation_id": e["observation_id"], "passage": e["passage"]} for e in f["evidence"]],
            "created": f["created"]}


def _brief(con, row, props):
    """A case's dated estimate in brief: who, which stream, over what period, the formula, the given values,
    and the revenue and shortfall in each world. lookup of the case gives the inputs and their bases."""
    c = db.get_company(con, row["company_id"])
    last = db.latest_revision(con, row["id"])
    current = forms.read(row, last["snapshot"], props)
    case, res = current["case"], current["results"]
    return {"case_id": row["id"], "status": row["status"], "company": c["name"], "events": row["events"],
            "stream": case["stream"], "period": case["period"], "estimated": last["saved_at"],
            "condition": res["condition"], "revenue": case["revenue"], "baseline_revenue": res["baseline_revenue"],
            "given_values": [{"given": g["given"], "given_not": g["given_not"],
                              "values": {k: v["value"] for k, v in g["values"].items()}} for g in case["given_values"]],
            "worlds": [{"world": w["label"], "values": w["values"], "revenue": w["revenue"], "shortfall": w["shortfall"],
                        "share": w["share"]} for w in res["worlds"]],
            "max_shortfall": res["max_shortfall"], "currency": row["currency"],
            "revision": row["revision"], "updated": row["updated"]}


UPDATE = ("Edit 'case' and pass it to save_case with a change_note. To revise a proposition, give it in full, its ways "
          "with it, in propositions; to edit the company, put its object (with its id) in 'case' in place of the ID. "
          "record_finding with withdraw_case withdraws the case. read_record(observation_id) returns any cited record "
          "in full.")


WITHDRAWN = ("This case is withdrawn, and reads as its last revision held it, with the propositions as they stood then. "
             "Saving 'case' with a change_note returns it to the active queue, computed on the propositions as they "
             "now stand; lookup of a proposition's key returns it as it now stands.")


def lookup(id_or_query: str) -> dict:
    """A saved case (case_...), company (co_...), finding (fnd_...) or workspace result (wk_...) by its ID; a
    proposition by its key or title; a company by its name, alias or domain."""
    q = (id_or_query or "").strip()
    if not q:
        raise ToolError("give an ID, a proposition's key or title, or a company's name, alias or domain")
    con = db.connect()
    try:
        props = db.propositions(con)
        if q.startswith("case_"):
            row = db.get_case(con, q)
            if not row:
                raise ToolError(f"no saved case {q!r}")
            current = forms.read(row, db.latest_revision(con, q)["snapshot"], props)
            props = current["propositions"]
            company = db.get_company(con, row["company_id"])
            current["company"] = {"id": company["id"], **company["data"]}
            titles = {o["id"]: o for o in db.cited(con, q)}
            case, res = current["case"], current["results"]

            def passages(at, cites, **more):
                return [{"at": at, "passage": c["passage"], "checked": c.get("checked"), **more,
                         "observation_id": c["observation_id"]} for c in cites]

            evidence = []
            for at, fig in _figures(case):
                evidence += passages(at, [fig["source"]] * bool(fig.get("source"))
                                     + list((fig.get("assumption") or {}).get("evidence") or []))
            for i, e in enumerate(case["evidence"]):
                evidence += passages(f"evidence[{i}]", [e], supports=e["supports"])
            for e in evidence:
                o = titles.get(e["observation_id"]) or {}
                e["source"] = {"title": (o.get("data") or {}).get("title"), "url": o.get("url"), "kind": o.get("kind")}
            fs = db.findings(con, "case_id=? OR company_id=?", (q, row["company_id"]))
            roots = list(dict.fromkeys(worlds.root(props, k) for k in res["reads"]))
            return {"case": _editable(q, current), "status": row["status"],
                    "company_id": company["id"], "company": company["data"],
                    "propositions": [_proposition_view(props, k)["proposition"] for k in roots],
                    "condition": res["condition"], "results": res,
                    "warnings": current["warnings"], "evidence": evidence,
                    "findings": [_finding_view(con, f) for f in fs], "revision": row["revision"],
                    **({"conversation_outcome": {k: v for k, v in row["progress"].items() if k in ("outcome", "outcome_note")}}
                       if (row["progress"] or {}).get("outcome") else {}),
                    "updated": row["updated"], "how_to_update": UPDATE if row["status"] == "active" else WITHDRAWN,
                    "revisions": db.revisions(con, q)[-8:]}
        if q.startswith("co_"):
            c = db.get_company(con, q)
            if not c:
                raise ToolError(f"no saved company {q!r}")
            return {"company_id": q, "company": c["data"],
                    "cases": [_brief(con, r, props) for r in db.cases(con, "company_id=?", (q,))],
                    "findings": [_finding_view(con, f) for f in db.findings(con, "company_id=?", (q,))]}
        if q.startswith("fnd_"):
            f = db.get_finding(con, q)
            if not f:
                raise ToolError(f"no finding {q!r}")
            return _finding_view(con, f) | {"status": f["status"], "supersedes": f["supersedes"]}
        if q.startswith("wk_"):
            r = db.get_result(con, q)
            if not r:
                raise ToolError(f"no saved result {q!r}")
            return {"result_id": q, "name": r["name"], "sql": r["sql"], "note": r["note"], "columns": r["columns"],
                    "rows": r["rows"][:SHOW_ROWS], "row_count": r["row_count"], "created": r["created"]}
        key = db.find_proposition(con, q)
        if key:
            top = worlds.root(props, key)
            within = [top, *_under(props, top)]
            readers = sorted({c for k in within for c in db.readers(con, k)})
            marks = ",".join("?" * len(within))
            return {**_proposition_view(props, key), **({"way_of": top} if top != key else {}),
                    "cases": [_brief(con, db.get_case(con, c), props) for c in readers],
                    "findings": [_finding_view(con, f) for f in db.findings(con, f"proposition IN ({marks})", within)]}
        name_key, domain = names.vendor_key(q), names.domain_key(q)
        cos = {r["id"]: r for r in db.companies_by_key(con, name_key)} if name_key else {}
        if domain and "." in domain and db.companies_by_domain(con, domain):
            row = db.companies_by_domain(con, domain)
            cos[row["id"]] = row
        if not cos:
            raise ToolError(f"Nothing saved is named {q!r}. lookup takes an ID, a proposition's key or title, or a "
                            "company's name, alias or domain.")
        return {"companies": [{"company_id": c["id"], "company": c["data"],
                               "cases": [_brief(con, r, props) for r in db.cases(con, "company_id=?", (c["id"],))],
                               "findings": [_finding_view(con, f) for f in db.findings(con, "company_id=?", (c["id"],))]}
                              for c in cos.values()]}
    finally:
        con.close()


# ---------- records ----------

VIEW = 12000  # characters of text a read returns at once


def search_records(series: str, query: str = "", filters: dict | None = None, limit: int = sources.LIMIT,
                   cursor: str | None = None) -> dict:
    try:
        return sources.search(series, query, filters, limit, cursor)
    except SourceError as e:
        raise ToolError(str(e))


def _locate(ref):
    """(observation ID, fetch function) for a URL, a record_id or a result_set."""
    if re.match(r"^https?://", ref):
        return db.make_id("obs_", "page", ref), lambda: web.read(ref)
    if not sources.parse(ref):
        raise ToolError("read_record takes a record_id or a result_set that search_records returned, a URL, or an "
                        "observation ID (obs_...)")

    def fetch():
        got = sources.read(ref)
        return got
    series, kind, rest = sources.parse(ref)
    return db.make_id("obs_", series.id, kind, rest), fetch


def _source_ref(obs):
    """The reference that reads an observation's source again."""
    return sources.again(obs) or obs.get("url")


def _view(obs, find=None, offset=0):
    text, data = obs.get("text") or "", obs.get("data") or {}
    out = {"observation_id": obs["id"], "kind": obs["kind"], "source": obs["source"], "url": obs.get("url"),
           "title": data.get("title"), "published": obs.get("published"), "retrieved_at": obs["retrieved_at"],
           "length": len(text)}
    if obs["kind"] in ("record", "result_set"):
        out["workspace_table"] = obs["source"]
        if data.get("count") is not None:
            out["records"] = data["count"]
    if not text and data.get("unchecked"):
        out["note"] = "Only quoted passages are stored for this page (read with WebFetch)."
        out["passages"] = data.get("passages", [])
        return out
    if find:
        low, hits = text.lower(), []
        # plain text, not a pattern: a backslash written as a regex escape (\$, \.) is dropped
        for term in [re.sub(r"\\(.)", r"\1", t).strip() for t in find.split("|") if t.strip()]:
            start, n = 0, 0
            while n < 8 and len(hits) < 24:
                i = low.find(term.lower(), start)
                if i < 0:
                    break
                hits.append({"term": term, "offset": i, "snippet": text[max(0, i - 600): i + len(term) + 600]})
                start, n = i + len(term), n + 1
        out["matches"] = hits
        if not hits:
            out["note"] = "No matches. Try other words, or read the text by offset."
        return out
    out["text"] = text[offset: offset + VIEW]
    if offset + VIEW < len(text):
        out["next_offset"] = offset + VIEW
    if offset == 0 and data.get("links"):
        out["links"] = data["links"][:80]
    return out


def read_record(id_or_url: str, find: str | None = None, offset: int = 0, fresh: bool = False) -> dict:
    """Read a record and store it as an observation. A stored record is reused unless fresh, which reads
    its source again (for an observation ID too) and updates the same observation and its rows."""
    ref = (id_or_url or "").strip()
    con = db.connect()
    try:
        if ref.startswith("obs_"):
            obs = db.get_observation(con, ref)
            if not obs:
                raise ToolError(f"no observation {ref!r}")
            if not fresh:
                with db.write(con):
                    db.mark_read(con, ref)
                return _view(obs, find, offset)
            src = _source_ref(obs)
            if not src:
                raise ToolError(f"{ref} has no source that can be read again")
            oid, fetch = ref, _locate(src)[1]
        else:
            oid, fetch = _locate(ref)
            obs = db.get_observation(con, oid)
            if obs and obs.get("text") and not fresh:
                with db.write(con):
                    db.mark_read(con, oid)
                return _view(obs, find, offset)
        try:
            got = fetch()
        except SourceError as e:
            raise ToolError(str(e))
        with db.write(con):
            db.put_observation(con, {**got, "id": oid, "retrieved_at": now_iso()})
            db.mark_read(con, oid)
        return _view(db.get_observation(con, oid), find, offset)
    finally:
        con.close()
