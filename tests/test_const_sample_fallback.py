# Project: xc-predictor / tests
# File:    test_const_sample_fallback.py
# Purpose: a pool constant is never lost because the SAMPLE happened to
#          miss the rated rows.
#
# ⚠⚠⚠ THE OWNER'S pool_view RUN, 2026-09-22:
#
#         pool_view: constant for hs_m/TF unavailable (0 usable rows, need 50)
#         pool_view: constant for hs_f/TF unavailable (0 usable rows, need 50)
#         hs_m            1211.5        --
#         hs_f            1476.7        --
#
#     _CONST_SQL takes 1,500 ranking_results rows for the pool INSIDE a CTE
#     and only then joins results_tf and drops the rows with no rating. An
#     unordered LIMIT over the biggest pool in the corpus returns a
#     correlated physical slice, and high school track is mostly sprints and
#     field events -- which the engine never rates (_tfQuery excludes
#     is_field and everything under 800m). Zero of 1,500 survived, for both
#     genders, while ms, college and pro all sampled cleanly.
#
#     Reproduced on Postgres 16: the shipped query returns 0 rows for the
#     big pool and 1,500 for a small one; moving the filter above the LIMIT
#     returns 1,500 for both.
#
# ★★ AND THE DAMAGE WAS NOT LOCAL. hsFactor takes C(hs)/C(pool) inside each
#    sport and combines the RATIOS, so losing the hs TF constant costs every
#    OTHER pool its TF ratio too. That same run printed "measured from 1
#    sport ratio(s)" for all eight pools: one missing sample halved the
#    evidence under the entire factor table.
#
#   python -m pytest -q tests/test_const_sample_fallback.py
import ast
import io
import os
import sys
import unittest

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
os.environ.setdefault("XCP_DB_QUIET", "1")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_SRC = io.open(os.path.join(_ROOT, "racecast", "pool_view.py"),
               encoding="utf-8").read()


class _Cur(object):
    """A cursor that answers each query from a scripted list."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.queries = []
        self.last = []

    def execute(self, sql, params=None):
        self.queries.append(" ".join(str(sql).split()))
        if "statement_timeout" in str(sql):
            return
        self.last = self.answers.pop(0) if self.answers else []

    def fetchall(self):
        return self.last

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Conn(object):
    def __init__(self, cur):
        self._cur = cur

    def cursor(self):
        return self._cur

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _run(pool, sport, answers):
    import contextlib
    import pool_view as pv
    cur = _Cur(answers)

    @contextlib.contextmanager
    def fake():
        yield _Conn(cur)

    # ! default_difficulty OPENS ITS OWN CONNECTION, and it is called
    #   before the sample. Left real, it raises first and the test ends up
    #   asserting about the exception path instead of the retry.
    old_conn, old_save = pv.getConn, pv._saveConstFile
    old_diff = pv.default_difficulty
    pv.getConn = fake
    pv.default_difficulty = lambda sport: 0.0
    pv._saveConstFile = lambda: None
    pv._CONST_CACHE.clear()
    pv._NONE_UNTIL.clear()
    pv._FAILED.clear()
    try:
        return pv._poolConstant(pool, sport), cur
    finally:
        pv.getConn, pv._saveConstFile = old_conn, old_save
        pv.default_difficulty = old_diff
        pv._CONST_CACHE.clear()
        pv._NONE_UNTIL.clear()
        pv._FAILED.clear()


_GOOD = [(100.0 + (i % 7), 1200.0 + (i % 11)) for i in range(400)]


class TheSampleIsRetriedFromTheStampedSource(unittest.TestCase):

    def test_hs_tf_is_recovered_when_ranking_results_gives_nothing(self):
        """⚠ THE OWNER'S CASE, EXACTLY. The primary returns zero rows; the
        constant must still be built."""
        value, cur = _run("hs_m", "TF", [[], _GOOD])
        self.assertIsNotNone(value)
        self.assertEqual(len(cur.queries) - sum(
            1 for q in cur.queries if "statement_timeout" in q), 2)

    def test_the_retry_reads_rating_pool_not_ranking_results(self):
        _value, cur = _run("hs_m", "TF", [[], _GOOD])
        real = [q for q in cur.queries if "statement_timeout" not in q]
        self.assertIn("ranking_results", real[0])
        self.assertIn("rating_pool", real[1])
        self.assertNotIn("ranking_results", real[1])

    def test_a_pool_that_samples_cleanly_is_not_retried(self):
        """! THE WORKING SEVEN DO NOT MOVE. ranking_results is the narrower
        statement -- rows the athlete ran WHILE IN this pool, already
        adjudicated -- so a clean primary sample ends it."""
        _value, cur = _run("college_m", "TF", [_GOOD, _GOOD])
        real = [q for q in cur.queries if "statement_timeout" not in q]
        self.assertEqual(len(real), 1)
        self.assertIn("ranking_results", real[0])

    def test_a_thin_sample_is_retried_too(self):
        """! NOT ONLY ZERO. Anything under _CONST_MIN_ROWS is an anecdote by
        this module's own definition, and the owner's run happened to show
        the extreme."""
        import pool_view as pv
        thin = _GOOD[:pv._CONST_MIN_ROWS - 1]
        _value, cur = _run("hs_f", "XC", [thin, _GOOD])
        real = [q for q in cur.queries if "statement_timeout" not in q]
        self.assertEqual(len(real), 2)

    def test_the_retry_may_not_make_a_sample_worse(self):
        """! A retry that came back thinner than the primary must be
        discarded, not adopted -- it is a second opinion, not an override."""
        import pool_view as pv
        primary = _GOOD[:pv._CONST_MIN_ROWS + 5]
        value, _cur = _run("hs_m", "XC", [primary, []])
        self.assertIsNotNone(value)

    def test_a_pro_pool_still_goes_straight_to_the_stamped_source(self):
        """★ pro_* CANNOT be sampled from ranking_results at all --
        isRankablePool refuses every one, so that table holds zero pro rows
        by construction. It skips the primary rather than retrying past it."""
        _value, cur = _run("pro_m", "TF", [_GOOD])
        real = [q for q in cur.queries if "statement_timeout" not in q]
        self.assertEqual(len(real), 1)
        self.assertIn("rating_pool", real[0])

    def test_the_retry_puts_the_statement_budget_back(self):
        """⚠ SET LOCAL IS TRANSACTION-SCOPED AND THIS CONNECTION IS POOLED.
        racecast/school.py leaked exactly this budget onto every later query
        in a request on 2026-09-21; the fix there was this same line."""
        _value, cur = _run("hs_m", "TF", [[], _GOOD])
        budgets = [q for q in cur.queries if "statement_timeout" in q]
        self.assertEqual(len(budgets), 2)
        self.assertIn("DEFAULT", budgets[-1])


class TheQueryShapesSayWhyEachExists(unittest.TestCase):

    def _assign(self, name):
        ns = {}
        for node in ast.parse(_SRC).body:
            if (isinstance(node, ast.Assign)
                    and getattr(node.targets[0], "id", "") == name):
                exec(ast.get_source_segment(_SRC, node), ns)   # noqa: S102
        self.assertIn(name, ns, f"{name} is gone")
        return ns[name]

    def test_the_primary_still_limits_inside_the_cte(self):
        """! THE BOUND IS THE POINT OF THE PRIMARY. It reads 1,500
        ranking_results rows and stops, which is what makes it cheap on a
        RARE pool where the stamped scan would run a long way. The fallback
        is what covers its blind spot, not a replacement for it."""
        for sql in self._assign("_CONST_SQL").values():
            self.assertIn("LIMIT", sql)
            self.assertIn("ranking_results", sql)

    def test_the_fallback_needs_no_join_at_all(self):
        """★ rating_pool is stamped by the go-live on every rated row,
        boards or not -- so the fallback is one table, and the LIMIT sits
        above the filter where it belongs."""
        for sport, sql in self._assign("_STAMPED_CONST_SQL").items():
            self.assertIn("rating_pool", sql)
            self.assertNotIn("JOIN", sql.upper())
            self.assertNotIn("ranking_results", sql)
            i, j = sql.upper().index("SPEED_RATING > 0"), sql.upper().rindex("LIMIT")
            self.assertLess(i, j, f"{sport}: the LIMIT must follow the filter")

    def test_each_sport_reads_its_own_table(self):
        sqls = self._assign("_STAMPED_CONST_SQL")
        self.assertIn("results_tf", sqls["TF"])
        self.assertNotIn("results_tf", sqls["XC"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
