"""
diag_extreme_courses.py -- READ ONLY. Audits the highest-difficulty courses.

Writes nothing.

    python scripts/diag_extreme_courses.py
    python scripts/diag_extreme_courses.py --limit 60 --min-results 100

WHY
    Canfield Mountain carries difficulty 0.789 -- among the largest in the
    table -- and its fields STILL rate 40-50 points low across 11 years. Its
    median raw time is ~2840s at 5000-6400m, i.e. 2.4-2.8x a normal HS XC race.
    That is not a hard cross-country course; it is a different event scraped
    into the XC corpus.

    A per-course difficulty can absorb a CONSTANT offset. It cannot absorb an
    event that is not comparable to the corpus it is calibrated against. So the
    extreme tail of course_difficulties is the natural place to look for
    misclassified events, and that is what this lists.

    ★ Difficulty being large is NOT itself evidence of a problem -- some courses
    really are brutal. The tell is the RAW TIME, which is independent of every
    correction the pipeline applies.
"""

import argparse

from psycopg2.extras import RealDictCursor

from database import getConn


# A 5K XC race: elite HS ~900s, slow HS ~1500s, back of a big field ~1800s.
# Past ~2100s at a nominal 5K the event is very likely not standard XC.
_RAW_TIME_SUSPECT = 2100

_SQL = """
WITH course_stats AS (
    SELECT btrim(m.course_name) AS course,
           count(*)                                       AS n_rows,
           count(DISTINCT m.distance)                     AS n_distances,
           round(min(m.distance)::numeric)                AS dist_min,
           round(max(m.distance)::numeric)                AS dist_max,
           percentile_cont(0.5) WITHIN GROUP
               (ORDER BY r.time_seconds)                  AS med_raw,
           percentile_cont(0.5) WITHIN GROUP
               (ORDER BY r.normalized_time)               AS med_norm,
           percentile_cont(0.5) WITHIN GROUP
               (ORDER BY r.speed_rating)                  AS med_rating
    FROM results r
    JOIN meets m ON m.div_id = r.div_id AND m.source = r.source
    WHERE r.normalized_time IS NOT NULL
      AND r.time_seconds > 0
      AND m.course_name IS NOT NULL
    GROUP BY 1
    HAVING count(*) >= %(min_results)s
)
SELECT cd.difficulty,
       cd.n_athletes,
       cs.*
FROM course_difficulties cd
JOIN course_stats cs ON cd.course_name = 'XC:' || cs.course
ORDER BY cd.difficulty DESC
LIMIT %(limit)s
"""


def _fetch(sql, params):
    with getConn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(sql, params)
            return cursor.fetchall()


def report(limit, minResults):
    """
    Highest-difficulty courses, with the evidence needed to judge each.

    ★ JOINS ON 'XC:' || course_name -- the CURRENT key. After the canonical
    re-key that becomes 'XC:<canonical_id>' and this join silently returns
    nothing. If the output is empty, that is why, not an absence of extreme
    courses.

    n_distances is the second tell. One venue reporting several distances for
    the same physical race means meets.distance is unreliable there, so
    normalized_time is wrong by a DIFFERENT amount per race -- and no single
    difficulty can absorb a varying error. Canfield: 5000 and 6400 for raw
    times of 2843 and 2831, i.e. the same effort labelled two ways.
    """
    rows = _fetch(_SQL, {"limit": limit, "min_results": minResults})

    print(f"\n  HIGHEST-DIFFICULTY XC COURSES "
          f"(>= {minResults} results, top {limit})")
    print(f"\n    {'diff':>6} {'rows':>8} {'athl':>7} {'nd':>3} "
          f"{'dist':>12} {'med_raw':>8} {'med_norm':>9} {'rating':>7}  course")

    for row in rows:
        distances = (f"{row['dist_min']:.0f}"
                     if row["n_distances"] == 1
                     else f"{row['dist_min']:.0f}-{row['dist_max']:.0f}")

        flags = []
        if row["med_raw"] and row["med_raw"] > _RAW_TIME_SUSPECT:
            flags.append("SLOW")
        if row["n_distances"] > 2:
            flags.append("MULTI-DIST")
        flag = ("  <-- " + " ".join(flags)) if flags else ""

        print(f"    {row['difficulty']:>6.3f} {row['n_rows']:>8,} "
              f"{row['n_athletes']:>7,} {row['n_distances']:>3} "
              f"{distances:>12} {row['med_raw']:>8.0f} "
              f"{row['med_norm']:>9.0f} {row['med_rating']:>7.1f}  "
              f"{row['course'][:38]}{flag}")

    print(f"\n    SLOW       median raw time > {_RAW_TIME_SUSPECT}s. Raw time is")
    print("               untouched by any correction, so it is the honest")
    print("               signal: this is probably not standard XC.")
    print("    MULTI-DIST 3+ distances at one venue -> meets.distance unreliable")
    print("               -> normalized_time wrong by a varying amount -> no one")
    print("               difficulty can absorb it.")
    print("\n    Neither flag = a genuinely hard course. Those are FINE and the")
    print("    difficulty is doing its job.")


def main():
    parser = argparse.ArgumentParser(
        description="Audit extreme course difficulties. Read only.")
    parser.add_argument("--limit", type=int, default=40)
    parser.add_argument("--min-results", type=int, default=50)
    args = parser.parse_args()

    print("=" * 78)
    print("EXTREME COURSE DIFFICULTY AUDIT -- XC")
    print("=" * 78)

    report(args.limit, args.min_results)

    print("\nNothing was written. This script is read only.\n")


if __name__ == "__main__":
    main()