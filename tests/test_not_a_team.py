# Every spelling of "unattached" the corpus uses is not a team.
#
# The page it was getting is a roster of every unattached runner in the
# country, and a units line that hands the non-team a league.
import os
import re
import ast
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _panelsNamespace():
    """isTeamName and its tables, without importing panels (psycopg2)."""
    src = open(os.path.join(ROOT, "racecast", "panels.py")).read()
    tree = ast.parse(src)
    ns = {"re": re}
    names = {"_NOT_A_TEAM_EXACT", "_NOT_A_TEAM_FRAGMENTS",
             "_NOT_A_TEAM_PREFIXES", "_WORD_RE", "_NON_SCHOOL",
             "_NON_SCHOOL_FRAGMENTS"}
    funcs = {"_is_non_school", "_notATeamWord", "isTeamName"}
    for node in tree.body:
        if isinstance(node, ast.Assign) and getattr(
                node.targets[0], "id", "") in names:
            exec(ast.get_source_segment(src, node), ns)
        if isinstance(node, ast.FunctionDef) and node.name in funcs:
            exec(ast.get_source_segment(src, node), ns)
    return ns


class ItRefusesEverySpellingOfUnattached(unittest.TestCase):
    def setUp(self):
        self.isTeamName = _panelsNamespace()["isTeamName"]

    def test_the_spellings_that_reached_a_page(self):
        for name in ("Unattached", "unattached", "UNATTACHED",
                     "Unattatched",          # the owner's report, 2026-09-17
                     "Un-attached", "Un Attached", "UNATT", "Unatt",
                     "Unat.", "Unattached-CA", "Unattached (OR)",
                     "Unattached Northwest"):
            with self.subTest(name=name):
                self.assertFalse(self.isTeamName(name))

    def test_the_other_non_teams_still_go(self):
        for name in ("Individual", "individuals", "Independent",
                     "No Team", "n/a", "None"):
            with self.subTest(name=name):
                self.assertFalse(self.isTeamName(name))

    # ⚠ THE HALF THAT MATTERS MORE. A prefix rule that fires inside a real
    #   name deletes a school's page, which is worse than the bug.
    def test_real_schools_starting_with_un_are_untouched(self):
        for name in ("Union High", "Unity", "Universal Academy",
                     "University High", "Uniontown", "Unionville",
                     "Dunatt Academy", "Hunattville High"):
            with self.subTest(name=name):
                self.assertTrue(self.isTeamName(name))

    def test_ordinary_schools(self):
        for name in ("Oregon", "Penn State", "La Jolla (CA)",
                     "Chisago Lakes/Rush City", "Williams"):
            with self.subTest(name=name):
                self.assertTrue(self.isTeamName(name))


if __name__ == "__main__":
    unittest.main()
