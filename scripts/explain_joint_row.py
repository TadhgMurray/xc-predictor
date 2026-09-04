"""
explain_joint_row.py -- one result's rating taken apart into the joint
solve's terms, beside the difficulty the page shows for it.

    python scripts/explain_joint_row.py --xc 123456 789012 --tf 555
    python scripts/explain_joint_row.py --xc 123456 --no-db

For each result id it says which of three things the number on the page is:

  in the solve    rating = 100 * pool_mean / (norm / exp(h * delta + u)).
                  Printed: the cell key, delta raw and on the display scale,
                  h at the athlete's own rating, the race-day u of that
                  (cell, day), and the terms a rating leaves OUT (curve, rust,
                  sport offset). The page's difficulty column is the display
                  delta alone: a day that ran 5% off its course, or the tilt
                  at 140, is in the rating and not in that column.
  no cell         in the pack with course -1 (corrected division, placeholder
                  course): not in the solve, priced FLAT by fill_ratings,
                  no course adjustment at all, whatever the column shows.
  not in the pack the streaming query refused it (chair athlete, twin, no
                  person or gender, out of band, unrated distance): priced
                  flat by fill_ratings, or NULL.

The last number, rating x adjusted time, is the constant the row implies.
Every solved row of one athlete shares it (100 x the career pool mean); a
flat-priced row shows the pool's K instead -- that is how a page that mixes
the two is told apart from a page that is wrong.

Reads the pack and the joint npz the live step wrote (2026-09-03: the
joint solve IS step 08 under XCP_JOINT_LIVE=1). Rebuilding the design costs
what the solve's setup costs -- minutes and most of the box's memory -- so
run it on the server, once, with every id you want to see.
"""
import argparse
import os
import sys

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in ("engine", "scripts"):
    _d = os.path.join(_ROOT, _p)
    if _d not in sys.path:
        sys.path.insert(0, _d)

import joint_solve as js                                        # noqa: E402

_SPORT = {"XC": 0, "TF": 1}
_TABLE = {"XC": "results", "TF": "results_tf"}


# rowTerms
# Purpose:   the model's terms for design row j, from the arrays run_joint
#            saved. PURE: no database, nothing loaded here.
# Arguments: D -- the Design the solve was fitted on; npz -- dict-like with
#            delta, delta_anchored, race_effect, rating, beta, rust, curve,
#            curve_anchored (any may be absent); j -- row index in D.
# Output:    dict of the terms; `effect` is what the rating divides the time
#            by (in log), `left_out` the sum of what it does not.
def rowTerms(D, npz, j):
    cell = int(D.cell[j])
    ath = int(D.athlete[j])
    delta = float(npz["delta"][cell])
    anchored = (float(npz["delta_anchored"][cell])
                if "delta_anchored" in npz else float("nan"))
    rating = (float(npz["rating"][ath])
              if "rating" in npz and npz["rating"] is not None else float("nan"))
    if np.isfinite(rating):
        r_clip = min(max(rating, js.TILT_RATING_LO), js.TILT_RATING_HI)
        h = 1.0 + js.TILT_K * (r_clip - 100.0) / 10.0
        amp = float(js.amplitudeFromRating(rating))
    else:
        h, amp = 1.0, 1.0
    u = float(npz["race_effect"][int(D.race[j])])
    dist = 0.0
    if getattr(D, "n_e", 0) and "dist_offset" in npz:
        dist = float(D.e_w[j]) * float(npz["dist_offset"][int(D.e_idx[j])])
    if getattr(D, "n_k", 0) and "altitude_coef" in npz:
        dist += float(npz["altitude_coef"][int(D.group_row[j])]) * float(D.alt[j])
    out = {"cell": cell, "athlete": ath, "delta": delta,
           "delta_anchored": anchored, "rating_season": rating,
           "h": h, "amp": amp, "u": u, "dist": dist,
           "effect": h * (delta + u) + dist,        # u tilted too (156)
           "curve": 0.0, "rust": 0.0, "beta": 0.0}
    if D.has_curve and "curve" in npz:
        # the solver's own row basis: full grid, pinned knot at zero, its
        # weight zeroed -- then re-anchored to the pool's row-weighted mean
        # exactly as the abilities were (solveJoint, "THE CURVE'S GAUGE")
        c = np.asarray(npz["curve"], dtype=np.float64)
        p = int(D.pool_row[j])
        f_row = (float(D.w0[j]) * c.reshape(-1)[int(D.k0[j])]
                 + float(D.w1[j]) * c.reshape(-1)[int(D.k1[j])])
        if "curve_anchored" in npz:
            mean_p = float(c[p, 0] - np.asarray(npz["curve_anchored"])[p, 0])
        else:
            mean_p = 0.0
        out["curve"] = amp * (f_row - mean_p)
    if D.has_rust and "rust" in npz and npz["rust"] is not None:
        out["rust"] = float(D.first[j]) * float(npz["rust"][int(D.pool_row[j])])
    if D.sc is not None and "beta" in npz and npz["beta"] is not None:
        out["beta"] = float(npz["beta"][ath]) * float(D.sc[j])
    out["left_out"] = out["curve"] + out["rust"] + out["beta"]
    return out


def _loadNpz(path):
    with np.load(path, allow_pickle=False) as z:
        return {k: z[k] for k in z.files}


def _dbRows(sport, ids):
    from database import getConn
    out = {}
    if not ids:
        return out
    with getConn() as conn, conn.cursor() as cur:
        cur.execute(f"""
            SELECT result_id, speed_rating, normalized_time, date
            FROM   {_TABLE[sport]} WHERE result_id = ANY(%s)""",
                    ([int(i) for i in ids],))
        for rid, sr, nt, d in cur.fetchall():
            out[int(rid)] = (sr, nt, d)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--xc", type=int, nargs="*", default=[],
                    help="results.result_id values")
    ap.add_argument("--tf", type=int, nargs="*", default=[],
                    help="results_tf.result_id values")
    ap.add_argument("--pack", default=os.path.join(_ROOT, "engine", "data",
                                                   "packed_XC_TF.npz"))
    ap.add_argument("--npz", default=os.path.join(_ROOT, "engine", "data",
                                                  "joint_difficulty.npz"))
    ap.add_argument("--no-db", action="store_true",
                    help="skip the stored rating; pack and npz only")
    args = ap.parse_args()
    if not args.xc and not args.tf:
        sys.exit("give at least one --xc or --tf result id")

    import pair_engine as pe
    import run_joint as rj

    print(f"[explain] loading {args.pack}")
    cols = pe.loadPack(args.pack)
    cols = rj.sortRowsByAthlete(cols)
    keep = (cols["course"] >= 0) & (cols["norm"] > 0)
    print("[explain] rebuilding the design (the solve's own row order)")
    D, _athlete_pool, pool_names = rj.buildDesign(cols, keep)
    npz = _loadNpz(args.npz)
    if npz["delta"].size != D.n_cell or npz["race_effect"].size != D.n_race:
        sys.exit(f"[explain] {args.npz} does not fit this pack "
                 f"({npz['delta'].size} cells vs {D.n_cell}, "
                 f"{npz['race_effect'].size} races vs {D.n_race}): the "
                 f"pack was rebuilt after the solve, or the other way round")
    mu = np.asarray(npz["mu"], dtype=np.float64)
    print(f"[explain] level mu[TF] - mu[XC] = {mu[1] - mu[0]:+.5f}; pools "
          f"{list(pool_names)}")

    pos = np.full(keep.size, -1, dtype=np.int64)
    pos[keep] = np.arange(int(keep.sum()))
    keys = [str(k) for k in cols["course_keys"]]
    want = {("XC", i) for i in args.xc} | {("TF", i) for i in args.tf}
    all_ids = np.array(sorted({i for _, i in want}), dtype=np.int64)
    hit = np.flatnonzero(np.isin(cols["result_id"], all_ids))
    where = {}
    for i in hit:
        sp = "TF" if int(cols["sport"][i]) == 1 else "XC"
        where[(sp, int(cols["result_id"][i]))] = int(i)

    stored = {}
    if not args.no_db:
        stored["XC"] = _dbRows("XC", args.xc)
        stored["TF"] = _dbRows("TF", args.tf)

    for sp, rid in sorted(want):
        sr, nt, d = stored.get(sp, {}).get(rid, (None, None, None))
        head = f"\n{sp} {rid}"
        if d is not None:
            head += f"  {d}"
        if nt is not None:
            head += f"  norm {float(nt):.1f}s"
        head += (f"  stored rating {float(sr):.1f}" if sr is not None
                 else "  stored rating NULL" if not args.no_db else "")
        print(head)
        i = where.get((sp, rid))
        if i is None:
            print("   NOT IN THE PACK: the streaming query refused it (chair "
                  "athlete, flagged twin, no person or gender, out of band, "
                  "unrated distance). Any rating on it is fill_ratings' flat "
                  "K / normalized_time: no course adjustment.")
            if sr is not None and nt:
                print(f"   rating x norm = {float(sr) * float(nt):,.0f} "
                      f"(the pool's K)")
            continue
        norm = float(cols["norm"][i])
        if not keep[i]:
            print(f"   IN THE PACK, NO CELL (course {int(cols['course'][i])}): "
                  "a corrected division or a placeholder course votes on no "
                  "course and is not in the solve. Priced flat by "
                  "fill_ratings: no course adjustment, whatever the page's "
                  "difficulty column shows.")
            if sr is not None:
                print(f"   rating x norm = {float(sr) * norm:,.0f} "
                      f"(the pool's K)")
            continue
        t = rowTerms(D, npz, pos[i])
        disp = 100.0 * np.expm1(t["delta_anchored"])
        print(f"   cell {keys[t['cell']]}   delta raw {t['delta']:+.4f}   "
              f"display {disp:+.1f}% (anchored {t['delta_anchored']:+.4f})")
        print(f"   h {t['h']:.3f} at season rating {t['rating_season']:.1f}   "
              f"race-day u {t['u']:+.4f}   track distance offset "
              f"{t['dist']:+.4f}   -> applied to the time: "
              f"{t['effect']:+.4f} in log = x{np.exp(-t['effect']):.4f}, "
              f"i.e. {100.0 * np.expm1(t['effect']):+.1f}% against the "
              f"column's {disp:+.1f}%")
        print(f"   left out of the rating (by design): curve {t['curve']:+.4f}"
              f" (amp {t['amp']:.2f}), rust {t['rust']:+.4f}, sport offset "
              f"{t['beta']:+.4f}")
        if sr is not None:
            k = float(sr) * norm / np.exp(t["effect"])
            print(f"   rating x adjusted time = {k:,.0f}  (every solved row of "
                  "this athlete should share it: 100 x the career pool mean)")


if __name__ == "__main__":
    main()
