"""The record shapes, declared once. The tools' input schemas, the store and the exports all use
these. What the researcher states is validated here; what code derives (results) is not part of
these inputs."""

from __future__ import annotations

import datetime as dt
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SLUG = r"^[a-z0-9][a-z0-9-]{1,80}$"
NAME = r"^[a-z][a-z0-9_]{1,40}$"
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


# ---------- event ----------

SIDES = ("yes", "no")
Kind = Literal["scheduled_decision", "hitting", "level_at_date"]
KIND = ("scheduled_decision: a decision scheduled on window_end; hitting: true where it happens at any time through "
        "window_end; level_at_date: a level on window_end")


class Way(Strict):
    """One way an outcome can turn out: a claim of its own, stated as the event is."""
    key: str = Field(pattern=SLUG, description="A short identifier for this outcome, in lowercase letters, digits and "
                                               "hyphens, unique within the event; it stays the same when the wording "
                                               "is revised")
    title: str = Field(description="The claim, in a few words")
    text: str = Field(description="The claim in full: what would make it true")
    kind: Kind = Field(description=KIND)
    window_start: dt.date | None = Field(None, description="For a hitting claim, the first day it counts from")
    window_end: dt.date = Field(description="The date it is decided on or by")
    resolution_source: str = Field(description="The record that will show whether it turned out true: its publisher, "
                                               "and the document, docket or data it will appear in")
    ways: list[Way] = Field(default=[], description="The ways this outcome can itself turn out. They exclude one another "
                                                    "and together make up this outcome")


Way.model_rebuild()


class Event(Strict):
    """An upcoming event outside the companies' control, stated as a claim that will turn out true or false.
    Its outcomes are its two sides, 'yes' (the claim is true) and 'no' (it is false), and all their ways.
    Several companies can depend on the same event; each case gives its inputs' values in the outcomes that
    change its own revenue."""
    id: str | None = Field(None, description="The saved event's ID; when given, it must be the event the key names")
    key: str = Field(pattern=SLUG, description="A short identifier for the event, in lowercase letters, digits and "
                                               "hyphens; it stays the same when the event is revised")
    title: str = Field(description="The claim, in a few words")
    text: str = Field(description="The claim in full: what would make the event true")
    description: str = Field(description="Who or what decides it, and how")
    kind: Kind = Field(description=KIND)
    window_start: dt.date | None = Field(None, description="For a hitting claim, the first day it counts from")
    window_end: dt.date = Field(description="The date it is decided on or by")
    timing: str | None = Field(None, description="The steps between now and window_end, each with its date and how "
                                                 "firm that date is")
    resolution_source: str = Field(description="The record that will show how it turned out: its publisher, and the "
                                               "document, docket or data it will appear in")
    yes_ways: list[Way] = Field(default=[], description="The ways the 'yes' side can turn out, where what follows for a "
                                                        "company differs between them, each a claim of its own. A "
                                                        "side's ways exclude one another and together make up the "
                                                        "side. Left empty, the side is one outcome")
    no_ways: list[Way] = Field(default=[], description="The ways the 'no' side can turn out, by the same rule")
    evidence: list[Cite] = Field(default=[], description="Passages that establish what the event is, when it is "
                                                         "decided, and what bears on how it turns out")

    @model_validator(mode="after")
    def _outcomes(self):
        seen = set(SIDES)

        def walk(ways, within):
            if len(ways) == 1:
                raise ValueError(f"'{within}' has one way, {ways[0].key!r}. The ways of an outcome exclude one another "
                                 f"and together make up the outcome, so one way alone is the outcome itself: give the "
                                 f"other ways '{within}' can turn out, or leave '{within}' whole")
            for w in ways:
                if w.key in seen:
                    raise ValueError(f"outcome key {w.key!r} is used twice; 'yes' and 'no' are the sides' keys, and each "
                                     "way's key is unique within the event")
                seen.add(w.key)
                walk(w.ways, w.key)

        walk(self.yes_ways, "yes")
        walk(self.no_ways, "no")
        return self


# ---------- opportunity ----------

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
    """A figure the formulas use, with exactly one basis. Its value is its value in the baseline."""
    unit: str = Field(description="What the figure counts or measures")
    value: float | None = Field(None, description="The figure. An amount of money is in whole units of the case's "
                                                  "currency. Leave it out where result gives it: the store reads it")
    source: Cite | None = Field(None, description="Observed in words: the passage that states the figure")
    result: Cell | None = Field(None, description="Observed by query: the cell of a saved compute result that holds the "
                                                  "figure")
    assumption: Assumption | None = Field(None, description="Assumed: what the figure rests on")
    note: str | None = Field(None, description="Anything else a reader needs to interpret the figure")

    @model_validator(mode="after")
    def _basis(self):
        if sum(x is not None for x in (self.source, self.result, self.assumption)) != 1:
            raise ValueError("give exactly one of source (observed in words), result (observed by query) or assumption")
        if self.value is None and self.result is None:
            raise ValueError("give the value")
        return self


class Given(Strict):
    """An input's value in one outcome of the event."""
    value: float = Field(description="The input's value in that outcome")
    assumption: Assumption = Field(description="What that value rests on")


FORMULAS = ("revenue", "contribution", "deferred_past_period", "cash_delayed")


def check_structure(inputs, by_outcome):
    """The rules inputs and the values given by outcome follow, for a calculation and a saved case alike."""
    bad = [k for k in inputs if not IDENT.match(k)]
    if bad:
        raise ValueError(f"input names must be identifiers: {bad}")
    for outcome, values in by_outcome.items():
        if not values:
            raise ValueError(f"by_outcome.{outcome}: give the inputs that take another value in this outcome")
        unknown = [k for k in values if k not in inputs]
        if unknown:
            raise ValueError(f"by_outcome.{outcome}: no input named {', '.join(unknown)}; an outcome gives another value "
                             "to an input the case states")


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


class Opportunity(Strict):
    """A case for contacting a company: one company, one event, one affected revenue stream. Its model is one
    revenue formula over its inputs, and the inputs' values in each outcome of the event that changes them."""
    opportunity_id: str | None = Field(None, description="The saved case's ID, when revising it")
    company: Company | str = Field(description="The company, in full or by saved ID")
    event: Event | str = Field(description="The event, in full or by saved key or ID")
    stream_key: str = Field(pattern=SLUG, description="A short identifier for the affected revenue stream, in lowercase "
                                                      "letters, digits and hyphens")
    stream: str = Field(description="The revenue stream that depends on the event")
    dependency: str = Field(description="Why the company earns less in those outcomes: the contract, customer, "
                                        "program, facility or channel through which they reach revenue")
    timing: str = Field(description="When the company would feel the effect")
    period: Period
    currency: str = Field(pattern=r"^[A-Z]{3}$", description="The currency of the amounts of money, as an ISO code; "
                                                            "amounts are in whole units of it")
    revenue: str = Field(description="Formula: the revenue the company would recognize within the period, including "
                                     "work or sales made up within it. At the inputs' values it gives the baseline; "
                                     "with an outcome's values, the revenue in that outcome")
    contribution: str | None = Field(None, description="Formula: that revenue less the costs that change with it")
    deferred_past_period: str | None = Field(None, description="Formula: revenue the company would recognize after the "
                                                               "period instead of within it. Leave out when later "
                                                               "recovery is unknown")
    cash_delayed: str | None = Field(None, description="Formula: cash the company would collect later than it otherwise "
                                                       "would; a timing effect, not lost revenue")
    inputs: dict[str, Input] = Field(description="The figures the formulas use, by name, each at its value in the "
                                                 "baseline")
    by_outcome: dict[str, dict[str, Given]] = Field(
        min_length=1, description="For each outcome of the event that changes this revenue, by the outcome's key ('yes', "
                                  "'no' or a way's key): the inputs that take another value there. An outcome's values "
                                  "hold in its ways, and a way gives the values that differ further. These outcomes are "
                                  "the case's condition: what has to happen for this revenue to be affected")
    denominator: Denominator | None = None
    case: Reasons = Field(description="Why the company is worth contacting, and when")
    buyer: Person = Field(description="The person to approach")
    outreach: Outreach
    evidence: list[Evidence] = Field(default=[], description="Passages that support the case's other claims")
    next_action: str = Field(description="What the operator or the researcher should do next")
    change_note: str = Field(min_length=3, description="What this save changed")

    @model_validator(mode="after")
    def _consistent(self):
        check_structure(self.inputs, self.by_outcome)
        return self


class Calculation(Strict):
    """A draft of the parts calculate_exposure needs."""
    event: Event | str | None = Field(None, description="The event, in full or by saved key or ID. With it, each outcome "
                                                        "named is checked against the event, and an outcome's values "
                                                        "hold in its ways")
    revenue: str = Field(description="Formula: the revenue the company would recognize within the period, including "
                                     "work or sales made up within it. At the inputs' values it gives the baseline; "
                                     "with an outcome's values, the revenue in that outcome")
    contribution: str | None = Field(None, description="Formula: that revenue less the costs that change with it")
    deferred_past_period: str | None = Field(None, description="Formula: revenue the company would recognize after the "
                                                               "period instead of within it. Leave out when later "
                                                               "recovery is unknown")
    cash_delayed: str | None = Field(None, description="Formula: cash the company would collect later than it otherwise "
                                                       "would; a timing effect, not lost revenue")
    inputs: dict[str, Input] = Field(description="The figures the formulas use, by name, each at its value in the "
                                                 "baseline")
    by_outcome: dict[str, dict[str, Given]] = Field(
        min_length=1, description="For each outcome of the event that changes this revenue, by the outcome's key ('yes', "
                                  "'no' or a way's key): the inputs that take another value there. An outcome's values "
                                  "hold in its ways, and a way gives the values that differ further. These outcomes are "
                                  "the case's condition: what has to happen for this revenue to be affected")
    denominator: Denominator | None = None

    @model_validator(mode="after")
    def _consistent(self):
        check_structure(self.inputs, self.by_outcome)
        return self


# ---------- findings: research kept before it is a case ----------

class Finding(Strict):
    kind: Literal["fact", "inference", "conclusion", "question", "dropped"] = Field(
        description="fact: what a source states; inference or conclusion: what follows from the evidence; question: "
                    "what is still open; dropped: a company, dependency or lead set aside")
    statement: str = Field(min_length=10, description="The finding, with its reasons")
    company: Company | str | None = Field(None, description="The company it concerns, in full or by saved ID")
    event: Event | str | None = Field(None, description="The event it concerns, in full or by saved key or ID")
    opportunity_id: str | None = Field(None, description="The saved case it concerns")
    withdraw_case: bool = Field(False, description="Withdraw the case named by opportunity_id from the active queue; "
                                                   "the finding records why")
    evidence: list[Cite] = Field(default=[], description="Passages it rests on; a fact needs at least one")
    results: list[str] = Field(default=[], description="Workspace results it rests on")
    supersedes: str | None = Field(None, description="An earlier finding this one replaces")

    @model_validator(mode="after")
    def _basis(self):
        if self.kind == "fact" and not self.evidence:
            raise ValueError("a fact cites the passage that states it")
        if self.withdraw_case and not self.opportunity_id:
            raise ValueError("withdraw_case needs the opportunity_id of the case to withdraw")
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
