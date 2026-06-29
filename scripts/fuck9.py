# Project: xc-predictor
# File:    scripts/unblock_meets.py
# Purpose: Clear the lock pileup wedging the launcher. A long diagnostic reader
#          holds ACCESS SHARE on `meets`; createTables' "ALTER TABLE meets ADD
#          COLUMN IF NOT EXISTS" waits for ACCESS EXCLUSIVE behind it and then
#          blocks everything else. Lists the active (non-idle) sessions with who
#          blocks whom, and on confirmation terminates them.
#
# Run with the launcher OFF.  python scripts/unblock_meets.py

import sys
sys.path.insert(0, "scripts")
from database import getConn


# main
# Purpose:   Show active sessions + blocking chain, then terminate on confirm.
def main() -> None:
    with getConn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT pid, state, wait_event_type,
                   pg_blocking_pids(pid) AS blocked_by,
                   left(query, 70)
            FROM pg_stat_activity
            WHERE datname = current_database()
              AND pid <> pg_backend_pid()
              AND state <> 'idle'
            ORDER BY pid
        """)
        rows = cur.fetchall()
        if not rows:
            print("no active sessions — nothing to clear.")
            return

        print("active sessions:")
        for pid, state, wevt, blocked_by, q in rows:
            print(f"  pid={pid}  state={state}  wait={wevt}  blocked_by={blocked_by}")
            print(f"      {q!r}")

        if input(f"terminate these {len(rows)} sessions? [y/N] ").strip().lower() != "y":
            print("aborted, nothing terminated.")
            return

        for pid, *_ in rows:
            cur.execute("SELECT pg_terminate_backend(%s)", (pid,))
        conn.commit()
        print(f"terminated {len(rows)} sessions. Start ONE launcher now.")


if __name__ == "__main__":
    main()