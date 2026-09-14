"""A time nobody in this pool has run must not head a board.

★ THE SPRINTS WERE NOT IN THE TABLE (owner, 2026-09-14: "on best
  times/marks there's issues with wrong times for the sprint races being
  shown on the boards"). _WR_PACE began at 800 m and recordPace clamps
  below its first point, so a 100 m was judged against the 800 m record
  pace -- 126 s/km, i.e. 12.6 s for 100 m. Real sprint pace is far faster,
  so the rule's answer had the wrong sign down there.

★ AND THE OPEN RECORD IS A WEAK BAR FOR A SEVENTH GRADER (owner: "should
  prob throw something to stop crazy times for each pool -- whatever that
  pool's record is").
"""
import io
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "engine"))

import record_pace as R                                        # noqa: E402


class TheCurve(unittest.TestCase):

    def test_it_reaches_the_sprints(self):
        for sex in ("M", "F"):
            self.assertEqual(R._WR_PACE[sex][0][0], 55,
                             "the table must start at a sprint")

    def test_a_real_sprint_is_not_impossible(self):
        """The whole bug: every legitimate sprint read as faster than the
        record because the record was the 800 m's."""
        for t, d, sex in ((10.30, 100, "M"), (11.20, 100, "F"),
                          (20.80, 200, "M"), (23.50, 200, "F"),
                          (46.00, 400, "M"), (52.00, 400, "F"),
                          (6.60, 60, "M"), (6.20, 55, "M")):
            self.assertFalse(R.impossiblePace(t, d, sex),
                             f"{sex} {d}m {t}s is an ordinary sprint")

    def test_a_sprint_nobody_has_run_still_is(self):
        for t, d, sex in ((9.00, 100, "M"), (9.80, 100, "F"),
                          (40.00, 400, "M")):
            self.assertTrue(R.impossiblePace(t, d, sex))

    def test_the_curve_is_allowed_to_fall_and_rise(self):
        """100 m is the fastest pace ever run; 60 m and 200 m are slower.
        Nothing may assume the sequence only rises."""
        pts = R._WR_PACE["M"]
        paces = [p for _d, p in pts]
        self.assertLess(paces[2], paces[1], "100m is faster than 60m")
        self.assertLess(paces[2], paces[3], "100m is faster than 200m")

    def test_the_distance_records_are_untouched(self):
        self.assertAlmostEqual(R.recordPace(5000, "M"), 755.36 / 5.0, places=6)
        self.assertAlmostEqual(R.recordPace(10000, "F"), 1734.14 / 10.0,
                               places=6)


class ThePoolFloor(unittest.TestCase):

    def test_college_and_pro_are_exempt(self):
        self.assertTrue(R.exemptPool("college_m"))
        self.assertTrue(R.exemptPool("pro_f|TF"))
        self.assertFalse(R.impossibleRow(9.30, 100, "M", "college_m"))

    def test_a_young_pool_is_held_to_a_tighter_floor(self):
        self.assertTrue(R.impossibleRow(105.0, 800, "M", "ms_m"),
                        "1:45 for 800 is not a middle schooler")
        self.assertFalse(R.impossibleRow(125.0, 800, "M", "ms_m"),
                         "2:05 is fast for a middle schooler, not impossible")

    def test_the_high_school_floor_never_flags_a_real_high_school_record(self):
        """The girls' 100 m record is 10.65 against a 10.49 world record --
        1.5% apart -- which is why the HS factor is small and honest."""
        for t, d, pool in ((10.65, 100, "hs_f"), (10.13, 100, "hs_m"),
                           (44.69, 400, "hs_m"), (106.45, 800, "hs_m"),
                           (233.30, 1609, "hs_m"), (812.0, 5000, "hs_m")):
            sex = "F" if pool.endswith("_f") else "M"
            self.assertFalse(R.impossibleRow(t, d, sex, pool),
                             f"{pool} {d}m {t}s is a real record")

    def test_an_unknown_pool_is_judged_by_the_open_record_alone(self):
        self.assertEqual(R.poolFactor("something_m"), 1.0)
        self.assertEqual(R.poolFactor(None), 1.0)

    def test_the_factor_reads_either_spelling(self):
        self.assertEqual(R.poolFactor("ms_f"), R.poolFactor("ms_f|TF"))


class GatedInTheBuild(unittest.TestCase):
    """★ NOT ON THE BOARD (owner, 2026-09-14: "the thing stopping wrong
    sprint races should be in building ranking resutls so rloading the page
    doesnt take even longer"). The first version of this put the rule in
    the best-times board's candidate WHERE as a generated SQL predicate --
    a piecewise log-interpolated curve evaluated per candidate row, on
    every page load, on the board that had just been reported slow. A row
    that is not a performance belongs out of ranking_results; then every
    board is clean for free."""

    def build(self):
        with io.open(os.path.join(_ROOT, "racecast",
                                  "build_ranking_results.py"),
                     encoding="utf-8") as fh:
            return fh.read()

    def test_the_build_applies_the_full_rule(self):
        src = self.build()
        self.assertIn(
            "impossibleRow(row.time_seconds, distance, row.gender, pool)", src)

    def test_the_pool_floor_reaches_the_build(self):
        """impossibleRow is the one call that carries it; impossiblePace
        alone is the open record and would leave the young pools open."""
        src = self.build()
        i = src.index("impossibleRow(row.time_seconds")
        self.assertNotIn("impossiblePace(row.time_seconds",
                         src[max(0, i - 600):i + 200])
        self.assertIn("impossibleRow", src[src.index("from record_pace import"):
                                           src.index("from record_pace import")
                                           + 200])

    def test_the_exemption_is_inside_it(self):
        """impossibleRow checks exemptPool itself, so the caller cannot
        forget it the way the open-record call had to remember."""
        with io.open(os.path.join(_ROOT, "engine", "record_pace.py"),
                     encoding="utf-8") as fh:
            rp = fh.read()
        body = rp[rp.index("def impossibleRow"):]
        self.assertIn("if exemptPool(pool):", body)

    def test_the_board_does_not_re_check_it(self):
        with io.open(os.path.join(_ROOT, "racecast", "rankings.py"),
                     encoding="utf-8") as fh:
            src = fh.read()
        self.assertNotIn("paceFloorSql", src,
                         "the board must not pay per row for a rule the "
                         "build already applied once")

    def test_the_sql_generator_is_gone_rather_than_left_unused(self):
        with io.open(os.path.join(_ROOT, "engine", "record_pace.py"),
                     encoding="utf-8") as fh:
            rp = fh.read()
        self.assertNotIn("paceFloorSql", rp)
        self.assertNotIn("_paceCase", rp)


if __name__ == "__main__":
    unittest.main()
