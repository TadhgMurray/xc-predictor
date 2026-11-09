#!/usr/bin/env python3
"""
distance_mode_check.py -- what the distance curve by ability does to a time,
with and without the per-pool residual. READ-ONLY, no database.

    /srv/venv/bin/python scripts/distance_mode_check.py

★ WHY (owner, 2026-11-09: "the ratings went up too"). After the full run in
  the ability mode the college women's board read 169.8 (a 14:11 5K) for
  an 18:24 6K. A 6K or 8K time is converted to its 5K equivalent before it
  is rated; a conversion that takes too much off makes every longer race
  rate high. This prints, for real race times at the distances each level
  runs, the 5K equivalent and the exponent it implies (t5 = t * (5000/d)^k;
  Riegel's 1.06 for comparison), with the residual and without, and the
  residual each pool has.
"""
import math
import os
import pickle
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_ROOT, "engine"))

import distance_ability as da  # noqa: E402

CASES = [  # pool, metres, times in seconds
    ("college_f", 6000, (1104, 1200, 1320, 1440)),       # 18:24 20:00 22:00 24:00
    ("college_m", 8000, (1380, 1500, 1620, 1740)),       # 23:00 25:00 27:00 29:00
    ("college_m", 10000, (1740, 1860, 1980)),            # 29:00 31:00 33:00
    ("hs_f", 4000, (840, 960, 1080)),                    # 14:00 16:00 18:00
    ("hs_m", 3200, (540, 600, 660)),                     # 9:00 10:00 11:00
    ("hs_m", 8000, (1440, 1560)),
    ("ms_m", 3200, (600, 690, 780)),
]


def _t(s):
    return f"{int(s // 60)}:{s % 60:04.1f}"


def main():
    path = os.path.join(_ROOT, "engine", "data", "distance_ability.pkl")
    with open(path, "rb") as f:
        art = pickle.load(f)
    print(f"artifact: {path}  fitted {art.get('fitted')}  residual in it: {da.hasResidual(art)}")
    for pool in sorted({c[0] for c in CASES}):
        fam = da.family(art, pool, "XC", residual=True)
        print(f"  residual applied to {pool}: {da.delta(fam, pool) if fam else 'no family'}")
    print(f"\n{'pool':<10} {'dist':>6} {'time':>8} | {'5K, residual':>14} {'k':>6} | "
          f"{'5K, shared':>12} {'k':>6} | {'Riegel 1.06':>11}")
    for pool, d, times in CASES:
        fr = da.family(art, pool, "XC", residual=True)
        fs = da.family(art, pool, "XC", residual=False)
        if fr is None:
            print(f"{pool:<10} {d:>6}  no family")
            continue
        for t in times:
            a = t * da.factorForTime(fr, t, d, pool)
            b = t * da.factorForTime(fs, t, d, None)
            riegel = t * (5000.0 / d) ** 1.06
            ka = math.log(a / t) / math.log(5000.0 / d)
            kb = math.log(b / t) / math.log(5000.0 / d)
            print(f"{pool:<10} {d:>6} {_t(t):>8} | {_t(a):>14} {ka:>6.3f} | {_t(b):>12} {kb:>6.3f} | {_t(riegel):>11}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
