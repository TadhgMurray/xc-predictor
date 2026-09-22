# Project: xc-predictor / tests
# File:    test_xc_level_pin.py
# Purpose: cross country's level against the track is ASSERTED, and asserting
#          it does not touch the spread.
#
# ★★ THE MEASUREMENT (owner, 2026-09-21: "the xc difficutly is fucked ngl").
#    scripts/diag_difficulty_anchor.py on the live table, after the first
#    scope=merge run:
#
#        median XC course   +1.255%   over 46,853 cells
#        median TF venue    -0.015%   over 27,544 cells
#        XC - TF            +1.270%   expected +5.830%
#
#    Every reader of course_difficulties -- conversions.venueEffect,
#    difficulty_view, the course pages -- assumes joint_golive's convention:
#    the average outdoor track is 0.0, so an ordinary XC course carries the
#    grass cost, joint_solve.XC_TRACK_GAP = ln(1.06) = 0.0583.
#
#    The bracket engine had no term for it. Not a wrong one -- NONE; a grep
#    for sport_level in bracket_engine.py returned nothing, while
#    deploy/solve_env.sh has set XCP_SPORT_LEVEL=0.0583 all along and passed
#    it to run_joint --sport-level. The number was configured and honoured by
#    the JOINT model while XCP_DIFFICULTY=bracket published the BRACKET
#    engine's courses, which never saw it.
#
# ⚠ AND IT CANNOT BE MEASURED, WHICH IS WHY IT IS ASSERTED. mu is a
#   definition because the seasons do not overlap. scope=merge was the
#   attempt to measure it from athletes who race both -- a bridge reaching
#   0.2-0.5% of XC rows -- and +1.27% against an expected +5.83% is that
#   attempt's answer.
#
#   python -m unittest tests.test_xc_level_pin
import ast
import io
import os
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _src():
    with io.open(os.path.join(_ROOT, "engine", "bracket_engine.py"),
                 encoding="utf-8") as fh:
        return fh.read()


class TheArithmetic(unittest.TestCase):
    """★ RUN, NOT READ. The shift is four lines of numpy; a string assertion
    about them would have agreed with the version that had no term at all."""

    def setUp(self):
        import numpy as np
        self.np = np
        # PG_XC, PG_OUTDOOR, PG_INDOOR = 0, 1, 2
        self.PG_XC, self.PG_INDOOR = 0, 2

    def _shift(self, D, cell_pg, w, hard_ref, xc_level):
        """The engine's OWN lines, lifted out of the source and executed.

        ★ LIFTED, NOT COPIED. A transcription here would keep passing after
          someone edited the engine -- and the bug this test exists for was
          the engine having no such lines at all, which every string
          assertion in the file would have been happy with. The block is
          found by its `if pin_xc:` guard and run verbatim on arrays a real
          pack would take an hour to produce.
        """
        np = self.np
        src = _src()
        i = src.index("        if pin_xc:")
        j = src.index("        if pin_indoor:", i)
        block = "\n".join(l[8:] for l in src[i:j].rstrip().splitlines())
        ns = {"np": np, "pin_xc": True, "PG_XC": self.PG_XC,
              "cell_pg": np.asarray(cell_pg),
              "w_c_": np.asarray(w, dtype=float),
              "hard_ref": np.asarray(hard_ref, dtype=bool),
              "xc_level": float(xc_level),
              "D_new_": np.asarray(D, dtype=float)}
        exec(compile(block, "<bracket_engine:pin_xc>", "exec"), ns)  # noqa: S102
        return ns["D_new_"]

    def test_the_xc_mean_becomes_the_asserted_level(self):
        D = [0.02, -0.01, 0.00, 0.30]
        pg = [0, 0, 0, 2]                      # three XC cells, one indoor
        w = [10.0, 30.0, 60.0, 5.0]
        out = self._shift(D, pg, w, [False] * 4, 0.0583)
        xc = self.np.array(out)[:3]
        got = self.np.average(xc, weights=[10.0, 30.0, 60.0])
        self.assertAlmostEqual(got, 0.0583, places=12)

    def test_the_spread_is_untouched(self):
        """! ADDITIVE. Every course keeps its exact distance from every other
        -- that is what makes this a gauge choice and not a clamp."""
        D = [0.02, -0.01, 0.00]
        w = [10.0, 30.0, 60.0]
        out = self._shift(D, [0, 0, 0], w, [False] * 3, 0.0583)
        for i in range(len(D) - 1):
            self.assertAlmostEqual(out[i] - out[i + 1], D[i] - D[i + 1],
                                   places=12)
        self.assertAlmostEqual(float(self.np.std(out)),
                               float(self.np.std(D)), places=12)

    def test_it_moves_no_other_sport(self):
        """⚠ PG_XC ONLY. Touching a track cell would undo the gauge's zero,
        which is the thing cross country is being measured against."""
        D = [0.00, 0.00, 0.30]
        pg = [1, 1, 2]                          # outdoor, outdoor, indoor
        out = self._shift(D, pg, [5.0] * 3, [False] * 3, 0.0583)
        self.assertEqual(list(out), D)

    def test_a_named_reference_course_is_exempt(self):
        """! ~hard_ref. A course xc_reference.py names carries an ASSERTED
        value; sliding it with the mean would make it not a reference -- the
        mistake skip_recentre and the population shift already cost twice."""
        D = [0.02, -0.01, 0.08]
        pg = [0, 0, 0]
        w = [10.0, 30.0, 60.0]
        ref = [False, False, True]
        out = self._shift(D, pg, w, ref, 0.0583)
        self.assertAlmostEqual(out[2], 0.08, places=12)   # unmoved
        moved = self.np.array(out)[:2]
        self.assertAlmostEqual(
            self.np.average(moved, weights=[10.0, 30.0]), 0.0583, places=12)

    def test_a_cell_with_no_votes_does_not_vote(self):
        """! w > 0. A cell nobody raced has no reading, and letting it into
        the mean would let an unraced course define where the sport sits."""
        D = [0.02, 99.0]
        out = self._shift(D, [0, 0], [10.0, 0.0], [False, False], 0.0583)
        self.assertAlmostEqual(out[0], 0.0583, places=12)

    def test_it_is_idempotent(self):
        """! ASSERTING TWICE IS ASSERTING ONCE. The shift SETS the mean, it
        does not add to it -- so a second pass (a resumed solve, a re-gauge)
        cannot stack the grass cost on top of itself."""
        D = [0.02, -0.01, 0.00]
        w = [10.0, 30.0, 60.0]
        once = self._shift(D, [0, 0, 0], w, [False] * 3, 0.0583)
        twice = self._shift(once, [0, 0, 0], w, [False] * 3, 0.0583)
        for a, b in zip(once, twice):
            self.assertAlmostEqual(a, b, places=12)


class TheWiring(unittest.TestCase):

    def setUp(self):
        self.src = _src()

    def test_the_default_is_to_assert(self):
        ns = {}
        for node in ast.parse(self.src).body:
            if (isinstance(node, ast.Assign)
                    and getattr(node.targets[0], "id", "").startswith("XC_LEVEL")):
                exec(ast.get_source_segment(self.src, node), ns)   # noqa: S102
        self.assertEqual(ns["XC_LEVEL_MODE_DEFAULT"], "pin")
        self.assertIn("off", ns["XC_LEVEL_MODES"])

    def test_the_gap_is_joint_solves_number_not_a_second_copy(self):
        """⚠ ONE DEFINITION. A literal 0.0583 here would be the third copy of
        a constant this repo has already been bitten by owning twice."""
        i = self.src.index("if xc_level is None:")
        self.assertIn("XC_TRACK_GAP", self.src[i:i + 200])
        body = self.src[self.src.index("def fit("):]
        self.assertNotIn("xc_level = 0.0583", body)

    def test_the_shift_runs_before_the_indoor_pin(self):
        """! DISJOINT SETS, so order cannot matter -- but the two are read
        together and the file should show that."""
        self.assertLess(self.src.index("if pin_xc:"),
                        self.src.index("if pin_indoor:"))

    def test_a_run_says_whether_the_level_was_chosen_or_measured(self):
        self.assertIn("ASSERTED: xc_level=", self.src)
        self.assertIn("NOT asserted (xc_level_mode=off)", self.src)

    def test_it_warns_that_merge_and_the_pin_contradict(self):
        """★ scope=merge exists to MEASURE this level; the pin overwrites
        whatever it found. Running both is asking two questions and keeping
        one answer, so the run says so."""
        i = self.src.index("ASSERTED: xc_level=")
        self.assertIn("scope=merge tries to MEASURE", self.src[i:i + 1200])


if __name__ == "__main__":
    unittest.main(verbosity=2)
