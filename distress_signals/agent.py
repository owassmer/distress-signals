"""What the researcher is told. The system prompt is the mandate, the operator's offering when one is
supplied (config/offering.local.md), and what each tool does. A run's prompt is its assignment: the
standing mandate, or a subject or brief the operator selects, or a saved case to refresh."""

import json

from .util import CONFIG, today

TOOL_NAMES = ("search_records", "read_record", "compute", "record_finding", "lookup", "calculate_exposure",
              "save_opportunity")
TOOLS = ["WebSearch", "WebFetch", *(f"mcp__ds__{n}" for n in TOOL_NAMES)]

RESULT = {"type": "object", "required": ["opportunity_ids", "summary", "follow_on_leads"], "properties": {
    "opportunity_ids": {"type": "array", "items": {"type": "string"}, "description": "The cases saved"},
    "summary": {"type": "string", "description": "Who is worth contacting, what revenue depends on the outcome, and "
                                                 "why the conversation is timely; and what was set aside, and why"},
    "follow_on_leads": {"type": "array", "items": {
        "type": "object", "required": ["company_or_event", "reason", "source_urls"],
        "properties": {"company_or_event": {"type": "string"},
                       "reason": {"type": "string", "description": "Why it is worth researching"},
                       "source_urls": {"type": "array", "items": {"type": "string"}},
                       "company": {"type": "object", "required": ["name"],
                                   "description": "The company the lead concerns, when it concerns one company",
                                   "properties": {"name": {"type": "string"},
                                                  "domain": {"type": "string", "description": "Its website domain"}}}}}}}}

TOOLS_TEXT = """\
## Tools

Today is {today}. What you know about events ends at your training cutoff, before today; the records these tools
reach are current.

- `search_records` searches the public sources listed in the tool description. `read_record` reads and stores
  a source record, web page or PDF so its passages can be cited. WebSearch searches the open web and WebFetch
  reads a page.
- `compute` runs SQL over the records read with `read_record` and the findings and results recorded, for totals,
  intermediate figures and dates.
- `record_finding` records facts, inferences, conclusions, open questions and dropped leads, and can withdraw a
  case from the queue.
- `lookup` looks up a case, company, event, finding or workspace result by its ID, or a company or event by its
  name. For a company, it also shows the company's deals in the operator's CRM, with their stages and outcomes.
- `calculate_exposure` calculates the revenue at stake without saving; `save_opportunity` saves a case and puts it
  in front of the operator in their CRM.

The final answer lists the cases saved, a summary, and follow-on leads."""


def system():
    """Excluded companies aren't named here: whatever the context names, the analyst reaches for. Saving a
    case or finding for one is refused, with the reason."""
    offering = CONFIG / "offering.local.md"
    head = ("## The operator\n\n" + offering.read_text().strip() + "\n\n") if offering.exists() else ""
    return (head + "## Mandate\n\n" + (CONFIG / "mandate.md").read_text().strip() + "\n\n"
            + TOOLS_TEXT.format(today=today().isoformat()))


ASSIGNMENTS = {
    "mandate": "Carry out the mandate.",
    "company": "Research {seed}. The cases from this research are for {seed}; what you learn about other companies is "
               "recorded as findings and follow-on leads.",
    "event": "Research this event and the companies whose revenue depends on it: {seed}",
    "source": "Start from this source: {seed}",
    "brief": "{seed}",
}


def prompt(seed_kind, seed=None, brief=None, question=None, case=None, lead=None):
    """The run's assignment, as the operator gave it."""
    if case:
        parts = [f"Refresh the saved case {case['opportunity']['opportunity_id']}: read its sources again with "
                 "read_record(fresh=true), revise what the evidence no longer supports, and save it with the same "
                 "opportunity_id, or withdraw it with record_finding if there is no longer a reason to contact the "
                 "company. The case as saved:\n\n" + json.dumps(case, indent=1, default=str)]
    elif lead:
        parts = [f"Research this follow-on lead: {lead['subject']}. Reason it was noted: {lead.get('reason')}. Sources: "
                 f"{', '.join(lead.get('urls') or []) or 'none'}."]
    elif seed_kind == "brief":
        parts = [brief.strip()]
    else:
        parts = [ASSIGNMENTS[seed_kind].format(seed=seed)]
        if brief:
            parts.append(brief.strip())
    if question:
        parts.append("Operator's note: " + question.strip())
    return "\n\n".join(parts)
