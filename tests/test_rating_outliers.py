# Project: xc-predictor / tests
# File:    test_rating_outliers.py
# Purpose: the outlier rule's judgement, without a database.
#
#   python tests/test_rating_outliers.py
#
# ⚠⚠ THE POINT OF THE WHOLE MODULE, as arithmetic. Owner (2026-09-18): "if a
#    result is more than 5-15 sigma away (from their season's median or mean or
#    whatever) do not rate or rank." With MEAN and standard deviation that
#    range catches nothing, because one outlier inflates the deviation it is
#    measured against: a 400 among five ratings near 150 scores z = 2.24.
#    Against median and MAD the same row scores z = 124.6. Pinned below, since
#    a future "simplification" to mean/sd would silently disable the rule.
import ast
import io
import os
import statistics as st
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load():
    with io.open(os.path.join(_ROOT, "engine", "rating_outliers.py"),
                 encoding="utf-8") as fh:
        src = fh.read()
    ns = {}
    for node in ast.parse(src).body:
        if isinstance(node, ast.FunctionDef) and node.name in ("counts",
                                                               "flagged"):
            exec(ast.get_source_segment(src, node), ns)     # noqa: S102
        if isinstance(node, ast.Assign):
            t = getattr(node.targets[0], "id", "")
            if t.isupper():
                try:
                    exec(ast.get_source_segment(src, node), ns)   # noqa: S102
                except Exception:                                 # noqa: BLE001
                    pass
    return ns, src


class TheRobustSpreadIsTheWholeDesign(unittest.TestCase):

    SEASON = [150.0, 151.0, 149.5, 150.5, 152.0, 400.0]

    def test_mean_and_sd_would_hide_the_outlier(self):
        mean, sd = st.mean(self.SEASON), st.pstdev(self.SEASON)
        z = (400.0 - mean) / sd
        self.assertLess(z, 3.0, "mean/sd should hide it -- that is the problem")

    def test_median_and_mad_catch_it(self):
        ns, _src = _load()
        med = st.median(self.SEASON)
        mad = st.median([abs(x - med) for x in self.SEASON])
        sigma = max(ns["MAD_TO_SIGMA"] * mad, ns["MIN_SPREAD"])
        self.assertGreater((400.0 - med) / sigma, 15.0)

    def test_the_scaling_constant_is_the_standard_one(self):
        ns, _src = _load()
        self.assertAlmostEqual(ns["MAD_TO_SIGMA"], 1.4826, places=4)

    # ! A ZERO MAD MUST NOT DIVIDE BY ZERO. Three near-identical ratings is an
    #   ordinary season, not an infinitely precise one.
    def test_a_zero_mad_is_floored_not_infinite(self):
        ns, src = _load()
        self.assertGreater(ns["MIN_SPREAD"], 0.0)
        self.assertIn("GREATEST", src)
        self.assertIn("%(min_spread)s", src)


class TheTwoSidesAreNotTheSameQuestion(unittest.TestCase):
    """A wildly slow race is a jog and is ordinary; a wildly fast one is a data
    error. So the slow side is OFF unless asked for."""

    ROWS = [  # (result_id, person, season, rating, med, n_races, sigma, z)
        (1, 10, 2024, 400.0, 150.0, 6, 2.0, 125.0),
        (2, 10, 2024, 100.0, 150.0, 6, 2.0, -25.0),
        (3, 11, 2024, 156.0, 150.0, 6, 2.0, 3.0),
    ]

    def test_the_slow_side_is_off_by_default(self):
        ns, _src = _load()
        self.assertIsNone(ns["SLOW_SIGMA"])
        got = ns["flagged"](self.ROWS, "XC", fast=8.0, slow=None)
        self.assertEqual([r[0] for r in got], [1])

    def test_the_slow_side_can_be_asked_for(self):
        ns, _src = _load()
        got = ns["flagged"](self.ROWS, "XC", fast=8.0, slow=8.0)
        self.assertEqual(sorted(r[0] for r in got), [1, 2])
        self.assertEqual({r[9] for r in got}, {"fast", "slow"})

    def test_a_row_inside_the_bar_is_left_alone(self):
        ns, _src = _load()
        got = ns["flagged"](self.ROWS, "XC", fast=8.0, slow=8.0)
        self.assertNotIn(3, [r[0] for r in got])

    # ★ ONE SCAN, SEVERAL BARS. The owner gave a RANGE, so the report has to
    #   price every candidate without rescanning 200M rows per threshold.
    def test_counts_prices_several_bars_from_one_scan(self):
        ns, _src = _load()
        got = ns["counts"](self.ROWS, [5, 8, 10, 15], slow=True)
        self.assertEqual(got[15][0], 1)
        self.assertEqual(got[5][0], 1)
        self.assertEqual(got[5][1], 1)      # the slow one, at 5
        self.assertEqual(got[15][1], 1)


class ItChangesNothingYet(unittest.TestCase):

    def test_it_writes_a_table_and_does_not_touch_speed_rating(self):
        _ns, src = _load()
        self.assertIn("CREATE TABLE IF NOT EXISTS rating_outlier", src)
        code = src.split('"""', 2)[2]
        for danger in ("UPDATE results", "UPDATE results_tf",
                       "speed_rating =", "mergeIntoResults"):
            self.assertNotIn(danger, code, danger)

    def test_few_races_means_no_opinion(self):
        _ns, src = _load()
        self.assertIn("HAVING count(*) >= %(min_races)s", src)

    # ⚠ THE CTEs MUST BE MATERIALIZED or each one re-runs per row -- the same
    #   fault that made a crest query take fifteen minutes on 2026-09-18.
    def test_the_ctes_are_materialized(self):
        _ns, src = _load()
        self.assertEqual(src.count("AS MATERIALIZED"), 4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
