"""The field check (owner, 2026-10-15, Marmonte League Finals 2018): a race
whose field rated above both its earlier and its later races of the season
is pulled down by the smaller gap. One way only.

    python -m pytest -q tests/test_field_check.py
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "engine"))

import numpy as np                                               # noqa: E402

from joint_golive import fieldCheck                              # noqa: E402


def _season(xs, n_ath=12, days=(60, 30, 10)):
    """n_ath athlete-seasons, each running races 0..len(xs)-1 in order, with
    log rating xs[k] + a per-athlete constant."""
    x, ath, d, race = [], [], [], []
    for a in range(n_ath):
        for k, v in enumerate(xs):
            x.append(v + 0.3 * a)
            ath.append(a)
            d.append(days[k])
            race.append(k)
    return (np.array(x), np.array(ath), np.array(d, float),
            np.array(race), len(xs))


def _run(xs, **kw):
    x, a, d, r, n = _season(xs, **kw)
    return fieldCheck(x, a, d, r, n, np.ones(x.size, bool))[0]


def test_an_inflated_middle_race_is_pulled_down():
    corr = _run([0.0, 0.04, 0.0])
    assert np.allclose(corr, [0.0, 0.04, 0.0])


def test_the_smaller_gap_decides():
    corr = _run([0.0, 0.05, 0.02])
    assert np.isclose(corr[1], 0.03)


def test_a_field_getting_fitter_is_left_alone():
    assert np.allclose(_run([0.0, 0.03, 0.06]), 0.0)


def test_a_slow_race_is_never_lifted():
    assert np.allclose(_run([0.0, -0.05, 0.0]), 0.0)


def test_a_small_field_is_left_alone():
    assert np.allclose(_run([0.0, 0.04, 0.0], n_ath=5), 0.0)


def test_the_cap():
    assert np.isclose(_run([0.0, 0.20, 0.0])[1], 0.06)


def test_same_day_races_are_neither_before_nor_after():
    # races 0 and 1 on one day, race 2 later: race 1 has no earlier race
    assert np.allclose(_run([0.0, 0.04, 0.0], days=(30, 30, 10)), 0.0)
