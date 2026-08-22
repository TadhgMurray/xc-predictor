# Project: xc-predictor / scripts
# File:    peek_pack.py
# Purpose: READ ONLY. Open the cached pack the engine actually rated from, and
#          print, for one race, the athlete key and venue key IT USED.
#
#     python scripts/peek_pack.py --meet 271870 --div 1083175
#
# ★ WHY THE PACK AND NOT A REPLAY. replay_rating calls poolOf NOW. The engine
#   called it at PACK TIME and froze the answer into packed_XC_TF.npz --
#   08_golive logs "[cache] loading packed_XC_TF.npz". Everything between then
#   and now that touches pooling (season_level, grade_sanity, pro_flag) moves
#   what poolOf returns without moving the pack. A replay that disagrees with
#   the pack proves only that the inputs changed, not what the engine did.
#
#   buildResultRatings does exactly two lookups per row:
#
#       pm = means[ athlete_keys[ athlete[i] ][1] ]     <- the pool STRING
#       d  = difficulty[ course[i] ]                    <- the cell CODE
#
#   Both are in the pack. This prints them. If two rows of one race carry
#   different pool strings or different cell codes, that is the whole answer,
#   measured from the artefact the ratings were built from.

import argparse
import os
import sys

import numpy as np

sys.path.insert(0, "engine")
sys.path.insert(0, "scripts")

from database import getConn                       # noqa: E402

_DEFAULT = os.path.join("engine", "data", "packed_XC_TF.npz")


def main():
    ap = argparse.ArgumentParser(
        description="What pool and cell did the ENGINE use for this race?")
    ap.add_argument("--meet", type=int, required=True)
    ap.add_argument("--div", type=int, required=True)
    ap.add_argument("--pack", default=_DEFAULT)
    ap.add_argument("--limit", type=int, default=40)
    args = ap.parse_args()

    if not os.path.exists(args.pack):
        sys.exit(f"pack not found: {args.pack}")

    with getConn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT result_id, time_seconds, speed_rating, normalized_time
                FROM   results
                WHERE  meet_id = %s AND div_id = %s AND speed_rating > 0
                ORDER  BY time_seconds
            """, (args.meet, args.div))
            db = {int(r[0]): r[1:] for r in cur.fetchall()}
    if not db:
        sys.exit("no rated rows for that race")

    print(f"  loading {args.pack} ...", flush=True)
    z = np.load(args.pack, allow_pickle=True)
    rid = z["result_id"]
    athlete = z["athlete"]
    course = z["course"]
    a_keys = z["athlete_keys"]
    c_keys = z["course_keys"]
    norm = z["norm"]

    want = np.fromiter(db.keys(), dtype=np.int64)
    idx = np.nonzero(np.isin(rid, want))[0]
    print(f"  {len(idx):,} of this race's {len(db):,} rated rows are in the "
          f"pack\n")
    if len(idx) == 0:
        print("  NONE of them are in the pack. The ratings on these rows did "
              "not come from\n  this pack -- it is from a different run than "
              "the ratings in the table.")
        return 0

    order = idx[np.argsort([db[int(rid[i])][0] for i in idx])][:args.limit]
    print(f"    {'time':>8} {'rating':>7} {'norm(db)':>9} {'norm(pack)':>10} "
          f"{'cell':>7}  pool key")
    pools, cells = {}, {}
    for i in order:
        r = int(rid[i])
        t, sr_, nt = db[r]
        ak = a_keys[athlete[i]]
        pool = ak[1] if isinstance(ak, (tuple, list)) and len(ak) > 1 else ak
        cc = int(course[i])
        pools[str(pool)] = pools.get(str(pool), 0) + 1
        cells[cc] = cells.get(cc, 0) + 1
        print(f"    {float(t):>8.1f} {float(sr_):>7.2f} {float(nt):>9.2f} "
              f"{float(norm[i]):>10.2f} {cc:>7}  {pool}")

    print(f"\n  DISTINCT POOL KEYS THE ENGINE USED: {len(pools)}")
    for k, n in sorted(pools.items(), key=lambda kv: -kv[1]):
        print(f"    x{n:<5} {k}")
    print(f"\n  DISTINCT CELL CODES THE ENGINE USED: {len(cells)}")
    for k, n in sorted(cells.items(), key=lambda kv: -kv[1]):
        name = c_keys[k] if 0 <= k < len(c_keys) else "(venueless)"
        print(f"    x{n:<5} {k}  {name}")

    if len(pools) > 1:
        print("\n  -> the engine used MORE THAN ONE POOL in this race. pm is "
              "means[pool], so\n     these rows were divided by different "
              "constants. That is the fault.")
    elif len(cells) > 1:
        print("\n  -> the engine used MORE THAN ONE CELL in this race, so the "
              "rows got\n     different difficulties. That is the fault.")
    else:
        print("\n  -> one pool, one cell. Then rating * norm(pack) must be "
              "constant; if it is\n     not, the ratings in the table were "
              "not written from this pack.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
