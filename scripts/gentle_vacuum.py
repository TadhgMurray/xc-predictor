#!/usr/bin/env python3
"""
gentle_vacuum.py -- VACUUM (ANALYZE) the rebuilt boards tables at the
pipeline's pace, not autovacuum's. Pipeline step 18, the last one.

    python scripts/gentle_vacuum.py [--table ranking_results --table athlete_season]

★ WHY (owner, 2026-09-15). ranking_results is rebuilt from scratch every
  run: 61.6M rows, 23 GB, loaded into a shadow with autovacuum OFF (see
  build_ranking_results.createShadow) and swapped in. Left alone, Postgres
  starts an insert-triggered autovacuum on it minutes after the swap --
  a full pass over the heap and every index, at autovacuum's own cost
  settings, on the site's disk, while the pipeline's later steps are
  also running. That is the slowness that outlives a Ctrl-C.

  The table still needs the pass (the visibility map is what lets the
  boards' index-only scans skip the heap), so the pipeline does it
  itself, last, on one reniced quiet-mode backend with a cost delay
  that keeps it to a trickle: vacuum_cost_delay 20 ms at cost limit 200
  is roughly 10 MB/s of heap. Slow, and invisible.
"""
import argparse
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))
from database import getConn, dbQuiet                        # noqa: E402

DEFAULT_TABLES = ("ranking_results", "athlete_season")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--table", action="append", default=None)
    ap.add_argument("--cost-delay-ms", type=int, default=20 if dbQuiet() else 2)
    args = ap.parse_args()
    tables = args.table or list(DEFAULT_TABLES)
    with getConn() as conn:
        conn.autocommit = True                      # VACUUM cannot run in a transaction
        with conn.cursor() as cur:
            cur.execute("SET vacuum_cost_delay = %s", (f"{args.cost_delay_ms}ms",))
            cur.execute("SET vacuum_cost_limit = 200")
            for t in tables:
                cur.execute("SELECT to_regclass(%s)", (t,))
                if cur.fetchone()[0] is None:
                    print(f"  {t}: absent, skipped")
                    continue
                t0 = time.time()
                cur.execute(f"VACUUM (ANALYZE) {t}")
                print(f"  {t}: vacuumed and analysed in {(time.time() - t0) / 60:.1f} min "
                      f"(cost delay {args.cost_delay_ms} ms)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
