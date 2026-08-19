# Project: xc-predictor
# Author:  Tadhg Murray
# File:    engine/location_merge.py
# Purpose: collapse duplicate location_ids that are the same physical venue.
#
#   THE BUG
#   -------
#   The TF difficulty cell is keyed TF:loc:<location_id>:<in|out>. When one
#   venue carries two location_ids, its evidence is SPLIT across two cells and
#   each gets its own thin fit:
#
#       Redondo Nike Track Festival   2011-2024  loc 60874   2,107 rows
#                                     2025       loc 138145    199 rows
#                                     33.8471,-118.3841 BOTH -- identical to
#                                     four decimals, i.e. the same point.
#
#       Dublin Distance Fiesta        2009-2026  loc 68937   4,053 rows
#                                     2024 ONLY  loc 137743    328 rows
#
#       Granada Distance & Sprint     2019,2026  loc 68784   4,954 rows
#                                     2022-2025  loc 68785     872 rows
#
#   Nothing about these venues changed. A duplicate location row was minted --
#   the high, late ids (137743, 138145) look like a geocoding pass creating
#   rows instead of matching existing ones. The symptom the owner saw is a
#   meet whose difficulty differs year to year at a fixed venue.
#
#   ★ THIS IS THE La Salle BUG ONE LAYER DOWN. There a node key MERGED
#     distinct institutions; here a location key SPLITS one venue. Both break
#     the same invariant: the key must partition reality, not cut across it.
#
#   MEASURED SCOPE: 7,923 location_ids in duplicate coordinate groups,
#   1,703,846 meets_tf rows -- about 12% of the table.
#
#   TWO TIERS, DIFFERENT EVIDENCE
#   -----------------------------
#   The pair-distance histogram has a spike and NO trough:
#
#        0- 50 m   13,681 pairs   <- 5.3x the next bin
#       50-100      2,600
#      100-150      2,103
#      150-200      1,660          smooth decay, no second population
#
#   So distance separates cleanly ONLY in the first bin. Past 50 m the
#   "same venue" and "different nearby venue" populations overlap and no
#   threshold can split them.
#
#     TIER 1  <= 50 m. Two independently-geocoded points 50 m apart are inside
#             one track. Distance alone is sufficient.
#
#     TIER 2  50-500 m, plus a TEMPORAL signature: a venue whose id defects
#             for one season and returns is a duplicate, not a move. Dublin is
#             68937 for seventeen years EXCEPT 2024. That pattern is stronger
#             evidence than 140 m of distance, and it is independent of it.
#
#   ⚠ CONSERVATIVE ON PURPOSE. A missed merge leaves a cell fragmented -- the
#     status quo. A WRONG merge pools two genuinely different venues into one
#     difficulty and corrupts both. The errors are not symmetric, so tier 1
#     deliberately misses Dublin (140 m) and tier 2 has to earn it separately.

import os
import sys

# ★ scripts/ ON THE PATH AT IMPORT TIME, NOT INSIDE __main__. `database` and
#   `corrections` live there, and a module imported BY another script never
#   runs its own __main__ block -- so a path fix that only happens there works
#   when the file is run directly and fails when it is imported.
_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.dirname(_HERE),
           os.path.join(os.path.dirname(_HERE), "scripts")):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

# ------------------------------------------------------------------ #
#  CONSTANTS -- the measured ones are marked
# ------------------------------------------------------------------ #

# ★ READ OFF THE HISTOGRAM, NOT CHOSEN. The 0-50 m bin holds 13,681 pairs
#   against 2,600 in 50-100 -- a 5.3x cliff. Everything above 50 m decays
#   smoothly with no gap, so 50 is the last point where distance is decisive.
TIER1_METRES = 50.0

# Tier 2's distance window. Not a merge criterion on its own -- the temporal
# signature does the deciding. 500 m keeps it inside a plausible campus.
TIER2_METRES = 500.0

# A venue must appear in at least this many distinct years for a one-year
# defection to be meaningful. Two years cannot show "defects and returns".
TIER2_MIN_YEARS = 4

# Grid used to bucket candidate pairs before measuring.
#
# ★ SIZE THE GRID TO THE SEARCH RADIUS. This was 0.01 deg (~1.1 km lat), which
#   made the 3x3 neighbourhood a 3.3 km x 2.6 km box -- scanned to find pairs
#   within 500 m, and originally within 50 m. In a city that is a thousandfold
#   more candidate pairs than the answer needs, and it dominated the run.
#
#   0.006 deg is ~670 m of latitude and ~530 m of longitude at 38N, so the 3x3
#   neighbourhood still GUARANTEES coverage of TIER2_METRES (500 m) -- the
#   nearest point in a non-adjacent cell is at least one full cell away -- while
#   cutting the candidate set by roughly (0.01/0.006)^2 ~ 2.8x.
#
# ⚠ THE INVARIANT: GRID * 111320 * cos(max latitude of interest) must exceed
#   TIER2_METRES, or the 3x3 box stops covering the search radius and real
#   duplicates go silently unproposed. At 49N (the northern edge of the lower
#   48) 0.006 deg of longitude is ~438 m, which is under 500 m -- so the
#   neighbourhood is widened to 5x5 in buildPairs rather than shrinking
#   the grid further, because a wider neighbourhood on a fine grid is still far
#   cheaper than a 3x3 on a coarse one.
GRID = 0.006


# ------------------------------------------------------------------ #
# CHUNK 1 -- THE POINT TABLE
# ------------------------------------------------------------------ #

def buildPoints(cur):
    """
    loc_pts: one row per location_id with coordinates, a grid key, and its
    row weight.

    ★ A REAL TABLE, NOT A CTE. The same join over a CTE planned as a nested
      loop (cost 8.45M) because Postgres cannot index a CTE.

    ★ gx/gy ARE INTEGER GRID CELLS, and that is what makes the pair join fast.
      An earlier version joined with `round(b.lat,2) BETWEEN round(a.lat,2)
      - 0.01 AND + 0.01`. That is a RANGE condition: hash joins cannot use it,
      so the planner fell back to a nested loop over 8,476^2 ~ 72M pairs. With
      integer cells the neighbourhood becomes nine EQUALITY joins, which hash.

    `rows` is the tie-break for which id survives a merge: the id carrying the
    most evidence wins, so the surviving cell keeps the thickest history and
    the fewest rows have to be rewritten.
    """
    cur.execute("DROP TABLE IF EXISTS loc_pts")
    cur.execute(f"""
        CREATE TABLE loc_pts AS
        SELECT location_id,
               avg(gps_lat)::float8  AS lat,
               avg(gps_long)::float8 AS lon,
               floor(avg(gps_lat)  / {GRID})::int AS gx,
               floor(avg(gps_long) / {GRID})::int AS gy,
               count(*)                           AS rows
        FROM   meets_tf
        WHERE  location_id IS NOT NULL
          AND  gps_lat  IS NOT NULL
          AND  gps_long IS NOT NULL
        GROUP  BY 1
    """)
    # ⚠ THIS INDEX IS LOAD-BEARING. buildPairs self-joins loc_pts once per
    #   grid offset (25 of them) on (gx, gy). Without it the planner has
    #   nothing to probe and the join degrades catastrophically.
    cur.execute("CREATE INDEX ON loc_pts (gx, gy)")
    cur.execute("CREATE UNIQUE INDEX ON loc_pts (location_id)")
    cur.execute("ANALYZE loc_pts")
    cur.execute("SELECT count(*), sum(rows) FROM loc_pts")
    n, rows = cur.fetchone()
    print(f"    {n:,} located venues covering {rows:,} meets_tf rows")
    return n


def _distanceSql(a="a", b="b"):
    """
    Equirectangular distance in metres between two loc_pts rows.

    Accurate to well under a metre at these separations and far cheaper than
    haversine. 111320 m per degree of latitude; longitude degrees shrink by
    cos(latitude), taken at the pair's midpoint.
    """
    return f"""
        sqrt( power(({a}.lat - {b}.lat) * 111320.0, 2)
            + power(({a}.lon - {b}.lon) * 111320.0
                    * cos(radians(({a}.lat + {b}.lat) / 2)), 2) )"""


# The 5x5 grid neighbourhood, as a literal VALUES list.
#
# ★ NOT generate_series. Postgres cannot estimate a set-returning function and
#   cannot correlate two of them with the join keys, so with generate_series it
#   planned `b.gx = a.gx + dx` as a JOIN FILTER inside a nested loop over the
#   series -- hashing on gy alone, ignoring the (gx, gy) index entirely, and
#   materialising 293,306 rows to loop over five times. A literal VALUES list
#   is a known 25 rows and lets the offsets be folded into concrete columns.
_OFFSETS = ",".join(f"({dx},{dy})"
                    for dx in range(-2, 3) for dy in range(-2, 3))


def buildExpanded(cur):
    """
    loc_exp: loc_pts x the 25 grid offsets, as a REAL table with statistics.

    ★ MATERIALISED, NOT A SUBQUERY -- AND THIS IS THE WHOLE POINT. Expanding
      inline left the planner estimating 122 rows for a join that produces
      ~900,000, so it chose a Merge Join and spent 90% of the plan's cost
      sorting 915,850 rows. With a real table and ANALYZE it has honest
      statistics and an index on both sides, and reaches the Hash Join
      (loc_pts hashed, loc_exp probing, no sort) on its own -- no
      `SET enable_mergejoin = off` needed.

      A session flag fixes one run. Statistics fix every run.
    """
    cur.execute("DROP TABLE IF EXISTS loc_exp")
    cur.execute(f"""
        CREATE TABLE loc_exp AS
        SELECT p.location_id, p.lat, p.lon, p.rows,
               p.gx + o.dx AS kx,
               p.gy + o.dy AS ky
        FROM   loc_pts p
        CROSS  JOIN (VALUES {_OFFSETS}) AS o(dx, dy)
    """)
    cur.execute("CREATE INDEX ON loc_exp (kx, ky)")
    cur.execute("ANALYZE loc_exp")
    cur.execute("SELECT count(*) FROM loc_exp")
    n = cur.fetchone()[0]
    print(f"    {n:,} expanded grid entries")
    return n


def buildPairs(cur):
    """
    loc_pair: EVERY location pair within TIER2_METRES, computed ONCE.

    ★ THIS IS THE FIX FOR THE RUN TIME. The pair scan used to happen three
      times: tier1 generated it at 50 m, heldPairs regenerated it at 500 m
      (a hundredfold larger set), and tier2 computed distances a third time
      inside its own join. All three now read this table.

    ★ 5x5 NEIGHBOURHOOD, NOT 3x3. With GRID at 0.006 deg, one cell of
      longitude is ~530 m at 38N but only ~438 m at 49N -- under TIER2_METRES,
      so a 3x3 box would stop covering the search radius in the north and
      silently drop real duplicates. Widening the neighbourhood on a fine grid
      is still far cheaper than a 3x3 on a coarse one, and it removes the
      latitude dependence instead of hiding it.

    ★ THE DISTANCE IS COMPUTED ONCE PER PAIR, in the inner select, then
      filtered outside. Putting it in both the SELECT and the WHERE made every
      candidate pay two sqrt and two cos.

    `b.location_id > a.location_id` yields each pair once.
    """
    cur.execute("DROP TABLE IF EXISTS loc_pair")
    cur.execute(f"""
        CREATE TABLE loc_pair AS
        SELECT * FROM (
            SELECT a.location_id AS id_a, a.rows AS rows_a,
                   b.location_id AS id_b, b.rows AS rows_b,
                   {_distanceSql()} AS metres
            FROM   loc_exp a
            JOIN   loc_pts b
                   ON  b.gx = a.kx
                   AND b.gy = a.ky
                   AND b.location_id > a.location_id
        ) d
        WHERE d.metres <= {TIER2_METRES}
    """)
    cur.execute("CREATE INDEX ON loc_pair (id_a, id_b)")
    cur.execute("CREATE INDEX ON loc_pair (metres)")
    cur.execute("ANALYZE loc_pair")
    cur.execute("SELECT count(*) FROM loc_pair")
    n = cur.fetchone()[0]
    cur.execute(f"SELECT count(*) FROM loc_pair WHERE metres <= {TIER1_METRES}")
    near = cur.fetchone()[0]
    print(f"    {n:,} candidate pairs within {TIER2_METRES:.0f} m "
          f"({near:,} within {TIER1_METRES:.0f} m)")
    return n


# ------------------------------------------------------------------ #
# CHUNK 1b -- SURFACE ATTRIBUTES (the merge veto)
# ------------------------------------------------------------------ #
#
# Coordinates prove two ids are in the SAME PLACE. They do not prove the same
# SURFACE, and the cell key is TF:loc:<location_id>:<in|out> -- so merging an
# indoor id into an outdoor one would pool two genuinely different running
# surfaces into one difficulty. That is a worse outcome than the fragmentation
# we are fixing: a fragmented cell is thin, a wrongly-merged cell is WRONG.
#
# ⚠ STATE IS DELIBERATELY NOT A CONSTRAINT. An earlier check flagged dozens of
#   Okinawa pairs whose only difference was 'OS' vs 'os' -- the same value in
#   different case -- and the column also carries non-state codes ('os', '66',
#   'NCR') for overseas meets. A casing difference is not evidence of two
#   venues, and coordinates already establish location far more reliably than
#   a free-text code. Including it would have blocked correct merges for a
#   reason that has nothing to do with geography.
#
# ⚠ WHAT THIS VETO WILL ALSO CATCH, AND WHY IT IS STILL RIGHT. Several Florida
#   pairs disagree on is_indoor while their meet names read "Maroon and Gold
#   Indoor/Outdoor", "Red and Black Indoor/Outdoor", "Indoor/Outdoor Series
#   Meet#3". Florida has almost no indoor facilities; is_indoor=1 there is
#   near-certainly parsed out of the meet TITLE, not the venue. So the flag is
#   probably the thing that is wrong, and the merge is probably fine.
#
#   The veto still holds them, because the cost is asymmetric: skipping a good
#   merge leaves a cell exactly as fragmented as it is today, while making a
#   bad one corrupts two cells and is invisible afterwards. They surface in
#   the held list for a human, which is where a probably belongs.


def _attrVetoSql(a="xa", b="xb"):
    """
    True when two ids' surface attributes CONFLICT.

    ★ `IS DISTINCT FROM`, NOT `<>`. These columns are nullable, and `NULL <> 1`
      evaluates to NULL -- not true -- so a plain inequality silently passes
      every conflict involving a NULL. IS DISTINCT FROM treats NULL as a value.

    That is deliberately strict: it treats "known indoor vs unknown" as a
    conflict rather than assuming agreement. Unknown is not agreement, and the
    held list is cheap.
    """
    return f"""(
           {a}.is_indoor    IS DISTINCT FROM {b}.is_indoor
        OR {a}.track_type   IS DISTINCT FROM {b}.track_type
        OR {a}.track_length IS DISTINCT FROM {b}.track_length
    )"""


def buildAttrs(cur):
    """
    loc_attrs: one surface signature per location_id.

    `n_*` count DISTINCT values WITHIN one id. An id that is internally
    inconsistent -- some rows indoor, some outdoor under the SAME location --
    is its own problem and cannot be used to veto anything, so it is recorded
    and reported rather than silently collapsed by the min().
    """
    cur.execute("DROP TABLE IF EXISTS loc_attrs")
    cur.execute("""
        CREATE TABLE loc_attrs AS
        SELECT location_id,
               min(is_indoor)               AS is_indoor,
               min(track_type)              AS track_type,
               min(track_length)            AS track_length,
               count(DISTINCT is_indoor)    AS n_indoor,
               count(DISTINCT track_type)   AS n_type,
               count(DISTINCT track_length) AS n_len,
               min(meet_name)               AS sample
        FROM   meets_tf
        WHERE  location_id IS NOT NULL
        GROUP  BY 1
    """)
    cur.execute("CREATE UNIQUE INDEX ON loc_attrs (location_id)")
    cur.execute("ANALYZE loc_attrs")
    cur.execute("""SELECT count(*) FILTER (WHERE n_indoor > 1
                                              OR n_type > 1
                                              OR n_len > 1)
                   FROM loc_attrs""")
    inconsistent = cur.fetchone()[0]
    if inconsistent:
        print(f"    ⚠ {inconsistent:,} location_ids are internally "
              f"inconsistent on surface attributes")
    return inconsistent


def heldPairs(cur, limit=40):
    """
    Pairs the veto blocked. Printed, never silently dropped.

    ★ EXCLUSION STAYS VISIBLE. A held pair is a venue whose cell stays
      fragmented, which is a real cost -- it belongs in the log where someone
      can look at the meet names and decide, not in a filter nobody sees.
    """
    cur.execute(f"""
        SELECT round(p.metres::numeric, 1),
               p.id_a, xa.is_indoor, xa.track_type, xa.track_length,
               p.id_b, xb.is_indoor, xb.track_type, xb.track_length,
               left(xa.sample, 28), left(xb.sample, 28)
        FROM loc_pair p
        JOIN loc_attrs xa ON xa.location_id = p.id_a
        JOIN loc_attrs xb ON xb.location_id = p.id_b
        WHERE {_attrVetoSql()}
        ORDER BY p.metres
        LIMIT %s
    """, (limit,))
    rows = cur.fetchall()
    if not rows:
        print("    no pairs held by the surface veto")
        return 0
    print(f"\n    HELD by surface conflict (showing {len(rows)}):")
    print(f"    {'m':>6} {'id_a':>9} {'in':>3} {'type':>8} {'len':>6}"
          f" {'id_b':>9} {'in':>3} {'type':>8} {'len':>6}  meets")
    for (m, ia, ii, it, il, ib, ji, jt, jl, na, nb) in rows:
        print(f"    {m:>6} {ia:>9} {str(ii):>3} {str(it or '-'):>8} "
              f"{str(il or '-'):>6} {ib:>9} {str(ji):>3} "
              f"{str(jt or '-'):>8} {str(jl or '-'):>6}  {na} | {nb}")
    return len(rows)


# ------------------------------------------------------------------ #
# CHUNK 2 -- TIER 1: DISTANCE ALONE
# ------------------------------------------------------------------ #

def tier1(cur):
    """
    Pairs within TIER1_METRES. Distance is sufficient at this range.

    Writes to loc_merge_pair, which both tiers append to and CHUNK 4 resolves.
    """
    cur.execute("""DROP TABLE IF EXISTS loc_merge_pair""")
    cur.execute("""CREATE TABLE loc_merge_pair (
                       id_a bigint, rows_a int,
                       id_b bigint, rows_b int,
                       metres real, tier int)""")
    cur.execute(f"""
        INSERT INTO loc_merge_pair (id_a, rows_a, id_b, rows_b, metres, tier)
        SELECT p.id_a, p.rows_a, p.id_b, p.rows_b, p.metres, 1
        FROM loc_pair p
        JOIN loc_attrs xa ON xa.location_id = p.id_a
        JOIN loc_attrs xb ON xb.location_id = p.id_b
        WHERE p.metres <= {TIER1_METRES}
          AND NOT {_attrVetoSql()}
    """)
    print(f"    tier 1 (<= {TIER1_METRES:.0f} m): {cur.rowcount:,} pairs")
    return cur.rowcount


# ------------------------------------------------------------------ #
# CHUNK 3 -- TIER 2: THE ONE-YEAR DEFECTION
# ------------------------------------------------------------------ #

def buildVenueYears(cur):
    """
    venue_years: which years each (meet_name, location_id) was used.

    ★ MATERIALISED ONCE. tier2 needs this set TWICE -- once for the dominant
      id and once for the blip -- and inlining it meant two full passes over
      results_tf (28M rows) in a single statement, with no guarantee Postgres
      shares the work. One table, one scan, then two cheap index reads.

    ★ meets_tf CARRIES AN EVENT DIMENSION, so it must be collapsed to one row
      per (meet_id, div_id) BEFORE joining results_tf -- 658,036 distinct
      pairs span 14.17M rows, a 21.5x fan-out that would multiply every count
      here by roughly twenty.
    """
    cur.execute("DROP TABLE IF EXISTS venue_years")
    cur.execute("""
        CREATE TABLE venue_years AS
        SELECT m.meet_name, m.location_id, count(DISTINCT substr(r.date,1,4)) AS n_years
        FROM  (SELECT meet_id, div_id,
                      min(meet_name)   AS meet_name,
                      min(location_id) AS location_id
               FROM   meets_tf
               WHERE  location_id IS NOT NULL AND meet_name IS NOT NULL
               GROUP  BY 1, 2
               HAVING count(DISTINCT location_id) = 1) m
        JOIN   results_tf r
               ON r.meet_id = m.meet_id AND r.div_id = m.div_id
        WHERE  r.date IS NOT NULL
        GROUP  BY 1, 2
    """)
    cur.execute("CREATE INDEX ON venue_years (meet_name)")
    cur.execute("CREATE INDEX ON venue_years (location_id)")
    cur.execute("ANALYZE venue_years")
    cur.execute("SELECT count(*) FROM venue_years")
    n = cur.fetchone()[0]
    print(f"    {n:,} (meet_name, location) pairs with year counts")
    return n


def tier2(cur):
    """
    A meet name whose location_id defects for a MINORITY of its years while a
    dominant id holds the rest, with the two points within TIER2_METRES.

    ★ THE TEMPORAL SIGNATURE IS THE EVIDENCE, NOT THE DISTANCE. Dublin
      Distance Fiesta is 68937 in 2009-2023 and 2025-2026, and 137743 in 2024
      alone. A venue does not move for one season and move back. Distance here
      only bounds the claim to the same campus; the pattern decides.

    A genuine relocation looks different: the old id stops and the new one
    continues. `d.n_years >= 3 * s.n_years` captures defect-and-return while
    rejecting a clean switch, because after a real move the new id is the
    dominant one going forward.
    """
    cur.execute(f"""
        INSERT INTO loc_merge_pair (id_a, rows_a, id_b, rows_b, metres, tier)
        SELECT DISTINCT p.id_a, p.rows_a, p.id_b, p.rows_b, p.metres, 2
        FROM   loc_pair p
        JOIN   venue_years d ON d.location_id = p.id_a
        JOIN   venue_years s ON s.location_id = p.id_b
                            AND s.meet_name   = d.meet_name
        JOIN   loc_attrs xa ON xa.location_id = p.id_a
        JOIN   loc_attrs xb ON xb.location_id = p.id_b
        WHERE  p.metres >  {TIER1_METRES}
          AND  p.metres <= {TIER2_METRES}
          AND  NOT {_attrVetoSql()}
          -- one id dominates the meet's history, the other is a blip.
          -- greatest/least so the test is symmetric: loc_pair orders by id,
          -- not by which side happens to be dominant.
          AND  greatest(d.n_years, s.n_years) >= 3 * least(d.n_years, s.n_years)
          AND  d.n_years + s.n_years >= {TIER2_MIN_YEARS}
          AND  NOT EXISTS (SELECT 1 FROM loc_merge_pair x
                           WHERE x.id_a = p.id_a AND x.id_b = p.id_b)
    """)
    print(f"    tier 2 (defect-and-return, "
          f"{TIER1_METRES:.0f}-{TIER2_METRES:.0f} m): {cur.rowcount:,} pairs")
    return cur.rowcount


# ------------------------------------------------------------------ #
# CHUNK 4 -- RESOLVE PAIRS INTO GROUPS
# ------------------------------------------------------------------ #

def resolve(cur):
    """
    Pairs -> a canonical id per connected group.

    ★ TRANSITIVE CLOSURE IS REQUIRED, NOT OPTIONAL. If A~B and B~C, all three
      are one venue even when A and C are 90 m apart and never paired
      directly. Merging pairwise without closing would leave A and C in
      different cells and defeat the whole exercise.

    Iterated minimum-label propagation: each id takes the smallest label among
    its neighbours, repeatedly, until nothing changes. Groups here are tiny
    (2-4 ids), so this converges in a handful of passes.

    The canonical id is then the group member with the MOST rows -- the label
    is only a grouping device, not the survivor.
    """
    cur.execute("DROP TABLE IF EXISTS loc_label")
    cur.execute("""CREATE TABLE loc_label AS
                   SELECT location_id, location_id AS label FROM loc_pts""")
    cur.execute("CREATE UNIQUE INDEX ON loc_label (location_id)")

    # ★ THE EDGE LIST IS BUILT ONCE, OUTSIDE THE LOOP. It used to be a
    #   UNION ALL subquery INSIDE the UPDATE, so every one of up to twenty
    #   passes re-scanned loc_merge_pair twice and re-materialised the result
    #   with no index to join against.
    cur.execute("DROP TABLE IF EXISTS loc_edge")
    cur.execute("""CREATE TABLE loc_edge AS
                   SELECT id_a AS x, id_b AS y FROM loc_merge_pair
                   UNION ALL
                   SELECT id_b, id_a FROM loc_merge_pair""")
    cur.execute("CREATE INDEX ON loc_edge (x)")
    cur.execute("ANALYZE loc_edge")

    for i in range(20):
        cur.execute("""
            UPDATE loc_label t
            SET    label = q.newlabel
            FROM (
                SELECT e.x AS location_id, min(n.label) AS newlabel
                FROM   loc_edge e
                JOIN   loc_label n ON n.location_id = e.y
                GROUP  BY e.x
            ) q
            WHERE t.location_id = q.location_id
              AND q.newlabel < t.label
        """)
        if cur.rowcount == 0:
            print(f"    closure converged after {i} passes")
            break
        cur.execute("ANALYZE loc_label")

    cur.execute("DROP TABLE IF EXISTS loc_canon")
    cur.execute("""
        CREATE TABLE loc_canon AS
        SELECT l.location_id,
               first_value(l.location_id)
                 OVER (PARTITION BY l.label ORDER BY p.rows DESC,
                                                     l.location_id)
                 AS canon_id,
               l.label,
               p.rows
        FROM   loc_label l
        JOIN   loc_pts   p ON p.location_id = l.location_id
    """)
    cur.execute("CREATE UNIQUE INDEX ON loc_canon (location_id)")
    cur.execute("ANALYZE loc_canon")

    cur.execute("""SELECT count(*) FILTER (WHERE location_id <> canon_id),
                          count(DISTINCT canon_id)
                                 FILTER (WHERE location_id <> canon_id)
                   FROM loc_canon""")
    moved, groups = cur.fetchone()
    print(f"    {moved:,} ids merge into {groups:,} canonical venues")
    return moved


def report(cur, limit=30):
    """The proposals, biggest first. Read this before writing."""
    cur.execute("""
        SELECT c.canon_id, c.location_id, c.rows,
               (SELECT min(meet_name) FROM meets_tf m
                WHERE m.location_id = c.location_id) AS sample_name
        FROM   loc_canon c
        WHERE  c.location_id <> c.canon_id
        ORDER  BY c.rows DESC
        LIMIT  %s
    """, (limit,))
    print(f"\n    {'canon':>10} {'merged':>10} {'rows':>8}  meet")
    for canon, loc, rows, name in cur.fetchall():
        print(f"    {canon:>10} {loc:>10} {rows:>8}  {(name or '?')[:46]}")


# ------------------------------------------------------------------ #
# CHUNK 5 -- WRITE
# ------------------------------------------------------------------ #

def write(cur):
    """
    Back up, then rewrite meets_tf.location_id to the canonical id.

    ⚠ THE BACKUP HOLDS THE ORIGINAL VALUE AND IS TAKEN BEFORE THE UPDATE, so
      rollback is one statement and does not depend on this script or on
      loc_canon surviving.
    """
    cur.execute("DROP TABLE IF EXISTS meets_tf_location_bak")
    cur.execute("""
        CREATE TABLE meets_tf_location_bak AS
        SELECT m.meet_id, m.div_id, m.event_id, m.location_id
        FROM   meets_tf m
        JOIN   loc_canon c ON c.location_id = m.location_id
        WHERE  c.location_id <> c.canon_id
    """)
    cur.execute("SELECT count(*) FROM meets_tf_location_bak")
    print(f"    backed up {cur.fetchone()[0]:,} rows")

    cur.execute("""
        UPDATE meets_tf m
        SET    location_id = c.canon_id
        FROM   loc_canon c
        WHERE  c.location_id = m.location_id
          AND  c.location_id <> c.canon_id
    """)
    print(f"    {cur.rowcount:,} meets_tf rows repointed")
    print("    ROLLBACK:\n"
          "      UPDATE meets_tf m SET location_id = b.location_id\n"
          "      FROM meets_tf_location_bak b\n"
          "      WHERE m.meet_id = b.meet_id AND m.div_id = b.div_id\n"
          "        AND m.event_id IS NOT DISTINCT FROM b.event_id;")


# ------------------------------------------------------------------ #
# CHUNK 6 -- ENTRY POINT
# ------------------------------------------------------------------ #

def main(live=False, skip_tier2=False):
    from database import getConn

    with getConn() as conn:
        with conn.cursor() as cur:
            print("[loc] building point table...")
            buildPoints(cur)
            buildAttrs(cur)
            buildExpanded(cur)
            buildPairs(cur)

            print("\n[loc] finding duplicate venues...")
            tier1(cur)
            if not skip_tier2:
                buildVenueYears(cur)
                tier2(cur)
            cur.execute("CREATE INDEX ON loc_merge_pair (id_a, id_b)")
            cur.execute("ANALYZE loc_merge_pair")

            heldPairs(cur)

            print("\n[loc] resolving groups...")
            resolve(cur)
            report(cur)

            if live:
                print("\n[loc] writing...")
                write(cur)
                conn.commit()
                print("\n[loc] WRITTEN. Re-solve: the TF difficulty cell key "
                      "is TF:loc:<location_id>, so every merged venue's cell "
                      "changes.")
            else:
                conn.rollback()
                print("\n[loc] DRY RUN -- pass --write to apply")


if __name__ == "__main__":
    main(live="--write" in sys.argv,
         skip_tier2="--tier1-only" in sys.argv)