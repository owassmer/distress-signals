# The model

distress-signals finds the companies whose revenue depends on an upcoming event outside their control. For
each one it says how the event reaches the business, how much revenue is at stake under which
circumstances, who is responsible for it, and why the conversation is timely. An operator uses it to decide
whom to contact and why.

This file states the things the system reasons about and how they relate. `SPEC.md` states how the code
holds them. Section 9 says which parts the code holds today.

## 1. Terms

Each term has one meaning throughout the system. Where event-risk has the same thing, the term is
event-risk's (its `ONTOLOGY.md`, and `MATH.md` 1.1, 1.2 and 1.9).

| Term | Meaning |
|---|---|
| Event | A proposition a case concerns: a claim about something outside the companies' control that turns out true or false on or by a date. Its meaning is what would make it true, when, and what record would establish it |
| Side | One of an event's two sides. Yes is the claim turning out true. No is the claim turning out false |
| Way | A proposition that refines an outcome: one way that outcome can turn out. Sibling ways exclude one another and together cover their parent. A way can have ways of its own, by the same rule |
| Outcome | A side or a way |
| World | An outcome left whole in the analysis at hand. A case's worlds are the outcomes it names |
| Joint world | One outcome of each of several events, taken together |
| Company | The operator of the business that earns the revenue |
| Revenue stream | The business activity whose revenue a case is about |
| Concentration | The share of a company's revenue or volume that comes through one customer, payer, program, carrier or contract, as its records report it today. It establishes the scale of a dependency |
| Dependency | The mechanism through which the event affects the stream: the contract, customer, program, facility or channel |
| Period | The span over which a case counts revenue |
| Baseline | The stream's revenue over the period under the reference conditions the case states: the comparison the shortfall is measured from |
| Shortfall | Baseline revenue less the stream's revenue in a world, signed. Zero means the two revenues coincide |
| Open | An outcome a case names no revenue for. The case says nothing about it |
| Case | One company, one event, one revenue stream: the dependency, the shortfall in each world the case names, the person responsible, and why to reach out now |
| Input | A figure a case's calculation uses. It is observed, derived or assumed, and it opens to its basis |
| Estimate | A case as it stood on its forecast date, with the claim, assumptions and evidence as they stood then. Every saved revision of a case is an estimate |
| Settlement | What the records show afterwards: the outcome that happened, the value each assumed input took, and the revenue the company reported |
| Conversation outcome | What the operator records on a deal after contacting the company. Everywhere else, "outcome" means an outcome of an event |

## 2. The event

An event is a proposition: a claim that will turn out true or false.

| Field | Meaning |
|---|---|
| `key` | The event's identity. It stays the same when the event is revised |
| `title` | The claim in a few words |
| `text` | The claim in full: what would make the event true |
| `description` | Who or what decides it, and how |
| `kind` | `scheduled_decision`: a decision scheduled on `window_end`. `hitting`: true where it happens at any time through `window_end`, counted from `window_start` where one is stated and otherwise from the day the event is first saved. `level_at_date`: a level on `window_end` |
| `window_start`, `window_end` | The window. `window_end` is the date the event is decided on or by |
| `timing` | The steps between now and `window_end`, each with its date and how firm that date is |
| `resolution_source` | The record that will establish how the event turned out: its publisher, and the document, docket or data it will appear in |
| `yes_ways`, `no_ways` | The ways of each side |
| `evidence` | Passages that establish what the event is, when it is decided, and what bears on how it turns out |

**Sides.** Yes and No divide the possibilities by the truth of the claim. Which side costs a company
revenue is that company's case to state. The No side of a `hitting` event holds the event happening after
the window.

**Ways.** A way is a proposition linked to the outcome it refines. It has a `key`, a `title` and a `text`
that says what would make it true, and its own `window_end` and `resolution_source` where those differ from
its parent's. The analyst gives an outcome ways where what follows for a company differs between them: how
far a decision goes, which form a filing takes, who is selected, how a party responds. A proceeding with
several steps is stated this way: the outcomes of a later step are ways of the earlier step's outcome, each
with the later date.

**What can be a way.** Knowing an outcome can settle one input to a case and leave the company's response
uncertain. Where a decision depends on whether a particular response occurs, that response is stated as a
proposition in its own right: a way of the outcome it follows. A measured quantity can do two things at
once: enter a case's calculation as an input, and decide a proposition about its level. An airline's
passengers at an airport is such a quantity: it is an input to the airport's revenue, and "it carries at
least 30% fewer passengers there next year" is a claim the same measurement decides.

**Outcomes and keys.** An event's outcomes are its two sides and all its ways. The sides' keys are `yes`
and `no`. Each way's key is unique within the event. A key is the outcome's identity: it stays the same
when the outcome's wording is revised, and a finer distinction is drawn by giving the outcome ways.

**One statement, one place.** An event is stated once. Every case that concerns it refers to that
statement, so companies that depend on the same decision share the event and its outcomes, and a revision
reaches all of them. Each case names the outcomes that matter to its own company, so one company's case can
turn on the Yes side while another's turns on a way of the No side.

**Who states what.** That sibling ways exclude one another and together cover their parent is the
analyst's statement, made from the records it read. Code holds the keys, confirms that every outcome a case
names is an outcome of its event, and keeps every outcome a saved case names when the event is revised.

**Illustration.** An airline's lenders have organized and its bonds trade far below par. The event:
"The airline files a Chapter 11 petition or stops scheduled passenger service by December 31 of next
year", of kind `hitting`. Its Yes side has two ways. `keeps-flying`: it reorganizes in Chapter 11 and still
carries passengers on December 31. `stops-flying`: it has stopped scheduled passenger service by December 31.
Its No side has no ways. The resolution source is the bankruptcy court's docket for the petition and the
transportation statistics that report the airline's passengers each month.

## 3. The case

A case is one company, one event and one revenue stream. It identifies the business that earns the
revenue, the activity exposed and the dependency. It states the period, the calculation, when the company
would feel the effect, the person responsible for the affected business or its financial exposure, why it
makes sense to reach out now, and an opening that person could answer from their knowledge of the business.

**The worlds a case names.** A case names the outcomes of its event it has analyzed and gives the stream's
revenue in each. Those outcomes are its worlds. They lie apart: each is a different outcome, and none lies
within another. A figure for a world is the figure for that outcome as a whole.

**The baseline.** The baseline is the stream's revenue over the period under the reference conditions the
case states: the comparison the analyst intends. Both the baseline and the revenue in a world are for the
same business activity, period, currency and accounting basis. Background conditions that affect the
baseline, such as another carrier's schedule or the year's traffic, enter both.

**The shortfall.** The shortfall in a world is the baseline's revenue less the revenue in that world,
signed. A world whose revenue equals the baseline's has a shortfall of zero, and the case says so by naming
that world with that revenue. An outcome the case names no revenue for is open.

**Refinement.** When an outcome a case names is given ways, the case's figure stays the figure for the
outcome as a whole. Each way has its own figure once the case names it. The operating relationship can
hold throughout an outcome while its ways have different shortfalls, because an input takes a different
value in each.

**The condition.** What has to happen for the company's revenue to be affected is the worlds whose revenue
differs from the baseline's. It is read from the event, in the event's words.

**Concentration, dependency, shortfall.** A reported concentration establishes the scale of a dependency
and says where to look. The dependency says how an outcome reaches the stream. The shortfall is what the
calculation gives in a world: how the event changes what the business earns during the period.

**Illustration.** The airline carries 30% of an airport's passengers: that is the concentration. Under the
airport's airline agreement, the airline's fees are spread over the carriers that remain, so an outcome
reaches the airport through parking, rental cars and concessions, which earn 100 a year, and through the
month of charges, 2, that a carrier leaves unpaid when it stops: that is the dependency. The baseline is
100, with the airline flying its current schedule and traffic at this year's level. The case names two
worlds of the event above. In `keeps-flying`, taking the airline to fly 30% less at this airport and other
carriers to replace half of the passengers it drops, the shortfall is 100 × 0.30 × 0.30 × (1 − 0.5) = 4.5.
In `stops-flying`, taking other carriers to replace half, it is 100 × 0.30 × (1 − 0.5) + 2 = 17. The No
side is open in this case.

## 4. The calculation

Write `R(w, z)` for the stream's revenue over the period in world `w`, given the operating circumstances
`z` it still depends on there. Write `B` for the baseline. The shortfall is `L = B − R(w, z)`. Code
evaluates it; the analyst states the formulas and the inputs.

**Basis.** Every input opens to what it rests on. An observed input opens to the record that establishes
it: the passage that states it, checked word for word against the stored record. A derived input opens to
its inputs and calculation: the workspace result that computes it from records the run read. An assumed
input opens to the reasoning and the evidence for applying it: the passages and workspace results the
assumption rests on, and why they apply here. A replacement rate measured after one carrier's exit is
derived; taking it to hold for another carrier is an assumption that rests on it.

**One statement, one place.** A quantity several cases use is stated once, on the event's outcome where the
outcome sets it, and each case's formula refers to it. A quantity particular to one company is stated in
that company's case.

**Four figures.** They are different things, and each is shown as what it is.

| Figure | What it is |
|---|---|
| Scenario figure | The shortfall at particular inputs: what the model gives under those assumptions |
| Sensitivity range | The shortfalls the model gives as inputs vary over stated ranges |
| Conditional mean | The mean shortfall in a world, taking account of how likely the remaining circumstances are |
| Predictive interval | Bounds stated with the probability that they cover the realized quantity |

**Judgments about the response.** Where evidence supports a judgment about how the business or the parties
around it are likely to respond in a world, the analyst states that judgment with its basis: a distribution
over the operating inputs that accounts for how they vary together. Evaluating the model over that
distribution gives a distribution of shortfalls, and from it the conditional mean and a predictive
interval. A case with scenario figures and sensitivity ranges stands on its own for the operator.

**The conditional mean, in symbols.** For a condition `C` of positive probability, partition `C` into
worlds, and let `W_C` be those with positive probability given `C`. With the information available on the
forecast date,

`E[L | C] = sum over w in W_C of P(w | C) × E[L | w]`

Taking `C` to be all possibilities gives the overall mean. The same relation holds when a world is refined
into its ways, so a change in the ways' relative likelihoods changes the mean shortfall given the event
while the event's probability stays fixed. `E[L | w]` is stated here. `P(w | C)` is event-risk's
(section 8).

**Timing within the period.** Revenue is what the company recognizes within the period, work or sales made
up within it included. Revenue recognized after the period is deferred past it, and the lost revenue is the
shortfall less that deferral. Cash collected later than it otherwise would be is a timing effect, reported
apart. `SPEC.md` section 7 lists each result.

## 5. The analyst's tools

Each action is designed through what the analyst can express and what it learns in return. The operator's
case and the analyst's working view read the same statements.

| The analyst | It learns in return |
|---|---|
| States an event, or revises one | The outcomes as saved, and each case that names them |
| Refines an outcome into ways | For every case on the event: the figures established for the finer outcomes, and the figures known only for their parent |
| States a case | The condition read from the event, each world's revenue and shortfall, the outcomes left open, and the basis checks |
| Revises an operating assumption | Its effect on the case: each world's figures before and after |
| Removes a statement | The current results without it, and what depended on it |
| Looks up an event | The proposition and its relations, and each case's dated estimate: its baseline, formulas, worlds and open outcomes |

## 6. Time

**Three dates.** The event's window says when the outcome is decided. The case's period says when the
revenue is counted. The case's `why_now_date` is the date that makes the conversation timely. Each serves
its own purpose.

**Why now.** Between today and the decision the outcome is open, and the company can still act on it. The
case says what in that time makes the conversation timely: a step in the event's `timing`, a filing, a
contract coming up for renewal.

**Runs.** A run is one session of the analyst under an assignment: the mandate, a brief, a company, an
event, or a saved case to refresh. The inquiry begins wherever the evidence reveals a dependency. The
mandate runs each day. The assignment is the whole of what the analyst is given; a run that reaches a saved
event or case through its own research finds it and revises it under its key.

**Estimates.** Each save writes a revision with its date and its run. The revision holds the event, the
case, the inputs and their bases as they stood, so an estimate refers to the claim, assumptions and
evidence of its forecast date. A case's revisions, in order, are its estimates.

## 7. Settlement and scoring

**The event settles.** The resolution record establishes the outcome that happened. It is recorded on the
event: the key of the deepest outcome the record establishes, the date, and the passage that shows it.

**The inputs settle.** Each assumed input names the record that will show the value it took. An input
first estimated by a workspace query over a series is measured again by the same query over the later
records.

**The revenue settles.** The case names the line in the company's own reporting that carries the stream,
and the filing that will report it for the period. A later report tests the estimate of revenue in the
world that happened, on the same revenue line and period.

**The baseline and attribution.** The shortfall is measured from the baseline, and the loss attributable to
the event depends on what the business would otherwise have earned. A report tests the baseline where the
baseline's reference conditions are the world that happened.

**The score.** Each estimate is scored as it stood on its forecast date, in the world that happened. For
each assumed input, the score is the realized value against the stated value, and whether it fell within a
stated predictive interval. For the case, it is the reported line against the revenue the estimate gave for
that world. Over many settled estimates, the share of realized values inside their intervals is set against
the intervals' stated probability, and the average signed error shows whether the estimates run high or low.

## 8. The seam with event-risk

The two systems share the proposition. A portfolio watches it; a company case concerns it. The same
proposition serves both decisions, and either analyst can discover or refine it. event-risk assesses how
likely each world is, values an exposure in it, and evaluates protection against it.

**What crosses.** The proposition and its relations: the claim, kind, window and resolution source, the
ways of each outcome, and the evidence, each passage with the record it is quoted from. With it, each
case's dated operating estimate: its forecast date, baseline and reference conditions, formulas, inputs
with their bases, and the revenue and shortfall in each world it names. `show EVENT` and `show CASE` print
them, and the operator carries them across. A reader can reconstruct each figure from what the analyst
stated, and see which judgments would change it.

**The same words on both sides.**

| Here | In event-risk |
|---|---|
| An event's `title`, `text`, `kind`, `window_start`, `window_end`, `resolution_source` | The same fields of a proposition's definition |
| A way in `yes_ways` or `no_ways` | A `way` link from the event's proposition, with `side` yes or no |
| A way of a way | A `way` link from that way |
| A world: an outcome left whole in the analysis at hand | A world |
| A passage of event evidence, with its record | An `evidence` link: the passage and its source |
| A measured quantity that is an input here and decides a proposition about its level | A `Metric`, and the proposition decided on it |

**Three measures, each with its own meaning.** A revenue shortfall is measured against the case's baseline,
over the case's period. A holding's gain or loss is measured against its value today, and takes a valuation
of the holding in that world. A contract pays according to its own terms. event-risk sets a contract's
proceeds, after its cost, against the operating shortfall in the same world and on comparable dates, and
where payment is still uncertain it reads the exposure that remains under each possible payoff.

**Statements each system makes about the shared proposition.**

| Statement | Made by |
|---|---|
| The operating estimate: the baseline, the relationship, the inputs, and the shortfall in each world, with its forecast date | distress-signals |
| A judgment about how the business responds in a world, with its basis | distress-signals |
| The probability of each world | event-risk |
| What a world does to a holding's value | event-risk |
| What a market asks, and what differs between its rules and the outcome | event-risk |
| What a contract pays in each world, set against the shortfall there | event-risk |

**Two baselines.** event-risk names one world of an event as the baseline its prices are measured from. A
case's baseline here is revenue under the reference conditions that case states, and each case has its own.

**Each system scores its own statement.** The resolution record establishes the outcome, and that scores
the event probability. The realized inputs and the reported revenue in that world score the operating
estimate.

## 9. Build

| Part | In code |
|---|---|
| The event as a proposition with a kind, a window, a resolution source, sides and ways (section 2) | Built |
| A case naming its worlds, with open outcomes and the condition read from the event (section 3) | Built |
| Estimates kept by forecast date, with the event, inputs and bases as they stood (section 6) | Built |
| `show EVENT`: the proposition, its relations, and each case's dated estimate by world (section 8) | Built |
| One formula for a case; an input's value in each world; scenarios within a world; assumptions that open to evidence; observed values read from results (section 4) | 1 |
| The event, its ways and each case's estimate written in event-risk's terms (section 8) | 2 |
| What the analyst learns on refining an outcome and on revising an assumption (section 5) | 3 |
| Judgments about the response as distributions, the conditional mean and predictive intervals (section 4) | Later |
| Settlement and scoring (section 7) | Later |
| The mandate run each day (section 6) | Later |

Parts 1 to 3 are built in that order. The whole is then tried on runs on new subjects, unrelated to one
another and to anything it was built against.

Today a case gives its figures as versions. Each version names its world and carries its own revenue
formula, and versions that name the same world are scenario figures under different operating assumptions.
An input is observed (`source`), derived, or assumed with its reasoning. Part 1 takes their place.
