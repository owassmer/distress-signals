# Distress Signals

Distress Signals is an AI sales prospecting agent. It finds companies whose revenue depends on an upcoming event, estimates how much is at stake, and identifies whom to contact. With HubSpot connected, each qualified opportunity becomes a proposed deal with a sourced memo and draft outreach.

[Explore the interactive demo](https://owassmer.github.io/distress-signals/)

## How it works

**Prospecting.** A Claude agent can start with a company, an event, or a custom brief. In an open search, it discovers both the events and the companies by following leads through filings, regulatory notices, public contracts, payment records, and the open web.

**Qualification.** The agent explains how an event could affect a revenue stream and chooses the scenarios and formulas. The software runs the calculations, checks quotations against stored records when available, and keeps the source, calculation, or assumption behind each input.

**Follow-up.** From HubSpot, a team can request research, review a proposed deal, or ask the agent to revisit it when circumstances change. The agent can retrieve earlier work and recorded deal outcomes in later searches.

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

The research log stores reasoning summaries, messages and tool calls. A case refresh starts from the saved case and checks its sources again.

The [design specification](SPEC.md) describes the research tools, evidence checks and data model.
