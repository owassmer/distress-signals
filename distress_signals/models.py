"""The record shapes, declared once. The tools' input schemas, the store and the exports all use
these. What the researcher states is validated here; what code derives (worlds, figures) is not part of
these inputs. docs/model.md states what each shape means."""

from __future__ import annotations

import datetime as dt
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, create_model, model_validator

SLUG = r"^[a-z0-9][a-z0-9-]{1,80}$"
IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------- shared pieces ----------

class Cite(Strict):
    """A passage quoted from a source."""
    observation_id: str | None = Field(None, description="The stored record the passage is quoted from")
    url: str | None = Field(None, description="The page the passage is quoted from, when it couldn't be read with "
                                              "read_record; the passage is then stored unchecked")
    passage: str = Field(min_length=3, description="The relevant section of the source, word for word: for a figure, "
                                                   "the sentence that states it or its line item with the table's "
                                                   "heading, never the number alone. '...' marks an omission")
    checked: bool | None = Field(None, exclude=True, description="Set by the store; ignored on input")

    @model_validator(mode="after")
    def _one_place(self):
        if bool(self.observation_id) == bool(self.url):
            raise ValueError("give exactly one of observation_id or url")
        return self


class Person(Strict):
    role: str = Field(description="What this person is responsible for: the affected business, or its financial exposure")
    name: str | None = Field(None, description="The person's name, as a public business source gives it")
    title: str | None = Field(None, description="The person's title")
    observation_id: str | None = Field(None, description="The stored record that names this person in this role")


# ---------- company ----------

class Company(Strict):
    id: str | None = Field(None, description="The saved company's ID; with it, this object edits that company")
    name: str = Field(min_length=1, description="The company's name")
    domain: str | None = Field(None, description="The company's website domain")
    aliases: list[str] = Field(default=[], description="Other names the company is known by, including former names "
                                                       "and the names of its operating units")
    description: str | None = Field(None, description="How the company earns its revenue")
    relationships: str | None = Field(None, description="Parent companies, subsidiaries or other related entities that "
                                                        "bear on the exposure")
    contacts: list[Person] = Field(default=[], description="People at the company found in public business sources")


# ---------- propositions ----------

LEVELS = (">", ">=", "<", "<=", "==")


class Proposition(Strict):
    """A claim that will turn out true or false: an event, a way an event or another claim can turn out, or
    what a company or the parties around it do once an outcome is known. One shape for all of them; its
    ways and its links tell them apart."""
    key: str = Field(pattern=SLUG, description="A short identifier, in lowercase letters, digits and hyphens, unique "
                                               "among all saved propositions; it stays the same when the wording is "
                                               "revised")
    side: Literal["yes", "no"] | None = Field(None, description="On a way of an event: the side of the event it divides, "
                                                                "'yes' where left out")
    title: str = Field(description="The claim, in a few words")
    text: str = Field(description="The claim in full: what would make it true")
    kind: Literal["scheduled_decision", "hitting", "level_at_date"] = Field(
        description="scheduled_decision: a decision scheduled on window_end; hitting: true where it happens at any "
                    "time through window_end; level_at_date: a level on window_end")
    window_start: dt.date | None = Field(None, description="For a hitting claim, the first day it counts from")
    window_end: dt.date = Field(description="The date it is decided on or by")
    comparison: Literal[">", ">=", "<", "<=", "==", "occurs"] = Field(
        "occurs", description="occurs where the claim is true when something happens. Where it is about a level: one of "
                              ">, >=, <, <= and ==, with metric and value")
    metric: str | None = Field(None, description="For a claim about a level: a short identifier of the quantity, the "
                                                 "same on every proposition about that quantity")
    unit: str | None = Field(None, description="The metric's unit")
    value: float | None = Field(None, description="The threshold the comparison is made against")
    upper_comparison: Literal["<", "<="] | None = Field(None, description="A band's upper end: with comparison > or >= "
                                                                          "its lower end")
    upper_value: float | None = Field(None, description="The band's upper threshold")
    resolution_source: str | None = Field(None, description="Where a record will show how it turned out, that record: "
                                                            "its publisher, and the document, docket or data it will "
                                                            "appear in")
    description: str | None = Field(None, description="Who or what decides it, and how")
    timing: str | None = Field(None, description="The steps between now and window_end, each with its date and how firm "
                                                 "that date is")
    evidence: list[Cite] = Field(default=[], description="Passages that establish what the claim is, when it is "
                                                         "decided, and what bears on how it turns out")
    implies: list[str] = Field(default=[], description="Keys of propositions that are true wherever this one is. A "
                                                       "claim about what follows an outcome implies that outcome")
    implies_not: list[str] = Field(default=[], description="Keys of propositions that are false wherever this one is "
                                                           "true")
    ways: list[Proposition] = Field(default=[], description="The ways it can turn out, each a proposition of its own. "
                                                            "The ways beside one another exclude one another and "
                                                            "together make up what they divide. A way divides an "
                                                            "outcome for every case that reads it")

    @model_validator(mode="after")
    def _level(self):
        if self.comparison == "occurs":
            if any(x is not None for x in (self.value, self.upper_comparison, self.upper_value)):
                raise ValueError(f"{self.key}: a claim that occurs has no value; a claim about a level states its "
                                 "comparison")
        else:
            if self.metric is None or self.value is None:
                raise ValueError(f"{self.key}: a claim about a level states its metric and its value")
            if (self.upper_comparison is None) != (self.upper_value is None):
                raise ValueError(f"{self.key}: a band states both upper_comparison and upper_value")
            if self.upper_comparison and self.comparison not in (">", ">="):
                raise ValueError(f"{self.key}: a band's lower end is > or >=")
            if self.upper_value is not None and self.upper_value <= self.value:
                raise ValueError(f"{self.key}: a band runs from value up to upper_value, and here they are "
                                 f"{self.value:g} and {self.upper_value:g}. A single value is comparison ==")
        return self


Proposition.model_rebuild()


def check_propositions(roots):
    """The rules a proposition given in full follows with its ways: keys used once, a side named only on a
    way of the proposition itself, and no way alone beside nothing."""
    seen = set()

    def walk(p, depth):
        if p.key in seen:
            raise ValueError(f"the key {p.key!r} is used twice; a key names one proposition")
        seen.add(p.key)
        if depth != 1 and p.side is not None:
            raise ValueError(f"{p.key}: side is named on a way of a proposition that is not itself a way")
        for side in ("yes", "no"):
            group = [w for w in p.ways if (w.side or "yes") == side]
            if depth > 0 and side == "no" and group:
                raise ValueError(f"{p.key}: the ways of a way divide it; they name no side")
            if len(group) == 1:
                raise ValueError(f"{group[0].key!r} is the only way of {p.key!r}"
                                 + (" on its No side" if side == "no" else "") + ". The ways beside one another exclude "
                                 "one another and together make up what they divide, so one way alone is the whole: "
                                 "give the other ways, or none")
        for w in p.ways:
            walk(w, depth + 1)

    for r in roots:
        if r.side is not None:
            raise ValueError(f"{r.key}: side is named on a way, inside the proposition it divides")
        walk(r, 0)


# ---------- the case ----------

class Assumption(Strict):
    """What an assumed value rests on."""
    note: str = Field(min_length=10, description="The reasoning: why this value, and why what it rests on applies here")
    evidence: list[Cite] = Field(default=[], description="Passages the assumption rests on")
    results: list[str] = Field(default=[], description="Saved compute results the assumption rests on")


class Cell(Strict):
    """One value of a saved compute result. The store reads it."""
    result_id: str = Field(description="The saved compute result")
    column: str = Field(description="The column that holds the value")
    row: dict[str, Any] = Field(default={}, description="Where the result has several rows: one or more of its columns "
                                                        "with the values that pick out one row")


class Input(Strict):
    """A figure the formulas use, with exactly one basis. Its value is its value over the case's period in the
    baseline."""
    unit: str = Field(description="What the figure counts or measures")
    value: float | None = Field(None, description="The figure, over the case's period. An amount of money is in whole "
                                                  "units of the case's currency. Leave it out where result gives it: "
                                                  "the store reads it")
    source: Cite | None = Field(None, description="Observed in words: the passage that states the figure for the "
                                                  "case's period")
    result: Cell | None = Field(None, description="Observed by query: the cell of a saved compute result that holds the "
                                                  "figure")
    assumption: Assumption | None = Field(None, description="Assumed: what the figure rests on. A figure a record "
                                                            "states for an earlier period, carried into the case's "
                                                            "period, is assumed and rests on that record")
    note: str | None = Field(None, description="Anything else a reader needs to interpret the figure")

    @model_validator(mode="after")
    def _basis(self):
        if sum(x is not None for x in (self.source, self.result, self.assumption)) != 1:
            raise ValueError("give exactly one of source (observed in words), result (observed by query) or assumption")
        if self.value is None and self.result is None:
            raise ValueError("give the value")
        return self


class Value(Strict):
    value: float = Field(description="The value the input takes")
    assumption: Assumption = Field(description="What that value rests on")


class Given(Strict):
    """The values some inputs take given some propositions true and others false."""
    given: list[str] = Field(default=[], description="Keys of the propositions that are true")
    given_not: list[str] = Field(default=[], description="Keys of the propositions that are false")
    values: dict[str, Value] = Field(min_length=1, description="The inputs that take another value there, by name")

    @model_validator(mode="after")
    def _condition(self):
        if not self.given and not self.given_not:
            raise ValueError("a given value names at least one proposition, in given or given_not")
        both = sorted(set(self.given) & set(self.given_not))
        if both:
            raise ValueError(f"{', '.join(both)} named in given and in given_not")
        return self


class Period(Strict):
    """The span over which revenue is counted."""
    start: dt.date = Field(description="The first day of the period")
    end: dt.date = Field(description="The last day of the period")

    @model_validator(mode="after")
    def _order(self):
        if self.end <= self.start:
            raise ValueError("period end must be after its start")
        return self


class Denominator(Strict):
    """What the shortfall is a share of."""
    label: str = Field(description="What the denominator is, over what period")
    formula: str = Field(description="Formula for its value")


class Reasons(Strict):
    why_matters: str = Field(description="Why the exposure matters enough to warrant a conversation")
    why_now: str = Field(description="Why it makes sense to reach out now")
    why_now_date: dt.date | None = Field(None, description="The date that makes it timely")
    priority: int = Field(ge=1, le=5, description="Where this company stands among those worth contacting: 1 is first")
    priority_reason: str = Field(description="Why it stands there")


class Outreach(Strict):
    draft: str = Field(description="A short opening that the person could answer from their knowledge of the business")


class Evidence(Cite):
    supports: str = Field(description="What this passage establishes")


FORMULAS = ("revenue", "contribution", "deferred_past_period", "cash_delayed")


def check_model(inputs, given_values):
    """The rules a case's model follows, for a calculation and a saved case alike."""
    bad = [k for k in inputs if not IDENT.match(k)]
    if bad:
        raise ValueError(f"input names must be identifiers: {bad}")
    for i, g in enumerate(given_values):
        unknown = [k for k in g.values if k not in inputs]
        if unknown:
            raise ValueError(f"given_values[{i}]: no input named {', '.join(unknown)}; a given value sets an input the "
                             "case states")


class Case(Strict):
    """A case for contacting a company: one company, one revenue stream, and the event or events the stream
    depends on. Its model is one revenue formula, its inputs, and the values they take given outcomes."""
    case_id: str | None = Field(None, description="The saved case's ID, when revising it")
    company: Company | str = Field(description="The company, in full or by saved ID")
    events: list[str] = Field(min_length=1, description="The key of the event the case is about: the proposition, "
                                                        "upcoming and outside the company's control, that this revenue "
                                                        "depends on. Two keys where two events are read together on "
                                                        "one stream")
    stream_key: str = Field(pattern=SLUG, description="A short identifier for the affected revenue stream, in lowercase "
                                                      "letters, digits and hyphens")
    stream: str = Field(description="The business activity whose revenue depends on the events")
    dependency: str = Field(description="Why the company earns less in those worlds: the contract, customer, program, "
                                        "facility or channel through which the events reach the revenue")
    timing: str = Field(description="When the company would feel the effect")
    period: Period
    currency: str = Field(pattern=r"^[A-Z]{3}$", description="The currency of the amounts of money, as an ISO code; "
                                                            "amounts are in whole units of it")
    revenue: str = Field(description="Formula: the revenue the company would recognize within the period, including "
                                     "work or sales made up within it. At the inputs' values it gives the baseline; "
                                     "at their values in a world, the revenue there")
    contribution: str | None = Field(None, description="Formula: that revenue less the costs that change with it")
    deferred_past_period: str | None = Field(None, description="Formula: revenue the company would recognize after the "
                                                               "period instead of within it. Leave out when later "
                                                               "recovery is unknown")
    cash_delayed: str | None = Field(None, description="Formula: cash the company would collect later than it otherwise "
                                                       "would; a timing effect, not lost revenue")
    inputs: dict[str, Input] = Field(description="The figures the formulas use, by name, each at its value over the "
                                                 "period in the baseline")
    given_values: list[Given] = Field(min_length=1, description="Each says: given these propositions true and these "
                                                                "false, these inputs take these values. The case's "
                                                                "worlds are the combinations of its events and of the "
                                                                "propositions named here that the links allow; a way "
                                                                "named brings the ways beside it. In each world an "
                                                                "input takes the most specific given value that holds "
                                                                "there, and otherwise its own value")
    denominator: Denominator | None = None
    reasons: Reasons = Field(description="Why the company is worth contacting, and when")
    buyer: Person = Field(description="The person to approach")
    outreach: Outreach
    evidence: list[Evidence] = Field(default=[], description="Passages that support the case's other claims")
    next_action: str = Field(description="What the operator or the researcher should do next")
    change_note: str = Field(min_length=3, description="What this save changed")

    @model_validator(mode="after")
    def _consistent(self):
        return _consistent(self)


def _consistent(m):
    check_model(m.inputs, m.given_values)
    if len(set(m.events)) != len(m.events):
        raise ValueError("events: a key is named twice")
    return m


MODEL_FIELDS = ("events", *FORMULAS, "inputs", "given_values", "denominator")
Draft = create_model("Draft", __base__=Strict, __doc__="A draft of a case's model, for calculate_exposure.",
                     __validators__={"_consistent": model_validator(mode="after")(_consistent)},
                     **{k: (Case.model_fields[k].annotation, Case.model_fields[k]) for k in MODEL_FIELDS})


# ---------- findings: research kept before it is a case ----------

class Finding(Strict):
    kind: Literal["fact", "inference", "conclusion", "question", "dropped"] = Field(
        description="fact: what a source states; inference or conclusion: what follows from the evidence; question: "
                    "what is still open; dropped: a company, dependency or lead set aside")
    statement: str = Field(min_length=10, description="The finding, with its reasons")
    company: Company | str | None = Field(None, description="The company it concerns, in full or by saved ID")
    proposition: str | None = Field(None, description="The key of the proposition it concerns")
    case_id: str | None = Field(None, description="The saved case it concerns")
    withdraw_case: bool = Field(False, description="Withdraw the case named by case_id from the active queue; the "
                                                   "finding records why")
    evidence: list[Cite] = Field(default=[], description="Passages it rests on; a fact needs at least one")
    results: list[str] = Field(default=[], description="Workspace results it rests on")
    supersedes: str | None = Field(None, description="An earlier finding this one replaces")

    @model_validator(mode="after")
    def _basis(self):
        if self.kind == "fact" and not self.evidence:
            raise ValueError("a fact cites the passage that states it")
        if self.withdraw_case and not self.case_id:
            raise ValueError("withdraw_case needs the case_id of the case to withdraw")
        return self


# ---------- observation (written by code, not the researcher) ----------

class Observation(Strict):
    id: str
    kind: str = Field(min_length=1, description="Record type supplied by the source adapter")
    source: str
    native_id: str | None = None
    url: str | None = None
    published: str | None = None
    retrieved_at: str
    text: str | None = None
    data: dict = {}
