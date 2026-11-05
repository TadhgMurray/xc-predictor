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


# ★ A DELIBERATE SPLIT STAYS SPLIT (owner, 2026-10-08: "the rain course was
#   done manually by me"). "Mt. San Antonio College (rain course)" sits at
#   exactly the main course's coordinates and is a different, faster loop
#   (engine/course_merge.py measured -3.2% against +4.3%). course_merge
#   protects it, but this clustering never asked: the normalized names are
#   near-identical, so the rain course took the main course's canonical id
#   and the joint solve fitted the two as one -- the 2023 and 2025 CIF-SS
#   Finals read "-6.5%" and "-5.7%" days on the main Mt. SAC cell and pulled
#   its difficulty down. Same regex as course_merge, on the RAW names (the
#   normalized ones may have dropped the parenthesis): a protected name joins
#   only a name carrying the same protected suffix.
def protectedTag(name):
    """'(rain course)' etc. for a deliberately split name, else None."""
    import os as _os
    import sys as _sys
    _eng = _os.path.join(_os.path.dirname(_os.path.dirname(
        _os.path.abspath(__file__))), "engine")
    if _eng not in _sys.path:
        _sys.path.insert(0, _eng)
    from course_merge import _PROTECTED
    m = _PROTECTED.search(str(name or ""))
    return m.group(0).lower() if m else None


def protectedSplit(venueA, venueB):
    return protectedTag(venueA.get("name")) != protectedTag(venueB.get("name"))


# ★ ONE NAME, ONE PARK, A FEW HUNDRED METRES OR A TOWN APART (owner,
#   2026-11-02: Harkness Memorial State Park read +2.5% on its course page and
#   +5.1% on a race there). Three canonical ids for the one park: the main
#   one, a geocode 590 m off (outside the 500 m radius) and one 5 km off at
#   the town's centre (outside the 1 km search cells altogether). Each got
#   its own difficulty from its own few races.
#   EXACTLY the same name (after normalising and expanding) merges within
#   SAME_NAME_RADIUS, and within SAME_NAME_FAR when the name has
#   DISTINCTIVE_MIN words that are not the generic ones -- "Harkness
#   Memorial" is one place; "Central Park" and "Lincoln High School" are
#   many, and keep the ordinary rule. protectedSplit still vetoes (a rain
#   course stays its own).
SAME_NAME_RADIUS = 1500
SAME_NAME_FAR = 8000
DISTINCTIVE_MIN = 2
_GENERIC_VENUE = frozenset("""
    high school middle elementary junior senior jr sr park state county city town
    field fields complex course courses golf club country center centre regional
    community college university sports sport athletic athletics recreation rec area
    the of at and a trail trails grounds ground farm farms lake memorial north south
    east west central saint st stadium track xc cross
""".split())


def distinctive(canonical):
    """The words of a normalised venue name that name THIS place. Pure."""
    return [w for w in str(canonical or "").split() if w not in _GENERIC_VENUE and not w.isdigit()]


_SCHOOL_WORDS = frozenset({"high", "school", "middle", "elementary", "academy", "junior", "prep"})


_STATES = ("alabama alaska arizona arkansas california colorado connecticut delaware florida "
           "georgia hawaii idaho illinois indiana iowa kansas kentucky louisiana maine maryland "
           "massachusetts michigan minnesota mississippi missouri montana nebraska nevada ohio "
           "oklahoma oregon pennsylvania tennessee texas utah vermont virginia washington "
           "wisconsin wyoming").split()
_STATE_TAILS = set(_STATES) | {"hampshire", "jersey", "mexico", "york", "carolina", "dakota",
                               "island"} | {
    "al", "ak", "az", "ar", "ca", "co", "ct", "de", "fl", "ga", "hi", "id", "il", "in", "ia",
    "ks", "ky", "la", "me", "md", "ma", "mi", "mn", "ms", "mo", "mt", "ne", "nv", "nh", "nj",
    "nm", "ny", "nc", "nd", "oh", "ok", "or", "pa", "ri", "sc", "sd", "tn", "tx", "ut", "vt",
    "va", "wa", "wv", "wi", "wy", "guam"}


def townName(canonical):
    """A name that is a PLACE, not a venue -- "Yankton, South Dakota",
    "Knoxville, TN", "Bethany WV", a results-site link -- names every course
    in that town, so it never gets the far radius (owner's merge list,
    2026-11-02). Pure."""
    words = str(canonical or "").split()
    if not words:
        return True
    if any(w.startswith(("www", "http")) or ".com" in w for w in words):
        return True
    return words[-1] in _STATE_TAILS


def farName(canonical):
    """Does this name pick out ONE place, so a geocode a town away is still
    it? Two distinctive words ("Lehigh Parkway"), or a long non-school name
    with one ("Harkness Memorial State Park"). "Central Park", "Riverside
    Park", "Lincoln High School" do not. Pure."""
    words = str(canonical or "").split()
    if townName(canonical):
        return False
    d = distinctive(canonical)
    if len(d) >= DISTINCTIVE_MIN:
        return not (_SCHOOL_WORDS & set(words))
    return len(d) >= 1 and len(words) >= 4 and not (_SCHOOL_WORDS & set(words))


def sameNamePairs(venues):
    """[(idxA, idxB, metres, 1.0)] for venues with exactly the same normalised
    name inside SAME_NAME_RADIUS, or SAME_NAME_FAR for a distinctive name.
    Pure."""
    from course_identity import haversineMeters
    by = {}
    for v in venues:
        if v.get("canonical"):
            by.setdefault(v["canonical"], []).append(v)
    out = []
    for name, group in by.items():
        if len(group) < 2:
            continue
        reach = SAME_NAME_FAR if farName(name) else SAME_NAME_RADIUS
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                a, b = group[i], group[j]
                if protectedSplit(a, b):
                    continue
                d = haversineMeters(float(a["gps_lat"]), float(a["gps_long"]),
                                    float(b["gps_lat"]), float(b["gps_long"]))
                if d <= reach:
                    out.append((min(a["idx"], b["idx"]), max(a["idx"], b["idx"]), d, 1.0))
    return out


# ★ ONE PARK UNDER A SPELLING AND AN EVENT WORD (owner, 2026-11-03: search
#   listed "Laverne Gibson Course", "LaVern Gibson Championship XC Course"
#   and "LaVern Gibson Championship Course" -- the NCAA course, three ids).
#   The names differ by a letter and by "Championship", so neither the
#   exact-name rule above nor the similarity threshold joins them. The
#   CORE of a name is its distinctive words less the event words a feed
#   adds ("Championship", "Invitational"), each folded for spelling
#   (a final e: Laverne/LaVern). Two venues with the same core of at least
#   two words, inside SAME_NAME_RADIUS, are one. "Laverne Park" (one word)
#   never joins; a protected split still vetoes.
_EVENT_WORDS = frozenset("""
    championship championships champs invitational invite meet loop run race races
    classic open relays
""".split())


def coreKey(canonical):
    """The spelling-folded distinctive words of a venue name, less event
    words, as a sorted tuple; None under two words or for a town. Pure."""
    import re as _re
    if townName(canonical):
        return None
    words = str(canonical or "").split()
    if _SCHOOL_WORDS & set(words):
        return None
    core = set()
    for w in distinctive(canonical):
        if w in _EVENT_WORDS:
            continue
        w = _re.sub(r"[^a-z0-9]", "", w.lower())
        if len(w) > 4 and w.endswith("e"):
            w = w[:-1]
        if w:
            core.add(w)
    return tuple(sorted(core)) if len(core) >= DISTINCTIVE_MIN else None


def coreNamePairs(venues):
    """[(idxA, idxB, metres, 1.0)] for venues whose names share a core
    (coreKey) inside SAME_NAME_RADIUS. Pure."""
    from course_identity import haversineMeters
    by = {}
    for v in venues:
        k = coreKey(v.get("canonical"))
        if k:
            by.setdefault(k, []).append(v)
    out = []
    for group in by.values():
        if len(group) < 2 or len({v.get("canonical") for v in group}) < 2:
            continue
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                a, b = group[i], group[j]
                if a.get("canonical") == b.get("canonical") or protectedSplit(a, b):
                    continue
                d = haversineMeters(float(a["gps_lat"]), float(a["gps_long"]),
                                    float(b["gps_lat"]), float(b["gps_long"]))
                # ★ AS FAR AS AN IDENTICAL NAME (owner, 2026-11-05: three
                #   spellings of LaVern Gibson in the search box -- geocodes
                #   further apart than SAME_NAME_RADIUS): a core is two
                #   distinctive words, which is the bar SAME_NAME_FAR asks of
                #   one name. keepIds' agreement test guards the merge.
                if d <= SAME_NAME_FAR:
                    out.append((min(a["idx"], b["idx"]), max(a["idx"], b["idx"]), d, 1.0))
    return out


# ★ THE TIMES MUST AGREE (owner, 2026-11-05: "how can we merge courses like
#   these without merging things that are on the same location but perhaps a
#   different course (like mt sac rain vs regular)"). Two existing courses a
#   core-name merge would join keep their own ids when the solve has fitted
#   each (CORE_MIN_RESULTS+) and their difficulties differ by more than
#   CORE_AGREE: a loop that reads measurably faster or slower is another
#   course, whatever it is called. A protected tag ("(rain course)") splits
#   them before this is asked.
CORE_AGREE = 0.02
CORE_MIN_RESULTS = 200


def loadDifficulties():
    """{display course name: (results-weighted difficulty, results)} over
    every era of the course; {} without the table."""
    out = {}
    try:
        import os as _os
        import sys as _sys
        _rc = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "racecast")
        if _rc not in _sys.path:
            _sys.path.insert(0, _rc)
        from courses import courseDisplayName
        with getConn() as conn, conn.cursor() as cur:
            cur.execute("SELECT to_regclass('course_difficulties')")
            if cur.fetchone()[0] is None:
                return {}
            cur.execute("""SELECT course_name, difficulty, n_results FROM course_difficulties
                           WHERE course_name LIKE 'XC:%%' AND difficulty IS NOT NULL""")
            acc = {}
            for name, d, n in cur.fetchall():
                k = courseDisplayName(name)
                if k:
                    a = acc.setdefault(k, [0.0, 0])
                    a[0] += float(d) * (n or 0)
                    a[1] += n or 0
            out = {k: (v[0] / v[1], v[1]) for k, v in acc.items() if v[1]}
    except Exception as exc:                                  # noqa: BLE001
        print(f"  (course difficulties not read: {type(exc).__name__}: {exc})")
    return out


def coreAgree(names, diffs, agree=CORE_AGREE, min_n=CORE_MIN_RESULTS):
    """(ok, why) for joining these existing courses' names. Pure."""
    known = [(n, diffs[n][0]) for n in names if n in diffs and diffs[n][1] >= min_n]
    if len(known) < 2:
        return True, "fewer than two fitted, nothing to disagree"
    lo, hi = min(d for _n, d in known), max(d for _n, d in known)
    spread = hi - lo
    return (spread <= agree,
            "difficulties " + ", ".join(f"{n} {d:+.3f}" for n, d in known)
            + f" (spread {spread:.3f}, bar {agree})")


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
                           blockFn=blocksMerge, venueBlockFn=protectedSplit)
    pairs = pairs + sameNamePairs(venues) + coreNamePairs(venues)
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


def loadExisting():
    """{(course_name, lat, lng): (canonical_id, canonical_name)} from the live
    table, keyed exactly as loadVenues keys a venue; {} when there is none."""
    with getConn() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT to_regclass('course_canonical')")
            if cursor.fetchone()[0] is None:
                return {}
            cursor.execute("""SELECT course_name, round(gps_lat::numeric, 5)::float8,
                                     round(gps_long::numeric, 5)::float8,
                                     canonical_id, canonical_name
                              FROM course_canonical""")
            return {(n, la, lo): (cid, cname) for n, la, lo, cid, cname in cursor.fetchall()}


def keepIds(rows, old, merge_same_name=False, merge_core=False, diffs=None):
    """The fresh clustering, renumbered so every course keeps the id it has.

    ★ WHY (2026-09-26: Penrose Park, raced this season, had no difficulty
      because this table was last built by hand before it existed, and the
      build was never in the pipeline). It could not simply be added: ids
      come from a counter over sorted clusters, so ONE new venue shifts the
      id of every course after it -- and course_difficulties, keyed by id,
      would then hand every race page another course's number until the
      next solve went live. So:
        a venue already mapped      keeps its id and name, always;
        a new venue in a cluster    takes the id of that cluster's biggest
          with mapped venues          mapped venue (it is the same course);
        a new venue in a new        gets a fresh id above every id in use.
          cluster
      Two existing ids are never merged, even when a new venue bridges them:
      that would renumber a course, which is the thing this exists to stop.
    ★ EXCEPT, WITH merge_same_name (--merge-same-name, 2026-11-02), a
      cluster whose venues all carry the SAME normalised name and that holds
      several existing ids: one place under several ids (Harkness Memorial
      State Park). It takes the id of its biggest venue; the others' rows in
      course_difficulties stand orphaned until the next full solve fits the
      merged course. Printed in the plan, opt-in, never in the nightly.
    Pure. rows: buildMapping's tuples. Returns (rows, n_new_venues, n_new_ids).
    """
    keepIds.refused = []
    keepIds.detail = []
    by_cluster = {}
    for r in rows:
        by_cluster.setdefault(r[3], []).append(r)
    next_id = max((cid for cid, _ in old.values()), default=0) + 1
    out, n_new_v, n_new_ids = [], 0, 0
    merges = []
    for cid in sorted(by_cluster):
        members = by_cluster[cid]
        mapped = [(old[(m[0], m[1], m[2])], m[6]) for m in members
                  if (m[0], m[1], m[2]) in old]
        one_name = len({expandAbbreviations(normalizeCourseName(m[0])) for m in members}) == 1
        cores = {coreKey(expandAbbreviations(normalizeCourseName(m[0]))) for m in members}
        one_core = len(cores) == 1 and None not in cores
        n_old = len({oid for (oid, _n), _r in mapped})
        core_ok = False
        if merge_core and one_core and not one_name and n_old > 1:
            core_ok, why = coreAgree(sorted({nm for (_oid, nm), _r in mapped}), diffs or {})
            if not core_ok:
                getattr(keepIds, "refused", []).append(
                    (sorted({nm for (_oid, nm), _r in mapped}), why))
        if ((merge_same_name and one_name) or core_ok) and n_old > 1:
            (host_id, host_name), _n = max(mapped, key=lambda t: t[1])
            gone = sorted({oid for (oid, _nm), _r in mapped} - {host_id})
            merges.append((host_name, host_id, gone))
            # what the plan shows: every name joined, by which rule, and
            # each one's fitted difficulty (diffs) where there is one
            d = diffs or {}
            keepIds.detail.append((
                "same name" if (merge_same_name and one_name) else "core name",
                [f"{nm} ({d[nm][0]:+.3f}, {d[nm][1]} results)" if nm in d else nm
                 for nm in sorted({nm for (_oid, nm), _r in mapped})]))
            for m in members:
                if (m[0], m[1], m[2]) not in old:
                    n_new_v += 1
                out.append((m[0], m[1], m[2], host_id, host_name, 0, m[6]))
            continue
        if mapped:
            (host_id, host_name), _n = max(mapped, key=lambda t: t[1])
        else:
            host_id, host_name = next_id, members[0][4]
            next_id += 1
            n_new_ids += 1
        for m in members:
            key = (m[0], m[1], m[2])
            if key in old:
                oid, oname = old[key]
            else:
                oid, oname = host_id, host_name
                n_new_v += 1
            out.append((m[0], m[1], m[2], oid, oname, 0, m[6]))
    # ★ EXCEPT A DELIBERATE SPLIT THAT AN OLD BUILD MERGED (protectedSplit).
    #   "Keeps its id, always" would hand the rain course the main course's id
    #   on every rebuild, which is the bug. Where one id holds names with
    #   different protected tags, the tag with the most rows (the plain name)
    #   keeps it and each other tag gets a fresh id of its own, named as it
    #   is spelled. Its difficulty arrives with the next solve.
    by_id = {}
    for i, r in enumerate(out):
        by_id.setdefault(r[3], []).append(i)
    for cid, idxs in sorted(by_id.items()):
        tags = {}
        for i in idxs:
            tags.setdefault(protectedTag(out[i][0]), []).append(i)
        if len(tags) < 2:
            continue
        keep = max(tags, key=lambda t: sum(out[i][6] or 0 for i in tags[t]))
        for t, members in sorted(tags.items(), key=lambda kv: str(kv[0])):
            if t == keep:
                continue
            name = max(members, key=lambda i: out[i][6] or 0)
            for i in members:
                a, b, c, _d, _e, f, g = out[i]
                out[i] = (a, b, c, next_id, out[name][0], f, g)
            next_id += 1
            n_new_ids += 1
    sizes = {}
    for r in out:
        sizes[r[3]] = sizes.get(r[3], 0) + 1
    out = [(a, b, c, d, e, sizes[d], g) for a, b, c, d, e, _f, g in out]
    keepIds.merges = merges
    return out, n_new_v, n_new_ids


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
    parser.add_argument("--merge-same-name", action="store_true",
                        help="with --incremental: merge existing ids that are one venue under "
                             "one name (see sameNamePairs); listed in the plan")
    parser.add_argument("--merge-core-name", action="store_true",
                        help="with --incremental: merge existing ids whose names differ only by "
                             "filler (coreKey: 'LaVern Gibson Course' / '... Championship "
                             "Course') when their fitted difficulties agree within CORE_AGREE; "
                             "merges and refusals listed in the plan")
    parser.add_argument("--incremental", action="store_true",
                        help="keep every existing course's id; new venues join their "
                             "cluster's id or get a fresh one (the pipeline's mode, 06b)")
    args = parser.parse_args()

    venues = loadVenues()
    rows, stats = buildMapping(venues, args.radius, args.min_sim)
    if args.incremental:
        old = loadExisting()
        diffs = loadDifficulties() if args.merge_core_name else {}
        rows, n_new_v, n_new_ids = keepIds(rows, old, merge_same_name=args.merge_same_name,
                                           merge_core=args.merge_core_name, diffs=diffs)
        detail = getattr(keepIds, "detail", [])
        for i, (name, host, gone) in enumerate(getattr(keepIds, "merges", [])):
            print(f"  merge: {name}  ids {', '.join(map(str, gone))} -> {host}")
            if i < len(detail):
                print(f"      {detail[i][0]}: " + "  |  ".join(detail[i][1]))
        for names, why in getattr(keepIds, "refused", []):
            print(f"  KEPT APART: {' / '.join(names)}  -- {why}")
        print(f"\nincremental: {len(old):,} venues already mapped keep their ids; "
              f"{n_new_v:,} new venues ({n_new_ids:,} new courses, "
              f"{n_new_v - n_new_ids:,}-ish joining existing ones)")
        stats["canonical_venues"] = len({r[3] for r in rows})

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