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
    for pool in sorted(POOLS):
        for sport in ("XC", "TF"):
            v = pool_view._poolConstant(pool, sport)
            shown = f"{v:.4f}" if v is not None else "unavailable"
            print(f"  {pool:<10} {sport}: {shown}")
    print(f"written to {pool_view._constFile()}")


if __name__ == "__main__":
    main()
