"""The record shapes, declared once. The tools' input schemas, the store and the exports all use
these. What the researcher states is validated here; what code derives (results) is not part of
these inputs."""

from __future__ import annotations

import datetime as dt
import re
from typing import Literal

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


class Derived(Strict):
    """A figure computed in the workspace."""
    result_id: str = Field(description="The saved compute result whose rows contain the value")
    note: str | None = Field(None, description="Which value in the result, and what it measures")


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

class Event(Strict):
    """An upcoming event outside the companies' control. Several companies can depend on the same event
    in different ways; each case states the condition that affects its own revenue."""
    id: str | None = Field(None, description="The saved event's ID; when given, it must be the event the key names")
    key: str = Field(pattern=SLUG, description="A short identifier for the event, in lowercase letters, digits and "
                                               "hyphens; it stays the same when the event is revised")
    title: str = Field(description="The event, in a few words")
    description: str = Field(description="What could happen, and what decides whether it does")
    date: dt.date | None = Field(None, description="The date it is decided or begins")
    timing: str | None = Field(None, description="When it could happen, and how long it could last")
    evidence: list[Cite] = Field(default=[], description="Passages that establish what the event is and when it happens")


# ---------- opportunity ----------

class Input(Strict):
    """A figure the calculation uses, with exactly one provenance."""
    value: float = Field(description="The figure. An amount of money is in whole units of the case's currency")
    unit: str = Field(description="What the figure counts or measures")
    source: Cite | None = Field(None, description="Reported: the passage that states the figure")
    derived: Derived | None = Field(None, description="Derived: the workspace result that computes the figure")
    assumption: str | None = Field(None, description="Assumed: the reasoning behind the figure")
    note: str | None = Field(None, description="Anything else a reader needs to interpret the figure")

    @model_validator(mode="after")
    def _provenance(self):
        if sum(x is not None for x in (self.source, self.derived, self.assumption)) != 1:
            raise ValueError("give exactly one of source (reported), derived (workspace result) or assumption")
        return self


class Baseline(Strict):
    """The company's revenue over the period if the event doesn't affect it."""
    description: str = Field(description="What the baseline assumes")
    revenue: str = Field(description="Formula: the revenue the company would recognize within the period")
    contribution: str | None = Field(None, description="Formula: that revenue less the costs that change with it")


class Affected(Strict):
    """The company's revenue over the period if the condition occurs. A case can model more than one
    version of its condition."""
    name: str = Field(pattern=NAME, description="A short name for this version, in lowercase letters, digits and "
                                                "underscores")
    description: str = Field(description="This version of the condition: what happens, and for how long")
    timeline: str = Field(description="When the company's work or sales stop, resume and are made up, and when revenue "
                                      "and cash land, relative to the period")
    revenue: str = Field(description="Formula: the revenue the company would recognize within the period, including "
                                     "work or sales made up within it")
    deferred_past_period: str | None = Field(None, description="Formula: revenue the company would recognize after the "
                                                               "period instead of within it. Leave out when later "
                                                               "recovery is unknown")
    contribution: str | None = Field(None, description="Formula: that revenue less the costs that change with it")
    cash_delayed: str | None = Field(None, description="Formula: cash the company would collect later than it otherwise "
                                                       "would; a timing effect, not lost revenue")


def check_structure(inputs, affected):
    """The rules inputs and affected versions follow, for a calculation and a saved case alike."""
    bad = [k for k in inputs if not IDENT.match(k)]
    if bad:
        raise ValueError(f"input names must be identifiers: {bad}")
    names = [a.name for a in affected]
    if len(set(names)) != len(names):
        raise ValueError("the names of the affected versions must be unique")


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
    """A case for contacting a company: one company, one event, one affected revenue stream."""
    opportunity_id: str | None = Field(None, description="The saved case's ID, when revising it")
    company: Company | str = Field(description="The company, in full or by saved ID")
    event: Event | str = Field(description="The event, in full or by saved key or ID")
    stream_key: str = Field(pattern=SLUG, description="A short identifier for the affected revenue stream, in lowercase "
                                                      "letters, digits and hyphens")
    stream: str = Field(description="The revenue stream that depends on the event")
    condition: str = Field(description="What has to happen for this revenue to be affected")
    dependency: str = Field(description="Why the company earns less if that happens: the contract, customer, program, "
                                        "facility or channel through which it reaches revenue")
    timing: str = Field(description="When the company would feel the effect")
    period: Period
    currency: str = Field(pattern=r"^[A-Z]{3}$", description="The currency of the amounts of money, as an ISO code; "
                                                            "amounts are in whole units of it")
    inputs: dict[str, Input] = Field(description="The figures the formulas use, by name")
    baseline: Baseline
    affected: list[Affected] = Field(min_length=1, description="Each version of the condition the case models")
    denominator: Denominator | None = None
    case: Reasons = Field(description="Why the company is worth contacting, and when")
    buyer: Person = Field(description="The person to approach")
    outreach: Outreach
    evidence: list[Evidence] = Field(default=[], description="Passages that support the case's other claims")
    next_action: str = Field(description="What the operator or the researcher should do next")
    change_note: str = Field(min_length=3, description="What this save changed")

    @model_validator(mode="after")
    def _consistent(self):
        check_structure(self.inputs, self.affected)
        return self


class Calculation(Strict):
    """A draft of the parts calculate_exposure needs."""
    inputs: dict[str, Input] = Field(description="The figures the formulas use, by name")
    baseline: Baseline
    affected: list[Affected] = Field(min_length=1)
    denominator: Denominator | None = None

    @model_validator(mode="after")
    def _consistent(self):
        check_structure(self.inputs, self.affected)
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
    kind: Literal["payment", "award", "filing", "financial_fact", "page"]
    source: str
    native_id: str | None = None
    url: str | None = None
    published: str | None = None
    retrieved_at: str
    text: str | None = None
    data: dict = {}
