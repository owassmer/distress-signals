"""Find original Canadian issuer documents through FREEDAR's public metadata API."""

import re
from urllib.parse import urlsplit

import requests

from ..util import SourceError, session


API = "https://freedar.ca/api"
COLLECTION = "FREEDAR index of Canadian issuer websites"
DIGEST = re.compile(r"[0-9a-f]{64}")


def _get(path, params=None):
    try:
        response = session().get(API + path, params=params, timeout=35)
        if response.status_code == 409:
            raise SourceError("FREEDAR's index changed. Restart the search at offset 0 for the current version.")
        response.raise_for_status()
        return response.json()
    except (requests.RequestException, ValueError) as exc:
        response = getattr(exc, "response", None)
        status = response.status_code if response is not None else "unavailable"
        raise SourceError(f"FREEDAR could not deliver the public report metadata ({status}).") from None


def _row(document):
    digest = document.get("document_sha256")
    if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
        raise SourceError("FREEDAR returned a report without a valid document hash.")
    observations = document.get("source_observations")
    if not isinstance(observations, list) or not observations:
        raise SourceError("FREEDAR returned a report without its issuer-source observations.")
    for observation in observations:
        url = urlsplit(observation.get("source_url") or "")
        if url.scheme not in {"http", "https"} or not url.hostname:
            raise SourceError("FREEDAR returned a report without a valid original document URL.")
    return {"record_id": f"sedar_plus:website/{digest}", "title": observations[0].get("title") or "Issuer document",
            "document_sha256": digest, "url": f"{API}/v1/documents/{digest}",
            "issuer_document_url": observations[0]["source_url"], "source_observations": observations,
            "provenance": document.get("provenance_label"), "document_rights": document.get("document_rights"),
            "licence_status": document.get("licence_status")}


def search(query, *, issuer_collection=None, limit=25, offset=0, epoch=None, generation=None):
    if type(limit) is not int or not 1 <= limit <= 50 or type(offset) is not int or offset < 0:
        raise SourceError("The issuer website index supports limit 1–50 and a nonnegative offset.")
    if offset or epoch or generation:
        if not all(isinstance(v, str) and DIGEST.fullmatch(v) for v in (epoch, generation)):
            raise SourceError("Use epoch and generation from the first page when continuing an issuer website search.")
    params = {"q": query, "limit": limit, "offset": offset}
    if issuer_collection:
        params["collection"] = issuer_collection
    if epoch:
        params.update(epoch=epoch, generation=generation)
    data = _get("/filing-search", params)
    if (not isinstance(data.get("results"), list) or not isinstance(data.get("more"), bool)
            or not all(isinstance(data.get(k), str) and DIGEST.fullmatch(data[k])
                       for k in ("epoch_sha256", "generation_sha256"))):
        raise SourceError("The issuer website index returned an incomplete search response.")
    if epoch and (data["epoch_sha256"], data["generation_sha256"]) != (epoch, generation):
        raise SourceError("FREEDAR's index changed. Restart the search at offset 0.")
    rows = [_row(document) for document in data["results"]]
    return {"results": rows, "collection": COLLECTION, "complete": not data["more"],
            "next_offset": offset + len(rows) if data["more"] else None,
            "epoch": data["epoch_sha256"], "generation": data["generation_sha256"],
            "note": "These metadata records identify original issuer documents. Read issuer_document_url "
                    "for the issuer's report and its figures. Coverage is limited to the indexed collections."}


def read(native_id):
    prefix, sep, digest = native_id.partition("/")
    if prefix != "website" or not sep or not DIGEST.fullmatch(digest):
        raise SourceError("Use the issuer website record_id returned by search_records.")
    payload = _get(f"/v1/documents/{digest}")
    document = payload.get("document")
    if not isinstance(document, dict) or document.get("document_sha256") != digest:
        raise SourceError("FREEDAR returned a different document than the requested hash.")
    row = _row(document)
    lines = [COLLECTION, f"Indexed document SHA-256: {digest}", f"Provenance: {row['provenance']}"]
    links = []
    for observation in row["source_observations"]:
        title = observation.get("title") or "Issuer document"
        url = observation["source_url"]
        links.append({"text": title, "url": url})
        lines.extend([f"Title: {title}", f"Issuer website collection: {observation.get('collection_id')}",
                      f"Original issuer document: {url}"])
    lines.append("Read the original issuer document URL for report text, dates and financial figures.")
    return {"source": "sedar_plus", "kind": "filing_index", "native_id": native_id, "url": row["url"],
            "published": None, "text": "\n".join(lines), "data": {**row, "links": links, "collection": COLLECTION}}
