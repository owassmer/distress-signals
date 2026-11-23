"""What the analyst is told. The system prompt is config/system.md, the same in every session but for its
role and today's date. A session's prompt is its assignment: an open search, or a
company, an event, a source, a brief or a lead the operator selects, or a saved case to refresh."""

import json
import os

from .util import CONFIG, today

TOOL_NAMES = ("search_records", "read_record", "compute", "record_finding", "lookup", "calculate_exposure",
              "save_case")
TOOLS = ["WebSearch", "WebFetch", *(f"mcp__ds__{n}" for n in TOOL_NAMES)]

# The practitioner the system prompt's first sentence names, in the order the work runs: what can happen to the
# revenue and how the event is defined, then what each outcome would cost. AGENT_ROLE gives another, for a trial.
ROLE = "an underwriter and a forensic accountant"

RESULT = {"type": "object", "required": ["case_ids", "summary", "follow_on_leads"], "properties": {
    "case_ids": {"type": "array", "items": {"type": "string"}, "description": "The cases saved or revised in this session"},
    "summary": {"type": "string", "description": "What the session established and what that rests on: each case with "
                                                 "its company, its event and the revenue in its outcomes; for a group "
                                                 "of companies or an open search, which companies and dependencies were "
                                                 "examined and in which records; and what was left to judgment and "
                                                 "how it was decided"},
    "follow_on_leads": {"type": "array", "description": "Companies or events outside this assignment that the research "
                                                        "found reason to examine",
                        "items": {
        "type": "object", "required": ["company_or_event", "reason", "source_urls"],
        "properties": {"company_or_event": {"type": "string"},
                       "reason": {"type": "string", "description": "What the research found that gives it an "
                                                                   "assignment of its own"},
                       "source_urls": {"type": "array", "items": {"type": "string"}},
                       "company": {"type": "object", "required": ["name"],
                                   "description": "The company the lead concerns, when it concerns one company",
                                   "properties": {"name": {"type": "string"},
                                                  "domain": {"type": "string", "description": "Its website domain"}}}}}}}}


def system(role=None):
    """The system prompt: config/system.md with the role its first sentence names and today's date. It names no
    company, event or series: whatever the context names, the analyst reaches for. A company the operator
    excludes is refused where a case or finding is saved."""
    text = (CONFIG / "system.md").read_text().strip()
    return text.replace("{role}", role or os.environ.get("AGENT_ROLE") or ROLE).replace("{today}", today().isoformat())


ASSIGNMENTS = {
    "mandate": "The search is open. Find where a material share of a company's revenue depends on an event that is "
               "not yet settled and is outside the company's control, and establish each case.",
    "company": "Establish the events {seed}'s revenue depends on, and each case. Cases saved in this session are for "
               "{seed}, and the store refuses others; what you learn about other companies is kept as findings and "
               "follow-on leads.",
    "event": "Establish which companies' revenue depends on this event, and each case: {seed}",
    "source": "Start from this document, and establish the cases it leads to: {seed}",
    "brief": "{seed}",
}


def prompt(seed_kind, seed=None, brief=None, question=None, case=None, lead=None):
    """The run's assignment, as the operator gave it."""
    if case:
        parts = [f"Refresh the saved case {case['case']['case_id']}: bring it up to date against the records as they "
                 "now stand. read_record(fresh=true) reads a source again. Revise what the records no longer support "
                 "and save the case with the same case_id, or, where the revenue no longer depends on the event as "
                 "the case states, withdraw it with record_finding. The case as saved:\n\n"
                 + json.dumps(case, indent=1, default=str)]
    elif lead:
        parts = [f"An earlier session noted this as worth an assignment of its own: {lead['subject']}. Its reason: "
                 f"{lead.get('reason')}. Its sources: {', '.join(lead.get('urls') or []) or 'none'}."]
    elif seed_kind == "brief":
        parts = [brief.strip()]
    else:
        parts = [ASSIGNMENTS[seed_kind].format(seed=seed)]
        if brief:
            parts.append(brief.strip())
    if question:
        parts.append("The operator's note: " + question.strip())
    return "\n\n".join(parts)
