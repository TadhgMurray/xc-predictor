# Project: xc-predictor / tests
# File:    test_difficulty_calibration.py
# Purpose: the calibration diagnostic's judgement, without a database.
#
#   python tests/test_difficulty_calibration.py
#
# ★ THE OWNER'S QUESTION (2026-09-18): "does the median of each percentile
#   runner actually have their rating increase by such, or are they overfed."
#
# ⚠⚠ THE TABLE IS EASY TO READ BACKWARDS, and that is the risk worth a test.
#    A FLAT column is the PASS: if difficulty is honest it has already been
#    removed from the rating, so like-for-like positions score the same on hard
#    and easy courses. A RISING column is the bug.
import ast
import io
import os
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load():
    with io.open(os.path.join(_ROOT, "engine",
                             "diag_difficulty_calibration.py"),
                 encoding="utf-8") as fh:
        src = fh.read()
    ns = {}
    for node in ast.parse(src).body:
        if isinstance(node, ast.FunctionDef) and node.name == "slope":
            exec(ast.get_source_segment(src, node), ns)      # noqa: S102
    return ns["slope"], src


class TheSlope(unittest.TestCase):

    def setUp(self):
        self.slope, self.src = _load()

    def test_a_calibrated_difficulty_is_flat(self):
        self.assertEqual(self.slope([1, 2, 3, 4], [150, 150, 150, 150]), 0.0)

    def test_overfeeding_shows_as_a_positive_slope(self):
        self.assertAlmostEqual(
            self.slope([1, 2, 3, 4], [150, 152, 154, 156]), 2.0)

    def test_under_crediting_shows_as_negative(self):
        self.assertLess(self.slope([1, 2, 3, 4], [156, 154, 152, 150]), 0)

    # ! UNDEFINED RATHER THAN ZERO. One bin, or every bin at the same
    #   difficulty, is no evidence -- reporting 0.0 there would read as "flat,
    #   calibrated", which is the opposite of "we cannot tell".
    def test_too_little_evidence_is_none_not_flat(self):
        self.assertIsNone(self.slope([1], [150]))
        self.assertIsNone(self.slope([2, 2], [150, 160]))
        self.assertIsNone(self.slope([], []))

    def test_missing_cells_are_skipped_not_counted_as_zero(self):
        self.assertAlmostEqual(
            self.slope([1, 2, 3, 4], [150, None, 154, 156]), 2.0, places=1)


class TheDesignThatMakesItMeanAnything(unittest.TestCase):

    def setUp(self):
        self.slope, self.src = _load()

    # ⚠⚠ THE PERCENTILE MUST BE WITHIN THE RACE. Comparing everyone on hard
    #    courses with everyone on easy ones measures who SHOWS UP --
    #    championship courses are hard AND hold better fields, an effect far
    #    larger than the calibration error being looked for.
    def test_the_percentile_is_computed_inside_each_race(self):
        self.assertIn("percent_rank() OVER (PARTITION BY r.div_id, r.source",
                      self.src)
        self.assertIn("count(*) OVER (PARTITION BY r.div_id, r.source",
                      self.src)

    def test_small_fields_and_thin_courses_are_excluded(self):
        self.assertIn("r.field >= %(min_field)s", self.src)
        self.assertIn("COALESCE(n_results, 0) >= %(min_course)s", self.src)

    # ! A ROBUST CENTRE, like every other summary in this work: one absurd
    #   rating in a cell must not move the cell.
    def test_the_cell_statistic_is_a_median(self):
        self.assertIn("percentile_cont(0.5) WITHIN GROUP (ORDER BY r.rating)",
                      self.src)

    def test_the_ctes_are_materialized(self):
        self.assertEqual(self.src.count("AS MATERIALIZED"), 3)

    def test_it_writes_nothing(self):
        code = self.src.split('"""', 2)[2]
        for danger in ("INSERT", "UPDATE", "DELETE", "TRUNCATE", "conn.commit"):
            self.assertNotIn(danger, code, danger)

    # ★ AND IT SAYS WHICH WAY IS GOOD, in the output itself.
    def test_the_output_explains_that_flat_is_the_pass(self):
        self.assertIn("flat -- difficulty looks calibrated", self.src)
        self.assertIn("OVERFED", self.src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
