"""explain_rating.py -- take one result and print every term behind its rating.

    rating = 100 * pool_mean * (1 + difficulty) / normalized_time

Four terms, and a wrong rating is always exactly one of them. This prints each
one from the source the ENGINE used, next to the value the site displays, and
does the arithmetic both ways so the disagreement names itself.

★ IT INVERTS AS WELL AS EVALUATES. pool_mean is never stored -- it is computed
  in memory during a solve -- so the only way to see the one that was actually
  applied is to recover it from a rated row:

      pool_mean * (1 + difficulty) = speed_rating * normalized_time / 100

  Comparing that against pool_mean measured from pair_athlete_season, and
  against the difficulty in course_difficulties, isolates which term moved.

Usage:
    python tools/explain_rating.py --meet 263430 --div 1045252
    python tools/explain_rating.py --person 29578044
"""
import sys

sys.path.insert(0, "scripts"); sys.path.insert(0, "engine")


def parseArgs(argv):
    out = {"meet": None, "div": None, "person": None, "limit": 12}
    for i, a in enumerate(argv):
        if a in ("--meet", "--div", "--person", "--limit") and i + 1 < len(argv):
            out[a[2:]] = int(argv[i + 1])
    if out["meet"] is None and out["person"] is None:
        sys.exit("need --meet <id> [--div <id>]  or  --person <id>")
    return out


# The rated rows themselves, with the pool the ENGINE settled on.
_ROWS = """
    SELECT r.result_id, r.person_id, r.date, r.meet_id, r.div_id,
           r.grade, r.school, r.source,
           r.time_seconds, r.normalized_time, r.speed_rating,
           pas.pool, pas.ability, pas.rating_seasonal, pas.races,
           m.course_id, m.distance AS stored_distance,
           gf.grade AS fixed_grade, gf.level AS fixed_level, gf.method
    FROM   results r
    LEFT   JOIN pair_athlete_season pas
           ON pas.person_id::bigint = r.person_id
          AND pas.season = (CASE WHEN substring(r.date, 6, 2) >= '08'
                                 THEN substring(r.date, 1, 4)::int
                                 ELSE substring(r.date, 1, 4)::int - 1 END)
    LEFT   JOIN meets m ON m.meet_id = r.meet_id AND m.div_id = r.div_id
    LEFT   JOIN grade_fix gf
           ON gf.person_id = r.person_id
          AND gf.season = (CASE WHEN substring(r.date, 6, 2) >= '08'
                                THEN substring(r.date, 1, 4)::int
                                ELSE substring(r.date, 1, 4)::int - 1 END)
    WHERE  {where}
      AND  r.speed_rating IS NOT NULL
    ORDER  BY r.time_seconds
    LIMIT  {limit}
"""

# ! pool_mean IS MEASURED, NOT READ. It is a solve-time quantity that is never
#   written down, so the honest source is the pool's own athlete-seasons:
#   rating_seasonal = 100 * pool_mean / ability, so pool_mean = ability * r/100.
_POOL_MEAN = """
    SELECT round(avg(ability * rating_seasonal / 100)::numeric, 1),
           count(*)
    FROM   pair_athlete_season
    WHERE  pool = %(pool)s AND season = %(season)s
      AND  ability > 0 AND rating_seasonal > 0
"""

# Every difficulty row for this course -- all distances, because a venue that
# hosts a 1600 and a 5000 has one row for each and picking the wrong one is
# exactly the kind of mistake this script exists to catch.
_DIFFS = """
    SELECT course_name, difficulty, n_results, distance_m
    FROM   course_difficulties
    WHERE  course_name = %(key)s
    ORDER  BY distance_m
"""

_COURSE_NAME = """
    SELECT DISTINCT 'XC:' || course_name
    FROM   meets WHERE course_id = %(cid)s AND course_name IS NOT NULL
"""


def poolMean(cur, pool, season, cache={}):
    if pool is None:
        return None, 0
    k = (pool, season)
    if k not in cache:
        cur.execute(_POOL_MEAN, {"pool": pool, "season": season})
        cache[k] = cur.fetchone() or (None, 0)
    return cache[k]


def difficulties(cur, course_id, cache={}):
    """All course_difficulties rows for this course, by distance."""
    if course_id is None:
        return []
    if course_id not in cache:
        cur.execute(_COURSE_NAME, {"cid": course_id})
        row = cur.fetchone()
        if row is None:
            cache[course_id] = []
        else:
            cur.execute(_DIFFS, {"key": row[0]})
            cache[course_id] = cur.fetchall()
    return cache[course_id]


def explain(cur, r):
    """Print the four terms for one row, and what they imply."""
    (rid, pid, date, meet, div, grade, school, src, raw, norm, rating,
     pool, ability, rat_season, races, cid, stored, fg, fl, meth) = r

    season = int(str(date)[:4])
    if str(date)[5:7] >= "08":
        acad = season
    else:
        acad = season - 1
    mean, n_pool = poolMean(cur, pool, acad)

    print("=" * 74)
    print(f"  result {rid}   person {pid}   {date}   meet {meet}/{div}")
    print(f"  {school}   grade {grade!r} -> verdict {fg!r}/{fl!r} ({meth})")
    print("-" * 74)
    print(f"  raw time            {raw:>12.2f} s")
    print(f"  normalized_time     {norm:>12.2f} s   "
          f"(factor {norm / raw:.4f})")
    print(f"  speed_rating        {rating:>12.2f}")
    print(f"  pool                {str(pool):>12}   "
          f"ability {ability:.1f}, season rating {rat_season:.1f}, "
          f"{races} races")

    # ★ THE INVERSION. Everything above is stored; pool_mean and difficulty
    #   are not, so recover the product the engine actually applied.
    applied = rating * norm / 100.0
    print("-" * 74)
    print(f"  ENGINE APPLIED      pool_mean x (1+d) = {applied:>10.1f}")
    if mean:
        print(f"  pool_mean MEASURED  {mean:>10.1f}   "
              f"(from {n_pool:,} athlete-seasons in {pool}, {acad})")
        implied_d = applied / float(mean) - 1.0
        print(f"  => IMPLIED d        {implied_d:>+10.4f}")
    else:
        implied_d = None
        print("  pool_mean MEASURED       n/a   (no athlete-seasons in pool)")

    rows = difficulties(cur, cid)
    if rows:
        print("-" * 74)
        print(f"  course_difficulties for course {cid}:")
        for key, d, nres, dist in rows:
            star = ""
            if stored is not None and dist is not None \
                    and abs(float(dist) - float(stored)) < 1:
                star = "  <- this race's distance"
            print(f"      {str(dist):>7} m   d = {float(d):+.4f}   "
                  f"n = {nres:,}{star}")
        # ⚠ THE COMPARISON THAT MATTERS. If the implied d matches none of
        #   these, the engine did not get its difficulty from this table --
        #   or matched a different course.
        if implied_d is not None:
            near = min(rows, key=lambda x: abs(float(x[1]) - implied_d))
            print(f"  closest row to the implied d: {float(near[1]):+.4f} "
                  f"at {near[3]} m")
            if abs(float(near[1]) - implied_d) > 0.02:
                print("  ⚠ NO ROW MATCHES. The engine's difficulty is not in "
                      "this course's table.")
    else:
        print(f"  no course_difficulties rows for course {cid}")

    # And the arithmetic forwards, from the displayed terms.
    if rows and mean and stored is not None:
        same = [x for x in rows if x[3] is not None
                and abs(float(x[3]) - float(stored)) < 1]
        if same:
            d = float(same[0][1])
            expect = 100.0 * float(mean) * (1 + d) / norm
            print("-" * 74)
            print(f"  FORWARD from the displayed terms:")
            print(f"      100 x {float(mean):.1f} x (1 {d:+.4f}) / {norm:.2f}"
                  f"  =  {expect:.2f}")
            print(f"      actual speed_rating                    "
                  f"=  {rating:.2f}")
            print(f"      ratio                                  "
                  f"=  {rating / expect:.4f}")
    print()


def main():
    a = parseArgs(sys.argv[1:])
    from database import getConn

    if a["person"] is not None:
        where = f"r.person_id = {a['person']}"
    elif a["div"] is not None:
        where = f"r.meet_id = {a['meet']} AND r.div_id = {a['div']}"
    else:
        where = f"r.meet_id = {a['meet']}"

    with getConn() as conn, conn.cursor() as cur:
        cur.execute(_ROWS.format(where=where, limit=a["limit"]))
        rows = cur.fetchall()
        if not rows:
            print("no rated rows matched")
            return
        for r in rows:
            explain(cur, r)


if __name__ == "__main__":
    main()