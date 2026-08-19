"""
diag_engine_quality.py -- READ ONLY. Measures engine backlog items 1 and 6.

Writes nothing. Reads results.speed_rating and athlete_ratings as they stand.

    python scripts/diag_engine_quality.py
    python scripts/diag_engine_quality.py --sport TF

WHAT IT MEASURES

  C1  EARLY-SEASON INFLATION (backlog #1)
      Claim: early-season fields race rusty, the engine blames the COURSE, the
      course difficulty comes out too high, and every rating at that meet is
      inflated. Reported symptom: a UCSB field rated +40-53 over its own season
      average. SEASON_PHASE_CORRECTION is meant to remove exactly this, and
      SEASON_SHRINK_K=200 is suspected too strong for sparse early weeks.

      Measured WITHIN athlete: each race is compared to that athlete's OWN mean
      for the same season. That controls for field quality -- an early meet may
      genuinely attract better runners, and a raw week-by-week average could not
      tell that apart from inflation.

  C2  RATINGS COMPRESSION (backlog #6)
      Claim: a 140 and a 150 are far apart in time and should be further apart
      in rating. LINK_SHRINK_K=10 and DAMPING=0.3 are suspected too strong.

      The engine defines rating = pool_mean / ability * 100, so ability is
      proportional to 1/rating and two rating bins PREDICT a time ratio:

          predicted_time_ratio = rating_hi / rating_lo

      We bin athletes by rating and compare that prediction against their actual
      median normalized_time. observed > predicted means times spread further
      apart than ratings do -- i.e. compression, exactly as reported.

BASELINE FIRST. Run this BEFORE the course-canonical engine re-run and again
after. Both knobs are shrinkage parameters pulling on the same residuals, so
they must be measured together and moved once, not tuned in sequence.
"""

import argparse

from psycopg2.extras import RealDictCursor

from database import getConn


# XC seasons run roughly August to early December. day-of-year 213 is Aug 1,
# 340 is ~Dec 6. The engine itself stores doy (d.timetuple().tm_yday) for its
# season-phase term, so this uses the same clock rather than inventing one.
_SEASON_START_DOY = {"XC": 213, "TF": 60}
_SEASON_END_DOY = {"XC": 340, "TF": 190}

_TABLE = {"XC": "results", "TF": "results_tf"}

# An athlete needs a few races before "deviation from own season mean" means
# anything. With 2 races each deviation is just half the gap between them.
_MIN_SEASON_RACES = 4

# Rating bins for the compression test.
_BIN_LO, _BIN_HI, _BIN_COUNT = 80, 180, 20
_MIN_BIN_ATHLETES = 50


def _fetch(sql, params=None):
    """
    Run one read-only query, return a list of dicts.

    RealDictCursor throughout: these queries return 4-6 columns and positional
    indexing would break silently if anyone reordered a SELECT.
    """
    with getConn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(sql, params or ())
            return cursor.fetchall()


def probePools():
    """
    Print what `pool` values actually exist in athlete_ratings.

    THE SCHEMA LESSON, APPLIED. The docs disagree with the code here: one
    session file says pools are BARE ("hs_m") and sportless, while
    speed_ratings.poolOf clearly builds f"{pool}|{sport}". Whichever last wrote
    the table wins, and only the table knows. Everything below tolerates both
    shapes, but it is worth seeing which one is live before reading any number.
    """
    rows = _fetch("""
        SELECT pool, count(*) AS athletes
        FROM athlete_ratings
        GROUP BY 1
        ORDER BY athletes DESC
        LIMIT 15
    """)

    print("\n  POOLS PRESENT IN athlete_ratings")
    for row in rows:
        print(f"    {row['athletes']:>9,}  {row['pool']}")


# --------------------------------------------------------------------------
# C1 -- EARLY-SEASON INFLATION
# --------------------------------------------------------------------------

_SEASON_PHASE_SQL = """
WITH raced AS (
    SELECT person_id,
           speed_rating,
           substring(date, 1, 4)::int          AS season_year,
           EXTRACT(DOY FROM date::date)::int   AS doy
    FROM {table}
    WHERE speed_rating IS NOT NULL
      AND person_id IS NOT NULL
      AND date ~ '^(19|20)[0-9]{{2}}-[0-9]{{2}}-[0-9]{{2}}$'
),
seasoned AS (
    SELECT raced.*,
           avg(speed_rating) OVER w AS season_mean,
           count(*)          OVER w AS season_races
    FROM raced
    WINDOW w AS (PARTITION BY person_id, season_year)
)
SELECT ((doy - %(start)s) / 7) + 1                        AS week,
       count(*)                                           AS races,
       count(DISTINCT person_id)                          AS athletes,
       round(avg(speed_rating - season_mean)::numeric, 2)  AS mean_dev,
       round(avg(speed_rating)::numeric, 1)                AS mean_rating
FROM seasoned
WHERE season_races >= %(min_races)s
  AND doy BETWEEN %(start)s AND %(end)s
GROUP BY 1
ORDER BY 1
"""


def measureSeasonPhase(sport):
    """
    Mean within-athlete rating deviation, by week of season.

    The date column is TEXT, not a date -- the precompute SQL elsewhere in this
    project uses substring(date,1,4)::int, and the engine's _asDate accepts both
    a date and a string. So the regex guard runs BEFORE the ::date cast: a
    malformed date would otherwise abort the whole query rather than skip a row.

    ★ THE GUARD THAT PREVENTS THE CRASH IS THE GUARD THAT HIDES THE DATA. That
    regex silently drops any row whose date is not exactly YYYY-MM-DD. If the
    race counts below look far too low, audit what it discarded before reading
    anything into the numbers.

    WINDOW w AS (...) names the window once and reuses it for both avg and
    count, so Postgres sorts the partition a single time instead of twice.
    """
    rows = _fetch(
        _SEASON_PHASE_SQL.format(table=_TABLE[sport]),
        {"start": _SEASON_START_DOY[sport],
         "end": _SEASON_END_DOY[sport],
         "min_races": _MIN_SEASON_RACES},
    )

    print(f"\n  C1 -- EARLY-SEASON INFLATION ({sport})")
    print(f"  within-athlete deviation from own season mean, "
          f"athletes with >= {_MIN_SEASON_RACES} races")
    print(f"\n    {'week':>5} {'races':>10} {'athletes':>10} "
          f"{'mean_dev':>10} {'mean_rating':>12}")

    for row in rows:
        flag = "  <-- inflated" if row["mean_dev"] and row["mean_dev"] > 5 else ""
        print(f"    {row['week']:>5} {row['races']:>10,} {row['athletes']:>10,} "
              f"{row['mean_dev']:>10} {row['mean_rating']:>12}{flag}")

    print("\n    READ: mean_dev is what SEASON_PHASE_CORRECTION exists to flatten.")
    print("    Near zero across all weeks -> the correction is working, item 1 closes.")
    print("    Large positive in weeks 1-3  -> still under-correcting; "
          "SEASON_SHRINK_K=200 is too strong for sparse early weeks.")
    print("    Large NEGATIVE in weeks 1-3  -> now OVER-correcting. Do not read "
          "this as success.")


# --------------------------------------------------------------------------
# C2 -- RATINGS COMPRESSION
# --------------------------------------------------------------------------

_COMPRESSION_SQL = """
WITH rated AS (
    SELECT athlete_id AS person_id,
           speed_rating
    FROM athlete_ratings
    WHERE speed_rating > 0
      AND n_races >= 5
      AND (pool = %(pool)s OR pool LIKE %(pool_sport)s)
),
timed AS (
    SELECT person_id,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY normalized_time) AS med_nt
    FROM {table}
    WHERE normalized_time IS NOT NULL
      AND person_id IS NOT NULL
    GROUP BY 1
)
SELECT width_bucket(rated.speed_rating, %(lo)s, %(hi)s, %(bins)s) AS bin,
       count(*)                                          AS athletes,
       round(avg(rated.speed_rating)::numeric, 1)        AS mean_rating,
       round(percentile_cont(0.5) WITHIN GROUP
             (ORDER BY timed.med_nt)::numeric, 1)        AS med_time
FROM rated
JOIN timed USING (person_id)
GROUP BY 1
HAVING count(*) >= %(min_bin)s
ORDER BY 1
"""


def _compressionRatios(bins):
    """
    Compare predicted against observed time ratio between adjacent bins.

    The engine defines rating = pool_mean / ability * 100, so ability is
    proportional to 1/rating and two bins predict a time ratio directly:

        predicted = rating_hi / rating_lo
        observed  = time_lo / time_hi        (faster athletes -> higher rating)

    ratio = observed / predicted
        ~1.0  ratings track time as the algebra says. No compression.
        >1.0  times spread FURTHER apart than ratings do -> COMPRESSED.
        <1.0  ratings spread further than times -> exaggerated.

    Adjacent bins only. Comparing the extremes would fold every intermediate
    distortion into one number and hide where along the scale it happens.
    """
    out = []
    for lower, upper in zip(bins, bins[1:]):
        ratingLo = float(lower["mean_rating"])
        ratingHi = float(upper["mean_rating"])
        timeLo = float(lower["med_time"])
        timeHi = float(upper["med_time"])

        if ratingLo <= 0 or timeHi <= 0:
            continue

        predicted = ratingHi / ratingLo
        observed = timeLo / timeHi
        out.append((lower, upper, predicted, observed, observed / predicted))

    return out


def measureCompression(sport, pool):
    """
    Do rating gaps match the time gaps they imply?

    `pool` is passed bare ("hs_m"); the query accepts both that and the
    sport-suffixed form, since which one is live depends on when the table was
    last written.

    n_races >= 5 excludes athletes whose ability is mostly shrinkage rather than
    evidence -- they would show apparent compression that is really just a small
    sample pulled toward the pool mean, which is correct behaviour, not the bug.
    """
    bins = _fetch(
        _COMPRESSION_SQL.format(table=_TABLE[sport]),
        {"pool": pool, "pool_sport": f"{pool}|%",
         "lo": _BIN_LO, "hi": _BIN_HI, "bins": _BIN_COUNT,
         "min_bin": _MIN_BIN_ATHLETES},
    )

    print(f"\n  C2 -- RATINGS COMPRESSION ({sport}, pool {pool})")

    if len(bins) < 2:
        print(f"    only {len(bins)} usable bins -- pool absent or too thin.")
        return

    print(f"\n    {'rating':>8} {'athletes':>10} {'med_time':>10}")
    for row in bins:
        print(f"    {row['mean_rating']:>8} {row['athletes']:>10,} "
              f"{row['med_time']:>10}")

    print(f"\n    {'from':>8} {'to':>8} {'predicted':>11} "
          f"{'observed':>10} {'ratio':>8}")

    ratios = _compressionRatios(bins)
    for lower, upper, predicted, observed, ratio in ratios:
        flag = "  <-- compressed" if ratio > 1.15 else ""
        print(f"    {lower['mean_rating']:>8} {upper['mean_rating']:>8} "
              f"{predicted:>11.3f} {observed:>10.3f} {ratio:>8.3f}{flag}")

    if ratios:
        mean = sum(r[4] for r in ratios) / len(ratios)
        print(f"\n    mean ratio: {mean:.3f}")
        print("    READ: ~1.0 -> ratings track time correctly, item 6 closes.")
        print("    >1.15 -> real compression; LINK_SHRINK_K=10 / DAMPING=0.3 "
              "are the suspects.")
        print("    ★ Do NOT move either knob on this alone -- both also move C1. "
              "Read both, then change one thing.")


def main():
    parser = argparse.ArgumentParser(
        description="Measure engine backlog items 1 and 6. Read only.")
    parser.add_argument("--sport", choices=["XC", "TF"], default="XC")
    parser.add_argument("--pools", default="hs_m,hs_f,college_m,college_f",
                        help="comma-separated pools for the compression test")
    args = parser.parse_args()

    print("=" * 72)
    print(f"ENGINE QUALITY DIAGNOSTIC -- {args.sport}")
    print("=" * 72)

    probePools()
    measureSeasonPhase(args.sport)

    for pool in args.pools.split(","):
        measureCompression(args.sport, pool.strip())

    print("\nNothing was written. This script is read only.\n")


if __name__ == "__main__":
    main()