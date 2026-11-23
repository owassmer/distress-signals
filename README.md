# Distress Signals

Distress Signals is an autonomous prospecting agent for brokers and advisers who help businesses hedge against revenue losses. It finds companies where a significant share of sales depends on a specific outcome outside their control. The agent traces that exposure through public records, estimates the potential shortfall, and creates a proposed deal in HubSpot with the evidence, a suggested contact, and a draft message.

[Explore the interactive demo](https://owassmer.github.io/distress-signals/)

## How it works

The agent follows leads through [company filings, contracts, regulatory notices, payment records](docs/data-sources.md), and the open web. For each prospect, it identifies the part of the business at risk and explains how and when sales could fall.

Each event is stated as a claim that will turn out true or false, with the ways it can turn out and what follows from them. Claude builds a revenue model for each case, and Python works out every combination of outcomes and the shortfall in each. The deal memo shows which figures are reported, calculated, or assumed and links to the supporting records.

## Setting it up

You need Python 3.12 or later, the [Claude Code](https://docs.anthropic.com/en/docs/claude-code) command
line tool signed in to a Claude account, and a HubSpot account with a private app key.

1. Install:

   ```bash
   python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
   .venv/bin/python -m playwright install chromium --only-shell
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

## Using HubSpot

On a company record, use **Distress run** to search for new prospects, investigate that company, or find businesses exposed to a particular event. Each opportunity appears as a **Proposed** deal with the research memo and draft outreach.

Use **Refresh this case** on a deal to check whether the exposure has changed. Record the result of a conversation in **Distress outcome** so the agent can refer to it the next time it researches that company. [HubSpot reference](SPEC.md#9-hubspot)

## From a terminal

```bash
.venv/bin/python -m distress_signals research                    # find new prospects
.venv/bin/python -m distress_signals research --company NAME     # investigate one company
.venv/bin/python -m distress_signals research --event TEXT       # find companies exposed to an event
.venv/bin/python -m distress_signals refresh OPP_ID              # check a saved case again
.venv/bin/python -m distress_signals push-hubspot                # send changed cases to HubSpot
.venv/bin/python -m distress_signals export-guide RUN_ID         # one run as a visual guide, for sharing
.venv/bin/python -m distress_signals show ID_OR_NAME             # a saved case, company or event
```

Watch the agent work at http://localhost:8790 while the services are running. [Command reference](SPEC.md#10-commands)

## Configuration

| File | Holds |
|---|---|
| `config/mandate.md` | Criteria for identifying prospects |
| `config/offering.local.md` | Your firm’s products and services (optional, kept local) |
| `config/exclude.yaml`, `config/exclude.local.yaml` | Companies to leave out |
| `config/ledgers.yaml` | The state and local government payment ledgers |

[Design specification and evidence checks](SPEC.md)
