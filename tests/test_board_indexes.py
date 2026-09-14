"""The boards that ORDER BY the clock need the same index shape the boards
that order by the rating already have.

★ "best times/marks is pretty slow, same with performances" (owner,
  2026-09-14). rr_board_time_idx is (pool, sport, year, time_seconds), so
  it can only reach its ordering column when a YEAR pins the third
  position -- and the Academic year filter defaults to Any. The default
  Best-times board therefore sorted every row of the pool to take the
  first fifty. rr_pool_rating_idx was added for exactly this on the rating
  boards; the time boards never got the equivalent.
"""
import io
import os
import re
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def indexes():
    with io.open(os.path.join(_ROOT, "racecast", "build_ranking_results.py"),
                 encoding="utf-8") as fh:
        src = fh.read()
    block = src[src.index('_CANONICAL_INDEXES = {'):]
    block = block[:block.index("\n}")]
    return dict(re.findall(r'\("(\w+)",\s*\n?\s*"([^"]+)"', block))


class BoardIndexes(unittest.TestCase):

    def setUp(self):
        self.idx = indexes()

    def test_the_time_boards_can_order_without_a_year(self):
        self.assertIn("rr_pr_time_idx", self.idx)
        self.assertTrue(self.idx["rr_pr_time_idx"].startswith("(pool,"))
        self.assertTrue(self.idx["rr_pr_time_idx"].endswith("time_seconds)"))

    def test_the_distance_is_inside_the_key_not_a_filter_after_it(self):
        """The PR board ranks the clock AT ONE DISTANCE -- that is what the
        board is. A range filter between the equality and the ordering
        brings the sort back."""
        for name in ("rr_pr_time_idx", "rr_pr_sport_time_idx"):
            cols = self.idx[name]
            self.assertLess(cols.index("distance"), cols.index("time_seconds"),
                            f"{name}: distance must precede the ordering")

    def test_both_shapes_exist_because_sport_may_be_both(self):
        """sport='both' adds no predicate at all, so an index with sport in
        the second position cannot be used."""
        self.assertIn("rr_pr_time_idx", self.idx)
        self.assertIn("rr_pr_sport_time_idx", self.idx)
        self.assertNotIn("sport", self.idx["rr_pr_time_idx"])
        self.assertIn("sport", self.idx["rr_pr_sport_time_idx"])

    def test_performances_keeps_its_ordering_once_a_distance_is_picked(self):
        cols = self.idx["rr_perf_dist_rating_idx"]
        self.assertLess(cols.index("distance"), cols.index("speed_rating"))

    def test_the_originals_are_untouched(self):
        """These were measured; nothing here replaces them."""
        self.assertEqual(self.idx["rr_board_time_idx"],
                         "(pool, sport, year, time_seconds)")
        self.assertEqual(self.idx["rr_pool_rating_idx"],
                         "(pool, speed_rating DESC)")


if __name__ == "__main__":
    unittest.main()
