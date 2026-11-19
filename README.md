# distress-signals

distress-signals finds companies whose revenue depends on something about to happen that they don't
control: a large contract coming up for renewal, a regulator's decision, a rule or law taking effect, a
court ruling, a funding deadline. For each one it works out how much revenue is at stake and why, and
names the person at the company to talk to, with a short opening message.

It's built for people whose business is helping companies with exposure like that, such as advisers,
brokers, insurers and lenders, who need to know whom to call, about what, and why now.

**[See an example run →](https://owassmer.github.io/distress-signals/)** One full research session: what it
found, each case in full with the figures behind it, and the research itself, which you can replay step by
step.

## What you get for each company

- **The event:** what has to happen, and when.
- **The dependency:** why the company earns less if it happens. This is the contract, customer, product,
  program or channel the revenue runs through.
- **The exposure:** how much revenue is at stake over a stated period, and what share of the business that
  is. Every figure is traced to its source or shown as an assumption, with the reasoning.
- **The person:** who is responsible for the affected business or its financial exposure, and the public
  source that names them.
- **Why now:** the decision, deadline or development that makes the conversation timely.
- **An opening:** a short message the person could answer from their own knowledge of the business.

## How it works

**A research agent does the investigating.** It's a Claude model working with a set of research tools.
It searches current public records, reads what it finds, follows leads, and decides which companies are
worth contacting. The records are:
- the full text of SEC filings, where companies describe the events they expect to affect them;
- the Federal Register, the government's dated calendar of rules and notices;
- federal contract awards;
- payment records from 19 state and local governments;
- the open web.

It covers private companies as well as public ones, because government payment records reach any company
a government pays.

**Code keeps the numbers honest.** The agent can't just assert a figure. Every number in a case is one of
three things:
- quoted from a source, and checked word for word against the stored document;
- calculated from records it read;
- labeled as an assumption, with its reasoning.

The arithmetic is done by code, not by the model.

**HubSpot is where you work.** Each case arrives in HubSpot as a deal, linked to the company and the
person, with a full memo. You start research from HubSpot too, by setting a field on a company record.
While a run goes, you can watch it live. As conversations happen, you record outcomes on the deal, and
they flow back into the research.

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

   The services start whenever you log in to the Mac, and restart if they stop.

## Using it

Open a company record in HubSpot and set **Distress run** to one of these:

| Option | What it does |
|---|---|
| Research this company | Looks for an upcoming event that this company's revenue depends on |
| Research the event in the note | Starts from an event you describe in **Distress run note**, and finds the companies exposed to it |
| Run the open search | Looks anywhere for companies with this kind of exposure, following the standing instructions in `config/mandate.md` |
| Run the note as a brief | Answers a question you write in **Distress run note** |

On a case's deal, **Refresh this case** checks the case again against current sources.

Within a minute, **Distress run status** changes to Running. Then:
- Each case appears as a deal in the **Proposed** stage, with the company, the person and the memo.
- The record's activity timeline gets a running commentary, with a link to a live view of the research.
- When the run ends, the record shows a summary and what the run cost.
- Follow-on leads appear as companies marked **Proposed lead**. Set **Research this company** on one to
  look into it.

Moving a deal out of Proposed, or closing it with an outcome, is your review. Outcomes, recorded in the
**Distress outcome** field on the deal, come back into the research.

A run usually takes half an hour or so, and costs roughly $10 to $20 in model usage.

## From a terminal

Everything above can also be run directly:

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

The researcher makes the judgment calls: which events matter, which companies are exposed, how to model
the revenue at stake, and who to contact. The code supplies records, checks quotations and figures, does
the arithmetic, and keeps everything traceable.

The researcher starts each run from current records rather than from what it remembers, and nothing from
earlier runs is handed to it as a starting list. Every run keeps a complete transcript of the model's
reasoning and every tool call, so any case can be traced back to how it was found.

`SPEC.md` describes the design in full.
