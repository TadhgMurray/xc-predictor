"""
diag_hs_factor.py -- what the HS-equivalent factor is made of, per pool.

    python scripts/diag_hs_factor.py            # every pool
    python scripts/diag_hs_factor.py --board    # + the home page's MS rows

Read-only. Prints each pool's constant per sport (the median of
rating * normalized_time / (1 + d) / 100 over that pool's own rows, i.e.
the pool mean in that sport's normalised units), the per-sport ratio
against the same-gender HS pool, and the factor pool_view will apply.
Issue 139: the middle-school boards read 220 after the factor became one
number per pool; this is the table to read before touching the formula.
"""
import argparse
import sys

sys.path.insert(0, "racecast")
sys.path.insert(0, "scripts")
sys.path.insert(0, "engine")

import pool_view as PV                                           # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--board", action="store_true")
    args = ap.parse_args()
    PV._CONST_CACHE.clear(); PV._FACTOR_CACHE.clear()
    pools = ("hs_m", "hs_f", "ms_m", "ms_f", "college_m", "college_f")
    print(f"{'pool':<10} {'C(XC)':>9} {'C(TF)':>9} {'hs/pool XC':>11} {'hs/pool TF':>11} {'factor':>8}")
    for pool in pools:
        cs = {sp: PV._poolConstant(pool, sp) for sp in ("XC", "TF")}
        g = pool.rsplit("_", 1)[-1]
        hs = {sp: PV._poolConstant("hs_" + g, sp) for sp in ("XC", "TF")}
        r = {sp: (hs[sp] / cs[sp] if cs[sp] and hs[sp] else None) for sp in ("XC", "TF")}
        f = PV.hsFactor(pool, "XC", 5000)
        fmt = lambda v, w=9, p=1: (f"{v:>{w}.{p}f}" if v is not None else f"{'--':>{w}}")
        print(f"{pool:<10} {fmt(cs['XC'])} {fmt(cs['TF'])} {fmt(r['XC'], 11, 3)} "
              f"{fmt(r['TF'], 11, 3)} {fmt(f, 8, 3)}")
    if args.board:
        from database import getConn
        with getConn() as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT rr.person_id, rr.pool, rr.sport, rr.speed_rating,
                       rr.time_seconds, rr.distance, r.normalized_time
                FROM   ranking_results rr
                JOIN   results r ON r.result_id = rr.result_id
                WHERE  rr.pool = 'ms_m' AND rr.sport = 'XC'
                  AND  rr.speed_rating IS NOT NULL
                ORDER  BY rr.speed_rating DESC LIMIT 5
            """)
            print("\n  top ms_m XC rows: person, pool, sport, rating, time, distance, normalized_time")
            for row in cur.fetchall():
                print("   ", row)


if __name__ == "__main__":
    main()
