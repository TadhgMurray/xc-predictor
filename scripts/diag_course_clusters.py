"""
diag_course_clusters.py -- READ ONLY. Proposes course-identity merges.

Writes nothing. Touches no pickle. Does not run the engine. Prints what a
given (radius, name-similarity) setting WOULD merge, and what the semantic
rules vetoed, so thresholds get chosen from real output instead of a guess.

    python scripts/diag_course_clusters.py
    python scripts/diag_course_clusters.py --min-sim 0.8 --samples 25
    python scripts/diag_course_clusters.py --no-rules      # rules off, A/B

Expects database.py, course_identity.py and course_name_rules.py alongside it
in scripts/. Python puts the script's own directory on sys.path.
"""

import argparse

from psycopg2.extras import RealDictCursor

from database import getConn
from course_identity import (
    normalizeCourseName,
    findMergePairs,
    buildClusters,
    haversineMeters,
    nameSimilarity,
    buildGridIndex,
    _neighborKeys,
)
from course_name_rules import expandAbbreviations, blocksMerge


# Metres. Observed single-venue fragmentation mostly sits under 300 m, but
# Shelby Farms Park legitimately spans ~2 km while Rolling Meadows Golf Course
# spans 3.6 km and is almost certainly two venues. No clean gap exists, so the
# sweep shows where the tradeoff turns.
#
# Do not exceed ~1000 m without raising cellDegrees in course_identity --
# see the note in buildGridIndex.
_RADII_TO_TRY = [100, 300, 500, 1000]


_LOAD_VENUES_SQL = """
SELECT course_name,
       round(gps_lat::numeric,  5)::float8 AS lat,
       round(gps_long::numeric, 5)::float8 AS lng,
       count(*) AS n_rows
FROM meets
WHERE gps_lat  IS NOT NULL AND gps_lat  <> 0
  AND gps_long IS NOT NULL AND gps_long <> 0
  AND course_name IS NOT NULL
  AND btrim(course_name) <> ''
GROUP BY 1, 2, 3
"""


def loadVenues():
    """
    Distinct (name, point) venues with their row counts.

    GROUPED, not raw rows: `meets` fans out on div_id (invariant 0.7), so one
    venue appears many times. 5 decimals is ~1 m, finer than any threshold.

    n_rows is how much data rides on each venue. A merge moving 500,000
    results deserves far more scrutiny than one moving 12.

    'canonical' is normalize THEN expand. The expansion is what lifts correct
    "HS <-> High School" pairs from 0.67 to 1.00, which is what makes a higher
    similarity threshold safe.

    RealDictCursor is required: this SELECT has four columns and positional
    indexing would break silently if anyone reordered them.

    Should return 22,019 venues / 811,090 rows.
    """
    with getConn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(_LOAD_VENUES_SQL)
            rows = cursor.fetchall()

    venues = []
    for idx, row in enumerate(rows):
        canonical = expandAbbreviations(normalizeCourseName(row["course_name"]))
        venues.append({
            "idx": idx,
            "name": row["course_name"],
            "canonical": canonical,
            "gps_lat": row["lat"],
            "gps_long": row["lng"],
            "n_rows": row["n_rows"],
        })

    return venues


def findVetoedPairs(venues, radiusMeters, minNameSimilarity, cellDegrees=0.01):
    """
    Pairs that passed distance AND similarity but were VETOED by a rule.

    THE OTHER HALF OF THE AUDIT. findMergePairs shows what the rules let
    through; this shows what they stopped. Without it a rule that is far too
    aggressive looks identical to one that is working -- both just produce
    fewer merges.

    Deliberately duplicates the traversal in findMergePairs rather than
    threading a collector through it. This is throwaway diagnostic code and
    must not complicate the function that will eventually feed the pipeline.
    """
    index = buildGridIndex(venues, cellDegrees)
    vetoed = []

    for cellKey, bucket in index.items():
        candidates = []
        for neighborKey in _neighborKeys(cellKey):
            candidates.extend(index.get(neighborKey, []))

        for venueA in bucket:
            for venueB in candidates:
                idxA = venueA["idx"]
                idxB = venueB["idx"]
                if idxA >= idxB:
                    continue

                distance = haversineMeters(
                    venueA["gps_lat"], venueA["gps_long"],
                    venueB["gps_lat"], venueB["gps_long"],
                )
                if distance > radiusMeters:
                    continue

                similarity = nameSimilarity(venueA["canonical"],
                                            venueB["canonical"])
                if similarity < minNameSimilarity:
                    continue

                blocked, reason = blocksMerge(venueA["canonical"],
                                              venueB["canonical"])
                if blocked:
                    vetoed.append((idxA, idxB, distance, similarity, reason))

    return vetoed


def _summarizeClusters(clusters, venues):
    """
    Headline counts for one setting.

    Rows matter more than clusters: 61.7% of venues hold only 12% of the data,
    so many tail merges are far less consequential than a few head merges.
    """
    merged = [group for group in clusters.values() if len(group) > 1]

    return {
        "clusters": len(merged),
        "venues_merged": sum(len(group) for group in merged),
        "rows_merged": sum(venues[idx]["n_rows"]
                           for group in merged for idx in group),
        "largest_cluster": max((len(group) for group in merged), default=0),
    }


def _printLargestClusters(clusters, venues, limit):
    """
    Biggest clusters first -- where a bad merge does the most damage.

    Above ~6 venues is suspect. Pau-Wa-Lu at 6 spellings is the worst
    LEGITIMATE case in this corpus; the 9-venue Cottonwood cluster at 500 m
    was a chain merge through "Complex" -> "Park" and should now be split by
    the facility veto.
    """
    merged = [group for group in clusters.values() if len(group) > 1]
    merged.sort(key=len, reverse=True)

    print(f"\n  LARGEST CLUSTERS (top {limit})")
    for group in merged[:limit]:
        totalRows = sum(venues[idx]["n_rows"] for idx in group)
        print(f"    [{len(group)} venues, {totalRows:,} rows]")
        for idx in sorted(group, key=lambda i: -venues[i]["n_rows"]):
            print(f"       {venues[idx]['n_rows']:>7,}  {venues[idx]['name']}")


def _printMarginalPairs(pairs, venues, limit):
    """
    Merges that only just cleared the similarity bar -- where the threshold
    is straining. If these look right, lower it. If wrong, the fix is usually
    a new rule, not a higher number.
    """
    ranked = sorted(pairs, key=lambda pair: pair[3])

    print(f"\n  MOST MARGINAL MERGES (lowest passing similarity, top {limit})")
    for idxA, idxB, distance, similarity in ranked[:limit]:
        print(f"    sim {similarity:.2f}  {distance:>6.0f}m   "
              f"{venues[idxA]['name']}   <->   {venues[idxB]['name']}")


def _printVetoedPairs(vetoed, venues, limit):
    """
    HIGHEST-similarity vetoes first -- the pairs the rules were least
    comfortable refusing, and therefore the most likely false positives.
    """
    ranked = sorted(vetoed, key=lambda item: -item[3])

    print(f"\n  VETOED BY RULE (highest similarity refused, top {limit})")
    for idxA, idxB, distance, similarity, reason in ranked[:limit]:
        print(f"    sim {similarity:.2f}  {distance:>6.0f}m  [{reason}]")
        print(f"        {venues[idxA]['name']}   <->   {venues[idxB]['name']}")


def runSweep(venues, minNameSimilarity, sampleLimit, useRules):
    """One pass per radius: summary, then detail."""
    print(f"\nLoaded {len(venues):,} venues, "
          f"{sum(v['n_rows'] for v in venues):,} meet rows.")
    print(f"Name-similarity threshold: {minNameSimilarity}")
    print(f"Semantic rules: {'ON' if useRules else 'OFF'}")

    blockFn = blocksMerge if useRules else None

    for radiusMeters in _RADII_TO_TRY:
        pairs = findMergePairs(venues, radiusMeters, minNameSimilarity,
                               blockFn=blockFn)
        clusters = buildClusters(len(venues), pairs)
        summary = _summarizeClusters(clusters, venues)

        print("\n" + "=" * 72)
        print(f"RADIUS {radiusMeters} m")
        print("=" * 72)
        print(f"  pairs proposed   {len(pairs):,}")
        print(f"  clusters formed  {summary['clusters']:,}")
        print(f"  venues merged    {summary['venues_merged']:,} of {len(venues):,}")
        print(f"  rows affected    {summary['rows_merged']:,}")
        print(f"  largest cluster  {summary['largest_cluster']} venues")

        _printLargestClusters(clusters, venues, sampleLimit)
        _printMarginalPairs(pairs, venues, sampleLimit)

        if useRules:
            vetoed = findVetoedPairs(venues, radiusMeters, minNameSimilarity)
            print(f"\n  vetoed by rule   {len(vetoed):,} pairs")
            _printVetoedPairs(vetoed, venues, sampleLimit)


def main():
    parser = argparse.ArgumentParser(
        description="Propose course-identity merges. Read only.")
    parser.add_argument("--min-sim", type=float, default=0.8,
                        help="minimum name similarity to merge (default 0.8; "
                             "safe to raise now that abbreviations expand)")
    parser.add_argument("--samples", type=int, default=15,
                        help="clusters/pairs to print per radius (default 15)")
    parser.add_argument("--no-rules", action="store_true",
                        help="disable semantic vetoes, for A/B comparison")
    args = parser.parse_args()

    venues = loadVenues()
    runSweep(venues, args.min_sim, args.samples, useRules=not args.no_rules)

    print("\nNothing was written. This script is read only.\n")


if __name__ == "__main__":
    main()