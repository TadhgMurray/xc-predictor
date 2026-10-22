#!/usr/bin/env python3
"""
diag_equal_time.py -- the public scale today (one multiplier per pool)
against the equal-time map (XCP_EQUAL_TIME_SCALE=1), on the real clocks.
READ ONLY; changes nothing on the site.

    set -a; . /etc/xc-predictor.env; set +a
    /srv/venv/bin/python scripts/diag_equal_time.py
    /srv/venv/bin/python scripts/diag_equal_time.py --person 12345678

★ WHY (owner, 2026-10-22): a college 149.2 read 16:02 beside a high school
  149.3 at 16:50. For each pool with an HS twin it prints the map's fit
  (hs = a * own ** b) and, for a ladder of own ratings, the public number
  today and under the map, with the track 5K each one means on the HS clock
  -- under the map that 5K equals the pool's own 5K, which is the point.
  --person shows one athlete's seasons both ways.
"""
import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

POOLS = ("college_f", "college_m", "ms_f", "ms_m")
LADDER = (80, 100, 110, 120, 130, 140, 150, 160)


def mmss(sec):
    if not sec:
        return "  -  "
    m, s = divmod(float(sec), 60)
    return f"{int(m)}:{s:04.1f}"


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--person", type=int, default=None)
    a = ap.parse_args()
    import pool_view as PV

    def both(fn):
        old = os.environ.get("XCP_EQUAL_TIME_SCALE")
        out = []
        for flag in ("0", "1"):
            os.environ["XCP_EQUAL_TIME_SCALE"] = flag
            PV._MAP_CACHE.clear()
            out.append(fn())
        if old is None:
            os.environ.pop("XCP_EQUAL_TIME_SCALE", None)
        else:
            os.environ["XCP_EQUAL_TIME_SCALE"] = old
        return out

    for pool in POOLS:
        hs_pool = "hs_" + pool.rsplit("_", 1)[-1]
        (m0, m1) = both(lambda: PV.hsMap(pool, "XC"))
        if m1 is None:
            print(f"\n{pool}: no HS twin")
            continue
        why = PV._FACTOR_WHY.get(pool + "|map", "")
        print(f"\n== {pool} -> {hs_pool}   today: x{m0[0]:.4f}   equal time: "
              f"{m1[0]:.4f} * own^{m1[1]:.4f}   {why}")
        print(f"   {'own':>5} {'own 5K':>8} | {'today':>6} {'= HS 5K':>8} | {'equal time':>10} {'= HS 5K':>8}")
        for own in LADDER:
            t_own = PV._trackSeconds(own, pool)
            h0 = m0[0] * own ** m0[1]
            h1 = m1[0] * own ** m1[1]
            print(f"   {own:>5} {mmss(t_own):>8} | {h0:>6.1f} {mmss(PV._trackSeconds(h0, hs_pool)):>8} | "
                  f"{h1:>10.1f} {mmss(PV._trackSeconds(h1, hs_pool)):>8}")

    if a.person:
        from database import getConn
        with getConn() as conn, conn.cursor() as cur:
            cur.execute("""SELECT year, split_part(pool, '|', 1), sport, mean_rating
                           FROM athlete_season WHERE person_id = %s AND mean_rating > 0
                           ORDER BY year, sport""", (a.person,))
            rows = cur.fetchall()
            conn.rollback()
        print(f"\n== person {a.person}")
        print(f"   {'year':>4} {'pool':<10}{'sport':<6}{'own':>7}{'today':>8}{'equal':>8}{'5K':>9}")
        for yr, pool, sport, r in rows:
            h0, h1 = both(lambda: PV.toHs(r, pool, sport))
            print(f"   {yr:>4} {pool:<10}{sport:<6}{float(r):>7.1f}"
                  f"{(h0 or float('nan')):>8.1f}{(h1 or float('nan')):>8.1f}"
                  f"{mmss(PV._trackSeconds(r, pool)):>9}")


if __name__ == "__main__":
    main()
