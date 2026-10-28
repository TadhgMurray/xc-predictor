# The listing behind XCP_DAY_GUARD_NEAR_ZERO (scripts/diag_day_guard_swaps.py)
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import diag_day_guard_swaps as D                               # noqa: E402


def test_berrien_would_swap_and_midlothian_would_not():
    # Berrien 2026-09-08: the two varsity races flagged, 179 of 393 rows
    assert D.wouldSwap(179, 393, -0.0245, 0.142, [-0.024, -0.025])
    # Midlothian: three of eight flagged, 344 of 1,091 rows -- a clear majority
    assert not D.wouldSwap(344, 1091, 0.47, 0.0, [0.46, 0.47, 0.48])
    # flagged side further from zero than the day: no swap
    assert not D.wouldSwap(179, 393, 0.20, 0.05, [0.20, 0.21])
    # flagged side scattered (one long, one short): no single reading
    assert not D.wouldSwap(179, 393, 0.02, 0.14, [0.20, -0.18])
    # the flagged side not itself a believable day (North County, -11.8%)
    assert not D.wouldSwap(116, 272, -0.118, 0.415, [-0.11, -0.125])
