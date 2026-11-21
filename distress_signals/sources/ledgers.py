"""Government payment ledgers (Socrata SODA API): who a government pays, from which department,
how much and when.

  search_payee()   a name across every ledger: payee spelling x department, with totals and dates
  payee_lines()    every payment line for one exact payee spelling in one ledger
"""

import datetime as dt
import re
import os
import statistics as st
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from urllib.parse import urlencode

from .. import names
from ..util import SourceError, config, http_json, money, to_date, to_float, today
from . import Source

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


def _since(since):
    return to_date(since) or today() - dt.timedelta(days=3 * 365)


def _payee_rows(ledger, name, since):
    # $q is Socrata's indexed full-text search: much faster than a like-scan on large ledgers. It can match
    # other columns, so rows are kept only where the payee name matches as whole words. Amounts are cast
    # because some ledgers store them as text.
    rows = []
    for ds in ledger.datasets:
        rows += soql(ledger, ds, {"$select": f"{ledger.vendor} as v, {ledger.unit} as u, count(*) as n, "
                                             f"sum({ledger.amount}::number) as a, min({ledger.paid}) as f, max({ledger.paid}) as l",
                                  "$q": " ".join(names.core_tokens(name)), "$where": f"{ledger.paid} >= '{since.isoformat()}'",
                                  "$group": f"{ledger.vendor}, {ledger.unit}", "$limit": 2000}, timeout=120)
    return [r for r in rows if names.matches(r.get("v") or "", name)]


def search_payee(name, since=None, ledger_ids=None):
    """{results, errors, coverage}: each payee spelling that matches the name as whole words, by
    department, across the ledgers (all of them unless ledger_ids)."""
    if not names.core_tokens(name):
        raise SourceError("give a company name to search payees for")
    since = _since(since)
    chosen = [L for L in load() if not ledger_ids or L.id in ledger_ids]
    results, errors, coverage = [], {}, {}

    def one(L):
        try:
            return L, _payee_rows(L, name, since), None
        except Exception as e:
            return L, [], e

    with ThreadPoolExecutor(8) as ex:
        for fut in as_completed([ex.submit(one, L) for L in chosen]):
            L, rows, err = fut.result()
            if err:
                errors[L.id] = f"{type(err).__name__}: {str(err)[:150]}"
                continue
            for r in rows:
                payee = (r.get("v") or "").strip()
                results.append({"record_id": f"ledger:{L.id}:{since.isoformat()}:{payee}", "government": L.payer,
                                "payee": payee,
                                "department": names.clean_unit(r.get("u") or ""), "payments": int(r.get("n") or 0),
                                "total": round(to_float(r.get("a")) or 0.0, 2),
                                "first": (r.get("f") or "")[:10], "last": (r.get("l") or "")[:10]})
            if rows:
                start = history_start(L)
                coverage[L.id] = {"government": L.payer, "from": start.isoformat() if start else None}
    results.sort(key=lambda r: -r["total"])
    return {"results": results[:300], "errors": errors, "coverage": coverage, "since": since.isoformat()}


RETAIN = 10000  # payment lines a record keeps and lists


def payee_lines(ledger_id, payee, since=None):
    """The observation for one payee spelling in one ledger since `since`: totals over every line, and
    the lines themselves (the newest RETAIN), all listed in the text."""
    L = next((x for x in load() if x.id == ledger_id), None)
    if not L:
        raise SourceError(f"no ledger {ledger_id!r}")
    since = _since(since)
    where = f"{L.vendor}={q(payee)} AND {L.paid} >= '{since.isoformat()}'"
    cols = L.columns()
    lines = []
    for ds in L.datasets:
        for r in soql_all(L, ds, {"$select": ",".join(dict.fromkeys(cols.values())), "$where": where,
                                  "$order": f"{L.paid} DESC"}):
            lines.append({"paid": (r.get(L.paid) or "")[:10], "department": names.clean_unit(r.get(L.unit) or ""),
                          "amount": to_float(r.get(L.amount)),
                          "invoice_date": (r.get(L.invoice_date) or "")[:10] if L.invoice_date else None,
                          "invoice_id": r.get(L.invoice_id) if L.invoice_id else None,
                          "description": (r.get(L.description) or "")[:160] if L.description else None})
    if not lines:
        raise SourceError(f"{L.payer} shows no payments to {payee!r} since {since}")
    lines.sort(key=lambda x: x["paid"], reverse=True)
    by_dept = {}
    for x in lines:
        d = by_dept.setdefault(x["department"], {"payments": 0, "total": 0.0})
        d["payments"] += 1
        d["total"] += x["amount"] or 0.0
    kept = lines[:RETAIN]
    start = history_start(L)
    text = [f"{L.payer} payments to {payee} from {since} through {lines[0]['paid']} "
            f"(ledger coverage from {start}; invoice date means {L.invoice_date_meaning or 'not available'}).",
            f"{len(lines)} payment lines, {money(sum(x['amount'] or 0 for x in lines))} in total.", "", "By department:"]
    text += [f"  {d}: {v['payments']} payments, {money(v['total'])}" for d, v in sorted(by_dept.items(), key=lambda kv: -kv[1]["total"])]
    if len(lines) > RETAIN:
        text += ["", f"Totals cover all {len(lines)} lines; the newest {RETAIN} are listed below. "
                     "Read again with a later since to list a shorter period in full."]
    text += ["", "Lines, newest first (paid | department | amount | invoice date | invoice ID | description):"]
    text += [f"  {x['paid']} | {x['department']} | {money(x['amount'])} | {x['invoice_date'] or ''} | {x['invoice_id'] or ''} | "
             f"{x['description'] or ''}" for x in kept]
    rows = {"payments": [{"government": L.payer, "payee": payee, "paid_date": x["paid"], "department": x["department"],
                          "amount": x["amount"], "invoice_date": x["invoice_date"] or None, "invoice_id": x["invoice_id"],
                          "description": x["description"]} for x in kept]}
    return {"kind": "payment", "source": L.source, "native_id": f"{ledger_id}:{since.isoformat()}:{payee}", "rows": rows,
            "url": L.rows_url(L.datasets[0], where), "text": "\n".join(text),
            "data": {"title": f"{L.payer} payments to {payee} since {since}", "government": L.payer, "payee": payee,
                     "ledger": ledger_id, "since": since.isoformat(), "coverage_from": start.isoformat() if start else None,
                     "lines_total": len(lines), "lines_listed": len(kept), "by_department": by_dept}}


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


def _locate(ref):
    m = re.match(r"^ledger:([a-z0-9_]+):(\d{4}-\d{2}-\d{2}):(.+)$", ref) or re.match(r"^ledger:([a-z0-9_]+):()(.+)$", ref)
    if not m:
        return None
    lid, since, payee = m[1], m[2] or None, m[3]
    return ("ledger", lid, since or "default", payee), lambda: payee_lines(lid, payee, since)


SOURCE = Source(
    id="ledgers",
    publisher=f"{len(load())} U.S. state and local governments (their published payment ledgers)",
    record="one payee spelling in one government's ledger, with every payment line to it.",
    names="the payee and the paying department.",
    dates="each payment's date, and its invoice date where the ledger has one.",
    search="query is a company name, matched by whole words; returns each payee spelling by paying department with payment "
           "counts, totals and dates. Takes one to two minutes. Filters: since (YYYY-MM-DD; the default is the last three "
           "years; the record IDs it returns keep it), ledgers (ledger IDs).",
    read="every payment line it keeps; fills payments.",
    coverage="each government's payments from the date its published ledger begins; a search's result gives those dates.",
    filters=frozenset({"since", "ledgers"}),
    find=lambda query, filters: search_payee(query, since=filters.get("since"), ledger_ids=filters.get("ledgers")),
    label=lambda q, f: f"Searched state and local government payments to “{q}”",
    locate=_locate,
    again=lambda obs: f"ledger:{obs['native_id']}" if obs["source"].startswith("ledger:") and obs.get("native_id") else None,
    noun="a payment ledger",
    kind="payment", tables=("payments",),
)
