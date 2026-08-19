"""
diag_difficulty_vs_distance.py -- READ ONLY. Is difficulty tracking terrain,
or is it absorbing distance-normalization error?

Writes nothing.

    python scripts/diag_difficulty_vs_distance.py
    python scripts/diag_difficulty_vs_distance.py --min-results 100

THE QUESTION
    diag_extreme_courses showed that ~25 of the 40 highest-difficulty XC
    courses race UNDER 3000m, while genuine 5K courses are the minority.

    Difficulty is supposed to mean "this terrain is harder than average". It
    must therefore be UNCORRELATED with race distance -- a hilly 2-miler and a
    hilly 5K are both hilly, and normalized_time has already removed distance
    by construction.

    If mean difficulty rises as distance falls, distance has NOT been fully
    removed, and course difficulty is silently absorbing the leftover. That is
    the allocation-map failure: an L4 (normalization) error eaten by an L5
    (difficulty) parameter, where it is invisible and uncorrectable.

WHY IT MATTERS BEYOND TIDINESS
    Backlog #3 proposes re-keying difficulty to (venue, distance). If this
    correlation is real, that change would fit a per-distance PARAMETER to a
    per-distance BUG -- locking the error in and making it permanent rather
    than exposing it.
"""

import argparse

from psycopg2.extras import RealDictCursor

from database import getConn


# Buckets chosen around the XC distances that actually occur: 1 mile (1609),
# 1.5 mile (2414), 3k, 4k, 5k, 8k. Boundaries sit BETWEEN the common values so
# a single real race distance never straddles two buckets.
_DISTANCE_BUCKETS = [
    (0, 1800, "<1800  (~1 mile)"),
    (1800, 2600, "1800-2600 (~1.5mi)"),
    (2600, 3200, "2600-3200 (~3k)"),
    (3200, 4200, "3200-4200 (~4k)"),
    (4200, 5200, "4200-5200 (~5k)"),
    (5200, 99999, ">5200  (6k-8k)"),
]

_SQL = """
WITH course_modal AS (
    -- One distance per course: the MODAL one, not the mean. A venue hosting a
    -- 3k and an 8k has a meaningless mean, and DISTINCT ON + ORDER BY count
    -- DESC picks the distance that most of its results were actually run at.
    SELECT DISTINCT ON (course)
           course, distance, n_at_distance, n_total
    FROM (
        SELECT btrim(m.course_name)              AS course,
               m.distance                        AS distance,
               count(*)                          AS n_at_distance,
               sum(count(*)) OVER (PARTITION BY btrim(m.course_name))
                                                 AS n_total
        FROM results r
        JOIN meets m ON m.div_id = r.div_id AND m.source = r.source
        WHERE r.normalized_time IS NOT NULL
          AND m.course_name IS NOT NULL
          AND m.distance IS NOT NULL
          AND m.distance > 0
        GROUP BY 1, 2
    ) per_distance
    ORDER BY course, n_at_distance DESC
)
SELECT cm.course,
       cm.distance,
       cm.n_total,
       round((cm.n_at_distance::numeric / cm.n_total), 3) AS modal_share,
       cd.difficulty,
       cd.n_athletes
FROM course_modal cm
JOIN course_difficulties cd ON cd.course_name = 'XC:' || cm.course
WHERE cm.n_total >= %(min_results)s
"""


def _fetch(sql, params):
    with getConn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(sql, params)
            return cursor.fetchall()


def _bucketOf(distance):
    """First matching bucket label, or None if beyond every range."""
    for low, high, label in _DISTANCE_BUCKETS:
        if low <= distance < high:
            return label
    return None


def _summarize(values):
    """
    n, mean, median for a list of floats. No numpy -- this is a few thousand
    values and importing it here would be the only dependency in the file.
    """
    if not values:
        return (0, 0.0, 0.0)

    ordered = sorted(values)
    n = len(ordered)
    mean = sum(ordered) / n
    median = (ordered[n // 2] if n % 2
              else (ordered[n // 2 - 1] + ordered[n // 2]) / 2)
    return (n, mean, median)


def report(minResults):
    """
    Mean course difficulty by modal race distance.

    ★ THE READ. Difficulty should be FLAT across buckets. A monotone rise as
    distance falls means the distance correction is incomplete and difficulty
    is absorbing the remainder.

    modal_share is printed as a data-quality check, NOT as part of the finding:
    a low share means the venue hosts many distances, so its "modal distance"
    is a weak summary and its row here is less trustworthy.
    """
    rows = _fetch(_SQL, {"min_results": minResults})

    buckets = {label: [] for _, _, label in _DISTANCE_BUCKETS}
    shares = {label: [] for _, _, label in _DISTANCE_BUCKETS}

    for row in rows:
        label = _bucketOf(float(row["distance"]))
        if label is None:
            continue
        buckets[label].append(float(row["difficulty"]))
        shares[label].append(float(row["modal_share"]))

    print(f"\n  COURSE DIFFICULTY BY MODAL RACE DISTANCE "
          f"(courses with >= {minResults} results)")
    print(f"\n    {'distance bucket':>22} {'courses':>8} {'mean diff':>11} "
          f"{'median diff':>12} {'modal share':>12}")

    for _, _, label in _DISTANCE_BUCKETS:
        n, mean, median = _summarize(buckets[label])
        if n == 0:
            continue
        _, shareMean, _ = _summarize(shares[label])
        print(f"    {label:>22} {n:>8,} {mean:>11.4f} {median:>12.4f} "
              f"{shareMean:>12.2f}")

    print("\n    READ: FLAT across buckets -> distance is fully removed upstream")
    print("    and difficulty means terrain. Item #3 can proceed as designed.")
    print("\n    RISING as distance falls -> normalized_time is systematically")
    print("    too slow for short races, and difficulty is absorbing it. Fix the")
    print("    distance layer FIRST; a per-distance difficulty key would fit a")
    print("    parameter to the bug and make it permanent.")


def main():
    parser = argparse.ArgumentParser(
        description="Is difficulty correlated with race distance? Read only.")
    parser.add_argument("--min-results", type=int, default=50)
    args = parser.parse_args()

    print("=" * 78)
    print("DIFFICULTY vs DISTANCE -- XC")
    print("=" * 78)

    report(args.min_results)

    print("\nNothing was written. This script is read only.\n")


if __name__ == "__main__":
    main()