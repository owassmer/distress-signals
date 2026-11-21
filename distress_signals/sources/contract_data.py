"""Three series of contracts with end dates: E-Rate funding requests, city and county contracts, and the
contracts electricity sellers report to FERC.

  erate_funding_requests   USAC's open data (Socrata), which applies every filter and counts the matches.
  municipal_contracts      four city and county portals (Socrata) with their own column names, mapped onto the
                           same fields; a search asks them all and merges their rows by end date.
  eqr_contracts            FERC publishes each quarter's Electric Quarterly Reports as one archive of about
                           4,000 filings and 4 GB, one zip per respondent. A search by respondent reads that
                           respondent's filing. Any other search reads the filings in turn, a few at a time, and
                           returns a cursor: it reaches every contract, slowly.
"""
import csv
import datetime as dt
import functools
import io
import json
import re
import shutil
import tempfile
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote, unquote, urlencode
from zipfile import ZipFile

import requests
from bs4 import BeautifulSoup
from remotezip import RemoteZip

from .. import names
from ..util import SourceError, http_json, to_float
from . import F, Series, outline, page

VIEWER = "https://eqrreportviewer.ferc.gov/"
USAC = "https://opendata.usac.org/resource"
ERATE = USAC + "/qdmp-ygft.json"
FORM471 = USAC + "/9s6i-myen.json"


def _literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def _soda(endpoint, **params):
    try:
        data = http_json(endpoint, params={"$" + k: v for k, v in params.items()}, tries=3, timeout=60)
    except Exception as e:
        raise SourceError(f"{endpoint.split('/')[2]}: {type(e).__name__}: {str(e)[:160]}")
    if not isinstance(data, list):
        raise SourceError("The public data portal did not return records")
    return data


def _where(s, columns, taken=True):
    """The SoQL conditions for a search, from each field's column: words found in a name or text, an identifier
    or listed value equal, a date or amount within its range. Fields without a column are left to the registry."""
    parts = []
    for f in s.series.fields:
        column = columns.get(f.name)
        if not column or not s.peek(f.name):
            continue
        if f.ranged:
            lo, hi = s.between(f.name) if taken else (s.peek(f.name).get("lo"), s.peek(f.name).get("hi"))
            cast = column if f.type == "date" else f"{column}::number"
            parts += [f"{cast} >= {_literal(lo) if f.type == 'date' else lo}"] if lo is not None else []
            parts += [f"{cast} <= {_literal(hi + 'T23:59:59') if f.type == 'date' else hi}"] if hi is not None else []
            continue
        given = s.any(f.name) if taken else s.peek(f.name).get("is", [])
        if f.type in ("identifier",) or f.listed:
            parts.append("(" + " OR ".join(f"upper({column}::text) = {_literal(g.upper())}" for g in given) + ")")
        else:
            parts.append("(" + " OR ".join("(" + " AND ".join(f"upper({column}) like {_literal('%' + w.upper() + '%')}"
                                                              for w in (names.tokens(g) or [g])) + ")" for g in given) + ")")
    return parts


def _day(value):
    return (value or "")[:10] or None


# ---------- E-Rate funding requests ----------

ERATE_COLUMNS = {"funding_request_number": "funding_request_number", "form_version": "form_version", "funding_year": "funding_year",
                 "applicant": "organization_name", "applicant_type": "organization_entity_type_name", "state": "state",
                 "service_provider": "spin_name", "service_type": "form_471_service_type_name", "contract_type": "contract_type_name",
                 "award_date": "award_date", "service_start_date": "service_start_date",
                 "contract_expiration_date": "contract_expiration_date", "status": "form_471_frn_status_name",
                 "funding_commitment_request": "funding_commitment_request"}


def _frn(row):
    frn, version = row["funding_request_number"], row["form_version"]
    where = f"funding_request_number={_literal(frn)} AND form_version={_literal(version)}"
    out = {"id": f"{frn}/{version}", "url": ERATE + "?" + urlencode({"$where": where})}
    for field, column in ERATE_COLUMNS.items():
        value = row.get(column)
        out[field] = _day(value) if field.endswith("_date") else to_float(value) if field in ("funding_year", "funding_commitment_request") else value
    return out


def _search_erate(s):
    where = " AND ".join(_where(s, ERATE_COLUMNS))
    params = {"order": "funding_year DESC, contract_expiration_date DESC, funding_request_number, form_version",
              "offset": s.cursor or 0, "limit": max(1, min(s.size, 1000))}
    params |= {"where": where} if where else {}
    params |= {"q": s.query()} if s.words() else {}
    rows = _soda(ERATE, **params)
    total = None
    if s.cursor is None:
        total = int(_soda(ERATE, select="count(*) as n", **{k: v for k, v in params.items() if k in ("where", "q")})[0]["n"])
    return page([_frn(r) for r in rows], params["offset"] + len(rows) if len(rows) == params["limit"] else None, total,
                "latest funding year first")


def _read_erate(key):
    frn, _, version = key.partition("/")
    where = f"funding_request_number={_literal(frn)} AND form_version={_literal(version)}"
    rows = _soda(ERATE, where=where, limit=2)
    if len(rows) != 1:
        raise SourceError(f"USAC has no {version} funding request {frn}")
    row = rows[0]
    form_where = f"application_number={_literal(row['application_number'])}" + (" AND form_version='Original'" if version == "Original" else "")
    form = _soda(FORM471, where=form_where, select="application_number,form_version,certified_datetime,file_url", limit=2)
    links = [{"text": "Form 471 application record", "url": FORM471 + "?" + urlencode({"$where": form_where})}]
    for f in form:
        pdf = f.get("file_url") or {}
        url = pdf.get("url") if isinstance(pdf, dict) else pdf
        if url:
            links.append({"text": f"FCC Form 471 PDF ({f['form_version']})", "url": url})
    return {**_frn(row), "document": "\n".join(outline(row)), "links": links}


# ---------- city and county contracts ----------

# Each portal's host, dataset, and the column behind each field.
LOCAL = {
    "Chicago, IL": ("data.cityofchicago.org", "rsxa-ify5", {
        "contract_number": "purchase_order_contract_number", "vendor": "vendor_name", "description": "purchase_order_description",
        "department": "department", "contract_type": "contract_type", "start_date": "start_date", "end_date": "end_date",
        "amount": "award_amount"}),
    "Cook County, IL": ("datacatalog.cookcountyil.gov", "qh8j-6k63", {
        "contract_number": "contract_number", "vendor": "vendor_name", "description": "description", "department": "lead_department",
        "contract_type": "commodity_type", "start_date": "start_date", "end_date": "end_date", "amount": "amount"}),
    "Montgomery County, MD": ("data.montgomerycountymd.gov", "vmu2-pnrc", {
        "contract_number": "contractno", "vendor": "vendor", "description": "contractdesc", "department": "deptname",
        "contract_type": "contracttype", "start_date": "execution", "end_date": "expiration"}),
    "San Francisco, CA": ("data.sfgov.org", "cqi5-hm2d", {
        "contract_number": "contract_no", "vendor": "prime_contractor", "description": "contract_title", "department": "department",
        "contract_type": "contract_type", "start_date": "term_start_date", "end_date": "term_end_date", "amount": "agreed_amt"}),
}
SLUG = {name: re.sub(r"[^a-z]+", "_", name.lower()).strip("_") for name in LOCAL}


def _contract(name, row):
    host, dataset, columns = LOCAL[name]
    out = {"id": f"{SLUG[name]}/{row.get(':id')}", "url": f"https://{host}/d/{dataset}", "jurisdiction": name}
    for field, column in columns.items():
        value = row.get(column)
        out[field] = _day(value) if field.endswith("_date") else to_float(value) if field == "amount" else value
    return out


def _search_local(s):
    given = s.any("jurisdiction")
    chosen = [n for n in LOCAL if not given or any(set(names.tokens(g)) <= set(names.tokens(n)) for g in given)]
    at, size, words = s.cursor or {n: 0 for n in chosen}, max(1, min(s.size, 1000)), s.query()
    # a field a portal has no column for (an amount, in one county) matches nothing there
    asked = [f.name for f in s.series.fields if f.name != "jurisdiction" and s.peek(f.name)]
    conditions = {n: _where(s, LOCAL[n][2], taken=False) for n in chosen}
    for name in asked:
        s.between(name) if s.series.field(name).ranged else s.any(name)

    def one(name):
        host, dataset, columns = LOCAL[name]
        if any(f not in columns for f in asked):
            return []
        params = {"select": ":id," + ",".join(columns.values()), "order": f"{columns['end_date']} DESC, :id",
                  "offset": at[name], "limit": size}
        where = " AND ".join(conditions[name] + [f"{columns['contract_number']} IS NOT NULL"])
        return [_contract(name, r) for r in _soda(f"https://{host}/resource/{dataset}.json", where=where, **params,
                                                 **({"q": words} if words else {}))]
    live = [n for n in chosen if at.get(n, -1) >= 0]
    with ThreadPoolExecutor(4) as ex:
        got = dict(zip(live, ex.map(one, live)))
    merged = sorted((r for rows in got.values() for r in rows), key=lambda r: r["end_date"] or "", reverse=True)[:size]
    used = {}
    for r in merged:
        used[r["jurisdiction"]] = used.get(r["jurisdiction"], 0) + 1
    nxt = dict(at)
    for name in live:
        n = used.get(name, 0)
        nxt[name] = -1 if len(got[name]) < size and n == len(got[name]) else at[name] + n
    return page(merged, nxt if any(v >= 0 for v in nxt.values()) else None, order="latest end date first")


def _read_local(key):
    slug, _, row_id = key.partition("/")
    name = next((n for n, v in SLUG.items() if v == slug), None)
    if not name:
        raise SourceError(f"municipal_contracts has no record {key!r}")
    host, dataset, columns = LOCAL[name]
    endpoint = f"https://{host}/resource/{dataset}.json"
    rows = _soda(endpoint, select=":id," + ",".join(columns.values()), where=f":id = {_literal(row_id)}")
    if not rows:
        raise SourceError(f"{name}'s dataset no longer has this row; search for the contract again.")
    number = rows[0].get(columns["contract_number"])
    every = _soda(endpoint, where=f"{columns['contract_number']} = {_literal(number)}", order=":id", limit=1000)
    links = [{"text": "Contract PDF", "url": pdf["url"]} for r in every for pdf in [r.get("contract_pdf") or r.get("category") or {}]
             if isinstance(pdf, dict) and pdf.get("url")]
    lines = [f"Every row {name} publishes for contract {number} ({len(every)}):"]
    for r in every:
        lines += ["", *outline({k: v for k, v in r.items() if not isinstance(v, dict)})]
    return {**_contract(name, rows[0]), "document": "\n".join(lines), "links": list({x["url"]: x for x in links}.values())}


# ---------- contracts in FERC's Electric Quarterly Reports ----------

def _viewer_post(client, soup, target, argument="", changes=None):
    """Submit the public ASP.NET viewer's current form, including its per-request view state."""
    body = {i["name"]: i.get("value", "") for i in soup.select('input[type="hidden"][name]')}
    for select in soup.select("select[name]"):
        option = select.find("option", selected=True)
        if not option and not select.has_attr("multiple") and int(select.get("size", 1)) <= 1:
            option = select.find("option")
        if option:
            body[select["name"]] = option.get("value", "")
    for key, value in list(body.items()):
        if key.endswith("_ClientState") and value.startswith("{"):
            state = json.loads(value)
            if "TabWasLoadedOnceState" in state:
                state["TabWasLoadedOnceState"][state["ActiveTabIndex"]] = True
                if key == "TabContainerReportViewer_ClientState" and target == "TabContainerReportViewer":
                    state["ActiveTabIndex"] = 1
                body[key] = json.dumps(state, separators=(",", ":"))
    body.update({"__EVENTTARGET": target, "__EVENTARGUMENT": argument, "__ASYNCPOST": "true",
                 "ScriptManager1": "updPnlReportViewer|" + target, **(changes or {})})
    response = client.post(VIEWER, data={k: v for k, v in body.items() if v},
                           headers={"Referer": VIEWER, "Origin": VIEWER.rstrip("/"),
                                    "X-MicrosoftAjax": "Delta=true", "X-Requested-With": "XMLHttpRequest"},
                           timeout=60)
    response.raise_for_status()
    return BeautifulSoup(response.content, "html.parser")


@functools.lru_cache(maxsize=8)
def _eqr_catalog(quarter, need_names):
    with requests.Session() as client:
        # This user agent is accepted by the same form that serves the public viewer.
        client.headers["User-Agent"] = "Mozilla/5.0"
        opening = client.get(VIEWER, timeout=45)
        opening.raise_for_status()
        soup = BeautifulSoup(opening.content, "html.parser")
        downloads = _viewer_post(client, soup, "TabContainerReportViewer", "activeTabChanged:1")
        archives = {}
        for link in downloads.select('a[href]'):
            match = re.fullmatch(r"CSV_(\d{4})_Q([1-4])\.zip", link.get_text(strip=True), re.I)
            if match and link["href"].startswith(VIEWER):
                archives[f"{match[1]}Q{match[2]}"] = link["href"]
        if not archives:
            raise SourceError("The FERC report viewer did not return its quarterly download links")
        if quarter is None:
            return sorted(archives, reverse=True), None, {}
        if quarter not in archives:
            raise SourceError(f"Quarter {quarter} is absent from FERC's archive ({min(archives)} through {max(archives)})")
        names = {}
        if need_names:
            periods = soup.select_one('select[id$="ddlReportPeriodSum"]')
            period = next((o for o in periods.select("option") if o.get_text(strip=True).startswith(quarter[-2:] + ",")
                           and o.get_text(strip=True).endswith(quarter[:4])), None) if periods else None
            if period is None:
                raise SourceError(f"FERC did not list respondent names for {quarter}")
            if period != (periods.find("option", selected=True) or periods.find("option")):
                soup = _viewer_post(client, soup, periods["name"], changes={periods["name"]: period["value"]})
            names = {o["value"]: o.get_text(strip=True) for o in soup.select('select[id$="lbxRespondentSum"] option')}
            if not names:
                raise SourceError(f"FERC did not return its respondent list for {quarter}")
    return quarter, archives[quarter], names


def _eqr_members(archive, quarter):
    pattern = re.compile(rf"CSV_{quarter[:4]}_{quarter[-2:]}_(\d+)_(\d+)\.zip", re.I)
    members = []
    for entry in archive.infolist():
        match = pattern.fullmatch(entry.filename)
        if match:
            members.append((match[1], match[2], entry))
    if not members:
        raise SourceError("FERC's quarterly archive contains no recognized filing archives")
    return sorted(members, key=lambda item: (int(item[0]), int(item[1])))


def _eqr_csv(archive, member, identities=False):
    # The outer ZIP also contains transaction data for thousands of other companies. A range
    # request fetches just this filing. Large individual filings spill to disk.
    result = {"contracts": [], "ident": []}
    with tempfile.SpooledTemporaryFile(max_size=8_000_000) as filing:
        with archive.open(member) as stream:
            shutil.copyfileobj(stream, filing, length=524288)
        with ZipFile(filing) as inner:
            for file in inner.namelist():
                kind = file.lower().rsplit("_", 1)[-1]
                if kind == "contracts.csv" or (identities and kind == "ident.csv"):
                    raw = inner.read(file)
                    try:
                        text = raw.decode("utf-8-sig")
                    except UnicodeDecodeError:
                        text = raw.decode("cp1252")
                    rows = csv.DictReader(io.StringIO(text, newline=""))
                    if kind == "contracts.csv" and not {"contract_unique_id", "seller_company_name",
                                                         "contract_termination_date"}.issubset(rows.fieldnames or []):
                        raise SourceError("FERC contract file is missing its contract identifier, seller or termination date")
                    result[kind[:-4]].extend(rows)
    return result


def _quarters():
    return _eqr_catalog(None, False)[0]


def _eqr_day(value):
    value = (value or "").strip()
    return f"{value[:4]}-{value[4:6]}-{value[6:8]}" if re.fullmatch(r"\d{8}", value) else None


def _eqr_contracts(quarter, respondent, filing, name, lines):
    """One filing's contracts: the lines sharing a contract's ID are its products and rates."""
    grouped = {}
    for line in lines:
        grouped.setdefault(line["contract_unique_id"], []).append(line)
    out = []
    for key, rows in grouped.items():
        first = rows[0]
        out.append({"id": f"{quarter}/{respondent}/{filing}/{quote(key, safe='')}", "url": VIEWER, "quarter": quarter,
                    "respondent": name, "respondent_id": respondent, "seller": first.get("seller_company_name"),
                    "customer": first.get("customer_company_name"),
                    "affiliate": {"Y": "yes", "N": "no"}.get((first.get("contract_affiliate") or "").upper()),
                    "contract_id": key, "service_agreement_id": first.get("contract_service_agreement_id") or None,
                    "tariff_reference": first.get("ferc_tariff_reference") or None,
                    "execution_date": _eqr_day(first.get("contract_execution_date")),
                    "commencement_date": _eqr_day(first.get("commencement_date_of_contract_term")),
                    "termination_date": _eqr_day(first.get("contract_termination_date")),
                    "actual_termination_date": _eqr_day(first.get("actual_termination_date")),
                    "extension_provision": first.get("extension_provision_description") or None,
                    "product": list(dict.fromkeys(r["product_name"] for r in rows if r.get("product_name"))), "_lines": rows})
    return out


BATCH, BATCH_BYTES = 40, 40_000_000  # filings read in one step, and the most they may weigh together


def _search_eqr(s):
    quarters = [q for q in _quarters() if not s.peek("quarter") or q in [x.upper() for x in s.peek("quarter").get("is", [])]]
    s.any("quarter")
    at = s.cursor or {"q": 0, "m": 0}
    if at["q"] >= len(quarters):
        return page([])
    quarter = quarters[at["q"]]
    _, url, listed = _eqr_catalog(quarter, True)
    ids, named = s.any("respondent_id"), s.any("respondent")
    try:
        with RemoteZip(url, timeout=90, headers={"Accept-Encoding": "identity"}) as archive:
            members = _eqr_members(archive, quarter)
            if ids:
                members = [m for m in members if m[0] in ids]
            if named:
                members = [m for m in members if any(set(names.tokens(g)) <= set(names.tokens(listed.get(m[0]) or "")) for g in named)]
            part, weight = [], 0
            for m in members[at["m"]:]:
                if part and (len(part) >= BATCH or weight + m[2].compress_size > BATCH_BYTES):
                    break
                part.append(m)
                weight += m[2].compress_size
            rows = [r for respondent, filing, entry in part
                    for r in _eqr_contracts(quarter, respondent, filing, listed.get(respondent) or respondent,
                                            _eqr_csv(archive, entry)["contracts"])]
    except SourceError:
        raise
    except Exception as e:
        raise SourceError(f"FERC's {quarter} archive could not be read: {type(e).__name__}: {str(e)[:150]}")
    done = at["m"] + len(part) >= len(members)
    nxt = {"q": at["q"], "m": at["m"] + len(part)} if not done else {"q": at["q"] + 1, "m": 0}
    return page([{k: v for k, v in r.items() if k != "_lines"} for r in rows], nxt if nxt["q"] < len(quarters) else None,
                order="latest quarter first, by respondent")


def _read_eqr(key):
    m = re.fullmatch(r"(\d{4}Q[1-4])/(\d+)/(\d+)/([^/]+)", key)
    if not m:
        raise SourceError("An eqr_contracts record_id is the one a search returned")
    quarter, respondent, filing, code = m.groups()
    _, url, listed = _eqr_catalog(quarter, True)
    with RemoteZip(url, timeout=90, headers={"Accept-Encoding": "identity"}) as archive:
        entry = next((e for rid, fid, e in _eqr_members(archive, quarter) if (rid, fid) == (respondent, filing)), None)
        if not entry:
            raise SourceError("This filing is no longer in FERC's archive for the quarter; search the quarter again.")
        got = _eqr_csv(archive, entry, identities=True)
    record = next((r for r in _eqr_contracts(quarter, respondent, filing, listed.get(respondent) or respondent, got["contracts"])
                   if r["contract_id"] == unquote(code)), None)
    if not record:
        raise SourceError("The filing has no such contract.")
    lines = [f"Products and rates ({len(record['_lines'])} lines):"]
    for row in record["_lines"]:
        lines += ["", *outline({k: v for k, v in row.items() if v})]
    return {**{k: v for k, v in record.items() if k != "_lines"}, "document": "\n".join(lines),
            "links": [{"text": "FERC's EQR report viewer", "url": VIEWER}]}


USAC_NAME = "Universal Service Administrative Company"
SERIES = [
    Series(
        id="erate_funding_requests", title="E-Rate funding requests",
        record="a funding request (FRN) on an FCC Form 471: a school's or library's request for E-Rate support for services "
               "from one service provider under one contract, as first filed or as it now stands",
        creator="E-Rate applicants", publisher=USAC_NAME, jurisdiction="federal", since="2016",
        fields=(F("funding_request_number", "identifier"), F("form_version", ("Original", "Current")), F("funding_year", "number"),
                F("applicant", "name"), F("applicant_type", "text"), F("state", "place"), F("service_provider", "name"),
                F("service_type", "text"), F("contract_type", "text"), F("award_date", "date"), F("service_start_date", "date"),
                F("contract_expiration_date", "date"), F("status", "text"), F("funding_commitment_request", "amount")),
        search=_search_erate, read=_read_erate),
    Series(
        id="municipal_contracts", title="city and county contracts",
        record="a row of a city's or county's published contracts: a contract, or an amendment or purchase order under it, "
               "with its vendor and term",
        creator="city and county governments", jurisdiction="; ".join(LOCAL),
        fields=(F("jurisdiction", "place"), F("contract_number", "identifier"), F("vendor", "name"), F("description", "text"),
                F("department", "name"), F("contract_type", "text"), F("start_date", "date"), F("end_date", "date"),
                F("amount", "amount")),
        search=_search_local, read=_read_local),
    Series(
        id="eqr_contracts", title="FERC Electric Quarterly Report contracts",
        record="a contract for the sale of electricity or transmission that a seller reported to FERC in one quarter's "
               "Electric Quarterly Report",
        creator="public utilities and power marketers under FERC's jurisdiction", publisher="Federal Energy Regulatory Commission",
        jurisdiction="federal", coverage=lambda series: (min(_quarters()), max(_quarters())),
        fields=(F("quarter", "identifier"), F("respondent", "name"), F("respondent_id", "identifier"), F("seller", "name"),
                F("customer", "name"), F("affiliate", ("yes", "no")), F("contract_id", "identifier"),
                F("service_agreement_id", "identifier"), F("tariff_reference", "text"), F("execution_date", "date"),
                F("commencement_date", "date"), F("termination_date", "date"), F("actual_termination_date", "date"),
                F("extension_provision", "text"), F("product", "text", many=True)),
        search=_search_eqr, read=_read_eqr),
]
