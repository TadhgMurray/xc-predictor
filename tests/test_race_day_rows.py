"""race_day_effect gets one row per (race, distance cell): under the venue
race key a race spans every distance run at that venue that day, and the
writer used to keep only one of them (2026-10-05: the missing hovers)."""
import os
import sys

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "engine"))

from joint_golive import racePairs  # noqa: E402


def test_every_distance_of_a_venue_day_gets_a_row():
    # race 0 = one venue-day with a 5000 (cell 3) and a 4000 (cell 7);
    # race 1 = another day, 5000 only
    race = np.array([0, 0, 0, 0, 0, 1, 1])
    cell = np.array([3, 3, 7, 3, 7, 3, 3])
    r, c, first, n = racePairs(race, cell, 10)
    got = sorted(zip(r.tolist(), c.tolist(), n.tolist()))
    assert got == [(0, 3, 3), (0, 7, 2), (1, 3, 2)]
    # the first row of each pair really belongs to it
    assert all(race[f] == rr and cell[f] == cc for f, rr, cc in zip(first, r, c))


def test_cell_ids_beyond_the_key_list_do_not_collide():
    r, c, _f, n = racePairs([0, 0], [12, 2], 5)
    assert sorted(zip(r.tolist(), c.tolist())) == [(0, 2), (0, 12)]
