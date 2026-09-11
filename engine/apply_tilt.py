# Project: xc-predictor
# Author:  Tadhg Murray
# File:    engine/apply_tilt.py
# Purpose: apply the ability tilt to results.speed_rating, so EVERY consumer
#          sees it -- athlete pages, race pages, the API -- not just the
#          leaderboards that happen to import tilt.py.
#
#   THE MEASUREMENT (see racecast/tilt.py)
#   --------------------------------------
#   A course does not slow every runner by the same factor. Mean residual by
#   (cell difficulty x athlete rating) over 51M rows tilts systematically, and
#   the tilt tracks difficulty at corr = -0.993:
#
#       h(rating) = 1 + K * (rating - 100) / 10,     K = -0.031
#       delta_eff = delta * h(rating)
#
#       Steens Mountain  delta +0.4522   140 -> 134.6   -5.41
#       Hydrangea Ranch  delta +0.1204   140 -> 138.1   -1.87
#       Woodbridge       delta -0.0295   140 -> 140.5   +0.53
#
#   Rating 100 is unchanged by construction -- that is the anchor.
#
#   ★ WHY A POST-PASS AND NOT AN ENGINE FLAG. pair_all's --tilt was tried and
#     rejected: it moved elite ratings UP by ~0.2 when the correction should
#     move them down, and the validation had read an untilted cached solve. As
#     a post-pass the arithmetic is visible, K stays tunable without a
#     three-hour pipeline run, and the whole thing reverts in one statement.
#
#   ★ IDEMPOTENT BY CONSTRUCTION. Every run reads from results_rating_pretilt,
#     an immutable snapshot taken the first time, and writes
#     tilt(pretilt_value). It NEVER reads the current speed_rating. Running
#     twice therefore gives the same answer as running once -- which matters,
#     because a tilt applied to an already-tilted rating is a different and
#     meaningless number, and nothing downstream would show that it happened.
#
#   ⚠ AND IT MUST RE-RUN AFTER EVERY SOLVE. pair_all --golive rewrites
#     speed_rating from scratch, so the tilt is wiped and the snapshot is
#     stale. This belongs in the pipeline immediately after --golive, and
#     --refresh drops the old snapshot so it is retaken from the new ratings.

import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.dirname(_HERE),
           os.path.join(os.path.dirname(_HERE), "scripts"),
           os.path.join(os.path.dirname(_HERE), "racecast")):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)


# ------------------------------------------------------------------ #
#  CONSTANTS -- mirrored from racecast/tilt.py
# ------------------------------------------------------------------ #

TILT_K = -0.031

# ⚠ RAILS, NOT A CLAMP AT 140. The tilt was fitted over ratings ~70-140 and
#   the line runs on past that: h = 0.69 at a rating of 200, 0.60 at 229,
#   1.50 at -61, so within any rating that exists these bounds never bind
#   (the joint solver extrapolates the same way, TILT_RATING_LO/HI 40/200).
#   They only stop a garbage rating from inverting the sign of a course.
H_MIN, H_MAX = 0.60, 1.50

# How the join finds a row's cell. The engine stores XC difficulty keyed
# 'XC:' || course_name with the distance snapped to the nearest 100 m.
_DIST_SNAP = 100


# ------------------------------------------------------------------ #
#  THE PHASE CLOCK
# ------------------------------------------------------------------ #
#
# ★ 26 MINUTES WITH NO BREAKDOWN IS NOT A MEASUREMENT. mergeColumn prints its
#   own per-statement timings, which accounted for 165 of the 1,566 seconds
#   this step took on the 2026-08 run. The other 1,400 were spent between
#   four print statements. Same clock as build_ranking_results, same reason.
_PHASES = []


class phase:
    def __init__(self, label):
        self.label = label

    def __enter__(self):
        self.t0 = time.time()
        return self

    def __exit__(self, *exc):
        dt = time.time() - self.t0
        _PHASES.append((self.label, dt))
        print(f"    [{dt:7.1f}s] {self.label}")
        return False


def phaseReport():
    if not _PHASES:
        return
    total = sum(dt for _l, dt in _PHASES)
    print("\n[tilt] WHERE THE TIME WENT")
    for label, dt in _PHASES:
        share = 100.0 * dt / total if total else 0.0
        print(f"    {dt / 60:7.1f} min  {share:5.1f}%  {label}")


# ------------------------------------------------------------------ #
# CHUNK 1 -- THE SNAPSHOT
# ------------------------------------------------------------------ #

# Past this many moved rows, a heap rebuild beats an in-place UPDATE. See the
# note in apply(): 122s of UPDATE buys about half a million rows, and a
# rebuild of this table took 122s for 39M.
REBUILD_ABOVE = 500_000


def ensureSnapshot(cur, refresh=False):
    """
    results_rating_pretilt: (result_id, speed_rating) as the SOLVER wrote it.

    ★ THIS TABLE IS THE IDEMPOTENCE. The update always computes
      tilt(pretilt) -> speed_rating, never tilt(speed_rating), so the number of
      times it has run does not affect the answer.

    ⚠ --refresh IS REQUIRED AFTER EVERY SOLVE and easy to forget. pair_all
      --golive replaces speed_rating wholesale; if the old snapshot survives,
      the next apply would tilt LAST run's ratings onto THIS run's rows. The
      check below refuses to proceed when the snapshot looks stale.
    """
    cur.execute("SELECT to_regclass('public.results_rating_pretilt')")
    exists = cur.fetchone()[0] is not None

    if exists and refresh:
        cur.execute("DROP TABLE results_rating_pretilt")
        exists = False
        print("    dropped the old snapshot (--refresh)")

    if not exists:
        with phase("snapshot CTAS (results -> results_rating_pretilt)"):
            cur.execute("""
                CREATE TABLE results_rating_pretilt AS
                SELECT result_id, speed_rating
                FROM   results
                WHERE  speed_rating IS NOT NULL
            """)
        with phase("snapshot indexes"):
            cur.execute("CREATE UNIQUE INDEX ON "
                        "results_rating_pretilt (result_id)")
            # ! AND ONE ON THE RATING, FOR report(). That query filters
            #   `p.speed_rating >= 130` and then joins the whole of results to
            #   it -- without this the filter is a scan of all 34.6M snapshot
            #   rows on every run. 130+ is a fraction of a percent of them.
            cur.execute("CREATE INDEX ON results_rating_pretilt (speed_rating)")
            cur.execute("ANALYZE results_rating_pretilt")
        cur.execute("SELECT count(*) FROM results_rating_pretilt")
        print(f"    snapshot taken: {cur.fetchone()[0]:,} ratings")
        return True

    # A stale snapshot is the one failure mode that produces silently wrong
    # numbers, so check rather than assume.
    cur.execute("""
        SELECT count(*) FROM results r
        JOIN   results_rating_pretilt p ON p.result_id = r.result_id
        WHERE  r.speed_rating IS NOT NULL
    """)
    covered = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM results WHERE speed_rating IS NOT NULL")
    total = cur.fetchone()[0]
    print(f"    snapshot exists: covers {covered:,} of {total:,} rated rows")
    if covered < total * 0.99:
        print("    ⚠ the snapshot misses >1% of rated rows -- it predates the "
              "current solve.\n      Re-run with --refresh.")
        return False
    return True


# ------------------------------------------------------------------ #
# CHUNK 2 -- APPLY
# ------------------------------------------------------------------ #

def _tiltSql(rating="p.speed_rating", diff="cd.difficulty"):
    """
    The tilt, as SQL.

        h        = clamp(1 + K*(rating-100)/10, H_MIN, H_MAX)
        tilted   = rating * (1 + delta*h) / (1 + delta)

    ★ THE (1+delta) DENOMINATOR IS NOT DECORATIVE. The stored rating already
      has delta divided out of it; multiplying by (1+delta*h)/(1+delta)
      replaces delta with delta*h rather than applying h on top of an
      uncorrected number. Dropping the denominator would double-count the
      course.

    ⚠ GUARDED AGAINST delta = -1. That would be a course infinitely easy and
      the denominator zero; the CASE leaves such a row untouched rather than
      producing an infinity that propagates into a leaderboard.
    """
    return f"""
        CASE
          WHEN {diff} IS NULL THEN {rating}
          WHEN abs(1.0 + {diff}) < 1e-9 THEN {rating}
          ELSE {rating}
               * (1.0 + {diff} * least({H_MAX}, greatest({H_MIN},
                     1.0 + {TILT_K} * ({rating} - 100.0) / 10.0)))
               / (1.0 + {diff})
        END"""


# Rows whose tilt moves them less than this are left alone.
#
# ★ THIS IS THE SPEEDUP, AND IT IS NOT AN APPROXIMATION. The tilt is
#   proportional to BOTH the cell's difficulty and the athlete's distance from
#   100, so the overwhelming majority of rows -- ordinary runners on ordinary
#   courses -- move by hundredths of a point. Rewriting 33.9M rows to change
#   most of them by 0.003 costs a full table rewrite plus index maintenance on
#   every index over speed_rating, which is why the unfiltered UPDATE ran for
#   hours.
#
#   0.05 is well under the display precision (one decimal) and far under the
#   3.3-4.5 point per-athlete week-to-week variation ah measures, so a row
#   below it is unchanged for every purpose anyone has.
MIN_SHIFT = 0.05

# A cell whose |delta| is under this cannot move any plausible rating by
# MIN_SHIFT, so it is skipped before results is touched at all.
#   |shift| ~ |delta| * 0.031 * (rating-100)/10 / (1+delta)
# At delta = 0.02 and rating 140 that is 0.024 -- under MIN_SHIFT. At 0.04 it
# is 0.047. So 0.03 is the point below which nothing can qualify.
DELTA_FLOOR = 0.03


def apply(cur, conn):
    """
    Rewrite results.speed_rating as tilt(pretilt_rating).

    ★ THE AFFECTED ROWS ARE COMPUTED ONCE, THEN UPDATED. An earlier version
      batched the UPDATE by result_id range, which did not help at all: the
      planner drove the join from meets (812,079 rows) down into results via
      an index scan per meet-division, and applied the result_id filter LAST.
      Every batch therefore did the whole job and threw 95% away.

      Building the (result_id, tilted) pairs into a temp table first turns the
      work into one pass plus one indexed update -- and because MIN_SHIFT
      removes the rows that barely move, that table is small.

    ★ ONLY CELLS THAT CAN MOVE ANYTHING ARE CONSIDERED. The tilt scales with
      delta x (rating - 100), so a cell with |delta| under DELTA_FLOOR cannot
      shift any plausible rating by MIN_SHIFT. Filtering cells before touching
      results is what makes this cheap: ~150 cells matter, not 73,000.

    ★ XC ONLY. TF difficulty is keyed on location_id, not course_name, so TF
      rows join nothing and are left alone. A known gap, not an oversight.
    """
    cur.execute("DROP TABLE IF EXISTS tilt_new")
    _t0 = time.time()
    cur.execute(f"""
        CREATE TEMP TABLE tilt_new AS
        WITH cell AS (
            -- The only cells whose tilt can reach MIN_SHIFT at any sane
            -- rating. |shift| ~ |delta| * |K| * (rating-100)/10 / (1+delta),
            -- so below this floor nothing moves enough to store.
            SELECT course_name, distance_m, difficulty
            FROM   course_difficulties
            WHERE  course_name LIKE 'XC:%'
              AND  difficulty IS NOT NULL
              AND  abs(difficulty) >= {DELTA_FLOOR}
        ),
        div AS (
            -- ★ ONE ROW PER (meet_id, div_id), NOT ONE PER meets ROW.
            --   meets is not unique on (meet_id, div_id) -- a division can
            --   carry several rows -- so joining it straight to cell yields
            --   the same result_id more than once, with possibly DIFFERENT
            --   difficulties. That surfaced as a duplicate-key error building
            --   the index, which is the good outcome: silently keeping an
            --   arbitrary one would have retilted rows against whichever
            --   difficulty the planner happened to emit first.
            --
            --   min() picks deterministically. A division whose rows disagree
            --   on difficulty is a cell-key problem in its own right and is
            --   counted below rather than hidden.
            SELECT m.meet_id, m.div_id,
                   min(c.difficulty)               AS difficulty,
                   count(DISTINCT c.difficulty)    AS n_diff
            FROM   meets m
            JOIN   cell c
                   ON c.course_name = 'XC:' || m.course_name
                  AND c.distance_m  =
                      (round(m.distance / {_DIST_SNAP}.0)
                       * {_DIST_SNAP})::int
            GROUP  BY 1, 2
        )
        SELECT p.result_id,
               {_tiltSql('p.speed_rating', 'd.difficulty')} AS tilted
        FROM   div d
        JOIN   results r ON r.meet_id = d.meet_id AND r.div_id = d.div_id
        JOIN   results_rating_pretilt p ON p.result_id = r.result_id
        WHERE  abs({_tiltSql('p.speed_rating', 'd.difficulty')}
                   - p.speed_rating) >= {MIN_SHIFT}
    """)
    _PHASES.append(("tilt_new CTAS (the join that finds the moved rows)",
                    time.time() - _t0))
    print(f"    [{time.time() - _t0:7.1f}s] tilt_new CTAS")
    # ⚠ THE SAME JOIN KEY AS `div` ABOVE, WHICH IT DID NOT USE. This check
    #   joined on course_name ALONE -- no distance, no DELTA_FLOOR -- so every
    #   distance cell of a venue matched every division at it, and the count it
    #   printed was not the count of divisions min() actually had to choose
    #   between. It reported 693,781 on a run whose `div` CTE saw far fewer,
    #   because a venue with four distance cells "disagreed" with itself four
    #   ways by construction.
    #
    #   Measuring the wrong thing is the whole cost here: a name-only join
    #   fans 812k meets rows across every cell sharing a name, aggregates the
    #   lot, and then reports a number nobody can act on. Mirroring `div`
    #   costs a fraction of that and the number means what the line says.
    _t0 = time.time()
    cur.execute(f"""
        SELECT count(*) FROM (
            SELECT m.meet_id, m.div_id
            FROM   meets m
            JOIN   course_difficulties c
                   ON c.course_name = 'XC:' || m.course_name
                  AND c.distance_m  =
                      (round(m.distance / {_DIST_SNAP}.0)
                       * {_DIST_SNAP})::int
            WHERE  c.course_name LIKE 'XC:%'
              AND  c.difficulty IS NOT NULL
              AND  abs(c.difficulty) >= {DELTA_FLOOR}
            GROUP  BY 1, 2
            HAVING count(DISTINCT c.difficulty) > 1
        ) q""")
    amb = cur.fetchone()[0]
    _PHASES.append(("ambiguity check", time.time() - _t0))
    if amb:
        print(f"    ⚠ {amb:,} meet-divisions map to >1 difficulty at the same "
              f"snapped distance; min() taken")
    with phase("tilt_new index + analyze"):
        cur.execute("CREATE UNIQUE INDEX ON tilt_new (result_id)")
        cur.execute("ANALYZE tilt_new")
    cur.execute("SELECT count(*) FROM tilt_new")
    n = cur.fetchone()[0]
    print(f"    {n:,} rows move by >= {MIN_SHIFT}")
    if not n:
        return 0

    # ! UPDATE OR REBUILD, DECIDED BY THE ROW COUNT, BECAUSE THE CROSSOVER IS
    #   MEASURED. merge_column's own numbers: an UPDATE costs 150-290us per
    #   row on this table, and a full heap rebuild took 122s for 39M rows. So
    #   122s of UPDATE buys roughly half a million rows, and past that the
    #   rebuild is cheaper however many rows it has to copy.
    #
    #   A tilt that moves a few thousand ratings should not rewrite 34M rows;
    #   one that moves ten million should not crawl through them one at a
    #   time. Neither tool is right for both, which is why the threshold is
    #   here rather than a fixed choice.
    #
    # ! AND THE REBUILD PATH MUST PRESERVE UNMATCHED ROWS. tilt_new holds only
    #   the rows that MOVE; every other rating has to survive untouched, so
    #   preserve_unmatched=True is not optional here the way it is for a full
    #   re-solve that stages every row.
    if n >= REBUILD_ABOVE:
        from merge_column import mergeColumn

        # ! DROP THE PREVIOUS MERGE'S LEFTOVER FIRST, because in this pipeline
        #   there is always one. linkage_check --golive runs immediately before
        #   this script and its merge renames results aside to results_old;
        #   mergeColumn then refuses to start because a swap onto an existing
        #   _old would fail after fifteen minutes of rebuilding.
        #
        #   That guard is right in general -- it saved 834 seconds once -- and
        #   wrong here specifically, where the leftover is the expected output
        #   of the step before. Dropping it is safe at this point: --golive
        #   completed, so results holds the merged ratings and results_old is
        #   the pre-merge copy nothing reads.
        #
        # ⚠ ONLY THE TABLE THIS MERGE TOUCHES. results_tf_old is left alone; a
        #   blanket drop here would discard the undo copy for a merge that may
        #   not have finished.
        cur.execute("DROP TABLE IF EXISTS results_old")
        conn.commit()
        print("    dropped results_old (the previous merge's undo copy)")

        print(f"    {n:,} rows is past {REBUILD_ABOVE:,}; rebuilding instead "
              f"of updating in place")
        with phase("mergeColumn (heap rebuild of results)"):
            mergeColumn(conn, "results", "speed_rating", "tilt_new",
                        key="result_id", val="tilted", preserve_unmatched=True)
        print(f"    {n:,} ratings retilted")
        return n

    _t_update = time.time()
    cur.execute("""
        UPDATE results r
        SET    speed_rating = t.tilted
        FROM   tilt_new t
        WHERE  r.result_id = t.result_id
          AND  r.speed_rating IS DISTINCT FROM t.tilted
    """)
    total = cur.rowcount
    conn.commit()
    _PHASES.append(("in-place UPDATE", time.time() - _t_update))
    print(f"    {total:,} ratings retilted")
    return total


def report(cur, limit=12, preview=False):
    """
    Biggest movers among strong ratings, so the change is visible.

    ★ THE DISTANCE IS PART OF THE JOIN KEY. Without it, a venue with four
      distance cells produces four identical rows -- same n, same averages --
      and the "biggest movers" ordering becomes meaningless. course_name alone
      is not the cell.

    `preview` computes what the tilt WOULD do rather than what it did, so a
    dry run shows a real number instead of a column of zeros. After --write
    the two are the same query with the arithmetic already applied.
    """
    shift_expr = (f"avg({_tiltSql()} - p.speed_rating)" if preview
                  else "avg(r.speed_rating - p.speed_rating)")
    now_expr = (_tiltSql().join(("avg(", ")")) if preview
                else "avg(r.speed_rating)")
    cur.execute(f"""
        SELECT m.course_name,
               cd.distance_m,
               round(cd.difficulty::numeric, 4)          AS delta,
               count(*)                                  AS n,
               round(avg(p.speed_rating)::numeric, 1)    AS was,
               round(({now_expr})::numeric, 1)           AS now,
               round(({shift_expr})::numeric, 2)         AS shift
        FROM   results r
        JOIN   results_rating_pretilt p ON p.result_id = r.result_id
        JOIN   meets m ON m.meet_id = r.meet_id AND m.div_id = r.div_id
        JOIN   course_difficulties cd
               ON cd.course_name = 'XC:' || m.course_name
              AND cd.distance_m  =
                  (round(m.distance / {_DIST_SNAP}.0) * {_DIST_SNAP})::int
        WHERE  p.speed_rating >= 130
        GROUP  BY 1, 2, 3
        HAVING count(*) >= 50
        ORDER  BY abs({shift_expr}) DESC
        LIMIT  %s
    """, (limit,))
    label = "would move" if preview else "moved"
    print(f"\n[tilt] biggest movers among 130+ ratings ({label})")
    print(f"    {'delta':>9}{'dist':>7}{'n':>8}{'was':>8}{'now':>8}"
          f"{'shift':>8}  course")
    for course, dist, delta, n, was, now, shift in cur.fetchall():
        print(f"    {delta:>9}{dist:>7}{n:>8}{was:>8}{now:>8}{shift:>8}"
              f"  {course[:38]}")


def revert(cur, conn):
    """
    Put the solver's ratings back.

    Only touches rows that actually differ, which after a MIN_SHIFT-filtered
    apply is the same small set the apply wrote -- not the whole table.
    """
    cur.execute("""
        UPDATE results r
        SET    speed_rating = p.speed_rating
        FROM   results_rating_pretilt p
        WHERE  p.result_id = r.result_id
          AND  r.speed_rating IS DISTINCT FROM p.speed_rating
    """)
    n = cur.rowcount
    conn.commit()
    print(f"    {n:,} ratings restored")


# ------------------------------------------------------------------ #
# CHUNK 3 -- ENTRY POINT
# ------------------------------------------------------------------ #

def main(live=False, refresh=False, undo=False):
    from database import getConn

    with getConn() as conn:
        with conn.cursor() as cur:
            if undo:
                revert(cur, conn)
                return

            print("[tilt] checking the pre-tilt snapshot...")
            if not ensureSnapshot(cur, refresh=refresh):
                conn.rollback()
                return
            conn.commit()

            if not live:
                print("\n[tilt] DRY RUN -- pass --write to apply")
                report(cur, preview=True)
                conn.rollback()
                return

            print("\n[tilt] applying...")
            apply(cur, conn)
            with phase("report (biggest movers)"):
                report(cur)
            phaseReport()
            print("\n[tilt] applied. Re-run panels so the boards match.")
            print("       Undo: python engine\\apply_tilt.py --undo")


if __name__ == "__main__":
    main(live="--write" in sys.argv,
         refresh="--refresh" in sys.argv,
         undo="--undo" in sys.argv)