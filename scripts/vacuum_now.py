#!/usr/bin/env python3
# File: scripts/vacuum_now.py
# Purpose: Run VACUUM ANALYZE at FULL speed on the result tables after the big
#          merge update. Autovacuum is throttled (cost-delay) and crawls for hours
#          on a 193M-row table; a MANUAL vacuum ignores that throttle and blasts
#          through. Also refreshes planner stats so later queries plan well.
#
#          Must run OUTSIDE a transaction (VACUUM can't run in one), so this sets
#          autocommit. Run it with nothing else hitting these tables.
import sys, time
sys.path.insert(0, "scripts")
from database import getConn

with getConn() as conn:
    conn.autocommit = True          # VACUUM cannot run inside a transaction
    cur = conn.cursor()
    for table in ("results_tf", "results"):
        print(f"vacuuming {table} (full speed) ...", flush=True)
        t0 = time.time()
        cur.execute(f"VACUUM (ANALYZE) {table}")
        print(f"  done in {time.time()-t0:.0f}s", flush=True)
print("all done -- tables clean and analyzed.")