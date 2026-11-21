"""Federal awards (USAspending): paginated award search, and each award's detail with its period of
performance, option value, recipient, funding accounts and modifications."""

import datetime as dt
import re
from ..util import SourceError, http_json, money, today

API = "https://api.usaspending.gov/api/v2"
AWARD_TYPES = {"contracts": ["A", "B", "C", "D"],
               "idvs": ["IDV_A", "IDV_B", "IDV_B_A", "IDV_B_B", "IDV_B_C", "IDV_C", "IDV_D", "IDV_E"],
               "grants": ["02", "03", "04", "05"], "loans": ["07", "08"], "direct_payments": ["06", "10"],
               "other": ["09", "11"]}
FIELDS = ["Award ID", "Recipient Name", "Recipient UEI", "Awarding Agency", "Awarding Sub Agency", "Award Amount",
          "Start Date", "End Date", "Description", "generated_internal_id"]


def _call(path, body=None):
    try:
        if body is None:
            return http_json(f"{API}{path}", timeout=90, tries=3)
        return http_json(f"{API}{path}", method="POST", json_body=body, timeout=90, tries=3)
    except Exception as e:
        raise SourceError(f"USAspending {path}: {type(e).__name__}: {str(e)[:200]}")


EARLIEST = "2007-10-01"  # the first fiscal year USAspending covers
MAX_PAGES = 50           # 5,000 awards read at most in one search


def _row(x):
    gid = x.get("generated_internal_id")
    return {"record_id": f"usaspending:award:{gid}", "award_id": x.get("Award ID"),
            "recipient": x.get("Recipient Name"), "recipient_uei": x.get("Recipient UEI"),
            "agency": x.get("Awarding Agency"), "sub_agency": x.get("Awarding Sub Agency"),
            "amount": x.get("Award Amount"), "start": x.get("Start Date"), "end": x.get("End Date"),
            "description": (x.get("Description") or "")[:200], "url": f"https://www.usaspending.gov/award/{gid}"}


def search_awards(query=None, agency=None, since=None, until=None, award_types="contracts", keyword=None,
                  active_on=None, max_results=300):
    """{results, scanned, qualifying, complete}: awards by recipient name (query), keyword and awarding
    agency, the largest first.

    With active_on, only awards whose current end date is on or after it. The search reads in
    end-date order, which reaches every such award before the first that ended earlier; it reads all of
    them (up to MAX_PAGES), then returns the largest, so an award ending soon competes with one ending
    late. Without since, it reaches back to 2007, so long-running contracts with no recent actions are
    found."""
    if award_types not in AWARD_TYPES:
        raise SourceError(f"award_types is one of {sorted(AWARD_TYPES)}")
    if not query and not keyword:
        raise SourceError("give a recipient name as the query, or a keyword filter")
    start = since or (EARLIEST if active_on else (today() - dt.timedelta(days=3 * 365)).isoformat())
    filters = {"award_type_codes": AWARD_TYPES[award_types],
               "time_period": [{"start_date": start, "end_date": until or today().isoformat()}]}
    if query:
        filters["recipient_search_text"] = [query]
    if keyword:
        filters["keywords"] = [keyword]
    if agency:
        filters["agencies"] = [{"type": "awarding", "tier": "toptier", "name": agency}]
    sort = "End Date" if active_on else "Award Amount"
    found, scanned, no_end, complete, page = [], 0, 0, False, 1
    while page <= MAX_PAGES:
        r = _call("/search/spending_by_award/", {"filters": filters, "fields": FIELDS, "limit": 100, "page": page,
                                                  "sort": sort, "order": "desc"})
        for x in r.get("results", []):
            scanned += 1
            if active_on:
                end = x.get("End Date") or ""
                if not end:
                    no_end += 1
                    continue
                if end < active_on:
                    complete = True
                    break
            found.append(_row(x))
        if complete or (not active_on and len(found) >= max_results):
            break
        if not r.get("page_metadata", {}).get("hasNext"):
            complete = True
            break
        page += 1
    found.sort(key=lambda row: -(row["amount"] or 0))
    out = {"results": found[:max_results], "scanned": scanned, "complete": complete}
    if active_on:
        out["qualifying"] = len(found)
        if no_end:
            out["without_end_date"] = no_end
        if not complete:
            out["note"] = (f"Read {scanned} awards in end-date order without reaching the first that ended before "
                           f"{active_on}; the largest of those read are returned. Narrow by agency or keyword to read them all.")
    elif not complete:
        out["note"] = f"The {len(out['results'])} largest of more awards that match; narrow the search or raise max_results."
    return out


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


def award(gid):
    """The observation for one award."""
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
             f"{money(data['total_outlays'])}. The rows are in the workspace table award_funding:"]
    for a in accounts:
        lines.append(f"  {a['federal_account']} {a['title']} ({a['funding_agency']}): obligated {money(a['obligated'])}, "
                     f"outlays {money(a['outlays'])}")
        lines += [f"    FY{y['fiscal_year']} through period {y['through_period']}: obligated {money(y['obligated'])}, "
                  f"outlays {money(y['outlays'])}" for y in a["by_fiscal_year"]]
    if not accounts:
        lines.append("  none reported")
    lines += ["", "Modifications, newest first (date, number, action, obligation, description):"]
    lines += [f"  {m['date']} {m['modification']} {m['action']}: {money(m['obligation'])}. {m['description']}" for m in mods]
    aid = data["award_id"]
    rows = {"awards": [{"award_id": aid, "generated_id": gid, "award_type": data["type"], "recipient": data["recipient"],
                        "recipient_uei": data["recipient_uei"], "parent_recipient": data["parent_recipient"],
                        "awarding_agency": data["awarding_agency"], "awarding_sub_agency": data["awarding_sub_agency"],
                        "funding_agency": data["funding_agency"], "funding_sub_agency": data["funding_sub_agency"],
                        "start_date": data["start"], "current_end_date": data["current_end"],
                        "potential_end_date": data["potential_end"], "date_signed": data["date_signed"],
                        "total_obligation": data["total_obligation"], "base_exercised_options": data["base_exercised_options"],
                        "base_and_all_options": data["base_and_all_options"], "total_outlays": data["total_outlays"],
                        "parent_award": data["parent_award"], "description": data["description"]}],
            "award_funding": [{"award_id": aid, "federal_account": f.get("federal_account"), "account_title": f.get("account_title"),
                               "funding_agency": f.get("funding_agency_name"), "program_activity_code": f.get("program_activity_code"),
                               "program_activity_name": f.get("program_activity_name"), "object_class": f.get("object_class"),
                               "object_class_name": f.get("object_class_name"), "disaster_code": f.get("disaster_emergency_fund_code"),
                               "fiscal_year": f.get("reporting_fiscal_year"),
                               "fiscal_period": f.get("reporting_fiscal_month") or (f.get("reporting_fiscal_quarter") or 0) * 3,
                               "obligated": f.get("transaction_obligated_amount"), "outlays_fytd": f.get("gross_outlay_amount")}
                              for f in funding],
            "award_modifications": [{"award_id": aid, "action_date": m["date"], "modification": m["modification"],
                                     "action": m["action"], "obligation": m["obligation"], "description": m["description"]}
                                    for m in mods]}
    return {"kind": "award", "source": "usaspending", "native_id": gid, "url": f"https://www.usaspending.gov/award/{gid}",
            "published": d.get("date_signed"), "text": "\n".join(lines), "data": data, "rows": rows}
