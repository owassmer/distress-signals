"""The live feed: while a run goes, its events stream into HubSpot. Each minute, one note on the record
that asked for the run (a company, or a case's deal) gives the model's reasoning summaries and what it
did since the last note, and "Distress run progress" says what it is doing now. The researcher isn't
touched: the feed reads the run's event stream as it is written to out/runs/<run>.jsonl.

  python -m distress_signals feed RUN_ID companies|deals RECORD_ID     attach to a run that is going
"""

import datetime as dt
import html
import json
import threading
import time

from . import db, hubspot
from .util import OUT, found_line

EVERY = 60            # seconds between notes
NOTE_CHARS = 20000    # a note's length before the rest goes into the next one


def _esc(text):
    return html.escape(text).replace("\n", "<br>")


def describe(use):
    """One plain line for a tool call."""
    name, i = use["name"].replace("mcp__ds__", ""), use.get("input") or {}
    if name == "WebSearch":
        return f"Searched the web: {i.get('query')}"
    if name == "WebFetch":
        return f"Read without storing: {i.get('url')}"
    if name == "search_records":
        return f"Searched {i.get('source')}: {i.get('query')}"
    if name == "read_record":
        return f"Read {i.get('id_or_url')}" + (f", looking for {i['find']}" if i.get("find") else "")
    if name == "compute":
        return "Computed: " + (i.get("note") or i.get("save_as") or (i.get("sql") or "")[:160])
    if name == "lookup":
        return f"Looked up {i.get('id_or_query')}"
    if name == "calculate_exposure":
        return "Calculated the revenue at stake for a draft case"
    if name == "record_finding":
        f = i.get("finding") or {}
        return f"Recorded a finding ({f.get('kind')}): {(f.get('statement') or '')[:400]}"
    if name == "save_opportunity":
        from .watch import _company_name, title_label
        o = i.get("opportunity") or {}
        return f"Saving the case for {_company_name(o.get('company'))}: {title_label(o.get('stream_key'))}"
    if name == "StructuredOutput":
        return "Writing the final answer"
    return name


def _result_text(block):
    c = block.get("content")
    if isinstance(c, list):
        return "\n".join(x.get("text", "") for x in c if isinstance(x, dict))
    return str(c or "")


class Feed:
    """Reads a run's events as they are written and posts them to a HubSpot record."""

    def __init__(self, run_id, obj, hs_id):
        self.run_id, self.obj, self.hs_id = run_id, obj, hs_id
        self.path = OUT / "runs" / f"{run_id}.jsonl"
        self.offset, self.items, self.uses = 0, [], {}
        self.calls, self.cases, self.done, self.now = 0, {}, False, ""  # cases: opportunity ID -> company ID
        self.since = time.strftime("%H:%M")

    def read(self):
        if not self.path.exists():
            return
        with open(self.path, "rb") as f:
            f.seek(self.offset)
            chunk = f.read()
        end = chunk.rfind(b"\n")
        if end < 0:
            return
        self.offset += end + 1
        for line in chunk[:end].splitlines():
            try:
                self._event(json.loads(line))
            except ValueError:
                continue

    def _event(self, e):
        t = e.get("type")
        if t == "assistant":
            for b in e["message"]["content"]:
                kind = b.get("type")
                if kind == "thinking" and (b.get("thinking") or "").strip():
                    self.items.append(("Thinking", _esc(b["thinking"].strip())))
                elif kind == "text" and (b.get("text") or "").strip():
                    self.items.append(("Says", _esc(b["text"].strip())))
                elif kind == "tool_use":
                    self.calls += 1
                    self.uses[b["id"]] = b
                    self.now = describe(b)
                    self.items.append(("Did", _esc(self.now)))
        elif t == "user" and isinstance(e["message"].get("content"), list):
            for b in e["message"]["content"]:
                if b.get("type") != "tool_result":
                    continue
                use = self.uses.get(b.get("tool_use_id")) or {}
                name, text = use.get("name", "").replace("mcp__ds__", ""), _result_text(b)
                if b.get("is_error"):
                    self.items.append(("Refused", _esc(f"{name}: {text[:300]}")))
                elif name == "save_opportunity":
                    self._saved(use, text)
                elif name == "record_finding" and ((use.get("input") or {}).get("finding") or {}).get("withdraw_case"):
                    oid = use["input"]["finding"].get("opportunity_id")
                    self.cases.pop(oid, None)
                    self.items.append(("Case", _esc(f"Withdrew case {oid}: {use['input']['finding'].get('statement', '')[:400]}")))
        elif t == "system" and e.get("subtype") == "compact_boundary":
            self.items.append(("Did", "Its context was compacted to make room"))
        elif t == "result":
            self.done = True
            self.items.append(("Finished", _esc(f"The run ended ({e.get('subtype')}), {e.get('num_turns')} turns.")))

    def _saved(self, use, text):
        try:
            out = json.loads(text)
        except ValueError:
            return
        from .watch import _company_name
        oid = out.get("opportunity_id")
        name = _company_name(out.get("company_id") or ((use.get("input") or {}).get("opportunity") or {}).get("company"))
        self.cases[oid] = out.get("company_id") or name
        con = db.connect()
        try:
            d = db.get_delivery(con, hubspot.DEST, "deal", oid)
        finally:
            con.close()
        link = f' <a href="{hubspot.record_url("deals", d["external_id"])}">Open the deal</a>' if d else ""
        self.items.append(("Case", f"Saved the case for {_esc(str(name))}, revision {out.get('revision')}.{link}"))

    def _elapsed(self):
        con = db.connect()
        try:
            row = con.execute("SELECT started FROM runs WHERE id=?", (self.run_id,)).fetchone()
        finally:
            con.close()
        if not row:
            return ""
        return f"{int((dt.datetime.now() - dt.datetime.fromisoformat(row['started'])).total_seconds() // 60)} min"

    def post(self):
        """Post what has come in since the last note, and the progress line."""
        now = time.strftime("%H:%M")
        stats = f"{self._elapsed()} in, {self.calls} tool calls, {found_line(list(self.cases.values()))}"
        if self.items:
            from .watch import url
            head = (f"<p><b>Distress run {self.run_id}, {self.since} to {now}</b> ({stats}) "
                    f'<a href="{url(self.run_id)}">Watch live</a></p>')
            body, notes = head, []
            for label, text in self.items:
                part = f"<p><b>{label}:</b> {text}</p>"
                if len(body) + len(part) > NOTE_CHARS:
                    notes.append(body)
                    body = head
                body += part
            notes.append(body)
            for n in notes:
                hubspot.add_note(self.obj, self.hs_id, n)
            self.items, self.since = [], now
        from .watch import url
        status = "Finished" if self.done else "Running"
        hubspot.set_run_progress(self.obj, self.hs_id, f"{status}, {now}: {stats}." + ("" if self.done else f" Now: {self.now}")
                                 + f" Watch live: {url(self.run_id)}")

    def skip_backlog(self):
        """Count what has happened so far without posting it, for a feed attached to a run already reported."""
        self.read()
        self.items = []

    def run(self, stop=None):
        """Post every minute until the run ends or stop is set."""
        stop = stop or threading.Event()
        while True:
            self.read()
            try:
                self.post()
            except Exception as e:
                print(f"feed {self.run_id}: couldn't post: {type(e).__name__}: {str(e)[:200]}", flush=True)
            if self.done or stop.is_set():
                if stop.is_set() and not self.done:  # the run stopped without a final event
                    self.read()
                    self.post()
                return
            stop.wait(EVERY)
