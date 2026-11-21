"""State and local government payment ledgers (Socrata SODA API): who a government pays, from which
department, how much and when. config/ledgers.yaml maps each government's published checkbook onto the same
fields; a search asks every ledger at once and merges their payments by date.
"""

import os
import statistics as st
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from urllib.parse import urlencode

from .. import names
from ..util import SourceError, config, http_json, to_date, to_float
from . import F, Series, page

PAGE = 50000


@dataclass
class Ledger:
    id: str
    payer: str
    level: str
    domain: str
    datasets: list
    vendor: str
    paid: str
    amount: str
    unit: str
    invoice_date: str = None
    invoice_date_meaning: str = None
    due_date: str = None
    invoice_id: str = None
    description: str = None
    notes: str = None
    _history_start: object = field(default=None, repr=False)

    @property
    def source(self):
        return f"ledger:{self.id}"

    def columns(self):
        cols = {"vendor": self.vendor, "paid": self.paid, "amount": self.amount, "unit": self.unit,
                "invoice_date": self.invoice_date, "due_date": self.due_date,
                "invoice_id": self.invoice_id, "description": self.description}
        return {k: v for k, v in cols.items() if v}

    def dataset_page(self, ds):
        return f"https://{self.domain}/d/{ds}"

    def rows_url(self, ds, where):
        return f"https://{self.domain}/resource/{ds}.json?" + urlencode({"$where": where})


def load():
    return [Ledger(**x) for x in config("ledgers.yaml")["ledgers"]]


def q(s):
    return "'" + (s or "").replace("'", "''") + "'"


def soql(ledger, ds, params, timeout=90):
    headers = {}
    if os.environ.get("SOCRATA_APP_TOKEN"):
        headers["X-App-Token"] = os.environ["SOCRATA_APP_TOKEN"]
    return http_json(f"https://{ledger.domain}/resource/{ds}.json", params=params, headers=headers,
                     timeout=timeout, tries=3)


def soql_all(ledger, ds, params):
    out, offset = [], 0
    while True:
        batch = soql(ledger, ds, {**params, "$limit": PAGE, "$offset": offset})
        out += batch
        if len(batch) < PAGE:
            return out
        offset += PAGE


def history_start(ledger):
    if ledger._history_start is None:
        lows = []
        for ds in ledger.datasets:
            r = soql(ledger, ds, {"$select": f"min({ledger.paid}) as lo"})
            if r and r[0].get("lo"):
                lows.append(to_date(r[0]["lo"]))
        ledger._history_start = min(lows) if lows else None
    return ledger._history_start


def _where(L, s, taken):
    """The SoQL conditions one ledger applies for a search, with its own column names; None when the search
    asks for something the ledger does not record."""
    parts, text = [], []
    for given in taken["payee"]:  # a list is any of them: each name's words, all in the payee column
        words = names.core_tokens(given) or names.tokens(given)
        text.append("(" + " AND ".join(f"upper({L.vendor}) like {q('%' + w.upper() + '%')}" for w in words) + ")")
    if text:
        parts.append("(" + " OR ".join(text) + ")")
    lo, hi = taken["paid_date"]
    parts += [f"{L.paid} >= {q(lo)}"] if lo else []
    parts += [f"{L.paid} <= {q(hi + 'T23:59:59')}"] if hi else []
    lo, hi = taken["amount"]
    parts += [f"{L.amount}::number >= {lo}"] if lo is not None else []
    parts += [f"{L.amount}::number <= {hi}"] if hi is not None else []
    if taken["invoice_id"]:
        if not L.invoice_id:
            return None
        parts.append(f"{L.invoice_id} in ({', '.join(q(x) for x in taken['invoice_id'])})")
    return " AND ".join(parts)


def _payment(L, ds, r):
    row = r.get(":id")
    return {"id": f"{L.id}:{ds}:{row}", "url": L.dataset_page(ds), "government": L.payer,
            "payee": (r.get(L.vendor) or "").strip() or None, "department": names.clean_unit(r.get(L.unit) or "") or None,
            "paid_date": (r.get(L.paid) or "")[:10] or None, "amount": to_float(r.get(L.amount)),
            "invoice_date": ((r.get(L.invoice_date) or "")[:10] or None) if L.invoice_date else None,
            "invoice_id": r.get(L.invoice_id) if L.invoice_id else None,
            "description": r.get(L.description) if L.description else None}


def _search(s):
    """Payments across the ledgers, newest first: each ledger's next rows, merged by date."""
    governments = s.any("government")
    chosen = [L for L in load() if not governments or any(set(names.tokens(g)) <= set(names.tokens(L.payer)) for g in governments)]
    taken = {"payee": s.any("payee"), "paid_date": s.between("paid_date"), "amount": s.between("amount"),
             "invoice_id": s.any("invoice_id")}
    # Socrata's full-text search is indexed, where a like-scan of a large ledger is not: the words of the query,
    # of each payee and of a description narrow it, and the conditions on the payee column make it exact.
    words = " ".join([s.query()] + [" ".join(names.core_tokens(g) or names.tokens(g)) for g in taken["payee"][:1]]
                     + s.peek("description").get("is", [])[:1]).strip() if len(taken["payee"]) <= 1 else s.query()
    at = s.cursor or {"at": {L.id: 0 for L in chosen}}
    size = max(1, min(s.size, 1000))

    def one(L):
        where, rows, count = _where(L, s, taken), [], 0
        if where is None:
            return L, [], 0
        cols = ",".join(dict.fromkeys([":id", *L.columns().values()]))
        for ds in L.datasets:
            params = {"$select": cols, "$order": f"{L.paid} DESC, :id", "$limit": size, "$offset": at["at"][L.id]}
            params |= {"$where": where} if where else {}
            params |= {"$q": words} if words else {}
            rows += [_payment(L, ds, r) for r in soql(L, ds, params, timeout=120)]
            if s.cursor is None:
                n = soql(L, ds, {k: v for k, v in params.items() if k in ("$where", "$q")} | {"$select": "count(*) as n"}, timeout=120)
                count += int((n or [{}])[0].get("n") or 0)
        return L, rows, count

    live = [L for L in chosen if at["at"].get(L.id, -1) >= 0]
    got, total = {}, 0
    with ThreadPoolExecutor(8) as ex:
        futures = {ex.submit(one, L): L for L in live}
        for fut in as_completed(futures):
            L = futures[fut]
            try:
                _, rows, count = fut.result()
            except Exception as e:
                raise SourceError(f"{L.payer}'s ledger did not answer ({type(e).__name__}: {str(e)[:120]}). The same search "
                                  "can be made again, or with government set to the others.")
            got[L.id], total = rows, total + count
    merged = sorted((r for rows in got.values() for r in rows), key=lambda r: r["paid_date"] or "", reverse=True)[:size]
    used = {}
    for r in merged:
        lid = r["id"].split(":", 1)[0]
        used[lid] = used.get(lid, 0) + 1
    nxt = dict(at["at"])
    for L in live:  # a ledger is finished when it returned fewer rows than asked and all of them were used
        n = used.get(L.id, 0)
        nxt[L.id] = -1 if len(got[L.id]) < size * len(L.datasets) and n == len(got[L.id]) else at["at"][L.id] + n
    more = any(v >= 0 for v in nxt.values())
    return page(merged, {"at": nxt} if more else None, total if s.cursor is None else None, "newest first")


def _read(given):
    lid, ds, row = (given.split(":", 2) + ["", ""])[:3]
    L = next((x for x in load() if x.id == lid), None)
    if not L or ds not in L.datasets:
        raise SourceError(f"government_payments has no record {given!r}")
    cols = ",".join(dict.fromkeys([":id", *L.columns().values()]))
    rows = soql(L, ds, {"$select": cols, "$where": f":id = {q(row)}"})
    if not rows:
        raise SourceError(f"{L.payer}'s ledger no longer has the row {row}; search for the payment again.")
    return _payment(L, ds, rows[0])


def _coverage(series):
    """The earliest and latest payment dates across the ledgers."""
    def span(L):
        out = []
        for ds in L.datasets:
            r = soql(L, ds, {"$select": f"min({L.paid}) as lo, max({L.paid}) as hi"})
            out += [(r[0].get("lo") or "")[:10], (r[0].get("hi") or "")[:10]] if r else []
        return [x for x in out if x]
    with ThreadPoolExecutor(8) as ex:
        dates = [d for found in ex.map(span, load()) for d in found]
    return (min(dates), max(dates)) if dates else (None, None)


def verify_invoice_dates(ledger, n=5000):
    """A mapped invoice_date must behave like one: paid - invoice_date over the latest n
    payments is spread out and almost never negative."""
    if not ledger.invoice_date:
        return None
    rows = soql(ledger, ledger.datasets[0], {"$select": f"{ledger.invoice_date} as i, {ledger.paid} as p",
                                             "$where": f"{ledger.invoice_date} IS NOT NULL",
                                             "$order": f"{ledger.paid} DESC", "$limit": n})
    lags = [(to_date(r["p"]) - to_date(r["i"])).days for r in rows if r.get("i") and r.get("p")]
    if len(lags) < 20:
        return {"n": len(lags), "pass": False}
    dec = st.quantiles(lags, n=10)
    res = {"n": len(lags), "p10": dec[0], "median": st.median(lags), "p90": dec[-1],
           "share_zero": round(sum(x == 0 for x in lags) / len(lags), 3),
           "share_negative": round(sum(x < 0 for x in lags) / len(lags), 3)}
    res["pass"] = res["share_negative"] < 0.05 and res["share_zero"] < 0.5 and res["median"] > 0
    return res


SERIES = [Series(
    id="government_payments", title="state and local government payments",
    record="a payment by a state or local government to a payee: one line of its published checkbook",
    creator="state and local governments", jurisdiction="; ".join(L.payer for L in load()), coverage=_coverage,
    fields=(F("government", "place"), F("payee", "name"), F("department", "name"), F("paid_date", "date"),
            F("amount", "amount"), F("invoice_date", "date"), F("invoice_id", "identifier"), F("description", "text")),
    search=_search, read=_read)]
