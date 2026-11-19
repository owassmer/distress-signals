"""Export the public run and preserve links to the former research page."""

from pathlib import Path

from . import watch


LEGACY_PAGE = """<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Distress Signals</title>
<script>location.replace("index.html" + location.search + location.hash);</script>
<p><a href="index.html">Open findings</a></p></html>
"""


def export_html(run_id):
    return watch.export_html(run_id, public=True, example_deal=True)


def export_guide(run_id, folder):
    """Write a self-contained public page and forward legacy run.html bookmarks."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "index.html").write_text(export_html(run_id), encoding="utf-8")
    (folder / "run.html").write_text(LEGACY_PAGE, encoding="utf-8")
    (folder / ".nojekyll").write_text("")
    return folder
