# The model

distress-signals establishes how a company's revenue depends on an upcoming event outside its control. For
each company and event it says how the event reaches the business, what the revenue would be over a stated
period in each way the event can turn out, and who at the company is responsible for it. The figures are
used while the event is open: weighted by each outcome's probability, compared across companies, set against
protection that pays in an outcome, and put to the people who run the company's finances.

The analyst states a few kinds of thing: propositions, the links between them, and for each case one
formula, its inputs, and the values those inputs take given outcomes. Code computes every figure from
those statements. The terms and the construction are event-risk's wherever event-risk has the same thing
(its `ONTOLOGY.md`, and `MATH.md` 1.1 to 1.3 and 1.9), so a proposition stated here is the same
proposition there. `SPEC.md` states how the code holds all of this, and section 10 says which parts it
holds today.

## 1. Terms

| Term | Meaning |
|---|---|
| Proposition | A claim that will turn out true or false: what would make it true, when it is decided, and the record that will show it |
| Outcome | A proposition turning out true, or turning out false |
| Way | One way a proposition can turn out, stated as a proposition of its own. The ways beside one another exclude one another and together make up what they divide |
| Event | A proposition a case is about: something upcoming, outside the company's control |
| Company | The operator of the business that earns the revenue |
| Revenue stream | The business activity whose revenue a case is about |
| Period | The span over which a case counts revenue |
| Input | A figure a case's formula uses, at its value in the baseline, with its basis |
| Given value | The value an input takes given some propositions true and others false, with its basis |
| World | One of the cases a case's propositions divide what can happen into. In each world every one of those propositions is true or false |
| Baseline | The stream's revenue over the period: the formula at the inputs' values |
| Shortfall | Baseline revenue less the stream's revenue in a world |
| Condition | The worlds in which the revenue differs from the baseline, in the propositions' words |
| Case | One company, one revenue stream, and the event or events it depends on: the formula, the inputs, the given values, and the person responsible |
| Estimate | A case as it stood on a date. Every saved revision of a case is an estimate |
| Conversation outcome | What the operator records on a deal after contacting the company. Everywhere else, "outcome" means an outcome of a proposition |

## 2. Propositions

Every claim is a proposition: an event, a way of an event, and what a company or the parties around it do
once an outcome is known. They have one shape and are told apart by their links.

| Field | Meaning |
|---|---|
| `key` | The proposition's identity. It stays the same when the wording is revised |
| `title` | The claim in a few words |
| `text` | The claim in full: what would make it true |
| `kind` | `scheduled_decision`: a decision scheduled on `window_end`. `hitting`: true where it happens at any time through `window_end`, counted from `window_start` where one is stated and otherwise from the day it is first saved. `level_at_date`: a level on `window_end` |
| `window_start`, `window_end` | The window. `window_end` is the date the claim is decided on or by |
| `comparison` | `occurs` where the claim is true when something happens. Where it is about a level: one of `>`, `>=`, `<`, `<=`, `==` |
| `metric`, `unit`, `value`, `upper_comparison`, `upper_value` | For a claim about a level: the quantity, its unit, the threshold, and for a band its upper end |
| `resolution_source` | Where a record will show how it turned out, that record: its publisher, and the document, docket or data it will appear in |
| `description`, `timing` | Who or what decides it and how; the steps between now and `window_end`, each with its date and how firm that date is |
| `evidence` | Passages that establish what the claim is, when it is decided, and what bears on how it turns out |

**Two links.**

| Link | From, to | What it says |
|---|---|---|
| `way` | A proposition, to each way it can turn out. On an event the link names the side it divides, `yes` or `no` | The ways beside one another exclude one another and together make up the proposition, or the side of the event, they divide. A way can have ways of its own |
| `implies`, `implies_not` | A proposition, to another | Wherever the first is true, the second is true (`implies`) or false (`implies_not`). The outcomes the second adds to the first are a way of it beside the first |

**Level fields.** Two propositions on one metric, in one unit, with one resolution source, decided the
same way over the same window, are compared by their fields. Code reads from them that one implies the
other, and that two cannot both be true where the level is read once, on a date or at a decision. A way's
values lie within those of what it divides, and code reads where the ways beside one another overlap or
leave values in none of them.

**What gets a proposition.** A way divides an outcome for every case that reads it: how far a decision
goes, which form a filing takes, who is selected. What one company or its counterparties do once an
outcome is known is a proposition of its own, linked to that outcome with `implies` or `implies_not`. It
divides the outcome for the cases that read it, and the event's own ways stay as they are. Every figure a
case gives belongs to a world, so a second figure for an outcome is a second proposition.

**Keys.** A proposition is identified by its key, which is unique in the store. A case's given values and
a proposition's links name propositions by key.

**One statement, one place.** A proposition is stated once, and every case that reads it refers to that
statement. A proposition given in full takes the place of the saved one, its ways with it, and keeps every
proposition an active case reads and every proposition another is linked to: wording is revised under the
same key, and a finer distinction is drawn by giving a proposition ways or by linking another to it.

## 3. The case

A case is one company, one revenue stream, and the event or events the stream depends on. A case has one
event unless the analyst reads two events together on one stream. The company, the events and the stream's
key identify it, and its deal is named for them.

**The model.**

| Part | What it is |
|---|---|
| The formula | The stream's revenue over the period, as an expression over input names |
| The inputs | Each figure the formula uses: the name the formula refers to it by, a label that says in words what it is, its unit, its value over the period in the baseline, and its basis |
| The given values | Each says: given these propositions true and these false, this input takes this value, with what that value rests on |

A given value names the propositions it is given by key: `given` for those that are true, `given_not`
for those that are false. One condition can set several inputs.

**The rest of the case.** The dependency, the mechanism through which the events reach the stream. When an
outcome's effect reaches the revenue. The person responsible for the affected business or the company's
finances, with the record that names them. An opening to that person is written after the analysis, from
the saved case, and is no part of it.

**Two dates.** A proposition's window says when it is decided, and it is the event's own. The case's period
says when the revenue is counted. Where an event can happen at different times within its window, when it
happens changes how much of the period it reaches, and that is a difference between ways.

## 4. Worlds and figures

**The propositions a case reads** are its events and every proposition one of its given values names.
With each way so named come the ways beside it; and where what it divides is itself a way, that way and
the ways beside it, and so on up to the event.

**The case's worlds** are the combinations of those propositions being true or false that the links
allow. A way is true only where what it divides is true, and exactly one of the ways beside one another is
true there. Where A implies B, no world has A true and B false; where A implies not B, no world has both
true. Links are followed through propositions the case does not read.

So an outcome stays whole in a case until one of the case's given values names a way within it. It is
then its ways, and the way that holds the one named is its ways in turn. A way another case reads leaves
this case's worlds as they were.

**An input's value in a world.** A given value holds in the worlds where everything it is given is true
and everything it is given not is false. In each world an input takes the value of the one given value
that holds there, sets that input, and has its worlds strictly within those of every other given value
that holds there and sets it. An input no given value sets there stands at its own value.

**The figures.** Write `f` for the formula, `x` for the inputs' values, and `x_w` for their values in
world `w`. The baseline is `B = f(x)`. The revenue in `w` is `R_w = f(x_w)`, and the shortfall is
`B − R_w`. A world no given value holds in stands at the baseline. The condition is the worlds where
`R_w` differs from `B`. The revenue at stake is the largest shortfall, with its world.

**Sensitivity.** The store reports how far the shortfall moves with each stated figure, one figure at a
time. Each input's own value and each given value is moved up and down by a tenth of the largest value its
input takes anywhere in the case, the worlds' shortfalls are computed again, and the largest change in any
world is kept, with that world. Each figure comes back with which parts of a basis it has: read, read and
carried by an assumption, or assumed. An input that is zero everywhere has no row.

**Timing within the period.** Revenue is what the company recognizes within the period, work or sales made
up within it included. Revenue recognized after the period, the contribution, and cash collected late each
have their own formula over the same inputs, evaluated in each world the same way.

**Illustration.** An agency funds a program year to year. The event `program-renewal`: "The agency renews
the program by June 30." Its Yes side has three ways, bands on the program's funding as a share of this
year's: `renewed-in-full` (at least all of it), `cut-under-half` (at least half, less than all), and
`cut-half-or-more` (less than half). A contractor earns 100 a year under the program. If the program ends,
it may win part of the work back under whoever takes over: `subcontract-won`, "The contractor holds at
least half of its program work as a subcontractor within a year of a non-renewal", which implies not
`program-renewal`.

```
revenue = program_revenue * share_kept

inputs         program_revenue   100    read from the annual report for this year, and carried into the period by an assumption
               share_kept        1      assumed: funding continues at this year's level

given values   given not program-renewal    share_kept = 0
               given cut-under-half         share_kept = 0.7
               given cut-half-or-more       share_kept = 0.4
               given subcontract-won        share_kept = 0.5

worlds         renewed-in-full                          revenue 100   shortfall 0
               cut-under-half                           revenue 70    shortfall 30
               cut-half-or-more                         revenue 40    shortfall 60
               not renewed, subcontract won             revenue 50    shortfall 50
               not renewed, subcontract not won         revenue 0     shortfall 100
```

Where the program is not renewed and the subcontract is won, two given values hold. The one given
`subcontract-won` holds in one world, which lies within the two worlds where the program is not renewed,
so it gives the value. The condition is the last four worlds.

## 5. Basis

Every figure opens to what it rests on.

A figure's basis has two parts, one or both.

| Part | It opens to | Who writes the value |
|---|---|---|
| Where the figure is read, in words (`source`) | The passage that states it, checked word for word against the stored record | The analyst |
| Where the figure is read, by query (`result`) | One cell of a saved result: the result, the column, and the columns that pick out the row | The store, which reads the cell |
| The assumption by which it holds (`assumption`) | The reasoning, and the passages and saved results it rests on | The analyst |

A figure is read in one place. Which parts a figure has says what kind of figure it is:

| The figure | Its basis |
|---|---|
| A record states this quantity, for this company, over the case's period | Where it is read |
| A record states it for another period, another company or another quantity: a proxy | Where it is read, and the assumption that carries it over |
| No record states it | The assumption |

An input's value is its value over the case's period, as things stand. A given value has the same basis: the
terms of an agreement can state a value for an outcome, a result can hold a measured rate that an
assumption carries to this case, and otherwise the value rests on its assumption. A computed figure opens to
the formula and the values it took in that world.

## 6. What the analyst states and learns

A case is stated whole in one call: the case, with any proposition it introduces or revises given in full
beside it, its ways and its links included. The same call with nothing saved calculates a draft.

| The analyst | It learns in return |
|---|---|
| States a case | Each world, with the propositions true and false in it, the inputs that take another value there, the revenue and the shortfall; the baseline; the condition; the result of each basis check; the propositions the call saved; and each other active case the call's propositions moved, which gets a revision of its own |
| Calculates a draft | The same worlds and figures, with nothing saved |
| Either | The sensitivity of the shortfall to each figure, and the propositions the case reads that carry no evidence |
| Looks up a proposition | It in full with its ways and links, what code reads from its level fields, and each case that reads it: the case's formula, given values and worlds |
| Looks up a case | The case as it now stands, with the propositions it reads and its revisions |
| Changes part of a saved case | The case as computed after the change, and each figure before and after: the baseline, the revenue at stake, and each world's revenue and shortfall |

**A statement that cannot hold is refused**, with the reason, and the store stays as it was:

- a given value that names a proposition the store does not hold, or an input the case does not state;
- a world where several given values set the same input and none has its worlds strictly within those of
  every other: the refusal names the world, the input and those given values;
- a key that already names a proposition elsewhere in the store;
- a given value whose condition holds in no world;
- ways beside one another whose level fields overlap, a way whose values lie outside those of what it
  divides, or one way alone;
- a passage that is not in its record, a result cell that picks out no row or several, a formula that
  cannot be evaluated;
- a statement of a proposition that leaves out one an active case reads or one another proposition is
  linked to, or that leaves an active case without its figures, naming the case or the proposition.

## 7. Estimates

Each save writes a revision with its date and its run: the case, the propositions it reads, the inputs and
their bases, and the figures, as they stood. A case's revisions, in order, are its estimates. A statement of
a proposition moves every active case it reaches, by being read, through the ways beside what a case reads,
or through a link; each such case gets an estimate of its own. A withdrawn case reads as its last estimate.

## 8. The seam with event-risk

The two systems share the proposition. A portfolio watches it; a company case depends on it. Either
analyst can state or refine it. event-risk says how likely each outcome is, values a holding in it, and
evaluates protection against it.

**What crosses** is a document for each event: the propositions the cases on it read, with their fields,
ways, links and evidence, and each case's dated estimate with its formula, inputs, given values and
worlds.

| Here | In event-risk |
|---|---|
| A proposition's `title`, `text`, `kind`, `window_start`, `window_end`, `comparison`, `value`, `upper_comparison`, `upper_value`, `resolution_source` | The same fields of a proposition |
| A proposition's `metric` | A `Metric`, which its analyst adds from the quantity and unit |
| A `way` link, with its side on an event | A `way` link, with its `side` |
| `implies` | `implies` |
| `implies_not` | A chance of zero for the first given the second. Where the second is an event, the first is a way of its No side beside the rest |
| The passages of evidence quoted from one record | One `evidence` link to that source, with the passages, each run of adjacent sentences its own |
| A given value's `given` and `given_not` | The grammar of a chance: the propositions it is given, true and false |
| A case's worlds | The cases of the structure its propositions form (`MATH.md` 1.2), read together as joint worlds (1.9) |
| A world no given value holds in stands at the baseline | A world left without shocks stands where the baseline does (1.3) |

event-risk states a chance for each proposition, given the others it depends on. Each world of a case
then has a probability, and the mean shortfall is the sum over the worlds of probability times shortfall.

**Three measures, each with its own meaning.** A revenue shortfall is measured against the case's baseline,
over the case's period. A holding's gain or loss is measured against its value today, and takes a valuation
of the holding in that world. A contract pays according to its own terms. event-risk sets a contract's
proceeds, after its cost, against the shortfall in the same world and on comparable dates.

## 9. Cases the build passes

Each is a test in `tests/test_model.py`, by its number. All use the illustration's formula and inputs unless they
say otherwise.

| # | Statements | Result |
|---|---|---|
| 1 | The illustration as written | Its five worlds, with those figures |
| 2 | A second contractor's case on `program-renewal`, with a claim of its own that implies not `program-renewal` | Its worlds are the event's and its own claim's. The first contractor's five worlds and the event's ways are as they were |
| 3 | Given values only for `program-renewal` false | Two worlds: renewed, at the baseline; not renewed |
| 4 | Case 3, after another case's given values name `cut-half-or-more` | The same two worlds |
| 5 | Given `program-renewal`: `late_fee = 2`. Given `cut-half-or-more`: `share_kept = 0.4`. Formula `program_revenue * share_kept - late_fee` | Four worlds: the three ways, each with the fee, the third also at 0.4; not renewed, at the baseline. No figure is labeled for the Yes side as a whole |
| 6 | The illustration without the `implies_not` link | Refused: the subcontract can then be won where the program is cut, and there two given values set `share_kept` and neither's worlds lie within the other's |
| 7 | The illustration, with `given not program-renewal, given not subcontract-won: share_kept = 0.1` added | The last world takes 0.1: its worlds lie within those of the value given not `program-renewal` |
| 8 | Every way of `program-renewal`, and its No side, has a given value, `renewed-in-full` at 1.1 | Four worlds, none at the baseline. Each shortfall is measured from the formula at the inputs' values, and the first is −10 |
| 9 | Two events on one stream, each with two ways on its Yes side and a given value on each way | Nine worlds: every pair of the first event's three worlds and the second's |
| 10 | A fourth way of `program-renewal`'s Yes side, "funded at 40% to 60%" | Refused: its band overlaps two of the ways beside it |
| 11 | `program_revenue` given as a result cell | The store reads 100 from the cell. A row that picks out no row or several is refused |
| 12 | A given value named for a proposition that implies `cut-half-or-more`, while no given value names a way of `program-renewal` | Three worlds: renewed with that proposition true; renewed with it false; not renewed. The first lies within the Yes side through the link |
| 13 | `cut-half-or-more` has two ways of its own, by whether a quarter of the funding remains. One given value, for the lower of them | Five worlds: `renewed-in-full`, `cut-under-half`, the two ways of `cut-half-or-more`, and not renewed. Four stand at the baseline |
| 14 | Given `cut-under-half`: 0.7. Given `program-renewal` and `cut-under-half`: 0.6 | Refused: the two hold in the same world and neither's worlds lie strictly within the other's |
| 15 | The system prompt's illustration, with a tenth of each input's largest value | Its seven figures, by how far each moves a shortfall: 16 for the two values of `regained`, 10 for the rest, each with its world |
| 16 | One given value of a saved case changed | That world's revenue and shortfall before and after; the other worlds as they were; one revision; the rest of the case as saved |
| 17 | A given value added for a proposition the case did not read; then one removed | The worlds the change brings, with no figure before, and those it removes; a world left with no given value stands at the baseline |
| 18 | A change that leaves two given values unordered in a world, removes an input the formula uses, or leaves no given value | Refused, and the saved case stands |

## 10. Build

| Part | In code |
|---|---|
| Propositions in one shape, with level fields, ways and the two links (section 2) | Built |
| A case's events, model and given values; its worlds and their figures (sections 3 and 4) | Built |
| The three bases, with a result cell read by the store (section 5) | Built |
| A case and its propositions stated in one call; what each call returns and refuses (section 6) | Built |
| Estimates, written for a case and for each other case a restated proposition moves (section 7) | Built |
| The sensitivity of the shortfall to each figure; changing part of a saved case, with each figure before and after (sections 4 and 6) | Built |
| The document that crosses to event-risk (section 8) | Next |

The whole is tried on runs on new subjects, unrelated to one another and to anything it was built against.
