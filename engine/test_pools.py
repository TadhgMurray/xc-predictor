"""
test_pools.py -- verify the elem and pro pool changes before running anything.

    python scripts/test_pools.py               # all checks
    python scripts/test_pools.py --review      # just the pro false-positive scan
    python scripts/test_pools.py --sample 40   # more example rows

Run from the PROJECT ROOT, like panels.py.

★ IT IMPORTS THE REAL FUNCTIONS. Every check runs poolFor / isProSchool from
  normalize_distance, not a SQL reimplementation of them. A reimplementation
  can agree with the code and both be wrong, or disagree and send you chasing a
  difference that only exists in the test -- which is the whole reason poolFor
  is an SSOT in the first place.

WHAT CHANGED, AND WHY EACH CHECK EXISTS

  ELEM   GRADE_TO_LEVEL now maps grades 1-5 to "elem" instead of "ms".
         Check 1 sizes the move; check 2 asks whether 5/6 is the right
         boundary, since a smooth pace curve through it would mean the line is
         arbitrary.

  PRO    isProSchool() runs BEFORE the grade in poolFor, because a pro's grade
         is stale -- Eduardo Herrera reads grade 12 while racing for Mexico.
         Check 3 shows who gets caught and what they used to be pooled as.
         Check 4 is the dangerous direction: schools a FRAGMENT sweeps up that
         are not actually pro.
"""

import sys
import argparse
from collections import Counter

sys.path.insert(0, "scripts")
from database import getConn

sys.path.insert(0, "engine")
from normalize_distance import (poolFor, isProSchool, normalizeGrade,
                                GRADE_TO_LEVEL, _PRO_EXACT, _PRO_FRAGMENTS)

import psycopg2.extras


FETCH_BATCH = 50_000


def _cursor(conn, name):
    """Server-side cursor: rows stay on the Postgres side and arrive in
    batches, so memory stays flat over a full-corpus scan."""
    cur = conn.cursor(name=name, cursor_factory=psycopg2.extras.RealDictCursor)
    cur.itersize = FETCH_BATCH
    return cur


# ------------------------------------------------------------------ #
#  1. THE ELEM / MS SPLIT
# ------------------------------------------------------------------ #

_GRADE_SQL = """
    SELECT r.grade,
           count(*)                                        AS races,
           count(DISTINCT r.person_id)                     AS athletes,
           avg(r.time_seconds / NULLIF(m.distance, 0) * 1609.34) AS sec_per_mile,
           avg(m.distance)                                 AS mean_distance
    FROM results r
    JOIN meets m ON m.div_id = r.div_id AND m.source = r.source
    WHERE m.distance > 0
      AND r.time_seconds BETWEEN 200 AND 3000
    GROUP BY 1
"""


def checkElemSplit(conn):
    """
    How many rows move ms -> elem, and do they look like younger runners?

    Grades are grouped through normalizeGrade + GRADE_TO_LEVEL, so '9th', '08'
    and 'Freshman' land where the engine puts them rather than where a regex
    would.

    sec_per_mile is the sanity column. It is RAW -- untouched by the distance
    spline, the era curve or weather -- so it is the one number here that cannot
    be wrong because of the thing being tested.
    """
    print("\n" + "=" * 68)
    print("1. ELEM / MS SPLIT")
    print("=" * 68)

    by_level = {}
    by_grade = {}

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(_GRADE_SQL)
        for row in cur.fetchall():
            canon = normalizeGrade(row["grade"])
            level = GRADE_TO_LEVEL.get(canon)
            if level not in ("elem", "ms"):
                continue

            races = int(row["races"])
            rec = by_level.setdefault(level, {"races": 0, "athletes": 0,
                                              "pace_w": 0.0, "dist_w": 0.0})
            rec["races"] += races
            rec["athletes"] += int(row["athletes"])
            if row["sec_per_mile"]:
                rec["pace_w"] += float(row["sec_per_mile"]) * races
            if row["mean_distance"]:
                rec["dist_w"] += float(row["mean_distance"]) * races

            g = by_grade.setdefault(canon, {"races": 0, "level": level,
                                            "pace_w": 0.0})
            g["races"] += races
            if row["sec_per_mile"]:
                g["pace_w"] += float(row["sec_per_mile"]) * races

    print(f"\n  {'level':>6} {'races':>12} {'athletes':>11} "
          f"{'sec/mile':>10} {'mean dist':>11}")
    for level in ("elem", "ms"):
        r = by_level.get(level)
        if not r:
            print(f"  {level:>6}  (none)")
            continue
        print(f"  {level:>6} {r['races']:>12,} {r['athletes']:>11,} "
              f"{r['pace_w'] / r['races']:>10.1f} "
              f"{r['dist_w'] / r['races']:>11.0f}")

    print(f"\n  {'grade':>6} {'level':>6} {'races':>12} {'sec/mile':>10}")
    for canon in sorted(by_grade, key=lambda k: (len(k), k)):
        g = by_grade[canon]
        if not g["races"]:
            continue
        print(f"  {canon:>6} {g['level']:>6} {g['races']:>12,} "
              f"{g['pace_w'] / g['races']:>10.1f}")

    print("\n  READ: sec/mile should STEP at the 5/6 boundary. A smooth drift")
    print("  through it means the split is arbitrary and 5 could sit with 6.")


# ------------------------------------------------------------------ #
#  2. THE PRO CATCH
# ------------------------------------------------------------------ #

_SCHOOL_SQL = """
    SELECT school,
           count(*)                    AS races,
           count(DISTINCT person_id)   AS athletes,
           avg(speed_rating)           AS mean_rating,
           mode() WITHIN GROUP (ORDER BY grade)  AS modal_grade,
           mode() WITHIN GROUP (ORDER BY source) AS modal_source
    FROM results
    WHERE school IS NOT NULL AND btrim(school) <> ''
    GROUP BY 1
    HAVING count(*) >= 5
"""


def _loadSchools(conn):
    """Every school with 5+ races, once. Both checks below read this."""
    with _cursor(conn, "pool_schools") as cur:
        cur.execute(_SCHOOL_SQL)
        return list(cur)


def checkProCatch(conn, schools, sample):
    """
    Who does isProSchool catch, and what were they pooled as before?

    ★ THE COLUMN THAT MATTERS IS old_pool. Every row here was previously routed
      by its GRADE, so anything showing 'hs' is a professional who was dragging
      the high school pool mean and corrupting the difficulty of every course
      they raced. That population is the entire reason step 0 overrides grade.
    """
    print("\n" + "=" * 68)
    print("2. PRO CATCH")
    print("=" * 68)

    caught = [s for s in schools if isProSchool(s["school"])]
    old_pools = Counter()
    races = athletes = 0

    for s in caught:
        races += int(s["races"])
        athletes += int(s["athletes"])
        # What poolFor WOULD have returned before, i.e. skipping step 0.
        prior = poolFor(s["modal_grade"], "M", s["modal_source"], school=None)
        old_pools[prior or "unknown"] += int(s["races"])

    print(f"\n  {len(caught):,} school strings caught, "
          f"{races:,} races, {athletes:,} athletes")
    print(f"\n  {'was pooled as':>22} {'races':>12}")
    for pool, n in old_pools.most_common():
        flag = "   <-- was contaminating this pool" if pool.startswith(
            ("hs_", "ms_", "elem_")) else ""
        print(f"  {pool:>22} {n:>12,}{flag}")

    caught.sort(key=lambda s: -int(s["races"]))
    print(f"\n  BIGGEST CAUGHT (top {sample})")
    print(f"  {'races':>10} {'grade':>7}  school")
    for s in caught[:sample]:
        print(f"  {int(s['races']):>10,} {str(s['modal_grade'] or ''):>7}  "
              f"{s['school']}")


def checkFalsePositives(conn, schools, sample):
    """
    Schools a FRAGMENT catches that look like real schools.

    ⚠ THIS IS THE DANGEROUS DIRECTION. A brand that slips through costs one
      mis-pooled athlete. A fragment that sweeps up a real school pools its
      whole roster as professionals -- into a tiny pool with a wildly different
      mean -- and nothing downstream will flag it.

    The heuristic is deliberately crude: a caught school containing a word like
    "high", "school", "academy" or "college" is almost certainly not a pro team.
    It will miss cases; it is a prompt to look, not a verdict.
    """
    print("\n" + "=" * 68)
    print("3. FRAGMENT FALSE-POSITIVE SCAN")
    print("=" * 68)

    school_words = ("high school", " hs", "h.s.", "academy", "college",
                    "university", "middle school", " ms ", "prep",
                    "elementary", "district", "isd", "catholic", "christian")

    suspects = []
    for s in schools:
        name = str(s["school"]).strip().lower()
        if name in _PRO_EXACT:
            continue                        # exact matches are deliberate
        if not isProSchool(s["school"]):
            continue
        if any(w in name for w in school_words):
            hit = next((f for f in _PRO_FRAGMENTS if f in name), "?")
            suspects.append((int(s["races"]), s["school"], hit))

    if not suspects:
        print("\n  None. No caught school contains a school-like word.")
        return

    suspects.sort(reverse=True)
    print(f"\n  {len(suspects)} suspect(s) -- caught, but named like a school:")
    print(f"\n  {'races':>10}  {'fragment':>22}  school")
    for races, name, frag in suspects[:sample]:
        print(f"  {races:>10,}  {frag:>22}  {name}")
    print("\n  Each of these pools a whole roster as professional. Either")
    print("  narrow the fragment or move the brand to _PRO_EXACT.")


def checkMisses(conn, schools, sample):
    """
    Schools NOT caught whose names contain a brand word.

    The complement of the scan above: brands that slip through because the
    string is spelled in a way no fragment covers. "Saucony Freedom Track Club"
    was exactly this before fragments existed.
    """
    print("\n" + "=" * 68)
    print("4. LIKELY MISSES")
    print("=" * 68)

    brandish = ("track club", "athletics club", "running club", "elite",
                "distance project", "racing team", "tc", "athletics")

    misses = []
    for s in schools:
        if isProSchool(s["school"]):
            continue
        name = str(s["school"]).strip().lower()
        if any(b in name for b in brandish):
            misses.append((int(s["races"]), s["school"],
                           str(s["modal_grade"] or "")))

    misses.sort(reverse=True)
    print(f"\n  {len(misses)} uncaught school(s) with a club-like name.")
    print("  Most will be youth clubs, which are NOT pro. Scan for real teams.")
    print(f"\n  {'races':>10} {'grade':>7}  school")
    for races, name, grade in misses[:sample]:
        print(f"  {races:>10,} {grade:>7}  {name}")


def main():
    parser = argparse.ArgumentParser(
        description="Verify the elem and pro pool changes. Read only.")
    parser.add_argument("--review", action="store_true",
                        help="only the pro checks (2-4)")
    parser.add_argument("--sample", type=int, default=25,
                        help="example rows per section (default 25)")
    args = parser.parse_args()

    print("=" * 68)
    print("POOL CHANGE VERIFICATION")
    print(f"  {len(_PRO_EXACT)} exact pro strings, "
          f"{len(_PRO_FRAGMENTS)} fragments")
    print("=" * 68)

    with getConn() as conn:
        if not args.review:
            checkElemSplit(conn)

        print("\n  loading schools...")
        schools = _loadSchools(conn)
        print(f"  {len(schools):,} school strings with 5+ races")

        checkProCatch(conn, schools, args.sample)
        checkFalsePositives(conn, schools, args.sample)
        checkMisses(conn, schools, args.sample)

    print("\nNothing was written. This script is read only.\n")


if __name__ == "__main__":
    main()