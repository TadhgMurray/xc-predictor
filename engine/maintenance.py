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
