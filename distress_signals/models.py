"""The record shapes, declared once. The tools' input schemas, the store and the exports all use
these, and each field's description is what the analyst reads of it. What the analyst states is validated
here; what code derives (worlds, figures) is not part of these inputs. docs/model.md states what each shape
means."""

from __future__ import annotations

import datetime as dt
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, create_model, model_validator
from pydantic.json_schema import SkipJsonSchema

SLUG = r"^[a-z0-9][a-z0-9-]{1,80}$"
IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------- shared pieces ----------

class Cite(Strict):
    """A passage quoted from a record."""
    observation_id: str | None = Field(None, description="The stored record the passage is quoted from")
    url: str | None = Field(None, description="In place of observation_id, the address the store read the record from")
    passage: str = Field(min_length=3, description="Words quoted from the record. The store finds them word for word "
                                                   "in the record it stored ('...' marks an omission), takes a "
                                                   "quotation of three or more words and figures with a word among "
                                                   "them (a table's row with its heading), and keeps the whole "
                                                   "sentence or row the quotation falls in where it read the record's "
                                                   "layout")
    checked: SkipJsonSchema[bool | None] = Field(None, exclude=True)  # set by the store; no part of what is stated

    @model_validator(mode="after")
    def _one_place(self):
        if bool(self.observation_id) == bool(self.url):
            raise ValueError("give exactly one of observation_id or url")
        return self


class Person(Strict):
    role: str = Field(description="What this person is responsible for: the affected business, or the company's finances")
    name: str | None = Field(None, description="The person's name, as a public record gives it")
    title: str | None = Field(None, description="The person's title")
    observation_id: str | None = Field(None, description="The stored record that names this person in this role")


# ---------- company ----------

class Company(Strict):
    id: str | None = Field(None, description="The saved company's ID; with it, this object edits that company")
    name: str = Field(min_length=1, description="The name of the operator of the business that earns the revenue")
    domain: str | None = Field(None, description="Its website domain")
    aliases: list[str] = Field(default=[], description="Other names it is known by, including former names and the "
                                                       "names of its operating units")
    description: str | None = Field(None, description="How it earns its revenue")
    relationships: str | None = Field(None, description="Parents, subsidiaries and related entities that bear on the "
                                                        "revenue or on who operates the business")


# ---------- propositions ----------

LEVELS = (">", ">=", "<", "<=", "==")


class Proposition(Strict):
    """A claim that will turn out true or false: an event, a way an event or another claim can turn out, or
    something that may follow an outcome. One shape for all of them; its ways and its links tell them apart."""
    key: str = Field(pattern=SLUG, description="A short identifier in lowercase letters, digits and hyphens, unique "
                                               "among all saved propositions. It names the proposition for as long as "
                                               "it is saved, through any revision of its wording or its dates")
    side: Literal["yes", "no"] | None = Field(None, description="On a way of a proposition that is not itself a way: "
                                                                "the outcome it divides, 'yes' (true) where left out, "
                                                                "or 'no' (false)")
    title: str = Field(description="The claim in a few words. A world is named by the titles of the propositions true "
                                   "in it, and by 'Not:' before the title of one that is false")
    text: str = Field(description="The claim in full: what would make it true. Its record is held against this text, "
                                  "and a case's condition is written by joining the texts of the propositions in its "
                                  "worlds")
    kind: Literal["scheduled_decision", "hitting", "level_at_date"] = Field(
        description="scheduled_decision: a decision scheduled on window_end. hitting: true where it happens at any "
                    "time through window_end. level_at_date: a level, or a state of affairs, on window_end")
    window_start: dt.date | None = Field(None, description="For a hitting claim, the first day it counts from; the day "
                                                           "it is first saved where left out")
    window_end: dt.date = Field(description="The date it is decided on or by")
    comparison: Literal[">", ">=", "<", "<=", "==", "occurs"] = Field(
        "occurs", description="occurs where the claim is true when something happens or is the case. Where it is about "
                              "a quantity's level: one of >, >=, <, <= and ==, with metric and value")
    metric: str | None = Field(None, description="For a claim about a level: a short identifier of the quantity, the "
                                                 "same on every proposition about that quantity")
    unit: str | None = Field(None, description="The metric's unit")
    value: float | None = Field(None, description="The threshold the comparison is made against")
    upper_comparison: Literal["<", "<="] | None = Field(None, description="For a band, its upper end; comparison, > or "
                                                                          ">=, and value are then its lower end")
    upper_value: float | None = Field(None, description="The band's upper threshold")
    resolution_source: str | None = Field(None, description="The record that will show how it turned out: its "
                                                            "publisher, and the document, docket or data it will "
                                                            "appear in")
    description: str | None = Field(None, description="Who or what decides it, and how; and what bears on how it will "
                                                      "turn out")
    timing: str | None = Field(None, description="The steps between now and window_end, each with its date and how firm "
                                                 "that date is")
    evidence: list[Cite] = Field(default=[], description="Passages that establish what the claim is, when it is "
                                                         "decided, and what bears on how it will turn out")
    implies: list[str] = Field(default=[], description="Keys of propositions that are true wherever this one is. A "
                                                       "claim about what follows an outcome implies that outcome")
    implies_not: list[str] = Field(default=[], description="Keys of propositions that are false wherever this one is "
                                                           "true. A claim about what follows a proposition turning "
                                                           "out false implies not that proposition")
    ways: list[Proposition] = Field(default=[], description="The ways it can turn out, each a proposition in full. The "
                                                            "ways beside one another exclude one another and together "
                                                            "make up what they divide, for every case that reads them")

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
    """The reasoning by which a figure holds for an input."""
    note: str = Field(min_length=10, description="The reasoning: why the input takes this value over the period, or in "
                                                 "these outcomes. Where the figure is read for another period, "
                                                 "another company or another quantity, what it stands in for and why "
                                                 "it holds here")
    evidence: list[Cite] = Field(default=[], description="Passages the reasoning rests on")
    results: list[str] = Field(default=[], description="Saved compute results the reasoning rests on")


class Cell(Strict):
    """One value of a saved compute result. The store reads it."""
    result_id: str = Field(description="The saved compute result")
    column: str = Field(description="The column that holds the value")
    row: dict[str, Any] = Field(default={}, description="Where the result has several rows: one or more of its columns "
                                                        "with the values that pick out one row")


def _basis(m):
    """A figure's basis is where it is read, the assumption by which it holds, or both."""
    if m.source is not None and m.result is not None:
        raise ValueError("a figure is read in one place: give source or result, not both")
    if m.source is None and m.result is None and m.assumption is None:
        raise ValueError("give the basis: where the figure is read (source or result), the assumption by which it "
                         "holds, or both")
    if m.value is None and m.result is None:
        raise ValueError("give the value")
    return m


class Input(Strict):
    """One driver of the revenue: a figure the formulas use, at its value over the case's period as things
    stand, with its basis."""
    label: str = Field(min_length=3, description="What the input is, in words: the quantity, and whose it is. The "
                                                 "name is how the formulas refer to it; the label is how a reader of "
                                                 "the case reads it")
    unit: str = Field(description="What the figure counts or measures. For an amount of money, the case's currency "
                                  "code; amounts are in whole units of it")
    value: float | None = Field(None, description="The figure over the case's period, as things stand. Left out where "
                                                  "result gives it: the store reads it")
    source: Cite | None = Field(None, description="Where the figure is read, in words: the passage that states it")
    result: Cell | None = Field(None, description="Where the figure is read, by query: the cell of a saved compute "
                                                  "result that holds it")
    assumption: Assumption | None = Field(None, description="The assumption by which the figure holds for this input "
                                                            "over the case's period. With source or result, it "
                                                            "carries a figure read for another period, company or "
                                                            "quantity over to this input. Alone, it is the basis of a "
                                                            "figure no record gives")
    note: str | None = Field(None, description="Anything else a reader needs to interpret the figure")

    @model_validator(mode="after")
    def _basis(self):
        return _basis(self)


class Value(Strict):
    """The value an input takes given outcomes, with its basis."""
    value: float | None = Field(None, description="The value the input takes there. Left out where result gives it")
    source: Cite | None = Field(None, description="Where the value is read, in words: the passage that states it")
    result: Cell | None = Field(None, description="Where the value is read, by query: the cell of a saved compute "
                                                  "result that holds it")
    assumption: Assumption | None = Field(None, description="The assumption by which the input takes this value in "
                                                            "these outcomes. A value that source or result states for "
                                                            "these outcomes, as the terms of an agreement can, has "
                                                            "that part alone")

    @model_validator(mode="after")
    def _basis(self):
        return _basis(self)


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
    formula: str = Field(description="Formula for its value, over the inputs")


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
    """The analysis of one company, one revenue stream, and the event or events the stream depends on. Its model
    is one revenue formula, its inputs, and the values they take given outcomes."""
    case_id: str | None = Field(None, description="The saved case's ID, when revising it")
    company: Company | str = Field(description="The company, in full or by saved ID")
    events: list[str] = Field(min_length=1, description="The key of the event the case is about. Two keys where the "
                                                        "stream depends on two events and they are read together")
    stream_key: str = Field(pattern=SLUG, description="A short identifier for the revenue stream, in lowercase letters, "
                                                      "digits and hyphens")
    stream: str = Field(description="The revenue stream: the business activity whose revenue depends on the events")
    dependency: str = Field(description="How the events reach that revenue: the contract, customer, program, facility "
                                        "or channel through which an outcome changes what the company earns")
    timing: str = Field(description="When an outcome's effect reaches the revenue, and what sets that date")
    period: Period
    currency: str = Field(pattern=r"^[A-Z]{3}$", description="The currency of the amounts of money, as an ISO code; "
                                                            "amounts are in whole units of it")
    revenue: str = Field(description="Formula over the inputs: the revenue the company would recognize from the stream "
                                     "within the period, including work or sales made up within it. At the inputs' own "
                                     "values it gives the baseline; at their values in a world, the revenue there")
    contribution: str | None = Field(None, description="Formula: that revenue less the costs that change with it")
    deferred_past_period: str | None = Field(None, description="Formula: revenue the company would recognize after the "
                                                               "period instead of within it. Left out where later "
                                                               "recovery is unknown")
    cash_delayed: str | None = Field(None, description="Formula: cash the company would collect later than it otherwise "
                                                       "would; a timing effect, not lost revenue")
    inputs: dict[str, Input] = Field(description="The drivers of the revenue, by name, each at its value over the "
                                                 "period as things stand")
    given_values: list[Given] = Field(min_length=1, description="Each says: given these propositions true and these "
                                                                "false, these inputs take these values")
    denominator: Denominator | None = Field(None, description="The figure the shortfall is expressed as a share of, so "
                                                              "that its size against the company can be read")
    person: Person = Field(description="The person responsible for the affected business or for the company's finances")
    evidence: list[Evidence] = Field(default=[], description="Passages that establish the dependency and the timing")
    change_note: str = Field(min_length=3, description="What this save changed")

    @model_validator(mode="after")
    def _consistent(self):
        return _consistent(self)


def _consistent(m):
    check_model(m.inputs, m.given_values)
    if len(set(m.events)) != len(m.events):
        raise ValueError("events: a key is named twice")
    return m


class GivenChange(Strict):
    """A given value to add, or a change to the saved one with the same given and given_not."""
    given: list[str] = Field(default=[], description="Keys of the propositions that are true")
    given_not: list[str] = Field(default=[], description="Keys of the propositions that are false")
    values: dict[str, Value | None] = Field(min_length=1, description="The inputs whose value there is added or "
                                                                      "replaced, by name, each in full; null removes one")

    @model_validator(mode="after")
    def _condition(self):
        return Given._condition(self)


KEPT_OR_CHANGED = ("stream", "dependency", "timing", "period", "currency", *FORMULAS, "denominator", "person", "evidence")
Changes = create_model(
    "Changes", __base__=Strict, __doc__="The parts of a saved case that change. A part left out stays as saved.",
    **{k: (Case.model_fields[k].annotation | None, Field(None, description=Case.model_fields[k].description))
       for k in KEPT_OR_CHANGED},
    inputs=(dict[str, Input | None] | None, Field(None, description="Inputs to add or replace, by name, each in full; "
                                                                    "null removes one")),
    given_values=(list[GivenChange] | None, Field(None, description="Given values to add or change")),
    change_note=(str, Field(min_length=3, description="What this changes")))


MODEL_FIELDS = ("events", *FORMULAS, "inputs", "given_values", "denominator")
Draft = create_model("Draft", __base__=Strict, __doc__="A draft of a case's model, for calculate_exposure.",
                     __validators__={"_consistent": model_validator(mode="after")(_consistent)},
                     **{k: (Case.model_fields[k].annotation, Case.model_fields[k]) for k in MODEL_FIELDS})


# ---------- findings: what the research established apart from a case's figures ----------

class Finding(Strict):
    kind: Literal["fact", "inference", "conclusion"] = Field(
        description="fact: what a record states. inference: what follows from records, by reasoning the statement "
                    "gives. conclusion: what the research established about its subject")
    statement: str = Field(min_length=10, description="The finding, with its reasons")
    company: Company | str | None = Field(None, description="The company it concerns, in full or by saved ID")
    proposition: str | None = Field(None, description="The key of the proposition it concerns")
    case_id: str | None = Field(None, description="The saved case it concerns")
    withdraw_case: bool = Field(False, description="Withdraw the case named by case_id; the finding records why the "
                                                   "revenue no longer depends on the event as the case states")
    evidence: list[Cite] = Field(default=[], description="Passages it rests on; a fact needs at least one")
    results: list[str] = Field(default=[], description="Saved compute results it rests on")
    supersedes: str | None = Field(None, description="An earlier finding this one replaces")

    @model_validator(mode="after")
    def _basis(self):
        if self.kind == "fact" and not self.evidence:
            raise ValueError("a fact cites the passage that states it")
        if self.withdraw_case and not self.case_id:
            raise ValueError("withdraw_case needs the case_id of the case to withdraw")
        if self.company is None and self.proposition is None and self.case_id is None:
            raise ValueError("a finding concerns a company, a proposition or a case: name the one it concerns")
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
