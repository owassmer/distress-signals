"""Issuer disclosures delivered by MSRB/CSA and separately published copies. The public filing
portals are for individual lookups; neither adapter crawls them."""

import csv
import datetime as dt
import json
import os
import re
from functools import lru_cache
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qsl, quote, urlsplit
from xml.etree import ElementTree as ET

import requests

from ..util import SourceError, session, stable_id
from . import web

FILTERS = {"emma": {"collection", "since", "until", "limit", "offset"},
           "sedar_plus": {"cse_listing", "issuer_collection", "epoch", "generation", "since", "until", "limit", "offset"}}
EMMA_COLLECTION = "MSRB continuing disclosure delivery"
SEDAR_COLLECTION = "Licensed SEDAR+ document catalog"
CSE_COLLECTION = "Canadian Securities Exchange, issuer's SEDAR filings"


def _bounded(path, root):
    path = path.resolve()
    if not path.is_relative_to(root):
        raise SourceError("The disclosure delivery references a file outside its root directory")
    return path


def _pdf(path):
    if not path.is_file():
        raise SourceError("The publisher's document file is missing from the configured delivery")
    with path.open("rb") as stream:
        content = stream.read(web.MAX_BYTES + 1)
    if len(content) > web.MAX_BYTES or not content.startswith(b"%PDF-"):
        raise SourceError("Expected a PDF within the document size limit")
    try:
        text, _ = web._pdf(content)
    except Exception as exc:
        raise SourceError(f"Could not extract the delivered PDF: {type(exc).__name__}") from exc
    if not re.sub(r"\[page \d+\]", "", text).strip():
        raise SourceError("The filing PDF has no extractable text")
    return text


def _published(value):
    """Calendar date of the source's timestamp, retaining its supplied time zone."""
    return dt.date.fromisoformat(value[:10]).isoformat()


def _emma_snapshot():
    folder = os.getenv("EMMA_DISCLOSURE_DIR")
    if not folder or not Path(folder).is_dir():
        raise SourceError("Set EMMA_DISCLOSURE_DIR to the unpacked MSRB continuing-disclosure XML and PDF delivery")
    root = Path(folder).resolve()
    indexes, files = [], {}
    for path in root.rglob("*"):
        suffix = path.suffix.casefold()
        if suffix == ".xml":
            path = _bounded(path, root)
            stat = path.stat()
            indexes.append((path, stat.st_mtime_ns, stat.st_size))
        elif suffix == ".pdf":
            files[path.stem] = _bounded(path, root)
    if not indexes:
        raise SourceError("The configured MSRB directory contains no disclosure XML files")
    return _emma_index(tuple(sorted(indexes))), files


@lru_cache(maxsize=1)
def _emma_index(indexes):
    latest = {}
    for path, _, _ in indexes:
        try:
            root = ET.parse(path).getroot()
            for sub in root.iter():
                if sub.tag.rsplit('}', 1)[-1] != "ContinuingDisclosureSubmission":
                    continue
                sid = sub.attrib["SubmissionIdentifier"]
                stamp = dt.datetime.fromisoformat(sub.attrib["SubmissionTransactionDateTime"])
                if sid not in latest or stamp > latest[sid][0]:
                    latest[sid] = stamp, sub
        except (OSError, ET.ParseError, KeyError, TypeError, ValueError) as exc:
            raise SourceError(f"Invalid MSRB transaction in {path.name}: {exc}") from exc
    if not latest:
        raise SourceError("No MSRB ContinuingDisclosureSubmission records found in the XML delivery")
    rows = {}
    for sid, (_, sub) in latest.items():
        # Each transaction replaces the complete prior submission, including removals and archived files.
        if sub.get("SubmissionStatus") != "Publish":
            continue
        securities = [s.attrib for s in sub.findall('.//{*}Security')]
        contacts = [c.attrib for c in sub.findall('{*}ContactInformation')]
        issuers = {s['IssuerName'] for s in securities if s.get('IssuerName')}
        issuers.update(c['ContactOrganizationName'] for c in contacts if c.get('ContactType') == 'Issuer' and c.get('ContactOrganizationName'))
        obligors = {c['ContactOrganizationName'] for c in contacts if c.get('ContactType') == 'Obligor' and c.get('ContactOrganizationName')}
        disclosures = [{**d.attrib, 'periods': [p.attrib for p in d.findall('{*}DisclosurePeriod')],
                        'subcategories': [c.get('Name') for c in d.findall('{*}DisclosureSubCategory')]}
                       for d in sub.findall('.//{*}Disclosure')]
        categories = [v for d in disclosures for k, v in d.items() if k.endswith('Category')]
        title = ', '.join(dict.fromkeys(categories)) or f"Continuing disclosure {sid}"
        for doc in sub.findall('{*}Document'):
            if doc.get('DocumentStatus') != 'Current':
                continue
            for file in doc.findall('.//{*}File'):
                if file.get('FileStatus') != 'Current':
                    continue
                fid, did = file.attrib['FileIdentifier'], doc.attrib['DocumentIdentifier']
                native = f"{sid}/{did}/{fid}"
                rows[native] = {"record_id": f"emma:{native}", "title": title,
                                "url": f"https://emma.msrb.org/MarketActivity/ContinuingDisclosureDetails/{quote(sid, safe='')}",
                                "published": file.get('FilePostingDate') or doc.get('DocumentPostingDate'),
                                "issuers": sorted(issuers), "obligors": sorted(obligors), "securities": securities,
                                "disclosures": disclosures, "file_id": fid, "document_id": did,
                                "submission_id": sid, "transaction_at": sub.get('SubmissionTransactionDateTime')}
    return rows


# CSA provides DDS schemas and SFTP delivery details to each subscriber. This CSV is the adapter's
# input contract for a current, public-only snapshot exported from an authorized delivery.
_DDS_FIELDS = {"document_id", "issuer", "title", "published", "url", "file"}


# These designate a public document, version or presentation. An unfamiliar query may instead be a
# bearer credential; the original delivery can still be read using its public document permalink.
_PUBLIC_QUERY_FIELDS = {"id", "docid", "documentid", "documentreference", "filingid", "fileid", "attachmentid",
                        "version", "versionid", "lang", "language", "locale", "format", "inline", "download"}


def _public_document_url(url):
    link = urlsplit(url)
    if (link.scheme != 'https' or not link.hostname or link.username is not None or link.password is not None
            or not link.path.strip('/') or (link.fragment and not re.fullmatch(r'page=\d+', link.fragment))):
        return False
    parameters = parse_qsl(link.query, keep_blank_values=True)
    for key, value in parameters:
        if key.replace('_', '').lower() not in _PUBLIC_QUERY_FIELDS or not re.fullmatch(r'[\w .~:/+-]*={0,2}', value):
            return False
    if link.hostname.removeprefix('www.') in {'sedarplus.ca', 'sedarplus.com'}:
        return '/records/document' in link.path and any(k.lower() in {'id', 'documentid'} and v for k, v in parameters)
    return True


def _dds_catalog():
    catalog = os.getenv("SEDAR_DDS_INDEX")
    if not catalog or not Path(catalog).is_file():
        raise SourceError("Set SEDAR_DDS_INDEX to a current CSV index of licensed SEDAR+ documents, "
                          "or give cse_listing for an authorized CSE collection")
    path = Path(catalog).resolve()
    rows = {}
    try:
        with path.open(encoding='utf-8-sig', newline='') as stream:
            reader = csv.DictReader(stream)
            if not _DDS_FIELDS.issubset(reader.fieldnames or []):
                raise SourceError('SEDAR_DDS_INDEX requires columns ' + ', '.join(sorted(_DDS_FIELDS)))
            for line in reader:
                if any(not (line.get(k) or '').strip() for k in _DDS_FIELDS):
                    raise SourceError(f"Missing SEDAR+ document fields on catalog line {reader.line_num}")
                doc_id = line['document_id']
                if doc_id in rows:
                    raise SourceError(f"Duplicate document identifier on catalog line {reader.line_num}")
                # Require a document-specific source link: the home page or a generic search cannot be cited.
                if not _public_document_url(line['url']):
                    raise SourceError(f"Expected a public document URL without credentials on catalog line {reader.line_num}")
                rows[doc_id] = ({"record_id": 'sedar_plus:dds/' + doc_id, "issuer": line['issuer'],
                                 "title": line['title'], "url": line['url'], "published": _published(line['published']),
                                 "document_id": doc_id}, _bounded(path.parent / line['file'], path.parent))
    except (OSError, csv.Error, UnicodeError, ValueError) as exc:
        raise SourceError(f"Could not read licensed SEDAR+ document catalog: {exc}") from exc
    return rows


class _ListingPage(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_data, self.chunks = False, []

    def handle_starttag(self, tag, attrs):
        if tag == 'script':
            self.in_data = dict(attrs).get('id') == '__NEXT_DATA__'

    def handle_endtag(self, tag):
        if tag == 'script':
            self.in_data = False

    def handle_data(self, text):
        if self.in_data:
            self.chunks.append(text)


def _cse_rows(slug):
    if os.getenv('CSE_SEDAR_LICENSED') != '1':
        raise SourceError("The CSE requires permission for commercial reuse. Set CSE_SEDAR_LICENSED=1 "
                          "for a deployment with that permission, or use a licensed DDS catalog.")
    if not isinstance(slug, str) or not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', slug):
        raise SourceError("cse_listing is the issuer slug from thecse.com/listings/<slug>/sedar-filings/")
    index = f'https://thecse.com/listings/{slug}/sedar-filings/'
    try:
        response = session().get(index, timeout=45)
        response.raise_for_status()
        page = _ListingPage()
        page.feed(response.text)
        meta = json.loads(''.join(page.chunks))['props']['pageProps']['staticCompanyData']['metadata']
        feed = meta.get('sedar_filings')
        if not feed:
            raise SourceError(f"CSE lists no SEDAR filing feed at {index}")
        if not re.fullmatch(r'https://webapi(?:-backup)?\.thecse\.com/trading/listed/sedar_filings/\d+\.json', feed):
            raise SourceError(f"Unrecognized CSE filing index at {index}")
        response = session().get(feed, timeout=45)
        response.raise_for_status()
        files = response.json()['list']
        rows = {}
        for entry in files:
            if entry.get('status') != 'available':
                continue
            url = entry['url']
            if not re.fullmatch(r'https://sedar-filings(?:-backup)?\.thecse\.com/[^?#]+\.pdf', url, re.I):
                raise SourceError(f"Unrecognized CSE document URL in {feed}")
            native = 'cse/' + slug + '/' + stable_id(entry.get('accession_number') or urlsplit(url).path)
            rows[native] = {"record_id": 'sedar_plus:' + native, "title": entry['document_description'],
                            "issuer_as_listed": meta['security_name'], "url": url, "published": entry['public_date'],
                            "category": entry.get('document_category'), "filing": entry.get('filing_description'),
                            "language": entry.get('document_language'), "accession": entry.get('accession_number')}
        return rows
    except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
        raise SourceError(f"Could not read the CSE filing index for {slug}: {type(exc).__name__}") from exc


def search(source, query, *, collection=None, cse_listing=None, issuer_collection=None, epoch=None, generation=None,
           since=None, until=None, limit=30, offset=0):
    public_index = issuer_collection is not None or epoch is not None or generation is not None
    if cse_listing and public_index:
        raise SourceError("Choose one collection per search: a CSE listing or the issuer-website index")
    if type(limit) is not int or not 1 <= limit <= 200 or type(offset) is not int or offset < 0:
        raise SourceError('limit must be 1–200 and offset a nonnegative integer')
    if source == 'emma':
        collection = collection or ('msrb' if os.getenv('EMMA_DISCLOSURE_DIR') else 'california')
        if collection == 'california':
            if since or until:
                raise SourceError('since and until apply to MSRB filing publication dates; use collection=msrb')
            from . import issuer_municipal
            return issuer_municipal.search(query, limit=limit, offset=offset)
        if collection != 'msrb':
            raise SourceError('EMMA collection is msrb or california')
        records, pdfs = _emma_snapshot()
        rows = [dict(row, pdf_available=row['file_id'] in pdfs) for row in records.values()]
        coverage = EMMA_COLLECTION
    elif cse_listing:
        rows, coverage = _cse_rows(cse_listing).values(), CSE_COLLECTION
    elif public_index or not os.getenv('SEDAR_DDS_INDEX'):
        if since or until:
            raise SourceError('since and until require publisher filing dates from a DDS catalog or CSE listing. '
                              'The independent issuer-website index uses its own discovery dates.')
        from . import issuer_websites
        return issuer_websites.search(query, issuer_collection=issuer_collection, limit=limit, offset=offset,
                                      epoch=epoch, generation=generation)
    else:
        rows = [r for r, _ in _dds_catalog().values()]
        coverage = SEDAR_COLLECTION
    try:
        low = dt.date.fromisoformat(since).isoformat() if since else None
        high = dt.date.fromisoformat(until).isoformat() if until else None
    except (TypeError, ValueError) as exc:
        raise SourceError('since and until use YYYY-MM-DD') from exc
    if low and high and low > high:
        raise SourceError('since must be on or before until')
    matches = []
    for row in rows:
        pub = (row.get('published') or '')[:10]
        if (low and pub < low) or (high and (not pub or pub > high)):
            continue
        fields = json.dumps([value for key, value in row.items() if key != 'url'], ensure_ascii=False).casefold()
        if all(term in fields for term in query.casefold().split()):
            matches.append(row)
    matches.sort(key=lambda r: (r['published'] or '', r['record_id']), reverse=True)
    end = offset + limit
    return {"results": matches[offset:end], "total_matches": len(matches), "complete": end >= len(matches),
            "next_offset": end if end < len(matches) else None, "collection": coverage}


def read(source, native_id):
    if source == 'sedar_plus' and native_id.startswith('website/'):
        from . import issuer_websites
        return issuer_websites.read(native_id)
    if source == 'emma' and native_id.startswith('california:'):
        from . import issuer_municipal
        return issuer_municipal.read(native_id)
    if source == 'emma':
        records, files = _emma_snapshot()
        row = records.get(native_id)
        if row is None:
            raise SourceError('This file is absent from the latest current MSRB submission in the configured delivery')
        path = files.get(row['file_id'])
        text = json.dumps(row, ensure_ascii=False, indent=2)
        if path:
            text += '\n\nFiling document\n' + _pdf(path)
        else:
            text += '\n\nOnly MSRB index metadata is available. The PDF is absent from this delivery.'
        kind = 'filing' if path else 'filing_index'
        coverage = EMMA_COLLECTION
    elif native_id.startswith('dds/'):
        entry = _dds_catalog().get(native_id[4:])
        if not entry:
            raise SourceError('Document absent from the current authorized SEDAR+ catalog')
        row, path = entry
        kind, text, coverage = 'filing', _pdf(path), SEDAR_COLLECTION
    elif native_id.startswith('cse/') and native_id.count('/') == 2:
        row = _cse_rows(native_id.split('/')[1]).get(native_id)
        if not row:
            raise SourceError('Document no longer available in the CSE filing index')
        doc = web.read(row['url'])
        if doc['data'].get('content_type') != 'application/pdf' and not doc['text'].startswith('[page 1]'):
            raise SourceError('The CSE filing URL returned a webpage instead of the disclosure PDF')
        kind, text, coverage = 'filing', doc['text'], CSE_COLLECTION
    else:
        raise SourceError('Use the disclosure record_id returned by search_records')
    return {"source": source, "kind": kind, "native_id": native_id, "url": row['url'], "published": row['published'],
            "text": text, "data": {**row, "collection": coverage}}
