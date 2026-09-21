# Project: xc-predictor / tests
# File:    test_unattached_race_level.py
# Purpose: an unattached runner resolves to the highest pool in their race.
#
# ★★ THE OWNER'S RULE (2026-09-20): "if a runner is unattached they should
#    resolve to the highest pool in the race they're running in."
#
#    Until now an unattached entry was professional, unconditionally --
#    pool_resolve's `no_team`, whose comment read "professional,
#    unconditionally". That is right about the absence of a school and wrong
#    about what it implies: a runner with no vest in a high school race is a
#    high schooler. The entry says nothing about the level; the race does.
#
# ⚠ WHY IT IS A SECOND TABLE, NOT A LOOSENED race_level. race_level requires
#   UNANIMITY among a race's known teams, and season_level depends on that
#   meaning: a high schooler with one pro-meet appearance must stay 'hs'.
#   Weakening it would repool athletes who HAVE a school. race_top_level
#   answers the different question -- the ceiling -- and only rows with no
#   team ever read it.
#
#   python -m unittest tests.test_unattached_race_level
import ast
import io
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _env  # noqa: E402,F401
for _p in (_ROOT, os.path.join(_ROOT, "engine"), os.path.join(_ROOT, "scripts"),
           os.path.join(_ROOT, "racecast")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pool_resolve as pr                                        # noqa: E402


def read(rel):
    with io.open(os.path.join(_ROOT, *rel.split("/")), encoding="utf-8") as fh:
        return fh.read()


def poolOf(**kw):
    """resolvePool with the arguments an unattached anet row carries."""
    base = dict(grade=None, gender="M", source="anet", school="Unattached",
                sport="XC", no_team=True, season=2026)
    base.update(kw)
    return pr.resolvePool(**base)


class TheRaceDecides(unittest.TestCase):

    def test_a_high_school_race_makes_an_unattached_runner_a_high_schooler(self):
        """★ THE WHOLE POINT. Same row, same absent team -- the race is the
        only thing that changed, and it is the only evidence there is."""
        got = poolOf(race_top_level="hs")
        self.assertTrue(got and got.startswith("hs_"), got)

    def test_each_level_carries_through(self):
        for level in ("elem", "ms", "hs", "college"):
            got = poolOf(race_top_level=level)
            self.assertTrue(got and got.startswith(level + "_"),
                            f"{level} -> {got}")

    def test_a_professional_race_still_means_professional(self):
        """! AND A STATED CEILING OF 'pro' IS STILL 'pro' -- reached through
        the rule rather than assumed, which is the difference."""
        got = poolOf(race_top_level="pro")
        self.assertTrue(got and got.startswith("pro_"), got)

    def test_no_verdict_falls_back_to_the_old_behaviour(self):
        """! PRO IS THE FALLBACK, NOT THE DEFAULT. A race the graph could not
        decide, or a database where level_graph has not run, must pool exactly
        as it did before -- so this can never be half-applied."""
        for verdict in (None, "", "club", "nonsense"):
            got = poolOf(race_top_level=verdict)
            self.assertTrue(got and got.startswith("pro_"),
                            f"{verdict!r} -> {got}")

    def test_the_ceiling_beats_a_stale_grade(self):
        """★ THE Pieter Heesters CASE, from season_level's own header: school
        'Unattached', grade '12' four years stale, rated against a HIGH SCHOOL
        pool. A college race ceiling must win over that grade."""
        got = poolOf(grade="12", race_top_level="college")
        self.assertTrue(got and got.startswith("college_"), got)

    def test_an_attached_row_is_untouched_by_the_fact(self):
        """! ONLY ROWS WITH NO TEAM READ IT. Passing the fact on an ordinary
        school row must change nothing -- otherwise this rule would repool
        athletes who HAVE a school, which is what race_level's unanimity
        requirement exists to prevent."""
        with_it = pr.resolvePool(grade="10", gender="M", source="anet",
                                 school="Herriman", sport="XC", season=2026,
                                 race_top_level="pro")
        without = pr.resolvePool(grade="10", gender="M", source="anet",
                                 school="Herriman", sport="XC", season=2026)
        self.assertEqual(with_it, without)
        self.assertTrue(without and without.startswith("hs_"), without)


class TheCeilingIsBuiltFromTheSameEvidence(unittest.TestCase):

    def setUp(self):
        self.src = read("engine/level_graph.py")

    def test_race_level_keeps_its_unanimity_rule(self):
        """⚠ THE EXISTING ARTIFACT IS NOT WEAKENED. season_level reads
        race_level and relies on 'only a season that is UNANIMOUSLY something
        else moves'."""
        i = self.src.index("def raceLevels(")
        body = self.src[i:self.src.index("\ndef ", i + 10)]
        self.assertIn("count(DISTINCT l.level) = 1", body)

    def test_the_ceiling_uses_a_level_order_not_alphabetical_min(self):
        """⚠ `min(level)` IS ALPHABETICAL -- 'college' < 'hs' < 'ms' < 'pro'.
        raceLevels gets away with it only because its HAVING forces one
        distinct level; a ceiling over a MIXED race must rank properly."""
        self.assertIn("_LEVEL_RANK_SQL", self.src)
        i = self.src.index("def raceTopLevels(")
        body = self.src[i:self.src.index("\n# ---", i)]
        self.assertIn("_LEVEL_RANK_SQL", body)
        self.assertNotIn("min(l.level)", body)

    def test_the_sql_order_matches_the_python_one(self):
        """★ ONE LEVEL ORDER, TWO LANGUAGES. The SQL CASE and
        pool_resolve._LEVEL_RANK must agree, or the ceiling written by the
        builder is not the ceiling the resolver ranks."""
        i = self.src.index("_LEVEL_RANK_SQL")
        block = self.src[i:i + 600]
        for level, rank in pr._LEVEL_RANK.items():
            self.assertIn(f"'{level}'", block, level)
            self.assertIn(f"THEN {rank}", block, f"{level}={rank}")

    def test_it_applies_the_same_known_teams_bar(self):
        """! A RACE WHOSE TEAMS ARE MOSTLY UNKNOWN HAS NO CEILING WORTH
        TRUSTING, and a row with no verdict falls back rather than guesses."""
        i = self.src.index("def raceTopLevels(")
        body = self.src[i:self.src.index("\n# ---", i)]
        # ! THE HAVING IS THE BAR. The rewrite moved it from a filter on a
        #   window column to a plain HAVING on the grouped counts; the
        #   assertion has to follow the code, not the old spelling.
        self.assertIn("count(l.school)::float / count(*) >=", body)

    def test_the_builder_runs_in_the_chain(self):
        """⚠ IT WAS IN NO CHAIN AT ALL, so race_top_level would never exist and
        the rule would be inert -- silently."""
        chain = read("scripts/overnight_fit_pool_solve.sh")
        self.assertIn("engine/level_graph.py --write", chain)
        self.assertIn("TIMEOUT_level_graph", chain)

    def test_level_graph_declares_its_flags(self):
        """⚠ IT HAND-PARSED sys.argv, which made it look like launcher.py to
        test_chain_invocations -- whose rule (a script with no argparse must be
        passed none) is right. A script that takes flags declares them."""
        self.assertIn("argparse.ArgumentParser", self.src)
        for flag in ("--write", "--find", "--level", "--refresh"):
            self.assertIn(f'"{flag}"', self.src)


class TheLoaderIsAffordableAndFailsSafe(unittest.TestCase):

    def setUp(self):
        self.src = read("engine/speed_ratings_db.py")
        i = self.src.index("def loadUnattachedRaceLevel(")
        self.body = self.src[i:self.src.index("\ndef ", i + 10)]

    def test_absent_table_is_not_an_error(self):
        """! THE SAME CONTRACT loadProTeams HAS: a database built before
        level_graph ran pools exactly as it did before."""
        self.assertIn("to_regclass('race_top_level')", self.body)
        self.assertIn("return out", self.body)

    def test_it_asks_only_about_unattached_rows(self):
        """★ WHAT MAKES IT AFFORDABLE. Keyed by result_id over the whole corpus
        this would be a dict the size of the pack."""
        self.assertIn("junk", self.body)
        self.assertIn("team_id = 0", self.body)

    def test_it_catches_both_feeds(self):
        """! anet writes team_id = 0; tfrrs carries no team id and says it in
        the school string. Asking only the first covers anet and misses every
        tfrrs row."""
        self.assertIn("results_tf", self.body)
        self.assertIn("_NON_SCHOOLS", self.body)

    def test_the_column_probe_guards_the_team_id_test(self):
        """! results_tf has carried team_id and not carried it; a hard
        reference would make this loader the thing that breaks on an older
        schema."""
        self.assertIn("information_schema.columns", self.body)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TheSqlParsesBeforeTheWorkStarts(unittest.TestCase):
    """⚠⚠ IT DID NOT, AND IT COST 2h24m (owner's run, 2026-09-20).

    raceTopLevels used `count(DISTINCT ...) OVER (PARTITION BY ...)`, which
    Postgres does not implement. The statement is the LAST thing level_graph
    runs, so it failed after two and a half hours of graph building -- inside
    the same transaction, so school_level_graph and race_level rolled back
    with it and the entire run was lost.

    Verified against a live Postgres: the broken statement is caught by the
    preflight in 9 milliseconds.
    """

    def setUp(self):
        self.src = read("engine/level_graph.py")

    def test_no_distinct_inside_a_window_function(self):
        self.assertNotIn("count(DISTINCT l.level) OVER", self.src)

    def test_the_ceiling_is_a_plain_aggregate(self):
        """★ max() OVER THE RANK is one grouped scan with no window to sort,
        and the rank maps back to a name at the end."""
        i = self.src.index("INSERT INTO race_top_level")
        body = self.src[i:i + 1200]
        self.assertIn("max(", body)
        self.assertIn("top_rank", body)
        self.assertIn("GROUP  BY rt.race", body)

    def test_there_is_a_preflight_and_it_runs_first(self):
        """! A STATEMENT THAT CANNOT PARSE CANNOT PARSE ON AN EMPTY TABLE
        EITHER, so this costs milliseconds and catches every syntax error,
        unknown column and unimplemented feature before the first row."""
        self.assertIn("def preflight(cur):", self.src)
        self.assertLess(self.src.index("preflight(cur)\n            print"),
                        self.src.index("buildGraph(cur)"))

    def test_the_preflight_leaves_nothing_behind(self):
        i = self.src.index("def preflight(cur):")
        body = self.src[i:self.src.index("\ndef ", i + 10)]
        self.assertIn("SAVEPOINT preflight", body)
        self.assertIn("ROLLBACK TO SAVEPOINT preflight", body)
        self.assertIn("finally:", body)

    def test_the_preflight_exercises_both_race_queries(self):
        i = self.src.index("def preflight(cur):")
        body = self.src[i:self.src.index("\ndef ", i + 10)]
        self.assertIn("raceLevels(cur)", body)
        self.assertIn("raceTopLevels(cur)", body)


class TheChainChecksTheLockBeforeHoursOfWork(unittest.TestCase):
    """⚠⚠ 3h21m (owner's run, 2026-09-20). The chain ran curve (51m),
    level_graph (2h24m), team_identity and team_pool, reached `solve`, and
    run_pipeline.sh died in 0s with "another pipeline holds .pipeline.lock" --
    a condition that was already true when the chain started."""

    def setUp(self):
        self.src = read("scripts/overnight_fit_pool_solve.sh")

    def test_the_lock_is_checked_before_the_first_step(self):
        self.assertIn(".pipeline.lock", self.src)
        self.assertLess(self.src.index(".pipeline.lock"),
                        self.src.index("step_fatal curve"))

    def test_it_only_asks_and_does_not_hold(self):
        """! THE SOLVE TAKES THE LOCK ITSELF hours later, so the check must
        acquire and release in a subshell."""
        # ! THE CODE LINE, NOT THE FIRST MENTION -- the comment above it names
        #   the file several times before the guard itself appears.
        i = self.src.index('exec 9>".pipeline.lock"')
        body = self.src[i:i + 200]
        self.assertIn("flock -n 9", body)
        self.assertTrue(self.src[:i].rstrip().endswith("("),
                        "the acquire must be inside a subshell so it releases")

    def test_it_says_a_held_lock_is_a_live_process(self):
        """! flock IS A KERNEL LOCK, NOT A STALE FILE -- deleting it does not
        help and makes a genuine double-run possible."""
        # ! AND THE ADVICE MUST RUN HERE. fuser and lsof are not installed on
        #   the server, so the chain points at scripts/who_holds_the_lock.sh,
        #   which reads /proc.
        self.assertIn("who_holds_the_lock.sh", self.src)
        self.assertNotIn("fuser -v", self.src)
        self.assertIn("kernel lock", self.src.lower())

    def test_skip_solve_is_still_allowed_through(self):
        self.assertIn('"${SKIP_SOLVE:-0}" != "1"', self.src)
