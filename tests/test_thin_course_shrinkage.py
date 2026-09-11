# Project: xc-predictor / tests
# File:    test_thin_course_shrinkage.py
# Purpose: A course seen once must not be published at full strength.
#
# ★ WHAT WAS MEASURED (scripts/shrinkage_audit.py on the corpus,
#   2026-09-10): courses backed by 0-2 races sitting at +54.7%, +52.2%,
#   +49.3%, +49.2%, and XC spread FALLING with evidence (thin sd 3.75%,
#   thick sd 4.50%) when it should rise.
#
# ★ THE MECHANISM, which is not "the prior is too weak". In a cell with ONE
#   race the course effect d and the race-day effect u are the same number.
#   Both are re-estimated each outer from their own posteriors, which is a
#   winner-take-all race: whichever is larger takes more of the common
#   effect, which makes it larger still. Measured on a world where the
#   answer is known, one race per cell:
#
#       kept 1.000,  tau 0.1386,  sigma_u 0.0012      <-- collapsed
#
#   sigma_u goes to zero and the course keeps ONE HUNDRED PERCENT of a
#   single race's noise.
#
# ★ TWO CHANGES, TESTED SEPARATELY BECAUSE THEY DO DIFFERENT JOBS.
#
#   identified_priors  tau2 and sigma_u2 are estimated only on cells with
#                      2+ races. A one-race cell consumes the prior and
#                      never votes on it. This STOPS THE COLLAPSE; on a
#                      mixed corpus it is worth about 0.88 -> 0.87, which
#                      is small and is not claimed to be more.
#
#   sigma_u_floor      how slow a race day is worth at minimum. THIS is
#                      what decides how much one race is trusted, because
#                      a one-race cell keeps tau2/(tau2+sigma_u2).
#
# ⚠ AND A THIRD THING THAT WAS TRIED AND REMOVED, so it is not tried again:
#   scaling pen_cell by rows-per-race, to turn n/(n+k) into R/(R+k). The
#   arithmetic is right, the effect is not -- penalising d while leaving u
#   free LAUNDERS the difficulty into the race term. sigma_u ran from 0.016
#   to 0.133 (planted delta_sd was 0.15) while tau collapsed to 0.005. The
#   difficulty was still there, wearing a different name.
import os
import sys
import unittest

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import joint_solve as js                                        # noqa: E402

# ! tau_max=None ON EVERY SOLVE HERE, DELIBERATELY. solveJoint now defaults
#   to js.TAU_MAX_DEFAULT -- the MEASURED true difficulty spread of this
#   corpus (XC 0.035, TF 0.0122). These worlds plant their own, wider,
#   spreads to make the mechanics visible, so the corpus cap would crush
#   them and the tests would be measuring the cap rather than the thing
#   they are about.


def mixedWorld(n_thick=50, n_thin=50, races_thick=12, per_race=14,
               delta_sd=0.05, u_sd=0.03, noise=0.05, seed=3):
    """The shape the real corpus has: many well-observed cells that can
    identify the variance components, and many one-race cells that cannot.

    ! AN ALL-THIN WORLD PROVES NOTHING about identified_priors -- with no
      identified cell to learn from it falls back to every cell, which is
      exactly the old behaviour. The mix is the point.
    """
    rng = np.random.default_rng(seed)
    n_cell = n_thick + n_thin
    truth = rng.normal(0, delta_sd, n_cell)
    truth -= truth.mean()
    ability = rng.normal(0, 0.06, 1200)
    ath, cel, rac, y = [], [], [], []
    rid = 0
    for c in range(n_cell):
        for _ in range(races_thick if c < n_thick else 1):
            u = rng.normal(0, u_sd)
            for a in rng.choice(len(ability), per_race, replace=False):
                ath.append(int(a)); cel.append(c); rac.append(rid)
                y.append(ability[a] + truth[c] + u + rng.normal(0, noise))
            rid += 1
    return (np.array(ath), np.array(cel), np.array(rac), np.array(y),
            truth, n_thick)


def _run(sigma_u_floor=0.0, identified_priors=True, **kw):
    ath, cel, rac, y, truth, n_thick = mixedWorld(**kw)
    out = js.solveJoint(y, ath, cel, rac, n_outer=6, tilt=False,
                        identified_priors=identified_priors,
                        sigma_u_floor=sigma_u_floor, tau_max=None)
    d = out["delta"] - out["delta"].mean()

    def kept(sl):
        return float(np.dot(d[sl], truth[sl]) / np.dot(truth[sl], truth[sl]))

    thin = slice(n_thick, None)
    return {"thick": kept(slice(0, n_thick)), "thin": kept(thin),
            "thin_rmse": float(np.sqrt(np.mean((d[thin] - truth[thin]) ** 2))),
            "tau": float(np.sqrt(out["tau2"][0])),
            # ! sigma_u2 IS PER GROUP NOW (see js.SIGMA_U_FLOOR). These
            #   worlds have one group, so element 0 is the whole story.
            "sigma_u": float(np.sqrt(np.atleast_1d(out["sigma_u2"])[0]))}


class Helpers(unittest.TestCase):
    def test_races_per_cell(self):
        D = js.Design(athlete=np.array([0, 1, 2, 3, 4, 5]),
                      cell=np.array([0, 0, 0, 0, 1, 1]),
                      race=np.array([0, 0, 0, 1, 2, 3]))
        self.assertTrue(np.array_equal(js.racesPerCell(D), [2, 2]))

    def test_cell_of_race(self):
        D = js.Design(athlete=np.array([0, 1, 2, 3]),
                      cell=np.array([0, 0, 1, 1]),
                      race=np.array([0, 1, 2, 3]))
        self.assertTrue(np.array_equal(js.cellOfRace(D), [0, 0, 1, 1]))

    def test_a_cell_with_no_rows_is_safe(self):
        D = js.Design(athlete=np.array([0, 1]), cell=np.array([0, 0]),
                      race=np.array([0, 1]), n_cell=4)
        r = js.racesPerCell(D)
        self.assertEqual(len(r), 4)
        self.assertEqual(int(r[0]), 2)
        self.assertTrue(np.all(r[1:] == 0))


class SigmaUCollapse(unittest.TestCase):
    def test_one_race_per_cell_used_to_keep_everything(self):
        """The bug, reproduced. Every cell has one race, so d and u are the
        same number everywhere and the winner-take-all update runs free."""
        rng = np.random.default_rng(0)
        n_cell = 60
        truth = rng.normal(0, 0.15, n_cell); truth -= truth.mean()
        ability = rng.normal(0, 0.20, 400)
        ath, cel, rac, y = [], [], [], []
        for c in range(n_cell):
            u = rng.normal(0, 0.03)
            for a in rng.choice(400, 50, replace=False):
                ath.append(int(a)); cel.append(c); rac.append(c)
                y.append(ability[a] + truth[c] + u + rng.normal(0, 0.02))
        # ! nested_var=False: this reproduces the DIAGONAL E-step's collapse
        #   (the bug as recorded). The nested E-step of 2026-09-11 no longer
        #   drives sigma_u all the way to zero even on this degenerate world
        #   (it lands near 0.009), so the demonstration pins the old E-step.
        out = js.solveJoint(np.array(y), np.array(ath), np.array(cel),
                            np.array(rac), n_outer=6, tilt=False,
                            sigma_u_floor=0.0, tau_max=None,
                            nested_var=False)
        d = out["delta"] - out["delta"].mean()
        kept = float(np.dot(d, truth) / np.dot(truth, truth))
        self.assertGreater(kept, 0.95,
                           "the fixture no longer reproduces the collapse")
        self.assertLess(float(np.sqrt(np.atleast_1d(out["sigma_u2"])[0])),
                        0.005,
                        "sigma_u did not collapse; fixture has drifted")

    def test_the_floor_stops_it(self):
        """The same degenerate world, with a race day worth 0.03."""
        rng = np.random.default_rng(0)
        n_cell = 60
        truth = rng.normal(0, 0.15, n_cell); truth -= truth.mean()
        ability = rng.normal(0, 0.20, 400)
        ath, cel, rac, y = [], [], [], []
        for c in range(n_cell):
            u = rng.normal(0, 0.03)
            for a in rng.choice(400, 50, replace=False):
                ath.append(int(a)); cel.append(c); rac.append(c)
                y.append(ability[a] + truth[c] + u + rng.normal(0, 0.02))
        out = js.solveJoint(np.array(y), np.array(ath), np.array(cel),
                            np.array(rac), n_outer=6, tilt=False,
                            sigma_u_floor=0.03, tau_max=None)
        self.assertGreaterEqual(
            float(np.sqrt(np.atleast_1d(out["sigma_u2"])[0])), 0.03 - 1e-9)


class RaceDayFloor(unittest.TestCase):
    def test_thin_shrinks_and_thick_survives(self):
        off = _run(sigma_u_floor=0.0)
        on = _run(sigma_u_floor=0.03)
        self.assertLess(on["thin"], off["thin"] * 0.9,
                        f"thin barely moved: {on['thin']:.3f} vs "
                        f"{off['thin']:.3f}")
        self.assertGreater(on["thick"], 0.95,
                           f"thick paid too much: {on['thick']:.3f}")

    def test_the_floor_improves_thin_accuracy(self):
        """⚠ NOT JUST 'SHRINKS MORE'. Shrinking more is trivial -- multiply
        by anything under one. The claim is that the thin cells get CLOSER
        to the truth, and that is only true up to the real race-day sd."""
        off = _run(sigma_u_floor=0.0)
        on = _run(sigma_u_floor=0.03)
        self.assertLess(on["thin_rmse"], off["thin_rmse"],
                        f"rmse got worse: {on['thin_rmse']:.4f} vs "
                        f"{off['thin_rmse']:.4f}")

    def test_too_high_a_floor_hurts_again(self):
        """The optimum is the true race-day sd (0.03 here), not infinity."""
        best = _run(sigma_u_floor=0.03)
        over = _run(sigma_u_floor=0.10)
        self.assertGreater(over["thin_rmse"], best["thin_rmse"],
                           "an absurd floor should over-shrink and cost "
                           "accuracy, but did not -- the sweep is wrong")

    def test_the_fitted_sigma_u_badly_understates_a_real_race_day(self):
        """★ THE FLOOR'S WHOLE JUSTIFICATION, measured. Plant a race-day
        effect of a known size and see what comes back. If this ever
        recovers the truth, the floor should be removed, not retuned."""
        ath, cel, rac, y, truth, _ = mixedWorld(u_sd=0.03)
        out = js.solveJoint(y, ath, cel, rac, n_outer=6, tilt=False,
                            sigma_u_floor=0.0, tau_max=None)
        fitted = float(np.sqrt(np.atleast_1d(out["sigma_u2"])[0]))
        self.assertLess(fitted, 0.03 * 0.75,
                        f"sigma_u is no longer understated ({fitted:.4f} vs "
                        "a planted 0.030) -- re-justify SIGMA_U_FLOOR")

    def test_the_floor_costs_accuracy_when_race_days_do_not_vary(self):
        """⚠ THE COST, recorded so it is not discovered later. The floor is
        a CLAIM that race days vary. Where they do not, forcing one makes
        abilities worse, and that is the price of the claim."""
        ath, cel, rac, y, truth, _ = mixedWorld(u_sd=0.0)
        errs = {}
        for fl in (0.0, 0.03):
            out = js.solveJoint(y, ath, cel, rac, n_outer=6, tilt=False,
                                sigma_u_floor=fl, tau_max=None)
            d = out["delta"] - out["delta"].mean()
            errs[fl] = float(np.sqrt(np.mean((d - truth) ** 2)))
        self.assertGreater(errs[0.03], errs[0.0],
                           "the floor now looks free on a world with no "
                           "race-day effect; that would be surprising")

    def test_zero_floor_is_the_fitted_behaviour(self):
        r = _run(sigma_u_floor=0.0)
        self.assertLess(r["sigma_u"], 0.03,
                        "the fitted sigma_u on this world is biased low; if "
                        "it is not, the floor's justification is gone")


class IdentifiedPriors(unittest.TestCase):
    def test_it_does_not_hurt_thick_cells(self):
        old = _run(identified_priors=False)
        new = _run(identified_priors=True)
        self.assertAlmostEqual(new["thick"], old["thick"], delta=0.03)

    def test_it_raises_sigma_u_rather_than_lowering_it(self):
        """One-race cells drag sigma_u down because their u cannot be told
        from their d. Excluding them should move it UP, toward the truth."""
        old = _run(identified_priors=False)
        new = _run(identified_priors=True)
        self.assertGreaterEqual(new["sigma_u"], old["sigma_u"])


class PerSportSplit(unittest.TestCase):
    """★★ WITHIN A RACE THE COURSE AND THE DAY ARE THE SAME NUMBER
    (rowPrediction), so which one takes it is decided by tau2 against
    sigma_u2 and by nothing else. The two sports need different answers:
    XC course difficulty reproduces at 0.928, TF's at 0.574.

    ⚠ A SHARED FLOOR WAS ACTIVELY WRONG. With tau[XC] capped at the
    measured 0.035, a shared race-day floor of 0.045 made the DAY prior
    larger than the COURSE prior, so the day won every cross country
    split and real difficulty leaked into u."""

    def _world(self, seed=5):
        """Two groups. Every cell in both has ONE race, so d and u are
        perfectly collinear and only the priors can separate them."""
        rng = np.random.default_rng(seed)
        n_cell = 80
        truth = rng.normal(0, 0.05, n_cell)
        ability = rng.normal(0, 0.06, 1500)
        ath, cel, rac, y = [], [], [], []
        for c in range(n_cell):
            for a in rng.choice(len(ability), 30, replace=False):
                ath.append(int(a)); cel.append(c); rac.append(c)
                y.append(ability[a] + truth[c] + rng.normal(0, 0.03))
        group = np.array([0] * (n_cell // 2) + [1] * (n_cell // 2))
        return (np.array(y), np.array(ath), np.array(cel), np.array(rac),
                group, truth, n_cell)

    def test_the_floor_is_applied_per_sport(self):
        y, ath, cel, rac, group, truth, n_cell = self._world()
        out = js.solveJoint(y, ath, cel, rac, group=group, n_outer=6,
                            tilt=False, tau_max={0: 0.035, 1: 0.0122},
                            sigma_u_floor={0: 0.0, 1: 0.045})
        su = np.sqrt(out["sigma_u2"])
        self.assertEqual(len(su), 2, "sigma_u is still a single number")
        self.assertLess(su[0], 0.045,
                        f"XC took the floor it was not given: {su[0]:.4f}")
        # ! >=, not ==. The floor is a FLOOR: if the data fits a larger
        #   race-day sd than 0.045 it keeps the larger one, which is the
        #   whole point of the word.
        self.assertGreaterEqual(su[1], 0.045 - 1e-9,
                                f"TF fell below its floor: {su[1]:.4f}")
        self.assertGreater(su[1], su[0],
                           f"TF should carry the larger day prior: "
                           f"XC {su[0]:.4f}, TF {su[1]:.4f}")

    def test_xc_keeps_its_difficulty_and_tf_gives_it_to_the_day(self):
        y, ath, cel, rac, group, truth, n_cell = self._world()
        out = js.solveJoint(y, ath, cel, rac, group=group, n_outer=6,
                            tilt=False, tau_max={0: 0.035, 1: 0.0122},
                            sigma_u_floor={0: 0.0, 1: 0.045})
        d = out["delta"]
        half = n_cell // 2

        def kept(sl):
            dd = d[sl] - d[sl].mean()
            tt = truth[sl] - truth[sl].mean()
            return float(np.dot(dd, tt) / np.dot(tt, tt))

        xc, tf = kept(slice(0, half)), kept(slice(half, None))
        self.assertGreater(xc, tf * 1.5,
                           f"XC should keep far more of a one-race course "
                           f"than TF: XC {xc:.2f}, TF {tf:.2f}")
        self.assertGreater(xc, 0.4, f"XC kept only {xc:.2f}")

    def test_the_shared_045_floor_was_starving_xc(self):
        """The regression, reproduced: give XC the old shared floor and it
        keeps materially less of its own courses."""
        y, ath, cel, rac, group, truth, n_cell = self._world()
        half = n_cell // 2

        def xcKept(floor):
            out = js.solveJoint(y, ath, cel, rac, group=group, n_outer=6,
                                tilt=False, tau_max={0: 0.035, 1: 0.0122},
                                sigma_u_floor=floor)
            d = out["delta"][:half]
            dd = d - d.mean()
            tt = truth[:half] - truth[:half].mean()
            return float(np.dot(dd, tt) / np.dot(tt, tt))

        shared = xcKept({0: 0.045, 1: 0.045})
        split = xcKept({0: 0.0, 1: 0.045})
        self.assertGreater(split, shared * 1.25,
                           f"the shared floor is no longer costing XC: "
                           f"shared {shared:.2f}, per-sport {split:.2f}")


if __name__ == "__main__":
    unittest.main()
