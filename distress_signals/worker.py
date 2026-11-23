"""The worker lets HubSpot drive research. It polls HubSpot for run requests and runs them one at a time,
writes each run's status and results to the record that asked, proposes follow-on leads as companies,
re-delivers cases whose delivery failed, and reads conversation outcomes back.

  python -m distress_signals serve              the loop; the background service runs it
  python -m distress_signals install-worker     run it as a macOS background service (launchd)
  python -m distress_signals uninstall-worker
"""

import os
import plistlib
import shutil
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

from . import db, feed, hubspot, pipeline
from .util import OUT, ROOT, found_line, load_env

POLL = 60          # seconds between looks at HubSpot for requests
OUTCOMES = 300     # seconds between reading outcomes and re-delivering changed cases
LABEL = "com.distress-signals.worker"
PLIST = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
LOG = OUT / "serve.log"


def _log(msg):
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}", flush=True)


def _saved(r):
    """The cases a run saved or revised: [(ID, company ID, company name, stream, status)]."""
    con = db.connect()
    try:
        return [tuple(row) for oid in r["saved"] for row in con.execute(
            "SELECT o.id, o.company_id, c.name, o.stream_key, o.status FROM cases o "
            "JOIN companies c ON c.id=o.company_id WHERE o.id=?", (oid,))]
    finally:
        con.close()


def found(r):
    """The run's results, counted as everywhere else (util.found_line)."""
    return found_line([cid for _, cid, _, _, status in _saved(r) if status == "active"])


def summary(r):
    """The run's report for the record that asked: the analyst's summary, the cases, the leads."""
    from .watch import title_label
    saved = _saved(r)
    saved = sorted(saved, key=lambda x: (x[2].lower(), x[3] or ""))
    active = [x for x in saved if x[4] == "active"]
    lines = [r["summary"].strip()]
    if active:
        lines += ["", f"Found {found_line([x[1] for x in active])} (each exposure is a deal):"]
        lines += [f"- {name}: {title_label(stream)} ({oid})" for oid, _, name, stream, _ in active]
    if len(active) < len(saved):
        lines += ["", "Withdrawn:"] + [f"- {name}: {title_label(stream)} ({oid})" for oid, _, name, stream, st in saved if st != "active"]
    if r["leads"]:
        lines += ["", "Follow-on leads:"] + [f"- {x['company_or_event']}: {x['reason']}" for x in r["leads"]]
    s = r.get("stats") or {}
    lines += ["", f"Run {r['run_id']}: {s.get('num_turns')} turns, {round((s.get('duration_ms') or 0) / 60000)} minutes."]
    return "\n".join(lines)


def run_request(obj, hs_id, props):
    """Run one request and write its status and results back to the record."""
    load_env()  # picks up settings added to .env since the worker started, such as CLAUDE_CODE_OAUTH_TOKEN
    kind, note = props.get("distress_run"), (props.get("distress_run_note") or "").strip() or None
    hubspot.mark_running(obj, hs_id)
    stop, feeder = threading.Event(), []

    def on_start(rid):  # the run has its ID: show it, and start the live feed on the record
        hubspot.set_run_id(obj, hs_id, rid)
        t = threading.Thread(target=feed.Feed(rid, obj, hs_id).run, args=(stop,), daemon=True)
        t.start()
        feeder.append(t)

    try:
        if obj == "deals":
            oid = props.get("distress_opportunity_id")
            if kind != "refresh" or not oid:
                raise ValueError("This deal isn't a distress-signals case, so there is nothing to refresh.")
            r = pipeline.research("refresh", oid, case_id=oid, question=note, on_start=on_start)
        elif kind == "research":
            r = pipeline.research("company", props.get("name"), question=note, on_start=on_start)
        elif kind == "event":
            if not note:
                raise ValueError("'Research the event in the note' needs the event described in Distress run note.")
            r = pipeline.research("event", note, on_start=on_start)
        elif kind == "mandate":
            r = pipeline.research("mandate", question=note, on_start=on_start)
        elif kind == "brief":
            if not note:
                raise ValueError("'Run the note as a brief' needs the brief in Distress run note.")
            r = pipeline.research("brief", note, brief=note, on_start=on_start)
        else:
            raise ValueError(f"Unknown request {kind!r}.")
        hubspot.mark_finished(obj, hs_id, "done", summary(r), (r.get("stats") or {}).get("total_cost_usd"))
        n = hubspot.propose_leads(r["leads"], r["run_id"])
        _log(f"{obj} {hs_id}: {kind} run {r['run_id']} done; {found(r)}, {n} lead(s) proposed")
    except Exception as e:
        _log(f"{obj} {hs_id}: {kind} failed: {type(e).__name__}: {str(e)[:500]}")
        traceback.print_exc()
        try:
            hubspot.mark_finished(obj, hs_id, "failed", f"{type(e).__name__}: {str(e)[:3000]}")
        except Exception:
            traceback.print_exc()
    finally:
        stop.set()
        for t in feeder:
            t.join(timeout=120)


def tick(state):
    """One pass: read outcomes and re-deliver (every OUTCOMES seconds), then start the oldest request if no
    run is going, or mark waiting requests queued."""
    busy = state.get("run") is not None and state["run"].is_alive()
    if not busy:
        state["current"] = None
    if time.time() - state.get("outcomes_at", 0) >= OUTCOMES:
        hubspot.pull_outcomes(quiet=True)
        if not busy:  # during a run, the save tool delivers each case itself
            hubspot.push(quiet=True)
        state["outcomes_at"] = time.time()
    reqs = [r for r in hubspot.requests() if (r[0], r[1]) != state.get("current")]
    if busy:
        for obj, hs_id, props in reqs:
            if props.get("distress_run_status") != "queued" and hubspot.is_requested(obj, hs_id):
                hubspot.mark_queued(obj, hs_id)
        return
    for obj, hs_id, props in reqs:
        if not hubspot.is_requested(obj, hs_id):  # already taken; search hadn't caught up
            continue
        _log(f"{obj} {hs_id}: {props.get('distress_run')} requested ({props.get('name') or props.get('dealname')})")
        state["current"] = (obj, hs_id)
        state["run"] = threading.Thread(target=run_request, args=(obj, hs_id, props), daemon=True)
        state["run"].start()
        return


def serve():
    load_env()
    if not hubspot.configured():
        raise SystemExit("HUBSPOT_SERVICE_KEY is not set in .env")
    item = os.environ.get("CLAUDE_TOKEN_KEYCHAIN")
    _log(f"worker started, pid {os.getpid()}; polling HubSpot every {POLL}s; Claude sign-in: "
         + (f"keychain item '{item}', read before each run" if item else "CLAUDE_CODE_OAUTH_TOKEN from .env"
            if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") else "the claude CLI's own sign-in"))
    try:
        n = hubspot.recover_running()
        if n:
            _log(f"{n} run(s) left running by a previous worker marked failed")
    except Exception as e:
        _log(f"couldn't check for runs left running: {e}")
    state = {}
    while True:
        try:
            tick(state)
        except Exception as e:
            _log(f"poll failed: {type(e).__name__}: {str(e)[:300]}")
        time.sleep(POLL)


AGENTS = {LABEL: "serve", "com.distress-signals.watch": "watch"}  # launchd label: the command it runs


def install(only=None):
    """Run the worker and the watch page as launchd agents: they start at login, restart if they stop, and
    log to out/serve.log and out/watch.log."""
    claude = shutil.which("claude")
    if not claude:
        raise SystemExit("claude isn't on PATH")
    path = ":".join(dict.fromkeys([str(Path(claude).parent), str(Path(sys.executable).parent), "/opt/homebrew/bin",
                                   "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin"]))
    OUT.mkdir(exist_ok=True)
    domain = f"gui/{os.getuid()}"
    for label, command in AGENTS.items():
        if only and command != only:
            continue
        plist = PLIST.parent / f"{label}.plist"
        log = LOG if command == "serve" else OUT / "watch.log"
        spec = {"Label": label, "ProgramArguments": [sys.executable, "-m", "distress_signals", command],
                "WorkingDirectory": str(ROOT), "RunAtLoad": True, "KeepAlive": True,
                "StandardOutPath": str(log), "StandardErrorPath": str(log),
                "EnvironmentVariables": {"PATH": path, "HOME": str(Path.home()), "PYTHONUNBUFFERED": "1"}}
        _stop(domain, label)
        plist.parent.mkdir(parents=True, exist_ok=True)
        with open(plist, "wb") as f:
            plistlib.dump(spec, f)
        for attempt in range(5):  # launchd can take a moment to let go of a service it just stopped
            r = subprocess.run(["launchctl", "bootstrap", domain, str(plist)], capture_output=True, text=True)
            if r.returncode == 0:
                break
            time.sleep(2)
        else:
            raise SystemExit(f"launchctl bootstrap {label} failed: {r.stderr.strip()}")
        print(f"{label} running; it starts at login and restarts if it stops. Log: {log}")


def _stop(domain, label=LABEL):
    """Stop a service if it is loaded, and wait until launchd has let it go."""
    subprocess.run(["launchctl", "bootout", f"{domain}/{label}"], capture_output=True)
    for _ in range(20):
        if subprocess.run(["launchctl", "print", f"{domain}/{label}"], capture_output=True).returncode != 0:
            return
        time.sleep(0.5)


def uninstall():
    for label in AGENTS:
        _stop(f"gui/{os.getuid()}", label)
        (PLIST.parent / f"{label}.plist").unlink(missing_ok=True)
    print("worker and watch page stopped and removed")
