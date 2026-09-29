"""The NCAA DI 2025 men's 10k was nuked by a 0.18 s/m "beyond any human"
bar -- a 30:00 10k (owner, 2026-09-29). The bar is the world record now, and
the drops it wrongly made are pardoned exactly from their own comments."""
import os
import sys

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in ("engine", "scripts"):
    sys.path.insert(0, os.path.join(_ROOT, _p))

import amnesty_division_drops as AD                              # noqa: E402

TEXT = """
_DISTANCE_DROP_XC = set()
_DISTANCE_DROP_XC.update({
    (27301, 1),  # 250 rows at 10000m: median pace 0.179 s/m at 10000 -- beyond any human; nothing rated to size it
    (26359, 1),  # 145 rows at 8047m: median pace 0.118 s/m at 8047 -- beyond any human; nothing rated to size it
    (555, 2),  # 40 rows at 5000m: survivors +40 off their own heads, implied 7100 snaps to no rung (err +9%)
    (25430, 1),  # 60 rows at 8047m: median pace 0.179 s/m at 8047 -- beyond any human; nothing rated to size it
    (26598, 0),  # 50 rows at 8047m: median pace 0.158 s/m at 8047 -- beyond any human; nothing rated to size it
    (105185, 414123),  # 90 rows at 5000m: median pace 0.178 s/m at 5000 -- beyond any human; nothing rated to size it
})
"""


def test_the_fast_bar_is_the_world_record():
    import find_dropped_divisions as F
    assert F.paceImpossible(1795.0, 10000.0) is None          # an NCAA median
    assert F.paceImpossible(951.0, 8047.0)                     # 15:51 "8k": impossible
    assert F.paceImpossible(9000.0, 5000.0)                    # a walk still is


def test_only_the_pace_bar_convictions_are_retried():
    convs = AD.convictions(TEXT)
    assert [(c[0], c[1]) for c in convs] == [(27301, 1), (26359, 1), (25430, 1),
                                             (26598, 0), (105185, 414123)]
    free, keep = AD.pardonable(convs)
    # NCAA DI 2025 and a 24:00 college 8k median: fields that really run that
    assert [(f[0], f[1]) for f in free] == [(27301, 1), (25430, 1)]
    # the owner's dry run: a 21:11 8k median and a 14:50 anet 5k median are
    # faster than any real field of their feed, and stay dropped
    assert [(k[0], k[1]) for k in keep] == [(26359, 1), (26598, 0), (105185, 414123)]


def test_a_feed_comes_from_the_rows_when_known():
    assert AD.feedOf((27301, 1)) == "tfrrs" and AD.feedOf((105185, 414123)) == "anet"
    assert AD.feedOf((27301, 1), {(27301, 1): {"anet", "tfrrs"}}) == "anet"


def test_the_pardon_block_runs_and_is_not_repeated():
    free, _keep = AD.pardonable(AD.convictions(TEXT))
    text = TEXT + AD.block(free)
    ns = {}
    exec(text, ns)
    assert ns["_DISTANCE_DROP_XC"] == {(26359, 1), (555, 2), (26598, 0), (105185, 414123)}
    assert "_DIVISION_DROP_PARDON" not in ns
    assert AD.alreadyPardoned(text) == {(27301, 1), (25430, 1)}
