#!/usr/bin/env python3
# File: scripts/kill_backends.py
# Purpose: Terminate stuck Postgres backends by pid. Killing the Python client
#          doesn't always stop the server-side query (especially when it's blocked
#          on a lock), so we cancel them at the DB level. Postgres then rolls back
#          their transactions -- safe, since the merge stamp is transactional.
#
# Usage:  python scripts/kill_backends.py 23680 35608
#         (pass the pids whats_running.py showed; with no args it terminates ALL
#          non-idle backends except this one.)
import sys
sys.path.insert(0, "scripts")
from database import getConn


def main():
    pids = [int(p) for p in sys.argv[1:]]
    with getConn() as conn:
        conn.autocommit = True
        cur = conn.cursor()
        if pids:
            for pid in pids:
                cur.execute("SELECT pg_terminate_backend(%s)", (pid,))
                ok = cur.fetchone()[0]
                print(f"  pid {pid}: {'terminated' if ok else 'not found / already gone'}")
        else:
            # no pids given: terminate every active non-idle backend but ourselves
            cur.execute("""
                SELECT pid FROM pg_stat_activity
                WHERE state <> 'idle' AND pid <> pg_backend_pid()
                  AND query NOT LIKE '%pg_stat_activity%'
            """)
            targets = [r[0] for r in cur.fetchall()]
            if not targets:
                print("  nothing active to terminate.")
            for pid in targets:
                cur.execute("SELECT pg_terminate_backend(%s)", (pid,))
                print(f"  pid {pid}: terminated")
    print("done.")


if __name__ == "__main__":
    main()