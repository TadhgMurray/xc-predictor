#!/usr/bin/env python3
"""
conversion_check.py -- does a cross country rating convert to the track time
the same athlete actually runs? READ-ONLY.

    set -a; . /etc/xc-predictor.env; set +a
    /srv/venv/bin/python scripts/conversion_check.py                  # all four pools
    /srv/venv/bin/python scripts/conversion_check.py --pool college_m --since 2015
    /srv/venv/bin/python scripts/conversion_check.py --pool college_m --meet "%Keene%"

★ WHY (owner, 2026-10-03: "xc races I convert to tf just kind of seem
  wrong. Like a 25:30 8k at Keene State is not a 9:28 3200m"; also "maybe tf
  slightly overrated"). The equivalents card turns a cross country result
  into a rating and the rating into a track time: the card is right exactly
  when a runner's cross country and track ratings agree. So the test is the
  runners who did both:

    fall XC season Y   against   the track springs BEFORE (Y) and AFTER (Y+1)

  Each athlete's median rating per (season half, distance) -- a median, so
  a bad day or a race count cannot move it -- and the two springs averaged,
  so a year's improvement cancels instead of reading as a conversion error
  (both legs are printed too; they straddle the answer by the winter and
  summer gains).

  gap = track rating - cross country rating, in rating points and as a
  percent of track TIME (a rating is 100 x pool mean / time, so +1% rating
  is about -1% time):
    gap > 0   they run FASTER on the track than their XC converts to: the
              card's track time is too SLOW (cross country under-credited)
    gap < 0   they run SLOWER on the track: the card's time is too FAST
              (track over-credited against XC -- "tf slightly overrated"
              reads here as the track RATINGS being high, gap > 0)

  Split by cross country distance (does an 8k convert differently from a
  6k? that is the distance curve), by track event (the event offsets), and
  by ability quartile of the XC rating (the gain's tilt). A uniform gap is
  the cross-sport level; a gap that moves with distance is the curve.
"""
import argparse
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
from database import getConn                                   # noqa: E402

XC_BANDS = (3000, 4000, 5000, 6000, 8000, 10000)
TF_BANDS = (800, 1500, 1600, 3000, 3200, 5000, 10000)
POOLS = ("hs_m", "hs_f", "college_m", "college_f")


def _bandSql(col, bands, tol):
    # nearest standard distance within tol (a 2-mile is a 3200, a mile a
    # 1600: under 1%); anything else is left out
    cases = " ".join(f"WHEN abs({col} - {b}) <= {b} * {tol} THEN {b}" for b in bands)
    return f"CASE {cases} END"


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pool", default=None, help="one pool (default: " + ", ".join(POOLS) + ")")
    ap.add_argument("--since", type=int, default=2010, help="first fall season")
    ap.add_argument("--meet", default=None,
                    help="only athletes who ran a fall XC meet whose name matches (SQL LIKE)")
    ap.add_argument("--min", type=int, default=30, help="hide cells with fewer athletes")
    a = ap.parse_args()
    pools = (a.pool,) if a.pool else POOLS
    t0 = time.time()
    with getConn() as conn:
        cur = conn.cursor()
        cur.execute("SET statement_timeout = 0")
        cur.execute("SET work_mem = '1GB'")
        cur.execute("""SELECT column_name FROM information_schema.columns
                       WHERE table_name = 'ranking_results'""")
        have = {r[0] for r in cur.fetchall()}
        date = "race_date" if "race_date" in have else "date"
        indoor = "COALESCE(is_indoor, 0) = 1" if "is_indoor" in have else "false"
        yr = f"left({date}::text, 4)::int"
        mo = f"substr({date}::text, 6, 2)::int"
        cur.execute(f"""
            CREATE TEMP TABLE cc_rows AS
            SELECT person_id, split_part(pool, '|', 1) AS pool,
                   {yr} AS y,
                   CASE WHEN sport = 'XC' AND {mo} >= 8 THEN 'fall'
                        WHEN sport = 'TF' AND {mo} <= 7 AND NOT {indoor} THEN 'spring'
                        WHEN sport = 'TF' AND {mo} <= 7 THEN 'winter' END AS half,
                   CASE WHEN sport = 'XC' THEN {_bandSql('distance', XC_BANDS, 0.04)}
                        ELSE {_bandSql('distance', TF_BANDS, 0.015)} END AS band,
                   speed_rating, meet_id, sport
            FROM   ranking_results
            WHERE  speed_rating IS NOT NULL AND person_id IS NOT NULL
              AND  split_part(pool, '|', 1) = ANY(%(pools)s)
              AND  {date}::text >= %(lo)s AND {date}::text ~ '^[0-9]{{4}}-[0-9]{{2}}'
        """, {"pools": list(pools), "lo": f"{a.since}-08-01"})
        cur.execute("DELETE FROM cc_rows WHERE half IS NULL OR band IS NULL")
        cur.execute("""
            CREATE TEMP TABLE cc_med AS
            SELECT person_id, pool, y, half, band,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY speed_rating) AS r,
                   count(*) AS n
            FROM cc_rows GROUP BY 1, 2, 3, 4, 5;
            CREATE INDEX ON cc_med (person_id, pool, y, half)""")
        meet_filter = ""
        if a.meet:
            meet_filter = """AND x.person_id IN (
                SELECT c.person_id FROM cc_rows c
                JOIN meets m ON m.meet_id = c.meet_id
                WHERE c.sport = 'XC' AND c.half = 'fall' AND m.meet_name ILIKE %(meet)s)"""
        # spring Y is BEFORE fall Y; spring Y+1 is AFTER it
        cur.execute(f"""
            CREATE TEMP TABLE cc_pair AS
            SELECT x.pool, x.y, x.band AS xc_band, t.band AS tf_band, x.person_id,
                   x.r AS xc, b.r AS before, t.r AS after, (b.r + t.r) / 2 AS tf
            FROM   cc_med x
            JOIN   cc_med b ON b.person_id = x.person_id AND b.pool = x.pool
                           AND b.y = x.y AND b.half = 'spring'
            JOIN   cc_med t ON t.person_id = x.person_id AND t.pool = x.pool
                           AND t.y = x.y + 1 AND t.half = 'spring' AND t.band = b.band
            WHERE  x.half = 'fall' {meet_filter}
        """, {"meet": a.meet})
        cur.execute("SELECT count(*), count(DISTINCT person_id) FROM cc_pair")
        n, people = cur.fetchone()
        print(f"[conv] {n:,} (athlete, XC distance, track event) pairs on {people:,} athletes "
              f"who ran fall XC between two track springs, since {a.since}"
              f"{' at ' + a.meet if a.meet else ''}  ({time.time() - t0:.0f}s)")
        print("  gap = track - XC rating (median over athletes); +gap = they run FASTER on the "
              "track than the card says\n")

        def table(by, label):
            cur.execute(f"""
                SELECT pool, {by}, count(*),
                       percentile_cont(0.5) WITHIN GROUP (ORDER BY tf - xc),
                       percentile_cont(0.5) WITHIN GROUP (ORDER BY (tf - xc) / xc * 100),
                       percentile_cont(0.5) WITHIN GROUP (ORDER BY before - xc),
                       percentile_cont(0.5) WITHIN GROUP (ORDER BY after - xc),
                       percentile_cont(0.5) WITHIN GROUP (ORDER BY xc)
                FROM cc_pair GROUP BY 1, 2 HAVING count(*) >= %s ORDER BY 1, 2""", (a.min,))
            rows = cur.fetchall()
            print(f"  by {label}:")
            print(f"    {'pool':<10}{label:>12}{'athletes':>10}{'gap pts':>9}{'gap %':>8}"
                  f"{'spring before':>15}{'spring after':>14}{'XC median':>11}")
            for pool, k, c, g, gp, bf, af, xm in rows:
                print(f"    {pool:<10}{str(k):>12}{c:>10,}{g:>+9.2f}{gp:>+7.2f}%"
                      f"{bf:>+15.2f}{af:>+14.2f}{xm:>11.1f}")
            print()

        table("'all'", "all")
        table("xc_band", "XC distance")
        table("tf_band", "track event")
        table("xc_band || ' -> ' || tf_band", "XC -> track")
        cur.execute("""
            ALTER TABLE cc_pair ADD COLUMN q int;
            UPDATE cc_pair p SET q = s.q FROM (
                SELECT ctid, ntile(4) OVER (PARTITION BY pool ORDER BY xc) AS q FROM cc_pair) s
            WHERE p.ctid = s.ctid""")
        table("q", "XC quartile")
        conn.rollback()
    print(f"[conv] read-only; done in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
