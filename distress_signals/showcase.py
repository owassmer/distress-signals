"""Export the public run with its cases' memos, and preserve links to the former research page."""

import datetime as dt
import html
import re
from pathlib import Path

from . import briefs, memo, watch


LEGACY_PAGE = """<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Distress Signals</title>
<script>location.replace("index.html" + location.search + location.hash);</script>
<p><a href="index.html">Open findings</a></p></html>
"""


def export_html(run_id, memo_at=None, home=watch.REPO):
    return watch.export_html(run_id, public=True, home=home, example_deal=True, memo_at=memo_at)


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


def export_guide(run_id, folder, home=watch.REPO, legacy=True):
    """Write a self-contained public page, each of its cases' memos beside it as a page and a PDF, and a
    forward for legacy run.html bookmarks. The folder holds everything the page links to, so it can be served
    from any static host. `home` is where the page's way out leads: the repository, or a site's home that
    lists this run among others. Returns (the folder, the cases the page shows)."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    shown = {}

    def memo_at(c):                                      # called for each case the page shows
        name = memo.filename(c)
        shown[name] = c
        return {"page": f"memos/{name}.html", "pdf": f"memos/{name}.pdf", "name": f"{name}.pdf"}

    (folder / "index.html").write_text(export_html(run_id, memo_at, home), encoding="utf-8")
    write_memos(folder, shown.values())
    if legacy:
        (folder / "run.html").write_text(LEGACY_PAGE, encoding="utf-8")
        (folder / ".nojekyll").write_text("")
    return folder, list(shown.values())


# ---------- a site: several runs, and a home that lists them ----------

ASKED = {"mandate": "The agent searched public records on its own for companies whose revenue rides on an upcoming event.",
         "company": "The agent was given one company and asked which upcoming events its revenue depends on.",
         "event": "The agent was given one event and asked which companies' revenue depends on it.",
         "source": "The agent was given one document and asked what revenue it puts at stake.",
         "refresh": "The agent brought one saved case up to date against the records.",
         "lead": "The agent followed up a lead from an earlier run."}

HOME_STYLE = """
.home h1{margin-top:6px}.home h2{margin:34px 0 4px}.home .sub{margin-bottom:18px}
.runcard{background:var(--card);border:1px solid var(--line);border-radius:16px;margin-bottom:16px;overflow:hidden}
.rc-head{display:flex;gap:20px;align-items:flex-start;justify-content:space-between;padding:20px 22px 16px}
.rc-head small{display:block;color:var(--mute);font-size:12.5px;font-weight:600;letter-spacing:.01em}
.rc-head h3{margin:3px 0 4px;font-size:20px;letter-spacing:-.015em;line-height:1.25;font-weight:740}
.rc-head h3 a{color:var(--ink)}.rc-head p{margin:0;color:var(--soft);font-size:14.5px;max-width:640px}
.rc-head .btn{flex:0 0 auto;margin-top:2px}.rc-head .btn.dark{color:#fff}.rc-head .btn:hover{text-decoration:none}
.rc-cases{width:100%;border-collapse:collapse;border-top:1px solid var(--line);font-size:14px}
.rc-cases th{text-align:left;font-size:12px;font-weight:600;color:var(--mute);padding:9px 22px;background:#fafbfc;border-bottom:1px solid var(--line)}
.rc-cases td{padding:12px 22px;border-bottom:1px solid var(--line2);vertical-align:top}.rc-cases tr:last-child td{border-bottom:0}
.rc-cases .who a{color:var(--ink);font-weight:650}.rc-cases .who span{display:block;color:var(--mute);font-size:13px}
.rc-cases .rc-stake{white-space:nowrap;font-variant-numeric:tabular-nums}.rc-cases .rc-stake b{display:block;font-size:16px}
.rc-cases .rc-stake span{display:block;color:var(--mute);font-size:12.5px}
.rc-cases .get{white-space:nowrap;text-align:right}.rc-cases .get a{font-weight:600;font-size:13px;margin-left:14px}
.rc-cases th.r,.rc-cases td.r{text-align:right}
.home .foot{color:var(--mute);font-size:13px;margin-top:26px}
@media(max-width:640px){.rc-head{flex-direction:column;gap:12px;padding:16px}.rc-cases th,.rc-cases td{padding:10px 16px}
  .rc-cases thead{display:none}.rc-cases tr{display:block;border-bottom:1px solid var(--line2)}.rc-cases td{display:block;border:0;padding:4px 16px}
  .rc-cases td:first-child{padding-top:12px}.rc-cases td:last-child{padding-bottom:12px}.rc-cases .get{text-align:left}.rc-cases .get a{margin:0 14px 0 0}}
"""


def _slug(run, taken):
    """A short folder name for a run: what it was, and what it was about."""
    kind, seed = run["seed_kind"], (run.get("seed") or "")
    base = "open-search" if kind == "mandate" else re.sub(r"[^a-z0-9]+", "-", seed.lower()).strip("-")[:40].strip("-") or kind
    name, n = base, 2
    while name in taken:
        name, n = f"{base}-{n}", n + 1
    taken.add(name)
    return name


def home(runs):
    """The site's home: each run as a card with what the agent was asked, when, and the cases it holds, each
    with what is at stake and its memo. `runs` is [{slug, run, cases}] in the order to show."""
    e = html.escape
    cards = []
    for r in runs:
        run, cases = r["run"], r["cases"]
        start = dt.datetime.fromisoformat(run["started"])
        mins = int((dt.datetime.fromisoformat(run["finished"]) - start).total_seconds() // 60) if run.get("finished") else None
        companies = len({c["company"]["name"] for c in cases})
        count = f"{len(cases)} case{'s' if len(cases) != 1 else ''}" + (f" at {companies} companies" if companies > 1 else "")
        meta = " · ".join(x for x in (f"{start:%b} {start.day}, {start.year}", f"{mins} min" if mins is not None else None, count) if x)
        asked = ASKED.get(run["seed_kind"]) or (run.get("seed") or "")
        rows = []
        for c in sorted(cases, key=lambda c: -abs(briefs.leading(c["results"])["shortfall"])):
            d, name = memo.document(c), memo.filename(c)
            share = f"{d['at_stake']['share'].lstrip('−+')} of revenue · " if d["at_stake"]["share"] else ""
            rows.append(
                f'<tr><td class="who"><a href="{r["slug"]}/index.html#case/{c["id"]}">{e(c["company"]["name"])}</a>'
                f'<span>{e(d["event"])}</span></td>'
                f'<td class="rc-stake r"><b class="{d["at_stake"]["tone"]}">{e(d["at_stake"]["impact"])}</b><span>{e(share + d["period"])}</span></td>'
                f'<td class="get"><a href="{r["slug"]}/memos/{name}.html">Memo</a>'
                f'<a href="{r["slug"]}/memos/{name}.pdf" download="{name}.pdf">PDF</a></td></tr>')
        table = ('<table class="rc-cases"><thead><tr><th>Company and event</th><th class="r">Largest modeled revenue impact</th><th></th></tr></thead>'
                 f'<tbody>{"".join(rows)}</tbody></table>') if rows else ""
        cards.append(
            f'<article class="runcard"><div class="rc-head"><div><small>{e(meta)}</small>'
            f'<h3><a href="{r["slug"]}/index.html">{e(watch._title(run))}</a></h3><p>{e(asked)}</p></div>'
            f'<a class="btn dark" href="{r["slug"]}/index.html">Open run →</a></div>{table}</article>')
    description = e(watch.GUIDE_PURPOSE, quote=True)
    return (
        f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>{e(watch.GUIDE_TITLE)} · Distress Signals</title><meta name="description" content="{description}">'
        f'<meta property="og:title" content="{e(watch.GUIDE_TITLE, quote=True)} · Distress Signals">'
        f'<meta property="og:description" content="{description}"><meta property="og:type" content="website">'
        f'<link rel="icon" href="{watch.FAVICON}"><style>{watch.STYLE}{watch.SHELL_STYLE}{HOME_STYLE}{watch.PUBLICATION}</style></head><body>'
        f'<header class="top run-nav"><div class="in"><a class="brand" href="index.html" aria-label="Distress Signals">'
        f'<img src="{watch.FAVICON}" alt=""><span>Distress Signals</span></a>'
        f'<a class="project-credit" href="https://github.com/owassmer">Built by <strong>Owen Wassmer</strong></a>'
        f'<a class="project-link source-link" href="{watch.REPO}" aria-label="Source on GitHub" title="Source on GitHub">'
        f'<span>GitHub</span></a></div></header>'
        f'<main class="wrap home"><h1>{e(watch.GUIDE_TITLE)}</h1><p class="lede">{e(watch.GUIDE_PURPOSE)}</p>'
        f'<h2>Research runs</h2><p class="sub">Each run is one assignment given to the agent. Open one to see what it found and replay '
        f'its research, or go straight to a case\'s memo.</p>{"".join(cards)}'
        f'<p class="foot">Every figure is computed from the case as the agent saved it, and every quotation was checked word for word '
        f'against the record it cites.</p></main></body></html>')


def export_site(run_ids, folder):
    """A public site for several runs: the home (index.html) lists them, and each run's guide sits in a folder
    of its own with its cases' memos. Returns the folder."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    runs, taken = [], set()
    for rid in run_ids:
        run = watch._run_row(rid)
        if not run:
            raise SystemExit(f"no run {rid!r}")
        slug = _slug(run, taken)
        _, cases = export_guide(rid, folder / slug, home="../index.html", legacy=False)
        runs.append({"slug": slug, "run": run, "cases": cases})
    (folder / "index.html").write_text(home(runs), encoding="utf-8")
    (folder / "run.html").write_text(LEGACY_PAGE, encoding="utf-8")
    (folder / ".nojekyll").write_text("")
    return folder
