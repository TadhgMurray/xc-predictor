"""
sweep_pages.py -- load every page type against the running site, timed.

One command to answer "which pages are slow": samples real ids from the
database (or takes yours), requests each page over HTTP like a browser
would, prints load times slowest-first, then pulls /debug/queries so the
statements behind the slow pages are in the same output. Run it twice to
see what caching buys: the first pass is cold, the second warm.

Usage:
    python scripts/sweep_pages.py
    python scripts/sweep_pages.py --school "MIT" --base http://127.0.0.1:5000

The site must be running.
"""

import argparse
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, "scripts")
from database import getConn   # noqa: E402


def pick_samples(school_arg):
    """Real ids to visit, cheaply: one row from each results table plus
    the small course table. Override the school to stress a big one."""
    s = {}
    cm = getConn()
    conn = cm.__enter__()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT person_id, meet_id, div_id, school FROM results
            WHERE person_id IS NOT NULL AND school IS NOT NULL LIMIT 1
        """)
        row = cur.fetchone()
        if row:
            s["athlete"], s["xc_meet"], s["xc_div"], s["school"] = row
        cur.execute("""
            SELECT person_id, meet_id, event_id, div_id FROM results_tf
            WHERE person_id IS NOT NULL LIMIT 1
        """)
        row = cur.fetchone()
        if row:
            s["athlete_b"], s["tf_meet"], s["tf_event"], s["tf_div"] = row
        cur.execute("""
            SELECT substring(course_name from 4) FROM course_common_distance
            ORDER BY n_results DESC LIMIT 1
        """)
        row = cur.fetchone()
        if row:
            s["course"] = row[0]
    finally:
        cm.__exit__(None, None, None)
    if school_arg:
        s["school"] = school_arg
    return s


def urls_for(s):
    q = urllib.parse.quote
    out = [("home", "/")]
    out.append(("rankings performance",
                "/rankings?board=performance&sport=XC&pool=hs_m"))
    out.append(("rankings ability", "/rankings?board=ability&sport=XC&pool=hs_m"))
    out.append(("rankings pr 5000",
                "/rankings?board=pr&sport=XC&pool=hs_m&distance=5000"))
    if "athlete" in s:
        out.append(("athlete", f"/athlete/{s['athlete']}"))
    if "school" in s:
        sc = q(str(s["school"]), safe="")
        out.append(("school XC", f"/school/{sc}?sport=XC"))
        out.append(("school TF", f"/school/{sc}?sport=TF"))
        out.append(("school PRs XC", f"/school/{sc}/prs?sport=XC"))
        out.append(("school PRs TF", f"/school/{sc}/prs?sport=TF"))
    if "xc_meet" in s:
        out.append(("meet XC", f"/meet/xc/{s['xc_meet']}"))
        out.append(("race XC", f"/race/xc/{s['xc_meet']}/{s['xc_div']}"))
    if "tf_meet" in s:
        out.append(("meet TF", f"/meet/tf/{s['tf_meet']}"))
        out.append(("race TF",
                    f"/race/tf/{s['tf_meet']}/{s['tf_event']}/{s['tf_div']}"))
        out.append(("compiled TF", f"/meet/tf/{s['tf_meet']}/compiled"))
    if "course" in s:
        out.append(("course", f"/course/{q(str(s['course']), safe='')}"))
    if "athlete" in s and "athlete_b" in s:
        out.append(("compare",
                    f"/compare?a={s['athlete']}&b={s['athlete_b']}"))
    out.append(("search", "/search?q=smith"))
    out.append(("conversions", "/conversions"))
    return out


def fetch(base, path, timeout=120):
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(base + path, timeout=timeout) as resp:
            body = resp.read()
            return (time.perf_counter() - t0) * 1000, resp.status, len(body)
    except urllib.error.HTTPError as e:
        return (time.perf_counter() - t0) * 1000, e.code, 0
    except Exception as e:                     # noqa: BLE001
        return (time.perf_counter() - t0) * 1000, str(e)[:40], 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:5000")
    ap.add_argument("--school")
    args = ap.parse_args()

    samples = pick_samples(args.school)
    print("samples:", samples)
    results = []
    for label, path in urls_for(samples):
        ms, status, size = fetch(args.base, path)
        results.append((ms, label, path, status, size))
        print(f"  {ms:9.1f}ms  {str(status):>4}  {label:<22} {path}")

    print("\nSLOWEST PAGES")
    print("=" * 70)
    for ms, label, path, status, size in sorted(results, reverse=True)[:12]:
        print(f"  {ms:9.1f}ms  {str(status):>4}  {label:<22} {path}")

    print("\nQUERY REPORT (/debug/queries)")
    print("=" * 70)
    try:
        with urllib.request.urlopen(args.base + "/debug/queries",
                                    timeout=30) as resp:
            print(resp.read().decode("utf-8", "replace"))
    except Exception as e:                     # noqa: BLE001
        print(f"could not fetch /debug/queries: {e}")


if __name__ == "__main__":
    main()
