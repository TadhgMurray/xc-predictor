# Project: xc-predictor / tests
# File:    test_pro_hs_equivalent.py
# Purpose: a professional-pool row can be shown on the HS-equivalent scale.
#
# ★★ THE ASK (owner, 2026-09-20): "one of the issues is when ppl are in pro
#    pool they are not able to be hs-equivalent. We should make them able to be
#    hs-equivalent."
#
#    Ratings are POOL-RELATIVE, so a row pooled pro carries a number on the
#    professional yardstick. Beside high schoolers on the same result page that
#    is not a small error -- it is the ~50-point split the owner was looking at
#    on 2026-09-20, and the HS-equivalent view is the thing that exists to
#    remove it.
#
# ⚠ TWO SEPARATE PLACES SAID NO, AND ONLY ONE OF THEM WAS KNOWN:
#
#    1. pool_view.hsFactor HAS had a pro fallback since 2026-09-08 -- a pro
#       pool rides on the same-gender college factor -- but it was gated on
#       `not ratios`, i.e. "pro_m had no constants at all". The OTHER way to
#       fail is a pro pool that HAS constants whose factor then misses the
#       0.5-2.0 sanity rail, and that is the likelier one: the pro-to-HS gap is
#       the largest in the corpus while the rail was sized on college and
#       middle school ("on the order of 10-30%"). That branch left ratios
#       non-empty, skipped the fallback, and returned None.
#
#    2. rankings._SCALE_POOLS -- the pools the board's HS-equivalent CASE is
#       built from -- simply did not list pro_m or pro_f, so a pro row fell to
#       the `ELSE 1.0` arm.
#
#   python -m unittest tests.test_pro_hs_equivalent
import ast
import io
import os
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(rel):
    with io.open(os.path.join(_ROOT, *rel.split("/")), encoding="utf-8") as fh:
        return fh.read()


class TheFallbackCatchesBothFailures(unittest.TestCase):
    """pool_view.hsFactor, read as a tree rather than as text: the pro
    fallback must be reachable from EVERY way the factor can come out None."""

    def setUp(self):
        self.src = read("racecast/pool_view.py")
        self.fn = None
        for node in ast.parse(self.src).body:
            if isinstance(node, ast.FunctionDef) and node.name == "hsFactor":
                self.fn = node
        self.assertIsNotNone(self.fn, "hsFactor is gone from pool_view.py")
        self.body = ast.get_source_segment(self.src, self.fn)

    def test_the_fallback_is_not_gated_on_the_constants_alone(self):
        """⚠ THE REGRESSION. `if not ratios and pool.startswith("pro_")` only
        catches the no-constants case; the sanity-rail rejection fell straight
        through it."""
        self.assertNotIn('if not ratios and pool.startswith("pro_")',
                         self.body)
        self.assertIn('if factor is None and pool.startswith("pro_")',
                      self.body)

    def test_the_fallback_runs_after_the_sanity_rail(self):
        """★ ORDER IS THE WHOLE FIX. The rail sets factor = None; the fallback
        has to be downstream of that or it cannot see it."""
        self.assertLess(self.body.index("_FACTOR_LO <= factor <= _FACTOR_HI"),
                        self.body.index('pool.startswith("pro_")'))

    def test_it_falls_back_to_the_same_gender_college_pool(self):
        """★ SAME GENDER, ALWAYS -- the module's own rule. A pro_f row must
        not be scaled through college_m."""
        self.assertIn('hsFactor("college_" + suffix', self.body)

    def test_the_fallback_cannot_recurse_forever(self):
        """! college_* does not start with pro_, so the one recursive call
        terminates. Asserted because a future 'college rides on X' would make
        it a loop."""
        self.assertFalse("college_m".startswith("pro_"))
        self.assertFalse("college_f".startswith("pro_"))


class TheBoardScalesProRows(unittest.TestCase):

    def setUp(self):
        self.src = read("racecast/rankings.py")

    def test_pro_is_in_the_scaled_pools(self):
        ns = {}
        for node in ast.parse(self.src).body:
            if (isinstance(node, ast.Assign)
                    and getattr(node.targets[0], "id", "") == "_SCALE_POOLS"):
                exec(ast.get_source_segment(self.src, node), ns)  # noqa: S102
        self.assertIn("_SCALE_POOLS", ns, "_SCALE_POOLS is gone")
        for p in ("pro_m", "pro_f"):
            self.assertIn(p, ns["_SCALE_POOLS"])

    def test_hs_pools_stay_out_on_purpose(self):
        """! THEIR FACTOR IS 1.0 BY CONSTRUCTION and `ELSE 1.0` covers them;
        naming them would add CASE arms that cannot change a number."""
        ns = {}
        for node in ast.parse(self.src).body:
            if (isinstance(node, ast.Assign)
                    and getattr(node.targets[0], "id", "") == "_SCALE_POOLS"):
                exec(ast.get_source_segment(self.src, node), ns)  # noqa: S102
        for p in ("hs_m", "hs_f"):
            self.assertNotIn(p, ns["_SCALE_POOLS"])


class WhatStillKeepsProOffTheBoards(unittest.TestCase):
    """⚠⚠ THE HONEST LIMIT OF THE CHANGE ABOVE, pinned so nobody reads the
    board fix as more than it is.

    build_ranking_results.isRankablePool refuses a pro pool outright, and
    athlete_season is built FROM ranking_results, so no pro row reaches either
    table -- which means _SCALE_POOLS' new arms cannot fire today. That
    exclusion is an owner decision from 2026-09-14 with its own reason on
    record ("published under a school pool it headed the college boards at
    170"), so it is left standing and asserted here rather than quietly
    reversed. Deleting this test is the deliberate act of changing that."""

    def test_pro_pools_are_still_excluded_from_the_boards_table(self):
        src = read("racecast/build_ranking_results.py")
        i = src.index("def isRankablePool")
        body = src[i:src.index("\ndef ", i + 10)]
        self.assertIn('startswith("pro_")', body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
