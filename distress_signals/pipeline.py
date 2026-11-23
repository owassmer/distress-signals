"""Research runs: launch the researcher on a seed with its tools, and record what it produced."""

import json
import os
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from . import agent, db, llm, sources, tools, transcript
from .util import OUT, ROOT, claude_token, now_iso

TIMEOUT = 3 * 3600  # seconds per research run; the mandate tells the analyst there is time to research thoroughly


def _brief_text(brief):
    if not brief:
        return None
    p = Path(brief)
    if not p.is_absolute():
        p = ROOT / brief if (ROOT / brief).exists() else Path.cwd() / brief
    return p.read_text() if p.exists() else brief


def _mcp_config(rid, log, principal=None):
    """The researcher's one MCP server. Its environment is given in full: Claude passes stdio servers
    only a minimal environment of its own. A company run names its principal company. The HubSpot key goes
    as the launcher has it, so a run started with an empty key delivers nothing."""
    env = {"PYTHONPATH": str(ROOT), "DISTRESS_DB": str(db.path().resolve()), "DISTRESS_RUN_ID": rid,
           "DISTRESS_RUN_LOG": str(log), **({"DISTRESS_RUN_PRINCIPAL": principal} if principal else {}),
           **({"HUBSPOT_SERVICE_KEY": os.environ["HUBSPOT_SERVICE_KEY"]} if "HUBSPOT_SERVICE_KEY" in os.environ else {})}
    cfg = {"mcpServers": {"ds": {"command": sys.executable, "args": ["-m", "distress_signals.mcp_server"], "env": env}}}
    f = tempfile.NamedTemporaryFile("w", suffix=".json", prefix=f"{rid}_", delete=False)
    json.dump(cfg, f)
    f.close()
    return f.name


def claude_env():
    """What the claude process is given beyond the launcher's own environment. Claude Code cuts an MCP tool's
    description at 4,096 characters unless CLAUDE_CODE_MAX_MCP_DESCRIPTION_LENGTH says otherwise, and
    search_records carries the whole catalog of sources, about 1,000 characters each."""
    return {"MCP_TOOL_TIMEOUT": "600000", "MCP_TIMEOUT": "60000", "CLAUDE_CODE_MAX_MCP_DESCRIPTION_LENGTH": "200000",
            **({"CLAUDE_CODE_OAUTH_TOKEN": token} if (token := claude_token()) else {})}


def research(seed_kind="mandate", seed=None, brief=None, question=None, case_id=None, lead_id=None, model=None,
             timeout=TIMEOUT, on_start=None):
    """One researcher run. seed_kind is mandate (the standing mandate), company, event, source or brief; a
    refresh attaches the saved case, a lead the saved lead. on_start(run_id) is called once the run has its
    ID. Returns {run_id, saved, summary, leads, stats, log, transcript, assignment}."""
    con = db.connect()
    try:
        case = tools.lookup(case_id) if case_id else None
        lead = db.get_lead(con, lead_id) if lead_id else None
        if lead_id and not lead:
            raise SystemExit(f"no lead {lead_id!r}")
        if case:
            seed_kind, seed = "refresh", case_id
        elif lead:
            seed_kind, seed = "lead", lead["subject"]
        rid = db.make_id("run_", seed_kind, seed, now_iso())
        if on_start:
            try:
                on_start(rid)
            except Exception as e:
                print(f"{rid}: on_start failed: {e}", flush=True)
        log = OUT / "runs" / f"{rid}.log"
        events = log.with_suffix(".jsonl")
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(f"{now_iso()} run {rid}: {seed_kind} {seed}\n")
        model = model or llm.MODEL
        principal = case["company"]["name"] if case else (seed if seed_kind == "company" else None)
        system, text = agent.system(), agent.prompt(seed_kind, seed, _brief_text(brief), question, case, lead)
        failed = sources.observe()  # coverage and jurisdictions, read from the data once a day
        if failed:
            print(f"{rid}: coverage not read for {', '.join(sorted(failed))}; the catalog keeps the last reading", flush=True)
        order = sources.order(rid)  # the MCP server draws the same order from the same run ID
        db.start_run(con, rid, seed_kind, seed or "", question, f"{model} ({llm.EFFORT})", str(log), principal, system, text,
                     series_order=order)
        assignment = log.with_suffix(".assignment.md")
        assignment.write_text(f"# {rid}: {seed_kind} {seed or ''}\n\nModel `{model}`, effort `{llm.EFFORT}`. Cases limited to: "
                              f"{principal or 'no company'}.\n\n"
                              f"Series, in the order its catalog lists them: {', '.join(order)}.\n\n"
                              f"## System prompt\n\n{system}\n\n## Prompt\n\n{text}\n")
        print(f"{rid}: {seed_kind}{' ' + repr(seed) if seed else ''}; transcript in {events.relative_to(ROOT)} "
              f"(readable: python -m distress_signals transcript {rid})", flush=True)
        cfg = _mcp_config(rid, log, principal)
        try:
            out, stats = llm.ask(system, text, agent.RESULT, events, model=model, timeout=timeout, tools=agent.TOOLS,
                                 mcp_config=cfg, env=claude_env())
        except Exception as e:
            db.finish_run(con, rid, db.saved_by_run(con, rid), error=str(e)[:2000])
            raise
        finally:
            Path(cfg).unlink(missing_ok=True)
            if events.exists():
                transcript.render(events, title=f"{rid}: {seed_kind} {seed or ''}".strip(), assignment=assignment)
        saved = db.saved_by_run(con, rid)
        db.finish_run(con, rid, saved, out["summary"], stats=stats | {"reported": out["case_ids"]})
        db.add_leads(con, rid, out["follow_on_leads"])
        if lead:
            db.set_lead(con, lead_id, "researched")
        return {"run_id": rid, "saved": saved, "summary": out["summary"], "leads": out["follow_on_leads"],
                "stats": stats, "log": str(log), "transcript": str(events.with_suffix(".md")),
                "assignment": str(assignment)}
    finally:
        con.close()


def research_many(jobs, workers=3, **kw):
    """Independent runs in parallel: jobs [(seed_kind, seed)]."""
    results = []
    with ThreadPoolExecutor(workers) as ex:
        futs = {ex.submit(research, kind, seed, **kw): seed for kind, seed in jobs}
        for fut in as_completed(futs):
            try:
                results.append(fut.result())
            except Exception as e:
                print(f"{futs[fut]}: run failed: {str(e)[:300]}", flush=True)
    return results
