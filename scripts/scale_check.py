#!/usr/bin/env python3
"""
scale_check.py -- the public (HS-equivalent) scale for each pool, worked out
fresh in this process. READ-ONLY.

    set -a; . /etc/xc-predictor.env; set +a; P=/srv/venv/bin/python
    $P scripts/scale_check.py

★ WHY (owner, 2026-11-08): a Tufts 8K at 81.1, Rupp's 131 shown as 94, a
  13:15 5000 m at 126.6. The site's numbers come from each web worker's own
  copy of the map; this one is built from scratch. If these lines look right
  and the site does not, the workers' copies were stale (restart them). If
  these look wrong too, the scale data itself is wrong, and the engine_scale
  rows and the clock seconds below say where.
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, os.path.join(_ROOT, "engine"), os.path.join(_ROOT, "racecast")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

POOLS = ("hs_m", "hs_f", "college_m", "college_f", "ms_m", "ms_f")


def _t(s):
    if not s:
        return "-"
    return f"{int(s // 60)}:{s % 60:04.1f}"


def main():
    import conversions
    import pool_view
    from database import getConn
    with getConn() as conn, conn.cursor() as cur:
        cur.execute("SELECT pool, sport, pool_mean, median_effect, anchor_shift, n_rows "
                    "FROM engine_scale ORDER BY 1, 2")
        print("engine_scale:")
        for r in cur.fetchall():
            print(f"  {r[0]:<12} {r[1]:<3} pool_mean {r[2]:.2f}  median_effect {r[3]:+.4f}"
                  f"  anchor_shift {r[4]:+.4f}  rows {r[5]:,}")
        conn.rollback()
    print(f"XCP_DISTANCE_BY={os.environ.get('XCP_DISTANCE_BY', '(unset)')}  "
          f"XCP_EQUAL_TIME_SCALE={os.environ.get('XCP_EQUAL_TIME_SCALE', '(unset)')}")
    for sport in ("XC", "TF"):
        print(f"\n{sport}: own rating -> public rating, and the 5K column's clock")
        for pool in POOLS:
            f = pool_view.repFactor(pool, sport)
            pool_view.hsMap(pool, sport)
            why = pool_view._FACTOR_WHY.get(pool + "|map") or pool_view._FACTOR_WHY.get(pool, "")
            cells = []
            for r in (100, 120, 130):
                h = pool_view.toHs(r, pool, sport)
                t = conversions.xcTrackSeconds(r, pool, 5000.0, sport)
                cells.append(f"{r}->{h and round(h, 1)} ({_t(t)})")
            print(f"  {pool:<10} factor {f and round(f, 4)}  " + "  ".join(cells))
            if why:
                print(f"             {why}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
