"""
diag_dual_sport_pack.py -- per pool, how many athlete-seasons in the
engine's pack raced BOTH sports (the rows the winter-gain shift and the
curve pin are measured on). run12's go-live table listed hs_m, hs_f and
elem_m only: no ms_* and no college_* pool had a single dual-sport
athlete-season, which cannot be right for a college team that runs XC
in the fall and track in the spring (issue 213).

    /srv/venv/bin/python scripts/diag_dual_sport_pack.py
    /srv/venv/bin/python scripts/diag_dual_sport_pack.py --pack engine/data/packed_XC_TF.npz
"""
import argparse
import sys

import numpy as np

sys.path.insert(0, "engine")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pack", default="engine/data/packed_XC_TF.npz")
    args = ap.parse_args()
    import pair_engine as pe
    cols = pe.loadPack(args.pack)
    keys = cols["athlete_keys"]
    pools = np.array([k[1] for k in keys])
    athlete = np.asarray(cols["athlete"])
    year = np.asarray(cols["year"])
    sport = np.asarray(cols["sport"])
    keep = np.asarray(cols["course"]) >= 0
    code, n = pe.athleteSeasonCodes(athlete, year)
    has_xc = np.zeros(n, dtype=bool); has_tf = np.zeros(n, dtype=bool)
    has_xc[code[keep & (sport == 0)]] = True
    has_tf[code[keep & (sport == 1)]] = True
    pool_of_season = np.empty(n, dtype=object)
    pool_of_season[code] = pools[athlete]
    print(f"{args.pack}: {n:,} athlete-seasons over {int(keep.sum()):,} rows")
    print(f"  {'pool':<26}{'seasons':>10}{'XC only':>10}{'TF only':>10}{'both':>10}{'both %':>8}")
    for p in sorted(set(pools.tolist())):
        m = pool_of_season == p
        both = int((m & has_xc & has_tf).sum())
        print(f"  {p:<26}{int(m.sum()):>10,}{int((m & has_xc & ~has_tf).sum()):>10,}"
              f"{int((m & has_tf & ~has_xc).sum()):>10,}{both:>10,}"
              f"{100.0 * both / max(int(m.sum()), 1):>7.1f}%")
    # the sport suffix, if the keys carry one, is the first thing to look at
    with_bar = sum(1 for p in set(pools.tolist()) if "|" in p)
    if with_bar:
        print(f"\n  ! {with_bar} pool names carry a '|sport' suffix: an athlete's XC and "
              f"TF rows are DIFFERENT athletes to the solve in those pools")


if __name__ == "__main__":
    main()
