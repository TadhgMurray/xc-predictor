# Project: xc-predictor
# File:    scripts/census_meet_collision.py
# Purpose: measure the tfrrs/anet meet_id collision BEFORE coding a split.
#
# The proposal on the table is "div_id < 100 means tfrrs". This script tests
# that guess against two discriminators that are definitive rather than
# numeric folklore:
#
#   meets_tfrrs membership  -- a meet_id with a meets_tfrrs row came from the
#                              tfrrs scraper, full stop.
#   person_id IS NULL       -- tfrrs rows carry athlete_id and no person_id
#                              (see panels.py ATHLETE_KEY).
#
# Read-only. Run it, paste the output, and the meet-page split gets coded
# against whichever column the data says is clean -- for TF too, where the
# div_id guess admittedly has no story.
import sys

sys.path.insert(0, "scripts")
from database import getConn                     # noqa: E402
from psycopg2.extras import RealDictCursor       # noqa: E402


def section(title):
    print(f"\n  {title}\n  " + "-" * len(title))


def run(cur, table):
    section(f"{table}: does div_id < 100 actually mean tfrrs?")
    cur.execute(f"""
        SELECT CASE WHEN r.div_id < 100 THEN 'div < 100' ELSE 'div >= 100' END AS bucket,
               count(DISTINCT r.meet_id)                    AS meets,
               count(*)                                     AS rows,
               count(*) FILTER (WHERE r.person_id IS NULL)  AS no_person_id,
               count(*) FILTER (WHERE EXISTS (
                   SELECT 1 FROM meets_tfrrs t
                   WHERE t.meet_id = r.meet_id))            AS in_meets_tfrrs
        FROM   {table} r
        GROUP  BY 1 ORDER BY 1
    """)
    print(f"    {'bucket':<12} {'meets':>9} {'rows':>12} "
          f"{'no person_id':>13} {'in meets_tfrrs':>15}")
    for r in cur.fetchall():
        print(f"    {r['bucket']:<12} {r['meets']:>9,} {r['rows']:>12,} "
              f"{r['no_person_id']:>13,} {r['in_meets_tfrrs']:>15,}")
    print("    (a clean split = each bucket ~0% or ~100% in the last two "
          "columns;\n     anything in between means the div_id guess is "
          "wrong for that table)")

    section(f"{table}: meets holding BOTH populations (the pages to split)")
    cur.execute(f"""
        SELECT r.meet_id,
               count(*) FILTER (WHERE r.div_id < 100)             AS low_div,
               count(*) FILTER (WHERE r.div_id >= 100)            AS high_div,
               count(*) FILTER (WHERE r.person_id IS NULL)        AS tfrrs_like,
               count(*) FILTER (WHERE r.person_id IS NOT NULL)    AS anet_like
        FROM   {table} r
        GROUP  BY r.meet_id
        HAVING count(*) FILTER (WHERE r.person_id IS NULL)     > 0
           AND count(*) FILTER (WHERE r.person_id IS NOT NULL) > 0
        ORDER  BY count(*) DESC
        LIMIT  20
    """)
    rows = cur.fetchall()
    if not rows:
        print("    none -- no meet mixes the two populations; a page-level "
              "source column is enough.")
        return
    print(f"    {'meet_id':>9} {'div<100':>8} {'div>=100':>9} "
          f"{'tfrrs-like':>11} {'anet-like':>10}")
    for r in rows:
        print(f"    {r['meet_id']:>9} {r['low_div']:>8,} {r['high_div']:>9,} "
              f"{r['tfrrs_like']:>11,} {r['anet_like']:>10,}")
    cur.execute(f"""
        SELECT count(*) AS n FROM (
            SELECT r.meet_id FROM {table} r
            GROUP  BY r.meet_id
            HAVING count(*) FILTER (WHERE r.person_id IS NULL)     > 0
               AND count(*) FILTER (WHERE r.person_id IS NOT NULL) > 0) q
    """)
    print(f"    ... {cur.fetchone()['n']:,} colliding meets in total")


def main():
    with getConn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        for table in ("results", "results_tf"):
            run(cur, table)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
