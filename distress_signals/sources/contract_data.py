"""Electricity seller filings, E-Rate funding requests and local government contracts."""
import csv
import datetime as dt
import io
import json
import re
import shutil
import tempfile
from collections import defaultdict
from urllib.parse import quote, unquote, urlencode
from zipfile import ZipFile

import requests
from bs4 import BeautifulSoup
from remotezip import RemoteZip

from ..util import SourceError, http_json

VIEWER = "https://eqrreportviewer.ferc.gov/"
USAC = "https://opendata.usac.org/resource"
ERATE = USAC + "/qdmp-ygft.json"
FORM471 = USAC + "/9s6i-myen.json"

# Host, dataset, contract number, vendor, description, published term-end field.
LOCAL = {
    "chicago": ("data.cityofchicago.org", "rsxa-ify5", "purchase_order_contract_number",
                "vendor_name", "purchase_order_description", "end_date"),
    "cook_county": ("datacatalog.cookcountyil.gov", "qh8j-6k63", "contract_number",
                    "vendor_name", "description", "end_date"),
    "montgomery_county_md": ("data.montgomerycountymd.gov", "vmu2-pnrc", "contractno",
                             "vendor", "contractdesc", "expiration"),
    "san_francisco": ("data.sfgov.org", "cqi5-hm2d", "contract_no", "prime_contractor",
                      "contract_title", "term_end_date"),
}
COMMON = {"page", "limit", "end_from", "end_until"}
FILTERS = {"ferc_eqr": COMMON | {"quarter", "respondent_id", "records", "filing_offset", "filings_per_page"},
           "erate": COMMON | {"version", "funding_year", "contract_type", "state"},
           "municipal_contracts": COMMON | {"jurisdiction"}}


def _literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def _url(endpoint, where):
    return endpoint + "?" + urlencode({"$where": where})


def _soda(endpoint, **params):
    data = http_json(endpoint, params={"$" + k: v for k, v in params.items()}, tries=2, timeout=45)
    if not isinstance(data, list):
        raise SourceError("The public data portal did not return records")
    return data


def _clauses(query, fields, end_field, start, end):
    parts = []
    if query:
        literal = _literal(query.strip().lower())
        parts.append("(" + " or ".join(f"contains(lower({f}), {literal})" for f in fields) + ")")
    if start:
        parts.append(f"{end_field} >= {_literal(start.isoformat())}")
    if end:
        parts.append(f"{end_field} < {_literal((end + dt.timedelta(days=1)).isoformat())}")
    return parts


def _window(endpoint, where, order, page, limit):
    rows = _soda(endpoint, where=" AND ".join(where), order=order, offset=(page - 1) * limit, limit=limit + 1)
    return rows[:limit], len(rows) > limit


def _observation(source, native, url, title, payload, links=()):
    fields = payload if isinstance(payload, dict) else {"records": payload}
    return {"kind": "contract_record", "source": source, "native_id": native, "url": url, "published": None,
            "text": json.dumps(payload, indent=2, ensure_ascii=False),
            "data": {"title": title, "links": list(links), **fields}}


def _erate_row(row):
    frn, version = row["funding_request_number"], row["form_version"]
    where = f"funding_request_number={_literal(frn)} AND form_version={_literal(version)}"
    return {"record_id": f"erate:{frn}/{version}", "url": _url(ERATE, where),
            "title": f"FRN {frn} • {row.get('spin_name') or 'Provider unreported'}",
            **{k: row.get(k) for k in ("application_number", "form_version", "organization_name", "funding_year",
                                       "spin_name", "contract_type_name", "contract_expiration_date", "extended_expiration_date",
                                       "form_471_frn_status_name", "funding_commitment_request")}}


def _erate(query, page, limit, start, end, version="Current", funding_year=None, contract_type=None, state=None):
    if version not in {"Current", "Original"}:
        raise SourceError("version is Current or Original")
    fields = ("spin_name", "organization_name", "funding_request_number", "contract_number")
    where = _clauses(query, fields, "contract_expiration_date", start, end) + ["form_version=" + _literal(version)]
    if funding_year is not None:
        where.append("funding_year=" + _literal(int(funding_year)))
    if contract_type:
        where.append("upper(contract_type_name)=" + _literal(contract_type.upper()))
    if state:
        where.append("state=" + _literal(state.upper()))
    rows, more = _window(ERATE, where, "funding_year DESC, funding_request_number", page, limit)
    return {"results": [_erate_row(r) for r in rows], "complete": not more, "next_page": page + 1 if more else None}


def _read_erate(native):
    if not re.fullmatch(r"\d+/(?:Current|Original)", native):
        raise SourceError("Use the FRN and form version from search")
    frn, version = native.split("/")
    where = f"funding_request_number={_literal(frn)} AND form_version={_literal(version)}"
    rows = _soda(ERATE, where=where, limit=2)
    if len(rows) != 1:
        raise SourceError(f"Expected one {version} funding request for FRN {frn}; found {len(rows)}")
    row = rows[0]
    app = row["application_number"]
    # USAC attaches the signed PDF to Original; the Current application carries subsequent changes.
    form_where = f"application_number={_literal(app)}"
    if version == "Original":
        form_where += " AND form_version='Original'"
    form = _soda(FORM471, where=form_where, select="application_number,form_version,certified_datetime,file_url", limit=2)
    links = [{"text": "Form 471 application record", "url": _url(FORM471, form_where)}]
    for f in form:
        pdf = f.get("file_url") or {}
        url = pdf.get("url") if isinstance(pdf, dict) else pdf
        if url:
            links.append({"text": f"FCC Form 471 PDF ({f['form_version']})", "url": url})
    return _observation("erate", native, _url(ERATE, where), _erate_row(row)["title"],
                        {"funding_request": row, "form_471": form}, links)


def _local(query, page, limit, start, end, jurisdiction=None):
    if jurisdiction is not None and jurisdiction not in LOCAL:
        raise SourceError(f"jurisdiction is one of {sorted(LOCAL)}")
    results, by_portal = [], {}
    for key in ([jurisdiction] if jurisdiction else LOCAL):
        host, dataset, number, vendor, desc, end_field = LOCAL[key]
        endpoint = f"https://{host}/resource/{dataset}.json"
        fields = [vendor, desc, number]
        if key == "san_francisco":
            fields.append("project_team_supplier")
        where = _clauses(query, fields, end_field, start, end) + [f"{number} IS NOT NULL"]
        rows, more = _window(endpoint, where, f"{number}, :id", page, limit)
        by_portal[key] = {"next_page": page + 1 if more else None}
        for row in rows:
            contract = row[number]
            result = {"record_id": f"municipal_contracts:{key}/{quote(contract, safe='')}",
                      "url": _url(endpoint, f"{number}={_literal(contract)}"),
                      "title": f"{contract} • {row.get(vendor) or 'Vendor unreported'}", "jurisdiction": key,
                      "contract_number": contract, "vendor": row.get(vendor), "description": row.get(desc),
                      "term_end": row.get(end_field)}
            for col in ("revision_number", "approval_date", "system_release", "project_team_supplier", "extend"):
                if col in row:
                    result[col] = row[col]
            results.append(result)
    more = any(v["next_page"] for v in by_portal.values())
    return {"results": results, "complete": not more, "next_page": page + 1 if more else None,
            "portals": by_portal, "scope": "Matching published rows; read a contract number for all its rows."}


def _read_local(native):
    key, sep, code = native.partition("/")
    if key not in LOCAL or not sep or not code:
        raise SourceError("Use the jurisdiction and contract number from search")
    host, dataset, number, *_ = LOCAL[key]
    contract = unquote(code)
    endpoint = f"https://{host}/resource/{dataset}.json"
    where = f"{number}={_literal(contract)}"
    rows = []
    while True:
        batch = _soda(endpoint, where=where, order=":id", limit=1000, offset=len(rows))
        rows.extend(batch)
        if len(batch) < 1000:
            break
    if not rows:
        raise SourceError("This contract number is absent from the publisher's current dataset")
    links = []
    for row in rows:
        pdf = row.get("contract_pdf") or {}
        if isinstance(pdf, dict) and pdf.get("url"):
            link = {"text": "Contract PDF", "url": pdf["url"]}
            if link not in links:
                links.append(link)
    return _observation("municipal_contracts", native, _url(endpoint, where), f"{key} contract {contract}", rows, links)


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
        quarter = quarter or max(archives)
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


def _eqr_filing(quarter, url, respondent, filing, name):
    return {"record_id": f"ferc_eqr:{quarter}/{respondent}/{filing}", "url": url, "quarter": quarter,
            "respondent_id": respondent, "filing_id": filing, "title": f"{name} • {quarter}",
            "respondent": name}


def _eqr(query, page, limit, start, end, quarter=None, respondent_id=None, records=None,
         filing_offset=0, filings_per_page=5):
    mode = records or ("contracts" if respondent_id or start or end else "filings")
    if mode not in {"contracts", "filings"}:
        raise SourceError("records is filings or contracts")
    if mode == "filings" and (start or end):
        raise SourceError("Use records=contracts to filter by contract termination")
    filing_offset, filings_per_page = int(filing_offset), int(filings_per_page)
    if filing_offset < 0 or not 1 <= filings_per_page <= 25:
        raise SourceError("filing_offset starts at zero and filings_per_page is 1 to 25")
    quarter, url, names = _eqr_catalog(quarter, True)
    start_row = (page - 1) * limit
    query = query.strip().casefold()
    rows, next_offset = [], None
    with RemoteZip(url, timeout=90, headers={"Accept-Encoding": "identity"}) as archive:
        members = _eqr_members(archive, quarter)
        if respondent_id:
            members = [m for m in members if m[0] == str(respondent_id)]
            if not members:
                raise SourceError(f"Respondent {respondent_id} has no published filing archive in {quarter}")
        if mode == "filings":
            for respondent, filing, _ in members:
                name = names.get(respondent) or f"FERC respondent {respondent}"
                if query in name.casefold() or query == respondent:
                    rows.append(_eqr_filing(quarter, url, respondent, filing, name))
        else:
            batch = members[filing_offset:filing_offset + filings_per_page]
            if filing_offset + len(batch) < len(members):
                next_offset = filing_offset + len(batch)
            for respondent, filing, member in batch:
                filing_record = _eqr_filing(quarter, url, respondent, filing, names.get(respondent) or respondent)
                grouped = defaultdict(list)
                for line in _eqr_csv(archive, member)["contracts"]:
                    key = line["contract_unique_id"]
                    if start or end:
                        try:
                            termination = dt.date.fromisoformat(line.get("contract_termination_date") or "")
                        except ValueError:
                            continue
                        if (start and termination < start) or (end and termination > end):
                            continue
                    if query not in " ".join((v or "") for v in line.values()).casefold():
                        continue
                    grouped[key].append(line)
                for key, lines in grouped.items():
                    # A contract can have several product and rate lines sharing a reported contract ID.
                    fields = ("seller_company_name", "customer_company_name", "contract_service_agreement_id",
                              "contract_termination_date", "actual_termination_date", "extension_provision_description")
                    terms = list(dict.fromkeys(tuple(line.get(f) for f in fields) for line in lines))
                    rows.append({**filing_record, "record_id": filing_record["record_id"] + "/" + quote(key, safe=""),
                                 "title": f"{lines[0]['seller_company_name']} • {key} • {quarter}",
                                 "contract_unique_id": key, "terms": [dict(zip(fields, t)) for t in terms],
                                 "matching_product_lines": len(lines)})
    more_rows = start_row + limit < len(rows)
    return {"results": rows[start_row:start_row + limit], "quarter": quarter, "complete": not more_rows and next_offset is None,
            "next_page": page + 1 if more_rows else None, "next_filing_offset": next_offset,
            "scope": "Respondents in FERC's quarterly archive" if mode == "filings" else
                     f"Contract terms in {len(batch)} of {len(members)} filing(s), from filing_offset {filing_offset}"}


def _read_eqr(native):
    match = re.fullmatch(r"(\d{4}Q[1-4])/(\d+)/(\d+)(?:/([^/]+))?", native)
    if not match:
        raise SourceError("Use the quarter, respondent and filing identifiers returned by FERC search")
    quarter, respondent, filing, code = match.groups()
    _, url, _ = _eqr_catalog(quarter, False)
    with RemoteZip(url, timeout=90, headers={"Accept-Encoding": "identity"}) as archive:
        entry = next((m for rid, fid, m in _eqr_members(archive, quarter)
                      if (rid, fid) == (respondent, filing)), None)
        if not entry:
            raise SourceError("This filing version is absent from the current FERC archive. Search the quarter again.")
        payload = _eqr_csv(archive, entry, identities=True)
    if code is not None:
        key = unquote(code)
        payload["contracts"] = [r for r in payload["contracts"] if r["contract_unique_id"] == key]
        if not payload["contracts"]:
            raise SourceError("This filing does not contain that contract identifier")
    payload.update(quarter=quarter, respondent_id=respondent, filing_id=filing, archive_member=entry.filename)
    return _observation("ferc_eqr", native, url, f"FERC EQR {native}", payload,
                        [{"text": "FERC report viewer", "url": VIEWER}])


def search(source, query, *, page=1, limit=25, end_from=None, end_until=None, **filters):
    page, limit = int(page), int(limit)
    if page < 1 or not 1 <= limit <= 100:
        raise SourceError("page starts at 1 and limit must be 1 to 100")
    start = dt.date.fromisoformat(end_from) if end_from else None
    end = dt.date.fromisoformat(end_until) if end_until else None
    if start and end and start > end:
        raise SourceError("end_from must be on or before end_until")
    return {"ferc_eqr": _eqr, "erate": _erate, "municipal_contracts": _local}[source](
        query or "", page, limit, start, end, **filters)


def read(source, native_id):
    return {"ferc_eqr": _read_eqr, "erate": _read_erate, "municipal_contracts": _read_local}[source](native_id)
