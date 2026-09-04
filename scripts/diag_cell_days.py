"""
diag_cell_days.py -- every race day of one cell, with what the joint solve
made of it: rows, the field's mean season rating, the field's mean 5K-
equivalent time, the pool mix, and the race-day u. For a cell whose
difficulty looks absurd (Great Park at +20%, Mt. SAC at +20%) this is the
question: which DAYS are dragging it, and do they look like the same race?

    python scripts/diag_cell_days.py --cell XC:8389:d4800
    python scripts/diag_cell_days.py --xc 66864064        # the cell of that row

A day whose field rates like the others but runs 20% slower is a distance
label (issue 111); a day that rates 15 points weaker and runs slower is a
different field and the solve should handle it; a u of -0.27 on the meet
that defines the venue means the cell is mostly OTHER days.
"""
import argparse
import os
import sys
from datetime import date, timedelta

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "engine"))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cell", default=None, help="course key, e.g. XC:8389:d4800")
    ap.add_argument("--xc", type=int, default=None, help="an XC result id: its cell")
    ap.add_argument("--tf", type=int, default=None, help="a TF result id: its cell")
    ap.add_argument("--pack", default=os.path.join(_ROOT, "engine", "data",
                                                   "packed_XC_TF.npz"))
    ap.add_argument("--npz", default=os.path.join(_ROOT, "engine", "data",
                                                  "joint_difficulty.npz"))
    args = ap.parse_args()
    import pair_engine as pe
    import run_joint as rj

    cols = pe.loadPack(args.pack)
    cols = rj.sortRowsByAthlete(cols)
    keep = (cols["course"] >= 0) & (cols["norm"] > 0)
    D, athlete_pool, pool_names = rj.buildDesign(cols, keep)
    with np.load(args.npz, allow_pickle=False) as z:
        npz = {k: z[k] for k in z.files}
    keys = [str(k) for k in cols["course_keys"]]

    if args.cell is None:
        rid = args.xc if args.xc is not None else args.tf
        sp = 0 if args.xc is not None else 1
        hit = np.flatnonzero((cols["result_id"] == rid) & (cols["sport"] == sp))
        if hit.size == 0:
            sys.exit(f"result {rid} is not in the pack")
        c = int(cols["course"][hit[0]])
        if c < 0:
            sys.exit(f"result {rid} has no cell (corrected division)")
        args.cell = keys[c]
    try:
        c = keys.index(args.cell)
    except ValueError:
        sys.exit(f"no cell {args.cell!r} in the pack")

    rows = np.flatnonzero(D.cell == c)
    delta = float(npz["delta"][c])
    print(f"cell {args.cell}: {rows.size:,} rows, delta raw {delta:+.4f} "
          f"({np.expm1(delta):+.1%}), {np.unique(D.race[rows]).size} race days")
    days_ago = np.asarray(cols["days"][keep])
    norm = np.asarray(cols["norm"][keep], dtype=np.float64)
    rating = npz["rating"] if "rating" in npz else None
    u = npz["race_effect"]
    today = date.today()
    print(f"  {'date':>10} {'rows':>6} {'field':>6} {'mean 5K-eq':>10} "
          f"{'u':>8} {'net':>7}  pools")
    per_race = {}
    for j in rows:
        per_race.setdefault(int(D.race[j]), []).append(int(j))
    lines = []
    for r, js_ in per_race.items():
        js_ = np.array(js_)
        d = today - timedelta(days=int(np.median(days_ago[js_])))
        fld = float(np.mean(rating[D.athlete[js_]])) if rating is not None else float("nan")
        m5 = float(np.mean(norm[js_]))
        pools = np.bincount(athlete_pool[D.athlete[js_]], minlength=len(pool_names))
        top = ", ".join(f"{pool_names[i]} {pools[i]}" for i in np.argsort(-pools)[:2]
                        if pools[i] > 0)
        lines.append((d, js_.size, fld, m5, float(u[r]), delta + float(u[r]), top))
    lines.sort()
    for d, n, fld, m5, uu, net, top in lines:
        print(f"  {d.isoformat():>10} {n:>6,} {fld:>6.1f} "
              f"{int(m5 // 60):>6}:{m5 % 60:04.1f} {uu:>+8.4f} {net:>+7.3f}  {top}")
    print("  field = mean season rating of the day's runners; mean 5K-eq = "
          "their mean normalised time; u = the race-day effect; net = delta "
          "+ u, what the rating applied (before the tilt). The same field "
          "at very different 5K-eq times is a distance label, not a course.")


if __name__ == "__main__":
    main()
