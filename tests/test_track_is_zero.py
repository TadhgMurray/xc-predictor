# Project: xc-predictor / tests
# File:    test_track_is_zero.py
# Purpose: The average track displays 0.0 -- AND SO DOES THE AVERAGE CROSS
#          COUNTRY COURSE. One display zero per sport.
#
# ★★★ TWO BUGS, OPPOSITE DIRECTIONS, ONE FILE. Both shipped.
#
#   2026-09-10: difficulty_view subtracted ONE corpus-wide mean from BOTH
#     sports. Cross country dominates the corpus, so that mean was about
#     +6% and it came off TRACKS TOO -- a typical track displayed near -6%
#     and the owner asked three times why track was not zero.
#
#   2026-09-11: the fix for that made the display the identity, so the
#     engine's track anchor reached the page raw. An ordinary XC course is
#     about +7% against a flat 400m oval, so EVERY cross country course
#     read seven points harder than anyone would call it. The Hydrangea
#     Ranch -- where the Ultimook Race is run -- published +13.26%. The
#     owner: "maybe a 7-9% max course. NOT A FUCKING 13% course." Against
#     other XC courses it is +5.9%, which is his number. He was told for
#     two days that the model might be broken. The model was right.
#
# ⚠ THE RULE THAT SATISFIES BOTH: subtract the mean OF THAT SPORT. The
#   stored scale is untouched -- one line, track at zero, exactly as
#   joint_golive writes it. The page shows a course against a typical
#   course of its own sport, because that is the question a course page is
#   being asked. sportGapPct() still reports the distance between the two
#   zeros, which is a real measurement and belongs on a page that compares
#   the sports.
import math
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "racecast")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# the corpus this file argues about: an average XC course is +6.9% on the
# stored (track-anchored) scale, an average track is 0.0 by construction
XC_MEAN = math.log(1.069)
TF_MEAN = 0.0


class OneZeroPerSport(unittest.TestCase):
    def setUp(self):
        import difficulty_view as dv
        self.dv = dv
        dv._state["at"] = float("inf")          # never refresh from a DB
        dv._state["XC"] = XC_MEAN
        dv._state["TF"] = TF_MEAN

    def test_the_average_track_reads_zero(self):
        """⚠ THE 2026-09-10 BUG. A track at the track mean must not read
        -6% because cross country dominates the corpus."""
        self.assertAlmostEqual(self.dv.relativePct(0.0, "TF"), 0.0, places=6)

    def test_the_average_xc_course_reads_zero(self):
        """⚠ THE 2026-09-11 BUG. An ordinary XC course must not read +6.9%
        just because a flat oval is the stored zero."""
        self.assertAlmostEqual(self.dv.relativePct(0.069, "XC"), 0.0,
                               places=6)

    def test_hydrangea(self):
        """★ THE ONE THAT COST TWO DAYS. Stored +13.26%; against other
        cross country courses it is about +5.9%, not +13.3%."""
        got = self.dv.relativePct(0.1326, "XC")
        self.assertAlmostEqual(got, 5.947, places=2)
        self.assertLess(got, 9.0, "still reads harder than the owner's "
                                  "7-9% read of the terrain")

    def test_the_sport_argument_now_matters(self):
        """It used to be required NOT to. The same stored number is a
        different course depending on which sport's typical course it is
        being compared with."""
        self.assertNotAlmostEqual(self.dv.relativePct(0.069, "XC"),
                                  self.dv.relativePct(0.069, "TF"), places=3)

    def test_a_hard_course_stays_hard(self):
        """Subtracting a zero is not flattening: order is preserved and a
        genuinely hard course still reads hard."""
        easy = self.dv.relativePct(0.02, "XC")
        mid = self.dv.relativePct(0.069, "XC")
        hard = self.dv.relativePct(0.1326, "XC")
        self.assertLess(easy, mid)
        self.assertLess(mid, hard)
        self.assertLess(easy, 0.0)

    def test_a_fast_track_reads_negative(self):
        self.assertLess(self.dv.relativePct(-0.01, "TF"), 0.0)


class TheSportGapIsStillReported(unittest.TestCase):
    """★ IT MOVED, IT DID NOT VANISH. Two display zeros hide a real
    measured difference; a page that compares the sports can still say it."""

    def setUp(self):
        import difficulty_view as dv
        self.dv = dv
        dv._state["at"] = float("inf")
        dv._state["XC"] = XC_MEAN
        dv._state["TF"] = TF_MEAN

    def test_the_gap_is_the_xc_premium(self):
        self.assertAlmostEqual(self.dv.sportGapPct(), 6.9, places=1)

    def test_the_guard_still_reads_the_stored_track_anchor(self):
        """joint_golive anchors the average track at zero; if the stored
        table says otherwise it was written by a different engine and that
        must stay visible rather than be silently absorbed."""
        self.dv._state["TF"] = 0.0583
        self.assertAlmostEqual(self.dv.trackDriftLog(), 0.0583, places=6)

    def test_the_guard_reads_courses_not_results(self):
        self.assertIn("TF:", self.dv._SQL)
        self.assertNotIn("n_results", self.dv._SQL,
                         "the zero is the average COURSE, not the average "
                         "result")

    def test_both_sports_are_read_in_one_query(self):
        self.assertIn("GROUP", self.dv._SQL.upper())


class NonsenseIsRejected(unittest.TestCase):
    def setUp(self):
        import difficulty_view as dv
        self.dv = dv
        dv._state["at"] = float("inf")
        dv._state["XC"] = XC_MEAN
        dv._state["TF"] = TF_MEAN

    def test_none_and_junk(self):
        for bad in (None, "", "abc", -0.95, -1.0):
            self.assertIsNone(self.dv.relativePct(bad, "XC"), repr(bad))

    def test_the_filter_says_so(self):
        self.assertEqual(self.dv.diffPct(None), " - ")
        self.assertEqual(self.dv.diffPct(0.069, "XC"), "0.0%")

    def test_words_are_sport_relative(self):
        self.assertIn("about the same", self.dv.diffWords(0.069, "XC"))
        self.assertIn("slower", self.dv.diffWords(0.1326, "XC"))


if __name__ == "__main__":
    unittest.main()
