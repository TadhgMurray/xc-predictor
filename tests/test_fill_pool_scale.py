"""The nightly fill prices a row on the scale of the pool it prices it in
(owner, 2026-11-04: Michelangelo Durante, a ninth grader's 17:39.5 for 5000 m
written on the middle school scale and priced hs_m -- 186.0).

    python -m pytest tests/test_fill_pool_scale.py -q
"""
import os
import sys
from collections import namedtuple

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in ("scripts", "engine", "racecast"):
    sys.path.insert(0, os.path.join(ROOT, p))

Row = namedtuple("Row", "result_id time_seconds distance normalized_time")


def _mods():
    try:
        import fill_ratings as F
        from normalize_distance import normalizeTime
        if not normalizeTime(1059.5, 5000.0, "hs_m", "XC"):
            raise RuntimeError("no distance artifact")
        return F, normalizeTime
    except Exception as e:                                  # noqa: BLE001
        pytest.skip(f"engine artifacts absent: {e}")


def test_a_row_on_the_ms_scale_moves_to_hs_and_keeps_its_corrections():
    F, nt = _mods()
    corr = 1.0071                                        # the weather credit inside it
    stored = nt(1059.5, 5000.0, "ms_m", "XC") / corr
    want = nt(1059.5, 5000.0, "hs_m", "XC") / corr
    if abs(stored / want - 1) < 0.10:
        pytest.skip("pools share one anchor here (by ability): nothing to move")
    got, moved = F.onPoolScale(Row(1, 1059.5, 5000.0, stored), "hs_m|XC", "XC", stored)
    assert moved
    assert abs(got / want - 1) < 1e-3


def test_a_right_row_and_an_unnamed_scale_are_left_alone():
    F, nt = _mods()
    right = nt(1059.5, 5000.0, "hs_m", "XC")
    got, moved = F.onPoolScale(Row(1, 1059.5, 5000.0, right), "hs_m", "XC", right)
    assert not moved and got == right
    junk = right * 1.35                                  # on no pool's scale
    got, moved = F.onPoolScale(Row(1, 1059.5, 5000.0, junk), "hs_m", "XC", junk)
    assert not moved and got == junk
    # no distance or no time: nothing to check against
    got, moved = F.onPoolScale(Row(1, 1059.5, None, junk), "hs_m", "XC", junk)
    assert not moved


def test_wired_into_the_fill_and_the_nightly():
    src = open(os.path.join(ROOT, "engine", "fill_ratings.py")).read()
    i = src.index("def pairs():")
    assert "onPoolScale(row, pool, sport" in src[i:i + 3000]
    assert "_writeNt(conn, sport, moved)" in src
    night = open(os.path.join(ROOT, "deploy", "nightly_update.sh")).read()
    j = night.index("step 09b_fill_venue")
    assert "--recheck-since" in night[j:j + 300]
