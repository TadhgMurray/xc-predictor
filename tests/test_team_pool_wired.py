# Project: xc-predictor / tests
# File:    test_team_pool_wired.py
# Purpose: team_pool's verdict actually reaches the solve.
#
# ⚠ WHAT WAS ALREADY WIRED, because the claim "the solve ignores the team-id
#   pooling" was too broad and this file exists partly to record the correction.
#   Team-id pooling has been LIVE: speed_ratings calls teamLevelOf(team_id,
#   slug, loadAnetLevels()) per row and resolvePool consumes team_level /
#   team_has_pros / no_team, so team_id -> level, team_id == 0 -> pro, and
#   club-with-a-pro -> pro all reach the pool. What reached NOTHING was
#   team_pool's own verdict -- and with it the rule the owner was emphatic
#   about: "no not 15 per year 15 over all time".
#
#   python -m unittest tests.test_team_pool_wired
import inspect
import os
import sys
import unittest

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "engine"), os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pool_resolve as pr                                       # noqa: E402


class TheVerdictReachesTheDecision(unittest.TestCase):

    def test_resolvePool_takes_team_pro(self):
        self.assertIn("team_pro", inspect.signature(pr.resolvePool).parameters)
        self.assertIs(
            inspect.signature(pr.resolvePool).parameters["team_pro"].default,
            False, "it must default to off, so every existing caller is "
                   "unchanged")

    def test_a_pro_team_makes_the_row_professional(self):
        # A perfectly ordinary high-school row: grade 10, a school name.
        base = dict(grade="10", gender="M", source="anet", school="Some High",
                    sport="XC", merge=True)
        self.assertEqual(pr.resolvePool(**base), "hs_m")
        self.assertEqual(pr.resolvePool(team_pro=True, **base), "pro_m")

    def test_it_is_ungated_like_no_team(self):
        # ! The club rules run through a season-majority gate because one
        #   national-team race should not repool a school season. A team with
        #   fewer than fifteen athletes in its whole history is not a school
        #   an athlete had a season at, so there is no majority to take --
        #   which is why this sits beside no_team rather than team_level.
        base = dict(grade="10", gender="F", source="anet", school="Some High",
                    sport="TF", merge=True)
        self.assertEqual(pr.resolvePool(no_team=True, **base), "pro_f")
        self.assertEqual(pr.resolvePool(team_pro=True, **base), "pro_f")

    def test_a_para_row_still_has_no_pool_at_all(self):
        # ! BEFORE EVERYTHING means before this too: the para exclusion is
        #   about the EVENT, not the athlete's level, so a pro team cannot
        #   promote a para row into a pool.
        for school in ("Wheelchair", "Some High"):
            got = pr.resolvePool("10", "M", "anet", school, "XC",
                                 team_pro=True, merge=True)
            if pr.isParaSchool(school):
                self.assertIsNone(got, f"{school} must stay unpooled")
            else:
                self.assertEqual(got, "pro_m")

    def test_an_unknown_sex_behaves_exactly_like_every_other_pro_path(self):
        """⚠ AND IT PRODUCES 'pro_unknown_gender', WHICH IS PRE-EXISTING AND
        WORTH KNOWING ABOUT.

        pool_resolve's own comment says "'pro_unknown_gender' is not a pool
        anything can rate against, so a row with no sex still drops -- one junk
        pool is not better than one missing row". That governs _proPoolFor, the
        FALLBACK branch used when neither grade nor school produced a pool to
        swap. It does NOT govern the swap branch, which simply replaces the
        level and keeps the sex suffix it was given.

        Measured on all four pro paths with gender=None:

            no_team=True                      -> 'pro_unknown_gender'
            is_pro=True                       -> 'pro_unknown_gender'
            team_level='club', has_pros=True  -> 'pro_unknown_gender'
            team_pro=True  (this change)      -> 'pro_unknown_gender'

        So is_pro -- the oldest path of the four -- has always done this. The
        thing this test pins is therefore CONSISTENCY: team_pro must behave
        like the paths beside it, not better and not worse. Making it drop
        alone would be a fifth behaviour for one decision. If the comment's
        intent is the real rule, the fix belongs on all four at once and
        changes every sexless pro row, which is not this change's business.
        """
        got = {}
        for label, kw in (("no_team", dict(no_team=True)),
                          ("is_pro", dict(is_pro=True)),
                          ("club_pros", dict(team_level="club",
                                             team_has_pros=True)),
                          ("team_pro", dict(team_pro=True))):
            got[label] = pr.resolvePool("10", None, "anet", "Some High", "XC",
                                        merge=True, **kw)
        self.assertEqual(got["team_pro"], got["is_pro"],
                         "team_pro must match the oldest pro path exactly")
        self.assertEqual(len(set(got.values())), 1,
                         f"all four pro paths must agree, got {got}")


class TheEngineSuppliesIt(unittest.TestCase):

    def test_poolOf_forwards_team_pro(self):
        import speed_ratings as sr
        self.assertIn("team_pro", inspect.signature(sr.poolOf).parameters)
        body = inspect.getsource(sr.poolOf)
        self.assertIn("team_pro=team_pro", body,
                      "poolOf must pass it through, not swallow it")

    def test_the_row_loop_passes_it_and_counts_it(self):
        src = open(os.path.join(_ROOT, "engine", "speed_ratings.py"),
                   encoding="utf-8").read()
        self.assertIn("no_team=no_team, team_pro=team_pro", src,
                      "the per-row poolOf call must forward it")
        self.assertIn('census["team_pool_pro"]', src,
                      "a rule that repools rows off the school boards must "
                      "have its cost in the census, like no_team_pro")

    def test_the_query_names_a_column_the_table_actually_has(self):
        """⚠ THE BUG THIS TEST DID NOT CATCH, BECAUSE IT ENCODED IT. The loader
        asked `WHERE pool = 'pro'`; build_team_pool's CREATE TABLE says `kind`.
        Every call raised UndefinedColumn, speed_ratings swallowed it as
        "team_pool unavailable", and the rule reported as wired had never
        repooled a row. The old assertion pinned the wrong spelling, so it
        passed throughout.

        So this checks the two files AGAINST EACH OTHER rather than against a
        literal I typed twice."""
        import re
        ddl = open(os.path.join(_ROOT, "engine", "build_team_pool.py"),
                   encoding="utf-8").read()
        create = ddl[ddl.index("CREATE TABLE IF NOT EXISTS team_pool"):]
        create = create[:create.index(")\n")]
        columns = set(re.findall(r"^\s*(\w+)\s+\w", create, re.M))
        self.assertIn("kind", columns, "the schema itself changed")

        db = open(os.path.join(_ROOT, "engine", "speed_ratings_db.py"),
                  encoding="utf-8").read()
        i = db.index("def loadProTeams(")
        body = db[i:i + 1800]
        used = set(re.findall(r"WHERE\s+(\w+)\s*=", body))
        self.assertTrue(used, "loadProTeams has no WHERE clause to check")
        for col in used:
            self.assertIn(col, columns,
                          f"loadProTeams filters on {col!r}, which team_pool "
                          f"does not have. Columns: {sorted(columns)}")

    def test_the_loader_degrades_to_empty_without_the_table(self):
        # ! A pack built before build_team_pool.py has run must pool exactly as
        #   it did before. Checked on the SQL, because the real call needs a db.
        src = open(os.path.join(_ROOT, "engine", "speed_ratings_db.py"),
                   encoding="utf-8").read()
        i = src.index("def loadProTeams(")
        body = src[i:i + 1500]
        self.assertIn("to_regclass('team_pool')", body)
        self.assertIn("return set(), {}", body)
        self.assertIn("kind = 'pro'", body,
                      "it must read the VERDICT, not re-derive the threshold")
        self.assertNotIn("n_athletes <", body,
                         "re-deriving the 15-athlete rule here would be the "
                         "second implementation of one decision, which is the "
                         "failure pool_resolve.py's header records")


if __name__ == "__main__":
    unittest.main()
