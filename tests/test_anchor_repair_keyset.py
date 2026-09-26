# Project: xc-predictor / tests
# File:    test_anchor_repair_keyset.py
# Purpose: anchor_repair walks the corpus once, not once per batch.
#
# ⚠⚠ WHAT IT COST (measured on the 2026-09-21 run). 05b_anchor_repair_xc and
#    _tf took 7,681 s -- 2h08m, the third-largest step in a 12.6h pipeline --
#    for work run_pipeline's own placement note calls "cheap ... one scan,
#    idempotent, no writes when nothing is wrong".
#
#    It paginated with
#
#        ORDER BY r.result_id LIMIT %(scan)s OFFSET %(off)s
#
#    advancing off by the batch size. Postgres cannot SKIP an offset: it
#    produces and discards every row before it. So batch i pays for i*batch
#    rows and the walk costs n^2 / (2*batch) visits instead of n. On
#    results_tf at ~61M rows with the default 200,000 batch that is about
#    9.3 BILLION row visits for a 61M-row table, roughly 150x one pass.
#
#    Shown directly on Postgres 16 (400,000 rows, 20,000 batch) --
#    EXPLAIN ANALYZE of the LAST batch of each:
#
#        OFFSET   Merge Left Join ... (actual rows=400000)   for 20,000 back
#        keyset   Merge Left Join ... (actual rows=20000)    for 20,000 back
#
#    Same rows, same order, verified by walking both and comparing the full
#    result_id lists. The wall-clock ratio there was only 2.3x because the
#    quadratic term needs scale to show; the plan is the evidence, not the
#    clock.
#
# ! result_id IS THE PRIMARY KEY of results and results_tf, so
#   `result_id > last ORDER BY result_id LIMIT n` is an index range scan
#   starting where the last batch stopped.
#
# ★ 2026-09-26: THE BATCHES ARE GONE ALTOGETHER. The walk is two set-based
#   passes now (engine/anchor_repair.py, "A PYTHON LOOP OVER EVERY ROW"):
#   one GROUP BY over the table, then one streamed read of only the rows
#   outside the band. There is no keyset left to test, so this file now
#   holds the line that mattered: no pass pages the table with OFFSET, and
#   each reads it once. tests/test_anchor_repair_setbased.py proves the new
#   walk writes exactly what the keyset one did.
#
#   python -m unittest tests.test_anchor_repair_keyset
import ast
import io
import os
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _src():
    with io.open(os.path.join(_ROOT, "engine", "anchor_repair.py"),
                 encoding="utf-8") as fh:
        return fh.read()


def _consts(src):
    ns = {}
    for node in ast.parse(src).body:
        if (isinstance(node, ast.Assign)
                and getattr(node.targets[0], "id", "") in
                ("_FROM", "_GROUPS", "_CANDIDATES", "_UPDATE")):
            exec(ast.get_source_segment(src, node), ns)  # noqa: S102
    return ns


class TheWalkReadsTheTableOnce(unittest.TestCase):

    def setUp(self):
        self.src = _src()
        self.sql = _consts(self.src)
        for name in ("_FROM", "_GROUPS", "_CANDIDATES"):
            self.assertIn(name, self.sql, f"{name} is gone")

    def test_there_is_no_offset(self):
        """⚠ THE REGRESSION. OFFSET makes a paged walk quadratic."""
        for name in ("_FROM", "_GROUPS", "_CANDIDATES"):
            self.assertNotIn("OFFSET", self.sql[name].upper(), name)

    def test_there_is_no_batch_loop_over_the_table(self):
        """No `result_id > after` seek and no LIMIT: each pass is one
        statement over the whole table."""
        for name in ("_FROM", "_GROUPS", "_CANDIDATES"):
            self.assertNotIn("%(after)s", self.sql[name], name)
            self.assertNotIn("LIMIT", self.sql[name].upper(), name)

    def test_the_candidates_are_streamed_in_result_id_order(self):
        """! ORDER MATTERS: the _FACTOR slots fill in result_id order, and
        the candidate loop replays that order."""
        self.assertIn("ORDER  BY r.result_id", self.sql["_CANDIDATES"])
        self.assertIn('conn.cursor(name="anchor_repair_candidates")',
                      self.src)
        self.assertIn("cur.itersize", self.src)

    def test_the_person_filter_survives(self):
        """! --person IS A DEBUGGING PATH and must still compose with both
        passes."""
        self.assertIn("{person}", self.sql["_FROM"])
        self.assertIn("AND r.person_id = %(person)s", self.src)


class TheWriteIsStillBatched(unittest.TestCase):
    """! UNCHANGED, AND ASSERTED SO. The file's own note says a per-row
    UPDATE over this corpus is hours and the VALUES join is seconds."""

    def test_one_statement_per_batch(self):
        src = _src()
        self.assertIn("FROM  (VALUES %s) AS v(result_id, nt)", src)
        self.assertIn("execute_values", src)
        self.assertIn("if len(writes) >= batch:", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
