# The model

distress-signals finds the companies whose revenue depends on an upcoming event outside their control. For
each one it says how the event reaches the business, how much revenue is at stake in which outcomes, who
is responsible for it, and why the conversation is timely. An operator uses it to decide whom to contact
and why.

This file states the things the system reasons about and how they relate. `SPEC.md` states how the code
holds them. Section 9 says which parts the code holds today.

## 1. Terms

Each term has one meaning throughout the system. Where event-risk has the same thing, the term is
event-risk's (its `ONTOLOGY.md`, and `MATH.md` 1.1 to 1.3).

| Term | Meaning |
|---|---|
| Claim | A statement that will turn out true or false: what would make it true, when it is decided, and what record will show it |
| Event | The claim a case depends on, about something outside the companies' control |
| Side | One of an event's two sides. Yes is the claim turning out true. No is the claim turning out false |
| Way | One way an outcome can turn out, stated as a claim of its own. Sibling ways exclude one another and together cover their parent. A way can have ways of its own, by the same rule |
| Outcome | A side or a way |
| Company | The operator of the business that earns the revenue |
| Revenue stream | The business activity whose revenue a case is about |
| Concentration | The share of a company's revenue or volume that comes through one customer, payer, program, carrier or contract, as its records report it today. It establishes the scale of a dependency |
| Dependency | The mechanism through which the event affects the stream: the contract, customer, program, facility or channel |
| Period | The span over which a case counts revenue |
| Input | A figure a case's formula uses, at its value in the baseline. It is observed in words, observed by query or assumed, and it opens to its basis |
| Baseline | The stream's revenue over the period: the formula at the inputs' values |
| Shortfall | Baseline revenue less the stream's revenue in an outcome |
| Case | One company, one event, one revenue stream: the dependency, the formula, the inputs, their values in each outcome that changes the revenue, the person responsible, and why to reach out now |
| Estimate | A case as it stood on a date, with the claim, inputs and evidence as they stood then. Every saved revision of a case is an estimate |
| Conversation outcome | What the operator records on a deal after contacting the company. Everywhere else, "outcome" means an outcome of an event |

## 2. The event

An event is a claim that will turn out true or false. Each of its ways is a claim too, stated the same way.

| Field | Meaning |
|---|---|
| `key` | The claim's identity. It stays the same when the claim is revised |
| `title` | The claim in a few words |
| `text` | The claim in full: what would make it true |
| `kind` | `scheduled_decision`: a decision scheduled on `window_end`. `hitting`: true where it happens at any time through `window_end`, counted from `window_start` where one is stated and otherwise from the day it is first saved. `level_at_date`: a level on `window_end` |
| `window_start`, `window_end` | The window. `window_end` is the date the claim is decided on or by |
| `resolution_source` | The record that will show how it turned out: its publisher, and the document, docket or data it will appear in |
| `ways` | The ways it can turn out. On an event they are given by side: `yes_ways` and `no_ways` |

An event also states `description`, who or what decides it and how; `timing`, the steps between now and
`window_end`, each with its date and how firm that date is; and `evidence`, the passages that establish
what the event is, when it is decided, and what bears on how it turns out.

**Sides.** Yes and No divide the possibilities by the truth of the claim. Which side costs a company
revenue is that company's case to state. The No side of a `hitting` event holds the event happening after
the window.

**Ways.** The analyst gives an outcome ways where what follows for a company differs between them: how far
a decision goes, which form a filing takes, who is selected, how a party responds. Every figure a case
gives belongs to one outcome, so two figures for an outcome are two ways of it. A proceeding with several
steps is stated this way: the outcomes of a later step are ways of the earlier step's outcome, each with
its own date and the record that will show it.

**Outcomes and keys.** An event's outcomes are its two sides and all its ways. The sides' keys are `yes`
and `no`. Each way's key is unique within the event. A key is the outcome's identity: it stays the same
when the outcome's wording is revised, and a finer distinction is drawn by giving the outcome ways.

**One statement, one place.** An event is stated once. Every case that depends on it refers to that
statement, so companies that depend on the same decision share the event and its outcomes, and a revision
reaches all of them. One company's case can turn on the Yes side while another's turns on a way of the No
side.

**Who states what.** That sibling ways exclude one another and together cover their parent is the
analyst's statement, made from the records it read. Code holds the keys, confirms that every outcome a case
gives values for is an outcome of its event, and keeps those outcomes when the event is revised.

**Illustration.** An agency funds a program year to year. The event: "The agency renews the program by
June 30", a `scheduled_decision`. Its Yes side has three ways. `renewed-in-full`: renewed at this year's
funding or more. `cut-under-half`: renewed at half of this year's funding or more, and less than all of it.
`cut-half-or-more`: renewed at less than half. Its No side has no ways. The resolution source is the
agency's notice of the decision.

## 3. The case

A case is one company, one event and one revenue stream. It identifies the business that earns the
revenue, the activity exposed and the dependency. It states the period, the model, when the company would
feel the effect, the person responsible for the affected business or its financial exposure, why it makes
sense to reach out now, and an opening that person could answer from their knowledge of the business.

**The model.** A case's model has three parts.

| Part | What it is |
|---|---|
| The formula | The stream's revenue over the period, as an expression over input names |
| The inputs | Each figure the formula uses, at its value in the baseline, with its basis |
| The values by outcome | For each outcome of the event that changes the revenue, the inputs that take another value there, each with what that value rests on |

**The baseline and the shortfall.** The baseline is the formula at the inputs' values. An outcome's revenue
is the formula with that outcome's values in place of the baseline's. The shortfall in an outcome is the
baseline less the revenue there.

**Ways.** An outcome's values hold in its ways, and a way gives the values that differ further.

**The condition.** What has to happen for the company's revenue to be affected is the outcomes the case
gives values for. It is read from the event, in the event's words.

**Concentration, dependency, shortfall.** A reported concentration establishes the scale of a dependency
and says where to look. The dependency says how an outcome reaches the stream. The shortfall is what the
model gives in an outcome: how the event changes what the business earns during the period.

**Illustration.** A contractor earns 100 a year under the program above, and nothing else depends on the
agency: that is the concentration and the dependency. The formula is `program_revenue * share_kept`. The
inputs are `program_revenue`, 100, from the contractor's annual report, and `share_kept`, 1, assumed. The
case gives `share_kept` three other values: 0 in `no`, 0.7 in `cut-under-half`, and 0.4 in
`cut-half-or-more`, each resting on the agency's budget documents. The baseline is 100. The shortfall is
100 where the program is not renewed, 30 where it is cut by less than half, and 60 where it is cut by half
or more.

## 4. The calculation

Write `f` for the formula, `x` for the inputs' values, and `x_o` for those values with outcome `o`'s in
place. The baseline is `B = f(x)`, the revenue in `o` is `R_o = f(x_o)`, and the shortfall is `B − R_o`.
For a way, `x_o` holds the values of each outcome it lies within, outermost first, and then its own. Code
evaluates it; the analyst states the formula and the values.

**Basis.** Every figure opens to what it rests on.

| An input is | It opens to | Who writes the value |
|---|---|---|
| Observed in words | The passage that states it, checked word for word against the stored record | The analyst |
| Observed by query | One cell of a saved result: the result, the column, and the columns that pick out the row | The store, which reads the cell |
| Assumed | The reasoning, and the passages and saved results it rests on | The analyst |

A value given in an outcome is assumed, and opens the same way. A rate measured for one company is
observed; taking it to hold for another is an assumption that rests on it.

**Timing within the period.** Revenue is what the company recognizes within the period, work or sales made
up within it included. Revenue recognized after the period is deferred past it, and the lost revenue is the
shortfall less that deferral. Cash collected later than it otherwise would be is a timing effect, reported
apart. Each has its own formula over the same inputs. `SPEC.md` section 7 lists each result.

## 5. The analyst's tools

Each action is designed through what the analyst can express and what it learns in return. The operator's
case and the analyst's working view read the same statements.

| The analyst | It learns in return |
|---|---|
| States an event, or revises one | The outcomes as saved, and each case that gives values for them |
| States a case | The condition read from the event, the baseline, each outcome's revenue and shortfall, and the result of each basis check |
| Calculates a draft | The same figures, with nothing saved |
| Looks up an event | The claim and its ways, and each case's dated estimate: its formula, its baseline, and its values, revenue and shortfall by outcome |
| Changes one value of a saved case | Each figure before and after |
| Gives an outcome ways | Each case on the event that gives values for that outcome |

## 6. Time

**Three dates.** The event's window says when the outcome is decided. The case's period says when the
revenue is counted. The case's `why_now_date` is the date that makes the conversation timely. Each serves
its own purpose.

**Why now.** Between today and the decision the outcome is open, and the company can still act on it. The
case says what in that time makes the conversation timely: a step in the event's `timing`, a filing, a
contract coming up for renewal.

**Runs.** A run is one session of the analyst under an assignment: the mandate, a brief, a company, an
event, or a saved case to refresh. The inquiry begins wherever the evidence reveals a dependency. The
assignment is the whole of what the analyst is given; a run that reaches a saved
event or case through its own research finds it and revises it under its key.

**Estimates.** Each save writes a revision with its date and its run. The revision holds the event, the
case, the inputs and their bases as they stood, so an estimate refers to the claim, the values and the
evidence of its date. A case's revisions, in order, are its estimates.

## 7. Settlement

**The event settles.** The resolution record shows the outcome that happened. It is recorded on the event:
the key of the deepest outcome the record establishes, the date, and the passage that shows it.

**The values settle.** A value assumed in the outcome that happened is set against what the records later
show it to have been. A value first read by a query is read again by the same query over the same searches.

**The revenue settles.** Where the company's own reporting carries the stream on one line for the period, a
later report sets that line against the revenue the estimate gave for the outcome that happened.

## 8. The seam with event-risk

The two systems share the claim. A portfolio watches it; a company case depends on it. Either analyst can
discover or refine it. event-risk says how likely each outcome is, values a holding in it, and evaluates
protection against it.

**What crosses.** The event and its ways, each with its claim, kind, window and resolution source, and the
evidence, each passage with the record it is quoted from. With it, each case's dated estimate: its date,
formula, inputs with their bases, values by outcome, and the baseline, revenue and shortfall in each
outcome. `show EVENT` and `show CASE` print them, and the operator carries them across. A reader can
reconstruct each figure from what the analyst stated.

**The same words on both sides.**

| Here | In event-risk |
|---|---|
| A claim's `title`, `text`, `kind`, `window_start`, `window_end`, `resolution_source` | The same fields of a proposition's definition, with `comparison` `occurs` |
| A way in `yes_ways` or `no_ways` | A proposition, and a `way` link to it from the event's proposition, with `side` yes or no |
| A way of a way | A proposition, and a `way` link to it from that way |
| An outcome with no ways | A world |
| An outcome's values hold in its ways | A consequence on an outcome gives its shock to the ways under it |
| The passages of event evidence quoted from one record | One `evidence` link to that source, with the passages |

**Three measures, each with its own meaning.** A revenue shortfall is measured against the case's baseline,
over the case's period. A holding's gain or loss is measured against its value today, and takes a valuation
of the holding in that outcome. A contract pays according to its own terms. event-risk sets a contract's
proceeds, after its cost, against the shortfall in the same outcome and on comparable dates.

**Statements each system makes about the shared claim.**

| Statement | Made by |
|---|---|
| The formula, the inputs, their values by outcome, and the shortfall in each outcome, with the estimate's date | distress-signals |
| The probability of each outcome | event-risk |
| What an outcome does to a holding's value | event-risk |
| What a market asks, and what differs between its rules and the outcome | event-risk |
| What a contract pays in each outcome, set against the shortfall there | event-risk |

**Two baselines.** event-risk names one world of an event as the baseline its prices are measured from. A
case's baseline here is its formula at its inputs' values, and each case has its own.

## 9. Build

| Part | In code |
|---|---|
| The event and its ways as claims, each with a kind, a window and a resolution source (section 2) | Built |
| A case's model: one formula, the inputs with their bases, and their values by outcome (sections 3 and 4) | Built |
| Estimates kept by date, with the event, inputs and bases as they stood (section 6) | Built |
| `show EVENT`: the claim, its ways, and each case's dated estimate by outcome (section 8) | Built |
| The event, its ways and each case's estimate written in event-risk's terms (section 8) | 1 |
| What the analyst learns on changing one value and on giving an outcome ways (section 5) | 2 |
| Settlement (section 7) | Later |

Parts 1 and 2 are built in that order. The whole is then tried on runs on new subjects, unrelated to one
another and to anything it was built against.
