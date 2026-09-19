# Project: xc-predictor / tests
# File:    test_degree_rule_and_merge.py
# Purpose: the two 2026-09-19 changes to the distance fitter, each pinned by
#          the failure that motivated it.
#
#          1. THE DEGREE RULE counted distinct distance transitions, which
#             penalises track for racing standardised distances (every
#             800/1600 doubler in the country lands in ONE endpoint bin) and
#             flatters a narrow quasi-continuous XC band. The replacement
#             measures log-span and EFFECTIVE distinct locations instead.
#          2. THE MERGED FIT shares one shape per pool across both sports
#             with eps still per sport. Its arithmetic must be identical to
#             correcting each sport's edges and solving once -- and its
#             gates must refit the MERGED model, not the per-sport one.
#
#   python -m unittest tests.test_degree_rule_and_merge
#   python -m pytest -q tests/test_degree_rule_and_merge.py
import math
import os
import sys
import types
import unittest

import numpy as np

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "engine"), os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

if "corrections" not in sys.modules:        # the fitter imports it at load
    _corr = types.ModuleType("corrections")
    _corr.distanceOverrideSQL = lambda *a, **k: ("", "")
    _corr.distanceDropSQL = lambda *a, **k: ""
    sys.modules["corrections"] = _corr

import fit_distance_exponent as fde                            # noqa: E402


# ------------------------------------------------------------------ #
# fixtures
# ------------------------------------------------------------------ #
def _pair(d1, d2, k=1.08, t1=200.0, pool="hs_m", sport="XC"):
    """A same-athlete pair obeying T2/T1 = (d2/d1)**k exactly."""
    return {"pool": pool, "sport": sport,
            "distance1": float(d1), "distance2": float(d2),
            "time1": float(t1),
            "time2": float(t1 * (d2 / d1) ** k)}


def _edge(d1, d2, delta, weight=100.0, count=50, s=1):
    return (math.log(d1), math.log(d2), delta, weight, count, s)


# ! THE REAL SET OF RACED TRACK DISTANCES, not a convenient subset. An
#   earlier version of this fixture used four rungs, which put the pool
#   below MIN_EDGES_FOR_POOL and made _fitOnePotential return None -- and
#   four endpoint locations cannot FIT a cubic anyway, only interpolate
#   one (three free coefficients, three independent difference
#   constraints from a four-node graph). The eight rungs below give 28
#   transitions: just under POLY_DEGREE_EDGES[0] = 30, which is exactly
#   where real college_f|TF sits at 29.
TRACK_RUNGS = (800, 1000, 1500, 1600, 3000, 3200, 5000, 10000)


def _standardisedTrack(k=1.08, reps=40):
    """Track's real shape: discrete rungs, many pairs on each."""
    rungs = TRACK_RUNGS
    out = []
    for i, a in enumerate(rungs):
        for b in rungs[i + 1:]:
            out += [_pair(a, b, k=k, t1=120.0 + j) for j in range(reps)]
    return out


def _quasiContinuousNarrow(k=1.08, reps=6):
    """XC's real shape in a narrow band: many distinct lengths, few each."""
    out = []
    for a in range(3200, 4000, 20):
        for b in (a + 900, a + 1100):
            out += [_pair(a, b, k=k, t1=600.0 + j) for j in range(reps)]
    return out


class TheDegreeRuleMeasuresWhereTheDataSits(unittest.TestCase):

    def test_perplexity_collapses_when_one_location_dominates(self):
        # Four bins, evenly supported -> effectively four locations.
        even = [_edge(800, 1600, 0.7, count=100),
                _edge(3200, 5000, 0.5, count=100)]
        self.assertAlmostEqual(fde._effectiveLocations(even), 4.0, places=6)
        # The same four bins, but one pair of endpoints carries everything.
        lopsided = [_edge(800, 1600, 0.7, count=1_000_000),
                    _edge(3200, 5000, 0.5, count=1)]
        self.assertLess(fde._effectiveLocations(lopsided), 2.2)

    def test_a_thousand_bins_in_one_place_is_not_a_thousand_locations(self):
        # ⚠ THE FAILURE THE OLD RULE HAD IN THE OTHER DIRECTION: a narrow
        #   quasi-continuous band earns a cubic by COUNT that its span
        #   cannot condition.
        edges = fde._aggregatePairEdges(_quasiContinuousNarrow())
        self.assertGreaterEqual(fde._distinctTransitions(edges), 30,
                                "fixture must earn degree 3 by the old rule")
        self.assertEqual(fde._polyDegree(fde._distinctTransitions(edges)), 3)
        # ...and the new rule refuses it, because the span is under a doubling
        self.assertLess(fde._edgeSpanLog(edges),
                        fde.POLY_DEGREE_SPAN_LOG[0])
        self.assertLess(fde._degreeByLocations(edges), 3)

    def test_standardised_track_distances_are_no_longer_punished(self):
        # ★ THE MOTIVATING FAILURE. Track spans 800..5000 -- a factor of
        #   6 -- on a handful of discrete rungs, so the transition count is
        #   small and the old rule denied it a cubic.
        edges = fde._aggregatePairEdges(_standardisedTrack())
        n_trans = fde._distinctTransitions(edges)
        self.assertLess(n_trans, fde.POLY_DEGREE_EDGES[0],
                        "fixture must be thin by the old count rule")
        self.assertLess(fde._polyDegree(n_trans), 3)
        # the span is wide and the locations are genuinely distinct
        self.assertGreaterEqual(fde._edgeSpanLog(edges),
                                fde.POLY_DEGREE_SPAN_LOG[0])
        self.assertEqual(fde._degreeByLocations(edges), 3)

    def test_the_count_ceiling_survives(self):
        # A wide span on two edges is still a straight line.
        thin = [_edge(800, 5000, 1.98, count=500),
                _edge(1600, 3200, 0.75, count=500)]
        self.assertGreaterEqual(fde._edgeSpanLog(thin),
                                fde.POLY_DEGREE_SPAN_LOG[0])
        self.assertEqual(fde._degreeByLocations(thin), 1)

    def test_the_active_rule_is_a_switch_and_both_are_reported(self):
        edges = fde._aggregatePairEdges(_standardisedTrack())
        n_trans = fde._distinctTransitions(edges)
        original = fde.DEGREE_RULE
        try:
            fde.DEGREE_RULE = "edges"
            deg_e, audit = fde._degreeFor(edges, n_trans)
            self.assertEqual(deg_e, audit["degree_edges"])
            fde.DEGREE_RULE = "locations"
            deg_l, audit2 = fde._degreeFor(edges, n_trans)
            self.assertEqual(deg_l, audit2["degree_locations"])
            # both rules' verdicts travel with the entry either way
            for a in (audit, audit2):
                self.assertIn("degree_edges", a)
                self.assertIn("degree_locations", a)
                self.assertGreater(a["span_log"], 0.0)
            self.assertNotEqual(deg_e, deg_l,
                                "the fixture exists to separate the rules")
        finally:
            fde.DEGREE_RULE = original

    def test_the_demotion_cap_still_wins_over_either_rule(self):
        edges = fde._aggregatePairEdges(_standardisedTrack())
        n_trans = fde._distinctTransitions(edges)
        original = fde.DEGREE_RULE
        try:
            for rule in fde.DEGREE_RULE_CHOICES:
                fde.DEGREE_RULE = rule
                deg, _a = fde._degreeFor(edges, n_trans, cap=1)
                self.assertEqual(deg, 1, f"cap ignored under {rule}")
        finally:
            fde.DEGREE_RULE = original


class TheMergedFitSharesShapeButNotEps(unittest.TestCase):

    def test_eps_correction_matches_the_solver_exactly(self):
        # ! The merged path corrects delta and passes eps=0. That must be
        #   arithmetically identical to passing eps, or the two paths
        #   disagree silently.
        edges = [_edge(800, 1600, 0.75, s=1), _edge(1600, 3200, 0.74, s=-1),
                 _edge(800, 3200, 1.49, s=1)]
        eps = 0.006
        direct, _d1 = fde._solveShapeRobust(edges, 2, eps)
        corrected, _d2 = fde._solveShapeRobust(
            fde._epsCorrectEdges(edges, eps), 2, 0.0)
        np.testing.assert_allclose(direct, corrected, rtol=0, atol=1e-12)

    def test_zero_eps_is_a_passthrough_not_a_rebuild(self):
        edges = [_edge(800, 1600, 0.75)]
        self.assertEqual(fde._epsCorrectEdges(edges, 0.0), edges)

    def test_one_shape_from_both_sports_spans_both_supports(self):
        # XC carries the long end, TF the short end. Neither alone reaches
        # both, and the merged span must.
        tf = _standardisedTrack(k=1.08)
        xc = [_pair(a, b, k=1.08, t1=1500.0 + j, sport="XC")
              for a in range(5000, 8000, 250)
              for b in (a + 2000,) for j in range(8)]
        merged = fde._fitMergedPotential({"TF": tf, "XC": xc})
        self.assertIsNotNone(merged)
        lo, hi = merged["span"]
        self.assertLessEqual(lo, 1600.0)
        self.assertGreaterEqual(hi, 8000.0)
        self.assertEqual(merged["sports"], ["TF", "XC"])
        self.assertEqual(merged["eps_source"], "per_sport")
        self.assertEqual(sorted(merged["eps_by_sport"]), ["TF", "XC"])
        self.assertEqual(sorted(merged["n_edges_by_sport"]), ["TF", "XC"])

    def test_a_shared_shape_recovers_a_shared_exponent(self):
        # Both sports generated from k = 1.08 exactly; the merged curve must
        # read 1.08 across the whole union, including where only one sport
        # testified.
        tf = _standardisedTrack(k=1.08)
        xc = [_pair(a, b, k=1.08, t1=1500.0 + j, sport="XC")
              for a in range(5000, 8000, 250)
              for b in (a + 2000,) for j in range(8)]
        merged = fde._fitMergedPotential({"TF": tf, "XC": xc})
        for d in (1000, 1600, 3200, 5000, 8000):
            self.assertAlmostEqual(fde._localExponent(merged, d), 1.08,
                                   delta=0.02, msg=f"at {d}m")

    def test_a_constant_terrain_penalty_on_xc_cannot_reach_the_shape(self):
        # ★ THE ARGUMENT FOR MERGING WITHOUT AN OFFSET TERM. Terrain is a
        #   constant multiplier on every XC time; it cancels in every
        #   within-XC difference, so the merged shape must not move.
        tf = _standardisedTrack(k=1.08)
        xc = [_pair(a, b, k=1.08, t1=1500.0 + j, sport="XC")
              for a in range(5000, 8000, 250)
              for b in (a + 2000,) for j in range(8)]
        base = fde._fitMergedPotential({"TF": tf, "XC": xc})
        slowed = []
        for p in xc:
            q = dict(p)
            q["time1"], q["time2"] = p["time1"] * 1.15, p["time2"] * 1.15
            slowed.append(q)
        moved = fde._fitMergedPotential({"TF": tf, "XC": slowed})
        for d in (1000, 1600, 3200, 5000, 8000):
            self.assertAlmostEqual(fde._localExponent(base, d),
                                   fde._localExponent(moved, d),
                                   delta=1e-6, msg=f"at {d}m")

    def test_the_two_sports_keep_their_own_calendar_offsets(self):
        edges_by_sport = {
            "XC": [_edge(3000, 5000, 0.55, s=1), _edge(3000, 5000, 0.51, s=-1),
                   _edge(4000, 6000, 0.44, s=1), _edge(4000, 6000, 0.40, s=-1)],
            "TF": [_edge(800, 1600, 0.75, s=1), _edge(800, 1600, 0.74, s=-1)],
        }
        # Below MIN_EPS_MATCHED_TRANSITIONS both fall back, loudly, to 0.
        got = fde._epsBySport(edges_by_sport)
        self.assertEqual({sp: v[1] for sp, v in got.items()},
                         {"XC": "none", "TF": "none"})
        # A caller-imposed map is honoured per sport and labelled 'level'.
        pinned = fde._epsBySport(edges_by_sport, {"XC": 0.01, "TF": 0.002})
        self.assertEqual(pinned["XC"], (0.01, "level"))
        self.assertEqual(pinned["TF"], (0.002, "level"))

    def test_a_merged_entry_pins_its_per_sport_eps_on_refit(self):
        entry = {"eps_source": "per_sport",
                 "eps_by_sport": {"XC": {"eps": 0.01, "source": "own"},
                                  "TF": {"eps": 0.002, "source": "own"}}}
        self.assertTrue(fde._isMergedEntry(entry))
        self.assertEqual(fde._epsPin(entry), {"XC": 0.01, "TF": 0.002})
        # ! a demotion must not re-derive it -- two unknowns moving at once
        self.assertEqual(fde._refitEpsArg(entry), {"XC": 0.01, "TF": 0.002})

    def test_a_single_sport_entry_keeps_its_old_refit_semantics(self):
        borrowed = {"eps_source": "level", "eps": 0.007}
        own = {"eps_source": "own", "eps": 0.009}
        self.assertFalse(fde._isMergedEntry(borrowed))
        self.assertEqual(fde._refitEpsArg(borrowed), 0.007)
        self.assertIsNone(fde._refitEpsArg(own),
                          "an own-eps entry re-derives, as it always did")
        self.assertEqual(fde._epsPin(own), 0.009)

    def test_the_gates_refit_the_model_they_are_gating(self):
        # ⚠ Hard-wired to _fitOnePotential, the stability probe would refit a
        #   MERGED entry unmerged and report the difference as knob
        #   sensitivity, auto-failing every merged pool.
        tf = _standardisedTrack(k=1.08)
        xc = [_pair(a, b, k=1.08, t1=1500.0 + j, sport="XC")
              for a in range(5000, 8000, 250)
              for b in (a + 2000,) for j in range(8)]
        by_sport = {"TF": tf, "XC": xc}
        entry = fde._fitMergedPotential(by_sport)
        seen = []
        refit = fde._mergedRefitter(by_sport)

        def spy(**kw):
            seen.append(kw)
            return refit(**kw)

        shift, flipped = fde._stabilityShift(
            [p for ps in by_sport.values() for p in ps], entry, refit=spy)
        self.assertEqual(len(seen), len(fde.STABILITY_TUKEY_PROBES))
        for kw in seen:
            self.assertEqual(kw["degree_cap"], entry["degree"])
            self.assertEqual(kw["eps_fixed"], fde._epsPin(entry))
        self.assertFalse(flipped)
        self.assertLess(shift, fde.STABILITY_TOL,
                        "a clean k=1.08 corpus must be knob-independent")

    def test_the_default_refitter_reproduces_the_old_hard_wired_call(self):
        pairs = _standardisedTrack(k=1.08)
        entry = fde._fitOnePotential(pairs)
        direct = fde._fitOnePotential(pairs, eps_fixed=entry["eps"],
                                      tukey_c=3.5,
                                      degree_cap=entry["degree"])
        viahandle = fde._refitter(pairs)(eps_fixed=entry["eps"], tukey_c=3.5,
                                        degree_cap=entry["degree"])
        np.testing.assert_allclose(direct["values"], viahandle["values"],
                                   rtol=0, atol=1e-12)


class TheOverlapShapeTestDecidesTheMerge(unittest.TestCase):

    def test_no_overlap_is_reported_not_judged(self):
        self.assertIsNone(fde._overlapBand((800, 3200), (4000, 10000)))
        self.assertIsNone(fde._overlapShapeVerdict({}, {}, (800, 3200),
                                                   (4000, 10000)))

    def test_identical_shapes_inside_a_wide_band_read_as_one_law(self):
        pairs = _standardisedTrack(k=1.08)
        pot = fde._fitOnePotential(pairs)
        v = fde._overlapShapeVerdict(pot, pot, (1000, 5000), (1000, 5000))
        self.assertTrue(v["powered"])
        self.assertTrue(v["agree"])
        self.assertLess(v["mean_gap"], 1e-9)
        self.assertEqual(len(v["rows"]), fde.OVERLAP_PROBES)

    def test_different_laws_inside_a_wide_band_refuse_the_merge(self):
        soft = fde._fitOnePotential(_standardisedTrack(k=1.04))
        hard = fde._fitOnePotential(_standardisedTrack(k=1.18))
        v = fde._overlapShapeVerdict(soft, hard, (1000, 5000), (1000, 5000))
        self.assertTrue(v["powered"])
        self.assertFalse(v["agree"])
        self.assertGreater(v["mean_gap"], fde.OVERLAP_AGREE_EXP)

    def test_a_narrow_band_never_returns_a_verdict(self):
        # ⚠ hs_m's real intersection is 2,813..3,200 -- 0.13 log units. A
        #   band that narrow can agree by accident, so it must not decide.
        pot = fde._fitOnePotential(_standardisedTrack(k=1.08))
        v = fde._overlapShapeVerdict(pot, pot, (2813, 3200), (2813, 3200))
        self.assertLess(v["width_log"], fde.OVERLAP_MIN_LOG)
        self.assertFalse(v["powered"])
        self.assertFalse(v["agree"], "no verdict from an underpowered band")

    def test_the_printer_never_raises_on_any_branch(self):
        pot = fde._fitOnePotential(_standardisedTrack(k=1.08))
        for sup in ((1000, 5000), (2813, 3200)):
            fde._printOverlapShape(
                "fixture", fde._overlapShapeVerdict(pot, pot, sup, sup))
        fde._printOverlapShape("fixture", None)


if __name__ == "__main__":
    unittest.main()


class TheSlopePriorReplacesTheClampWithPhysics(unittest.TestCase):
    """★ The floor makes a curve LEGAL, not RIGHT: MIN_LOCAL_EXP clamps the
       SAMPLED values after the fit, so the fit is still free to bend to 0.84
       and the clamp then flattens it to exactly 1.040. The prior puts the
       physics in the fit instead."""

    def setUp(self):
        self._saved = fde.PRIOR_SLOPE_WEIGHT

    def tearDown(self):
        fde.PRIOR_SLOPE_WEIGHT = self._saved

    def test_off_by_default_and_contributes_nothing_when_off(self):
        self.assertEqual(self._saved, 0.0, "the prior must ship off")
        edges = fde._aggregatePairEdges(_standardisedTrack(k=1.08))
        fde.PRIOR_SLOPE_WEIGHT = 0.0
        A, b, w = fde._priorSlopeRows(edges, 3, 1.0)
        self.assertEqual((A.shape, len(b), len(w)), ((0, 3), 0, 0),
                         "with the weight at 0 there are no prior rows at "
                         "all, which is what makes the old solve exact "
                         "rather than merely close")

    def test_a_tiny_weight_is_a_nudge_and_a_large_one_is_a_pull(self):
        # The knob has to be monotone or it cannot be tuned.
        pairs = _standardisedTrack(k=1.16)
        got = {}
        for weight in (0.0, 0.5, 8.0):
            fde.PRIOR_SLOPE_WEIGHT = weight
            got[weight] = fde._localExponent(fde._fitOnePotential(pairs), 5000)
        self.assertGreater(got[0.0], fde.K_PRIOR)
        self.assertLessEqual(got[8.0], got[0.5] + 1e-9)
        self.assertLessEqual(got[0.5], got[0.0] + 1e-9)

    def test_the_prior_row_basis_matches_polyDeriv(self):
        # ! If these two disagree the prior constrains something that is not
        #   the local exponent, silently.
        fde.PRIOR_SLOPE_WEIGHT = 1.0
        edges = fde._aggregatePairEdges(_standardisedTrack(k=1.08))
        A, b, _w = fde._priorSlopeRows(edges, 3, 1.0)
        L0 = math.log(fde.TARGET_DISTANCE_METERS)
        pts = [e[0] for e in edges] + [e[1] for e in edges]
        probes = np.linspace(min(pts), max(pts), fde.PRIOR_SLOPE_PROBES)
        coeffs = np.array([0.7, -0.03, 0.004])
        for row, x_abs in zip(A, probes):
            self.assertAlmostEqual(float(row @ coeffs),
                                   fde._polyDeriv(coeffs, x_abs - L0),
                                   places=12)
        self.assertTrue(all(abs(x - fde.K_PRIOR) < 1e-12 for x in b))

    def test_the_prior_is_weightless_where_the_endpoints_are(self):
        # ★ Data wins where data exists; that is the whole design.
        fde.PRIOR_SLOPE_WEIGHT = 1.0
        edges = fde._aggregatePairEdges(_standardisedTrack(k=1.08))
        _A, _b, w = fde._priorSlopeRows(edges, 3, 1.0)
        L0 = math.log(fde.TARGET_DISTANCE_METERS)
        pts = [e[0] for e in edges] + [e[1] for e in edges]
        probes = np.linspace(min(pts), max(pts), fde.PRIOR_SLOPE_PROBES)
        # the probe nearest a heavily-raced rung must carry ~no prior weight
        rung = math.log(1600.0)
        i_near = int(np.argmin(np.abs(probes - rung)))
        i_far = int(np.argmax(w))
        self.assertLess(w[i_near], w[i_far],
                        "the prior must be lighter near real endpoints")
        self.assertLess(w[i_near], 0.5 * float(np.max(w)))
        del L0

    def test_balance_is_zero_at_the_boundaries_and_one_inside(self):
        edges = fde._aggregatePairEdges(_standardisedTrack(k=1.08))
        # ! THE PROBES MUST COME FROM THE EDGES, as _priorSlopeRows builds
        #   them. An endpoint is the MEAN of its bin's members, so
        #   log(800) recomputed independently lands a float either side of
        #   it and a strict comparison at the boundary flips.
        pts = [e[0] for e in edges] + [e[1] for e in edges]
        probes = np.linspace(min(pts), max(pts), 9)
        self.assertEqual(list(fde._supportBalance([], probes)), [0.0] * 9)
        bal = fde._supportBalance(edges, probes)
        self.assertEqual(bal[0], 0.0, "nothing below the lowest endpoint")
        self.assertEqual(bal[-1], 0.0, "nothing above the highest")
        self.assertTrue(all(b == 1.0 for b in bal[1:-1]),
                        "the interior of a rung corpus is two-sided "
                        "throughout, however lumpy the rungs are")

    def test_the_prior_ignores_the_gaps_between_standardised_rungs(self):
        # ⚠ THE BUG THIS MEASURE REPLACED. A local-density kernel weighted
        #   2,063..2,546m and 6,564..8,102m -- the gaps BETWEEN rungs, where
        #   the polynomial interpolates between two well-supported points and
        #   needs no help -- and gave the actual boundaries zero.
        fde.PRIOR_SLOPE_WEIGHT = 1.0
        edges = fde._aggregatePairEdges(_standardisedTrack(k=1.12))
        w_typ = float(np.median([e[3] for e in edges]))
        _A, _b, w = fde._priorSlopeRows(edges, 3, w_typ)
        pts = [e[0] for e in edges] + [e[1] for e in edges]
        probes = np.linspace(min(pts), max(pts), fde.PRIOR_SLOPE_PROBES)
        for x, weight in zip(probes, w):
            d = math.exp(x)
            if 2000 <= d <= 8200:
                self.assertEqual(weight, 0.0,
                                 f"prior must stand down at {d:,.0f}m")
        self.assertGreater(w[0], 0.0, "the bottom boundary is one-sided")
        self.assertGreater(w[-1], 0.0, "the top boundary is one-sided")

    def test_a_well_supported_curve_barely_moves(self):
        # ⚠ THE PROPERTY THAT MATTERS MOST. A prior that shifts a curve the
        #   pairs already determine is a bias, not a regulariser.
        pairs = _standardisedTrack(k=1.12)
        fde.PRIOR_SLOPE_WEIGHT = 0.0
        base = fde._fitOnePotential(pairs)
        fde.PRIOR_SLOPE_WEIGHT = 1.0
        with_prior = fde._fitOnePotential(pairs)
        # ! A BOUND, NOT AN EQUALITY. The prior enters one global polynomial
        #   fit, so pinning the two boundaries tilts the whole cubic a
        #   little. Measured on a k=1.12 eight-rung corpus at weight 1, the
        #   largest interior shift is 0.003 -- an order of magnitude below
        #   the 0.03-0.06 differences this fitter exists to resolve, and far
        #   below the 0.08 the floor was silently imposing. If this bound
        #   ever has to be loosened, the prior has stopped being a
        #   regulariser.
        worst = max(abs(fde._localExponent(base, d)
                        - fde._localExponent(with_prior, d))
                    for d in (1600, 2400, 3200, 5000))
        self.assertLess(worst, 0.005, f"largest interior shift {worst:.4f}")

    def test_the_outermost_rung_moves_a_little_and_toward_the_prior(self):
        # ! AND THIS IS THE DESIGN, NOT A LEAK. 1,000m sits one rung above
        #   the 800m boundary, where support really is one-sided, so a small
        #   pull belongs there. What must never happen is a pull AWAY from
        #   the prior -- the signature of a cubic pinned in the middle and
        #   pivoting at its ends, which is what the density measure did
        #   (1.12 came back as 1.1296).
        pairs = _standardisedTrack(k=1.12)
        fde.PRIOR_SLOPE_WEIGHT = 0.0
        base = fde._localExponent(fde._fitOnePotential(pairs), 1000)
        fde.PRIOR_SLOPE_WEIGHT = 1.0
        moved = fde._localExponent(fde._fitOnePotential(pairs), 1000)
        self.assertLess(moved, base, "must move TOWARD 1.06, not away")
        self.assertLess(base - moved, 0.01, "and only a little")

    def test_the_clamp_stops_firing_because_the_fit_never_goes_there(self):
        """★ THE WHOLE POINT, AND IT IS MEASURED RATHER THAN ARGUED.

        A corpus that is clean and dense to 3,200m and whose ONLY evidence
        above that implies pace improving with distance -- an October 8k
        against a November 10k, the XC confound in its purest form.

        With the prior off, the cubic follows the poison below 1.0 and
        MIN_LOCAL_EXP clamps the result: 1.040 at 8,000m with 25 segments
        held to the floor. That is the floor making a curve LEGAL. With the
        prior on, the fit never reaches the impossible region, the clamp
        fires ZERO times, and the curve reads 1.061 -- the physics, not a
        clamp.
        """
        clean = (800, 1000, 1200, 1500, 1600, 2000, 2400, 3000, 3200)
        pairs = []
        for i, a in enumerate(clean):
            for b in clean[i + 1:]:
                pairs += [_pair(a, b, k=1.10, t1=200.0 + j)
                          for j in range(30)]
        poison = (3200, 5000, 8000, 10000)
        for i, a in enumerate(poison):
            for b in poison[i + 1:]:
                pairs += [_pair(a, b, k=0.80, t1=600.0 + j)
                          for j in range(30)]

        fde.PRIOR_SLOPE_WEIGHT = 0.0
        loose = fde._fitOnePotential(pairs)
        fde.PRIOR_SLOPE_WEIGHT = 1.0
        tight = fde._fitOnePotential(pairs)

        self.assertEqual(loose["degree"], 3, "fixture must earn curvature")
        # the floor is doing the work, and saying so
        self.assertGreater(loose["floored_segments"], 20)
        self.assertAlmostEqual(fde._localExponent(loose, 8000),
                               loose["min_local_exp"], delta=1e-3,
                               msg="without the prior the long end IS the "
                                   "floor, not a measurement")
        # with the prior the clamp has nothing left to do
        self.assertEqual(tight["floored_segments"], 0,
                         "the prior must make the clamp redundant")
        k_tight = fde._localExponent(tight, 8000)
        self.assertGreater(k_tight, loose["min_local_exp"] + 0.01)
        self.assertLess(abs(k_tight - fde.K_PRIOR), 0.03)

    def test_a_heavy_prior_bleeds_inward_which_is_why_it_is_a_knob(self):
        # ! THE LIMIT OF THE APPROACH, PINNED SO IT CANNOT BE FORGOTTEN. The
        #   prior enters as rows in ONE global polynomial fit, so it cannot
        #   be perfectly local: crank it high enough and the interior moves
        #   too. Measured on the fixture above, k at 1,600m drifts
        #   1.122 -> 1.098 (w=1) -> 1.085 (w=8). Weight 1 is the setting to
        #   start from; anything much larger is fitting the prior.
        clean = (800, 1000, 1200, 1500, 1600, 2000, 2400, 3000, 3200)
        pairs = []
        for i, a in enumerate(clean):
            for b in clean[i + 1:]:
                pairs += [_pair(a, b, k=1.10, t1=200.0 + j)
                          for j in range(30)]
        poison = (3200, 5000, 8000, 10000)
        for i, a in enumerate(poison):
            for b in poison[i + 1:]:
                pairs += [_pair(a, b, k=0.80, t1=600.0 + j)
                          for j in range(30)]
        got = {}
        for w in (1.0, 8.0):
            fde.PRIOR_SLOPE_WEIGHT = w
            got[w] = fde._localExponent(fde._fitOnePotential(pairs), 1600)
        self.assertLess(got[8.0], got[1.0],
                        "a heavier prior must pull the interior too -- if it "
                        "did not, this test is measuring nothing")
        self.assertLess(got[1.0] - got[8.0], 0.05,
                        "but even a heavy prior must not take the interior "
                        "over")


if __name__ == "__main__":
    unittest.main()
