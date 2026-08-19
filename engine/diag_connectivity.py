"""
diag_connectivity.py -- READ ONLY. Is the athlete-course graph connected?

Writes nothing.

    python engine/diag_connectivity.py
    python engine/diag_connectivity.py --sport XC
    python engine/diag_connectivity.py --min-component 50

WHY THIS EXISTS
    Difficulty is only identifiable through athletes who race more than one
    course. Formally the engine solves

        log(norm_ij) = log(ability_i) + log(1 + d_c)

    a two-way fixed-effects model. Its parameters are identified only WITHIN a
    connected component of the bipartite athlete-course graph. Across two
    components the relative level is not merely badly estimated -- it is
    ARBITRARY. Nothing in the data determines it, and no solver can recover it.

    Measured symptom: the same athlete, same season, rates ~9.5 points lower in
    Texas than California. If TX and CA sit in different components that is the
    whole explanation and a better solver changes nothing. If they share one
    component the gap is a CONDITIONING problem -- poorly identified but
    identifiable -- and a proper least-squares solve with ridge would fix it.

    Those two conclusions demand completely different work, which is why this
    runs before any rewrite.

★ THIS IS AN UPPER BOUND ON CONNECTIVITY
    The engine keys athletes on (person_id, pool); this keys on person_id
    alone. Splitting by pool can only ever SUBDIVIDE a component, never merge
    two. So the engine's real connectivity is no better than what prints here.
    If this shows fragmentation, the engine is at least that fragmented.
"""

import argparse
from collections import Counter

import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

from speed_ratings_db import streamResults, getConn


# Column positions in the rows streamResults yields. These mirror the SELECT
# list in _xcQuery / _tfQuery:
#   0 result_id  1 person_id  2 normalized_time  3 grade  4 source
#   5 school     6 date       7 sport            8 venue  9 gender
#
# ⚠ If either query's SELECT order changes, these break SILENTLY -- the script
# would read a date as a venue and report nonsense rather than raising.
_PID = 1
_SPORT = 7
_VENUE = 8


def collectEdges(sports):
    """
    Stream every rated result -> (athleteCodes, courseCodes, venueNames).

    Returns two aligned int arrays of edges plus the venue string for each
    course code, so components can be named afterwards.

    VENUELESS ROWS ARE SKIPPED. A row with no course contributes nothing to
    connectivity -- it still informs its athlete's ability in the engine, but
    it links that athlete to nothing, which is precisely what this measures.

    Codes are assigned via dicts rather than sorting: one pass, and the code
    space stays dense so the sparse matrix has no empty rows.
    """
    athleteCode = {}
    courseCode = {}
    venueNames = []

    aOut = []
    cOut = []

    seen = 0
    for sport in sports:
        for batch in streamResults(sport):
            aBatch = np.empty(len(batch), dtype=np.int64)
            cBatch = np.empty(len(batch), dtype=np.int64)
            kept = 0

            for row in batch:
                venue = row[_VENUE]
                if venue is None:
                    continue

                # Namespace by sport, exactly as packResults does, so an XC and
                # a TF venue can never collide on the same string.
                vkey = f"{row[_SPORT]}:{venue}"

                pid = row[_PID]

                a = athleteCode.get(pid)
                if a is None:
                    a = len(athleteCode)
                    athleteCode[pid] = a

                c = courseCode.get(vkey)
                if c is None:
                    c = len(courseCode)
                    courseCode[vkey] = c
                    venueNames.append(vkey)

                aBatch[kept] = a
                cBatch[kept] = c
                kept += 1

            if kept:
                aOut.append(aBatch[:kept].copy())
                cOut.append(cBatch[:kept].copy())

            seen += len(batch)
            if seen % 5_000_000 < len(batch):
                print(f"    ...{seen:,} rows, {len(athleteCode):,} athletes, "
                      f"{len(courseCode):,} courses")

    return (np.concatenate(aOut), np.concatenate(cOut), venueNames)


def buildComponents(aCodes, cCodes, nAthletes, nCourses):
    """
    Connected components of the bipartite graph -> (nComponents, labels).

    THE SQUARE-MATRIX TRICK. scipy's connected_components needs a square
    adjacency matrix, but ours is bipartite and rectangular. So nodes 0..nA-1
    are athletes and nodes nA..nA+nC-1 are courses, and each edge becomes
    (athlete, nA + course) in one square matrix of size nA + nC.

    coo_matrix TOLERATES DUPLICATE ENTRIES -- an athlete racing a course ten
    times just adds the same edge ten times, and connectivity only depends on
    the nonzero pattern. That avoids deduplicating ~60M pairs, which would cost
    more memory than the matrix itself.

    directed=False treats the matrix as undirected, so an edge stored one way
    connects both ways and we do not need to store the symmetric half.

    `labels` is indexed the same way: labels[:nA] for athletes, labels[nA:] for
    courses.
    """
    total = nAthletes + nCourses

    graph = coo_matrix(
        (np.ones(aCodes.size, dtype=np.int8), (aCodes, cCodes + nAthletes)),
        shape=(total, total),
    ).tocsr()

    nComponents, labels = connected_components(graph, directed=False)
    return nComponents, labels


def reportComponents(nComponents, labels, nAthletes, nCourses, minComponent):
    """
    Component sizes, and how much of the corpus sits outside the giant one.

    ★ THE HEADLINE IS `courses outside the giant component`. Every one of those
    has a difficulty whose level is arbitrary relative to the main body of the
    data -- not noisy, ARBITRARY. No amount of iteration fixes it.
    """
    athleteLabels = labels[:nAthletes]
    courseLabels = labels[nAthletes:]

    sizes = Counter(labels.tolist())
    ranked = sizes.most_common()
    giant = ranked[0][0]

    athletesOut = int((athleteLabels != giant).sum())
    coursesOut = int((courseLabels != giant).sum())

    print(f"\n  COMPONENTS")
    print(f"    total components          {nComponents:,}")
    print(f"    giant component nodes     {ranked[0][1]:,} of {len(labels):,} "
          f"({100.0 * ranked[0][1] / len(labels):.2f}%)")
    print(f"    athletes outside giant    {athletesOut:,} of {nAthletes:,}")
    print(f"    courses  outside giant    {coursesOut:,} of {nCourses:,}")

    big = [(comp, size) for comp, size in ranked[1:] if size >= minComponent]

    print(f"\n    non-giant components with >= {minComponent} nodes: {len(big)}")
    for comp, size in big[:15]:
        nA = int((athleteLabels == comp).sum())
        nC = int((courseLabels == comp).sum())
        print(f"      component {comp:>7}: {size:>8,} nodes "
              f"({nA:,} athletes, {nC:,} courses)")

    return giant, courseLabels


def nameOrphanCourses(courseLabels, venueNames, giant, limit=30):
    """
    Print venues sitting outside the giant component, largest components first.

    XC venue keys are 'XC:<canonical_id>:d<distance>', so the ids get resolved
    to names via course_canonical. TF keys are already readable.

    A failed lookup prints the raw key rather than raising -- a diagnostic that
    dies because a name is missing is worse than one that prints an id.
    """
    orphanIdx = np.flatnonzero(courseLabels != giant)
    if orphanIdx.size == 0:
        print("\n  Every course is in the giant component.")
        return

    bySize = Counter(courseLabels[orphanIdx].tolist())

    wanted = set()
    for comp, _size in bySize.most_common(10):
        for idx in orphanIdx[courseLabels[orphanIdx] == comp][:6]:
            wanted.add(int(idx))

    ids = set()
    for idx in wanted:
        parts = venueNames[idx].split(":")
        if len(parts) >= 2 and parts[0] == "XC" and parts[1].isdigit():
            ids.add(int(parts[1]))

    names = {}
    if ids:
        with getConn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT DISTINCT canonical_id, canonical_name "
                    "FROM course_canonical WHERE canonical_id = ANY(%s)",
                    (list(ids),))
                names = dict(cur.fetchall())

    print(f"\n  SAMPLE ORPHAN VENUES (largest non-giant components)")
    shown = 0
    for comp, size in bySize.most_common(10):
        print(f"\n    component {comp} -- {size} courses")
        for idx in orphanIdx[courseLabels[orphanIdx] == comp][:6]:
            key = venueNames[int(idx)]
            parts = key.split(":")
            label = key
            if len(parts) >= 2 and parts[0] == "XC" and parts[1].isdigit():
                label = f"{names.get(int(parts[1]), key)}  [{key}]"
            print(f"      {label}")
            shown += 1
            if shown >= limit:
                return


def main():
    parser = argparse.ArgumentParser(
        description="Connected components of the athlete-course graph. Read only.")
    parser.add_argument("--sport", choices=["XC", "TF", "both"], default="both")
    parser.add_argument("--min-component", type=int, default=20,
                        help="smallest non-giant component to list (default 20)")
    args = parser.parse_args()

    sports = ("XC", "TF") if args.sport == "both" else (args.sport,)

    print("=" * 72)
    print(f"CONNECTIVITY DIAGNOSTIC -- {', '.join(sports)}")
    print("=" * 72)
    print("\n  streaming results...")

    aCodes, cCodes, venueNames = collectEdges(sports)

    nAthletes = int(aCodes.max()) + 1
    nCourses = len(venueNames)

    print(f"\n    edges     {aCodes.size:,}")
    print(f"    athletes  {nAthletes:,}")
    print(f"    courses   {nCourses:,}")
    print("\n  finding components...")

    nComponents, labels = buildComponents(aCodes, cCodes, nAthletes, nCourses)
    giant, courseLabels = reportComponents(
        nComponents, labels, nAthletes, nCourses, args.min_component)
    nameOrphanCourses(courseLabels, venueNames, giant)

    print("\n    READ: one giant component holding ~all courses -> the model is")
    print("    identified and the state offsets are a CONDITIONING problem, so")
    print("    a proper least-squares solve with ridge would fix them.")
    print("    Several large components -> those blocks are formally")
    print("    unidentified, their relative levels are arbitrary, and NO solver")
    print("    change helps. The fix would have to be structural.")
    print("\nNothing was written. This script is read only.\n")


if __name__ == "__main__":
    main()