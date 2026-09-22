# Project: xc-predictor / tests
# File:    test_ext_slope_borrow.py
# Purpose: a pool with no tail pairs borrows a measured high-side fade
#          instead of inheriting a cubic's boundary tangent.
#
# ★★ THE MEASUREMENT (2026-09-22). Every pool in the shipped artifact that
#    could measure its own high-side extension agreed within 0.006:
#
#        hs_m|TF     15,182 tail pairs   own 1.0747
#        hs_f|TF      6,820              own 1.0711
#        ms_m|TF        711              own 1.0771
#        elem_m|TF      978              own 1.1011
#        elem_f|TF      617              own 1.1481
#        ---------------------------------------------
#        college_m|TF       4            NONE -> tangent 1.047
#        college_f|TF      21            NONE -> tangent
#        ms_f|TF          283            NONE -> tangent (17 short of 300)
#
#    MIN_EXT_PAIRS is 300. The pools that cleared it measured a fade; the
#    pools that did not fell back to the tangent of a cubic fitted on 32
#    edges, which has no claim to be a fade at all.
#
#    Independently, from season bests (scripts/diag_event_pairs.py, 30,142
#    college-men athlete-seasons), the real 10,000->5,000 fade is 1.071
#    against the shipped tangent's 1.047 -- sixteen seconds of 5K-equivalent
#    on every collegiate track 10k, because one pool had four tail pairs.
#
# ★ eps ALREADY HAD THIS RUNG: own -> borrowed -> none, with eps_source
#   recorded per pool. The extension slope had own -> tangent, missing the
#   middle. This is that rung, built the same way and from the same pass.
#
#   python -m unittest tests.test_ext_slope_borrow
import ast
import io
import math
import os
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC = os.path.join(_ROOT, "engine", "fit_distance_exponent.py")


def _src():
    with io.open(_SRC, encoding="utf-8") as fh:
        return fh.read()


def _load(names):
    """The named top-level functions/constants, executed without importing
    the module (it pulls scipy, numpy and a database config)."""
    src = _src()
    ns = {"math": math}
    import numpy as np
    ns["np"] = np
    for node in ast.parse(src).body:
        nm = (getattr(node, "name", None)
              or getattr(getattr(node, "targets", [None])[0], "id", None)
              if not isinstance(node, ast.Assign)
              else getattr(node.targets[0], "id", None))
        if nm in names:
            exec(ast.get_source_segment(src, node), ns)          # noqa: S102
    return ns


class TheTableOnlyCountsOwnMeasurements(unittest.TestCase):

    def setUp(self):
        self.ns = _load({"_sportExtSlopeTable"})
        self.fn = self.ns["_sportExtSlopeTable"]

    def _entry(self, slope, n):
        return {"ext_slope_hi": slope, "n_ext_pairs": n}

    def test_a_pool_with_no_slope_does_not_vote(self):
        first = {("TF", "hs_m"): (self._entry(1.0747, 15182), 9999),
                 ("TF", "college_m"): (self._entry(None, 4), 9999)}
        self.assertAlmostEqual(self.fn(first)["TF"], 1.0747, places=6)

    def test_the_median_is_pair_weighted(self):
        """! WEIGHTED BY TAIL PAIRS, not by pools. A pool with 617 pairs must
        not outvote one with 15,182."""
        first = {("TF", "hs_m"): (self._entry(1.0747, 15182), 1),
                 ("TF", "hs_f"): (self._entry(1.0711, 6820), 1),
                 ("TF", "elem_f"): (self._entry(1.1481, 617), 1),
                 ("TF", "elem_m"): (self._entry(1.1011, 978), 1),
                 ("TF", "ms_m"): (self._entry(1.0771, 711), 1)}
        got = self.fn(first)["TF"]
        self.assertAlmostEqual(got, 1.0747, places=6)

    def test_the_median_resists_a_different_regime(self):
        """⚠ elem's own slopes run 1.10-1.15 because its high side is a
        different physical regime (a span of 800-3000 m). A MEAN would let
        them drag the number the 5K-and-up pools agree on."""
        first = {("TF", "hs_m"): (self._entry(1.075, 15000), 1),
                 ("TF", "elem_f"): (self._entry(1.60, 900), 1)}
        self.assertAlmostEqual(self.fn(first)["TF"], 1.075, places=6)

    def test_sports_do_not_mix(self):
        first = {("TF", "hs_m"): (self._entry(1.0747, 15182), 1),
                 ("XC", "hs_m"): (self._entry(1.2000, 9000), 1)}
        t = self.fn(first)
        self.assertAlmostEqual(t["TF"], 1.0747, places=6)
        self.assertAlmostEqual(t["XC"], 1.2000, places=6)

    def test_nothing_measured_means_nothing_to_borrow(self):
        """! AND THE CALLER MUST COPE. No entry is better than a made-up one."""
        first = {("TF", "college_m"): (self._entry(None, 4), 1)}
        self.assertEqual(self.fn(first), {})


class BorrowingIsExact(unittest.TestCase):
    """★ RE-SAMPLED, NOT RE-FITTED. s_hi_override enters only
    _sampleClamped, which reads coeffs, knots and span -- none of which
    depends on the slope. This proves the shortcut equals the long way, on
    the shipped artifact's own coefficients."""

    def setUp(self):
        self.ns = _load({"_reslopeEntry", "_sampleClamped", "_polyEval",
                         "_polyDeriv", "MIN_LOCAL_EXP",
                         "TARGET_DISTANCE_METERS"})
        # ! engine/ IS NOT ON sys.path FOR A TEST RUN from the project
        #   root; the fitter gets it because the pipeline puts it there.
        import sys
        if os.path.join(_ROOT, "engine") not in sys.path:
            sys.path.insert(0, os.path.join(_ROOT, "engine"))
        from distance_shape import floorLocalExponent
        self.ns["_floorLocalExponent"] = floorLocalExponent

    def _shipped(self):
        import pickle
        with open(os.path.join(_ROOT, "engine", "data",
                               "distance_spline.pkl"), "rb") as fh:
            art = pickle.load(fh)
        e = dict(art["pools"]["college_m|TF"])
        e.setdefault("ext_slope_hi", None)
        return e

    def test_it_matches_a_full_resample_at_the_new_slope(self):
        e = self._shipped()
        got = self.ns["_reslopeEntry"](e, 1.0747)
        want, _fl = self.ns["_floorLocalExponent"](
            e["knots"],
            self.ns["_sampleClamped"](e["coeffs"], e["knots"],
                                      math.log(e["span"][0]),
                                      math.log(e["span"][1]),
                                      s_hi_override=1.0747),
            self.ns["MIN_LOCAL_EXP"])
        self.assertEqual(got["values"], want)

    def test_it_changes_only_the_tail(self):
        """! IN-SPAN VALUES ARE THE CUBIC and must not move; the whole point
        is that borrowing touches the extrapolation and nothing else."""
        e = self._shipped()
        got = self.ns["_reslopeEntry"](e, 1.0747)
        hi = math.log(e["span"][1])
        for k, before, after in zip(e["knots"], e["values"], got["values"]):
            if k <= hi:
                self.assertAlmostEqual(before, after, places=12)

    def test_it_raises_the_long_end(self):
        """The direction that matters: a 1.047 tangent under-fades, so the
        borrowed 1.0747 must make a 10k read FASTER over 5K, not slower."""
        e = self._shipped()
        got = self.ns["_reslopeEntry"](e, 1.0747)
        i = len(e["knots"]) - 1
        self.assertGreater(got["values"][i], e["values"][i])

    def test_a_pool_that_measured_its_own_is_left_alone(self):
        """⚠ BORROWING NEVER OVERRIDES A MEASUREMENT."""
        e = dict(self._shipped())
        e["ext_slope_hi"] = 1.0747
        self.assertIs(self.ns["_reslopeEntry"](e, 1.20), e)

    def test_it_records_where_the_slope_came_from(self):
        got = self.ns["_reslopeEntry"](self._shipped(), 1.0747)
        self.assertEqual(got["ext_slope_source"], "borrowed")


class TheWiring(unittest.TestCase):

    def setUp(self):
        self.src = _src()

    def test_every_entry_says_where_its_slope_came_from(self):
        self.assertIn('"ext_slope_source": ("own" if ext_slope is not None '
                      'else "tangent")', self.src)

    def test_the_borrow_happens_before_certification(self):
        """! THE GATE MUST JUDGE THE CURVE THAT SHIPS. Certifying the
        tangent version and swapping the slope underneath it afterwards
        would be a curve nothing checked."""
        i = self.src.index("_reslopeEntry(entry, ext_slopes[sport])")
        j = self.src.index("fitted = _certifyEntry(", i - 2000)
        self.assertLess(i, self.src.index("fitted = _certifyEntry(", i))
        self.assertGreater(i, j if j < i else -1)

    def test_the_table_is_built_from_pass_one(self):
        """! ONLY OWN MEASUREMENTS VOTE, which is only true if the table is
        built before pass 2 starts borrowing into entries."""
        self.assertLess(self.src.index("ext_slopes = _sportExtSlopeTable(first)"),
                        self.src.index("_reslopeEntry(entry, ext_slopes[sport])"))

    def test_the_run_prints_what_it_borrowed(self):
        self.assertIn("BORROWED", self.src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
