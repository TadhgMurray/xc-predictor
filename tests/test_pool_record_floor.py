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
import math
import os
import sqlite3
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "engine"))

import record_pace as R                                        # noqa: E402


def evalSql(sql, t, d, pool):
    """Run the generated predicate. split_part and ln are stood up so the
    SQL can be checked without Postgres."""
    con = sqlite3.connect(":memory:")
    con.create_function("ln", 1, math.log)
    con.create_function(
        "split_part", 3,
        lambda s, sep, i: ((s or "").split(sep) + ["", ""])[i - 1])
    return bool(con.execute(
        f"SELECT {sql} FROM (SELECT ? AS t, ? AS d, ? AS pool)",
        (t, d, pool)).fetchone()[0])


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


class TheSqlIsTheSameRule(unittest.TestCase):
    """Two copies of a rule drift. This one is generated from the table,
    and this test is what proves it did not."""

    CASES = [(10.30, 100, "hs_m"), (9.30, 100, "hs_m"), (10.65, 100, "hs_f"),
             (125.0, 800, "ms_m"), (105.0, 800, "ms_m"),
             (9.30, 100, "college_m"), (800.0, 5000, "hs_m"),
             (42.0, 400, "hs_m"), (45.0, 400, "hs_m"), (240.0, 1609, "hs_f"),
             (None, 100, "hs_m"), (10.0, None, "hs_m"), (0.0, 100, "hs_m"),
             (60.0, 400, "ms_f"), (1000.0, 5000, "elem_m"),
             (700.0, 5000, "college_f"), (95.0, 800, "hs_m|TF"),
             (5.50, 55, "hs_m"), (6.20, 55, "hs_m"), (1571.0, 10000, "hs_m")]

    def test_every_case_agrees_with_the_python_rule(self):
        sql = R.paceFloorSql("t", "d", "pool")
        for t, d, pool in self.CASES:
            sex = "F" if pool.split("|")[0].endswith("_f") else "M"
            self.assertEqual(
                evalSql(sql, t, d, pool), R.impossibleRow(t, d, sex, pool),
                f"{pool} {d}m {t}s: the SQL and the Python disagree")

    def test_a_missing_fact_is_not_a_finding(self):
        sql = R.paceFloorSql("t", "d", "pool")
        self.assertFalse(evalSql(sql, None, 100, "hs_m"))
        self.assertFalse(evalSql(sql, 10.0, None, "hs_m"))

    def test_it_takes_no_bind_parameters(self):
        """It goes straight into a WHERE, so nothing from a request may
        reach it and there is nothing for a caller to bind."""
        self.assertNotIn("%s", R.paceFloorSql("t", "d", "pool"))
        self.assertNotIn("%(", R.paceFloorSql("t", "d", "pool"))

    def test_it_matches_segments_rather_than_escaped_like_patterns(self):
        """LIKE 'hs\\_%' means one thing in Postgres and another in SQLite,
        and a rule that differs between the harness and production is
        worse than no rule."""
        sql = R.paceFloorSql("t", "d", "pool")
        self.assertNotIn("LIKE", sql)
        self.assertIn("split_part", sql)

    def test_the_pr_board_applies_it_to_its_candidate_set(self):
        with open(os.path.join(_ROOT, "racecast", "rankings.py"),
                  encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn("from record_pace import paceFloorSql", src)
        self.assertIn(
            "paceFloorSql('time_seconds', 'distance', 'pool')", src)
        i = src.index("paceFloorSql('time_seconds'")
        self.assertIn("cand_where", src[max(0, i - 1500):i],
                      "a row filtered after the LIMIT has already taken a "
                      "slot a real time needed")


if __name__ == "__main__":
    unittest.main()
