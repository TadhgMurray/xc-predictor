# Project: xc-predictor / tests
# File:    test_school_header_budget.py
# Purpose: schoolHeader's raw-results fallback is bounded, and bounding it
#          does not turn a slow page into a missing one.
#
# ⚠⚠ THE FALLBACK IS A SEQ SCAN OF BOTH FEEDS. `results.school` has no index
#    -- scripts/database.py builds three, on athlete_id, meet_id and
#    normalized_time -- so schoolHeader's third source walks ~61M rows twice.
#
#    It was written for the pipeline rebuild window, where athlete_season is
#    briefly empty and every school hits it for a few minutes. But a school
#    whose rows never REACH ranking_results falls through to it on EVERY page
#    view, forever: a pool the boards refuse (pro_*), grade_trust 'low', out
#    of US scope. Until 2026-09-21 the <15-athlete rule put whole high schools
#    on that path -- the owner's "all the ppl that are in a school are not
#    actually being counted/shown for that school" -- so those pages were two
#    table scans each, on a site that had already been taken down once this
#    week by one unindexed query.
#
# ★ AND THE BOUND MUST NOT 404 A REAL SCHOOL. app.py's school route aborts
#   404 on `header is None`, so a timeout that returned None would replace a
#   slow page with a missing one -- a worse bug than the one being fixed.
#   Hence the two stages: a LIMIT 1 existence probe decides whether the page
#   exists, and only the count(DISTINCT) may come back unknown.
#
#   python -m unittest tests.test_school_header_budget
import ast
import io
import os
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(rel):
    with io.open(os.path.join(_ROOT, *rel.split("/")), encoding="utf-8") as fh:
        return fh.read()


def _func(src, name):
    for node in ast.parse(src).body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(src, node)
    raise AssertionError(f"{name} is gone")


def _const(src, name):
    for node in ast.parse(src).body:
        if (isinstance(node, ast.Assign)
                and getattr(node.targets[0], "id", "") == name):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} is gone")


class TheRawFallbackIsBounded(unittest.TestCase):

    def setUp(self):
        self.src = _read("racecast/school.py")
        self.body = _func(self.src, "schoolHeader")

    def test_there_is_a_budget_and_it_is_short(self):
        ms = _const(self.src, "RAW_HEADER_TIMEOUT_MS")
        self.assertIsInstance(ms, int)
        # a page budget, not a batch job's
        self.assertGreater(ms, 0)
        self.assertLessEqual(ms, 10_000)

    def test_the_budget_is_applied(self):
        self.assertIn("statement_timeout", self.body)
        self.assertIn("RAW_HEADER_TIMEOUT_MS", self.body)

    def test_it_uses_a_savepoint_not_a_bare_rollback(self):
        """! THIS RUNS MID-REQUEST. `conn.rollback()` would abort reads the
        route has already done; the savepoint scopes the failure to the one
        statement that failed."""
        self.assertIn("SAVEPOINT raw_header", self.body)
        self.assertIn("ROLLBACK TO SAVEPOINT raw_header", self.body)
        self.assertNotIn("conn.rollback", self.body)

    def test_the_budget_does_not_leak_past_the_header(self):
        """⚠⚠ `SET LOCAL` IS TRANSACTION-SCOPED, NOT SAVEPOINT-SCOPED. On the
        success path the 2s budget survives the RELEASE and then applies to
        every later query in the same request -- the roster, the meet list,
        the boards. Measured on a real server: `SHOW statement_timeout` read
        '2s' after schoolHeader returned. A header's budget is not the
        page's. (The rollback path needs no reset: the savepoint undoes the
        SET along with the statement.)"""
        self.assertIn("SET LOCAL statement_timeout = DEFAULT", self.body)
        # and the reset must happen BEFORE the release, inside the savepoint
        self.assertLess(self.body.index("statement_timeout = DEFAULT"),
                        self.body.index("RELEASE SAVEPOINT raw_header"))

    def test_the_savepoint_is_released_on_the_happy_path(self):
        """! AN UNRELEASED SAVEPOINT PER PAGE VIEW is a leak inside the
        transaction, and there are two of these per call."""
        self.assertIn("RELEASE SAVEPOINT raw_header", self.body)


class ABoundedHeaderStillRendersThePage(unittest.TestCase):
    """★ THE 404 TRAP. app.py: `if header is None: abort(404)`."""

    def setUp(self):
        self.src = _read("racecast/school.py")
        self.body = _func(self.src, "schoolHeader")

    def test_the_existence_probe_is_separate_from_the_count(self):
        self.assertIn("LIMIT 1", self.body)
        # the probe comes first: existence decides the 404, the count does not
        self.assertLess(self.body.index("LIMIT 1"),
                        self.body.index("count(DISTINCT person_id)"))

    def test_the_probes_union_branches_are_parenthesised(self):
        """⚠ `LIMIT` INSIDE A BARE UNION BRANCH IS A SYNTAX ERROR in Postgres.
        The first version of this shipped without the parentheses: the probe
        raised, the handler swallowed it, and every school on this path
        404'd. Found by running it against a real server, not by reading it."""
        # ! FROM the probe, not TO the first count -- `count(DISTINCT
        #   person_id)` appears in schoolHeader's FIRST query too, so slicing
        #   to it gave an empty string and the assertion passed on nothing.
        probe = self.body[self.body.index("SELECT 1 AS present"):]
        probe = probe[:probe.index("count(DISTINCT person_id)")]
        self.assertIn("(SELECT 1 FROM results    WHERE", probe)
        self.assertIn("(SELECT 1 FROM results_tf WHERE", probe)

    def test_a_timed_out_count_returns_a_header_not_none(self):
        """! THE LAST RETURN IS THE RENDER-ANYWAY HEADER. Its keys must be the
        ones the template reads, all None, so the page prints what it has."""
        tail = self.body[self.body.rindex("return {"):]
        for key in ("athletes", "first_year", "last_year", "state"):
            self.assertIn(f'"{key}": None', tail, key)

    def test_a_real_failure_is_not_silent(self):
        """! A TIMEOUT IS EXPECTED; ANY OTHER ERROR IS A BUG whose only
        symptom is a 404, so it names itself on the console once."""
        self.assertIn("canceling statement", self.body)
        self.assertIn("schoolHeader: raw fallback raised", self.body)


class TheTemplateSurvivesAnUnknownCount(unittest.TestCase):

    def test_every_header_fact_is_guarded(self):
        """! THE YEARS SHARE ONE GUARD, which is right: "2019 to None" is
        worse than printing neither. What matters is that no fact reaches the
        page unguarded, so each one is named in a condition."""
        html = _read("racecast/templates/school.html")
        i = html.index('<p class="meta">\n        {% if header.state %}')
        line = html[i:i + 500]
        conds = [c for c in line.split("{% if ")[1:]]
        named = " ".join(c.split("%}")[0] for c in conds)
        for fact in ("header.state", "header.athletes",
                     "header.first_year", "header.last_year"):
            self.assertIn(fact, named, fact)


if __name__ == "__main__":
    unittest.main(verbosity=2)
