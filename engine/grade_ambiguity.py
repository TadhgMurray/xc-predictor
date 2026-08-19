"""
grade_ambiguity.py -- how many class-name grade rows are actually high school?

THE FINDING THIS MEASURES
    normalize_distance records as an owner-confirmed domain fact that "high
    school grades are NUMBERS (9-12), college classes are NAMES (FR/SO/JR/SR),
    there is no overlap". The corpus disagrees:

        Fr  anet  676,738 rows  2,676 schools  from 1945
        So  anet  518,786       2,548

    Wapahani, Sebring, Eden Valley-Watkins -- high schools. And the same athlete
    on the same date appears twice, once as '9' and once as 'FR'.

★ WHY THIS IS A LIVE BUG. poolFor step 1 is getPool(grade, gender), which
  returns IMMEDIATELY when the grade resolves and never consults the school.
  normalizeGrade('Fr') -> 'Fr' -> GRADE_TO_LEVEL['Fr'] -> 'college'. Measured
  across both results tables, 10,973,476 rows carry a class-name grade while
  their SCHOOL is a known high school or middle school.

TWO MODES
    (default)    the aggregate: rows by what the school says, per source.
    --examples   ★ THE ONE THAT MATTERS FOR REVIEW. The schools that actually
                 MOVE, with named athletes.

                 Ranking class-name schools by raw volume is useless here -- it
                 returns Luther, St. Olaf, Wartburg, Grinnell: real colleges
                 correctly using class names, none of which move. What moves is
                 the INTERSECTION -- a class name AND a school that
                 school_levels.pkl calls hs/ms/elem.

Measures only. Changes nothing.
"""

import os
import sys
from collections import defaultdict

# Run from the repo root or from engine/ -- either way the siblings import.
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, _ROOT,
           os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "scripts")):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

_CLASS_LIKE = ("fr", "so", "jr", "sr", "rs",
               "freshman", "sophomore", "junior", "senior", "redshirt",
               "fresh", "soph")


# ------------------------------------------------------------------ #
# CHUNK 1 -- THE AGGREGATE
# ------------------------------------------------------------------ #

def aggregate():
    """
    Class-name grade rows bucketed by what the SCHOOL says.

        hs / ms / elem   school says youth -> the fix CORRECTS the row
        college          school agrees     -> no change
        unresolved       school unknown    -> unchanged, deliberately: sending
                                              these to unknown_pool would trade
                                              a wrong pool for no pool at all
    """
    from database import getConn
    from normalize_distance import levelForSchool

    rows = []
    with getConn() as conn, conn.cursor() as cur:
        for table in ("results", "results_tf"):
            cur.execute(f"""
                SELECT r.source, r.school, count(*)
                FROM {table} r
                WHERE lower(btrim(COALESCE(r.grade, ''))) = ANY(%s)
                GROUP BY 1, 2
            """, (list(_CLASS_LIKE),))
            rows.extend(cur.fetchall())

    tally = defaultdict(lambda: defaultdict(int))
    for source, school, n in rows:
        bucket = (levelForSchool(school) if school else None) or "unresolved"
        tally[bucket][source or "?"] += n

    print("\n[grade] class-name grade rows, by what the SCHOOL says")
    print("    school level     source        rows")
    total = 0
    for bucket in sorted(tally):
        for source, n in sorted(tally[bucket].items(), key=lambda kv: -kv[1]):
            print(f"    {bucket:<15} {source:<10} {n:>11,}")
            total += n
    print(f"    {'TOTAL':<15} {'':<10} {total:>11,}")

    youth = sum(v for b in ("hs", "ms", "elem") for v in tally[b].values())
    unres = sum(tally["unresolved"].values())
    print(f"\n[grade] {youth:,} rows would be CORRECTED to a youth pool")
    print(f"[grade] {unres:,} have an unresolvable school and are left alone")
    print("\n[grade] run with --examples to see WHICH schools move, and who.")


# ------------------------------------------------------------------ #
# CHUNK 2 -- WHO ACTUALLY MOVES
# ------------------------------------------------------------------ #

def examples(top_schools=25, per_school=3):
    """
    The rows whose pool CHANGES: a class-name grade at a school the pickle says
    is youth.

    ★ READ THE NAMES. If they are high schools -- Wapahani, Sebring, Ponte Vedra
      -- the change is right. If any is a college, school_levels.pkl has it
      mislabelled and those rows would move the WRONG way. A community college
      tagged 'hs' is the specific failure to look for: plausible in aggregate,
      wrong in fact.
    """
    from database import getConn
    from normalize_distance import levelForSchool

    with getConn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT school, sum(n) FROM (
                SELECT school, count(*) AS n FROM results
                WHERE lower(btrim(COALESCE(grade,''))) = ANY(%s)
                  AND school IS NOT NULL GROUP BY 1
                UNION ALL
                SELECT school, count(*) FROM results_tf
                WHERE lower(btrim(COALESCE(grade,''))) = ANY(%s)
                  AND school IS NOT NULL GROUP BY 1
            ) q GROUP BY 1
        """, (list(_CLASS_LIKE), list(_CLASS_LIKE)))

        youth = [(s, int(n)) for s, n in cur.fetchall()
                 if levelForSchool(s) in ("hs", "ms", "elem")]
        youth.sort(key=lambda t: -t[1])

        moved = sum(n for _s, n in youth)
        print(f"\n[grade] {len(youth):,} schools move, {moved:,} rows total")
        print(f"[grade] top {top_schools} by volume, with sample athletes\n")

        for school, n in youth[:top_schools]:
            print(f"  {n:>9,} rows   [{levelForSchool(school)}]   {school}")
            cur.execute("""
                SELECT an.name, r.date, r.grade
                FROM results r
                LEFT JOIN athlete_named an ON an.person_id = r.person_id
                WHERE r.school = %s
                  AND lower(btrim(COALESCE(r.grade,''))) = ANY(%s)
                ORDER BY r.date DESC LIMIT %s
            """, (school, list(_CLASS_LIKE), per_school))
            for name, date, grade in cur.fetchall():
                print(f"                {date}  {str(grade):<10} {name or '?'}")
            print()


if __name__ == "__main__":
    if "--examples" in sys.argv:
        examples()
    else:
        aggregate()