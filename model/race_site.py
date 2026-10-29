#!/usr/bin/env python3
"""
race_site.py -- what the site's own predictor (the transformer, and
`blend` on top of it) said for a sample of the validation season's races,
saved so race_trees.py --site can put every baseline side by side on the
same races. READ ONLY on the database; writes one .npz.

    set -a; . /etc/xc-predictor.env; set +a; P=/srv/venv/bin/python
    $P -u model/race_site.py --rows /srv/pred/rows6.npz --out /srv/pred/site6.npz --races 1000

★ WHY (owner, 2026-10-27: "I really need a baseline for comparison"). The
  table carries the ratings (the site's ORDER today: blend moves a field by
  one factor, which no order can see) but not the transformer, so not the
  site's TIMES. This runs the transformer the way the page does --
  predict._predictTimes on the finished race, every runner's history cut
  the day before -- for race-morning races with a known distance, sampled
  from the validation season. 2-3 s a race.
"""
import argparse
import ast
import datetime
import os
import random
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for sub in ("racecast", "scripts", "engine", "model"):
    p = os.path.join(_ROOT, sub)
    if p not in sys.path:
        sys.path.insert(0, p)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rows", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--val-from", default="2024-08-01")
    ap.add_argument("--races", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()

    import numpy as np
    import psycopg2.extras
    import predict as P
    from database import getConn

    z = np.load(a.rows, allow_pickle=False)
    feats = [str(f) for f in z["features"]]
    fi = {f: i for i, f in enumerate(feats)}
    race, day, pid, X = z["race"], z["day"], z["pid"], z["X"]
    cut = datetime.date.fromisoformat(a.val_from).toordinal()
    ok = (day >= cut) & (X[:, fi["page_base"]] > 0.5)
    if "lead_days" in fi:
        ok &= np.nan_to_num(X[:, fi["lead_days"]], nan=0.0) == 0
    cand = sorted(set(race[ok].tolist()))
    random.Random(a.seed).shuffle(cand)
    pick = cand[:a.races]
    keys = z["race_keys"]
    rows_of = {}
    for i in np.flatnonzero(np.isin(race, pick)):
        rows_of.setdefault(int(race[i]), []).append(int(pid[i]))
    status = P.modelStatus()
    print(f"{len(pick):,} races sampled from {len(cand):,}; model: "
          f"{'available' if status.get('available') else status.get('reason')}", flush=True)

    out_key, out_pid, out_s = [], [], []
    with getConn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SET statement_timeout = '600s'")
        for n, rid in enumerate(pick, 1):
            k = ast.literal_eval(str(keys[rid]))
            target = {"mode": "rerun_exact", "sport": "XC", "meet_id": k[0],
                      "div_id": k[1], "source": k[2], "history_before": k[3]}
            ids = rows_of[rid]
            try:
                spec = P._targetSpec(cur, target)
                got = P._predictTimes(cur, ids, target, spec=spec)
            except Exception as exc:                 # noqa: BLE001
                conn.rollback()
                print(f"  {k}: {exc}", flush=True)
                continue
            conn.rollback()
            for p, e in zip(ids, got):
                if e.get("seconds") and e.get("is_race_time"):
                    out_key.append(str(keys[rid]))
                    out_pid.append(p)
                    out_s.append(float(e["seconds"]))
            if n % 50 == 0 or n == len(pick):
                print(f"  [{n}/{len(pick)}] {len(out_s):,} runners priced", flush=True)
    np.savez_compressed(a.out, key=np.array(out_key), pid=np.array(out_pid, dtype=np.int64),
                        model_s=np.array(out_s, dtype=np.float64),
                        model_version=np.array([str(status)]))
    print(f"wrote {a.out}: {len(out_s):,} runners in {len(set(out_key)):,} races")


if __name__ == "__main__":
    main()
