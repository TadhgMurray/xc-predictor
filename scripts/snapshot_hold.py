# Project: xc-predictor
# File Title: snapshot_hold.py
# Purpose: Hold one database snapshot open so other processes can read the
#          database exactly as it was at one moment (03 beside 04a).
"""Export a REPEATABLE READ snapshot and hold it until released.

    python scripts/snapshot_hold.py SNAPSHOT_FILE --release RELEASE_FILE
                                    [--max-seconds 14400] [--parent PID]

Opens one connection, BEGIN ISOLATION LEVEL REPEATABLE READ, SELECT
pg_export_snapshot(), writes the id to SNAPSHOT_FILE (atomically: a reader
never sees half an id) and keeps the transaction open. A process started
with XCP_PG_SNAPSHOT_FILE=SNAPSHOT_FILE then reads every transaction through
that snapshot (scripts/database.py, "Reading an exported snapshot").

It exits -- ending the transaction, so no new reader can import the
snapshot -- when RELEASE_FILE appears, on SIGTERM/SIGINT, when --parent (the
pipeline's shell) is gone, when its own connection dies, or after
--max-seconds. A reader still inside a transaction that imported the
snapshot keeps it; a reader that starts a NEW transaction after the holder
left gets "invalid snapshot identifier" and fails loudly, which is the
point: it never reads data the 04a linkers half moved.

★ WHY (2026-11-13): 03_pro_flag, 03b_age_bands and 03a_team_pool read who
  every row belongs to and the 04a linkers move person ids. With the
  snapshot they run beside 04a instead of before it (deploy/run_pipeline.sh,
  XCP_SNAPSHOT_03) and compute exactly what they computed before 04a began.
! A long-open snapshot holds back VACUUM's horizon for the whole time (dead
  rows 04a leaves are kept until release). Release it as soon as the
  readers are done; --max-seconds is the backstop.
"""
import argparse
import os
import signal
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import psycopg2                                                     # noqa: E402
import psycopg2.extensions                                          # noqa: E402

_STOP = []


def _onSignal(signum, _frame):
    _STOP.append(signum)


def _alive(pid):
    if not pid:
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _connect(dsn=None):
    if dsn:
        return psycopg2.connect(dsn, application_name="xcp-snapshot-hold")
    from config import PG_CONFIG
    return psycopg2.connect(**{**PG_CONFIG, "connect_timeout": 10,
                               "application_name": "xcp-snapshot-hold"})


def hold(out, release, max_seconds=4 * 3600, parent=None, dsn=None,
         poll=1.0, say=print):
    """Export, write `out`, hold until released. Returns the exit code."""
    conn = _connect(dsn)
    try:
        with conn.cursor() as cur:
            # an idle-in-transaction timeout (server or role) would end the
            # hold early and fail every reader's next transaction
            for setting in ("idle_in_transaction_session_timeout",
                            "statement_timeout"):
                try:
                    cur.execute(f"SET {setting} = 0")
                    conn.commit()
                except psycopg2.Error:
                    conn.rollback()
        conn.set_session(isolation_level=psycopg2.extensions.ISOLATION_LEVEL_REPEATABLE_READ)
        with conn.cursor() as cur:
            cur.execute("SELECT pg_export_snapshot(), now()")
            sid, at = cur.fetchone()
        tmp = f"{out}.tmp{os.getpid()}"
        with open(tmp, "w") as fh:
            fh.write(sid + "\n")
        os.replace(tmp, out)
        say(f"[snapshot] {sid} exported at {at:%H:%M:%S}, written to {out}; "
            f"held until {release} appears (max {max_seconds:.0f} s)", flush=True)
        t0 = time.monotonic()
        last_ping = t0
        while True:
            if _STOP:
                why = f"signal {_STOP[0]}"
                break
            if os.path.exists(release):
                why = "released"
                break
            if not _alive(parent):
                why = f"parent {parent} is gone"
                break
            if time.monotonic() - t0 > max_seconds:
                why = f"max {max_seconds:.0f} s reached"
                break
            if time.monotonic() - last_ping >= 30:
                # inside the same transaction: proves the hold is still alive
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
                last_ping = time.monotonic()
            time.sleep(poll)
        held = time.monotonic() - t0
        say(f"[snapshot] {sid} ended after {held:.0f} s ({why})", flush=True)
        return 0 if why == "released" else 3
    except psycopg2.Error as exc:
        say(f"[snapshot] the hold failed: {str(exc).strip()}", flush=True)
        return 2
    finally:
        try:
            if os.path.exists(out):
                os.remove(out)
        except OSError:
            pass
        try:
            conn.rollback()
            conn.close()
        except Exception:                                           # noqa: BLE001
            pass


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("snapshot_file")
    ap.add_argument("--release", required=True,
                    help="the hold ends when this file exists")
    ap.add_argument("--max-seconds", type=float, default=4 * 3600)
    ap.add_argument("--parent", type=int, default=0,
                    help="end the hold when this process is gone (0: never)")
    ap.add_argument("--dsn", default=os.environ.get("XCP_SNAPSHOT_DSN"),
                    help="connect here instead of scripts/config.py (tests)")
    args = ap.parse_args(argv)
    signal.signal(signal.SIGTERM, _onSignal)
    signal.signal(signal.SIGINT, _onSignal)
    try:
        os.remove(args.release)
    except OSError:
        pass
    return hold(args.snapshot_file, args.release, args.max_seconds,
                args.parent or None, args.dsn)


if __name__ == "__main__":
    sys.exit(main())
