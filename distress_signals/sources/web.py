"""Read any URL into text: HTML (scripts and styles dropped, links kept aside) or PDF."""

import io
import logging
import os
import re
import unicodedata
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

from ..util import Missing, SourceError, Throttled, Unreadable, session

UA = "Mozilla/5.0 (compatible; distress-signals; +https://github.com/owassmer/distress-signals)"
logging.getLogger("pypdf").setLevel(logging.ERROR)  # font-parsing notices aren't errors
MAX_BYTES = 30_000_000
SKIP = {"script", "style", "noscript", "svg", "template", "iframe", "head", "nav"}
BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "table", "ul", "ol",
         "header", "footer", "blockquote", "pre", "dt", "dd", "hr", "main", "aside", "figure", "caption", "form"}
CELL = {"td", "th"}
PUBLISHED = ("article:published_time", "datepublished", "date", "dc.date", "publishdate", "pubdate")


class _Text(HTMLParser):
    def __init__(self, base):
        super().__init__(convert_charrefs=True)
        self.base, self.out, self.links, self.meta = base, [], [], {}
        self.skip, self.title, self._title, self._href, self._anchor = 0, "", False, None, []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "title":
            self._title = True
        elif tag == "meta":
            k = (a.get("property") or a.get("name") or a.get("itemprop") or "").lower()
            if k in PUBLISHED and a.get("content"):
                self.meta.setdefault("published", a["content"])
        elif tag in SKIP:
            self.skip += 1
        elif tag == "a" and a.get("href") and not self.skip:
            self._href, self._anchor = urljoin(self.base, a["href"]), []
        if tag in BLOCK:
            self.out.append("\n")
        elif tag in CELL:
            self.out.append(" | ")

    def handle_endtag(self, tag):
        if tag == "title":
            self._title = False
        elif tag in SKIP and self.skip:
            self.skip -= 1
        elif tag == "a" and self._href:
            words = " ".join("".join(self._anchor).split())
            if words and self._href.startswith("http"):
                self.links.append({"text": words[:120], "url": self._href})
            self._href = None
        if tag in BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        if self._title:
            self.title += data
        if self.skip:
            return
        self.out.append(data)
        if self._href:
            self._anchor.append(data)


def _tidy(text):
    text = "".join(c for c in text if unicodedata.category(c) != "Cf")  # zero-width spaces and joiners print nothing
    lines = [" ".join(line.split()) for line in text.splitlines()]
    text = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _pdf(content):
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(content))
    pages = [f"[page {i}]\n{(p.extract_text() or '').strip()}" for i, p in enumerate(reader.pages[:400], 1)]
    return _tidy("\n\n".join(pages)), (reader.metadata or {}).get("/Title") or ""


def read(url):
    """{kind, source, url, published, text, data:{title, links, final_url, content_type}}."""
    host = (urlparse(url).hostname or "").lower()
    sec = host == "sec.gov" or host.endswith(".sec.gov")
    headers = {"User-Agent": os.environ.get("SEC_USER_AGENT") if sec else UA,
               "Accept": "text/html,application/xhtml+xml,application/pdf,text/plain;q=0.9,*/*;q=0.5"}
    if sec and not headers["User-Agent"]:
        raise SourceError("SEC requires SEC_USER_AGENT (a name and contact email) in .env")
    try:
        r = session().get(url, headers=headers, timeout=60, allow_redirects=True, stream=True)
        content = r.raw.read(MAX_BYTES + 1, decode_content=True)
    except Exception as e:
        raise SourceError(f"couldn't fetch {url}: {type(e).__name__}: {str(e)[:150]}. The store has not read it.")
    if r.status_code in (404, 410):
        raise Missing(f"{url} answered {r.status_code}: the publisher has nothing at that address.")
    if r.status_code == 429:
        raise Throttled(f"{url} answered 429: the publisher limits how fast it is read. The same read can be made later.")
    if r.status_code >= 400:
        raise SourceError(f"{url} answered {r.status_code}: its publisher refuses this reader, and the store has not read it.")
    if len(content) > MAX_BYTES:
        raise Unreadable(f"{url} is larger than {MAX_BYTES // 1_000_000} MB, the most a read takes")
    ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    title, links, published = "", [], None
    pdf = ctype == "application/pdf" or content[:5] == b"%PDF-"
    if pdf:
        try:
            text, title = _pdf(content)
        except Exception as e:  # pypdf's errors for a damaged file
            raise Unreadable(f"{url} is a PDF that could not be parsed ({type(e).__name__})")
    elif "html" in ctype or content.lstrip()[:200].lower().startswith((b"<!doctype html", b"<html")) or b"<body" in content[:5000].lower():
        p = _Text(r.url)
        p.feed(content.decode(r.encoding or r.apparent_encoding or "utf-8", errors="replace"))
        text, title, published = _tidy("".join(p.out)), " ".join(p.title.split()), p.meta.get("published")
        seen, links = set(), []
        for link in p.links:
            if link["url"] not in seen:
                seen.add(link["url"])
                links.append(link)
    elif ctype.startswith("text/") or ctype in ("application/json", "application/xml"):
        text = _tidy(content.decode(r.encoding or "utf-8", errors="replace"))
    else:
        raise Unreadable(f"{url} is {ctype or 'an unknown type'}, which read_record can't read")
    if pdf and not re.sub(r"\[page \d+\]", "", text).strip():
        raise Unreadable(f"{url} is a PDF with no text layer: its pages are images")
    if not pdf and len(text) < 200:  # an HTML shell whose content a script fills in
        raise Unreadable(f"{url} gave no readable text (a script may draw it), and the store has not read it.")
    kind = "filing" if sec and "/Archives/" in url else "page"
    return {"kind": kind, "source": "sec" if sec else host, "url": url, "published": published, "text": text,
            "data": {"title": title, "links": links[:300], "final_url": r.url, "content_type": ctype}}
