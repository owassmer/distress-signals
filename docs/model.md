# The model

distress-signals finds the companies whose revenue depends on an upcoming event outside their control. For
each one it says how much revenue is at stake under each outcome of that event, who is responsible for it,
and why the conversation is timely. An operator uses it to decide whom to contact and why.

This file states the things the system reasons about and how they relate. `SPEC.md` states how the code
holds them. Section 8 says which parts the code holds today.

## 1. Terms

Each term has one meaning throughout the system.

| Term | Meaning |
|---|---|
| Event | A claim about something outside the companies' control that turns out true or false on or by a date |
| Side | One of an event's two sides. Yes is the event turning out true. No is the event turning out false |
| Way | One way a side can turn out, stated as a claim of its own. A side's ways exclude one another and together make up the side. A way can have ways of its own, by the same rule |
| Outcome | A side or a way. An outcome holds every way within it |
| Company | The operator of the business whose revenue is at stake |
| Revenue stream | The part of a company's revenue a case is about |
| Concentration | The share of a company's revenue or volume that comes through one customer, payer, program, carrier or contract, as its records report it today |
| Dependency | Why the company earns less under an outcome: the contract, customer, program, facility or channel through which the outcome reaches the stream |
| Period | The span over which a case counts revenue |
| Baseline | The stream's revenue over the period under the outcomes the case leaves unnamed |
| Shortfall | Baseline revenue less the stream's revenue under an outcome: the revenue at stake under that outcome |
| Case | One company, one event, one revenue stream: the shortfall under each outcome the case names, the person responsible, and why to reach out now |
| Input | A figure a case's calculation uses. It is reported, derived or assumed |
| Estimate | A case as it stood on a date. Every saved revision of a case is an estimate |
| Settlement | What the records show afterwards: the outcome that happened, the value each assumed input took, and the revenue the company reported |
| Conversation outcome | What the operator records on a deal after contacting the company. Everywhere else, "outcome" means an outcome of an event |

## 2. The event

An event is stated as a claim that will turn out true or false.

| Field | Meaning |
|---|---|
| `key` | The event's identity. It stays the same when the event is revised |
| `title` | The claim in a few words |
| `text` | The claim in full: what would make the event true |
| `description` | Who or what decides it, and how |
| `kind` | `scheduled_decision`: a decision scheduled on `window_end`. `hitting`: true where it happens at any time through `window_end`, counted from `window_start` where one is stated and otherwise from the day the event is first saved. `level_at_date`: a level on `window_end` |
| `window_start`, `window_end` | The window. `window_end` is the date the event is decided on or by |
| `timing` | The steps between now and `window_end`, each with its date and how firm that date is |
| `resolution_source` | The record that will show how the event turned out: its publisher, and the document, docket or data it will appear in |
| `yes_ways`, `no_ways` | The ways of each side |
| `evidence` | Passages that establish what the event is, when it is decided, and what bears on how it turns out |

**Sides.** Yes and No say whether the claim turned out true. Which side costs a company revenue is that
company's case to state. A side with no ways is one outcome: everything that makes the claim true, or
everything that makes it false. The No side of a `hitting` event holds the event happening after the
window.

**Ways.** The analyst gives a side ways where what follows for a company differs between them: how far a
decision goes, which form a filing takes, who is selected. Each way has a `key`, a `title` and a `text`
that says what would make it true. It has its own `window_end` and `resolution_source` where those differ
from its parent's. A proceeding with several steps is stated this way: the outcomes of a later step are
ways of the earlier step's outcome, each with the later date.

**Outcomes and keys.** An event's outcomes are its two sides and all its ways. The sides' keys are `yes`
and `no`. Each way's key is unique within the event. A key is the outcome's identity: it stays the same
when the outcome's wording is revised, and a finer distinction is drawn by giving the outcome ways.

**One event, several companies.** Companies that depend on the same decision share the event and its
outcomes. Each case names the outcomes that change its own company's revenue, so one company's case can
turn on the Yes side while another's turns on a way of the No side.

**Who states what.** That a side's ways exclude one another and together make up the side is the analyst's
statement, made from the records it read. Code holds the keys, confirms that every outcome a case names is
an outcome of its event, and keeps every outcome a saved case names when the event is revised.

**Illustration.** An airline's lenders have organized and its bonds trade far below par. The event:
"The airline files a Chapter 11 petition or stops scheduled passenger service by December 31 of next
year", of kind `hitting`. Its Yes side has two ways. `keeps-flying`: it reorganizes in Chapter 11 and still
carries passengers on December 31. `stops-flying`: it has stopped scheduled passenger service by December 31.
Its No side is left whole. The resolution source is the bankruptcy court's docket for the petition and the
transportation statistics that report the airline's passengers each month.

## 3. The case

A case is one company, one event and one revenue stream. It states the dependency, the period, the
calculation, when the company would feel the effect, the person responsible for the affected business or
its financial exposure, why it makes sense to reach out now, and an opening that person could answer from
their knowledge of the business.

**What a case says about outcomes.** A case names the outcomes under which the stream's revenue differs and
gives the revenue under each. A figure given for an outcome holds for every way within it, and a way given
its own figure takes that one. The outcomes the case leaves unnamed are its baseline. At least one outcome
is always left to the baseline, because a shortfall is the difference between the revenue under two
outcomes.

**The condition.** What has to happen for the company's revenue to be affected is the outcomes the case
names. It is read from the event, in the event's words.

**Concentration, dependency, shortfall.** Concentration is a fact about today, and it says where to look.
The dependency says how an outcome reaches the stream. The shortfall is what the calculation gives under
that outcome.

**Illustration.** The airline carries 30% of an airport's passengers: that is the concentration. Under the
airport's airline agreement, the airline's fees are spread over the carriers that remain, so an outcome
reaches the airport through parking, rental cars and concessions, which earn 100 a year, and through the
month of charges, 2, that a carrier leaves unpaid when it stops: that is the dependency. The case names two
outcomes of the event above. Under `keeps-flying` the airline flies 30% less at this airport and other
carriers replace half of the passengers it drops: the shortfall is 100 × 0.30 × 0.30 × (1 − 0.5) = 4.5.
Under `stops-flying` the airline carries no one and other carriers replace half: the shortfall is
100 × 0.30 × (1 − 0.5) + 2 = 17. The No side is the baseline.

## 4. The calculation

The stream's revenue over the period is one formula over the case's inputs. Code evaluates it; the analyst
states the formula and the inputs.

**Provenance.** Every input is reported, derived or assumed. A reported input cites the passage that states
it, checked word for word against the stored record. A derived input cites the workspace result that
computes it from records the run read. An assumed input carries the analyst's reasoning.

**Inputs the event sets.** Some inputs take a different value in each outcome: how much of its flying the
airline keeps, whether a month of its charges goes unpaid. Such an input has its baseline value and a value
under each outcome the case names.

**Inputs with a range.** An assumed input that plays out at the company after the outcome, such as the
share of dropped passengers other carriers replace, carries a low and a high beside its value. The analyst
states them so that the value the input takes falls between them four times in five.

**The figures.** Under each outcome the case names, the revenue is the formula at the inputs' values there.
Its low and high are the least and the greatest the formula gives across the inputs' lows and highs. The
shortfall is the baseline's revenue less the outcome's. In the illustration, with replacement between 0 and
0.5, the shortfall under `stops-flying` is 17, and between 17 and 32.

**Where a distinction goes.** A distinction is a way of the event when the parties to the event settle it
and a record will show it: how far a decision goes, which form a filing takes. It is a range on an input
when it plays out at the company afterwards: how much traffic other carriers replace, how many customers
defer an order. Where an input's range depends on how an outcome comes about, the outcome takes ways, and
the input takes a value and a range under each.

**In symbols.** Write `x_o` for the values outcome `o` sets and `θ` for the inputs with a range. The
stream's revenue under `o` is `R_o = f(x_o, θ)`, and the shortfall is `S_o = R_baseline − R_o`. With a
probability `p_o` for each outcome that has no ways, the expected shortfall is the sum over those outcomes
of `p_o × S_o`. The formula, `x_o` and `θ` are stated here. `p_o` is event-risk's (section 7).

**Timing within the period.** Revenue is what the company recognizes within the period, work or sales made
up within it included. Revenue recognized after the period is deferred past it, and the lost revenue is the
shortfall less that deferral. Cash collected later than it otherwise would be is a timing effect, reported
apart. `SPEC.md` section 7 lists each result.

## 5. Time

**Three dates.** The event's window says when the outcome is decided. The case's period says when the
revenue is counted. The case's `why_now_date` is the date that makes the conversation timely.

**Why now.** Between today and the decision the outcome is open, and the company can still act on it. The
case says what in that time makes the conversation timely: a step in the event's `timing`, a filing, a
contract coming up for renewal.

**Runs.** A run is one session of the analyst under an assignment: the mandate, a brief, a company, an
event, or a saved case to refresh. The mandate runs each day. The assignment is the whole of what the
analyst is given; a run that reaches a saved event or case through its own research finds it and revises
it under its key.

**Estimates.** Each save writes a revision with its date and its run. A case's revisions, in order, are
its estimates: what the system held on each date, with the inputs, their provenance and the figures as
they stood.

## 6. Settlement and scoring

**The event settles.** When the resolution source shows the decision, or the window ends, the outcome that
happened is recorded on the event: the key of the deepest outcome the record establishes, the date, and
the passage that shows it.

**The inputs settle.** Each assumed input names the record that will show the value it took. An input
first estimated by a workspace query over a series is measured again by the same query over the later
records.

**The revenue settles.** The case names the line in the company's own reporting that carries the stream,
and the filing that will report it for the period.

**The score.** Each estimate is scored as it stood on its date, under the outcome that happened. For each
assumed input, the score is the realized value against the stated value, and whether it fell within the
range. For the case, it is the reported line against the revenue the estimate gave. Over many settled
estimates, the share of realized values that fell within their ranges is set against four in five, and the
average signed error shows whether the estimates run high or low.

## 7. The seam with event-risk

event-risk answers the questions on the other side of the outcome: how likely each outcome is, what it does
to a portfolio, and what a contract that pays on it would cover. The two systems meet at the event and its
outcomes.

**What crosses.** `show EVENT` prints it: the event's claim, kind, window and resolution source; its ways
by side; its evidence, each passage with the record it is quoted from; and for each case on the event, the
company, the stream, the period, the baseline's outcomes and the shortfall under each outcome the case
names. The operator carries it to event-risk.

**The same words on both sides.**

| Here | In event-risk |
|---|---|
| An event's `title`, `text`, `kind`, `window_start`, `window_end`, `resolution_source` | The same fields of a proposition's definition |
| A way in `yes_ways` or `no_ways` | A `way` link from the event's proposition, with `side` yes or no |
| A way of a way | A `way` link from that way |
| An outcome with no ways | A world |
| A passage of event evidence, with its record | An `evidence` link: the passage and its source |

**Who states what.**

| Statement | Stated by |
|---|---|
| The event, its window and its resolution source | distress-signals |
| The ways where a company's revenue differs | distress-signals |
| The shortfall under each outcome, with its range and the basis of every input | distress-signals |
| The probability of each outcome | event-risk |
| Ways within an outcome where prices, or what a contract pays, differ | event-risk |
| What a market asks, and what differs between its rules and the outcome | event-risk |
| What a contract pays under each outcome, set against the shortfall there | event-risk |

**Refinement.** A way event-risk draws lies within an outcome stated here, and the case's figure for that
outcome holds within it. Where a shortfall differs between two such ways, the event here takes those ways
and the case gives each its figure.

**Two baselines.** event-risk names one world of an event as the baseline its prices are measured from. A
case's baseline here is the revenue under the outcomes that case leaves unnamed, and each case has its own.

**Each system scores its own statement.** The outcome that happened scores event-risk's probability. The
realized inputs and the reported revenue under that outcome score the shortfall stated here.

## 8. Build

| Part | In code |
|---|---|
| The event as a claim with a kind, a window, a resolution source, sides and ways (section 2) | Built |
| A case naming outcomes, with the baseline as the outcomes left unnamed and the condition read from the event (section 3) | Built |
| What crosses the seam, printed by `show EVENT` (section 7) | Built |
| One formula for a case, inputs the event sets, inputs with a range, low and high figures (section 4) | Next |
| Settlement and scoring (section 6) | After the calculation |
| The mandate run each day (section 5) | After settlement |

Today a case gives its figures as versions. Each version names its outcome and carries its own revenue
formula, and versions under one outcome differ in what plays out at the company. The one formula with
ranges takes their place.
