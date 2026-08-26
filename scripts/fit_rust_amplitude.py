# Project: xc-predictor / scripts
# File:    fit_rust_amplitude.py
# Purpose: Re-measure the season-form amplitude per pool -- the number
#          rust_fitness._AMPLITUDE_AT_100 carries as v1 guesses for every
#          pool except hs_m.
#
#     python scripts/fit_rust_amplitude.py
#
# ★ WHY TODAY (Cabell Midland, 2026-08-27). check_phase_bias measured season
#   phase leaking into XC difficulty (Aug median +0.033 -> Oct +0.020), and
#   the MS-opener cells at one venue carry +0.07..+0.15. rust_fitness's own
#   comments predict this: hs_m's amplitude was RE-MEASURED in v2 and found
#   2.4x bigger than v1; every other pool still carries its v1 value with a
#   note that "the likely truth is that they are all too small" and an
#   instruction to re-run the week table per pool. This is that re-run,
#   rebuilt from the documented method:
#
#     residual = ln(normalized_time) demeaned within venue x distance,
#     bucketed by floor(days since the athlete's season opener / 7),
#     amplitude = decline from the week-1 mean to the week-8 mean.
#
# ★ THE BUILT-IN CREDIBILITY CHECK: hs_m is measured alongside. The original
#   estimator got 0.02979 on 9.1M rows (the table in rust_fitness.py). If
#   this reproduction lands near that, its ms/hs_f/college numbers deserve
#   belief; if it does not, fix the estimator before touching the constants.
#
# Read-only; report only. Board rows only (ranking_results), so wheelchair,
# out-of-band and corrected-division rows never contaminate the fit.

import sys
import time

sys.path.insert(0, "scripts")

from database import getConn                          # noqa: E402

_POOLS = ("hs_m", "hs_f", "ms_m", "ms_f", "college_m", "college_f")

# rust_fitness._AMPLITUDE_AT_100 today, for the comparison column.
_CURRENT = {"hs_m": 0.0295, "hs_f": 0.01458, "ms_m": 0.03402,
            "ms_f": 0.03564, "college_m": 0.00648, "college_f": 0.00648}

_SQL = """
SET work_mem = '2GB';
WITH rows AS (
    SELECT rr.pool,
           rr.person_id,
           rr.year                                    AS season,
           rr.race_date                               AS d,
           ln(r.normalized_time)                      AS lnt,
           -- the CELL, same identity the difficulty solve uses: place and
           -- snapped distance together
           COALESCE(m.course_name, mt.venue_name) || ':' ||
             (round(COALESCE(rr.distance, 0) / 100.0) * 100)::int AS cell
    FROM   ranking_results rr
    JOIN   results r  ON r.result_id = rr.result_id
    LEFT   JOIN meets m ON m.meet_id = rr.meet_id AND m.div_id = rr.div_id
    LEFT   JOIN meets_tfrrs mt ON mt.meet_id = rr.meet_id
                              AND mt.sport = 'XC'
    WHERE  rr.sport = 'XC'
      AND  rr.pool = ANY(%(pools)s)
      AND  r.normalized_time > 0
      AND  rr.race_date IS NOT NULL
), cellmean AS (
    -- ⚠ PER (cell, POOL). The first run demeaned within the cell alone and
    --   the credibility check refused it: hs_m read 0.0549 against the
    --   original 0.0295, with residual LEVELS of -0.06 (hs_m) and +0.14
    --   (hs_f) -- pool-vs-cellmates offsets, because boys, girls and MS
    --   share cells. The original was run on one pool at a time, so its
    --   cell means were per-pool by construction; this partition is that,
    --   in the all-pools pass.
    SELECT *, avg(lnt) OVER (PARTITION BY cell, pool)  AS cm,
              count(*) OVER (PARTITION BY cell, pool)  AS cn
    FROM   rows
), opener AS (
    SELECT *, min(d) OVER (PARTITION BY person_id, season) AS o
    FROM   cellmean
    WHERE  cn >= 8 AND cell IS NOT NULL
)
SELECT pool,
       floor((d - o) / 7.0)::int                       AS wk,
       count(*)                                       AS n,
       avg(lnt - cm)                                  AS resid
FROM   opener
WHERE  (d - o) BETWEEN 0 AND 97
GROUP  BY pool, 2
ORDER  BY pool, 2
"""


def main():
    t0 = time.time()
    with getConn() as conn, conn.cursor() as cur:
        cur.execute(_SQL, {"pools": list(_POOLS)})
        table = cur.fetchall()

    by_pool = {}
    for pool, wk, n, resid in table:
        by_pool.setdefault(pool, {})[int(wk)] = (int(n), float(resid))

    print("\n  mean ln-residual by week since the athlete's season opener,"
          "\n  demeaned within venue x distance (cells with >= 8 rows)."
          "\n  amplitude = wk1 mean - wk8 mean, the quantity"
          " _AMPLITUDE_AT_100 stores.\n")
    print(f"    {'pool':<11}{'wk1 n':>10}{'wk8 n':>10}{'wk1':>10}"
          f"{'wk8':>10}{'AMPLITUDE':>11}{'current':>9}{'ratio':>7}")
    print("    " + "-" * 68)
    for pool in _POOLS:
        wks = by_pool.get(pool, {})
        if 1 not in wks or 8 not in wks:
            print(f"    {pool:<11}  too little data (weeks present: "
                  f"{sorted(wks)})")
            continue
        n1, r1 = wks[1]
        n8, r8 = wks[8]
        amp = r1 - r8
        cur_v = _CURRENT.get(pool, float("nan"))
        print(f"    {pool:<11}{n1:>10,}{n8:>10,}{r1:>+10.5f}{r8:>+10.5f}"
              f"{amp:>+11.5f}{cur_v:>9.4f}{amp / cur_v:>7.2f}")
    print("\n  full week tables (wk: n, resid):")
    for pool in _POOLS:
        wks = by_pool.get(pool, {})
        if not wks:
            continue
        line = "  ".join(f"w{w}:{wks[w][1]:+.4f}" for w in sorted(wks)
                         if 0 <= w <= 10)
        print(f"    {pool:<11}{line}")
    print(f"\n  READ: hs_m's AMPLITUDE should land near 0.0295 -- that is "
          "the estimator's own\n  credibility check. If it does, the other "
          "pools' measured values replace the\n  v1 carry-overs in "
          "rust_fitness._AMPLITUDE_AT_100 (paste this output back).")
    print(f"  done in {(time.time() - t0) / 60:.1f} min")


if __name__ == "__main__":
    main()
