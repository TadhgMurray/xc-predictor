"""diag_hs_equiv.py -- WHY an HS-equivalent rating lands where it does.

    python scripts/diag_hs_equiv.py --person 29346285
    python scripts/diag_hs_equiv.py --pool ms_m --sport XC
    python scripts/diag_hs_equiv.py --audit            # every pool, one table

Issue #13. The entry is explicit that the algebra must NOT be touched before
the error is characterised, because the two previous corrections were both
found by a live test naming an athlete and neither by re-reading the formula.
This produces that characterisation.

★ WHAT "SLIGHTLY OFF" COULD MEAN, and this prints enough to tell them apart:

    hs_rating = rating x [C(hs_g)/C(pool)] x [F(d,pool)/F(d,hs_g)]

  so a wrong number has exactly four possible sources, and they leave
  different fingerprints:

    1. THE WRONG POOL WENT IN. stampRowsHs falls back to the table's MODAL
       pool when a row's exact ranking_results match misses -- so a row that
       never ranked is priced as though it were in the pool most of its
       neighbours are in. Printed per race as `pool src`: `exact` or `modal`.
    2. THE RAIL CLAMPED. A factor outside 0.5-2.0 is discarded and the rating
       silently stays on its own scale -- which looks like "the toggle did
       nothing for this athlete" rather than like an error. Printed as
       `RAILED` with the raw factor.
    3. C(pool) IS AN ANECDOTE. The constant is a median over up to 1500
       sampled rows and needs 50; a thin pool gets a noisy constant, and the
       whole scale for that pool moves with it. Printed as the sample size.
    4. F IS INHERITED, NOT FITTED. A pool whose distance spline was never
       fitted borrows another's, so F(d,pool) is not that pool's curve and
       the ratio is not that pool's level. Printed per pool.

⚠ IT WRITES NOTHING AND CHANGES NOTHING. Read-only, safe against the live
  site, and the numbers it prints are the ones the site is actually using --
  it calls pool_view, not a copy of the formula.
"""

import argparse
import os
import sys

sys.path.insert(0, "scripts")
sys.path.insert(0, "racecast")
sys.path.insert(0, "engine")

from database import getConn                                 # noqa: E402
import psycopg2.extras                                       # noqa: E402
import pool_view                                             # noqa: E402


def _factorParts(pool, sport, dist):
    """The pieces hsFactor multiplies, so a wrong answer can be attributed.

    ! RE-READ FROM pool_view, NOT RECOMPUTED. A second implementation of the
      formula here would diagnose itself rather than the site.
    """
    suffix = (pool or "").rsplit("_", 1)[-1]
    out = {"pool": pool, "dist": dist, "factor": None, "c_own": None,
           "c_hs": None, "f_own": None, "f_hs": None, "raw": None,
           "railed": False, "why": ""}
    if not pool or suffix not in ("m", "f"):
        out["why"] = "no gendered pool"
        return out
    if pool in ("hs_m", "hs_f"):
        out["factor"] = 1.0
        out["why"] = "already HS"
        return out
    if not dist:
        out["why"] = "no distance"
        return out

    out["c_own"] = pool_view._poolConstant(pool, sport)
    out["c_hs"] = pool_view._poolConstant("hs_" + suffix, sport)
    try:
        # ! THE SAME SYMBOL pool_view IMPORTED, reached through pool_view so
        #   there is no second import path to drift. It lives in
        #   conversions; pool_view binds it at module level.
        ff = pool_view._forward_factor
        out["f_own"] = ff(float(dist), pool, None, None, None, sport, None)
        out["f_hs"] = ff(float(dist), "hs_" + suffix,
                         None, None, None, sport, None)
    except Exception as exc:                                 # noqa: BLE001
        out["why"] = f"{type(exc).__name__}: {exc}"

    if out["c_own"] and out["c_hs"] and out["f_own"] and out["f_hs"]:
        out["raw"] = ((float(out["c_hs"]) / float(out["c_own"]))
                      * (float(out["f_own"]) / float(out["f_hs"])))
        lo, hi = pool_view._FACTOR_LO, pool_view._FACTOR_HI
        out["railed"] = not (lo <= out["raw"] <= hi)
    # The value the SITE will use, whatever this reconstruction says.
    out["factor"] = pool_view.hsFactor(pool, sport, dist)
    return out


def _constSample(cur, pool, sport):
    """How many rows C(pool) was actually medianed over. Below 50 it is not
    a constant, it is an anecdote -- and the whole pool's scale rides on it."""
    sql = pool_view._CONST_SQL.get(sport)
    if sql is None:
        return None
    try:
        cur.execute(f"SELECT count(*) FROM ({sql}) s",
                    {"pool": pool, "n": pool_view._CONST_SAMPLE})
        return cur.fetchone()[0]
    except Exception:                                        # noqa: BLE001
        return None


def person(cur, pid, sport):
    """Every rated race of one athlete, with the factor that priced it."""
    cur.execute("""
        SELECT r.result_id, r.pool, r.speed_rating, r.distance, r.race_date,
               r.sport
        FROM   ranking_results r
        WHERE  r.person_id = %(pid)s AND r.speed_rating IS NOT NULL
        ORDER  BY r.race_date
    """, {"pid": pid})
    rows = cur.fetchall()
    if not rows:
        print(f"no rated rows for person {pid}")
        return
    print(f"\nperson {pid}: {len(rows)} rated races")
    print(f"{'date':<12} {'sport':<5} {'pool':<10} {'dist':>6} "
          f"{'own':>7} {'hs':>7} {'factor':>7}  note")
    for r in rows:
        p = _factorParts(r["pool"], r["sport"], r["distance"])
        f = p["factor"]
        own = float(r["speed_rating"])
        hs = own * f if f else own
        note = []
        if f is None:
            note.append("NO FACTOR -- shown on its own scale")
        if p["railed"]:
            note.append(f"RAILED (raw {p['raw']:.3f})")
        if p["why"]:
            note.append(p["why"])
        print(f"{str(r['race_date']):<12} {r['sport']:<5} "
              f"{str(r['pool']):<10} {str(r['distance'] or ''):>6} "
              f"{own:>7.1f} {hs:>7.1f} "
              f"{(f'{f:.3f}' if f else '--'):>7}  {'; '.join(note)}")


def audit(cur, sport, only=None):
    """Every pool's factor at the representative distance, with the evidence
    behind it. This is the table to read first: a pool whose constant rests
    on forty rows will be wrong for every athlete in it, and no per-athlete
    hunt will show that as clearly as one line here."""
    cur.execute("""
        SELECT pool, count(*) AS n
        FROM   athlete_season
        WHERE  sport = %(s)s AND pool IS NOT NULL
        GROUP  BY pool ORDER BY count(*) DESC
    """, {"s": sport})
    pools = [r["pool"] for r in cur.fetchall()
             if not only or r["pool"] == only]
    dist = pool_view._REP_DIST.get(sport)
    print(f"\n{sport} at {dist:.0f}m -- the factor every aggregate uses")
    print(f"{'pool':<12} {'factor':>7} {'raw':>7} {'C(own)':>8} {'C(hs)':>8} "
          f"{'F(own)':>8} {'F(hs)':>8} {'Csample':>8}  note")
    for pl in pools:
        p = _factorParts(pl, sport, dist)
        n = _constSample(cur, pl, sport)
        note = []
        if p["railed"]:
            note.append("RAILED")
        if n is not None and n < pool_view._CONST_MIN_ROWS:
            note.append(f"C is an anecdote ({n} rows < "
                        f"{pool_view._CONST_MIN_ROWS})")
        if p["factor"] is None and not p["why"]:
            note.append("no factor")
        if p["why"]:
            note.append(p["why"])
        fmt = lambda v, w=8, d=3: (f"{v:>{w}.{d}f}" if isinstance(v, float)
                                   else f"{'--':>{w}}")
        print(f"{pl:<12} {fmt(p['factor'], 7)} {fmt(p['raw'], 7)} "
              f"{fmt(p['c_own'], 8, 2)} {fmt(p['c_hs'], 8, 2)} "
              f"{fmt(p['f_own'], 8, 4)} {fmt(p['f_hs'], 8, 4)} "
              f"{(str(n) if n is not None else '--'):>8}  {'; '.join(note)}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--person", type=int, help="one athlete, race by race")
    ap.add_argument("--pool", help="limit the audit to one pool")
    ap.add_argument("--sport", default="XC", choices=("XC", "TF"))
    ap.add_argument("--audit", action="store_true",
                    help="every pool's factor and the evidence behind it")
    args = ap.parse_args()

    if not args.person and not args.audit and not args.pool:
        ap.error("give --person, --pool or --audit")

    with getConn() as conn:
        with conn.cursor(
                cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if args.person:
                person(cur, args.person, args.sport)
            if args.audit or args.pool:
                audit(cur, args.sport, args.pool)

    print("\n! NOTHING WAS WRITTEN. Read the table above before changing "
          "pool_view.py -- issue #13 is explicit that the algebra has been "
          "re-read twice and the errors were found in the data both times.")


if __name__ == "__main__":
    main()
