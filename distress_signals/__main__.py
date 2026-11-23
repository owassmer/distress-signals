"""python -m distress_signals <command>

  research                  run the researcher on the standing mandate
  research --company NAME [--company NAME ...] | --event TEXT | --source URL | --lead ID | --brief FILE
           [--question TEXT] [--workers N] [--model MODEL] [--timeout MINUTES]
                            run it on the subject or brief the operator selects
  refresh CASE_ID [--question TEXT]
                            research a saved case again and save what changed
  leads                     follow-on leads not yet researched
  show ID_OR_QUERY          a saved case, company or proposition
  transcript RUN_ID         render a run's transcript (reasoning summaries, tool calls, results) to
                            out/runs/RUN_ID.md; works while the run is going
  export CASE_ID            print one case whole, as JSON
  ledgers [--verify]        list payment ledgers; --verify re-tests mapped invoice dates on live data
  hubspot-setup             create the "Distress signals" deal and contact properties in HubSpot
  push-hubspot [CASE_ID ...] [--dry-run]
                            deliver cases to HubSpot: the ones named, or every active case and any
                            delivered case that changed
  pull-outcomes             read each deal's stage, outcome and note back onto its case
  serve                     the worker: run research requested in HubSpot and write results back
  install-worker            run the worker as a macOS background service; uninstall-worker removes it
  feed RUN_ID companies|deals RECORD_ID [--from-now]
                            stream a run that is going into a HubSpot record's timeline
  watch                     the live page for watching runs, at http://localhost:8790/
  export-guide RUN_ID [--out FOLDER]
                            a visual guide to one run (index.html) and its full research trail (run.html), for
                            sharing, e.g. on GitHub Pages
  export-run RUN_ID [--public] [--out FILE]
                            one run as a single self-contained page with a replay button; --public leaves
                            out links into HubSpot
"""

import argparse
import json
from pathlib import Path

from . import briefs, db, feed, hubspot, pipeline, showcase, tools, transcript, watch, worker
from .util import OUT
from .sources import ledgers as L
from .util import load_env


def cmd_ledgers(a):
    for led in L.load():
        line = f"{led.id:<22} {led.payer:<44} invoice_date={'yes (' + led.invoice_date_meaning + ')' if led.invoice_date else 'no'}"
        if a.verify and led.invoice_date:
            v = L.verify_invoice_dates(led)
            line += (f"  verify: {'PASS' if v['pass'] else 'FAIL'} n={v['n']} median={v.get('median')}d "
                     f"p90={v.get('p90', 0):.0f}d zero={v.get('share_zero')} neg={v.get('share_negative')}")
        print(line, flush=True)


def report(r):
    s = r["stats"]
    print(f"\n{r['run_id']}: {worker.found(r)}; {len(r['saved'])} cases saved or revised, {s.get('num_turns')} turns, "
          f"{(s.get('duration_ms') or 0) / 60000:.0f} min")
    print(r["summary"])
    print(f"  transcript: {r['transcript']} (assignment: {r['assignment']})")
    for oid in r["saved"]:
        o = tools.lookup(oid)
        res, cur = o["results"], o["case"]["currency"]
        share = f" ({res['max_share']:.1%} of {res['denominator']['label']})" if res.get("max_share") is not None else ""
        print(f"  {oid} [{o['status']}] {o['company']['name']}: {o['case']['stream']} | revenue at stake "
              f"{cur} {res['max_shortfall']:,.0f}{share} | priority {o['case']['reasons']['priority']}")
    for x in r["leads"]:
        print(f"  lead: {x['company_or_event']}: {x['reason'][:140]}")


def main():
    load_env()
    ap = argparse.ArgumentParser(prog="distress_signals")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("research")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--company", action="append")
    g.add_argument("--event")
    g.add_argument("--source")
    g.add_argument("--lead")
    s.add_argument("--brief")
    s.add_argument("--question")
    s.add_argument("--workers", type=int, default=3)
    s.add_argument("--model")
    s.add_argument("--timeout", type=int, default=180, help="minutes per run")
    s = sub.add_parser("refresh"); s.add_argument("case_id"); s.add_argument("--question"); s.add_argument("--model")
    s.add_argument("--timeout", type=int, default=60, help="minutes")
    sub.add_parser("leads")
    s = sub.add_parser("show"); s.add_argument("id_or_query")
    s = sub.add_parser("transcript"); s.add_argument("run_id")
    s = sub.add_parser("export"); s.add_argument("case_id")
    s = sub.add_parser("ledgers"); s.add_argument("--verify", action="store_true")
    sub.add_parser("hubspot-setup")
    s = sub.add_parser("push-hubspot"); s.add_argument("case_ids", nargs="*"); s.add_argument("--dry-run", action="store_true")
    sub.add_parser("pull-outcomes")
    sub.add_parser("serve")
    sub.add_parser("install-worker")
    sub.add_parser("uninstall-worker")
    s = sub.add_parser("feed"); s.add_argument("run_id"); s.add_argument("object", choices=["companies", "deals"]); s.add_argument("record_id")
    s.add_argument("--from-now", action="store_true", help="don't post what happened before attaching")
    sub.add_parser("watch")
    s = sub.add_parser("export-guide"); s.add_argument("run_id"); s.add_argument("--out")
    s = sub.add_parser("export-run"); s.add_argument("run_id"); s.add_argument("--public", action="store_true"); s.add_argument("--out")
    a = ap.parse_args()

    if a.cmd == "research":
        kw = {"brief": a.brief, "question": a.question, "model": a.model, "timeout": a.timeout * 60}
        if a.company and len(a.company) > 1:
            for r in pipeline.research_many([("company", c) for c in a.company], workers=a.workers, **kw):
                report(r)
            return
        if a.company:
            r = pipeline.research("company", a.company[0], **kw)
        elif a.event:
            r = pipeline.research("event", a.event, **kw)
        elif a.source:
            r = pipeline.research("source", a.source, **kw)
        elif a.lead:
            r = pipeline.research("lead", a.lead, lead_id=a.lead, **kw)
        elif a.brief:
            r = pipeline.research("brief", a.brief, **kw)
        else:
            r = pipeline.research("mandate", **kw)
        report(r)
    elif a.cmd == "refresh":
        report(pipeline.research("refresh", a.case_id, case_id=a.case_id, question=a.question,
                                 model=a.model, timeout=a.timeout * 60))
    elif a.cmd == "leads":
        con = db.connect()
        for x in db.open_leads(con):
            print(f"{x['id']}  {x['subject']}: {(x['reason'] or '')[:150]}")
    elif a.cmd == "show":
        print(json.dumps(tools.lookup(a.id_or_query), indent=1, ensure_ascii=False, default=str))
    elif a.cmd == "transcript":
        events = OUT / "runs" / f"{a.run_id}.jsonl"
        if not events.exists():
            raise SystemExit(f"no transcript at {events}; runs before the stream-json runner have none")
        print(transcript.render(events, assignment=events.with_suffix(".assignment.md")))
    elif a.cmd == "export":
        found = briefs.cases(db.connect(), [a.case_id])
        if not found:
            raise SystemExit(f"no saved case {a.case_id}")
        print(json.dumps(briefs.export(found[0]), indent=1, ensure_ascii=False, default=str))
    elif a.cmd == "ledgers":
        cmd_ledgers(a)
    elif a.cmd == "hubspot-setup":
        hubspot.setup()
    elif a.cmd == "push-hubspot":
        hubspot.push(a.case_ids, a.dry_run)
    elif a.cmd == "pull-outcomes":
        hubspot.pull_outcomes()
    elif a.cmd == "serve":
        worker.serve()
    elif a.cmd == "install-worker":
        worker.install()
    elif a.cmd == "uninstall-worker":
        worker.uninstall()
    elif a.cmd == "feed":
        f = feed.Feed(a.run_id, a.object, a.record_id)
        if a.from_now:
            f.skip_backlog()
        f.run()
    elif a.cmd == "watch":
        watch.serve()
    elif a.cmd == "export-guide":
        print(showcase.export_guide(a.run_id, Path(a.out) if a.out else OUT / "guide" / a.run_id))
    elif a.cmd == "export-run":
        page = watch.export_html(a.run_id, public=a.public)
        out = Path(a.out) if a.out else OUT / "runs" / f"{a.run_id}{'-public' if a.public else ''}.html"
        out.write_text(page)
        print(out)


if __name__ == "__main__":
    main()
