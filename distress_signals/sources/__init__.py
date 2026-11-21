"""The sources that search_records and read_record reach.

Each source is one module that ends with a SOURCE: its card, which is everything the researcher is told
about it, and the functions behind it. The catalog the researcher reads is the cards, one after another, in
an order drawn for each run. Adding a source is its module and its name in MODULES; the tools, the catalog
and the run page's wording all come from here.

A card has the same fields for every source, and says what a record is, not what it is for:
  publisher  who publishes the record
  record     what one record is
  names      whom a record names
  dates      the dates a record carries
  search     what the query is, and every filter
  read       what reading a record returns, and the workspace tables it fills
  coverage   what the source reaches
A card is at most CARD_CHARS characters, so no source is described at greater length than another.
"""

import importlib
import random
from dataclasses import dataclass
from typing import Callable

MODULES = ("federal_register", "filings", "ledgers", "public_records", "sec")
CARD_CHARS = 1000


@dataclass(frozen=True)
class Source:
    id: str  # the name search_records takes
    publisher: str
    record: str
    names: str
    dates: str
    search: str
    read: str
    coverage: str
    filters: frozenset  # the filters its search accepts
    find: Callable  # find(query, filters) -> {"results": [...], and anything else the search reports}
    label: Callable  # label(query, filters) -> what a search did, in plain words, for the run page
    # For a source whose record IDs are its own (an address is read as a page):
    locate: Callable = lambda record_id: None  # -> (the parts of its observation ID, a function that reads it)
    again: Callable = lambda observation: None  # -> the record ID that reads a stored observation's source again
    noun: str = "the source record"  # what one of its records is called on the run page
    kind: str | None = None  # the kind of observation a read stores, where a read fills workspace tables
    tables: tuple = ()  # those tables

    def __post_init__(self):
        if len(self.card()) > CARD_CHARS:
            raise ValueError(f"the {self.id} card is {len(self.card())} characters; a card is at most {CARD_CHARS}")

    def card(self):
        return (f"- {self.id} · {self.publisher}\n  A record: {self.record}\n  Names: {self.names}\n  Dates: {self.dates}\n"
                f"  Search: {self.search}\n  Read: {self.read}\n  Coverage: {self.coverage}")


_all = None


def all():
    """{id: Source}, every source."""
    global _all
    if _all is None:
        found = [importlib.import_module(f"{__name__}.{m}").SOURCE for m in MODULES]
        _all = {s.id: s for s in found}
    return _all


def order(run_id=None):
    """The order a run's catalog lists the sources in: drawn from the run's ID, so every run sees its own and
    the same run always sees the same. Without a run, alphabetical."""
    ids = sorted(all())
    if run_id:
        random.Random(run_id).shuffle(ids)
    return ids


def catalog(ids):
    return "\n".join(all()[i].card() for i in ids)


def locate(record_id):
    """((observation ID parts), read function) and the source, for a record ID a source issued; else None."""
    for s in all().values():
        hit = s.locate(record_id)
        if hit:
            return hit, s
    return None


def again(observation):
    for s in all().values():
        ref = s.again(observation)
        if ref:
            return ref
    return None


def tables():
    """{observation kind: the workspace tables a read of it fills}."""
    return {s.kind: ", ".join(s.tables) for s in all().values() if s.kind}
