#!/usr/bin/env python3
"""
why_race_unrated.py -- why a cross country race page shows no ratings. READ ONLY.

    set -a; . /etc/xc-predictor.env; set +a
    /srv/venv/bin/python scripts/why_race_unrated.py https://racecast.co/race/xc/270077/1075582
    /srv/venv/bin/python scripts/why_race_unrated.py 270077 1075582

(owner, 2026-10-13: the West Windsor 8K and the MIT Alumni race -- one or
four rated runners of hundreds.) The checks done by hand for West Windsor,
in one place, per race:

  1. the rows: how many, how many normalized and rated, scrape dates
  2. condemned (impossible_race) and duplicate flags (result_twin)
  3. every copy of the race in either feed (canon_meet_id), and whether
     any copy is on the distance-drop list -- the backfill's dropped-twin
     rule carries a drop from one feed's copy to the other's
  4. the season pool of the unrated runners
and a one-line verdict for the first cause found.
"""
import os
import re
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in ("scripts", "engine", "backfill"):
    sys.path.insert(0, os.path.join(_ROOT, _p))


def parseArgs(argv):
    if len(argv) == 1:
        m = re.search(r"/race/xc/(\d+)/(\d+)", argv[0])
        if not m:
            sys.exit("give a /race/xc/<meet>/<div> URL, or a meet id and a div id")
        return int(m.group(1)), int(m.group(2))
    if len(argv) == 2:
        return int(argv[0]), int(argv[1])
    sys.exit(__doc__)


def main():
    meet, div = parseArgs(sys.argv[1:])
    from database import getConn
    try:
        import backfill_normalize as b
        drops = b._DISTANCE_DROP_BY_SPORT.get("XC") or set()
    except Exception as e:                                # noqa: BLE001
        print(f"(drop list not readable: {e})")
        drops = set()
    with getConn() as c, c.cursor() as cur:
        cur.execute("""
            SELECT count(*), count(normalized_time), count(speed_rating),
                   min(scraped_at)::date, max(scraped_at)::date,
                   min(date), string_agg(DISTINCT source, ','),
                   array_agg(DISTINCT canon_meet_id) FILTER (WHERE canon_meet_id IS NOT NULL)
            FROM   results WHERE meet_id = %s AND div_id = %s""", (meet, div))
        n, nn, nr, s0, s1, day, srcs, canons = cur.fetchone()
        if not n:
            sys.exit(f"no rows for meet {meet} div {div}")
        print(f"race {meet}/{div} ({srcs}, {day}): {n} rows, {nn} normalized, "
              f"{nr} rated; scraped {s0}..{s1}")

        cur.execute("SELECT count(*) FROM impossible_race WHERE meet_id = %s AND div_id = %s",
                    (meet, div))
        condemned = cur.fetchone()[0]
        cur.execute("""SELECT t.reason, count(*) FROM result_twin t
                       JOIN results r ON t.sport = 'XC' AND t.result_id = r.result_id
                       WHERE r.meet_id = %s AND r.div_id = %s GROUP BY 1""", (meet, div))
        twins = cur.fetchall()
        print(f"  condemned: {'yes' if condemned else 'no'}; duplicate flags: "
              f"{', '.join(f'{r} {k}' for r, k in twins) or 'none'}")

        dropped_copy = []
        own_drop = (meet, div) in drops
        if canons:
            cur.execute("""SELECT source, meet_id, div_id, count(*), count(normalized_time)
                           FROM results WHERE canon_meet_id = ANY(%s)
                           GROUP BY 1, 2, 3 ORDER BY 1, 2, 3""", (list(canons),))
            print("  every copy of the meet (canon_meet_id):")
            for src, m, d, k, kn in cur.fetchall():
                on = (m, d) in drops
                if on and (m, d) != (meet, div):
                    dropped_copy.append((src, m, d))
                print(f"    {src:6} {m}/{d}: {k} rows, {kn} normalized"
                      f"{'  ON THE DROP LIST' if on else ''}")
        else:
            print("  no other-feed copy linked (canon_meet_id empty)")

        cur.execute("""
            SELECT r.normalized_time IS NOT NULL, s.pool, count(*)
            FROM   results r
            LEFT   JOIN athlete_season s ON s.person_id = r.person_id AND s.sport = 'XC'
                   AND s.year = CASE WHEN substr(r.date, 6, 2) >= '08'
                                     THEN substr(r.date, 1, 4)::int
                                     ELSE substr(r.date, 1, 4)::int - 1 END
            WHERE  r.meet_id = %s AND r.div_id = %s
            GROUP  BY 1, 2 ORDER BY 1, 3 DESC""", (meet, div))
        print("  season pool, normalized vs not:")
        for normed, pool, k in cur.fetchall():
            print(f"    {'normalized' if normed else 'not       '}  {pool or '(none)':<16} {k}")
        cur.execute("""SELECT count(*) FROM results
                       WHERE meet_id = %s AND div_id = %s AND person_id IS NULL""",
                    (meet, div))
        no_person = cur.fetchone()[0]

    print("\n  verdict:", end=" ")
    if condemned:
        print("condemned as an impossible race (06c) -- the stored distance does not fit the times.")
    elif own_drop:
        print("this race is on the distance-drop list itself.")
    elif dropped_copy:
        src, m, d = dropped_copy[0]
        print(f"its {src} copy {m}/{d} is on the distance-drop list, and the backfill's "
              f"dropped-twin rule carries that drop to this copy. Lift the drop "
              f"(scripts/amnesty_division_drops.py, or a pardon in engine/corrections.py).")
    elif twins and sum(k for _r, k in twins) >= n - nn:
        print("the unrated rows are flagged as duplicates of another copy -- that copy carries the ratings.")
    elif no_person >= n - nn:
        print(f"{no_person} rows have no person yet -- they are rated once linked (04a).")
    elif s1 and nn == 0:
        print("nothing normalized: if it was scraped after the last pipeline run, the next run rates it.")
    else:
        print("no single cause found -- paste this output.")


if __name__ == "__main__":
    main()
