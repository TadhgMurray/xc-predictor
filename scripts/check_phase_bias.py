# Project: xc-predictor / scripts
# File:    check_phase_bias.py
# Purpose: Does season phase leak into course difficulty? Cells bucketed by
#          the median calendar day of their races, difficulty per bucket.
#
#     python scripts/check_phase_bias.py
#     python scripts/check_phase_bias.py --venue "Cabell Midland"
#
# ★ THE HYPOTHESIS THIS TESTS (Cabell Midland, 2026-08-27). The venue's
#   d3000 cell sits at +0.155 with bridge 0.81 -- its athletes travel, so
#   self-reference is NOT the mechanism. But every d3000 race there is an
#   August/September opener while the d5000 races are Oct-Nov: cell =
#   venue x distance, and distance correlates with calendar. If the
#   rust/form correction under-corrects early-season slowness, "everyone is
#   unfit in August" solves into early-season cells as course difficulty.
#   If that is real, it is corpus-wide and this table shows it: mean
#   difficulty should FALL monotonically across the doy buckets.
#
# Read-only. Needs the pack (row doy per cell) and pair_difficulty.npz
# (the solved difficulties) from the same engine/data directory.

import argparse
import os
import sys

sys.path.insert(0, "engine")
sys.path.insert(0, "scripts")

import numpy as np                                    # noqa: E402

import pair_engine as pe                              # noqa: E402

_DATA = os.path.join("engine", "data")

# XC season buckets, day-of-year. The point is the TREND, not the borders.
_BUCKETS = [("Aug (<= 243)", 0, 243), ("early Sep", 244, 258),
            ("late Sep", 259, 273), ("Oct", 274, 304),
            ("Nov+", 305, 400)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pack", default=os.path.join(_DATA, "packed_XC_TF.npz"))
    ap.add_argument("--diff", default=os.path.join(_DATA,
                                                   "pair_difficulty.npz"))
    ap.add_argument("--venue", default=None,
                    help="also print this venue's cells with their buckets")
    args = ap.parse_args()

    cols = pe.loadPack(args.pack)
    npz = np.load(args.diff, allow_pickle=True)
    keys_p = [str(k) for k in cols["course_keys"]]
    keys_d = [str(k) for k in npz["course_keys"]]
    if keys_p != keys_d:
        print("  pack and pair_difficulty disagree on course_keys -- they "
              "are from different runs. Re-run 08_golive or point --diff at "
              "the matching file.")
        return 1
    difficulty = np.asarray(npz["difficulty"], dtype=np.float64)
    solved = np.asarray(npz["solved"], dtype=bool)

    course = np.asarray(cols["course"])
    doy = np.asarray(cols["doy"], dtype=np.float64)
    ok = course >= 0
    n_cells = len(keys_p)

    # median doy and row count per cell, one bincount-free pass via sort
    order = np.argsort(course[ok], kind="stable")
    c_s, d_s = course[ok][order], doy[ok][order]
    starts = np.flatnonzero(np.r_[True, c_s[1:] != c_s[:-1]])
    ends = np.r_[starts[1:], len(c_s)]
    med_doy = np.full(n_cells, np.nan)
    n_rows = np.zeros(n_cells, dtype=np.int64)
    for s, e in zip(starts, ends):
        med_doy[c_s[s]] = np.median(d_s[s:e])
        n_rows[c_s[s]] = e - s

    is_xc = np.array([k.startswith("XC:") for k in keys_p], dtype=bool)
    use = solved & is_xc & ~np.isnan(med_doy)

    print(f"\n  {int(use.sum()):,} solved XC cells, difficulty by the "
          "median day-of-year of the cell's races.")
    print("  A falling column = season phase is leaking into difficulty "
          "(early races read 'hard').\n")
    print(f"    {'bucket':<16}{'cells':>8}{'rows':>12}{'mean diff':>11}"
          f"{'median':>9}{'p90':>8}")
    print("    " + "-" * 64)
    for name, lo, hi in _BUCKETS:
        m = use & (med_doy >= lo) & (med_doy <= hi)
        if not m.any():
            continue
        d = difficulty[m]
        w = n_rows[m].astype(np.float64)
        print(f"    {name:<16}{int(m.sum()):>8,}{int(w.sum()):>12,}"
              f"{np.average(d, weights=w):>+11.4f}"
              f"{float(np.median(d)):>+9.4f}"
              f"{float(np.percentile(d, 90)):>+8.3f}")

    if args.venue:
        pat = args.venue.lower()
        tags = [pat]
        try:
            from database import getConn
            with getConn() as conn, conn.cursor() as cur:
                cur.execute("SELECT DISTINCT canonical_id FROM "
                            "course_canonical WHERE course_name ILIKE %s",
                            (f"%{args.venue}%",))
                tags += [f"xc:{cid}:" for (cid,) in cur.fetchall()]
        except Exception:                              # noqa: BLE001
            pass
        print(f"\n    {'cell key':<34}{'med doy':>9}{'rows':>8}"
              f"{'difficulty':>12}")
        for i, k in enumerate(keys_p):
            if any(t in k.lower() for t in tags) and not np.isnan(med_doy[i]):
                print(f"    {k[:34]:<34}{med_doy[i]:>9.0f}{n_rows[i]:>8,}"
                      f"{difficulty[i]:>+12.4f}"
                      f"{'' if solved[i] else '  (unsolved)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
