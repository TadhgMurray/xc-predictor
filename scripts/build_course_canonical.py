"""
build_course_canonical.py -- writes the course_canonical mapping table.

DRY RUN BY DEFAULT. Pass --apply to write.

    python scripts/build_course_canonical.py            # show, write nothing
    python scripts/build_course_canonical.py --apply
    python scripts/build_course_canonical.py --radius 300 --min-sim 0.85 --apply

WHAT IT WRITES
    course_canonical maps each raw (course_name, lat, lng) triple to a
    canonical venue id. It CREATES a table and modifies nothing else --
    meets, course_difficulties and the engine are untouched. Rebuildable from
    scratch at any time, matching the project convention that every derived
    artifact is disposable.

WHY THE KEY IS A TRIPLE AND NOT JUST course_name
    course_name is NOT unique per venue. Measured: 18 distinct "Central Park"s
    and 19 "Riverside Park"s, spread across 271 degrees of longitude. Keying
    on name alone would collapse them into one venue -- the exact failure that
    disqualified course_id.

    ⚠ CONSEQUENCE FOR CONSUMERS: joining to this table requires the
    coordinates, not just the name, and they must be rounded to 5 decimals
    exactly as loadVenues does. A consumer that rounds differently gets no
    match rather than a wrong one -- a miss, not a bad merge -- but it will be
    silent. Round with round(gps_lat::numeric, 5).

DEFAULTS
    500 m / 0.8 similarity, chosen from the diag_course_clusters.py sweep.
    At 500 m the only merges that look wrong are absent, while the typo
    catches (Preformance/Rance, Acadamey, Cottonwoof) are all present. At
    1000 m a real error appears: Southwood Jr/Sr High School merging with
    White's Jr./Sr. High School at 681 m, where four generic tokens outvote
    the one distinctive one.
"""

import argparse

from psycopg2.extras import RealDictCursor, execute_values

from database import getConn
from course_identity import (
    normalizeCourseName,
    findMergePairs,
    buildClusters,
)
from course_name_rules import expandAbbreviations, blocksMerge


# Both sources, unioned. meets is anet-only (812,079 rows, zero tfrrs), so
# without the second leg every tfrrs XC venue is invisible to the engine --
# 1.13M rated results reaching it with difficulty = 0.
#
# The (name, lat, lng) key does cross-source merging for free: anet's
# "Holmdel Park" and tfrrs's "Holmdel Park" at the same coordinate collapse to
# one canonical venue, so both sources' results vote on one difficulty.
#
# ⚠ n_rows MIXES UNITS. `meets` fans out on div_id (many rows per meet);
# `meets_tfrrs` is one row per meet. So an anet spelling carries more weight
# than a tfrrs spelling of the same venue. n_rows is only used to CHOOSE THE
# DISPLAY SPELLING, never to compute anything, and preferring the anet spelling
# is the behaviour we want -- anet is the richer source. Documented rather than
# normalised, because normalising would change nothing except the arithmetic.
_LOAD_VENUES_SQL = """
WITH venue_rows AS (
    SELECT course_name, gps_lat, gps_long
    FROM meets
    WHERE gps_lat  IS NOT NULL AND gps_lat  <> 0
      AND gps_long IS NOT NULL AND gps_long <> 0
      AND course_name IS NOT NULL
      AND btrim(course_name) <> ''

    UNION ALL

    -- meets_tfrrs has no div_id: one row per (meet_id, sport), so no fan-out.
    -- sport is UPPERCASE here per invariant 0.6.
    SELECT venue_name AS course_name, gps_lat, gps_long
    FROM meets_tfrrs
    WHERE sport = 'XC'
      AND gps_lat  IS NOT NULL AND gps_lat  <> 0
      AND gps_long IS NOT NULL AND gps_long <> 0
      AND venue_name IS NOT NULL
      AND btrim(venue_name) <> ''
)
SELECT course_name,
       round(gps_lat::numeric,  5)::float8 AS lat,
       round(gps_long::numeric, 5)::float8 AS lng,
       count(*) AS n_rows
FROM venue_rows
GROUP BY 1, 2, 3
"""


# DROP then CREATE, not ALTER. The table is fully derived from meets plus the
# rules, so a rebuild is always cheaper and safer than a migration. Anything
# that depends on it must be able to survive a rebuild.
_CREATE_SQL = """
DROP TABLE IF EXISTS course_canonical;

CREATE TABLE course_canonical (
    course_name     text             NOT NULL,
    gps_lat         double precision NOT NULL,
    gps_long        double precision NOT NULL,
    canonical_id    integer          NOT NULL,
    canonical_name  text             NOT NULL,
    cluster_size    integer          NOT NULL,
    n_rows          bigint           NOT NULL,
    PRIMARY KEY (course_name, gps_lat, gps_long)
);

CREATE INDEX idx_course_canonical_id   ON course_canonical (canonical_id);
CREATE INDEX idx_course_canonical_name ON course_canonical (canonical_name);
"""


_INSERT_SQL = """
INSERT INTO course_canonical
    (course_name, gps_lat, gps_long, canonical_id,
     canonical_name, cluster_size, n_rows)
VALUES %s
"""


def loadVenues():
    """
    Distinct (name, point) venues with their row counts.

    GROUPED, not raw rows: meets fans out on div_id (invariant 0.7), so one
    venue appears many times. 5 decimals is ~1 m, finer than any threshold.

    'canonical' is normalize THEN expand. The expansion lifts correct
    "HS <-> High School" pairs from 0.67 to 1.00, which is what makes a 0.8
    similarity threshold safe rather than destructive.

    RealDictCursor is required: four columns, and positional indexing would
    break silently if anyone reordered them.
    """
    with getConn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(_LOAD_VENUES_SQL)
            rows = cursor.fetchall()

    venues = []
    for idx, row in enumerate(rows):
        venues.append({
            "idx": idx,
            "name": row["course_name"],
            "canonical": expandAbbreviations(
                normalizeCourseName(row["course_name"])),
            "gps_lat": row["lat"],
            "gps_long": row["lng"],
            "n_rows": row["n_rows"],
        })

    return venues


def _chooseCanonicalName(group, venues):
    """
    Pick the representative spelling for a cluster: the one with the most
    meet rows, ties broken alphabetically.

    MOST ROWS, because the dominant spelling is the one people actually use
    and the one already recognisable on the site. "Hedges Boyer Park" has 440
    rows and "Hedges-Boyer Park" has 4; the first is the real name.

    THE ALPHABETICAL TIE-BREAK IS NOT COSMETIC. Without it, two spellings with
    equal row counts would resolve according to dict iteration order, and the
    canonical name could silently change between runs on identical data --
    making the table non-reproducible and any diff against it meaningless.

    The sort key is a tuple. Python compares tuples element by element, so
    this sorts by -n_rows ascending (i.e. n_rows descending) and then by name
    ascending. Negating the count is how you get one field descending and
    another ascending in a single sorted() call.

    .strip() because the winning spelling often carries a trailing space
    ("Watkins Memorial "). Two clusters CAN legitimately end up with the same
    canonical_name after stripping -- canonical_id is the real key, and the
    name is for display.
    """
    ranked = sorted(group,
                    key=lambda idx: (-venues[idx]["n_rows"], venues[idx]["name"]))
    return venues[ranked[0]]["name"].strip()


def buildMapping(venues, radiusMeters, minNameSimilarity):
    """
    Cluster the venues and flatten to insertable rows.

    Returns (rows, stats) where each row is the tuple _INSERT_SQL expects.

    EVERY venue gets a row, including ones that merged with nothing --
    those are clusters of size 1 mapping to themselves. A consumer must be
    able to join unconditionally and always get an answer; if unmerged venues
    were absent, every consumer would need its own COALESCE fallback and one
    of them would eventually get it wrong.

    canonical_id is assigned from a counter over sorted cluster roots, so the
    same input always produces the same ids.
    """
    pairs = findMergePairs(venues, radiusMeters, minNameSimilarity,
                           blockFn=blocksMerge)
    clusters = buildClusters(len(venues), pairs)

    rows = []
    mergedClusters = 0
    mergedVenues = 0
    mergedRows = 0

    # sorted() over the roots so canonical_id assignment is deterministic.
    for canonicalId, root in enumerate(sorted(clusters.keys()), start=1):
        group = clusters[root]
        canonicalName = _chooseCanonicalName(group, venues)
        clusterSize = len(group)

        if clusterSize > 1:
            mergedClusters += 1
            mergedVenues += clusterSize
            mergedRows += sum(venues[idx]["n_rows"] for idx in group)

        for idx in group:
            venue = venues[idx]
            rows.append((
                venue["name"],
                venue["gps_lat"],
                venue["gps_long"],
                canonicalId,
                canonicalName,
                clusterSize,
                venue["n_rows"],
            ))

    stats = {
        "venues": len(venues),
        "canonical_venues": len(clusters),
        "merged_clusters": mergedClusters,
        "merged_venues": mergedVenues,
        "merged_rows": mergedRows,
        "pairs": len(pairs),
    }

    return rows, stats


def _printPlan(rows, stats, radiusMeters, minNameSimilarity):
    """Summary plus the largest clusters, so a dry run is actually readable."""
    print(f"\n  radius            {radiusMeters} m")
    print(f"  min similarity    {minNameSimilarity}")
    print(f"  pairs             {stats['pairs']:,}")
    print(f"  venues in         {stats['venues']:,}")
    print(f"  canonical venues  {stats['canonical_venues']:,}")
    print(f"  clusters >1       {stats['merged_clusters']:,}")
    print(f"  venues merged     {stats['merged_venues']:,}")
    print(f"  rows affected     {stats['merged_rows']:,}")

    # Group the flat rows back by canonical_id purely for display.
    byId = {}
    for row in rows:
        byId.setdefault(row[3], []).append(row)

    biggest = sorted(byId.values(), key=len, reverse=True)[:10]

    print("\n  LARGEST CLUSTERS (top 10)")
    for group in biggest:
        if len(group) < 2:
            continue
        canonicalName = group[0][4]
        totalRows = sum(item[6] for item in group)
        print(f"    -> {canonicalName}  [{len(group)} names, {totalRows:,} rows]")
        for item in sorted(group, key=lambda r: -r[6]):
            print(f"         {item[6]:>7,}  {item[0]}")


def writeMapping(rows):
    """
    Create the table and insert every row.

    ⚠ conn.commit() IS REQUIRED. getConn() hands out a pooled connection that
    does NOT auto-commit -- every other writer in this project ends with an
    explicit commit for the same reason. Without it the CREATE and the INSERT
    both roll back when the connection returns to the pool, and the script
    still reports success because it counted the rows it sent.

    execute_values batches the INSERT into few round trips instead of one per
    row; 26k individual INSERTs over a pool is slow enough to notice.

    DROP TABLE is the first statement, so a failure partway through must not
    destroy the previous mapping without replacing it. Everything is in one
    transaction, so a crash leaves the old table untouched.
    """
    with getConn() as conn:
        with conn.cursor() as cursor:
            cursor.execute(_CREATE_SQL)
            execute_values(cursor, _INSERT_SQL, rows, page_size=1000)
        conn.commit()


def main():
    parser = argparse.ArgumentParser(
        description="Build the course_canonical mapping table.")
    parser.add_argument("--radius", type=float, default=500,
                        help="merge radius in metres (default 500)")
    parser.add_argument("--min-sim", type=float, default=0.8,
                        help="minimum name similarity (default 0.8)")
    parser.add_argument("--apply", action="store_true",
                        help="actually write; without this it is a dry run")
    args = parser.parse_args()

    venues = loadVenues()
    rows, stats = buildMapping(venues, args.radius, args.min_sim)

    print(f"\nLoaded {len(venues):,} venues, "
          f"{sum(v['n_rows'] for v in venues):,} meet rows.")
    _printPlan(rows, stats, args.radius, args.min_sim)

    if not args.apply:
        print("\nDRY RUN -- nothing written. Re-run with --apply to write.\n")
        return

    writeMapping(rows)
    print(f"\nWrote {len(rows):,} rows to course_canonical "
          f"({stats['canonical_venues']:,} canonical venues).\n")


if __name__ == "__main__":
    main()