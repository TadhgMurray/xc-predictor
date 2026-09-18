# Project: xc-predictor / tests
# File:    test_team_pool.py
# Purpose: the pool precedence, which is the whole rule. Pure -- classify()
#          takes no database, so the owner's decisions are pinned as arithmetic.
#
#   python tests/test_team_pool.py
#
# ★ THE OWNER'S RULES (2026-09-18), each agreed separately:
#     - anet level 16 is a club (measured in the census: Oregon Track Club,
#       Nike Oregon Track Club, Georgetown Running Club);
#     - a club with ANY professional in it is pro;
#     - fewer than 15 distinct athletes OVER ALL TIME is pro, and smallness
#       WINS over anet's level -- "if they're that small I'd prefer to make
#       them pro";
#     - a missing team id infers nothing.
import ast
import io
import os
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load():
    with io.open(os.path.join(_ROOT, "engine", "build_team_pool.py"),
                 encoding="utf-8") as fh:
        src = fh.read()
    ns = {}
    for node in ast.parse(src).body:
        if isinstance(node, ast.FunctionDef) and node.name == "classify":
            exec(ast.get_source_segment(src, node), ns)     # noqa: S102
        if isinstance(node, ast.Assign) and getattr(
                node.targets[0], "id", "") == "PRO_MAX_ATHLETES":
            exec(ast.get_source_segment(src, node), ns)     # noqa: S102
    return ns["classify"], ns["PRO_MAX_ATHLETES"], src


class ThePrecedence(unittest.TestCase):

    def setUp(self):
        self.classify, self.pro_max, self.src = _load()

    def test_the_owners_number_is_fifteen(self):
        self.assertEqual(self.pro_max, 15)

    # ⚠ SMALLNESS BEATS anet's LEVEL, on the owner's explicit instruction. A
    #   college with fourteen athletes in its whole history is not a college.
    def test_a_tiny_team_is_pro_whatever_its_level_says(self):
        for level in ("college", "hs", "ms", "elem", "club", None):
            kind, _why = self.classify(14, level, 0)
            self.assertEqual(kind, "pro", level)

    # ! THE BOUNDARY IS "FEWER THAN", so fifteen itself is not caught.
    def test_exactly_fifteen_is_not_small(self):
        self.assertEqual(self.classify(15, "hs", 0)[0], "hs")
        self.assertEqual(self.classify(14, "hs", 0)[0], "pro")

    def test_one_professional_makes_the_whole_club_pro(self):
        self.assertEqual(self.classify(200, "club", 1)[0], "pro")
        self.assertEqual(self.classify(200, "club", 0)[0], "club")

    def test_anet_level_decides_everything_else(self):
        for level in ("college", "hs", "ms", "elem"):
            self.assertEqual(self.classify(200, level, 0)[0], level)

    # ! NO LEVEL IS NOT PRO. Absence of evidence is not evidence -- the owner
    #   agreed: a college that missed the link bars is still a college.
    def test_no_level_is_unknown_not_pro(self):
        self.assertEqual(self.classify(200, None, 0)[0], "unknown")

    def test_every_verdict_carries_a_reason(self):
        for args in ((14, None, 0), (200, "club", 1), (200, "club", 0),
                     (200, "hs", 0), (200, None, 0)):
            self.assertTrue(self.classify(*args)[1].strip(), args)


class WhatItReusesRatherThanRebuilds(unittest.TestCase):
    """The pool answer was already spread across three functions; this states
    it once per team without reimplementing any of them."""

    def setUp(self):
        self.classify, self.pro_max, self.src = _load()

    def test_it_defers_to_the_repos_own_level_and_pro_loaders(self):
        self.assertIn("from speed_ratings_db import loadTeamLevels", self.src)
        self.assertIn("from speed_ratings_db import loadClubPros", self.src)
        self.assertIn("min_pros=1", self.src)

    # ⚠ THE ATHLETE-POOL QUESTION STAYS WHERE IT IS. loadClubMajority holds
    #   the owner's 2026-09-14 rule that a collegian racing the Euros is not a
    #   professional; a team-level flag must not override it.
    def test_it_does_not_touch_the_athlete_majority_rule(self):
        # ! THE CODE, NOT THE DOCSTRING. The header names loadClubMajority on
        #   purpose, to say why it is left alone -- asserting over the whole
        #   file failed on the very sentence that documents the decision.
        code = self.src.split('"""', 2)[2]
        self.assertNotIn("loadClubMajority", code)
        self.assertIn("loadClubMajority", self.src.split('"""')[1])

    # ★ ALL TIME, NOT PER SEASON -- the owner said so twice.
    def test_the_size_count_has_no_season_in_it(self):
        i = self.src.index("def teamSizes(")
        body = self.src[i:self.src.index("\ndef ", i + 10)]
        for token in ("season", "substr(date", "yr"):
            self.assertNotIn(token, body)
        self.assertIn("count(DISTINCT person_id)", body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
