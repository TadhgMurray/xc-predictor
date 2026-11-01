#!/usr/bin/env python3
"""
fix_stragglers.py -- re-normalise the races where a few rows sit at a
different distance from the rest of their own group, and clear those rows'
ratings so the night's fill_ratings prices them again. Nightly step 05b.

    python scripts/fix_stragglers.py                 # DRY RUN: which races
    python scripts/fix_stragglers.py --apply         # this season and last
    python scripts/fix_stragglers.py --apply --since 2010

★ WHY (owner, 2026-11-01: Sagebrush 2021). A combined race's women get their
  distance from a distance_pin part row ('g:f'), applied by the pool a row
  had WHEN IT WAS NORMALISED. Four women whose gender was not known then kept
  the men's 7000 and read 136-142 (a 14:24 5K) beside teammates at 104-118;
  the sweep found the same shape all over (diag_div_stragglers). The nightly
  only normalises NEW rows, so a gender fixed later never reached them.

  1. find them: rows whose normalized_time / time_seconds is more than 4%
     off the median of their (race, pool) -- the same test as
     diag_div_stragglers, one SQL pass
  2. re-normalise those races on today's genders, pools and pins
     (backfill_normalize --meets-file, update mode)
  3. every row whose normalized_time moved loses its speed_rating;
     fill_ratings (step 09b, right after this) prices it again
  4. say what is still a straggler -- those are not stale, they are a
     different problem (a division that really mixes distances), for
     diag_div_stragglers and the distance tools
"""
import argparse
import os
import subprocess
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, os.path.join(_ROOT, "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

GAP = 0.04

_FIND = """
    WITH f AS (
        SELECT r.source, r.meet_id, r.div_id, split_part(r.rating_pool, '|', 1) AS pool,
               r.normalized_time / r.time_seconds AS fac
        FROM   results r
        WHERE  r.time_seconds > 0 AND r.normalized_time > 0
          AND  COALESCE(r.rating_pool, '') <> '' AND r.date >= %(since)s
    ), m AS (
        SELECT source, meet_id, div_id, pool,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY fac) AS med
        FROM   f GROUP BY 1, 2, 3, 4 HAVING count(*) >= 3
    )
    SELECT f.source, f.meet_id, count(*)
    FROM   f JOIN m USING (source, meet_id, div_id, pool)
    WHERE  m.med > 0 AND abs(f.fac / m.med - 1) > %(gap)s
    GROUP  BY 1, 2
"""


def find(cur, since):
    cur.execute(_FIND, {"since": since, "gap": GAP})
    return [(src, int(mid), int(n)) for src, mid, n in cur.fetchall()]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--since", help="first date (default: last season's first day)")
    a = ap.parse_args()

    from database import getConn
    sys.path.insert(0, os.path.join(_ROOT, "backfill"))
    since = a.since
    if not since:
        from datetime import date
        from season_year import academicYear, seasonStart
        since = seasonStart(academicYear(date.today()) - 1).isoformat()
    elif len(since) == 4:
        since = f"{since}-01-01"

    with getConn() as conn, conn.cursor() as cur:
        cur.execute("SET LOCAL statement_timeout = '60min'")
        races = find(cur, since)
        n_rows = sum(n for _s, _m, n in races)
        print(f"[stragglers] {n_rows:,} rows off their race-and-pool median by more than "
              f"{GAP:.0%} in {len(races):,} races since {since}")
        if not races or not a.apply:
            if races and not a.apply:
                print("  DRY RUN -- --apply re-normalises them")
            conn.rollback()
            return 0
        # 1. what each row is now
        cur.execute("CREATE TEMP TABLE strag_meet (src text, mid bigint)")
        from psycopg2.extras import execute_values
        execute_values(cur, "INSERT INTO strag_meet VALUES %s", [(s, m) for s, m, _n in races])
        cur.execute("""CREATE TEMP TABLE strag_snap AS
                       SELECT r.result_id, r.source, r.normalized_time
                       FROM results r JOIN strag_meet s
                         ON s.src = r.source AND s.mid = r.meet_id""")
        conn.commit()
        # 2. re-normalise those races
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as fh:
            fh.write("\n".join(f"{s}:{m}" for s, m, _n in races))
            path = fh.name
        rc = subprocess.call([sys.executable, "-u", os.path.join(_ROOT, "backfill", "backfill_normalize.py"),
                              "--sport", "XC", "--apply", "--meets-file", path])
        os.unlink(path)
        if rc != 0:
            print(f"[stragglers] backfill_normalize exited {rc}; ratings left as they were")
            return rc
        # 3. a row whose distance moved is priced again by fill_ratings
        cur.execute("""UPDATE results r SET speed_rating = NULL
                       FROM strag_snap s
                       WHERE r.result_id = s.result_id AND r.source = s.source
                         AND r.normalized_time IS DISTINCT FROM s.normalized_time""")
        moved = cur.rowcount
        conn.commit()
        print(f"[stragglers] {moved:,} rows re-normalised to a new distance; their ratings "
              f"are cleared for fill_ratings")
        # 4. what is left is not stale
        left = find(cur, since)
        n_left = sum(n for _s, _m, n in left)
        print(f"[stragglers] still off after re-normalising: {n_left:,} rows in {len(left):,} races "
              f"(a different problem: scripts/diag_div_stragglers.py --meet SRC:ID)")
        for src, mid, n in sorted(left, key=lambda x: -x[2])[:15]:
            print(f"   {src}:{mid}  {n} rows")
        conn.commit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
