# Project: xc-predictor / tests
# File:    test_team_levels.py
# Purpose: what anet's undocumented `level` codes mean, decided from our own
#          rows' grades. No database.
#
#   python -m pytest -q tests/test_team_levels.py
#   python tests/test_team_levels.py
#
# ⚠⚠ THE MEASURED FAILURE (server, 2026-09-17). link_tfrrs_to_anet reported
#    "0 anet teams are colleges", and the table it printed said why -- except
#    that it did not:
#
#       code       rows     hs     ms   elem   none   teams tfrrs col.  meaning
#          2  4,353,143     0%    88%     9%     2%  15,306        0%   ms
#          4 26,960,885    96%     2%     0%     2%  23,550        0%   hs
#          8  2,015,662     0%     0%     0%     3%   2,034        2%   (unnamed)
#
#    Code 8 is the college feed: two million rows over 2,034 teams. Its four
#    printed shares add to THREE PERCENT. The other ninety-seven were in
#    `other` -- a bucket nothing counted and the table did not print.
#
# ★ BECAUSE "A COLLEGE ROW CARRIES NO GRADE" IS FALSE. A college row carries
#   a CLASS YEAR: FR / SO / JR / SR / RS. Every one fell through to `other`,
#   so no code could ever reach the `none >= share` test and none was ever
#   named 'college'.
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "engine"), os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _nameLevelCode():
    """Loaded from source: speed_ratings_db imports numpy and a database
    module this sandbox has not got, and the decision is pure."""
    import ast
    import io
    path = os.path.join(_ROOT, "engine", "speed_ratings_db.py")
    with io.open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    fn = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name == "nameLevelCode")
    ns = {}
    exec(compile(ast.Module([fn], []), path, "exec"), ns)      # noqa: S102
    return ns["nameLevelCode"]


name = _nameLevelCode()


# the server's own table, 2026-09-17, with the unprinted remainder restored
SERVER = [
    # code, rows, hs, ms, elem, none, teams, known-college share
    (2,     4353143, .00, .88, .09, .02, 15306, .00, "ms"),
    (4,    26960885, .96, .02, .00, .02, 23550, .00, "hs"),
    (8,     2015662, .00, .00, .00, .03,  2034, .02, "college"),
    (16,     438587, .11, .27, .28, .30,  7620, .00, None),
    (1024,        0, .00, .00, .00, .00,     6, .00, None),
]


def _shares(hs, ms, elem, none):
    """The tally as loadTeamLevels builds it: whatever is left over after the
    four named buckets is the class-year one."""
    return {"hs": hs, "ms": ms, "elem": elem, "none": none,
            "coll": max(0.0, 1.0 - (hs + ms + elem + none))}


class TheServersOwnTable(unittest.TestCase):

    def test_every_code_is_named_the_way_the_owner_read_it(self):
        for code, n, hs, ms, elem, none, _teams, c_share, want in SERVER:
            got = name(_shares(hs, ms, elem, none), n, c_share)
            self.assertEqual(got, want, f"code {code}")

    def test_code_8_is_the_college_feed(self):
        """The one that mattered: 2,034 teams and 2M rows, unnamed before."""
        self.assertEqual(name(_shares(.00, .00, .00, .03), 2015662, .02),
                         "college")

    def test_and_it_needs_no_second_table_to_say_so(self):
        """The old test wanted a quarter of the code's teams to carry a
        school name a DIRECTORY knows. Code 8 scored 2%."""
        self.assertEqual(name(_shares(.0, .0, .0, .03), 2015662, 0.0),
                         "college")


class TheNumericGradesWinFirst(unittest.TestCase):
    """A high school CAN spell its grades FR/SO/JR/SR. anet's does not, but
    the ordering is what guarantees a numeric code is never called college."""

    def test_a_mostly_numeric_code_is_a_high_school(self):
        self.assertEqual(name(_shares(.60, .02, .00, .02), 100000, .90), "hs")

    def test_a_middle_school_stays_a_middle_school(self):
        self.assertEqual(name(_shares(.00, .88, .09, .02), 4353143, .50), "ms")


class TheUngradedFeedStillWorks(unittest.TestCase):
    """The old indirect rule is kept for a feed that really carries nothing."""

    def test_ungraded_and_known_colleges_is_a_college(self):
        self.assertEqual(name({"none": .95}, 50000, .40), "college")

    def test_ungraded_and_unknown_schools_is_a_club(self):
        self.assertEqual(name({"none": .95}, 50000, .05), "club")


class NotEnoughEvidence(unittest.TestCase):

    def test_a_code_with_too_few_rows_is_never_named(self):
        self.assertIsNone(name(_shares(.0, .0, .0, .0), 0, .0))
        self.assertIsNone(name({"coll": 1.0}, 199, .0))

    def test_a_mixture_is_left_unnamed_rather_than_guessed(self):
        """Code 16: 11/27/28/30 across four levels. Nothing reaches half."""
        self.assertIsNone(name(_shares(.11, .27, .28, .30), 438587, .0))


class TheTableShowsItsWorking(unittest.TestCase):

    def test_every_bucket_is_printed_now(self):
        """The version that printed four of six hid the one number that
        named the code."""
        import io
        with io.open(os.path.join(_ROOT, "engine", "speed_ratings_db.py"),
                     encoding="utf-8") as fh:
            src = fh.read()
        i = src.index("def printTeamLevels(")
        body = src[i:i + 900]
        self.assertIn('cols = ("hs", "ms", "elem", "coll", "none", "other")',
                      body)

    def test_the_class_year_spellings_are_the_ones_grade_sanity_uses(self):
        import io
        with io.open(os.path.join(_ROOT, "engine", "speed_ratings_db.py"),
                     encoding="utf-8") as fh:
            src = fh.read()
        i = src.index("def loadTeamLevels(")
        body = src[i:src.index("def printTeamLevels(", i)]
        for spelling in ("'fr'", "'so'", "'jr'", "'sr'", "'rs'", "'redshirt'",
                         "'freshman'", "'sophomore'", "'junior'", "'senior'",
                         "'fr-1'", "'so-2'", "'jr-3'", "'sr-4'"):
            self.assertIn(spelling, body, spelling)


if __name__ == "__main__":
    unittest.main(verbosity=2)
