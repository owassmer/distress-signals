"""Export the public run with its cases' memos, and preserve links to the former research page."""

from pathlib import Path

from . import memo, watch


LEGACY_PAGE = """<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Distress Signals</title>
<script>location.replace("index.html" + location.search + location.hash);</script>
<p><a href="index.html">Open findings</a></p></html>
"""


def export_html(run_id, memo_at=None):
    return watch.export_html(run_id, public=True, example_deal=True, memo_at=memo_at)


def write_memos(folder, cases, back="../index.html"):
    """Each case's memo beside a page: memos/<name>.html, with the way back to the case on that page and its PDF
    across the top of the screen, and memos/<name>.pdf, the same printed. Returns the names written."""
    (folder / "memos").mkdir(parents=True, exist_ok=True)
    names = []
    for c in cases:
        name, doc = memo.filename(c), memo.document(c)
        bar = {"back": f"{back}#case/{c['id']}", "back_label": "Case", "pdf": f"{name}.pdf", "name": f"{name}.pdf"}
        (folder / "memos" / f"{name}.html").write_text(memo.html(doc, bar=bar), encoding="utf-8")
        memo.pdf(memo.html(doc), folder / "memos" / f"{name}.pdf",
                 running=f"{doc['company']} · {doc['event']} · as of {doc['as_of']}")
        names.append(name)
    return names


def export_guide(run_id, folder):
    """Write a self-contained public page, each of its cases' memos beside it as a page and a PDF, and a
    forward for legacy run.html bookmarks. The folder holds everything the page links to, so it can be served
    from any static host."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    shown = {}

    def memo_at(c):                                      # called for each case the page shows
        name = memo.filename(c)
        shown[name] = c
        return {"page": f"memos/{name}.html", "pdf": f"memos/{name}.pdf", "name": f"{name}.pdf"}

    (folder / "index.html").write_text(export_html(run_id, memo_at), encoding="utf-8")
    write_memos(folder, shown.values())
    (folder / "run.html").write_text(LEGACY_PAGE, encoding="utf-8")
    (folder / ".nojekyll").write_text("")
    return folder
