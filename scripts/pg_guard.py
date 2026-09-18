# Project: xc-predictor / scripts
# File:    pg_guard.py
# Purpose: a read-only diagnostic must never be able to take the box down.
#
# ⚠⚠ WHY THIS EXISTS (owner, 2026-09-18):
#
#     psycopg2.errors.DiskFull: could not write to file
#     "base/pgsql_tmp/pgsql_tmp4056952.593": No space left on device
#
#    That was diag_indoor_level -- a READ-ONLY diagnostic -- filling the
#    server's temp space while an 11-hour scrape was running. Postgres frees
#    the temp files when the query aborts, so the damage was transient, but it
#    could equally have been the scraper that hit the wall first and died.
#
# ★ SO EVERY HEAVY DIAGNOSTIC OPENS WITH THESE. They are per-session settings,
#   so they bind only the connection that asks and cannot affect the scrapers:
#
#     temp_file_limit    the query DIES at this much spill instead of filling
#                        the disk. This is the one that matters.
#     statement_timeout  a diagnostic that runs for an hour is a diagnostic
#                        nobody is reading; better to fail and be narrowed.
#     work_mem           more memory per sort means less spilling in the first
#                        place, and a diagnostic is one connection, not 250.
#
# ! LIMITS, NOT OPTIMISATIONS. They do not make a bad query good; they make it
#   fail early and visibly, on its own, which is the behaviour a diagnostic
#   should have.
import os

TEMP_FILE_LIMIT = os.environ.get("XCP_DIAG_TEMP_LIMIT", "8GB")
STATEMENT_TIMEOUT = os.environ.get("XCP_DIAG_TIMEOUT", "30min")
WORK_MEM = os.environ.get("XCP_DIAG_WORK_MEM", "256MB")


def guard(cur, verbose=True, temp_limit=None, timeout=None, work_mem=None):
    """Bound this ONE connection's resource use. Returns what it set.

    Each SET is attempted separately: a managed Postgres that refuses one of
    them should not cost us the other two.
    """
    want = (("temp_file_limit", temp_limit or TEMP_FILE_LIMIT),
            ("statement_timeout", timeout or STATEMENT_TIMEOUT),
            ("work_mem", work_mem or WORK_MEM))
    got = {}
    for name, value in want:
        try:
            cur.execute(f"SET {name} = %s", (value,))
            got[name] = value
        except Exception as exc:                       # noqa: BLE001
            if verbose:
                print(f"    [guard] could not set {name}={value} "
                      f"({type(exc).__name__})", flush=True)
    if verbose and got:
        print("    [guard] " + ", ".join(f"{k}={v}" for k, v in got.items())
              + "  (this connection only; a spill past temp_file_limit "
                "aborts the query instead of the disk)", flush=True)
    return got
