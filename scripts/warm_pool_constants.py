"""
warm_pool_constants.py -- compute every HS-view pool constant, to disk.

Pipeline step 13b. The twenty pool x sport constants used to be sampled
lazily by the first page that needed them (~6s on the first home page of
a fresh process); pool_view persists them to a JSON sidecar, and this
computes the full set at pipeline time so no viewer ever pays for one.

    python scripts/warm_pool_constants.py
"""

import sys

sys.path.insert(0, "scripts")
sys.path.insert(0, "racecast")

from rankings import POOLS       # noqa: E402
import pool_view                 # noqa: E402


def main():
    # ⚠ REFRESH, NOT READ (2026-09-25). This used to call _poolConstant,
    #   which answers from the week-old sidecar -- so the step after a solve
    #   re-saved the previous solve's constants. refreshConstants clears the
    #   cache and measures the database as this solve left it.
    stale = 0
    for pool, sport, old, new, kept in pool_view.refreshConstants(sorted(POOLS)):
        o = f"{old:.4f}" if old is not None else "--"
        n = f"{new:.4f}" if new is not None else "unavailable"
        note = "  ⚠ SAMPLE FAILED, previous value kept" if kept else ""
        stale += kept
        print(f"  {pool:<10} {sport}: {o:>10} -> {n}{note}")
    print(f"written to {pool_view._constFile()}"
          + (f"  ({stale} kept from before -- see above)" if stale else ""))
    print("  ! restart the site (systemctl restart xc-predictor): running "
          "workers hold the old constants in memory.")


if __name__ == "__main__":
    main()
