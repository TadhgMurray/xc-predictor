#!/usr/bin/env python3
"""
diag_backfill_row.py -- why the backfill gives a row no normalized_time.
READ-ONLY: builds the backfill's own lookups and runs its own per-row
classifier (backfill_normalize._makeRowFn) on the rows asked for.

    cd /srv/xc-predictor && /srv/venv/bin/python scripts/diag_backfill_row.py 60883789 60883793
    ... --sport TF 123456

★ WHY (owner, 2026-10-29: Rocky Hansen at UNCG, 2025-08-29). Two runners'
  rows in a division whose twelve other finishers were normalised and
  rated had normalized_time NULL every run, with a corroborated college
  grade verdict and the season's other races pooled college_m. The pack
  only drops rows with no normalized_time, so the question is the
  backfill's, and its classifier names the reason -- this asks it
  directly instead of reading thirty rules by hand.

  Prints per row: the skip reason (or the value it would write), the
  distance it resolved, and the cross-feed facts the twin rules read
  (canon_meet_id, whether a tfrrs twin is matched, whether the other feed's
  copy sits in a dropped division).
"""
import argparse
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(_ROOT)                       # backfill_normalize puts engine/ and scripts/ on the path relatively
for _p in ("backfill", "engine", "scripts"):
    sys.path.insert(0, os.path.join(_ROOT, _p))


def main(ids, sport="XC"):
    import backfill_normalize as B
    from database import getConn
    cfg = B._configFor(sport, "copy")
    with getConn() as conn:
        (geom_idx, genders, season_levels, meet_distances, tfrrs_distances,
         matched_twins, canon_distances, wx_idx,
         wheel_divs, wheel_persons, wheel_athletes, dropped_twins,
         virtual_meets) = B._buildLookups(conn, cfg)
        fn = B._makeRowFn(cfg, geom_idx, genders, season_levels,
                          meet_distances, tfrrs_distances, matched_twins,
                          canon_distances, wx_idx, wheel_divs=wheel_divs,
                          wheel_persons=wheel_persons,
                          wheel_athletes=wheel_athletes,
                          dropped_twins=dropped_twins,
                          virtual_meets=virtual_meets)
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.age_band_result') IS NOT NULL")
            age_band = bool(cur.fetchone()[0])
            cur.execute("""SELECT 1 FROM information_schema.columns
                           WHERE table_name = %s AND column_name = 'rating_pool'""",
                        (cfg.table,))
            rating_pool = cur.fetchone() is not None
            cur.execute(B._streamSQL(cfg, age_band, rating_pool)
                        + "\n        WHERE r.result_id = ANY(%s)", (list(ids),))
            rows = cur.fetchall()
        conn.rollback()
    print(f"\n[row] {len(rows)} of {len(ids)} rows found in {cfg.table}\n")
    for row in rows:
        rid, value, reason, trace = fn(row)
        pid, canon = row[B._PERSON], row[B._CANON]
        print(f"  result {rid}  person {pid}  {row[B._SRC]} meet {row[B._MEET]} div {row[B._DIV]}  "
              f"date {row[B._DATE]}  grade {row[B._GRADE]!r}  time {row[B._TIME]}")
        print(f"    -> {reason}" + (f", value {value:.2f}" if value is not None else ""))
        if trace:
            print(f"    trace: {trace}")
        print(f"    canon_meet_id {canon}; tfrrs twin matched "
              f"{(pid, canon) in matched_twins if canon is not None else '-'}; "
              f"other feed's copy in a dropped division "
              f"{dropped_twins.get((pid, canon)) if canon is not None else '-'}")
        print()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("ids", nargs="+", type=int)
    ap.add_argument("--sport", default="XC", choices=("XC", "TF"))
    a = ap.parse_args()
    main(a.ids, a.sport)
