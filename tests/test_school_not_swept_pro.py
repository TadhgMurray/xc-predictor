# Project: xc-predictor / tests
# File:    test_school_not_swept_pro.py
# Purpose: a team anet has NOT called a club never sweeps its athletes into
#          the pro pool, by either of the routes that can do it.
#
# ⚠⚠⚠ THE SAME BUG, TWICE, TWO DAYS APART (owner, 2026-09-22: "there are
#     way too many ppl getting put in pro due to their schools, when their
#     schools are hs in anet").
#
#     2026-09-20 fixed build_team_pool.classify: a school that produced a
#     professional stays a school. That writes team_pool, which reaches
#     resolvePool as `team_pro`.
#
#     It was not the only door. pool_resolve has a SECOND route to the same
#     verdict -- `team_has_pros`, from loadClubPros by way of clubSeason's
#     majority gate -- and it tested only `team_level != "college"`. So an
#     anet high school with one pro-flagged athlete-season still swept
#     every athlete who raced mostly for it, and the first fix looked like
#     it had not worked.
#
#     2026-09-22 found a THIRD, and it is route 2's alone. classify ran the
#     club rule ABOVE the level test, so a team anet gives NO level became
#     pro on one professional -- Japan (5,468 athletes), United States
#     (5,189), France (5,080), Germany (4,260): national-team designations,
#     carrying tens of thousands of juniors between them.
#
# ★ AND ROUTE 3 IS DELIBERATELY NOT TIGHTENED THE SAME WAY. I changed it to
#   demand `team_level == "club"` for symmetry and had to put it back. The
#   two routes are not symmetric: route 2 sweeps EVERYONE who ever raced for
#   the team, route 3 only fires after clubSeason's MAJORITY gate -- which
#   is the owner's own 2026-09-16 condition, "DO do this, only when they run
#   a majority of races at that club". diag_pro_routes' route 3 listing is
#   levelled teams throughout; no level-less team was ever shown sweeping
#   through it. The gate is what makes a level-less elite squad safe there
#   and a level-less national team unsafe in route 2.
#
#     pool_resolve's own header is about exactly this: "There were three
#     implementations ... they disagreed on about a million
#     athlete-seasons." The second implementation was one file away from
#     the note warning about it.
#
# ! WHY IT IS NOT A CORNER. pro_flag classifies a SEASON on purpose
#   ("Lutkenhaus raced Millrose as a junior"; "Sadie Engelhardt forwent a
#   high school outdoor season"), so a senior flagged pro leaves a
#   pro-flagged season on their HIGH SCHOOL's team id -- and the whole
#   roster went out the door behind them.
#
# ! AND A CLUB MUST STILL SWEEP. A sponsor's youth squad carries grades
#   9-12 and is a club; the owner priced that against the boards on
#   2026-09-16 and chose it.
#
#   python -m unittest tests.test_school_not_swept_pro
import ast
import io
import os
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _src(rel):
    with io.open(os.path.join(_ROOT, *rel.split("/")), encoding="utf-8") as fh:
        return fh.read()


def _func(src, name):
    for node in ast.parse(src).body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(src, node)
    raise AssertionError(f"{name} is gone")


def _classify():
    """classify() lifted without importing the module, which wants a database.

    ! EVERY LITERAL TOP-LEVEL CONSTANT comes with it, the way
      tests/test_team_pool.py does it -- naming them by hand is how that file
      once turned a real assertion failure into a NameError.
    """
    src = _src("engine/build_team_pool.py")
    ns = {}
    for node in ast.parse(src).body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            try:
                exec(ast.get_source_segment(src, node), ns)      # noqa: S102
            except Exception:                                    # noqa: BLE001
                pass
    exec(_func(src, "classify"), ns)                             # noqa: S102
    return ns["classify"]


class NeitherRouteSweepsASchool(unittest.TestCase):

    def setUp(self):
        self.pr = _func(_src("engine/pool_resolve.py"), "resolvePool")

    def test_the_club_pros_route_excludes_every_school_level(self):
        """⚠ THE REGRESSION. `team_level != "college"` let hs, ms and elem
        through."""
        i = self.pr.index("if team_has_pros and team_level")
        clause = self.pr[i:i + 200]
        self.assertNotIn('team_level != "college"', clause)
        for lvl in ("college", "hs", "ms", "elem"):
            self.assertIn(f'"{lvl}"', clause, lvl)

    def test_a_club_still_sweeps(self):
        """! THE CASE THE OWNER PRICED AND KEPT: a sponsor's youth squad
        carries grades 9-12 and is a club."""
        i = self.pr.index("if team_has_pros and team_level")
        clause = self.pr[i:i + 200]
        self.assertNotIn('"club"', clause)

    def test_an_unknown_level_still_sweeps(self):
        """! ABSENCE OF A LEVEL IS NOT A SCHOOL. `None not in (...)` stays
        True, which is the behaviour this had before and should keep."""
        self.assertNotIn("team_level is not None and team_level not in", self.pr)


class TheOtherRouteIsStillFixed(unittest.TestCase):
    """! THE 2026-09-20 FIX, ASSERTED HERE TOO. The two doors are what this
    file is about, so it fails if either one reopens.

    ⚠ THIS USED TO READ THE SOURCE TEXT, and on 2026-09-22 it failed on a
      CORRECT change -- the club rule moving below the level test -- because
      it pinned the literal `return "pro", f"{n_pros} professional`. A test
      that pins a string argues for the string. classify() takes no database,
      so there is no excuse for asking anything but the function.
    """

    def setUp(self):
        self.classify = _classify()

    def test_team_pool_keeps_a_school_that_produced_a_professional(self):
        """A 1,962-athlete high school with 224 pro-flagged seasons on it is
        a high school."""
        kind, why = self.classify(1962, "hs", 224)
        self.assertEqual(kind, "hs")
        self.assertIn("not a club", why)

    def test_a_club_with_a_professional_is_still_pro(self):
        """! THE OWNER'S RULE, UNTOUCHED: "a club with ANY professional in
        it is pro"."""
        self.assertEqual(self.classify(400, "club", 5)[0], "pro")

    def test_a_levelless_team_with_a_professional_is_not_pro(self):
        """⚠⚠⚠ THE THIRD DOOR (owner, 2026-09-22: "They shouldn't be pooled
        pro ... Same for their team!").

        The club rule ran ABOVE the level test, so a team anet gives NO
        level became pro on one professional. diag_pro_routes found what
        those teams are -- Japan (5,468 athletes), United States (5,189),
        France (5,080), Germany (4,260): national-team designations, and
        every junior who ever wore one was pooled professional for a whole
        season.

        Rule 4 in this module's own header already said so: "a missing anet
        team id infers NOTHING. Absence of a link is absence of evidence,
        not evidence of pro." A missing LEVEL is the same absence.
        """
        kind, why = self.classify(5468, None, 3)
        self.assertEqual(kind, "unknown")
        self.assertIn("rule 4", why)

    def test_the_small_team_rule_still_outranks_a_missing_level(self):
        """! AND THE SIZE BAR IS UNTOUCHED. A level-less team of three is
        still pro, which is the rule that was there before any of this."""
        self.assertEqual(self.classify(3, None, 0)[0], "pro")


if __name__ == "__main__":
    unittest.main(verbosity=2)
