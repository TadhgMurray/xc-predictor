#!/usr/bin/env python3
"""
place_unplaced_meets.py -- a cross country race with a course name but no
coordinates takes the coordinates the other races at that course carry.

    set -a; . /etc/xc-predictor.env; set +a; P=/srv/venv/bin/python
    $P scripts/place_unplaced_meets.py                      # DRY RUN: what it would place
    $P scripts/place_unplaced_meets.py --write
    $P scripts/place_unplaced_meets.py --since 2024-08-01 --write

★ WHY (owner, 2026-11-03: the NESCAC race with no course, no weather and no
  race-day term; then this season's count -- every timer and uploaded race,
  417 athletic.net races and 600 tfrrs races with no place). A course is a
  name AND a place (course_canonical keys on both), so a race with no
  coordinates matches no course, gets no weather, and is left out of the
  solve -- its rating comes from the fill step's field anchor, and its page
  says "no fitted difficulty". The landers for timers, uploads and some
  feeds carry the venue's name and nothing else.

★ THE NAME DECIDES ONLY WHEN IT IS ONE PLACE. Among the placed races with
  the same course name (and the same state, when both have one), one
  coordinate pair -- to about 100 m -- must hold at least SHARE_MIN of them
  and at least MIN_RACES races. "High School" or "City Park" are many
  places and stay unplaced.
! Coordinates only: the course name is left as it is. A race filed at the
  wrong course is scripts/set_race_course.py's job.
"""
import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.join(os.path.dirname(_HERE), "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

MIN_RACES = 2
SHARE_MIN = 0.6
ROUND = 3          # decimals: ~100 m, so one park's two start lines are one place

# every placed race by (name, state): its coordinate pairs and their counts
_PLACES = f"""
    WITH placed AS (
        SELECT lower(btrim(course_name)) AS nm, upper(btrim(COALESCE(state, ''))) AS st,
               round(gps_lat::numeric, {ROUND}) AS la, round(gps_long::numeric, {ROUND}) AS lo,
               mode() WITHIN GROUP (ORDER BY ARRAY[gps_lat, gps_long]) AS ll, count(*) AS n
        FROM   meets
        WHERE  course_name IS NOT NULL AND btrim(course_name) <> ''
          AND  gps_lat IS NOT NULL AND gps_long IS NOT NULL
        GROUP  BY 1, 2, 3, 4
        UNION ALL
        SELECT lower(btrim(venue_name)), upper(btrim(COALESCE(state, ''))),
               round(gps_lat::numeric, {ROUND}), round(gps_long::numeric, {ROUND}),
               mode() WITHIN GROUP (ORDER BY ARRAY[gps_lat, gps_long]), count(*)
        FROM   meets_tfrrs
        WHERE  sport = 'XC' AND venue_name IS NOT NULL AND btrim(venue_name) <> ''
          AND  gps_lat IS NOT NULL AND gps_long IS NOT NULL
        GROUP  BY 1, 2, 3, 4
    ), byname AS (
        -- the exact pair most of them carry, so the race joins the course
        -- course_canonical already holds (it keys on the coordinates)
        SELECT DISTINCT ON (nm, st, la, lo) nm, st, la, lo,
               sum(n) OVER (PARTITION BY nm, st, la, lo) AS n, ll[1] AS lat, ll[2] AS lon
        FROM   placed
        ORDER  BY nm, st, la, lo, n DESC
    ), ranked AS (
        SELECT nm, st, lat, lon, n,
               sum(n) OVER (PARTITION BY nm, st) AS total,
               row_number() OVER (PARTITION BY nm, st ORDER BY n DESC) AS rk
        FROM   byname
    )
    SELECT nm, st, lat, lon, n, total FROM ranked
    WHERE  rk = 1 AND n >= {MIN_RACES} AND n >= {SHARE_MIN} * total
"""


def pickPlace(places, name, state):
    """(lat, lon, n) for a course name, from the (name, state) table: the
    race's own state first, else the name alone when only one state holds
    it. Pure."""
    nm = (name or "").strip().lower()
    st = (state or "").strip().upper()
    if not nm:
        return None
    if (nm, st) in places:
        return places[(nm, st)]
    if st:
        return None if any(k[0] == nm and k[1] and k[1] != st for k in places) else places.get((nm, ""))
    hits = [v for k, v in places.items() if k[0] == nm]
    return hits[0] if len(hits) == 1 else None


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", default=None, help="races on or after (default: all)")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--show", type=int, default=25)
    a = ap.parse_args()
    from database import getConn
    with getConn() as conn, conn.cursor() as cur:
        cur.execute("SET LOCAL statement_timeout = '30min'")
        cur.execute(_PLACES)
        places = {(r[0], r[1]): (float(r[2]), float(r[3]), int(r[4])) for r in cur.fetchall()}
        since = "AND meet_date >= %(since)s" if a.since else ""
        cur.execute(f"""SELECT meet_id, div_id, course_name, state, meet_name, meet_date, source FROM meets
                        WHERE gps_lat IS NULL AND course_name IS NOT NULL AND btrim(course_name) <> ''
                        {since}""", {"since": a.since})
        anet = cur.fetchall()
        since_t = "AND date >= %(since)s" if a.since else ""
        cur.execute(f"""SELECT meet_id, venue_name, state, meet_name, date FROM meets_tfrrs
                        WHERE sport = 'XC' AND gps_lat IS NULL AND venue_name IS NOT NULL
                          AND btrim(venue_name) <> '' {since_t}""", {"since": a.since})
        tf = cur.fetchall()
        got_m = [(m, d, c, s, nm, dt, src, pickPlace(places, c, s)) for m, d, c, s, nm, dt, src in anet]
        got_t = [(m, c, s, nm, dt, pickPlace(places, c, s)) for m, c, s, nm, dt in tf]
        hit_m = [g for g in got_m if g[-1]]
        hit_t = [g for g in got_t if g[-1]]
        print(f"[place] meets rows with a course but no place: {len(anet):,}; "
              f"{len(hit_m):,} placed by their course's name")
        print(f"[place] tfrrs XC meets with a venue but no place: {len(tf):,}; "
              f"{len(hit_t):,} placed by their venue's name")
        for m, d, c, s, nm, dt, _src, p in hit_m[:a.show]:
            print(f"   {dt or '':<10} {(nm or '')[:40]:<40} {c[:30]:<30} {s or '':<3} -> {p[0]:.4f},{p[1]:.4f} ({p[2]} races)")
        for m, c, s, nm, dt, p in hit_t[:a.show]:
            print(f"   {dt or '':<10} {(nm or '')[:40]:<40} {c[:30]:<30} {s or '':<3} -> {p[0]:.4f},{p[1]:.4f} ({p[2]} races)  tfrrs")
        if not a.write:
            conn.rollback()
            print("  DRY RUN -- --write places them")
            return 0
        for m, d, c, s, nm, dt, src, p in hit_m:
            cur.execute("UPDATE meets SET gps_lat = %s, gps_long = %s WHERE meet_id = %s AND div_id = %s "
                        "AND source IS NOT DISTINCT FROM %s AND gps_lat IS NULL", (p[0], p[1], m, d, src))
            # priced again on the course and the day's weather it now has
            cur.execute("UPDATE results SET normalized_time = NULL WHERE meet_id = %s AND div_id = %s "
                        "AND source IS NOT DISTINCT FROM %s", (m, d, src))
        for m, c, s, nm, dt, p in hit_t:
            cur.execute("UPDATE meets_tfrrs SET gps_lat = %s, gps_long = %s WHERE meet_id = %s "
                        "AND sport = 'XC' AND gps_lat IS NULL", (p[0], p[1], m))
            cur.execute("UPDATE results SET normalized_time = NULL WHERE source = 'tfrrs' AND meet_id = %s",
                        (m,))
        conn.commit()
        print(f"[place] {len(hit_m) + len(hit_t):,} races placed; the next normalise prices them "
              f"with their course and weather, and the next solve gives them a race-day term")
    return 0


if __name__ == "__main__":
    sys.exit(main())
