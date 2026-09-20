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
    # ! EVERY TOP-LEVEL CONSTANT, NOT A HAND-LISTED ONE. This used to exec
    #   PRO_MAX_ATHLETES by name, so the day classify started reading a second
    #   module constant (_SCHOOL_LEVELS) every test in ThePrecedence died with
    #   NameError instead of failing on the arithmetic it exists to pin. A
    #   literal assignment costs nothing to carry and cannot import a database.
    for node in ast.parse(src).body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            try:
                exec(ast.get_source_segment(src, node), ns)  # noqa: S102
            except Exception:                                # noqa: BLE001
                pass
    for node in ast.parse(src).body:
        if isinstance(node, ast.FunctionDef) and node.name == "classify":
            exec(ast.get_source_segment(src, node), ns)     # noqa: S102
    return ns["classify"], ns["PRO_MAX_ATHLETES"], src


# ★ THE PARSED TREE, NOT THE TEXT. Two assertions below are about what this
#   module CALLS and what its SQL says, and both used to be substring searches
#   over a slice of the file -- so a COMMENT that merely mentioned
#   loadClubMajority, or a comment sitting between two defs that used the word
#   "season", failed them. That already happened once (see the note inside
#   test_it_does_not_touch_the_athlete_majority_rule) and it happened again on
#   2026-09-20. Asking the AST asks the question the tests actually mean.
def _names(src):
    """Every identifier this module REFERENCES, comments and strings excluded."""
    out = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Name):
            out.add(node.id)
        elif isinstance(node, ast.Attribute):
            out.add(node.attr)
        elif isinstance(node, ast.alias):
            out.add((node.asname or node.name).split(".")[-1])
    return out


def _funcSource(src, name):
    """The source of ONE function, with no neighbouring comment in it."""
    for node in ast.parse(src).body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(src, node)
    raise AssertionError(f"{name} is gone from build_team_pool.py")


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

    # ⚠⚠ THE WORD "CLUB" IS THE RULE, AND THIS IS THE TEST THAT WAS MISSING.
    #    Until 2026-09-20 classify ran `if n_pros:` against EVERY level, so a
    #    high school that produced one professional was called pro. The suite
    #    was green throughout, because the only case anyone asserted was the
    #    club one directly above and the only case anyone asserted for a school
    #    passed n_pros=0 -- the question that mattered was never asked.
    #
    # ! AND IT IS NOT A CORNER. pro_flag.py classifies an athlete-SEASON on
    #   purpose ("Lutkenhaus raced Millrose as a junior"), so a senior flagged
    #   pro is a pro-flagged season sitting on their HIGH SCHOOL's team id.
    #   team_pro is ungated, so that one season repooled every athlete in the
    #   school's history and bent their ratings ~50 points.
    def test_a_school_that_produced_a_professional_is_still_a_school(self):
        for level in ("hs", "ms", "elem", "college"):
            for n_pros in (1, 3, 40):
                kind, why = self.classify(200, level, n_pros)
                self.assertEqual(kind, level, (level, n_pros))
                # the rescue says so, so report() can price it
                self.assertIn("not a club", why)

    # ! SMALLNESS STILL OUTRANKS IT. The school rescue must not resurrect a
    #   team the owner's 15-athlete rule has already condemned.
    def test_the_school_rescue_does_not_beat_the_fifteen_rule(self):
        for level in ("hs", "ms", "elem", "college"):
            self.assertEqual(self.classify(14, level, 5)[0], "pro", level)

    def test_anet_level_decides_everything_else(self):
        for level in ("college", "hs", "ms", "elem"):
            self.assertEqual(self.classify(200, level, 0)[0], level)

    # ! NO LEVEL IS NOT PRO. Absence of evidence is not evidence -- the owner
    #   agreed: a college that missed the link bars is still a college.
    def test_no_level_is_unknown_not_pro(self):
        self.assertEqual(self.classify(200, None, 0)[0], "unknown")

    def test_every_verdict_carries_a_reason(self):
        for args in ((14, None, 0), (200, "club", 1), (200, "club", 0),
                     (200, "hs", 0), (200, "hs", 1), (200, None, 0)):
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
        # ! WHAT IT CALLS, NOT WHAT IT MENTIONS. The header names
        #   loadClubMajority on purpose, to say why it is left alone, and the
        #   comment on classify now names it too -- so the question has to be
        #   asked of the parsed tree. Prose about a rule is not a call to it.
        self.assertNotIn("loadClubMajority", _names(self.src))
        self.assertIn("loadClubMajority", self.src.split('"""')[1])

    # ★ ALL TIME, NOT PER SEASON -- the owner said so twice.
    def test_the_size_count_has_no_season_in_it(self):
        # ! THE FUNCTION, NOT EVERYTHING UP TO THE NEXT ONE. The old slice ran
        #   to the next `def`, so it swallowed the comment block that documents
        #   classify -- and the word "season" in that prose failed a test about
        #   teamSizes' SQL.
        body = _funcSource(self.src, "teamSizes")
        for token in ("season", "substr(date", "yr"):
            self.assertNotIn(token, body)
        self.assertIn("count(DISTINCT person_id)", body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
