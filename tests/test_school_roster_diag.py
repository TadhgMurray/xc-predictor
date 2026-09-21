# Project: xc-predictor / tests
# File:    test_school_roster_diag.py
# Purpose: the school-roster diagnostic reads, is cheap by default, and asks
#          the PAGE what the page does instead of guessing.
#
# ★★ THE REPORT (owner, 2026-09-21): "all the ppl that are in a school are not
#    actually being counted/shown for that school. I see that school on the
#    athlete page but they are not on the school page."
#
#    racecast/diag_school_roster.py answers it. These are the properties that
#    make its answer worth trusting, and two of them are scars:
#
#    1. IT NEVER WRITES. It runs on the live server, next to a site that has
#       already been taken down once this week.
#    2. IT DOES NOT SEQ-SCAN results BY DEFAULT. `results.school` has no index
#       (scripts/database.py builds three: athlete_id, meet_id,
#       normalized_time). An unindexed query in this tree blocked every site
#       connection for ten hours on 2026-09-20 by queuing behind an ALTER.
#       The raw count lives behind --deep and carries a statement_timeout.
#    3. IT IMPORTS THE PAGE'S OWN FILTERS. Section D measures what
#       /school/<name> would show by calling schoolRoster, stateChips,
#       levelChips and currentSeason. A reimplementation of the level
#       predicate here would be a second definition of the page and would
#       disagree with it -- which is the failure pool_resolve.py's header
#       exists to record ("There were three implementations ... they
#       disagreed on about a million athlete-seasons").
#
#   python -m unittest tests.test_school_roster_diag
import ast
import io
import os
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PATH = os.path.join(_ROOT, "racecast", "diag_school_roster.py")


def _src():
    with io.open(_PATH, encoding="utf-8") as fh:
        return fh.read()


def _funcSource(src, name):
    for node in ast.parse(src).body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(src, node)
    raise AssertionError(f"{name} is gone from diag_school_roster.py")


def _sqlLiterals(src):
    """Every string constant in the file, which is where its SQL lives."""
    return [n.value for n in ast.walk(ast.parse(src))
            if isinstance(n, ast.Constant) and isinstance(n.value, str)]


class ItOnlyReads(unittest.TestCase):

    def test_no_statement_can_write(self):
        banned = ("insert into", "update ", "delete from", "truncate",
                  "create table", "drop table", "alter table")
        for text in _sqlLiterals(_src()):
            low = text.lower()
            # the module docstring quotes the chain it walks, so only look at
            # strings that are actually SQL
            if "select" not in low:
                continue
            for word in banned:
                self.assertNotIn(word, low, f"{word!r} in a SELECT string")

    def test_it_rolls_back_rather_than_commits(self):
        src = _src()
        self.assertIn("conn.rollback()", src)
        self.assertNotIn("conn.commit()", src)


class ItIsCheapUnlessAsked(unittest.TestCase):
    """⚠ results.school HAS NO INDEX. The default run must not touch it."""

    def test_the_default_sections_never_name_the_raw_tables(self):
        src = _src()
        for fn in ("poolVerdicts", "seasonRows", "poolsSeen", "findNames"):
            body = _funcSource(src, fn)
            self.assertNotIn("FROM   results", body, fn)
            self.assertNotIn("FROM results", body, fn)

    def test_the_raw_scan_is_the_only_place_that_does(self):
        body = _funcSource(_src(), "rawCount")
        self.assertIn("results", body)
        self.assertIn("results_tf", body)

    def test_the_raw_scan_sets_a_statement_timeout(self):
        """! A QUERY THAT CANNOT BE WAITED OUT IS THE ONE THAT TOOK THE SITE
        DOWN. This one gives up on its own."""
        body = _funcSource(_src(), "rawCount")
        self.assertIn("statement_timeout", body)
        self.assertIn("SET LOCAL", body)

    def test_it_is_behind_a_flag(self):
        src = _src()
        self.assertIn('"--deep"', src)
        self.assertIn("if not args.deep", src)


class ItNamesWhereThePeopleWent(unittest.TestCase):
    """★★ THE QUESTION THE DE LA SALLE RUN RAISED (2026-09-21).
    ranking_results held 1,036 athlete-seasons under the name and
    athlete_season held 43 -- and athlete_season is DERIVED from
    ranking_results, so they were filed somewhere, not lost.

    The two tables answer different questions. ranking_results carries a
    school PER ROW, so a person counts under every spelling they raced
    under. athlete_season carries ONE per (person, pool, sport, year) --
    `mode() WITHIN GROUP (ORDER BY school)`, the majority spelling -- and
    schoolRoster is an exact string equality on it. Section B can show the
    gap; only this section can say whether it is a split spelling or a
    dropped row, and they need different fixes."""

    def setUp(self):
        self.body = _funcSource(_src(), "whereTheyWent")

    def test_it_joins_the_two_tables_on_the_season_key(self):
        """! (person, sport, year) -- NOT pool. athlete_season splits a
        season by pool, so joining on pool would drop a person whose pool
        changed and call it a missing row."""
        for col in ("person_id", "sport", "year"):
            self.assertIn(f"s.{col}", self.body, col)
        self.assertNotIn("s.pool", self.body)

    def test_a_missing_row_is_a_bucket_not_a_silence(self):
        """⚠ A LEFT JOIN, AND THE NULL IS NAMED. An inner join would count
        only the people who survived the aggregate and report a clean split
        for a school the aggregate had dropped -- the opposite diagnosis."""
        self.assertIn("LEFT", self.body)
        self.assertIn("(no athlete_season row)", self.body)

    def test_it_counts_people_not_rows(self):
        """! count(DISTINCT person_id). ranking_results has one row per RACE,
        so a plain count(*) would weight a sprinter's season above a
        cross-country runner's and the buckets would not be comparable."""
        self.assertIn("count(DISTINCT rr.person_id)", self.body)


class ItAsksThePageRatherThanGuessing(unittest.TestCase):
    """★ SECTION D IMPORTS app.py's OWN HELPERS. The route forces a state and
    a level on a visitor who asked for neither (app.py:4630, app.py:4664), so
    the only honest way to say what the page shows is to run what it runs."""

    def setUp(self):
        self.body = _funcSource(_src(), "pageFilters")

    def test_it_imports_the_real_roster_and_chips(self):
        for name in ("schoolRoster", "currentSeason", "stateChips",
                     "levelChips", "levelOf"):
            self.assertIn(name, self.body, name)

    def test_it_does_not_reimplement_the_level_predicate(self):
        """⚠ THE PREDICATE IS app.py's `levelOf(pool) in (None, level)`, and
        it must be CALLED, not copied. A copy here drifts the day the page's
        rule changes and then reports a filter that no longer exists."""
        self.assertIn("levelOf(", self.body)
        self.assertNotIn('split("_")', self.body)
        self.assertNotIn('split("|")', self.body)

    def test_it_measures_the_drop_at_each_stage(self):
        """! THREE COUNTS, NOT ONE. Only the differences say which filter is
        responsible; a single final number names no cause."""
        for name in ("unfiltered", "filtered", "after_level"):
            self.assertIn(name, self.body, name)

    def test_it_turns_the_carry_forward_off(self):
        """! roster.py's carry-forward only ADDS last season's returners, so
        leaving it on would refill a roster a filter had just emptied and hide
        the very drop being measured."""
        self.assertIn("carry=False", self.body)

    def test_a_chip_dict_is_read_by_its_real_keys(self):
        """⚠ schoolClusters and levelChips both key the count `n`. Reading
        `athletes` printed '?' for every chip -- the one number that says
        whether the chip is even plausible."""
        self.assertIn("c.get('n'", self.body)
        self.assertNotIn("c.get('athletes'", self.body)


class ItUsesTheCursorThePageUses(unittest.TestCase):

    def test_real_dict_cursor(self):
        """! schoolRoster reads r["person_id"]. A tuple cursor makes section D
        raise instead of measure, and the raise would be reported as 'the page
        is fine'."""
        src = _src()
        self.assertIn("RealDictCursor", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
