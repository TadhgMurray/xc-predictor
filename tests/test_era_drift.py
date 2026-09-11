# Project: xc-predictor / tests
# File:    test_era_drift.py
# Purpose: A course whose difficulty CHANGES must be allowed to change --
#          and one that does not must stay put.
#
# ★★★ WHY THIS EXISTS (owner, asked three times across two days):
#     "I was hoping was to make course difficulty have more recent races
#      count more"
#     "courses do change. For example Mt. SAC started crazily cleaning
#      their course b4 the meet each year, making it faster"
#     "I think yearly (well not yearly yearly but a couple years) course
#      difficulties could be better"
#
#     Until now one number covered every year a venue existed, so a course
#     that changed was averaged across the change and came out wrong in
#     BOTH directions at once -- too hard for its early years, too easy for
#     its late ones.
#
# ⚠ SPLITTING ALONE WOULD BE WORSE THAN NOT SPLITTING. Cutting each cell
#   into eras multiplies the number of parameters and divides the evidence,
#   and a thin venue would shatter into noise. What makes it work is the
#   RANDOM WALK: the prior penalises the DIFFERENCE between adjacent eras,
#   so a well-measured venue can move and a thin one is held together. Same
#   device as Coulom's Whole-History Rating, where a player's strength is a
#   random walk in time.
#
#   The second test is therefore as important as the first: a feature that
#   finds a step nobody planted is not a feature.
import os
import sys
import unittest

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import joint_solve as js
import run_joint as rj

N_ATH, N_YEARS, N_CELLS = 300, 12, 24
DRIFTER, DRIFT_AT, DRIFT_BY = 3, 6, 0.06


def plantedWorld(seed=7):
    """One course gets DRIFT_BY harder from year DRIFT_AT. Everything else
    holds still."""
    rng = np.random.default_rng(seed)
    base = rng.normal(0, 0.03, N_CELLS)
    ath, yr, cell, dtrue = [], [], [], []
    for y in range(N_YEARS):
        for a in range(N_ATH):
            for _ in range(3):
                c = int(rng.integers(0, N_CELLS))
                ath.append(a)
                yr.append(y)
                cell.append(c)
                dtrue.append(base[c] + (DRIFT_BY if (c == DRIFTER
                                                     and y >= DRIFT_AT)
                                        else 0.0))
    ath = np.array(ath)
    ability = rng.normal(0, 0.05, N_ATH)
    y_obs = ability[ath] + np.array(dtrue) + rng.normal(0, 0.02, len(ath))
    return ath, np.array(yr), np.array(cell), y_obs, base


def solve(ath, yr, cell, y_obs, era_years):
    course, n_new, grp, keys, pairs, w, epb = rj.eraCells(
        cell, yr, era_years, N_CELLS, np.zeros(N_CELLS, np.int64),
        [f"c{i}" for i in range(N_CELLS)])
    race = np.arange(ath.size) // 30
    D = js.Design(ath, course, race, group_of_cell=grp,
                  n_ath=N_ATH, n_cell=n_new, n_race=int(race.max()) + 1,
                  era_pairs=pairs, era_w=w, eras_per_base=epb)
    out = js.solveJoint(y_obs, design=D, n_probe=0, n_outer=4, tilt=False,
                        robust=False, tau_max=None)
    return keys, out["delta"] - out["delta"].mean()


def cellEras(keys, d, c):
    idx = [(int(k.split("@e")[1]), i) for i, k in enumerate(keys)
           if k.startswith(f"c{c}@")]
    idx.sort()
    return np.array([d[i] for _e, i in idx])


class ACourseThatChangesIsAllowedTo(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.world = plantedWorld()
        a, y, c, yo, cls.base = cls.world
        cls.keys0, cls.d0 = solve(a, y, c, yo, 0)
        cls.keys2, cls.d2 = solve(a, y, c, yo, 2)

    def test_one_number_for_all_time_averages_the_change_away(self):
        """★ THE FAILURE THIS FEATURE IS FOR. Without eras the drifter
        lands between its two truths and is wrong in both directions."""
        got = self.d0[DRIFTER]
        lo, hi = self.base[DRIFTER], self.base[DRIFTER] + DRIFT_BY
        self.assertGreater(got, lo + 0.01, "not averaged upward at all")
        self.assertLess(got, hi - 0.01, "not averaged downward at all")

    def test_the_step_is_recovered(self):
        """⚠ THE ONE THAT MATTERS. Late eras minus early eras must equal
        the planted step."""
        e = cellEras(self.keys2, self.d2, DRIFTER)
        half = DRIFT_AT // 2
        step = e[half:].mean() - e[:half].mean()
        self.assertAlmostEqual(step, DRIFT_BY, places=2,
                               msg=f"planted {DRIFT_BY:+.3f}, found {step:+.3f}")

    def test_both_sides_land_on_their_own_truth(self):
        e = cellEras(self.keys2, self.d2, DRIFTER)
        half = DRIFT_AT // 2
        self.assertAlmostEqual(e[:half].mean(), self.base[DRIFTER], delta=0.015)
        self.assertAlmostEqual(e[half:].mean(), self.base[DRIFTER] + DRIFT_BY,
                               delta=0.015)


class ACourseThatDoesNotChangeStaysPut(unittest.TestCase):
    """★ THE OTHER HALF. A split without a walk prior would shatter every
    stable course into era-sized noise, which is worse than not splitting."""

    @classmethod
    def setUpClass(cls):
        a, y, c, yo, cls.base = plantedWorld()
        cls.keys2, cls.d2 = solve(a, y, c, yo, 2)

    def test_stable_courses_barely_wander(self):
        sds = [cellEras(self.keys2, self.d2, c).std()
               for c in range(N_CELLS) if c != DRIFTER]
        self.assertLess(float(np.mean(sds)), 0.01,
                        "stable courses are drifting between eras -- the "
                        "random-walk prior is too loose")

    def test_the_drifter_wanders_far_more_than_the_rest(self):
        rest = float(np.mean([cellEras(self.keys2, self.d2, c).std()
                              for c in range(N_CELLS) if c != DRIFTER]))
        mine = float(cellEras(self.keys2, self.d2, DRIFTER).std())
        self.assertGreater(mine, 3.0 * rest,
                           "the course that changed is not standing out")


class TheAdjacencyIsBuiltRight(unittest.TestCase):
    def test_a_gap_in_years_ties_more_loosely(self):
        """! A random walk's variance grows with elapsed time, so a venue
        with a decade of silence must not have its two ends treated as
        neighbours."""
        cell = np.array([0, 0, 0, 0])
        year = np.array([2000, 2001, 2020, 2021])
        _new, _n, _g, _k, pairs, w, _e = rj.eraCells(
            cell, year, 2, 1, np.zeros(1, np.int64), ["c0"])
        self.assertEqual(pairs.shape[1], 1)
        self.assertAlmostEqual(float(w[0]), 1.0 / 10.0, places=6)

    def test_off_is_a_no_op(self):
        cell = np.array([0, 1, 0])
        year = np.array([2000, 2001, 2002])
        new, n, g, k, pairs, w, epb = rj.eraCells(
            cell, year, 0, 2, np.zeros(2, np.int64), ["c0", "c1"])
        self.assertIsNone(pairs)
        self.assertEqual(n, 2)
        np.testing.assert_array_equal(new, cell)

    def test_rows_without_a_cell_survive(self):
        cell = np.array([0, -1, 0])
        year = np.array([2000, 2000, 2001])
        new, _n, _g, _k, _p, _w, _e = rj.eraCells(
            cell, year, 2, 1, np.zeros(1, np.int64), ["c0"])
        self.assertEqual(int(new[1]), -1)


if __name__ == "__main__":
    unittest.main()
