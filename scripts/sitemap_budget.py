# Project: xc-predictor / scripts
# File:    sitemap_budget.py
# Purpose: How many URLs are we ASKING Google to crawl, and what would a
#          race floor on athlete pages cost and save?
#
# ★★ THE NUMBER THIS EXISTS FOR (Search Console, 2026-09-11):
#
#      Discovered - currently not indexed   3,529,125
#      Crawled - currently not indexed         32,098
#      Indexed                                121,000   (flat for 5 days)
#      Server error (5xx)                          69
#
#    "Discovered - currently not indexed" is Google saying it found the URL
#    and CHOSE NOT TO CRAWL IT. At 3.5 million it is not a bug to fix on the
#    page; it is Google declining the size of the ask. Crawl budget is set by
#    how fast the site answers and how much authority it has, and a new site
#    does not have 3.6M URLs worth of either. Meanwhile Googlebot made 619
#    requests in two hours while YandexBot made 11,246.
#
#    Note what is NOT the problem: 69 5xx. The stale-flag outage did not
#    cost the index. That hypothesis is dead, and this is the live one.
#
# ⚠ THE FLOOR WAS DELIBERATELY REMOVED. build_sitemap.collect() carries
#   "every athlete with a rated race (owner, 2026-09-05: 'it should be all
#   athletes'); the three-race floor is gone". That was a decision, not an
#   accident -- so this script REPORTS what each floor would submit and
#   changes nothing. The owner picks.
#
# ! REMOVING A URL FROM THE SITEMAP IS NOT noindex. The page still exists,
#   still renders, still ranks if Google finds it by a link. The sitemap is
#   a request for attention, and asking for 3.6M pages' worth from a site
#   that has earned 121k gets the whole list discounted.
#
#   /srv/venv/bin/python scripts/sitemap_budget.py
import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, _ROOT, os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "racecast")):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

from database import getConn

FLOORS = (1, 2, 3, 4, 5, 6, 8, 10, 15)


def _exists(cur, name):
    cur.execute("SELECT to_regclass(%s)", (name,))
    return cur.fetchone()[0] is not None


def athleteFloors(cur):
    """Athletes per career-race floor, summing n_races across seasons.

    ⚠ ACROSS SEASONS, NOT WITHIN ONE. build_sitemap groups by person_id
      and keeps anyone with a season of n_races >= 1, so a junior with one
      race in each of three seasons is already in. The floor that matters
      to a reader is how much is ON the page, which is the career total.
    """
    cur.execute("""
        SELECT total, count(*) FROM (
            SELECT person_id, sum(n_races) AS total
            FROM   athlete_season
            GROUP  BY person_id
        ) t GROUP BY total ORDER BY total
    """)
    hist = [(int(t), int(c)) for t, c in cur.fetchall() if t is not None]
    total = sum(c for _t, c in hist)
    out = []
    for f in FLOORS:
        kept = sum(c for t, c in hist if t >= f)
        out.append((f, kept, total - kept))
    return out, total


def otherKinds(cur):
    """Everything else the sitemap submits, so the athlete share is honest."""
    kinds = {}
    if _exists(cur, "results"):
        cur.execute("""SELECT count(*) FROM (
                         SELECT 1 FROM results
                         WHERE meet_id IS NOT NULL AND div_id IS NOT NULL
                         GROUP BY meet_id, div_id) t""")
        kinds["races XC"] = int(cur.fetchone()[0])
    if _exists(cur, "results_tf"):
        cur.execute("""SELECT count(*) FROM (
                         SELECT 1 FROM results_tf
                         WHERE meet_id IS NOT NULL AND event_id IS NOT NULL
                           AND div_id IS NOT NULL
                         GROUP BY meet_id, event_id, div_id) t""")
        kinds["races TF"] = int(cur.fetchone()[0])
    for table, label in (("meet_agg_xc", "meets XC"), ("meet_agg_tf", "meets TF")):
        if _exists(cur, table):
            cur.execute(f"SELECT count(*) FROM {table}")
            kinds[label] = int(cur.fetchone()[0])
    if _exists(cur, "school_identity"):
        cur.execute("""SELECT count(DISTINCT school) FROM school_identity
                       WHERE is_primary AND school IS NOT NULL""")
        kinds["schools"] = int(cur.fetchone()[0])
    if _exists(cur, "course_difficulties"):
        cur.execute("""SELECT count(DISTINCT course_name)
                       FROM course_difficulties
                       WHERE course_name IS NOT NULL
                         AND TRIM(course_name) <> ''""")
        kinds["courses"] = int(cur.fetchone()[0])
    return kinds


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--indexed", type=int, default=121_000,
                    help="pages Search Console reports indexed, for the ratio")
    args = ap.parse_args()

    with getConn() as conn:
        with conn.cursor() as cur:
            cur.execute("SET work_mem = '1GB'")
            if not _exists(cur, "athlete_season"):
                sys.exit("no athlete_season -- run the pipeline first")
            floors, total_athletes = athleteFloors(cur)
            kinds = otherKinds(cur)

    other = sum(kinds.values())
    print("\n  what the sitemap submits BESIDES athletes")
    for k, v in sorted(kinds.items(), key=lambda kv: -kv[1]):
        print(f"    {k:<12} {v:>12,}")
    print(f"    {'TOTAL':<12} {other:>12,}")

    print(f"\n  athlete pages by career races "
          f"({total_athletes:,} athletes with a rated race)")
    print(f"    {'floor':>5}  {'submitted':>12}  {'dropped':>12}  "
          f"{'sitemap total':>14}  {'x indexed':>9}")
    for f, kept, dropped in floors:
        grand = kept + other
        print(f"    {f:>5}  {kept:>12,}  {dropped:>12,}  {grand:>14,}  "
              f"{grand / max(args.indexed, 1):>8.1f}x")

    print(f"""
  HOW TO READ IT. The last column is how many times your CURRENT INDEX
  ({args.indexed:,}) you would be asking Google to crawl. Google has told
  you what it thinks of a 30x ask: 3.5M discovered and not crawled.

  A sitemap is a request for attention, not a claim of ownership. Pages
  left out still exist, still render and still rank when Google finds them
  by a link -- they just stop competing with your good pages for a crawl
  budget that is already spent.

  ⚠ AND THE FLOOR IS THE OWNER'S CALL. build_sitemap carries an explicit
    instruction to submit every athlete. This script argues with it; it
    does not overrule it. If the number stays at 1, the lever is the other
    one: make the site answer faster and stop YandexBot and Applebot from
    eating the crawl capacity Googlebot is measuring.""")


if __name__ == "__main__":
    main()
