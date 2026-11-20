# Distress Signals

Distress Signals is an autonomous sales prospecting agent. It discovers upcoming events, identifies companies with revenue at risk, and uses public records to estimate what each company could lose. It creates a proposed deal in HubSpot with the supporting research, a suggested contact, and a draft message.

[Explore the interactive demo](https://owassmer.github.io/distress-signals/)

## How it works

**Discovery.** A Claude agent chooses which events and companies to investigate. It follows leads through filings, regulatory notices, contracts, payment records, and the open web to identify the part of a company’s business an event could affect.

**Revenue analysis.** The agent chooses how to model the affected revenue stream. The software runs the calculations and records which inputs were reported, derived, or assumed. A reviewer can trace a reported figure back to its source passage.

**Follow-up.** A sales team can launch research from a HubSpot company record, request an update from an existing deal, and record the outcome. The agent can consult that history when it researches the company again.

## Setting it up

You need Python 3.12 or later, the [Claude Code](https://docs.anthropic.com/en/docs/claude-code) command
line tool signed in to a Claude account, and a HubSpot account with a private app key.

1. Install:

   ```bash
   python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
   cp .env.example .env
   ```

2. Fill in `.env`:
   - `SEC_USER_AGENT`: your name and email address. The SEC requires this for automated access.
   - `HUBSPOT_SERVICE_KEY`: your HubSpot key. `.env.example` lists the permissions it needs.
   - `CLAUDE_TOKEN_KEYCHAIN` or `CLAUDE_CODE_OAUTH_TOKEN`: how background runs sign in to Claude. Name a
     macOS keychain item that holds your Claude token, or give the token itself.

3. Prepare HubSpot and start the background services:

   ```bash
   .venv/bin/python -m distress_signals hubspot-setup     # adds the fields and a "Proposed" deal stage
   .venv/bin/python -m distress_signals install-worker    # runs requested research and serves the live view
   ```

   The installed services start at macOS login and restart if they stop.

## Request research in HubSpot

The **Distress run** field on a company record accepts the following requests. **Run the open search** uses the standing mandate across companies.

| Option | What it does |
|---|---|
| Research this company | Looks for an upcoming event that this company's revenue depends on |
| Research the event in the note | Starts from an event you describe in **Distress run note**, and finds the companies exposed to it |
| Run the open search | Discovers events and exposed companies under the standing mandate in `config/mandate.md` |
| Run the note as a brief | Answers a question you write in **Distress run note** |

On a case's deal, **Refresh this case** checks the case again against current sources.

The worker marks the request **Running** when research starts.

- Each case appears as a deal in the **Proposed** stage, with the company, the person and the memo.
- The activity timeline links to the live research log.
- The completed run adds a summary to the requesting record.
- Follow-on leads appear as companies marked **Proposed lead**. Set **Research this company** on one to
  investigate the lead.

Record the result in the deal’s **Distress outcome** field. Subsequent company lookups include the recorded outcome.

## From a terminal

```bash
.venv/bin/python -m distress_signals research                    # the open search
.venv/bin/python -m distress_signals research --company NAME     # one company
.venv/bin/python -m distress_signals research --event TEXT       # an event and the companies exposed to it
.venv/bin/python -m distress_signals research --brief FILE       # a question or brief, from a file
.venv/bin/python -m distress_signals refresh OPP_ID              # check a saved case again
.venv/bin/python -m distress_signals push-hubspot                # send changed cases to HubSpot
.venv/bin/python -m distress_signals export-guide RUN_ID         # one run as a visual guide, for sharing
.venv/bin/python -m distress_signals show ID_OR_NAME             # a saved case, company or event
```

The live view of runs is at http://localhost:8790 while the services are running.

## Configuration

| File | Holds |
|---|---|
| `config/mandate.md` | What the researcher is asked to do, in plain words |
| `config/offering.local.md` | A description of what you offer, if you want the researcher to know it (not committed) |
| `config/briefs/` | Saved briefs, each used only when you choose it |
| `config/exclude.yaml`, `config/exclude.local.yaml` | Companies to leave out |
| `config/ledgers.yaml` | The state and local government payment ledgers |

## Design

The research log preserves the agent’s searches, source reads, calculations, and saved cases. The software checks quoted passages against stored documents and marks quotations drawn from web excerpts alone as unchecked.

The [design specification](SPEC.md) describes the research tools, evidence checks and data model.
