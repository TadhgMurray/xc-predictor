# Project: xc-predictor
# Author:  Tadhg Murray
# File:    engine/component_check.py
# Purpose: find difficulty cells whose delta is UNIDENTIFIABLE -- cells in a
#          connected component that never links to a well-measured one.
#
#   THE FAILURE
#   -----------
#   Joel Harris, SAISD, grade 8:
#
#       2024-02-03  Mission Concepcion Park  3218 m  11:36.9  rating 283.6
#       2024-01-27  MLK Park                 2414 m   9:07.4  rating 212.0
#       2025-02-27  Somerset Bulldog Relays   800 m    2:25.09 rating 103.7
#       2026-03-26  Randolph Ro-Hawk Relays   800 m    2:51.55 rating  88.8
#
#   11:36.9 for 3218 m is 5:48/mile. His TF ratings say 89-104. The XC ratings
#   are not slightly high, they are three times too high -- and the whole field
#   at both meets is inflated the same way, because it is a property of the
#   CELL, not of the runners.
#
#   ★ THE CAUSE IS ALGEBRAIC, NOT ARITHMETIC. The engine fits
#
#         observed_time  ~  ability x (1 + delta_course)
#
#     Two unknowns per observation. Adding a constant to every log-delta in a
#     component and subtracting it from every log-ability in that component
#     leaves EVERY residual unchanged. The fit cannot tell "hard course, fast
#     field" from "easy course, slow field" -- they are the same solution.
#
#     Normally that freedom is removed by LINKAGE: athletes who also race at
#     anchored venues pin their own ability, and delta is what is left over.
#     Mission Concepcion and MLK Park are two SAISD middle-school meets a week
#     apart, run by the same athletes, who race nowhere else in XC. They form
#     a CLOSED COMPONENT with no path to an anchor, so the scale is free and
#     the solver picked delta = +1.18 and +0.735.
#
#   ★ WHY SHRINKAGE DID NOT CATCH IT. Shrinkage keyed on row count sees 456
#     runners and reads abundant evidence. But 456 runners in ONE race is one
#     observation ABOUT THE COURSE, not 456. The quantity that matters is how
#     many independent, anchored paths reach the cell -- which is what this
#     script measures.
#
#   ⚠ THIS SCRIPT ONLY MEASURES. Forcing delta = 0 on an unanchored component
#     is a solver change; the point here is to size the problem and name the
#     cells first, because a fix applied to 144 cells covering 18,375 rows is
#     a very different decision from one touching thousands.

import os
import sys
from collections import defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.dirname(_HERE),
           os.path.join(os.path.dirname(_HERE), "scripts")):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)


# ------------------------------------------------------------------ #
#  CONSTANTS
# ------------------------------------------------------------------ #

# A cell is "well measured" -- an anchor for identification purposes -- when
# it has this many race days. Not rows: a single race day is one observation
# about the course however many people ran it.
ANCHOR_MIN_DAYS = 8

# A component is reported when its largest cell has fewer than this many days,
# i.e. nothing in it is well measured.
REPORT_MAX_DAYS = 4

# Cells below this row count are ignored entirely -- they are already shrunk
# to nothing and cannot move a leaderboard.
MIN_ROWS = 20


# ------------------------------------------------------------------ #
# CHUNK 1 -- UNION-FIND
# ------------------------------------------------------------------ #

class DSU:
    """
    Disjoint-set over cells, linked by shared athlete-seasons.

    ★ PATH COMPRESSION AND UNION BY SIZE. Without both, a long chain of meets
      degrades to O(n) per find and the pass over ~30M rows stops finishing.
      With them it is effectively linear.
    """

    def __init__(self):
        self.parent = {}
        self.size = {}

    def add(self, x):
        if x not in self.parent:
            self.parent[x] = x
            self.size[x] = 1

    def find(self, x):
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[x] != root:          # compress
            self.parent[x], x = root, self.parent[x]
        return root

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        if self.size[ra] < self.size[rb]:      # union by size
            ra, rb = rb, ra
        self.parent[rb] = ra
        self.size[ra] += self.size[rb]


# ------------------------------------------------------------------ #
# CHUNK 2 -- BUILD THE GRAPH
# ------------------------------------------------------------------ #

# ------------------------------------------------------------------ #
#  THE EDGE SOURCE -- BOTH SOURCES, NOT JUST anet
# ------------------------------------------------------------------ #
#
# ⚠ THE ORIGINAL JOIN WAS anet-ONLY AND SILENTLY SO. `meets` holds 812,055
#   anet rows and ZERO tfrrs, so every tfrrs XC result was invisible to the
#   graph. That biases this script in the worst direction: linkage that exists
#   only through tfrrs races looks absent, so components look MORE closed than
#   they are and the report over-counts unanchored cells.
#
# ⚠ AND THE anet JOIN WAS MISSING `source`. anet and tfrrs share div_id with
#   different meanings, so without it a row could match an unrelated meet --
#   the same bug panels.py's XC query documents. It is added here.
#
# ★ THE TFRRS DISTANCE IS PER DIVISION, IN JSONB -- mt.distance IS ALWAYS NULL.
#   Measured: 689,555 rated tfrrs XC rows, ALL of them with a null scalar
#   distance. A first attempt here filtered on `t.distance > 0` and silently
#   matched nothing, which looked exactly like "tfrrs has no XC data".
#
#   division_distances is keyed on the DIVISION ORDINAL as text -- "0", "1",
#   "2" -- and results.div_id is that ordinal. This is not a guess: it is what
#   speed_ratings_db._xcQuery already does in production,
#       (mt.division_distances -> r.div_id::text ->> 'distance')::real
#   and its comment records what reading the scalar instead cost -- a women's
#   5000 and a men's 8000 at one meet sharing one distance, the women's times
#   normalising as if run over 8k, and twelve college_f athletes landing above
#   every real performance in the corpus.
#
# ⚠ THE CELL KEY HERE IS AN APPROXIMATION OF THE ENGINE'S, AND ALWAYS WAS.
#   The engine keys venues on course_canonical.canonical_id where it has one,
#   falling back to the name; this script keys on the name alone, for both
#   sources. So two names that canonicalise to one venue look like two cells
#   here and one there. That makes this report CONSERVATIVE -- it can split a
#   component the solver treats as joined, never merge one it treats as
#   separate -- so an unanchored component reported here is real, and some
#   real ones may be missed.
_EDGE_UNION = """
            SELECT r.person_id,
                   CASE WHEN substr(r.date,6,2)::int >= 7
                        THEN substr(r.date,1,4)::int
                        ELSE substr(r.date,1,4)::int - 1 END AS ay,
                   r.date                                    AS race_date,
                   m.course_name,
                   (round(m.distance / 100.0) * 100)::int     AS dist
            FROM   results r
            JOIN   meets m ON m.meet_id = r.meet_id
                          AND m.div_id  = r.div_id
                          AND m.source  = r.source
            WHERE  r.speed_rating IS NOT NULL
              AND  r.person_id IS NOT NULL
              AND  m.course_name IS NOT NULL
              AND  m.distance > 0
              AND  r.date IS NOT NULL

            UNION ALL

            SELECT r.person_id,
                   CASE WHEN substr(r.date,6,2)::int >= 7
                        THEN substr(r.date,1,4)::int
                        ELSE substr(r.date,1,4)::int - 1 END AS ay,
                   r.date                                    AS race_date,
                   t.venue_name                              AS course_name,
                   (round(COALESCE(
                        (t.division_distances -> r.div_id::text
                           ->> 'distance')::real,
                        t.distance) / 100.0) * 100)::int       AS dist
            FROM   results r
            JOIN   meets_tfrrs t ON t.meet_id = r.meet_id
                                AND t.sport   = 'XC'
            WHERE  r.speed_rating IS NOT NULL
              AND  r.person_id IS NOT NULL
              AND  r.source = 'tfrrs'
              AND  t.venue_name IS NOT NULL
              AND  r.date IS NOT NULL
              AND  COALESCE(
                     (t.division_distances -> r.div_id::text
                        ->> 'distance')::real,
                     t.distance) > 0
"""

def loadEdges(cur):
    """
    Every (athlete-season, cell) pair, ordered so one pass builds the graph.

    ★ THE GROUPING UNIT IS THE ATHLETE-SEASON, matching the engine: it solves
      for (person_id, pool, year), so two cells are linked when one
      athlete-SEASON appears in both. Linking on the person across their whole
      career would connect a middle schooler's cells to their college ones and
      claim an identification the solver never had.

    Ordered by (person, year) so cells for one athlete-season arrive together
    and can be unioned without holding the whole map in memory.
    """
    cur.execute(f"""
        WITH edge AS (
{_EDGE_UNION}
        )
        SELECT person_id, ay, course_name, dist
        FROM   edge
        ORDER  BY person_id, ay
    """)
    return cur


def buildComponents(cur):
    """One streaming pass: union every cell an athlete-season touches."""
    dsu = DSU()
    cur_key, bucket, n = None, [], 0

    for person_id, ay, course, dist in loadEdges(cur):
        cell = (course, dist)
        dsu.add(cell)
        key = (person_id, ay)
        if key != cur_key:
            bucket, cur_key = [], key
        if bucket:
            dsu.union(bucket[0], cell)
        else:
            bucket.append(cell)
        n += 1
        if n % 5_000_000 == 0:
            print(f"\r    {n:,} rows", end="", flush=True)
    print(f"\r    {n:,} rows, {len(dsu.parent):,} cells")
    return dsu


# ------------------------------------------------------------------ #
# CHUNK 3 -- CELL FACTS
# ------------------------------------------------------------------ #

def loadCells(cur):
    """
    {(course, dist): (rows, days, difficulty)}.

    `days` is the identification-relevant count -- see ANCHOR_MIN_DAYS.
    """
    cur.execute(f"""
        WITH edge AS (
{_EDGE_UNION}
        )
        SELECT course_name, dist, count(*), count(DISTINCT race_date)
        FROM   edge
        GROUP  BY 1, 2
    """)
    cells = {(c, d): [n, days, None] for c, d, n, days in cur}

    cur.execute("""
        SELECT course_name, distance_m, difficulty
        FROM   course_difficulties
        WHERE  course_name LIKE 'XC:%'
    """)
    for name, dist, diff in cur:
        key = (name[3:], int(dist) if dist else None)
        if key in cells:
            cells[key][2] = float(diff) if diff is not None else None
    return cells


# ------------------------------------------------------------------ #
# CHUNK 4 -- REPORT
# ------------------------------------------------------------------ #

def report(dsu, cells, limit=30):
    """
    Components whose best cell is poorly measured -- delta there is free.
    """
    comps = defaultdict(list)
    for cell in cells:
        if cell in dsu.parent:
            comps[dsu.find(cell)].append(cell)

    bad = []
    for root, members in comps.items():
        best_days = max(cells[c][1] for c in members)
        if best_days >= REPORT_MAX_DAYS:
            continue
        rows = sum(cells[c][0] for c in members)
        if rows < MIN_ROWS:
            continue
        worst = max((abs(cells[c][2] or 0), c) for c in members)[1]
        bad.append((rows, best_days, len(members), worst, members))

    bad.sort(reverse=True)
    tot_rows = sum(b[0] for b in bad)
    print(f"\n[comp] {len(bad):,} unanchored components covering "
          f"{tot_rows:,} rated rows")
    print(f"       (best cell in each has < {REPORT_MAX_DAYS} race days, so "
          f"nothing in it pins the scale)")
    print(f"\n    {'rows':>8}{'days':>6}{'cells':>7}{'delta':>9}  worst cell")
    for rows, days, ncells, worst, members in bad[:limit]:
        d = cells[worst][2]
        ds = f"{d:+.3f}" if d is not None else "   -  "
        print(f"    {rows:>8}{days:>6}{ncells:>7}{ds:>9}  "
              f"{worst[0][:38]} {worst[1]}")
        if ncells > 1:
            for c in sorted(members)[:4]:
                if c != worst:
                    print(f"    {'':>30}  + {c[0][:38]} {c[1]}")
    return bad


# ------------------------------------------------------------------ #
# CHUNK 4b -- SATELLITES
# ------------------------------------------------------------------ #

def reportSatellites(dsu, cells, limit=25):
    """Components that never connect to the MAIN one.

    ★ A DIFFERENT PATHOLOGY FROM report() ABOVE, WITH THE SAME CAUSE.
      report() asks "is anything in this component well measured", which finds
      tiny islands like Mission Concepcion -- two meets, 456 runners, one race
      day each. It does NOT find a large closed circuit: the DoDEA Far East
      schools race dozens of venues over many race days, so every cell looks
      abundantly measured and the component passes that test comfortably.

      But a big satellite has exactly the same algebraic freedom as a small
      island. Adding a constant to every delta inside it and subtracting it
      from every ability inside it leaves every residual unchanged, because
      nothing crosses the boundary. Size is not anchoring; CONNECTION is.

      So this asks the other question: does this component touch the main one?
      Mission Concepcion is a tiny island, DoDEA is a large moon -- both are
      unanchored, and only this test sees the second.

    ⚠ MEASURES ONLY. Forcing delta = 0 on a satellite is a solver change, and
      the point of printing the row count is to size that decision before
      making it.
    """
    comps = defaultdict(list)
    for cell in cells:
        if cell in dsu.parent:
            comps[dsu.find(cell)].append(cell)

    sized = [(sum(cells[c][0] for c in members), root, members)
             for root, members in comps.items()]
    sized.sort(reverse=True)
    if not sized:
        print("\n[comp] no components found")
        return []

    main_rows, main_root, main_members = sized[0]
    total = sum(s[0] for s in sized)
    print(f"\n[comp] main component: {main_rows:,} rated rows "
          f"({main_rows / total:.1%} of all), {len(main_members):,} cells")

    sats = [s for s in sized[1:] if s[0] >= MIN_ROWS]
    sat_rows = sum(s[0] for s in sats)
    print(f"[comp] {len(sats):,} satellite components covering {sat_rows:,} "
          f"rows ({sat_rows / total:.1%})")
    print("       Nothing in a satellite links to the main component, so its")
    print("       whole delta scale is free -- however well measured it looks.")

    print(f"\n    {'rows':>9}{'cells':>7}{'days':>6}  largest cell")
    for rows, root, members in sats[:limit]:
        best_days = max(cells[c][1] for c in members)
        biggest = max((cells[c][0], c) for c in members)[1]
        print(f"    {rows:>9,}{len(members):>7}{best_days:>6}  "
              f"{biggest[0][:44]} {biggest[1]}")
    return sats


def main():
    from database import getConn
    with getConn() as conn:
        with conn.cursor() as cur:
            print("[comp] loading cell facts...")
            cells = loadCells(cur)
            print(f"    {len(cells):,} cells")
            print("[comp] building components from athlete-season linkage...")
            dsu = buildComponents(cur)
            report(dsu, cells)
            reportSatellites(dsu, cells)
    print("\n[comp] A cell here has no path to a well-measured one, so its")
    print("       delta and its athletes' abilities are mathematically")
    print("       interchangeable. Forcing delta = 0 for these components is")
    print("       the principled fix -- it does not lose information, because")
    print("       there was none.")


if __name__ == "__main__":
    main()