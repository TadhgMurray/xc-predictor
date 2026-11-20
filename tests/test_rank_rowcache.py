"""prepareRow's remembered lookups give the uncached answers (2026-11-20).

    python -m pytest -q tests/test_rank_rowcache.py
"""
import math
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in ("scripts", "racecast", "engine"):
    sys.path.insert(0, os.path.join(_ROOT, _d))
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
os.environ.setdefault("XCP_DB_QUIET", "1")

import build_ranking_results as B                                # noqa: E402
from record_pace import impossibleRow                            # noqa: E402


def test_cached_lookups_match_the_functions():
    pools = [None, "", "hs_m", "hs_f|TF", "ms_f", "elem_m", "college_m",
             "college_f|XC", "pro_m", "open_m", "hs_unknown_gender", "x"]
    dists = [None, 0, -5, 60, 100.0, 400, 800, 1609.34, 3200, 5000, 10000,
             42195, 99999, "5000", "bad", math.nan]
    times = [None, 0, -1, 6.5, 50, 110.0, 240, 600, 900, 1500, 9000, "x", math.nan]
    for _ in range(2):                              # cold, then from the cache
        for pool in pools:
            assert B.isRankablePool(pool) == B._isRankable(pool) if pool else \
                B.isRankablePool(pool) is False
            for d in dists:
                for sex in ("M", "F", None, "f", "x"):
                    for t in times:
                        assert (B._cached_impossibleRow(t, d, sex, pool)
                                == impossibleRow(t, d, sex, pool)), (t, d, sex, pool)
    assert B._homeStateOf("UNATTACHED (TX)", "WI", "hs_m") == "WI"
