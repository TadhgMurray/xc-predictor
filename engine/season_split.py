"""
season_split.py -- does the form correction treat one calendar year as one
training cycle when it is really two?

THE SUSPICION
    speed_ratings records the convention plainly: "the calendar year IS the
    season for both sports -- XC runs Aug-Dec of one year, TF Feb-Jul of one
    year." So within season 2024, TRACK COMES FIRST (Feb-Jul) and CROSS COUNTRY
    SECOND (Aug-Dec).

    rust_fitness measures days since the athlete's first race OF THAT SEASON.
    For an XC-only athlete a September race lands around day 30 -- the steep
    part of the curve, roughly 40% of the decline. For a dual-sport athlete
    whose season opened at a February track meet, the SAME September race lands
    near day 210, far past the week-8 plateau, and receives the full -2.95%.

★ SO THE SAME RACE GETS A DIFFERENT CORRECTION DEPENDING ON WHETHER THE ATHLETE
  RAN TRACK THAT SPRING. If fitness genuinely resets over the summer -- and it
  does; nobody carries February form into September -- then dual-sport athletes
  are systematically OVER-corrected in cross country, their times are treated as
  better than they were, and the surplus lands in whichever venue they raced.

★ THIS IS MEASURED WITHIN VENUE, so course difficulty cancels exactly. The
  contrast is between two athletes at the SAME race, one of whom ran track and
  one of whom did not.

Measures only.
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, "scripts")
for _p in (_HERE, _ROOT,
           os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "scripts")):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np

import pair_validate as pv


def seasonOpener(group, day, n_groups):
    """
    Days since this athlete-season's FIRST race -- the quantity rust_fitness
    keys the form curve on.

    cols["days"] counts BACKWARDS (days ago), so it is negated first: unnegated,
    every "first race" would be the athlete's LAST one.
    """
    fwd = -day.astype(np.int64)
    first = np.full(n_groups, np.iinfo(np.int64).max, dtype=np.int64)
    np.minimum.at(first, group, fwd)
    return (fwd - first[group]).astype(np.float64)


def ranTrackFirst(group, sport, day, n_groups):
    """
    Per row: did this athlete-season open with a TRACK race?

    That is the whole distinction. An athlete-season opening in February is on
    the far side of the form plateau by autumn; one opening in August is not.
    """
    fwd = -day.astype(np.int64)
    first = np.full(n_groups, np.iinfo(np.int64).max, dtype=np.int64)
    np.minimum.at(first, group, fwd)

    opener_tf = np.zeros(n_groups, dtype=bool)
    is_first = fwd == first[group]
    opener_tf[group[is_first & (sport == 1)]] = True
    return opener_tf[group]


def table(resid, days, dual, xc, weights_ok):
    """
    Mean residual for CROSS COUNTRY rows, by days-since-opener and by whether
    the season opened on the track.

    Positive residual = ran SLOWER than the model expected. If dual-sport
    athletes are over-corrected, their XC rows should sit systematically
    positive against the XC-only column.
    """
    m0 = weights_ok & xc
    bands = [(0, 20), (20, 40), (40, 60), (60, 100), (100, 160), (160, 400)]

    print("\n[season] mean residual on CROSS COUNTRY rows")
    print("         (positive = slower than the model expected)")
    print("    days since opener    XC-only          opened on track       gap")
    for lo, hi in bands:
        m = m0 & (days >= lo) & (days < hi)
        a, b = m & ~dual, m & dual
        if a.sum() < 2000 or b.sum() < 2000:
            continue
        va, vb = float(resid[a].mean()), float(resid[b].mean())
        print(f"    {lo:>4}-{hi:<4} {int(a.sum()):>10,}/{int(b.sum()):<10,} "
              f"{va:>+10.5f} {vb:>+18.5f} {vb - va:>+11.5f}")

    a, b = m0 & ~dual, m0 & dual
    if a.sum() and b.sum():
        print(f"\n    overall  XC-only {float(resid[a].mean()):+.5f}   "
              f"opened on track {float(resid[b].mean()):+.5f}   "
              f"gap {float(resid[b].mean() - resid[a].mean()):+.5f}")
        print("    a gap near zero means the shared-season convention is fine;")
        print("    a positive gap means dual-sport athletes are over-corrected")
        print("    in cross country and their surplus lands in the venue.")


def main(pack_path):
    import pair_all as pa

    D = pa.prepare(pack_path)
    for need in ("sport", "cols"):
        if need not in D:
            print(f"[season] pack has no '{need}'")
            return
    if "days" not in D["cols"]:
        print("[season] pack has no 'days' column")
        return

    pa.solve(D)                       # cached
    alpha, _c = pv.refitAlpha(D["y"], D["delta"], D["course"], D["group"],
                              D["n_groups"])
    resid = D["y"] - alpha[D["group"]] - D["delta"][D["course"]]

    day = D["cols"]["days"][D["keep"]]
    days = seasonOpener(D["group"], day, D["n_groups"])
    dual = ranTrackFirst(D["group"], D["sport"], day, D["n_groups"])
    xc = D["sport"] == 0

    ok = (D["degree"] >= 2)[D["course"]] & np.isfinite(resid)
    print(f"[season] {int((ok & xc & dual).sum()):,} XC rows from seasons that "
          f"opened on the track, {int((ok & xc & ~dual).sum()):,} that did not")
    table(resid, days, dual, xc, ok)


if __name__ == "__main__":
    here = os.path.join(_HERE, "data")
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    main(args[0] if args else os.path.join(here, "packed_XC_TF.npz"))