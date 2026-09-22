# Project: xc-predictor / tests
# File:    test_school_not_swept_pro.py
# Purpose: an anet SCHOOL never sweeps its athletes into the pro pool,
#          by EITHER of the two routes that can do it.
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
    file is about, so it fails if either one reopens."""

    def test_team_pool_keeps_a_school_that_produced_a_professional(self):
        src = _src("engine/build_team_pool.py")
        body = _func(src, "classify")
        i = body.index("if level in _SCHOOL_LEVELS:")
        j = body.index("if n_pros:", i)
        self.assertIn("not a club", body[j:j + 400])
        # and the pro verdict for n_pros must sit AFTER the school branch
        self.assertGreater(body.index('return "pro", f"{n_pros} professional'),
                           i)


if __name__ == "__main__":
    unittest.main(verbosity=2)
