# Project: xc-predictor / engine
# File:    maintenance.py
# Purpose: The site's maintenance flag, dropped around a table swap.
#
# ★ WHY (issue 300). A swap takes ACCESS EXCLUSIVE on a table every page
#   reads; for the seconds it takes, and for the rounds a busy queue makes
#   it wait, a page request either queues or fails. With the flag file
#   present the site answers every page 503 with Retry-After instead: a
#   visitor sees "back in a moment", a crawler keeps the page indexed and
#   returns. The flag is a file, not a table row, so it works while the
#   database is the thing being locked. racecast/app.py reads the same
#   path (XCP_MAINTENANCE_FLAG, default /var/tmp/racecast-maintenance).
#
# ⚠ A FLAG THAT OUTLIVES ITS PROCESS TAKES THE SITE DOWN FOR GOOD. The
#   removal below is a `finally`, so it survives an exception -- but not
#   SIGKILL, and not the box losing power. A pipeline step killed mid-swap
#   (kill -9 on a query that would not die is a normal thing to do here)
#   leaves the file behind, and from that moment EVERY page is a 503 until
#   a human notices and deletes it. That is not a hypothetical: it is
#   silent, it looks exactly like "the site is down", and a crawler that
#   gets 503 for days drops the pages it had indexed.
#
#   So the flag EXPIRES. isMaintenance() honours it only while it is
#   younger than MAX_AGE_S; an older file is treated as debris and the
#   site serves normally. The longest honest hold is merge_column's siege
#   (20 rounds x 30s plus the swap itself, ~11 min), and heartbeat() keeps
#   a legitimately long hold alive by refreshing mtime, so the ceiling
#   only ever fires on a flag whose owner is gone.
import contextlib
import os
import time

FLAG = os.environ.get("XCP_MAINTENANCE_FLAG", "/var/tmp/racecast-maintenance")

# ! Comfortably above the ~11 min siege ceiling, comfortably below the days
#   of 503 it takes Google to drop a page. A live holder refreshes it.
MAX_AGE_S = float(os.environ.get("XCP_MAINTENANCE_MAX_S", "1800"))


def flagAge(now=None):
    """Seconds since the flag was written, or None if there is no flag."""
    try:
        mtime = os.stat(FLAG).st_mtime
    except OSError:
        return None
    return max(0.0, (time.time() if now is None else now) - mtime)


def isMaintenance(now=None):
    """True while the site should answer 503. A flag older than MAX_AGE_S is
    debris from a killed process, not a swap in progress -- serve the site."""
    age = flagAge(now)
    return age is not None and age < MAX_AGE_S


def heartbeat():
    """Refresh the flag's mtime so a long but live hold does not expire."""
    try:
        os.utime(FLAG, None)
    except OSError:
        pass


def clearStale(now=None):
    """Remove a flag left behind by a killed process. Returns its age if one
    was removed, else None. Safe to call at the start of a pipeline run: a
    flag that old cannot belong to a swap that is still running."""
    age = flagAge(now)
    if age is None or age < MAX_AGE_S:
        return None
    try:
        os.remove(FLAG)
    except OSError:
        return None
    return age


@contextlib.contextmanager
def siteMaintenance(why=""):
    """The site is in maintenance for the block: the flag exists on entry
    and is gone on exit, however the block ends."""
    try:
        with open(FLAG, "w") as f:
            f.write(why or "table swap")
        os.chmod(FLAG, 0o644)
    except OSError as exc:                            # the site just stays up
        print(f"[maintenance] could not drop {FLAG}: {exc}", flush=True)
    try:
        yield
    finally:
        try:
            os.remove(FLAG)
        except OSError:
            pass


# ★★ QUIET FIRST, FLAG LAST (sweep 2026-10-10, D5/D6). Every swap used to
#    raise the flag BEFORE its first try and hold it through every retry --
#    so a pipeline swap behind one slow reader 503'd the whole site for
#    minutes (ranking_results: up to 20 tries x 15 s; merge_column's siege:
#    ~11 min), when most swaps take the lock on the first try in
#    milliseconds and need no flag at all. And the plain swaps (dbfast)
#    waited 5 s per try: exactly the site's lock_timeout
#    (deploy/server_setup.sh XCP_DB_LOCK_TIMEOUT_MS=5000), so a page that
#    queued behind a waiting swap failed at the same moment the swap gave
#    up -- the swap's patience was spent as the site's errors.
#
#  ★ NOW: many short tries with the site UP, and the flag only as the
#    fallback. Each quiet try waits QUIET_LOCK_MS for the lock -- a fifth of
#    the site's 5 s, so a page queued behind it is delayed at most a second
#    and never reaches its own timeout -- and between tries the swap holds
#    nothing for QUIET_PAUSE_S, so the queue drains. QUIET_TRIES of them is
#    ~4.5 minutes of looking for a gap; a busy table almost always has one.
#    Only when it never does is the flag raised and the caller's old,
#    patient path run under it (new page requests then stop at the 503 and
#    stop arriving at the table, which is what lets that path win).
#
#  ! ALL-OR-NOTHING PER TRY STAYS THE CALLER'S JOB: try_once does one whole
#    swap in ONE transaction under SET LOCAL lock_timeout = <ms> and commits
#    it; LockNotAvailable or DeadlockDetected out of it means "a reader was
#    in the way" (the two are the same event, see test_pipeline_failures),
#    and this rolls back and tries again.
QUIET_LOCK_MS = int(os.environ.get("XCP_SWAP_QUIET_LOCK_MS", "1000"))
QUIET_TRIES = int(os.environ.get("XCP_SWAP_QUIET_TRIES", "90"))
QUIET_PAUSE_S = float(os.environ.get("XCP_SWAP_QUIET_PAUSE_S", "2"))


def swapQuietlyFirst(conn, try_once, label, flagged, tries=None,
                     lock_ms=None, pause=None, log=print, sleep=time.sleep):
    """Run try_once(lock_ms) up to `tries` times with the site up; return
    its result on the first win. When every try loses to a reader, raise
    the maintenance flag and return flagged() -- the caller's old path --
    with the flag held for exactly that long."""
    import psycopg2.errors as _pge
    busy = (_pge.LockNotAvailable, _pge.DeadlockDetected)
    tries = QUIET_TRIES if tries is None else tries
    lock_ms = QUIET_LOCK_MS if lock_ms is None else lock_ms
    pause = QUIET_PAUSE_S if pause is None else pause
    for attempt in range(1, tries + 1):
        try:
            return try_once(lock_ms)
        except busy:
            conn.rollback()
            # ! A LINE NOW AND THEN, NOT NINETY. The tries are short on
            #   purpose; the log should say a swap is waiting, not scroll.
            if attempt == 1 or attempt % 15 == 0:
                log(f"  {label}: being read; quiet try {attempt}/{tries} "
                    f"(site up, {lock_ms}ms per try)", flush=True)
            sleep(pause)
    log(f"  {label}: {tries} quiet tries lost to readers -- raising the "
        f"maintenance flag for the rest of the swap", flush=True)
    with siteMaintenance(f"swap {label}"):
        return flagged()
