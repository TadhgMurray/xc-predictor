# Project: xc-predictor / engine
# File:    fill_ratings.py
# Purpose: EVERY result gets a speed rating. Pipeline step 09b.
#
#     python engine/fill_ratings.py              # fill both sports
#     python engine/fill_ratings.py --dry-run    # count and sample, no writes
#
# ★ THE OWNER'S RULE: when the ratings regenerate, everything gets one. The
#   engine's pack keeps a row out of the SOLVE when its normalized_time falls
#   outside the pool's pace band -- correctly, because an impossible row
#   would poison difficulties and pool means -- but "out of the solve" used
#   to also mean "no rating at all", and a race page full of dashes is how
#   the Ox Bow class of wrong distance HID: the worse the label, the fewer
#   rows survived to testify against it, while the tools that hunt wrong
#   distances all keyed on rated rows.
#
#   So after go-live and tilt have written the solve's own ratings, this
#   prices every remaining row on the same scale: rating = K_pool /
#   normalized_time, where K_pool is recovered from the rated corpus itself
#   -- for every board row, speed_rating * normalized_time is the pool's
#   constant (pool_mean * 100, carrying the row's difficulty adjustment),
#   so the per-pool median over ranking_results IS the engine's constant,
#   whatever engine wrote the ratings. Nothing is fit here and no formula
#   is copied; the constant is read back out of the engine's own output.
#
# ⚠ RATED IS NOT RANKED. A filled row is a number the solve refused to stand
#   behind; it appears on the race page and the athlete's page, and NEVER on
#   a board -- build_ranking_results gates on the same pace band the pack
#   used (normalize_distance.PACE_FLOOR/PACE_CEIL, one definition). The
#   payoff for detection: a wrong-distance division now carries a full field
#   of visibly absurd ratings instead of deleting its own evidence.
#
# ! WHAT STILL HAS NO RATING, honestly: rows with no person link, no gender,
#   no parseable date, or a pool nobody can resolve. Those are not priceable
#   on any scale, and the census printed at the end names each count.
#
# Idempotent: only NULL ratings are touched, and each go-live resets the
# column before this refills it, so filled values never go stale.

import argparse
import sys
import time

sys.path.insert(0, "scripts")
sys.path.insert(0, "engine")
sys.path.insert(0, "racecast")

import psycopg2.extras                                    # noqa: E402

from database import getConn                              # noqa: E402
import build_ranking_results as B                         # noqa: E402
from pool_resolve import resolvePool                      # noqa: E402
from season_year import seasonYearFromIso                 # noqa: E402
from speed_ratings_db import saveResultSpeedRatings       # noqa: E402

_TABLE = {"XC": "results", "TF": "results_tf"}

# ★ THE SAME QUERY THE BOARDS BUILD FROM, with one clause inverted: the rows
#   the boards did NOT see. Reused rather than copied so the five facts the
#   pool decision needs can never drift from the build's -- the anchored
#   assert makes a drift a loud failure instead of a silently wrong pool.
# ⚠ ONE MARK PER SPORT SINCE #46. The TF board query admits sprint rows
#   unrated -- WHERE (rated OR sprint) -- so its inverse is "unrated AND NOT
#   a sprint": a sprint has no rating on purpose and must never be priced
#   here. The 2026-09-01 run failed this step on the old single mark.
_MARK = {
    "XC": ("WHERE r.speed_rating IS NOT NULL",
           "WHERE r.speed_rating IS NULL"),
    # ! AND NOT A FIELD EVENT, since the marks board (owner, 2026-09-02): a
    #   field row is admitted for its mark and has no time to price.
    "TF": ("WHERE (r.speed_rating IS NOT NULL OR se.event_short IS NOT NULL\n"
           "               OR COALESCE(r.is_field, 0) = 1)",
           "WHERE r.speed_rating IS NULL AND se.event_short IS NULL\n"
           "               AND COALESCE(r.is_field, 0) = 0"),

}


def _sqlFor(sport):
    sql = B._SQL[sport]
    mark, inverse = _MARK[sport]
    assert mark in sql, (
        f"build_ranking_results._SQL[{sport!r}] no longer carries the WHERE "
        "this file inverts -- update fill_ratings._MARK together with it")
    return sql.replace(mark, inverse)


# Per-pool constant, recovered from the board rows. Sampled on result_id
# (deterministic, engine-independent); pools whose sample is thin get an
# exact second pass below.
_K_SAMPLED = """
    SELECT k.pool,
           percentile_cont(0.5) WITHIN GROUP
               (ORDER BY r.speed_rating * r.normalized_time),
           count(*)
    FROM   ranking_results k
    JOIN   {table} r ON r.result_id = k.result_id
    WHERE  k.sport = %s
      AND  mod(k.result_id, 25) = 0
      AND  r.speed_rating > 0
      AND  r.normalized_time > 0
    GROUP  BY 1
"""

_K_EXACT = """
    SELECT k.pool,
           percentile_cont(0.5) WITHIN GROUP
               (ORDER BY r.speed_rating * r.normalized_time),
           count(*)
    FROM   ranking_results k
    JOIN   {table} r ON r.result_id = k.result_id
    WHERE  k.sport = %s
      AND  k.pool = ANY(%s)
      AND  r.speed_rating > 0
      AND  r.normalized_time > 0
    GROUP  BY 1
"""

# Below this many sampled rows a median is noise; recompute those pools
# exactly (they are small, so the exact pass is cheap by construction).
_MIN_SAMPLE = 500


def poolConstants(conn, sport):
    table = _TABLE[sport]
    with conn.cursor() as cur:
        cur.execute(_K_SAMPLED.format(table=table), (sport,))
        rows = cur.fetchall()
        thin = [p for p, _k, n in rows if n < _MIN_SAMPLE]
        k = {p: float(v) for p, v, n in rows if n >= _MIN_SAMPLE}
        if thin:
            cur.execute(_K_EXACT.format(table=table), (sport, thin))
            for p, v, _n in cur.fetchall():
                k[p] = float(v)
    return k


def _rowPool(row, sport):
    """The board build's pool decision, verbatim -- see buildSport. The
    non-school/DODEA guards are deliberately NOT applied: those exclude rows
    from RANKINGS, and this file only prices rows for display."""
    season = seasonYearFromIso(sport, row.date)
    return resolvePool(row.grade, row.gender, row.source, row.school,
                       sport,
                       season=season,
                       season_level=row.season_level,
                       grade_untrusted=bool(row.grade_untrusted),
                       fixed_grade=row.fixed_grade,
                       fixed_level=row.fixed_level,
                       grade_verdict=row.grade_verdict,
                       person_id=row.person_id,
                       is_pro=bool(row.is_pro),
                       merge=True)


def fillSport(conn, sport, dry_run=False):
    t0 = time.time()
    k_by_pool = poolConstants(conn, sport)
    if not k_by_pool:
        print(f"  {sport}: ranking_results holds no rated rows -- run the "
              "pipeline through 10_rankings once before filling. Skipped.")
        return
    print(f"  {sport}: {len(k_by_pool)} pool constants recovered "
          "(median speed_rating * normalized_time per pool)")

    census = {"filled": 0, "no_pool": 0, "no_constant": 0, "bad_nt": 0}
    samples = []

    def pairs():
        # Its own server-side cursor: saveResultSpeedRatings streams these
        # into COPY on a second connection while this one is still reading.
        with conn.cursor(f"fill_{sport.lower()}_src",
                         cursor_factory=psycopg2.extras.NamedTupleCursor) as src:
            src.itersize = 50_000
            src.execute(_sqlFor(sport), {"since": "1990-01-01"})
            for row in src:
                nt = row.normalized_time
                if nt is None or float(nt) <= 0:
                    census["bad_nt"] += 1
                    continue
                pool = _rowPool(row, sport)
                if pool is None:
                    census["no_pool"] += 1
                    continue
                k = k_by_pool.get(pool)
                if k is None:
                    census["no_constant"] += 1
                    continue
                rating = round(k / float(nt), 2)
                census["filled"] += 1
                if len(samples) < 5:
                    samples.append((row.result_id, pool, float(nt), rating))
                yield (row.result_id, rating)

    if dry_run:
        for _ in pairs():
            pass
        print(f"  {sport}: DRY RUN -- would fill {census['filled']:,} rows")
    else:
        # mode="update": concurrent-safe, touches only the given rows, and
        # leaves no _old table -- exactly what a gap-fill wants. The heap
        # rebuild is for the solve's 30M-row full rewrite, not for this.
        saveResultSpeedRatings(sport, pairs(), mode="update")

    print(f"  {sport}: filled {census['filled']:,}   "
          f"unpriceable: {census['no_pool']:,} no pool, "
          f"{census['no_constant']:,} pool never on a board, "
          f"{census['bad_nt']:,} no normalized time   "
          f"({(time.time() - t0) / 60:.1f} min)")
    for rid, pool, nt, rating in samples:
        print(f"    sample: result {rid}  {pool:<12} nt {nt:7.1f}s "
              f"-> {rating:.1f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="count and sample; write nothing")
    ap.add_argument("--sport", choices=("XC", "TF"),
                    help="one sport only (default both)")
    args = ap.parse_args()

    sports = (args.sport,) if args.sport else ("XC", "TF")
    with getConn() as conn:
        # ★ THE INVARIANT, ENFORCED EVERY RUN (2026-08-27): no normalized
        #   time => no rating. The backfill's merge NULLs nt for skipped
        #   rows (wheelchair, nuked divisions, drops) but carries the old
        #   speed_rating across untouched -- and race pages display that
        #   column directly. Every nuked row kept its pre-nuke rating
        #   through every rebuild until this scrub existed.
        if not args.dry_run:
            with conn.cursor() as cur:
                for t in ("results", "results_tf"):
                    cur.execute(f"UPDATE {t} SET speed_rating = NULL "
                                f"WHERE normalized_time IS NULL "
                                f"AND speed_rating IS NOT NULL")
                    print(f"[fill] {t}: scrubbed {cur.rowcount:,} stale "
                          f"ratings (rating with no normalized_time)")
            conn.commit()
        # The board query's temp tables, prepared exactly as the board build
        # prepares them -- the reused SQL joins them per sport.
        B.ensureResultTwin(conn)       # the boards' WHERE anti-joins it
        # ★ AND THE CHAIR ATHLETES, BY PERSON (2026-09-03). The engine refused
        #   every row of theirs, so every row of theirs arrived here NULL and
        #   was priced flat -- a racing chair's 5K at K / normalized_time is
        #   a 140-something on a running scale. The anti-join lives in the
        #   boards' WHERE, which this file inverts, so it holds here too.
        B.ensureWheelchairPerson(conn)
        B.prepareGenderTemp(conn)
        if "XC" in sports:
            B.prepareXcTfrrsDistTemp(conn)
        if "TF" in sports:
            B.prepareTfStateTemp(conn)
            # The sprint whitelist the TF query joins (#46); without it the
            # query fails on a missing temp table.
            B.prepareSprintEvents(conn)
        for sport in sports:
            fillSport(conn, sport, dry_run=args.dry_run)
    print("fill_ratings done.")


if __name__ == "__main__":
    main()
