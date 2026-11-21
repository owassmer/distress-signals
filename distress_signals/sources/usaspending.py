"""USAspending.gov, three series: federal awards, the transactions on them, and their funding by federal
account.

  usaspending_awards         prime awards of every type. The search API takes one group of award types at a
                             time, so a search across types asks each group in turn. It continues by the last
                             record's sort value, which has no limit on how far it reads.
  usaspending_transactions   each action on an award: the award itself and every modification. Read for one
                             award from its own history, or across awards by recipient, agency and action date.
  usaspending_award_funding  an award's obligations and outlays by federal account and reporting period, read
                             for one award at a time.

A range on an award's end date or start date has no filter in the API. The search then sorts by that date,
newest first, and reads until it passes the range, which reaches every award in it.
"""

import functools
import math

from .. import names
from ..util import SourceError, http_json, money
from . import F, Series, page

API = "https://api.usaspending.gov/api/v2"
GROUPS = {"Contract": ["A", "B", "C", "D"],
          "IDV": ["IDV_A", "IDV_B", "IDV_B_A", "IDV_B_B", "IDV_B_C", "IDV_C", "IDV_D", "IDV_E"],
          "Grant": ["02", "03", "04", "05"], "Direct payment": ["06", "10"], "Loan": ["07", "08"], "Other": ["09", "11"]}
COUNTS = {"Contract": "contracts", "IDV": "idvs", "Grant": "grants", "Direct payment": "direct_payments",
          "Loan": "loans", "Other": "other"}
# What each group calls the fields this series shares: start, end, amount
OWN = {"IDV": ("Start Date", "Last Date to Order", "Award Amount"), "Loan": ("Issued Date", None, "Loan Value")}
SHARED = ["Award ID", "Recipient Name", "Recipient UEI", "Awarding Agency", "Awarding Sub Agency", "Funding Agency",
          "Funding Sub Agency", "Total Outlays", "Last Modified Date", "Base Obligation Date", "Description",
          "generated_internal_id", "NAICS", "PSC", "Place of Performance State Code"]
EARLIEST = "2007-10-01"  # the first fiscal year the search covers
PER = 100                # the most records one request returns


def _call(path, body=None):
    try:
        if body is None:
            return http_json(f"{API}{path}", timeout=90, tries=3)
        return http_json(f"{API}{path}", method="POST", json_body=body, timeout=90, tries=3)
    except Exception as e:
        raise SourceError(f"USAspending {path}: {type(e).__name__}: {str(e)[:200]}")


@functools.lru_cache(maxsize=256)
def _agency(kind, tier, given):
    """The agencies USAspending lists whose name holds every word given: their names at that tier."""
    want, out = set(names.tokens(given)), []
    for r in _call(f"/autocomplete/{kind}_agency/", {"search_text": given, "limit": 50}).get("results") or []:
        a = r.get(f"{tier}_agency") or {}
        top = tier == "toptier"
        if bool(r.get("toptier_flag")) == top and a.get("name") and (want <= set(names.tokens(a["name"])) or
                                                                     given.casefold() == (a.get("abbreviation") or "").casefold()):
            out.append(a["name"])
    return tuple(dict.fromkeys(out))


def _filters(s, dated):
    """The API's filters for a search, from the fields both awards and transactions share; None when a name
    given matches nothing USAspending lists. dated is the field its time_period filters."""
    f, text = {}, [x for x in s.any("recipient") + s.any("recipient_uei") if x]
    if text:
        f["recipient_search_text"] = text
    agencies = []
    for field, kind, tier in (("awarding_agency", "awarding", "toptier"), ("awarding_sub_agency", "awarding", "subtier"),
                              ("funding_agency", "funding", "toptier"), ("funding_sub_agency", "funding", "subtier")):
        given = s.any(field) if s.series.field(field) else []
        if given:
            found = [n for g in given for n in _agency(kind, tier, g)]
            if not found:
                return None
            agencies += [{"type": kind, "tier": tier, "name": n} for n in found]
    if agencies:
        f["agencies"] = agencies
    ids = s.any("award_id")
    if ids:
        f["award_ids"] = ids
    naics, psc, states = s.any("naics"), s.any("psc"), s.any("place_of_performance_state")
    if naics:
        f["naics_codes"] = {"require": naics}
    if psc:
        f["psc_codes"] = psc
    if states:
        f["place_of_performance_locations"] = [{"country": "USA", "state": x.upper()} for x in states]
    words = " ".join([s.query()] + s.peek("description").get("is", [])[:1]).strip()  # the registry checks description
    if words:
        f["keywords"] = [words]
    lo, hi = s.between(dated[0])
    if lo or hi:
        f["time_period"] = [{"start_date": max(lo or EARLIEST, EARLIEST), "end_date": hi or "2099-12-31", **dated[1]}]
    return f


def _code(x):
    return (x or {}).get("code") if isinstance(x, dict) else x


# ---------- awards ----------

def _award(x, group):
    start, end, amount = OWN.get(group, ("Start Date", "End Date", "Award Amount"))
    gid = x.get("generated_internal_id")
    return {"id": gid, "url": f"https://www.usaspending.gov/award/{gid}", "award_id": x.get("Award ID"),
            "award_type": group, "recipient": x.get("Recipient Name"), "recipient_uei": x.get("Recipient UEI"),
            "awarding_agency": x.get("Awarding Agency"), "awarding_sub_agency": x.get("Awarding Sub Agency"),
            "funding_agency": x.get("Funding Agency"), "funding_sub_agency": x.get("Funding Sub Agency"),
            "start_date": x.get(start), "end_date": x.get(end) if end else None,
            "date_signed": x.get("Base Obligation Date"), "last_modified": (x.get("Last Modified Date") or "")[:10] or None,
            "award_amount": x.get(amount), "total_outlays": x.get("Total Outlays"), "naics": _code(x.get("NAICS")),
            "psc": _code(x.get("PSC")), "place_of_performance_state": x.get("Place of Performance State Code"),
            "description": x.get("Description")}


def _search_awards(s):
    groups = s.any("award_type") or list(GROUPS)
    scan = next((f for f in ("end_date", "start_date") if s.peek(f)), None)  # a date range read in that date's order
    dated = ("last_modified", {"date_type": "last_modified_date"}) if s.peek("last_modified") or not s.peek("date_signed") \
        else ("date_signed", {"date_type": "date_signed"})
    f = _filters(s, dated)
    if f is None:
        return page([], total=0)
    amount = s.peek("award_amount")  # the API takes whole dollars: it narrows, and the registry applies the exact bounds
    if amount:
        if all(float(v).is_integer() for v in amount.values()):
            s.between("award_amount")
        f["award_amounts"] = [{k: v for k, v in (("lower_bound", math.floor(amount["lo"]) if "lo" in amount else None),
                                                  ("upper_bound", math.ceil(amount["hi"]) if "hi" in amount else None)) if v is not None}]
    since, until = s.between(scan) if scan else (None, None)
    at = s.cursor or {"g": 0, "id": None, "v": None, "page": 1}
    total = None
    if s.cursor is None and not scan and f:  # the count of every award in the database takes the API eight seconds
        counts = _call("/search/spending_by_award_count/", {"filters": f}).get("results") or {}
        total = sum(counts.get(COUNTS[g]) or 0 for g in groups)
    while at["g"] < len(groups):
        group = groups[at["g"]]
        start, end, amount = OWN.get(group, ("Start Date", "End Date", "Award Amount"))
        by = (end if scan == "end_date" else start) if scan else amount
        if scan and not by:  # a group without that date has nothing in a range of it
            at = {"g": at["g"] + 1, "id": None, "v": None, "page": 1}
            continue
        # By amount the API continues from the last record's sort value; by date it continues only by page number.
        per = PER if scan else max(1, min(s.size, PER))
        body = {"filters": f | {"award_type_codes": GROUPS[group]}, "limit": per, "page": at["page"] if scan else 1,
                "fields": SHARED + [x for x in (start, end, amount) if x], "sort": by, "order": "desc"}
        if at["id"] is not None and not scan:
            body |= {"last_record_unique_id": at["id"], "last_record_sort_value": at["v"]}
        try:
            got = _call("/search/spending_by_award/", body)
        except SourceError:
            if scan and not f:
                raise SourceError(f"USAspending can't put every award in order of {scan.replace('_', ' ')} to find a range "
                                  "of it. Give a recipient, an agency or another filter with the date range.")
            raise
        rows, meta = got.get("results") or [], got.get("page_metadata") or {}
        more = bool(meta.get("hasNext")) and (scan or meta.get("last_record_unique_id") is not None)
        out = [_award(x, group) for x in rows]
        if scan:
            dates = [r[scan] for r in out if r[scan]]
            if since and dates and min(dates) < since:
                more = False  # in this order, every later award is earlier than the range
            out = [r for r in out if r[scan] and (not since or r[scan] >= since) and (not until or r[scan] <= until)]
        nxt = {"g": at["g"], "id": meta.get("last_record_unique_id"), "v": meta.get("last_record_sort_value"),
               "page": at["page"] + 1} if more else {"g": at["g"] + 1, "id": None, "v": None, "page": 1}
        return page(out, nxt if nxt["g"] < len(groups) else None, total,
                    f"latest {scan.replace('_', ' ')} first" if scan else "largest first")
    return page([], total=total)


def _pages(path, body, pages):
    out, more = [], False
    for page in range(1, pages + 1):
        r = _call(path, {**body, "limit": 100, "page": page})
        out += r.get("results", [])
        if not r.get("page_metadata", {}).get("hasNext"):
            break
    else:
        more = True
    return out, more


def _accounts(funding):
    """Funding by federal account, at the source's grain. A funding line is an account, program activity,
    object class and disaster code; within one line the source reports several rows for the same
    fiscal period (one per appropriation behind the account), and they add up. Obligations
    (transaction_obligated_amount) are amounts for their reporting period. Outlays (gross_outlay_amount)
    are fiscal-year-to-date: a fiscal year counts each line's latest period, its rows summed."""
    period_of = lambda f: f.get("reporting_fiscal_month") or (f.get("reporting_fiscal_quarter") or 0) * 3
    accounts, outlays = {}, {}
    for f in funding:
        acct = (f.get("federal_account"), f.get("account_title"), f.get("funding_agency_name"))
        fy, period = f.get("reporting_fiscal_year"), period_of(f)
        y = accounts.setdefault(acct, {}).setdefault(fy, {"obligated": 0.0, "outlays": 0.0, "through_period": 0})
        y["obligated"] += f.get("transaction_obligated_amount") or 0.0
        y["through_period"] = max(y["through_period"], period)
        if f.get("gross_outlay_amount") is not None:
            line = (acct, f.get("program_activity_code"), f.get("object_class"), f.get("disaster_emergency_fund_code"), fy)
            by_period = outlays.setdefault(line, {})
            by_period[period] = by_period.get(period, 0.0) + f["gross_outlay_amount"]
    for (acct, *_, fy), by_period in outlays.items():
        accounts[acct][fy]["outlays"] += by_period[max(by_period)]
    out = []
    for (acct, title, agency), years in accounts.items():
        by_year = [{"fiscal_year": fy, "obligated": round(v["obligated"], 2), "outlays": round(v["outlays"], 2),
                    "through_period": v["through_period"]} for fy, v in sorted(years.items(), key=lambda kv: kv[0] or 0)]
        out.append({"federal_account": acct, "title": title, "funding_agency": agency,
                    "obligated": round(sum(y["obligated"] for y in by_year), 2),
                    "outlays": round(sum(y["outlays"] for y in by_year), 2), "by_fiscal_year": by_year})
    return sorted(out, key=lambda a: -abs(a["obligated"]))


def _read_award(gid):
    """An award with its document: its period of performance, values, parent, funding by account, and modifications."""
    d = _call(f"/awards/{gid}/")
    funding, funding_more = _pages("/awards/funding/", {"award_id": gid}, 100)
    mods, _ = _pages("/transactions/", {"award_id": gid, "sort": "action_date", "order": "desc"}, 3)
    accounts = _accounts(funding)
    mods = [{"date": m.get("action_date"), "modification": m.get("modification_number"),
             "action": m.get("action_type_description"), "obligation": m.get("federal_action_obligation"),
             "description": (m.get("description") or "")[:200]} for m in mods]
    pop = d.get("period_of_performance") or {}
    rec = d.get("recipient") or {}
    agency = lambda a: ((d.get(a) or {}).get("toptier_agency") or {}).get("name")
    sub = lambda a: ((d.get(a) or {}).get("subtier_agency") or {}).get("name")
    parent = d.get("parent_award") or {}
    data = {"title": f"{d.get('piid') or d.get('fain') or gid} to {rec.get('recipient_name')}",
            "award_id": d.get("piid") or d.get("fain") or d.get("uri"), "generated_id": gid,
            "type": d.get("type_description"), "description": d.get("description"),
            "recipient": rec.get("recipient_name"), "recipient_uei": rec.get("recipient_uei"),
            "parent_recipient": rec.get("parent_recipient_name"),
            "awarding_agency": agency("awarding_agency"), "awarding_sub_agency": sub("awarding_agency"),
            "funding_agency": agency("funding_agency"), "funding_sub_agency": sub("funding_agency"),
            "start": pop.get("start_date"), "current_end": pop.get("end_date"),
            "potential_end": (pop.get("potential_end_date") or "")[:10] or None,
            "date_signed": d.get("date_signed"), "total_obligation": d.get("total_obligation"),
            "base_exercised_options": d.get("base_exercised_options"),
            "base_and_all_options": d.get("base_and_all_options"),
            "total_outlays": d.get("total_account_outlay"),
            "parent_award": parent.get("piid") or parent.get("generated_unique_award_id"),
            "funding_accounts": accounts, "funding_records": len(funding), "funding_truncated": funding_more,
            "modifications": mods}
    lines = [f"Award {data['award_id']} ({data['type']}) to {data['recipient']} (UEI {data['recipient_uei']}"
             + (f"; parent {data['parent_recipient']}" if data["parent_recipient"] else "") + ").",
             f"Description: {data['description']}",
             f"Awarding agency: {data['awarding_agency']} / {data['awarding_sub_agency']}. "
             f"Funding agency: {data['funding_agency']} / {data['funding_sub_agency']}.",
             f"Period of performance: start {data['start']}, current end {data['current_end']}, potential end "
             f"{data['potential_end']}. Signed {data['date_signed']}.",
             f"Total obligation {money(data['total_obligation'])}; base and exercised options "
             f"{money(data['base_exercised_options'])}; base and all options {money(data['base_and_all_options'])}; "
             f"outlays {money(data['total_outlays'])}.",
             f"Parent award: {data['parent_award'] or 'none'}.", "",
             "Funding accounts (federal account spending reports). Obligations are summed over reporting periods; "
             "outlays are year-to-date, so each fiscal year counts each funding line's latest period"
             + (" (the first 10,000 funding records only)" if funding_more else "") + ". "
             f"Across accounts: obligated {money(sum(a['obligated'] for a in accounts))} against the award's "
             f"{money(data['total_obligation'])}; outlays {money(sum(a['outlays'] for a in accounts))} against its "
             f"{money(data['total_outlays'])}.:"]
    for a in accounts:
        lines.append(f"  {a['federal_account']} {a['title']} ({a['funding_agency']}): obligated {money(a['obligated'])}, "
                     f"outlays {money(a['outlays'])}")
        lines += [f"    FY{y['fiscal_year']} through period {y['through_period']}: obligated {money(y['obligated'])}, "
                  f"outlays {money(y['outlays'])}" for y in a["by_fiscal_year"]]
    if not accounts:
        lines.append("  none reported")
    lines += ["", "Modifications, newest first (date, number, action, obligation, description):"]
    lines += [f"  {m['date']} {m['modification']} {m['action']}: {money(m['obligation'])}. {m['description']}" for m in mods]
    row = _award({"generated_internal_id": gid, "Award ID": data["award_id"], "Recipient Name": data["recipient"],
                  "Recipient UEI": data["recipient_uei"], "Awarding Agency": data["awarding_agency"],
                  "Awarding Sub Agency": data["awarding_sub_agency"], "Funding Agency": data["funding_agency"],
                  "Funding Sub Agency": data["funding_sub_agency"], "Start Date": data["start"], "End Date": data["current_end"],
                  "Base Obligation Date": data["date_signed"], "Last Modified Date": (d.get("latest_transaction_contract_data") or {}).get("last_modified") or "",
                  "Award Amount": data["total_obligation"], "Total Outlays": data["total_outlays"],
                  "NAICS": (d.get("naics_hierarchy") or {}).get("base_code"), "PSC": (d.get("psc_hierarchy") or {}).get("base_code"),
                  "Place of Performance State Code": (d.get("place_of_performance") or {}).get("state_code"),
                  "Description": data["description"]}, _group(d))
    return {**row, "document": "\n".join(lines), "published": d.get("date_signed"),
            "links": [{"text": "Award on USAspending", "url": f"https://www.usaspending.gov/award/{gid}"}]}


def _group(d):
    """The award-type group of an award's detail record."""
    if d.get("category") == "idv":
        return "IDV"
    return next((g for g, codes in GROUPS.items() if d.get("type") in codes), "Other")


# ---------- transactions ----------

TX = ["Action Date", "Award ID", "Recipient Name", "Recipient UEI", "Transaction Amount", "Awarding Agency",
      "Awarding Sub Agency", "Funding Agency", "Funding Sub Agency", "Mod", "Transaction Description", "Action Type",
      "generated_internal_id"]


def _tx_id(award, mod, date, amount):
    return f"{award}~{mod or ''}~{date or ''}~{'' if amount is None else amount}"


def _transaction(x, group):
    gid = x.get("generated_internal_id")
    return {"id": _tx_id(gid, x.get("Mod"), x.get("Action Date"), x.get("Transaction Amount")),
            "url": f"https://www.usaspending.gov/award/{gid}", "award": gid, "award_id": x.get("Award ID"),
            "award_type": group, "modification_number": x.get("Mod"), "action_date": x.get("Action Date"),
            "action_type": x.get("Action Type"), "transaction_amount": x.get("Transaction Amount"),
            "recipient": x.get("Recipient Name"), "recipient_uei": x.get("Recipient UEI"),
            "awarding_agency": x.get("Awarding Agency"), "awarding_sub_agency": x.get("Awarding Sub Agency"),
            "funding_agency": x.get("Funding Agency"), "funding_sub_agency": x.get("Funding Sub Agency"),
            "description": x.get("Transaction Description")}


def _history(gid):
    """Every transaction of one award, newest first, with the award's own recipient and agencies."""
    d = _call(f"/awards/{gid}/")
    rows, _ = _pages("/transactions/", {"award_id": gid, "sort": "action_date", "order": "desc"}, 100)
    rec, group = d.get("recipient") or {}, _group(d)
    name = lambda a, tier: ((d.get(a) or {}).get(f"{tier}_agency") or {}).get("name")  # noqa: E731
    return [_transaction({"generated_internal_id": gid, "Award ID": d.get("piid") or d.get("fain") or d.get("uri"),
                          "Mod": m.get("modification_number"), "Action Date": m.get("action_date"),
                          "Action Type": m.get("action_type"), "Recipient Name": rec.get("recipient_name"),
                          "Recipient UEI": rec.get("recipient_uei"), "Transaction Description": m.get("description"),
                          "Transaction Amount": m.get("federal_action_obligation") if m.get("federal_action_obligation") is not None
                          else m.get("face_value_loan_guarantee"),
                          "Awarding Agency": name("awarding_agency", "toptier"), "Awarding Sub Agency": name("awarding_agency", "subtier"),
                          "Funding Agency": name("funding_agency", "toptier"), "Funding Sub Agency": name("funding_agency", "subtier")},
                         group) for m in rows]


def _search_transactions(s):
    award = s.one("award")
    if award:
        return page(_history(award), order="newest first")
    groups = s.any("award_type") or list(GROUPS)
    f = _filters(s, ("action_date", {}))
    if f is None:
        return page([], total=0)
    f.setdefault("time_period", [{"start_date": EARLIEST, "end_date": "2099-12-31"}])
    at = s.cursor or {"g": 0, "page": 1}
    group = groups[at["g"]]
    got = _call("/search/spending_by_transaction/", {"filters": f | {"award_type_codes": GROUPS[group]}, "fields": TX,
                                                      "limit": PER, "page": at["page"], "sort": "Action Date", "order": "desc"})
    more = bool((got.get("page_metadata") or {}).get("hasNext"))
    nxt = {"g": at["g"], "page": at["page"] + 1} if more else {"g": at["g"] + 1, "page": 1}
    return page([_transaction(x, group) for x in got.get("results") or []], nxt if nxt["g"] < len(groups) else None,
                order="newest first")


def _read_transaction(given):
    for r in _history(given.split("~", 1)[0]):
        if r["id"] == given:
            return r
    raise SourceError(f"usaspending_transactions has no record {given!r}")


# ---------- funding by federal account ----------

def _funding(gid):
    rows, _ = _pages("/awards/funding/", {"award_id": gid}, 100)
    out = []
    for i, f in enumerate(rows):
        period = f.get("reporting_fiscal_month") or (f.get("reporting_fiscal_quarter") or 0) * 3
        out.append({"id": f"{gid}~{f.get('federal_account')}~{f.get('reporting_fiscal_year')}~{period}~{i}",
                    "url": f"https://www.usaspending.gov/award/{gid}", "award": gid, "federal_account": f.get("federal_account"),
                    "account_title": f.get("account_title"), "funding_agency": f.get("funding_agency_name"),
                    "awarding_agency": f.get("awarding_agency_name"), "program_activity": f.get("program_activity_name"),
                    "object_class": f.get("object_class_name"), "disaster_code": f.get("disaster_emergency_fund_code"),
                    "fiscal_year": f.get("reporting_fiscal_year"), "fiscal_period": period,
                    "obligated": f.get("transaction_obligated_amount"), "outlays_fytd": f.get("gross_outlay_amount")})
    return out


def _read_funding(given):
    for r in _funding(given.split("~", 1)[0]):
        if r["id"] == given:
            return r
    raise SourceError(f"usaspending_award_funding has no record {given!r}")


def _newest(series):
    got = _call("/search/spending_by_award/", {"filters": {"award_type_codes": GROUPS["Contract"]}, "limit": 1, "page": 1,
                                               "fields": ["Award ID", "Last Modified Date"], "sort": "Last Modified Date", "order": "desc"})
    return EARLIEST, ((got.get("results") or [{}])[0].get("Last Modified Date") or "")[:10] or None


TREASURY = "U.S. Department of the Treasury (USAspending.gov)"
PARTIES = (F("recipient", "name"), F("recipient_uei", "identifier"), F("awarding_agency", "name"),
           F("awarding_sub_agency", "name"), F("funding_agency", "name"), F("funding_sub_agency", "name"))
SERIES = [
    Series(
        id="usaspending_awards", title="federal awards",
        record="a federal prime award: a contract, indefinite delivery vehicle (IDV), grant, loan, direct payment or other assistance",
        creator="federal agencies", publisher=TREASURY, jurisdiction="federal", since=EARLIEST, coverage=_newest,
        fields=(F("award_id", "identifier"), F("award_type", tuple(GROUPS)), *PARTIES, F("start_date", "date"),
                F("end_date", "date"), F("date_signed", "date"), F("last_modified", "date"), F("award_amount", "amount"),
                F("total_outlays", "amount"), F("naics", "identifier"), F("psc", "identifier"),
                F("place_of_performance_state", "place"), F("description", "text")),
        search=_search_awards, read=_read_award),
    Series(
        id="usaspending_transactions", title="federal award transactions",
        record="an action on a federal award: the award itself, or a modification to it, with the amount it obligates",
        creator="federal agencies", publisher=TREASURY, jurisdiction="federal", since=EARLIEST, coverage=_newest,
        fields=(F("award", "identifier"), F("award_id", "identifier"), F("award_type", tuple(GROUPS)),
                F("modification_number", "identifier"), F("action_date", "date"), F("action_type", "identifier"),
                F("transaction_amount", "amount"), *PARTIES, F("description", "text")),
        search=_search_transactions, read=_read_transaction),
    Series(
        id="usaspending_award_funding", title="federal awards' funding by account",
        record="the obligations and outlays on one federal award from one federal account in one reporting period",
        creator="federal agencies", publisher=TREASURY, jurisdiction="federal", since="2017",
        fields=(F("award", "identifier", required=True), F("federal_account", "identifier"), F("account_title", "text"),
                F("funding_agency", "name"), F("awarding_agency", "name"), F("program_activity", "text"),
                F("object_class", "text"), F("disaster_code", "identifier"), F("fiscal_year", "number"),
                F("fiscal_period", "number"), F("obligated", "amount"), F("outlays_fytd", "amount")),
        search=lambda s: page(_funding(s.one("award") or s.any("award")[0])), read=_read_funding),
]
