"""The nightly provisional race-day term (engine/provisional_day.py)."""
import math
import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in ("engine", "racecast", "scripts"):
    sys.path.insert(0, os.path.join(ROOT, p))

import provisional_day as P   # noqa: E402


def _race(u, n, b=-0.01, tilt=0.9, noise=0.02, rnd=None):
    """A field whose runners ran a day u (log time, + slow) against levels
    offset by b: g_i = b - tilt*u + noise."""
    rnd = rnd or random.Random(1)
    pairs = []
    for _ in range(n):
        lv = 120.0
        g = b - tilt * u + rnd.gauss(0, noise)
        pairs.append((lv * math.exp(g), lv))
    return pairs, [tilt] * n


def test_a_field_under_the_minimum_reads_nothing():
    pairs, tilts = _race(0.03, P.MIN_FIELD - 1)
    assert P.raceReading(pairs, tilts) is None


def test_the_calibrated_day_recovers_the_solve_and_beats_no_term():
    rnd = random.Random(7)
    solved = []
    for _ in range(80):
        u = rnd.gauss(0, 0.025)
        pairs, tilts = _race(u, 60, rnd=rnd)
        g, s, n, tilt = P.raceReading(pairs, tilts)
        solved.append((g, s, n, tilt, u))
    cal = P.calibrate(solved)
    assert abs(cal["b"] - (-0.01)) < 0.005 and 0.8 < cal["beta"] < 1.2
    rmse, rmse0 = P.backtest(solved, cal)
    assert rmse < 0.5 * rmse0 and P.passes(rmse, rmse0, len(solved))
    ho, ho0 = P.heldOut(solved)
    assert ho < 0.5 * ho0
    # a slow day reads slow, a fast one fast
    for u in (0.05, -0.04):
        pairs, tilts = _race(u, 100, rnd=rnd)
        got = P.dayFrom(*P.raceReading(pairs, tilts), cal)
        assert abs(got - u) < 0.012 and (got > 0) == (u > 0)


def test_a_small_field_is_shrunk_toward_a_typical_day():
    cal = {"b": 0.0, "t": 0.0004, "alpha": 0.0, "beta": 1.0}
    big = P.dayFrom(-0.045, 0.03, 200, 0.9, cal)
    small = P.dayFrom(-0.045, 0.03, 6, 0.9, cal)
    assert 0 < small < big <= 0.05 + 1e-9


def test_a_reading_with_no_signal_is_trusted_not_at_all():
    rnd = random.Random(2)
    solved = []
    for _ in range(100):
        pairs, tilts = _race(0.0, 40, rnd=rnd)          # the field says nothing...
        solved.append((*P.raceReading(pairs, tilts), rnd.gauss(0, 0.025)))  # ...about u
    cal = P.calibrate(solved)
    assert cal["beta"] < 0.3
    ho, ho0 = P.heldOut(solved)
    assert not P.passes(ho, ho0, len(solved))


def test_the_nightly_s_own_prices_are_told_from_the_solve_s():
    assert P.nightlyPriced([120.0, 101.33], [120.001, 101.3])
    assert not P.nightlyPriced([123.0, 104.0], [120.0, 101.3])
    assert P.quantile([1, 2, 3, 4], 0.75) == 3.25


def test_the_gate_refuses_too_few_or_no_better():
    assert not P.passes(0.01, 0.02, P.CAL_MIN - 1)
    assert not P.passes(0.019, 0.02, 100)
    assert P.passes(0.01, 0.02, 100)
    assert P.calibrate([(0, 0, 10, 1, 0)] * (P.CAL_MIN - 1)) is None


def test_the_nightly_runs_it_after_the_fill_and_never_blocks_the_boards():
    sh = open(os.path.join(ROOT, "deploy", "nightly_update.sh"), encoding="utf-8").read()
    i, j = sh.index("09b_fill_venue"), sh.index("09c_provisional_day")
    assert i < j < sh.index("10_rankings_prepare")
    assert "engine/provisional_day.py --write || true" in sh
    g = open(os.path.join(ROOT, "engine", "joint_golive.py"), encoding="utf-8").read()
    assert "provisional   boolean NOT NULL DEFAULT false" in g


def test_a_field_far_faster_than_itself_is_a_doubted_distance():
    """2026-11-04, the America East women: "6000 m" in 17:05.7."""
    b = -0.01
    assert P.doubtedDistance(b + 0.19, b)          # a 5K stored as 6K
    assert not P.doubtedDistance(b + 0.05, b)      # a fast day
    assert not P.doubtedDistance(b - 0.20, b)      # a slow field is never withheld here
    assert not P.doubtedDistance(None, b)
