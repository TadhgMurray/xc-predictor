"""
============================================================================
 pg_triage.py -- SEE what backends are doing, THEN cancel/terminate them
============================================================================

 THE SITUATION
 -------------
 A runaway script's queries need killing, and blind pg_terminate_backend
 felt like it did nothing. Three usual reasons, each visible in
 pg_stat_activity if you look BEFORE you shoot:

   (a) you killed the right pid but are watching the POOL -- getConn
       opens 5-250 connections, so the list is mostly idle bystanders
   (b) the backend is stuck in wait_event = ClientWrite: blocked sending
       rows to a frozen client. Kill the CLIENT (python process) first;
       the backend dies the moment its socket drops
   (c) terminate returns true meaning "signal SENT" -- the backend dies
       at its next interrupt check, which can lag

 KILL ORDER: client process first (Stop-Process python), then run this
 to sweep stragglers. Last resort if a backend survives termination for
 minutes: Restart-Service (WAL crash recovery makes it safe; all
 in-flight transactions roll back).

 Usage:
     python scripts/pg_triage.py                        # list only
     python scripts/pg_triage.py --cancel               # polite: cancel queries
     python scripts/pg_triage.py --terminate            # kill backends
     python scripts/pg_triage.py --like fuzzy --terminate   # only matching pids
============================================================================
"""

# ===========================================================================
# CHUNK 1: IMPORTS
# ===========================================================================

import argparse
import time

from database import getConn


# ===========================================================================
# CHUNK 2: LOOK -- the evidence listing
# ===========================================================================

def _listBackends(cur, like):
    # -----------------------------------------------------------------
    # Purpose:  every backend on this database EXCEPT ourselves, with
    #           the columns that distinguish the three failure stories:
    #             state       -- 'active' (running) / 'idle' (pool
    #                            bystander) / 'idle in transaction'
    #             wait_event  -- 'ClientWrite' = stuck sending to a
    #                            dead/frozen client (story b)
    #             runtime     -- how long the current query has run
    #             query       -- first 70 chars, enough to recognize
    # Arguments:
    #   cur  -- open cursor
    #   like -- optional substring; only backends whose query contains
    #           it (case-insensitive). None = all backends.
    # Output:  list of (pid, state, wait_type, wait_event, runtime,
    #          query) tuples.
    # -----------------------------------------------------------------
    cur.execute(
        "SELECT pid, state, wait_event_type, wait_event, "
        # now() - query_start = an INTERVAL; date_trunc drops the
        # microseconds so it prints readably
        "       date_trunc('second', now() - query_start) AS runtime, "
        # backend_type: 'client backend' vs 'parallel worker' -- the
        # column that makes a parallel TEAM visible as a team.
        # leader_pid (PG13+): a worker's leader; NULL for leaders.
        # KILL RULE: always target the LEADER -- killing a worker just
        # errors the leader (or wedges it waiting on IPC).
        "       backend_type, leader_pid, "
        "       left(query, 70) "
        "FROM pg_stat_activity "
        "WHERE datname = current_database() "
        "  AND pid <> pg_backend_pid() "        # never list (or kill!) ourselves
        "  AND (%s IS NULL OR query ILIKE '%%' || %s || '%%') "
        "ORDER BY state, runtime DESC NULLS LAST",
        (like, like),
    )
    return cur.fetchall()


def _printBackends(rows, label):
    # -----------------------------------------------------------------
    # Purpose:  the listing, headed by when it was taken. Workers print
    #           indented under an arrow to their leader so teams read
    #           as teams.
    # Arguments: rows (from _listBackends); label ('before'/'after').
    # Output:   None -- prints.
    # -----------------------------------------------------------------
    print(f"\n  --- {label}: {len(rows)} backend(s) ---")
    for pid, state, wtype, wevent, runtime, btype, leader, query in rows:
        wait = f"{wtype}/{wevent}" if wevent else "-"
        # a worker row shows who it belongs to; leaders show their type
        who = f"worker of {leader}" if leader else (btype or "-")
        print(f"  pid {pid:>6}  [{who:<18}] {state or '-':<8} wait={wait:<20} "
              f"run={str(runtime) or '-':<10} {query!r}")


# ===========================================================================
# CHUNK 3: SHOOT -- cancel (polite) or terminate (final)
# ===========================================================================

def _signalPids(cur, pids, terminate):
    # -----------------------------------------------------------------
    # Purpose:  send cancel or terminate to each pid. cancel stops the
    #           CURRENT QUERY (backend survives, goes idle); terminate
    #           kills the BACKEND (its client gets a dropped connection).
    #           Both return true = "signal sent", NOT "backend dead" --
    #           which is why the caller re-lists afterward.
    # Arguments:
    #   cur       -- open cursor
    #   pids      -- list of pids to signal
    #   terminate -- False = pg_cancel_backend, True = pg_terminate_backend
    # Output:  None -- prints one line per signal.
    # -----------------------------------------------------------------
    fn = "pg_terminate_backend" if terminate else "pg_cancel_backend"
    for pid in pids:
        # the function name comes from our two-literal choice above --
        # safe to interpolate; the pid is a value -> %s as always
        cur.execute(f"SELECT {fn}(%s)", (pid,))
        sent = cur.fetchone()[0]
        print(f"  {fn}({pid}) -> {'signal sent' if sent else 'NO SUCH PID'}")


# ===========================================================================
# CHUNK 4: MAIN -- look, (shoot, wait, look again)
# ===========================================================================

def main():
    parser = argparse.ArgumentParser(
        description="List Postgres backends with wait evidence; optionally kill.")
    parser.add_argument("--like", default=None,
                        help="only backends whose query contains this substring")
    parser.add_argument("--cancel", action="store_true",
                        help="pg_cancel_backend the listed pids (stop query, keep backend)")
    parser.add_argument("--terminate", action="store_true",
                        help="pg_terminate_backend the listed pids (kill backend)")
    args = parser.parse_args()

    with getConn() as conn:
        conn.autocommit = True         # signals should fire immediately, not queue
        cur = conn.cursor()

        rows = _listBackends(cur, args.like)
        _printBackends(rows, "before")

        if args.cancel or args.terminate:
            # only signal LEADERS actually doing something: killing a
            # parallel worker just errors/wedges its leader (kill the
            # leader and its workers die with it), and terminating idle
            # pool connections just makes the pool reopen them.
            targets = [r[0] for r in rows
                       if r[1] and r[1] != "idle" and r[6] is None]  # r[6] = leader_pid
            if not targets:
                print("\n  nothing non-idle to signal.")
            else:
                print()
                _signalPids(cur, targets, args.terminate)
                time.sleep(2)          # give the signals their interrupt check
                _printBackends(_listBackends(cur, args.like), "after")

        conn.autocommit = False        # restore before returning to the pool


if __name__ == "__main__":
    main()