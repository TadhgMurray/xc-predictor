#!/usr/bin/env python3
"""
race_bias.py -- which way the page's times miss, and where. Reads the
table model/race_rows.py wrote; no database, no model, a minute.

    /srv/venv/bin/python model/race_bias.py --rows /srv/pred/rows3.npz

★ WHY (owner, 2026-10-26: "why are the times so biased?"). The page's time
  for a runner is their rating form on the race's clock (race_rows.pageBase:
  base_abs). Its signed miss, ln(page / actual), says which way and by how
  much: + = the page said slower than they ran. Split by what the review
  names as causes -- the form's lag through a season (by month and by
  week of season), a college course with no cell (by has_cell and feed),
  level -- on the races with a real distance, from --from on.
  Medians per runner and per race (a race's median miss is its level miss;
  the day's luck lives there too, and averages out over many races).
"""
import argparse
import datetime
import math


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rows", required=True)
    ap.add_argument("--from", dest="lo", default="2024-08-01")
    a = ap.parse_args()

    import numpy as np
    z = np.load(a.rows, allow_pickle=False)
    feats = [str(f) for f in z["features"]]
    fi = {f: i for i, f in enumerate(feats)}
    X, race, day = z["X"], z["race"], z["day"]
    e = (z["base_abs"] - z["y_abs"]).astype(np.float64)       # ln(page / actual)
    keep = day >= datetime.date.fromisoformat(a.lo).toordinal()
    if "page_base" in fi:
        keep &= X[:, fi["page_base"]] > 0.5
    X, race, day, e = X[keep], race[keep], day[keep], e[keep]
    print(f"{len(e):,} runner-races in {len(np.unique(race)):,} races on the page's clock, "
          f"from {a.lo}\n")

    def table(title, key, order=None):
        groups = {}
        for i, k in enumerate(key):
            groups.setdefault(k, []).append(i)
        print(f"{title}: runners, races, median miss per runner, median per race "
              "(+ = the page said slower)")
        for g in (order or sorted(groups)):
            ix = np.array(groups.get(g, []))
            if len(ix) < 200:
                continue
            races = {}
            for i in ix:
                races.setdefault(int(race[i]), []).append(e[i])
            per_race = [float(np.median(v)) for v in races.values()]
            print(f"  {str(g):24} {len(ix):>9,} {len(races):>7,} "
                  f"{100 * (math.exp(np.median(e[ix])) - 1):+7.2f}% "
                  f"{100 * (math.exp(np.median(per_race)) - 1):+7.2f}%")
        print()

    names = {0: "elem", 1: "ms", 2: "hs", 3: "college", 4: "pro"}
    lv = X[:, fi["level"]]
    table("ALL", ["all"] * len(e))
    table("by level", [names.get(int(v), "?") if not math.isnan(v) else "?" for v in lv])
    table("by month", [datetime.date.fromordinal(int(d)).strftime("%Y-%m") for d in day])
    table("by week of season", [f"wk {int(w):02d}" if not math.isnan(w) else "?"
                                for w in X[:, fi["season_week"]]])
    table("by feed and course cell",
          [f"{'tfrrs' if t > 0.5 else 'anet'}, {'cell' if c > 0.5 else 'no cell'}"
           for t, c in zip(X[:, fi["is_tfrrs"]], X[:, fi["has_cell"]])])
    table("by runner", ["season opener" if o > 0.5 else "in season"
                        for o in X[:, fi["opener"]]])
    if "temp_c" in fi:
        t = X[:, fi["temp_c"]]
        table("by temperature at the hour",
              ["?" if math.isnan(v) else f"{int(v // 5 * 5):>3}-{int(v // 5 * 5) + 5} C" for v in t])


if __name__ == "__main__":
    main()
