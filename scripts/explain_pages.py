"""
explain_pages.py -- EXPLAIN ANALYZE the website's hot queries, verbatim.

Runs the EXACT SQL the pages run (imported from the page modules, not
copied) under EXPLAIN (ANALYZE, BUFFERS) and prints each plan with its
real timing, flagging sequential scans on the big tables. Start here
when a page is slow; /debug/queries on the site says WHICH statement,
this says WHY.

Usage:
    python scripts/explain_pages.py --school "MIT"
    python scripts/explain_pages.py --school "MIT" --sport TF
    python scripts/explain_pages.py --meet 640852        # TF scoring query
"""

import argparse
import sys

sys.path.insert(0, "scripts")
sys.path.insert(0, "racecast")
from database import getConn   # noqa: E402

BIG_TABLES = ("ranking_results", "results_tf", "results", "athletes",
              "athlete_season")


def explain(cur, title, sql, params):
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)
    cur.execute("EXPLAIN (ANALYZE, BUFFERS) " + sql, params)
    plan = [r[0] for r in cur.fetchall()]
    seq = [ln for ln in plan
           if "Seq Scan" in ln and any(t in ln for t in BIG_TABLES)]
    for ln in plan:
        print("  " + ln)
    if seq:
        print("\n  !! SEQUENTIAL SCAN ON A BIG TABLE -- the index is "
              "missing, invalid, or unusable for this predicate:")
        for ln in seq:
            print("  !!" + ln)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--school")
    ap.add_argument("--sport", default="TF", choices=("XC", "TF"))
    ap.add_argument("--meet", type=int)
    args = ap.parse_args()
    if not args.school and not args.meet:
        ap.error("pass --school and/or --meet")

    cm = getConn()
    conn = cm.__enter__()
    try:
        cur = conn.cursor()

        if args.school:
            import school_prs
            explain(cur, f"School PRs, running rows ({args.sport})",
                    school_prs.runningSql(args.sport),
                    {"school": args.school, "sport": args.sport})
            if args.sport == "TF":
                explain(cur, "School PRs, field rows",
                        school_prs.fieldSql(), {"school": args.school})
            explain(cur, f"School page, best performances ({args.sport})", """
                SELECT rr.person_id, rr.speed_rating
                FROM   ranking_results rr
                WHERE  rr.school = %(school)s AND rr.sport = %(sport)s
                  AND  rr.speed_rating IS NOT NULL
                ORDER  BY rr.speed_rating DESC LIMIT 100
            """, {"school": args.school, "sport": args.sport})

        if args.meet:
            explain(cur, f"TF meet scoring rows (meet {args.meet})",
                    # same coalesced-name select the scorer runs
                    """
                SELECT r.result_id
                FROM results_tf r
                JOIN meets_tf m ON m.meet_id = r.meet_id
                               AND m.div_id = r.div_id
                               AND m.event_id = r.event_id
                WHERE r.meet_id = %(meet)s
                  AND (r.time_seconds IS NOT NULL OR r.mark IS NOT NULL)
            """, {"meet": args.meet})

        print("\nindexes on the big tables:")
        for t in BIG_TABLES:
            cur.execute("""
                SELECT i.relname, idx.indisvalid
                FROM   pg_index idx
                JOIN   pg_class i ON i.oid = idx.indexrelid
                JOIN   pg_class tt ON tt.oid = idx.indrelid
                WHERE  tt.relname = %s
            """, (t,))
            for name, valid in cur.fetchall():
                flag = "" if valid else "   <-- INVALID"
                print(f"  {t}: {name}{flag}")
    finally:
        cm.__exit__(None, None, None)


if __name__ == "__main__":
    main()
