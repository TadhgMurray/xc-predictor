"""js.layeredDayRow: venue-day -> loop (race x cell) -> division (owner,
2026-11-03: "Should we keep the race day term separate for each different
distance run?" -- "sure build that"). Planted worlds, no database."""
import os
import sys

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "engine"))
import joint_solve as js  # noqa: E402

SIG = 0.03


def _world(loop_sd, n_race=300, per_div=40, seed=3):
    """Venue-days of two loops (cells 0, 1), two divisions on each loop.
    Each loop's true day is the venue's plus N(0, loop_sd) -- a mismeasured
    loop -- and its divisions agree with it."""
    rng = np.random.default_rng(seed)
    race = np.repeat(np.arange(n_race), 4 * per_div)
    cell = np.tile(np.repeat([0, 1], 2 * per_div), n_race)
    sub = np.repeat(np.arange(4 * n_race), per_div)
    venue = rng.normal(0.03, 0.02, n_race)            # mostly slow days: credit to lose
    loop = venue.repeat(2) + rng.normal(0, loop_sd, 2 * n_race)
    loop_row = loop[race * 2 + cell]
    r = loop_row + rng.normal(0, SIG, race.size)
    w = np.ones(race.size)
    num = np.bincount(race, weights=r)
    W = np.bincount(race).astype(float)
    u_loo = (num[race] - r) / (W[race] - 1)
    return r, w, race, cell, sub, u_loo, loop_row


def test_distance_ids_are_one_per_venue_day_loop():
    ids = js.distanceIds([0, 0, 0, 1, 1], [5, 5, 7, 5, 5])
    assert list(ids) == [0, 0, 1, 2, 2]
    assert js.distanceIds([], []).size == 0


def test_a_wrong_loop_no_longer_lends_its_day_to_the_other():
    r, w, race, cell, sub, u_loo, loop_row = _world(0.04)
    new, old, info = js.layeredDayRow(r, w, 1.0, race, cell, sub, u_loo, SIG ** 2)
    assert info["distance"]["sigma_v"] > 0.02                 # measured at the loop level
    # credit only ever comes off: never more than the venue gave
    assert np.all(np.abs(new) <= np.abs(u_loo) + 1e-12)
    # where the venue credited a loop more than its own day, the layer
    # takes more of that borrowed credit back than divisions alone did
    over = (u_loo > 0) & (loop_row < u_loo - 0.02)
    err_new = np.mean(np.abs(new[over] - np.clip(loop_row[over], 0, None)))
    err_old = np.mean(np.abs(old[over] - np.clip(loop_row[over], 0, None)))
    assert err_new <= err_old


def test_loops_that_agree_change_nothing():
    r, w, race, cell, sub, u_loo, _ = _world(0.0)
    new, old, info = js.layeredDayRow(r, w, 1.0, race, cell, sub, u_loo, SIG ** 2)
    assert info["distance"]["sigma_v"] < 0.004
    assert np.max(np.abs(new - old)) < 0.006


def test_one_loop_venue_is_the_old_lean_exactly():
    r, w, race, cell, sub, u_loo, _ = _world(0.03)
    one = cell == 0
    new, old, _ = js.layeredDayRow(r[one], w[one], 1.0, race[one], cell[one],
                                   np.unique(sub[one], return_inverse=True)[1], u_loo[one], SIG ** 2)
    assert np.allclose(new, old)


def test_off_by_default(monkeypatch):
    monkeypatch.delenv("XCP_DAY_DISTANCE", raising=False)
    assert not js.dayDistanceLayerOn()
    monkeypatch.setenv("XCP_DAY_DISTANCE", "1")
    assert js.dayDistanceLayerOn()
