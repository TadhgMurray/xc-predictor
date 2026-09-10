# Project: xc-predictor / tests
# File:    test_ability_weighting.py
# Purpose: Slaney's "serious runners only" observation, implemented two ways,
#          with the bias of the naive way demonstrated rather than asserted.
#
# ★ THE OBSERVATION IS RIGHT: a runner jogging the back of a JV race is not
#   producing a measurement of the course, and residual variance really is
#   heteroscedastic in ability.
#
# ⚠⚠ AND THE OBVIOUS OBJECTION TO THE NAIVE VERSION DID NOT SURVIVE THESE
#    TESTS, which is why they are written the way they are. The argument
#    was that cutting on finishing position is selection on the OUTCOME and
#    must bias the course easy. It does not -- recovered difficulty is
#    identical to four decimals with and without the cut, even on the
#    fixture below built specifically to break it (half the courses hosting
#    elite-only fields, half mixed, residual noise scaling with ability).
#
#    ABILITY IS A FREE PARAMETER. Keep an athlete only when they ran well
#    and their ability is estimated faster to match; the residual at the
#    kept rows goes to zero and the selection lands in the ability, not in
#    the cell. And ability recovery does not move either.
#
#    So these tests assert the MECHANICS (the filter keeps what it says, the
#    weights respond to real heteroscedasticity and not to noise) and
#    explicitly record the null result on the solve. Which of the two is
#    better on the real corpus is a question for the held-out score, not
#    for a simulation, and both are rungs on the ladder.
import os
import sys
import unittest

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import joint_solve as js                                       # noqa: E402


def heteroWorld(n_cell=40, races=6, per=40, n_ath=1200,
                fast_noise=0.02, slow_noise=0.09, seed=4):
    """Fast runners are consistent, slow runners are not -- which is the
    whole premise. Everything else is identical between them."""
    rng = np.random.default_rng(seed)
    truth = rng.normal(0, 0.05, n_cell)
    ability = rng.normal(0, 0.10, n_ath)          # wide, so bands populate
    ath, cel, rac, y = [], [], [], []
    rid = 0
    for c in range(n_cell):
        for _ in range(races):
            for a in rng.choice(n_ath, per, replace=False):
                # noise scales with how slow the athlete is
                lo, hi = ability.min(), ability.max()
                frac = (ability[a] - lo) / max(hi - lo, 1e-9)
                sd = fast_noise + frac * (slow_noise - fast_noise)
                ath.append(int(a)); cel.append(c); rac.append(rid)
                y.append(ability[a] + truth[c] + rng.normal(0, sd))
            rid += 1
    return (np.array(y), np.array(ath), np.array(cel), np.array(rac),
            truth, ability)


def kept(delta, truth):
    d = delta - delta.mean()
    t = truth - truth.mean()
    return float(np.dot(d, t) / np.dot(t, t))


class TopFraction(unittest.TestCase):
    def test_it_keeps_the_fastest_of_each_race(self):
        race = np.repeat(np.arange(3), 10)
        y = np.concatenate([np.arange(10.0) + 100 * i for i in range(3)])
        rng = np.random.default_rng(0)
        perm = rng.permutation(30)              # order must not matter
        w = js.topFractionWeights(y[perm], race[perm], 0.30)
        for r in range(3):
            m = race[perm] == r
            self.assertEqual(int(w[m].sum()), 3)
            self.assertEqual(sorted(y[perm][m][w[m] > 0] - 100 * r),
                             [0.0, 1.0, 2.0])

    def test_a_frac_of_one_keeps_everyone(self):
        race = np.repeat(np.arange(3), 10)
        y = np.arange(30.0)
        self.assertTrue(np.all(js.topFractionWeights(y, race, 1.0) == 1))
        self.assertTrue(np.all(js.topFractionWeights(y, race, 0.0) == 1))

    def test_every_race_keeps_at_least_one(self):
        """A tiny race must not vanish entirely and take its cell with it."""
        race = np.array([0, 0, 1, 2, 2, 2])
        y = np.array([1.0, 2.0, 5.0, 1.0, 2.0, 3.0])
        w = js.topFractionWeights(y, race, 0.10)
        for r in (0, 1, 2):
            self.assertGreaterEqual(int(w[race == r].sum()), 1)


class AbilityWeights(unittest.TestCase):
    def test_noisier_bands_get_less_weight(self):
        rng = np.random.default_rng(1)
        rating = np.concatenate([np.full(4000, 80.0), np.full(4000, 140.0)])
        resid = np.concatenate([rng.normal(0, 0.09, 4000),
                                rng.normal(0, 0.02, 4000)])
        w, var = js.abilityWeights(resid, rating)
        self.assertLess(w[:4000].mean(), w[4000:].mean() / 2,
                        "the noisy band was not downweighted")
        self.assertAlmostEqual(float(w.mean()), 1.0, delta=1e-6)

    def test_equal_noise_gives_flat_weights(self):
        """⚠ THE CONTROL. If the back of the field is NOT noisier, this must
        do nothing at all -- otherwise it is a free parameter pretending to
        be a measurement."""
        rng = np.random.default_rng(2)
        rating = np.concatenate([np.full(4000, 80.0), np.full(4000, 140.0)])
        resid = rng.normal(0, 0.05, 8000)
        w, _ = js.abilityWeights(resid, rating)
        self.assertAlmostEqual(w[:4000].mean(), w[4000:].mean(), delta=0.15)


class OnASolve(unittest.TestCase):
    """⚠ THESE RECORD A NULL RESULT ON PURPOSE. If a future change makes
    either scheme move the numbers, that is news and these should fail."""

    def _world(self, seed=4, n_cell=40, races=8, per=40, n_ath=1500):
        """Half the courses host ELITE-ONLY fields, half host mixed ones,
        and residual noise scales with ability -- the shape that was
        supposed to make a uniform top-25% cut distort RELATIVE difficulty."""
        rng = np.random.default_rng(seed)
        truth = rng.normal(0, 0.05, n_cell)
        ability = rng.normal(0, 0.10, n_ath)
        order = np.argsort(ability)
        span = float(ability.max() - ability.min())
        ath, cel, rac, y = [], [], [], []
        rid = 0
        for c in range(n_cell):
            elite = (c % 2 == 0)
            for _ in range(races):
                who = (order[:300][rng.choice(300, per, replace=False)]
                       if elite else rng.choice(n_ath, per, replace=False))
                for a in who:
                    frac = (ability[a] - ability.min()) / (span + 1e-9)
                    sd = 0.02 + frac * 0.07
                    ath.append(int(a)); cel.append(c); rac.append(rid)
                    y.append(ability[a] + truth[c] + rng.normal(0, sd))
                rid += 1
        return (np.array(y), np.array(ath), np.array(cel), np.array(rac),
                truth, ability)

    def _solve(self, y, ath, cel, rac, **kw):
        return js.solveJoint(y, ath, cel, rac, n_outer=5, tilt=False,
                             tau_max=None, sigma_u_floor=0.0, **kw)

    def test_the_cut_does_not_bias_relative_difficulty(self):
        y, ath, cel, rac, truth, _ = self._world()
        base = self._solve(y, ath, cel, rac)["delta"]
        cut = self._solve(y, ath, cel, rac, top_frac=0.25)["delta"]
        self.assertGreater(kept(base, truth), 0.9,
                           "baseline broken; the rest means nothing")
        self.assertAlmostEqual(kept(cut, truth), kept(base, truth), delta=0.02,
                               msg="the cut now moves difficulty -- that is "
                                   "NEWS, not a failure; read the header")

    def test_the_cut_does_not_bias_the_elite_versus_mixed_gap(self):
        """The sharpest version: does the cut change how elite-hosting
        courses rate against mixed-hosting ones?"""
        y, ath, cel, rac, truth, _ = self._world()
        ev, od = np.arange(0, 40, 2), np.arange(1, 40, 2)

        def gap(d):
            dd = d - d.mean()
            return float(dd[ev].mean() - dd[od].mean())

        base = gap(self._solve(y, ath, cel, rac)["delta"])
        cut = gap(self._solve(y, ath, cel, rac, top_frac=0.25)["delta"])
        self.assertAlmostEqual(cut, base, delta=0.003,
                               msg=f"elite/mixed gap moved: {base:+.4f} -> "
                                   f"{cut:+.4f}")

    def test_neither_scheme_moves_ability_recovery(self):
        """Ability is what we publish, so this is the one that would matter."""
        y, ath, cel, rac, _, ability = self._world()
        seen = np.unique(ath)

        def rmse(out):
            a = out["ability"][seen]
            t = ability[seen]
            a = a - a.mean(); t = t - t.mean()
            return float(np.sqrt(np.mean((a - t) ** 2)))

        pool = np.zeros(int(ath.max()) + 1, dtype=np.int64)
        base = rmse(self._solve(y, ath, cel, rac))
        cut = rmse(self._solve(y, ath, cel, rac, top_frac=0.25))
        aw = rmse(self._solve(y, ath, cel, rac, ability_weight=True,
                              athlete_pool=pool))
        for name, v in (("cut", cut), ("ability-weighted", aw)):
            self.assertAlmostEqual(v, base, delta=0.004,
                                   msg=f"{name} moved ability rmse "
                                       f"{base:.4f} -> {v:.4f}")


if __name__ == "__main__":
    unittest.main()
