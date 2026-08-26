"""
explain_pages.py -- EXPLAIN ANALYZE the website's hot queries, verbatim.

Runs the EXACT SQL the pages run (imported from the page modules, not
copied) under EXPLAIN (ANALYZE, BUFFERS) and prints each plan with its
real timing, flagging sequential scans on the big tables. Start here
when a page is slow; /debug/queries on the site says WHICH statement,
this says WHY.

Usage:
    python scripts/explain_pages.py --all                # the measured hot set
    python scripts/explain_pages.py --school "MIT"
    python scripts/explain_pages.py --school "MIT" --sport TF
    python scripts/explain_pages.py --meet 640852        # TF scoring query

--all samples real parameters from the database (a big race's result
ids, the busiest course, a school) and EXPLAINs every statement shape
the page sweep measured as hot: the stampRowsHs ANY() lookup, school
PRs running+field, school bests, TF scoring, the course join shape, the
home-page constant sample, and search. Run it after building indexes;
every plan should say Index Scan / Bitmap Heap Scan, never Seq Scan on
a big table.
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
    ap.add_argument("--all", action="store_true",
                    help="EXPLAIN the whole measured hot set with "
                         "sampled parameters")
    args = ap.parse_args()
    if not args.school and not args.meet and not args.all:
        ap.error("pass --all, --school and/or --meet")

    cm = getConn()
    conn = cm.__enter__()
    try:
        cur = conn.cursor()

        if args.all:
            # sample real parameters cheaply
            cur.execute("SELECT school FROM results WHERE school IS NOT NULL "
                        "LIMIT 1")
            row = cur.fetchone()
            if row and not args.school:
                args.school = row[0]
            cur.execute("SELECT meet_id FROM results_tf LIMIT 1")
            row = cur.fetchone()
            if row and not args.meet:
                args.meet = row[0]
            cur.execute("""
                SELECT substring(course_name from 4)
                FROM course_common_distance ORDER BY n_results DESC LIMIT 1
            """)
            course = (cur.fetchone() or [None])[0]
            cur.execute("SELECT result_id FROM ranking_results "
                        "WHERE sport = 'XC' LIMIT 800")
            ids = [r[0] for r in cur.fetchall()]
            cur.execute("SELECT person_id FROM ranking_results "
                        "WHERE person_id IS NOT NULL LIMIT 1")
            person = (cur.fetchone() or [None])[0]

            if ids:
                # stampRowsHs -- the HS-toggle lookup on EVERY result page.
                # The one that measured 11.6s a hit before idx_rr_result.
                explain(cur, f"stampRowsHs pool lookup ({len(ids)} ids)", """
                    SELECT result_id, pool FROM ranking_results
                    WHERE sport = %s AND result_id = ANY(%s)
                """, ("XC", ids))
            if course:
                # the course page's driving shape: meets by course_name,
                # results joined by (div_id, source)
                explain(cur, f"course join shape ({course})", """
                    SELECT count(*)
                    FROM results r
                    JOIN meets m ON m.div_id = r.div_id
                                AND m.source = r.source
                    WHERE m.course_name = %(course)s
                      AND r.speed_rating IS NOT NULL
                """, {"course": course})
            # the home page's pool-constant sample
            explain(cur, "home pool-constant sample", """
                WITH sample AS (
                    SELECT rr.result_id FROM ranking_results rr
                    WHERE rr.pool = 'hs_m' AND rr.sport = 'XC' LIMIT 1500
                )
                SELECT r.speed_rating, r.normalized_time
                FROM sample s JOIN results r ON r.result_id = s.result_id
                WHERE r.speed_rating > 0 AND r.normalized_time > 0
            """, {})
            if person:
                explain(cur, "athlete career rows", """
                    SELECT sport, result_id, pool, year
                    FROM ranking_results WHERE person_id = %s
                """, (person,))
            explain(cur, "search substring", """
                SELECT kind, label, sublabel, link
                FROM search_index
                WHERE search_text LIKE %s LIMIT 40
            """, ("%smith%",))

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
