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


class TheWalkIsKeyset(unittest.TestCase):

    def setUp(self):
        self.src = _src()
        ns = {}
        for node in ast.parse(self.src).body:
            if (isinstance(node, ast.Assign)
                    and getattr(node.targets[0], "id", "") == "_SELECT"):
                exec(ast.get_source_segment(self.src, node), ns)  # noqa: S102
        self.sql = ns.get("_SELECT")
        self.assertIsNotNone(self.sql, "_SELECT is gone")

    def test_there_is_no_offset(self):
        """⚠ THE REGRESSION. OFFSET makes the walk quadratic in the corpus."""
        self.assertNotIn("OFFSET", self.sql.upper())

    def test_it_seeks_past_the_last_row_it_saw(self):
        self.assertIn("r.result_id > %(after)s", self.sql)

    def test_the_order_matches_the_seek_column(self):
        """! KEYSET IS ONLY CORRECT IF THE ORDER IS THE SEEK COLUMN. Sorted
        by anything else, `result_id > after` skips rows rather than
        resuming -- silent data loss, not a slow query."""
        self.assertIn("ORDER  BY r.result_id", self.sql)
        i = self.sql.index("ORDER  BY r.result_id")
        self.assertIn("LIMIT", self.sql[i:])

    def test_the_cursor_advances_by_the_last_id_not_a_count(self):
        """⚠ `after += len(rows)` WOULD BE OFFSET WEARING A NEW NAME, and it
        would also be wrong: result_ids are not dense."""
        body = self.src[self.src.index("after = -1"):]
        body = body[:body.index("writes = []")]
        self.assertIn('after = rows[-1]["result_id"]', body)
        self.assertNotIn("after +=", body)

    def test_it_starts_below_every_real_id(self):
        """! -1, NOT 0. A result_id of 0 would be skipped by `> 0`."""
        self.assertIn("after = -1", self.src)

    def test_the_batch_still_terminates_on_a_short_read(self):
        self.assertIn("if len(rows) < args.batch:", self.src)

    def test_the_person_filter_survives(self):
        """! --person IS A DEBUGGING PATH and must still compose with the
        seek; a keyset rewrite that dropped it would be found by hand, at
        the worst moment."""
        self.assertIn("{person}", self.sql)
        self.assertIn("AND r.person_id = %(person)s", self.src)


class TheWriteIsStillBatched(unittest.TestCase):
    """! UNCHANGED, AND ASSERTED SO. The file's own note says a per-row
    UPDATE over this corpus is hours and the VALUES join is seconds. The
    read fix must not have disturbed the write."""

    def test_one_statement_per_batch(self):
        src = _src()
        self.assertIn("FROM  (VALUES %s) AS v(result_id, nt)", src)
        self.assertIn("execute_values", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
