"""SEC full-text search (efts.sec.gov): the text of every SEC filing since 2001, searched by words or a
phrase in double quotes, by form and filing date. Each result is one document of a filing (the report, or
an exhibit such as a press release), with its company, form, filing date and address. read_record reads
the document; its find returns the passages."""

import datetime as dt
import re
from urllib.parse import urlencode

from ..util import SourceError, today
from .sec import _get

EFTS = "https://efts.sec.gov/LATEST/search-index"
DISPLAY = re.compile(r"^(.*?)\s+(?:\(([^()]*)\)\s+)?\(CIK (\d+)\)\s*$")


def search(query, forms=None, since=None, until=None, max_results=100):
    q = (query or "").strip()
    if not q:
        raise SourceError("filings: give words to search for, or a phrase in double quotes")
    until = until or today().isoformat()
    since = since or (today() - dt.timedelta(days=120)).isoformat()
    params = {"q": q, "dateRange": "custom", "startdt": since, "enddt": until}
    if forms:
        params["forms"] = ",".join(forms if isinstance(forms, (list, tuple)) else [forms])
    want = min(int(max_results or 100), 500)
    out, seen, start, total = [], set(), 0, 0
    while len(out) < want:
        hits = _get(f"{EFTS}?{urlencode(params | {'from': start})}").get("hits") or {}
        total = (hits.get("total") or {}).get("value", 0)
        batch = hits.get("hits") or []
        if not batch:
            break
        for h in batch:
            s = h.get("_source") or {}
            adsh, _, name = (h.get("_id") or "").partition(":")
            cik = int((s.get("ciks") or ["0"])[0] or 0)
            url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{adsh.replace('-', '')}/{name}"
            if not (adsh and name and cik) or url in seen:
                continue
            seen.add(url)
            m = DISPLAY.match((s.get("display_names") or [""])[0])
            out.append({"record_id": url, "company": m.group(1).strip() if m else (s.get("display_names") or [None])[0],
                        "ticker": (m.group(2) or None) if m else None, "cik": cik, "form": s.get("form"),
                        "document": s.get("file_type"), "description": s.get("file_description"),
                        "filed": s.get("file_date"), "period": s.get("period_ending"), "items": s.get("items") or None})
            if len(out) >= want:
                break
        start += len(batch)
        if start >= total:
            break
    return {"results": out, "total_matches": total, "since": since, "until": until}
