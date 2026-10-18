#!/usr/bin/env python3
"""
diag_altitude_credit.py -- is the altitude credit the right size, for
visitors and for residents? READ ONLY, on the published ratings.

    set -a; . /etc/xc-predictor.env; set +a
    /srv/venv/bin/python scripts/diag_altitude_credit.py
    /srv/venv/bin/python scripts/diag_altitude_credit.py --since 2018 --sample 5

★ WHY (owner, 2026-10-18: "altitude: chosen numbers out of ass, especially
  half credit"). The solve holds the altitude coefficient at 3.5% per km
  above 600 m (joint_solve.ALT_PRIOR_MEAN, penalty 1e9: fixed) and gives a
  resident field half of it (ALT_ACCLIM = 0.5). Neither is fitted. The
  published ratings can say whether they are right, the way
  diag_course_credit does for the course and the day:

      inside one runner's season,
      ln(rating) = level + b_alt * venue km above 600 m
                   + b_course * course + b_day * day + b_date * days in

  b_alt = 0: the credit is right for this group. b_alt > 0: races at
  altitude rate HIGH (over-credited); < 0: under-credited.

  Split by the runner-season's HOME km (median venue km of their races, as
  the solve defines it):
    visitors   home < 0.3 km  -- b_alt tests the coefficient itself
    residents  home >= 0.7 km -- b_alt tests the acclimatisation share
  and, for visitors, by how much of the field at that race was resident,
  because the rating gives a race venue km minus half the FIELD's mean home
  km: a visitor at a resident-heavy race gets less credit by design.

  The suggested numbers at the end are what would zero each b, assuming
  the published credit is linear in venue km (it is, by construction).
"""
import argparse
import os
import sys
import time

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))

import diag_course_credit as cc                                  # noqa: E402

ALT_FLOOR_M = 600.0
K_NOW = 0.035
ACCLIM_NOW = 0.5


def homeKm(grp, km):
    """Per row, the median venue km of the row's runner-season. Pure."""
    grp = np.asarray(grp)
    km = np.asarray(km, dtype=float)
    out = np.empty_like(km)
    order = np.argsort(grp, kind="stable")
    g, k = grp[order], km[order]
    cuts = np.flatnonzero(np.diff(g)) + 1
    for a, b in zip(np.r_[0, cuts], np.r_[cuts, g.size]):
        out[order[a:b]] = np.median(k[a:b])
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", type=int, default=2019)
    ap.add_argument("--sample", type=int, default=5, help="1 runner in N")
    ap.add_argument("--pool")
    a = ap.parse_args()
    from database import getConn
    t0 = time.time()
    print(f"reading XC races since {a.since}, 1 runner in {a.sample} ...", flush=True)
    with getConn() as conn, conn.cursor() as cur:
        cur.execute("SET LOCAL statement_timeout = '10min'")
        rows = cc.load(cur, a.since, a.sample, a.pool, extra=True,
                       say=lambda m: print(f"{m}  ({time.time() - t0:.0f}s)", flush=True))
        cur.execute("SELECT key, elevation_m FROM venue_elevation WHERE key LIKE 'XC:%%'")
        elev = {int(k[3:]): float(e) for k, e in cur.fetchall() if k[3:].isdigit()}
        conn.rollback()
    keep = [r for r in rows if r[7] in elev]
    print(f"  {len(keep):,} of {len(rows):,} races at a venue with a known elevation")
    if not keep:
        return
    pid = np.array([r[0] for r in keep], dtype=np.int64)
    pool = np.array([r[1] for r in keep])
    yr = np.array([r[2] for r in keep], dtype=np.int64)
    y = np.array([r[3] for r in keep])
    course = np.array([r[4] for r in keep])
    day = np.array([r[5] for r in keep])
    din = np.array([r[6] for r in keep]) / 100.0
    km = np.array([max(elev[r[7]] - ALT_FLOOR_M, 0.0) / 1000.0 for r in keep])
    race = np.array([hash(r[8]) for r in keep])
    _, grp = np.unique(np.char.add(np.char.add(pid.astype(str), pool), yr.astype(str)),
                       return_inverse=True)
    home = homeKm(grp, km)
    # the field's mean home km at each race, from the sampled runners in it
    _, rinv = np.unique(race, return_inverse=True)
    n_r = np.bincount(rinv)
    field = (np.bincount(rinv, weights=home) / n_r)[rinv]
    X = np.column_stack([km, course, day, din])
    alt_rows = km > 0.3
    print(f"  {int(alt_rows.sum()):,} races at 900 m or higher; "
          f"{int((home >= 0.7).sum()):,} rows of resident runner-seasons\n")
    print("b_alt = change in ln(rating) per km of venue altitude above 600 m, inside one "
          "runner's season.\n0 = credit right; + = altitude races rate HIGH (over-credited)\n")
    print(f"  {'group':<34}{'races':>9}{'at alt':>8}{'b_alt':>9}{'±':>7}")

    def line(label, m):
        b, se, n = cc.withinFit(grp[m], y[m], X[m])
        if b is None:
            print(f"  {label:<34}{int(m.sum()):>9,}  too few")
            return None
        print(f"  {label:<34}{n:>9,}{int((m & alt_rows).sum()):>8,}{b[0]:>+9.4f}{se[0]:>7.4f}")
        return b[0]
    b_vis = line("visitors (home < 0.3 km)", home < 0.3)
    line("  ...at races with a sea-level field", (home < 0.3) & (field < 0.3))
    line("  ...at races with a resident field", (home < 0.3) & (field >= 0.7))
    b_res = line("residents (home >= 0.7 km)", home >= 0.7)
    line("everyone", np.ones(len(keep), bool))
    print()
    # ★ THE COEFFICIENT, WITH THE FIELD HELD. A visitor's published credit is
    #   K*km - K*ACCLIM*field (the field's mean home km); their true loss is
    #   k*km. Regressing on both, b_km = K - k whatever the field was.
    vm = home < 0.3
    bv, sev, nv = cc.withinFit(grp[vm], y[vm], np.column_stack([km, field, course, day, din])[vm])
    if bv is not None:
        print(f"  visitors, field held: b_km {bv[0]:+.4f} ± {sev[0]:.4f}, b_field {bv[1]:+.4f} "
              f"± {sev[1]:.4f} (scheme expects b_field {-K_NOW * ACCLIM_NOW:+.4f})")
        b_vis = bv[0]
    if b_vis is not None:
        print(f"  coefficient that would zero the visitors' b: {K_NOW - b_vis:+.4f} per km "
              f"(now {K_NOW}; the literature's range is about 0.020-0.030)")
    if b_res is not None:
        # a resident field gets K * (km - acclim * home); for residents km ~ home,
        # so their credit per km is K * (1 - acclim); b says the true is that - b
        share = 1.0 - ACCLIM_NOW
        k_true = (K_NOW - (b_vis if b_vis is not None else 0.0))
        if k_true > 0:
            acc = 1.0 - (K_NOW * share - b_res) / k_true
            print(f"  acclimatisation share that would zero the residents' b: {acc:.2f} "
                  f"(now {ACCLIM_NOW})")
    print("\n  reading: b_alt +0.010 means a race at 2,200 m (1.6 km over the floor) rates "
          "1.6% high.")


if __name__ == "__main__":
    main()
