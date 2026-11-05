"""field_guard: a whole field far above its own seasons is pulled down to a
believable peak, whatever the cause (owner, 2026-11-05)."""
import math
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "engine"))
import field_guard as FG                                      # noqa: E402


def _season(pid, here, div, others=(100.0, 101.0, 99.0, 100.0)):
    rows = [(f"{pid}-h", pid, 2026, "2026-10-30", div, here)]
    rows += [(f"{pid}-{i}", pid, 2026, f"2026-09-{10 + i}", ("anet", 9, i), r)
             for i, r in enumerate(others)]
    return rows


def test_an_inflated_field_is_pulled_to_the_peak_and_a_normal_one_is_not():
    big = ("anet", 285911, 1138472)
    ok = ("anet", 1, 1)
    rows = []
    for p in range(20):
        rows += _season(p, 107.0, big)          # 7% above every other race
        rows += _season(100 + p, 101.5, ok)     # a peak of 1.5%
    refs = FG.references(rows)
    gaps = {}
    for div, g in refs.values():
        gaps.setdefault(div, []).append(g)
    hit = FG.judge(gaps)
    assert big in hit and ok not in hit
    med, n, up, f = hit[big]
    assert n == 20 and abs(math.log(107.0 * f / 100.0) - FG.PEAK) < 1e-6


def test_a_runner_needs_other_days_and_a_field_needs_runners():
    # the same day's copy is not another race
    rows = [("a", 1, 2026, "2026-10-30", ("anet", 1, 1), 110.0),
            ("b", 1, 2026, "2026-10-30", ("tfrrs", 1, 0), 110.0),
            ("c", 1, 2026, "2026-09-01", ("anet", 2, 1), 100.0)]
    assert FG.references(rows) == {}
    rows = []
    for p in range(10):
        rows += _season(p, 110.0, ("anet", 5, 5))
    gaps = {}
    for div, g in FG.references(rows).values():
        gaps.setdefault(div, []).append(g)
    assert FG.judge(gaps) == {}                 # 10 runners: not judged


def test_never_raises():
    rows = []
    for p in range(20):
        rows += _season(p, 80.0, ("anet", 7, 7))
    gaps = {}
    for div, g in FG.references(rows).values():
        gaps.setdefault(div, []).append(g)
    assert FG.judge(gaps) == {}
