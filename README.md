# Distress Signals

Distress Signals searches public records for companies with material revenue exposure to an upcoming event. In an open search, the agent discovers both the event and the companies. It estimates the revenue exposed and identifies whom an adviser, broker, insurer or lender could contact about it.

In the published search, the agent investigated federal hemp restrictions and found two exposures in RYTHM’s filings. The restrictions could interrupt product sales and satisfy one condition for a Green Thumb affiliate to repurchase licensed brands. The licensing fees depend on whether the affiliate exercises that right, which brands it buys and when. [View the company cases](https://owassmer.github.io/distress-signals/#found)

## How it works

**Research.** A Claude agent searches public records, follows leads and selects cases that fit the brief. Sources include SEC filings, Federal Register notices and rules, federal contract awards, participating state and local payment ledgers, and the open web. Government payment records can identify private suppliers as well as public companies.

**Evidence and calculation.** Each model input has a source passage, a calculation from stored records or an explicit assumption. The system checks quoted passages against stored document text when available. When it has only a web excerpt, it records the quotation as unchecked. The agent specifies the scenarios and formulas, and the software calculates the revenue under each scenario.

**HubSpot.** With the integration configured, the operator can request research from a company record and review cases as proposed deals. Each deal includes a memo and links to the company and contact when available. The integration records deal outcomes for subsequent research.

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
