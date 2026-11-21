"""Try a series end to end against its publisher, through the tools the researcher uses. It makes live requests,
so it is run by hand, from the repository root:

    python tests/live_series.py SERIES [SERIES ...] [field=value ...] [--quick]

For each series named (all of them, if none is): an open search; then, for each field the first record has, a
search by that value, which must find that record, so a filter that silently matches nothing fails here; a
continued search, which must not repeat a record; a read of the record; a read of a small result set into the
series' table; and a query over the table and the views across series. field=value seeds every search of a
series that is read by a parent's field. --quick gives each search six seconds and skips the result set."""
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from distress_signals.util import OUT, load_env  # noqa: E402

(OUT / "dev").mkdir(parents=True, exist_ok=True)
os.environ.setdefault("DISTRESS_DB", str(OUT / "dev" / "test.db"))
os.environ.setdefault("DISTRESS_RUN_ID", "run_dev")
load_env()
from distress_signals import sources, tools  # noqa: E402

quick = "--quick" in sys.argv
if quick:
    sources.SEARCH_SECONDS = 6
ids = [a for a in sys.argv[1:] if not a.startswith("--") and "=" not in a] or sorted(sources.available())
SEED = dict(a.split("=", 1) for a in sys.argv[1:] if "=" in a and not a.startswith("--"))
bad = []

def call(fn, *a, **k):
    t = time.time()
    try:
        return fn(*a, **k), time.time() - t, None
    except Exception as e:
        return None, time.time() - t, f"{type(e).__name__}: {e}"

for sid in ids:
    s = sources.get(sid)
    print(f"\n===== {sid} =====")
    seed = {k: v for k, v in SEED.items() if k in s.keys()}
    got, secs, err = call(tools.search_records, sid, "", seed, 3)
    if err or not got["records"]:
        print(f"  open search FAILED ({secs:.1f}s): {err or 'no records'}"); bad.append((sid, "open search")); continue
    first = got["records"][0]
    print(f"  open search: {got['count']} records in {secs:.1f}s | total {got.get('total')} | cursor {'yes' if got.get('cursor') else 'no'} | order {got.get('order')}")
    print("  first:", json.dumps(first, ensure_ascii=False)[:420])
    empty = [f.name for f in s.fields if f.type != "full text" and all(r.get(f.name) in (None, "", []) for r in got["records"])]
    if empty: print("  fields empty in every record shown:", empty)
    # each field of the first record, used as a filter, must find it
    for f in s.fields:
        v = first.get(f.name)
        if v in (None, "", []) or f.type == "full text": continue
        v1 = v[0] if isinstance(v, list) else v
        if f.type == "date": flt = {f"{f.name}_from": str(v1)[:10], f"{f.name}_until": str(v1)[:10]}
        elif f.ranged: flt = {f"{f.name}_min": v1, f"{f.name}_max": v1}
        elif f.type == "text": flt = {f.name: " ".join(str(v1).split()[:3])}
        else: flt = {f.name: str(v1)}
        flt = seed | flt
        hit, n, examined, cur, secs, err = False, 0, None, None, 0, None
        for _ in range(1 if quick else 4):
            got2, s2, err = call(tools.search_records, sid, "", flt, 50, cur)
            secs += s2
            if err: break
            n += got2["count"]; examined = got2.get("examined"); cur = got2.get("cursor")
            hit = hit or any(r["record_id"] == first["record_id"] for r in got2["records"])
            if hit or not cur: break
        how = "publisher" if examined is None else f"registry, {examined} checked"
        flag = "ok " if hit else "..  " if cur and not err else "MISS"   # ".." = the search has more to read and stopped at its time budget
        if not hit and not (cur and not err): bad.append((sid, f"filter {list(flt)[-1]}"))
        print(f"  {flag} {json.dumps(flt, ensure_ascii=False)[:90]:<92} {n:>3} found, {how}, {secs:.1f}s{' | ' + err if err else ''}{' (more to read)' if cur and not hit else ''}")
    # continuing a search does not repeat records
    a, _, err = call(tools.search_records, sid, "", seed, 2)
    if a and a.get("cursor"):
        b, _, err = call(tools.search_records, sid, "", seed, 2, a["cursor"])
        if err: print("  cursor FAILED:", err); bad.append((sid, "cursor"))
        else:
            same = {r["record_id"] for r in a["records"]} & {r["record_id"] for r in b["records"]}
            print(f"  cursor: next {b['count']} records, {'REPEATS ' + str(same) if same else 'none repeated'}")
            if same or not b["records"]: bad.append((sid, "cursor repeats"))
    else:
        print("  cursor: none (the open search reached the end)" if a else f"  cursor FAILED: {err}")
    # read the record
    view, secs, err = call(tools.read_record, first["record_id"])
    if err: print(f"  read FAILED ({secs:.1f}s): {err}"); bad.append((sid, "read")); continue
    print(f"  read: {view['length']:,} chars in {secs:.1f}s | {view.get('title')} | links {len(view.get('links') or [])}")
    print("    " + view["text"][:330].replace("\n", "\n    "))
    # a small result set into the table, then SQL over it
    datef = next((f for f in s.fields if f.type == "date" and first.get(f.name)), None)
    idf = next((f for f in s.fields if f.type in ("identifier",) and first.get(f.name)), None)
    flt = ({f"{datef.name}_from": str(first[datef.name])[:10], f"{datef.name}_until": str(first[datef.name])[:10]} if datef
           else {idf.name: str(first[idf.name][0] if isinstance(first[idf.name], list) else first[idf.name])} if idf else {})
    got3, _, err = call(tools.search_records, sid, "", seed | flt, 1)
    if not err and not quick:
        view, secs, err = call(tools.read_record, got3["result_set"])
        if err: print(f"  result set FAILED ({secs:.1f}s): {err[:200]}"); bad.append((sid, "result set"))
        else:
            q, _, err = call(tools.compute, f'SELECT COUNT(*), COUNT(DISTINCT record_id) FROM "{sid}"')
            d, _, _ = call(tools.compute, f"SELECT field, COUNT(*), MIN(date), MAX(date) FROM record_dates WHERE series='{sid}' GROUP BY field")
            n, _, _ = call(tools.compute, f"SELECT field, COUNT(*), MIN(name) FROM record_names WHERE series='{sid}' GROUP BY field")
            print(f"  result set {got3['result_set'][:80]}: {view.get('records')} records in {secs:.1f}s | table rows, distinct: {q['rows'][0] if q else err}")
            print(f"    dates view: {d['rows'] if d else None}\n    names view: {n['rows'] if n else None}")
print("\nFAILED:" if bad else "\nall passed", bad or "")
