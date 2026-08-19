#!/usr/bin/env python3
# File: scripts/whats_running.py
# Purpose: Show what Postgres is actively doing right now and for how long, so a
#          slow step can be told apart from a true stall. Read-only.
import sys
sys.path.insert(0, "scripts")
from database import getConn
 
with getConn() as conn:
    cur = conn.cursor()
    cur.execute("""
        SELECT pid,
               state,
               now() - query_start AS running_for,
               wait_event_type,
               left(query, 90) AS query
        FROM pg_stat_activity
        WHERE state <> 'idle' AND query NOT LIKE '%pg_stat_activity%'
        ORDER BY query_start
    """)
    rows = cur.fetchall()
    if not rows:
        print("nothing active -- Postgres is idle.")
    for pid, state, running, wait, q in rows:
        print(f"pid {pid} | {state} | running {running} | wait={wait} | {q}")
 