#!/usr/bin/env python3
# File: scripts/explain_stamp.py
# Purpose: Show the query PLAN for the tfrrs and anet athlete stamp UPDATEs, to see
#          whether they use the indexes or seq-scan. Uses a tiny 3-row VALUES list
#          so EXPLAIN is instant and NOTHING is modified (EXPLAIN doesn't execute
#          the UPDATE). Read-only.
import sys
sys.path.insert(0, "scripts")
from database import getConn


def _explain(cur, label, sql):
    print(f"\n--- {label} ---")
    cur.execute("EXPLAIN " + sql)
    for row in cur.fetchall():
        print("  " + row[0])


def main():
    with getConn() as conn:
        cur = conn.cursor()
        # tiny fake VALUES lists; EXPLAIN only plans, never writes.
        _explain(cur, "tfrrs stamp (native_id, partial index expected)", """
            UPDATE results_tf AS r SET person_id = v.canon
            FROM (VALUES (1::bigint, 2::bigint)) AS v(canon, key)
            WHERE r.native_id = v.key AND r.source = 'tfrrs'
        """)
        _explain(cur, "anet stamp (athlete_id)", """
            UPDATE results_tf AS r SET person_id = v.canon
            FROM (VALUES (1::bigint, 2::bigint)) AS v(canon, key)
            WHERE r.athlete_id = v.key AND r.source = 'anet'
        """)
    print("\n=== Look for 'Index Scan using idx_...' = fast; "
          "'Seq Scan on results_tf' = the slowness. ===")


if __name__ == "__main__":
    main()