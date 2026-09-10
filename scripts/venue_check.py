#!/usr/bin/env python3
"""
venue_check.py -- for a named venue: what does the board say, and what do
the athletes say?

    scripts/venue_check.py --venue "Foot Locker" --venue NXN
    scripts/venue_check.py --venue "Balboa" --pct 40
    scripts/venue_check.py --venue "Mt. SAC" --venue "Crystal Springs"

Run from the PROJECT ROOT. READ-ONLY: one UNLOGGED scratch table inside a
transaction that is rolled back.

★ WHY (owner: "Foot Locker too low / NXN too high"). Every other tool here
  answers a question about the corpus. This one answers a question about a
  COURSE YOU NAMED, which is how the complaints actually arrive -- and it
  is reusable for the next venue rather than a one-off for these two.

★ WHAT IT COMPARES.

    published    course_difficulties -- what the site shows
    measured     the same venue re-measured from the rows, using the
                 leave-own-venue-out benchmark: an athlete's races
                 ELSEWHERE. The venue cannot move its own yardstick.
    split-half   the venue's races split in two, each half measured
                 independently. Says whether the measurement is stable
                 enough to argue about at all.

  published far from measured means the solver's difficulty for that cell
  disagrees with what the runners did. That can be real -- the solver sees
  the whole graph and this sees one venue -- but a large gap on a venue
  with plenty of races is worth explaining rather than assuming.

⚠ A NATIONAL CHAMPIONSHIP IS THE HARDEST CASE THERE IS, and Foot Locker
  and NXN are both. Two things bite:

    SELECTION. The field is the best runners in the country, so "their
    races elsewhere" are regional and state championships -- also fast,
    also peaked. If anything that biases the venue to look EASY.

    PEAKING. It is the last race of the season and everybody is rested.
    A course where everyone runs their season best reads as easy ground
    when it is actually a fast day. Nothing here corrects for that; the
    form curve in the solve is supposed to, and this is a way to see
    whether it did.

  So read a championship venue's number as "what the raw rows say before
  the season curve", and the gap against `published` as partly the curve
  doing its job. The n_athletes and reliability columns say how much
  evidence is behind either.

! Distances are reported separately. Foot Locker and NXN are both 5k, but
  a venue that races several distances has several difficulties and
  averaging them would invent one nobody ran.
"""

import argparse
import math
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, _ROOT, os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "racecast")):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

_SCRATCH = "vc_rows"

# ! see season_reliability._ISO_DATE for why this is a constant
_ISO_DATE = "r.date::text ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}'"
_SEASON = ("(CASE WHEN substr(r.date::text, 6, 2)::int >= 8 "
           "THEN substr(r.date::text, 1, 4)::int "
           "ELSE substr(r.date::text, 1, 4)::int - 1 END)")

# ★ EVERY ROW FOR EVERY ATHLETE WHO EVER RACED A NAMED VENUE, not just
#   their rows at it. The benchmark IS the other rows, so pulling only the
#   venue's rows would leave nothing to compare against.
_PASS_A = f"""
CREATE UNLOGGED TABLE {_SCRATCH} AS
WITH hits AS (
    SELECT DISTINCT meet_id, div_id, source, course_name
    FROM   meets
    WHERE  course_name IS NOT NULL AND course_name ILIKE ANY(%(pats)s)
), who AS (
    SELECT DISTINCT r.person_id
    FROM   results r
    JOIN   hits h ON h.meet_id = r.meet_id AND h.div_id = r.div_id
                 AND h.source = r.source
    WHERE  r.normalized_time > 0
      AND  abs(mod(hashint8(r.person_id::bigint), 10000)) < %(cut)s
)
SELECT r.person_id,
       {_SEASON}                                        AS season,
       m.course_name                                    AS venue,
       COALESCE(dov.distance, m.distance)::float        AS dist,
       (r.meet_id::text || ':' || r.div_id::text
        || ':' || COALESCE(r.source, ''))               AS race,
       r.date::text                                     AS d,
       ln(r.normalized_time)                            AS lnt,
       (h.meet_id IS NOT NULL)                          AS is_target
FROM   results r
JOIN   who w ON w.person_id = r.person_id
JOIN   meets m ON m.meet_id = r.meet_id AND m.div_id = r.div_id
               AND m.source = r.source
LEFT   JOIN dist_override dov ON dov.meet_id = r.meet_id
                             AND dov.div_id = r.div_id
LEFT   JOIN hits h ON h.meet_id = r.meet_id AND h.div_id = r.div_id
                  AND h.source = r.source
WHERE  r.normalized_time IS NOT NULL AND r.normalized_time > 0
  AND  m.course_name IS NOT NULL
  AND  r.date IS NOT NULL AND {_ISO_DATE}
"""

# leave-own-venue-out, then per (venue, distance cell) and per half
_MEASURE = f"""
WITH per_season AS (
    SELECT person_id, season, count(*) AS n_all, sum(lnt) AS s_all
    FROM   {_SCRATCH} GROUP BY 1, 2
), per_venue AS (
    SELECT person_id, season, venue, count(*) AS n_own, sum(lnt) AS s_own
    FROM   {_SCRATCH} GROUP BY 1, 2, 3
), resid AS (
    SELECT b.venue, b.race, b.person_id,
           round(b.dist / 100.0) * 100                      AS cell,
           b.lnt - (p.s_all - v.s_own) / (p.n_all - v.n_own) AS res
    FROM   {_SCRATCH} b
    JOIN   per_season p ON p.person_id = b.person_id AND p.season = b.season
    JOIN   per_venue  v ON v.person_id = b.person_id AND v.season = b.season
                       AND v.venue = b.venue
    WHERE  b.is_target AND p.n_all > v.n_own
), per_race AS (
    SELECT venue, cell, race, count(*) AS n,
           avg(res) AS race_res, min(person_id) AS anyone
    FROM   resid GROUP BY 1, 2, 3
), halved AS (
    SELECT venue, cell, race, n, race_res,
           (row_number() OVER (PARTITION BY venue, cell ORDER BY race) %% 2)
                                                            AS half
    FROM   per_race
)
SELECT venue, cell::int AS dist,
       count(*)                                             AS races,
       sum(n)                                               AS results,
       round((100 * sum(race_res * n) / sum(n))::numeric, 2) AS measured_pct,
       round((100 * stddev_samp(race_res)
              / sqrt(count(*)))::numeric, 2)                AS se_pct,
       round((100 * sum(race_res * n) FILTER (WHERE half = 0)
              / NULLIF(sum(n) FILTER (WHERE half = 0), 0))::numeric, 2)
                                                            AS half_a,
       round((100 * sum(race_res * n) FILTER (WHERE half = 1)
              / NULLIF(sum(n) FILTER (WHERE half = 1), 0))::numeric, 2)
                                                            AS half_b
FROM   halved
GROUP  BY 1, 2
HAVING sum(n) >= %(min_rows)s
ORDER  BY 1, 2
"""

_PUBLISHED = """
    SELECT substring(course_name FROM 4)                AS venue,
           distance_m::int                              AS dist,
           round((100 * difficulty)::numeric, 2)        AS published_pct,
           n_results, n_athletes
    FROM   course_difficulties
    WHERE  course_name LIKE 'XC:%%'
      AND  substring(course_name FROM 4) ILIKE ANY(%(pats)s)
    ORDER  BY 1, 2
"""


def _table(cols, rows, indent="    "):
    if not rows:
        print(f"{indent}(no rows)")
        return
    body = [[("" if v is None else str(v)) for v in r] for r in rows]
    w = [max(len(c), *(len(b[i]) for b in body)) for i, c in enumerate(cols)]
    print(indent + "  ".join(c.ljust(w[i]) for i, c in enumerate(cols)))
    print(indent + "  ".join("-" * w[i] for i in range(len(cols))))
    for b in body:
        print(indent + "  ".join(b[i].ljust(w[i]) for i in range(len(b))))


def main():
    ap = argparse.ArgumentParser(
        description="For a named venue: what the board says vs what the "
                    "athletes say.")
    ap.add_argument("--venue", action="append", required=True,
                    help="name fragment, case-insensitive; repeatable")
    ap.add_argument("--pct", type=float, default=100.0,
                    help="percent of that venue's ATHLETES to sample "
                         "(default 100 -- these are small fields)")
    ap.add_argument("--min-rows", type=int, default=20)
    ap.add_argument("--work-mem", default="256MB")
    ap.add_argument("--timeout", default="30min")
    args = ap.parse_args()

    pats = [f"%{v}%" for v in args.venue]
    cut = int(round(args.pct * 100))

    from database import getConn
    with getConn() as conn:
        cur = conn.cursor()
        cur.execute(f"SET LOCAL work_mem = '{args.work_mem}'")
        cur.execute(f"SET LOCAL statement_timeout = '{args.timeout}'")
        cur.execute("SET LOCAL max_parallel_workers_per_gather = 2")
        try:
            print("\n" + "=" * 74)
            print("1. WHAT THE BOARD PUBLISHES")
            print("=" * 74)
            cur.execute(_PUBLISHED, {"pats": pats})
            pub = cur.fetchall()
            _table([d[0] for d in cur.description], pub)
            published = {(r[0], r[1]): float(r[2]) for r in pub
                         if r[2] is not None}

            t0 = time.time()
            cur.execute(f"DROP TABLE IF EXISTS {_SCRATCH}")
            cur.execute(_PASS_A, {"pats": pats, "cut": cut})
            cur.execute(f"SELECT count(*) FROM {_SCRATCH}")
            n = cur.fetchone()[0]
            print(f"\n  {n:,} rows for the athletes who raced these venues "
                  f"({time.time() - t0:.0f}s)", flush=True)
            if n == 0:
                print("  no matching venue in `meets` -- check the spelling")
                return 1
            cur.execute(f"CREATE INDEX ON {_SCRATCH} (person_id, season)")

            print("\n" + "=" * 74)
            print("2. WHAT THE ATHLETES SAY (vs their races ELSEWHERE)")
            print("   measured_pct > 0 = they run SLOWER here than elsewhere")
            print("   half_a / half_b  = the venue's races split in two;")
            print("                      far apart means it is not stable")
            print("=" * 74)
            cur.execute(_MEASURE, {"min_rows": args.min_rows})
            cols = [d[0] for d in cur.description]
            meas = cur.fetchall()
            _table(cols, meas)

            print("\n" + "=" * 74)
            print("3. THE VERDICT PER CELL")
            print("=" * 74)
            if not meas:
                print("    nothing measurable -- the athletes here have too "
                      "few races elsewhere")
                return 0
            for row in meas:
                venue, dist, races, results, m, se, ha, hb = row
                m = float(m)
                se = float(se) if se is not None else float("nan")
                key = None
                for (pv, pd), val in published.items():
                    if pv.lower() in venue.lower() or venue.lower() in pv.lower():
                        if pd is None or abs((pd or 0) - dist) <= 150:
                            key = val
                            break
                print(f"\n    {venue}  {dist}m   {races} races, "
                      f"{results:,} results")
                print(f"      measured  {m:+.2f}%  (se {se:.2f})")
                if ha is not None and hb is not None:
                    spread = abs(float(ha) - float(hb))
                    print(f"      halves    {float(ha):+.2f}% / "
                          f"{float(hb):+.2f}%   (apart by {spread:.2f})")
                    if spread > 3.0:
                        print("      ⚠ the two halves disagree by more than "
                              "3 points -- this venue's\n        difficulty "
                              "is not stable enough to argue about.")
                if key is None:
                    print("      published (no course_difficulties row "
                          "matched this cell)")
                    continue
                print(f"      published {key:+.2f}%")
                gap = key - m
                if abs(gap) < max(1.0, 2 * se):
                    print("      => the board agrees with the runners.")
                elif gap > 0:
                    print(f"      => the board rates this course "
                          f"{gap:.2f} points HARDER than the\n         "
                          f"runners did, so times here are credited more "
                          f"than they earned.")
                else:
                    print(f"      => the board rates this course "
                          f"{-gap:.2f} points EASIER than the\n         "
                          f"runners did, so times here are credited less "
                          f"than they earned.")
            print("\n    ⚠ For a national championship (Foot Locker, NXN) "
                  "read the gap as partly\n      the season form curve: the "
                  "field is peaked and rested, so the raw rows\n      make "
                  "the ground look faster than it is. See this file's "
                  "header.")
            cur.execute(f"DROP TABLE IF EXISTS {_SCRATCH}")
        finally:
            conn.rollback()
            cur.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
