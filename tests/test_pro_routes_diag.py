# Project: xc-predictor / tests
# File:    test_pro_routes_diag.py
# Purpose: the pro-pooling routes can be attributed without a 12-hour solve.
#
# ⚠⚠⚠ WHY IT EXISTS (2026-09-22). resolvePool has FIVE routes to is_pro:
#     no_team, team_pro, team_has_pros, isProTeam, isProPerson.
#
#     On 2026-09-20 the owner reported schools pooled pro. I fixed
#     build_team_pool.classify -- the `team_pro` route -- and said it was
#     fixed. Two days later the same report came back, because
#     `team_has_pros` is a SECOND implementation of the same rule that
#     excluded only college.
#
#     The first fix was REAL and the symptom did not move. That is the
#     worst feedback there is: it reads as a wrong diagnosis when it was an
#     incomplete one, and it cost two solves to find out.
#
#     Nothing in the tree could answer "which rule claimed these
#     athletes", so the only instrument was the pipeline. This is the
#     instrument that should have existed first.
#
# ! IT READS THE ENGINE'S OWN LOADERS -- loadTeamLevels, loadClubPros,
#   team_pool -- rather than reimplementing their rules. A copy here would
#   be a THIRD implementation of the thing whose second implementation
#   caused the bug.
#
#   python -m unittest tests.test_pro_routes_diag
import ast
import io
import os
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _src():
    with io.open(os.path.join(_ROOT, "engine", "diag_pro_routes.py"),
                 encoding="utf-8") as fh:
        return fh.read()


class ItReadsRatherThanReimplements(unittest.TestCase):

    def setUp(self):
        self.src = _src()

    def test_it_uses_the_engines_loaders(self):
        for name in ("loadTeamLevels", "loadClubPros"):
            self.assertIn(name, self.src, name)

    def test_it_does_not_recompute_the_fifteen_athlete_rule(self):
        """⚠ A THIRD IMPLEMENTATION IS THE BUG, NOT THE FIX. The verdict is
        read from team_pool.kind, never re-derived from n_athletes."""
        self.assertIn("kind", self.src)
        self.assertNotIn("PRO_MAX", self.src)
        self.assertNotIn("< 15", self.src)

    def test_it_never_writes(self):
        low = self.src.lower()
        for word in ("insert into", "update ", "delete from", "create table",
                     "truncate"):
            self.assertNotIn(word, low, word)
        self.assertIn("conn.rollback()", self.src)


class ItSeparatesTheRoutes(unittest.TestCase):

    def setUp(self):
        self.src = _src()

    def test_the_school_bucket_is_split_from_college_and_club(self):
        """★ THE THREE BUCKETS ARE THE WHOLE POINT. college was always
        exempt, clubs should still sweep, and K-12 schools are the ones
        that went missing -- one number each or the report says nothing."""
        for name in ("school_pros", "college_pros", "club_pros"):
            self.assertIn(name, self.src, name)

    def test_it_checks_team_pool_for_schools_too(self):
        """! BOTH DOORS, EVERY RUN. If classify regresses, route 2 shows a
        K-12 school and the report says so rather than printing a total."""
        self.assertIn("classify is wrong again", self.src)
        self.assertIn("no K-12 school is called pro by team_pool", self.src)

    def test_it_names_the_teams(self):
        """⚠ BARE IDS ARE UNACTIONABLE, which is what the owner said about
        build_team_pool's own list: forty rows of numbers and `--`."""
        self.assertIn("def teamNames", self.src)
        self.assertIn("team_identity", self.src)
        self.assertIn("anet_team", self.src)


class ItStatesWhatItCannotSee(unittest.TestCase):
    """! AN UPPER BOUND SOLD AS AN EXACT COUNT IS A NEW WAY TO BE WRONG.
    team_has_pros passes clubSeason's majority gate, computed at pack time,
    so the population is knowable here and the claim is not."""

    def test_the_majority_gate_caveat_is_printed(self):
        src = _src()
        self.assertIn("majority gate", src)
        self.assertIn("The exact number is lower", src)

    def test_it_says_a_solve_is_needed_to_see_the_effect(self):
        """! rating_pool IS STAMPED AT PACK TIME. Without this line a reader
        fixes the rule, reloads the page and thinks nothing happened --
        which is most of what went wrong this week."""
        self.assertIn("IT TAKES A SOLVE TO SHOW", _src())


class TheTeamPoolReportNamesThings(unittest.TestCase):

    def test_the_small_team_list_shows_schools(self):
        with io.open(os.path.join(_ROOT, "engine", "build_team_pool.py"),
                     encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn("def teamNames", src)
        i = src.index("called pro by the <")
        self.assertIn("school", src[i:i + 700])


if __name__ == "__main__":
    unittest.main(verbosity=2)
