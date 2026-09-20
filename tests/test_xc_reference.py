# Project: xc-predictor / tests
# File:    test_xc_reference.py
# Purpose: cross country's absolute anchor -- the named reference courses, and
#          the see-saw they replace.
#
# ★★ WHY XC HAS NO ANCHOR TODAY. The bracket engine pins each (sport, era)
#    group's VOTE-WEIGHTED MEAN to zero. For track that mean is replaced by a
#    physical reference (a flat outdoor 400 IS 0.0). For XC there is no such
#    class, so the mean IS the zero -- and sum(w*D) = 0 means that if the
#    heavily-raced courses read high, every other course is negative BY
#    CONSTRUCTION. That is the owner's standing complaint, and it is a
#    property of the constraint rather than of the courses.
#
# ⚠ AND THE TRACK ANCHOR CANNOT REACH XC -- measured, not argued
#   (diag_xc_track_bridge.py, 2026-09-20, 5% athlete sample): at the bracket
#   window the XC-to-indoor bridge reaches 0.2-0.5% of XC rows, 2.7% even at
#   60 days. Merging the gauge groups would anchor cross country on a
#   half-percent subset. REFUTED -- re-run that script before proposing it.
#
#   python -m unittest tests.test_xc_reference
import io
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _env  # noqa: E402,F401
for _p in (_ROOT, os.path.join(_ROOT, "engine"), os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np                                            # noqa: E402
import xc_reference as xr                                     # noqa: E402


def read(rel):
    with io.open(os.path.join(_ROOT, *rel.split("/")), encoding="utf-8") as fh:
        return fh.read()


class TheKeyIsAStableIdentity(unittest.TestCase):

    def test_a_numeric_canonical_id_is_the_identity(self):
        self.assertEqual(xr.canonicalId("XC:12345:d5000"), 12345)
        self.assertEqual(xr.canonicalId("XC:12345:d5000@e3"), 12345)
        self.assertEqual(xr.canonicalId("XC:12345"), 12345)

    def test_an_unplaced_course_can_never_be_the_anchor(self):
        """! ONLY THE NUMERIC FORM IS AN IDENTITY. 'name:<x>' and raw strings
        are what venueKeyParts emits when course_canonical could not place the
        venue; they are not stable across rebuilds, so a zero resting on one
        would move when the clustering is re-run."""
        for k in ("XC:name:Some Park:d5000", "XC:Some Park:d5000", "XC:"):
            self.assertIsNone(xr.canonicalId(k), k)

    def test_track_keys_are_not_xc(self):
        for k in ("TF:loc:7:out", "TF:loc:7:in@e2"):
            self.assertIsNone(xr.canonicalId(k), k)


class TheMask(unittest.TestCase):

    KEYS = ["XC:12345:d5000@e0", "XC:999:d8000@e0",
            "TF:loc:7:out@e0", "XC:name:foo:d5000"]

    def test_empty_is_the_default_and_pins_nothing(self):
        """! A HALF-APPLIED ANCHOR IS THE ONE STATE THAT MUST BE IMPOSSIBLE.
        With nothing named the mask is all-False and the engine falls back to
        the old mean pin, announced."""
        self.assertEqual(xr.XC_REFERENCE, {})
        mask, val = xr.referenceMask(self.KEYS)
        self.assertFalse(mask.any())

    def test_named_courses_carry_their_asserted_value(self):
        mask, val = xr.referenceMask(self.KEYS, {12345: 0.0, 999: 0.012})
        self.assertEqual(list(mask), [True, True, False, False])
        self.assertAlmostEqual(val[0], 0.0)
        self.assertAlmostEqual(val[1], 0.012)

    def test_every_distance_at_a_named_course_is_pinned(self):
        """★ THE OWNER NAMES A COURSE, NOT A CELL. A 5000 and a 5-mile at the
        same park are two keys and one course."""
        keys = ["XC:77:d5000@e0", "XC:77:d8000@e0", "XC:78:d5000@e0"]
        mask, _ = xr.referenceMask(keys, {77: 0.0})
        self.assertEqual(list(mask), [True, True, False])


class TheSeeSawIsWhatThisReplaces(unittest.TestCase):
    """The engine's pin arithmetic, on cells shaped like the complaint."""

    def setUp(self):
        self.D = np.array([0.030, 0.028, 0.032, 0.006, 0.005, 0.007, 0.004])
        self.w = np.array([900., 850., 800., 60., 55., 50., 45.])
        self.ref = np.array([False] * 3 + [True] * 4)

    def _pin(self, gauge_ref):
        use = gauge_ref if gauge_ref.any() else np.ones(self.D.size, bool)
        return self.D - np.average(self.D[use], weights=self.w[use])

    def test_the_mean_pin_forces_courses_negative(self):
        """⚠ THIS IS THE COMPLAINT, AS ARITHMETIC. sum(w*D) = 0 and the
        heavily-raced courses read high, so the rest MUST be negative. No
        amount of better fitting changes it -- it is the constraint."""
        out = self._pin(np.ones(self.D.size, bool))
        self.assertAlmostEqual(float(np.average(out, weights=self.w)), 0.0)
        self.assertGreaterEqual(int((out < 0).sum()), 4)

    def test_the_reference_pin_does_not(self):
        """★ SAME DATA, DIFFERENT ZERO. The named ordinary courses are 0 and
        the heavily-raced ones are correctly positive."""
        out = np.where(self.ref, 0.0, self._pin(self.ref))
        self.assertEqual(int((out < 0).sum()), 0)
        self.assertGreater(out[0], 0.02)


class TheEngineUsesIt(unittest.TestCase):

    def setUp(self):
        self.src = read("engine/bracket_engine.py")

    def test_the_xc_cells_join_the_gauge_reference(self):
        """★ NO CHANGE TO THE PIN LOOP AT ALL. It already does
        `m_ref = m_g & gauge_ref; use = m_ref if m_ref.any() else m_g`, so
        adding XC cells to gauge_ref anchors the XC groups on them."""
        self.assertIn("gauge_ref = gauge_ref | xc_mask", self.src)
        self.assertIn("hard_ref = hard_ref | xc_mask", self.src)
        self.assertIn("use = m_ref if m_ref.any() else m_g", self.src)

    def test_the_pin_carries_a_value_not_a_hard_zero(self):
        """! 0.0 IS RIGHT FOR A FLAT OUTDOOR 400 -- that IS the zero. A named
        XC course may be asserted at something else, so the pin is a value."""
        self.assertIn("hard_val", self.src)
        self.assertIn("(w_c_ > 0), hard_val, D_new_", self.src)

    def test_it_says_so_when_nothing_is_named(self):
        """! NOBODY MAY BELIEVE XC IS ANCHORED WHEN IT IS NOT."""
        self.assertIn("NONE NAMED", self.src)
        self.assertIn("xc_reference.py --suggest", self.src)

    def test_an_import_failure_degrades_rather_than_kills_the_solve(self):
        i = self.src.index("import xc_reference as xr")
        self.assertIn("except Exception", self.src[i:i + 400])


class TheRefutationIsOnRecord(unittest.TestCase):
    """⚠ SO NOBODY PROPOSES MERGING THE GAUGE GROUPS AGAIN without re-running
    the measurement that refuted it."""

    def test_the_numbers_are_written_down_where_the_decision_lives(self):
        src = read("engine/xc_reference.py")
        self.assertIn("diag_xc_track_bridge", src)
        for token in ("0.2%", "2.7%", "REFUTED"):
            self.assertIn(token, src)

    def test_and_beside_the_engine_change(self):
        self.assertIn("diag_xc_track_bridge.py", read("engine/bracket_engine.py"))


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TheMergeScopeIsAvailableButNotTheDefault(unittest.TestCase):
    """★ owner, 2026-09-20: "idk maybe 3 % could be useful let's try it out".

    merge drops the SPORT from the group key, so XC cells and the flat-400
    reference cells land in one group per era and the existing pin loop
    anchors both on the reference. That is the whole mechanism.

    ⚠ IT IS NOT THE DEFAULT because the bridge it rests on was measured at
      0.2-0.5% of XC rows at the bracket window. The run prints how far XC
      moved and what share of XC weight still reads negative, because that --
      not the flag existing -- is the test.
    """

    def setUp(self):
        self.src = read("engine/bracket_engine.py")

    def test_the_default_is_unchanged_behaviour(self):
        import bracket_engine as be
        self.assertEqual(be.GAUGE_SCOPE_DEFAULT, "sport")
        self.assertEqual(be.GAUGE_SCOPES, ("sport", "merge"))

    def test_merge_drops_the_sport_from_the_group_key(self):
        self.assertIn('if gauge_scope == "merge":', self.src)
        self.assertIn("cell_group = cell_era.copy()", self.src)

    def test_an_unknown_scope_is_refused(self):
        self.assertIn("gauge_scope must be one of", self.src)

    def test_the_run_reports_how_far_xc_moved(self):
        self.assertIn("gauge scope=", self.src)
        self.assertIn("reads NEGATIVE", self.src)

    def test_the_flag_reaches_the_engine_from_the_pipeline(self):
        """⚠ THE LESSON --gauge TAUGHT: an option no caller forwards silently
        takes the default."""
        rj = read("engine/run_joint.py")
        self.assertIn('ap.add_argument("--gauge-scope"', rj)
        self.assertIn('place_kw["gauge_scope"] = gauge_scope', rj)
        self.assertIn('gauge_scope=getattr(args, "gauge_scope", None)', rj)
        self.assertIn("--gauge-scope", read("deploy/run_pipeline.sh"))
        self.assertIn("XCP_GAUGE_SCOPE", read("deploy/solve_env.sh"))
