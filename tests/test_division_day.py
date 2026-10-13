"""js.divisionDayRow: a division leans toward its own day by how much the
divisions of one venue-day really differ (owner, 2026-10-13: the race-day
term "does some straight bs sometimes"). Planted worlds, no database."""
import os
import sys

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "engine"))
import joint_solve as js  # noqa: E402

SIG = 0.03


def _world(v_sd, n_race=400, per_div=40, seed=1):
    """n_race venue-days of two divisions each; each division's true day is
    the venue's plus N(0, v_sd); rows carry N(0, SIG) noise."""
    rng = np.random.default_rng(seed)
    race = np.repeat(np.arange(n_race), 2 * per_div)
    sub = np.repeat(np.arange(2 * n_race), per_div)
    day = rng.normal(0, 0.03, n_race)
    div_day = day.repeat(2) + rng.normal(0, v_sd, 2 * n_race)
    r = div_day[sub] + rng.normal(0, SIG, race.size)
    w = np.ones(race.size)
    num = np.bincount(race, weights=r)
    W = np.bincount(race).astype(float)
    u_loo = (num[race] - r) / (W[race] - 1)
    return r, w, race, sub, u_loo, div_day


def test_divisions_that_agree_move_nothing():
    r, w, race, sub, u_loo, _ = _world(0.0)
    new, info = js.divisionDayRow(r, w, 1.0, race, sub, u_loo, SIG ** 2)
    assert info["sigma_v"] < 0.003
    assert np.max(np.abs(new - u_loo)) < 0.004


def test_divisions_that_differ_take_their_own_day():
    r, w, race, sub, u_loo, div_day = _world(0.03)
    new, info = js.divisionDayRow(r, w, 1.0, race, sub, u_loo, SIG ** 2,
                                  credit_only_down=False)
    assert 0.025 < info["sigma_v"] < 0.035            # measured, not set
    err_old = np.mean(np.abs(u_loo - div_day[sub]))
    err_new = np.mean(np.abs(new - div_day[sub]))
    assert err_new < 0.5 * err_old                     # closer to the true day
    assert info["k_median"] > 0.8                       # 40 rows: mostly its own


def test_flagged_division_and_lone_division_keep_the_venue_day():
    r, w, race, sub, u_loo, _ = _world(0.03, n_race=50)
    keep = np.ones(r.size)
    keep[sub == 0] = 0.0                               # the guard flagged it
    new, _ = js.divisionDayRow(r, w, 1.0, race, sub, u_loo, SIG ** 2, keep=keep)
    assert np.array_equal(new[sub == 0], u_loo[sub == 0])
    # a race of one division: nothing to lean toward
    one = race == 1
    r2, race2, sub2 = r[one], np.zeros(one.sum(), dtype=int), np.zeros(one.sum(), dtype=int)
    new2, info2 = js.divisionDayRow(r2, w[one], 1.0, race2, sub2, u_loo[one], SIG ** 2)
    assert np.array_equal(new2, u_loo[one]) and info2["rows_moved"] == 0


def test_switch_off(monkeypatch):
    monkeypatch.setenv("XCP_DAY_DIVISION_SHRINK", "0")
    assert not js.dayDivisionShrinkOn()
    monkeypatch.delenv("XCP_DAY_DIVISION_SHRINK")
    assert js.dayDivisionShrinkOn()


def test_it_never_adds_credit():
    """A tactical final is slow as a whole division: its venue day is 0,
    its own reads slow, and the lean must not hand the tactics back."""
    r, w, race, sub, u_loo, _ = _world(0.03)
    new, _ = js.divisionDayRow(r, w, 1.0, race, sub, u_loo, SIG ** 2)
    assert np.all(np.abs(new) <= np.abs(u_loo) + 1e-15)
    assert np.all(new * u_loo >= 0)                    # never past zero
    # tactical: venue day 0, the division ran 4% slow on its own
    race2 = np.zeros(80, dtype=int)
    sub2 = np.repeat([0, 1], 40)
    r2 = np.where(sub2 == 1, 0.04, -0.04)
    u0 = np.zeros(80)
    out, _ = js.divisionDayRow(r2, np.ones(80), 1.0, race2, sub2, u0, SIG ** 2)
    assert np.all(out == 0.0)
    # the Midlothian shape: venue day dragged to +6% by another division,
    # this division's own day ~0 -> its credit falls toward 0
    r3 = np.where(sub2 == 1, 0.12, 0.0)
    u3 = np.full(80, 0.06)
    out3, _ = js.divisionDayRow(r3, np.ones(80), 1.0, race2, sub2, u3, SIG ** 2)
    assert np.all(out3[sub2 == 0] < 0.03) and np.all(out3[sub2 == 1] <= 0.06)


def test_soft_threshold_switch(monkeypatch):
    import joint_golive as jg
    monkeypatch.delenv("XCP_DAY_SOFT", raising=False)
    assert jg.daySoft() == 0.0
    monkeypatch.setenv("XCP_DAY_SOFT", "0.02")
    assert jg.daySoft() == 0.02
    monkeypatch.setenv("XCP_DAY_SOFT", "junk")
    assert jg.daySoft() == 0.0
