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
import contextlib
import os

FLAG = os.environ.get("XCP_MAINTENANCE_FLAG", "/var/tmp/racecast-maintenance")


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
