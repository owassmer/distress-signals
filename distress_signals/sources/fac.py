"""Two series from the Federal Audit Clearinghouse: Single Audits and the federal awards each one reports.

A non-federal entity that spends $1,000,000 or more of federal awards in a fiscal year ($750,000 before fiscal
years beginning in October 2024) has a Single Audit under 2 CFR 200 Subpart F and files it with the FAC: states,
cities, counties, airports and transit authorities, school districts, universities, hospitals and other
nonprofits. The FAC's API (api.data.gov key) applies identifiers, listed values and dates, and matches a word in
a name; the registry applies the rest. A Single Audit's reporting package (the audited financial statements,
the schedule of expenditures of federal awards and the auditor's reports) is read from the FAC's address for it,
which redirects to a copy signed for the moment.
"""

import os
from urllib.parse import quote

import requests

from .. import names
from ..util import Missing, SourceError, Unreadable, http_json
from . import F, Series, page, web

API = "https://api.fac.gov"
PDF = "https://app.fac.gov/dissemination/report/pdf/"
STEP = 1000  # rows asked of the API at once; it gives up to 20,000
YES_NO = ("yes", "no")
OPINIONS = ("unmodified_opinion", "qualified_opinion", "adverse_opinion", "disclaimer_of_opinion", "not_gaap")
ENTITIES = ("state", "local", "tribal", "higher-ed", "non-profit", "unknown")


def _api(table, params):
    try:
        return http_json(f"{API}/{table}", params=params, headers={"X-Api-Key": os.environ["DATA_GOV_API_KEY"]}, tries=3, timeout=60)
    except requests.RequestException as e:
        status = getattr(getattr(e, "response", None), "status_code", None)
        raise SourceError(f"The Federal Audit Clearinghouse did not answer ({status or type(e).__name__})") from None


def _yes_no(value):
    return {"yes": "yes", "y": "yes", "no": "no", "n": "no"}.get(str(value or "").strip().lower())


def _number(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ---------- the PostgREST filters a search gives the API ----------

def _take(s, columns, stored):
    """The API's filters for what a search gives: an identifier or listed value equal, a date within its range, the
    longest word of a name or text in it. What it does not take, the registry applies."""
    params, ranges = {}, []
    for f in s.series.fields:
        column, given = columns.get(f.name), s.peek(f.name)
        if not column or not given:
            continue
        if f.type in ("date", "number", "amount"):
            lo, hi = s.between(f.name)
            ranges += [f"{column}.gte.{_plain(lo)}"] if lo is not None else []
            ranges += [f"{column}.lte.{_plain(hi)}"] if hi is not None else []
        elif f.type == "identifier" or (f.listed and not f.many):
            values = [stored.get(v, v) for v in s.any(f.name)]
            params[column] = "in.(" + ",".join(f'"{v}"' for v in values) + ")" if len(values) > 1 else "eq." + values[0]
        elif f.type in ("name", "text", "place") and len(given.get("is", [])) == 1:
            words = names.tokens(given["is"][0])
            if words:
                params[column] = f"ilike.*{max(words, key=len)}*"  # the registry checks every word
    if ranges:
        params["and"] = "(" + ",".join(ranges) + ")"
    return params


def _plain(x):
    return int(x) if isinstance(x, float) and x.is_integer() else x


def _rows(table, columns, order, s, stored, extra=None):
    at = s.cursor or 0
    params = {"select": ",".join(sorted(set(columns.values()))), "order": order, "limit": STEP, "offset": at // STEP * STEP,
              **_take(s, columns, stored), **(extra or {})}
    got = _api(table, params)
    part = got[at % STEP:at % STEP + s.size]
    more = len(got) == STEP or at % STEP + len(part) < len(got)
    return part, at + len(part) if more and part else None


# ---------- Single Audits ----------

AUDIT = {"report_id": "report_id", "auditee": "auditee_name", "auditee_uei": "auditee_uei", "auditee_ein": "auditee_ein",
         "city": "auditee_city", "state": "auditee_state", "entity_type": "entity_type", "audit_year": "audit_year",
         "fiscal_year_start": "fy_start_date", "fiscal_year_end": "fy_end_date", "accepted_date": "fac_accepted_date",
         "auditor": "auditor_firm_name", "gaap_results": "gaap_results", "going_concern": "is_going_concern_included",
         "material_weakness": "is_internal_control_material_weakness_disclosed",
         "material_noncompliance": "is_material_noncompliance_disclosed", "low_risk_auditee": "is_low_risk_auditee",
         "federal_expended": "total_amount_expended"}


def _audit(r):
    out = {"id": r["report_id"], "url": PDF + quote(r["report_id"], safe="")}
    for field, column in AUDIT.items():
        value = r.get(column)
        if field in ("going_concern", "material_weakness", "material_noncompliance", "low_risk_auditee"):
            value = _yes_no(value)
        elif field == "gaap_results":
            value = [v for v in (value or "").split(",") if v]
        elif field == "audit_year":
            value = _number(value)
        out[field] = value if value not in ("", None, []) else None
    return out


def _search_audits(s):
    # gaap_results holds several values in one column: the registry applies it
    columns = {k: v for k, v in AUDIT.items() if k != "gaap_results"} | {"_gaap": "gaap_results"}
    rows, nxt = _rows("general", columns, "fac_accepted_date.desc,report_id", s, {"yes": "Yes", "no": "No"}, {"is_public": "eq.true"})
    return page([_audit(r) for r in rows], nxt, order="latest accepted first")


def _read_audit(key):
    rows = _api("general", {"report_id": f"eq.{key}", "is_public": "eq.true", "limit": 1})
    if not rows:
        raise SourceError(f"The Federal Audit Clearinghouse has no public Single Audit {key}")
    record = _audit(rows[0])
    try:
        text = web.read(record["url"])["text"]
    except (Missing, Unreadable) as e:  # the package is gone, or arrived without text
        text = f"The reporting package's text could not be read: {e}"
    return {**record, "document": text, "published": record["accepted_date"]}


# ---------- federal awards ----------

AWARD = {"report_id": "report_id", "award_reference": "award_reference", "auditee_uei": "auditee_uei", "audit_year": "audit_year",
         "accepted_date": "fac_accepted_date", "agency_prefix": "federal_agency_prefix", "award_extension": "federal_award_extension",
         "program": "federal_program_name", "amount_expended": "amount_expended", "major_program": "is_major",
         "direct": "is_direct", "passthrough_amount": "passthrough_amount", "cluster": "cluster_name", "findings": "findings_count"}


def _award(r):
    alns = f"{r.get('federal_agency_prefix') or ''}.{r.get('federal_award_extension') or ''}".strip(".")
    return {"id": f"{r['report_id']}/{r['award_reference']}", "url": PDF + quote(r["report_id"], safe=""),
            "report_id": r["report_id"], "award_reference": r["award_reference"], "auditee_uei": r.get("auditee_uei"),
            "audit_year": _number(r.get("audit_year")), "accepted_date": r.get("fac_accepted_date"),
            "assistance_listing": alns or None, "program": r.get("federal_program_name"), "cluster": r.get("cluster_name") or None,
            "amount_expended": r.get("amount_expended"), "major_program": _yes_no(r.get("is_major")), "direct": _yes_no(r.get("is_direct")),
            "passthrough_amount": r.get("passthrough_amount"), "findings": r.get("findings_count")}


def _search_awards(s):
    extra = {}
    listings = s.any("assistance_listing")
    if listings:
        # the Assistance Listing Number is kept in two columns: the agency's two digits, and the program's three
        parts = [x.split(".", 1) for x in listings]
        if any(len(p) != 2 for p in parts):
            raise SourceError("An Assistance Listing Number is the agency's two digits and the program's, as in 20.106")
        extra["or"] = "(" + ",".join(f"and(federal_agency_prefix.eq.{a},federal_award_extension.eq.{b})" for a, b in parts) + ")"
    columns = {k: v for k, v in AWARD.items() if k not in ("agency_prefix", "award_extension")}
    columns |= {"_prefix": "federal_agency_prefix", "_extension": "federal_award_extension"}
    rows, nxt = _rows("federal_awards", columns, "fac_accepted_date.desc,report_id,award_reference", s, {"yes": "Y", "no": "N"}, extra)
    return page([_award(r) for r in rows], nxt, order="latest accepted first")


def _read_award(key):
    report, _, reference = key.partition("/")
    rows = _api("federal_awards", {"report_id": f"eq.{report}", "award_reference": f"eq.{reference}", "limit": 1})
    if not rows:
        raise SourceError(f"The Federal Audit Clearinghouse has no award {reference} in {report}")
    record = _award(rows[0])
    lines = [f"{k}: {v}" for k, v in rows[0].items() if v not in (None, "")]
    return {**record, "document": "\n".join(lines), "published": record["accepted_date"],
            "links": [{"text": "The Single Audit's reporting package", "url": record["url"]}]}


FAC = "General Services Administration (Federal Audit Clearinghouse)"
KEY = ("DATA_GOV_API_KEY",)
SERIES = [
    Series(
        id="fac_single_audits", title="Single Audits",
        record="a Single Audit of a state or local government, tribe, university or nonprofit that spent $1 million or more of "
               "federal awards in a fiscal year, with its audited financial statements",
        creator="non-federal entities that expend federal awards, and their auditors", publisher=FAC, jurisdiction="federal",
        since="2016", requires=KEY,
        fields=(F("report_id", "identifier"), F("auditee", "name"), F("auditee_uei", "identifier"), F("auditee_ein", "identifier"),
                F("city", "place"), F("state", "place"), F("entity_type", ENTITIES), F("audit_year", "number"),
                F("fiscal_year_start", "date"), F("fiscal_year_end", "date"), F("accepted_date", "date"), F("auditor", "name"),
                F("gaap_results", OPINIONS, many=True), F("going_concern", YES_NO), F("material_weakness", YES_NO),
                F("material_noncompliance", YES_NO), F("low_risk_auditee", YES_NO), F("federal_expended", "amount")),
        search=_search_audits, read=_read_audit),
    Series(
        id="fac_federal_awards", title="federal awards in Single Audits",
        record="a federal award a Single Audit reports the auditee expended in the fiscal year: one line of its Schedule of "
               "Expenditures of Federal Awards",
        creator="non-federal entities that expend federal awards", publisher=FAC, jurisdiction="federal", since="2016", requires=KEY,
        fields=(F("report_id", "identifier"), F("award_reference", "identifier"), F("auditee_uei", "identifier"),
                F("audit_year", "number"), F("accepted_date", "date"), F("assistance_listing", "identifier"), F("program", "text"),
                F("cluster", "text"), F("amount_expended", "amount"), F("major_program", YES_NO), F("direct", YES_NO),
                F("passthrough_amount", "amount"), F("findings", "number")),
        search=_search_awards, read=_read_award),
]
