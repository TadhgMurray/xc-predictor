# Project: xc-predictor / tests
# File:    test_event_pairs_window.py
# Purpose: diag_event_pairs explains an empty table instead of printing one.
#
# ⚠⚠ WHAT IT COST (2026-09-22). Asked for the collegiate 10,000 -> 5000
#    exchange rate, the script printed its header and not one row:
#
#        college_m: season-best 10000 m -> the same season's best 5000 m
#                 A best       n    B 25%  B median    B 75%
#        (nothing)
#
#    --lo/--hi are event A's TIME IN SECONDS and they default to 520-640 --
#    a 3200. A college 10k runs ~1700-2200 s, so every athlete fell outside
#    the buckets and the table came back empty. Nothing on screen said the
#    WINDOW was the problem rather than the data, so the natural reading is
#    "no college men race a 10k", which is false and would have ended the
#    investigation.
#
# ★ THE RULE: a diagnostic that can return nothing must be able to say why.
#   The same rule pool_view.hsFactor follows for a hidden toggle, and the
#   same one bracket_engine follows when a gauge finds no reference cell.
#   Here "why" is answerable with the data already in hand -- what the A
#   times actually are -- so the script reports them and prints the flags
#   that would work.
#
#   python -m unittest tests.test_event_pairs_window
import ast
import io
import os
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _src():
    with io.open(os.path.join(_ROOT, "scripts", "diag_event_pairs.py"),
                 encoding="utf-8") as fh:
        return fh.read()


class AnEmptyTableExplainsItself(unittest.TestCase):

    def setUp(self):
        self.src = _src()
        self.main = None
        for node in ast.parse(self.src).body:
            if isinstance(node, ast.FunctionDef) and node.name == "main":
                self.main = ast.get_source_segment(self.src, node)
        self.assertIsNotNone(self.main, "main() is gone")

    def test_it_returns_before_printing_a_header(self):
        """! THE HEADER IS THE LIE. A header with no rows reads as 'this
        pool does not race that event'."""
        i = self.main.index("if not rows:")
        j = self.main.index("season-best {args.a:.0f} m")
        self.assertLess(i, j)
        self.assertIn("return", self.main[i:j])

    def test_it_tells_the_two_cases_apart(self):
        """⚠ 'no data at all' AND 'wrong window' both come back empty, and
        they need different actions from the reader -- fix the flags, or
        stop looking."""
        self.assertIn("no", self.main)
        self.assertIn("season-bests exist", self.main)
        self.assertIn("check --pool and the distance", self.main)

    def test_it_reports_the_range_the_data_actually_has(self):
        """! THE ANSWER IS ALREADY IN THE DATABASE. percentiles of A's
        season-bests are one cheap query and they ARE the flags to use."""
        self.assertIn("percentile_cont(0.05)", self.main)
        self.assertIn("percentile_cont(0.95)", self.main)

    def test_it_prints_a_command_that_would_work(self):
        self.assertIn("try:  --lo", self.main)
        self.assertIn("--step", self.main)

    def test_the_probe_reuses_the_same_bind_parameters(self):
        """! ONE PARAM DICT. A second copy of a_lo/a_hi could drift from the
        query whose emptiness it is explaining, and then explain the wrong
        thing."""
        i = self.main.index("if not rows:")
        probe = self.main[i:i + 1400]
        self.assertIn("%(a_lo)s", probe)
        self.assertIn("%(pool)s", probe)
        self.assertIn('", p)', probe)


class TheUnitsAreNamed(unittest.TestCase):

    def test_the_time_flags_say_seconds(self):
        """⚠ (520, 640) IS A 3200's TIME. Nothing in the old help said the
        window was in seconds, let alone which event it was sized for."""
        src = _src()
        i = src.index('"--lo"')
        block = src[i:i + 900]
        self.assertIn("SECONDS", block)
        self.assertIn("3200", block)

    def test_it_does_not_write(self):
        src = _src()
        for word in ("insert into", "update ", "delete from", "create table"):
            self.assertNotIn(word, src.lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)
