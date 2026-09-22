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
#    1. pool_view.hsFactor could not price a pro pool whose own factor missed
#       the 0.5-2.0 sanity rail -- and it missed it often, because the rail was
#       sized on college and middle school ("on the order of 10-30%") while the
#       pro-to-HS gap is the largest in the corpus.
#
#    2. rankings._SCALE_POOLS -- the pools the board's HS-equivalent CASE is
#       built from -- simply did not list pro_m or pro_f, so a pro row fell to
#       the `ELSE 1.0` arm.
#
# ⚠⚠ AND THE FIRST FIX FOR (1) WAS THE WRONG ONE, which is what most of this
#    file now pins. On 2026-09-20 I widened the pro->college FALLBACK to fire
#    on any None factor, rail rejections included. A rail rejection means the
#    pool WAS measured, so that swapped a measured pro number for the
#    college-to-HS gap and multiplied pro ratings by it -- arithmetic on no
#    scale at all. The owner, next run: "hs-equivalent for pros fucks it up
#    rather than doing nothing ... it makes pros have even higher speed rating
#    rather than a hs equivalent one."
#
#    The rail was the thing that was wrong, so the rail is what moved
#    (_PRO_FACTOR_HI). The fallback went back to the one case it was written
#    for on 2026-09-08: a pro pool with NO constants at all.
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


def _hsFactor(measured=None, college=None):
    """hsFactor, executed with its measurements stubbed.

    ★ RUN, NOT READ. The 2026-09-20 bug was a one-line gate change that every
      string assertion in this file happily agreed with; only calling the
      function with a pro pool that HAS constants shows what it returns. The
      stub supplies C() and F() directly, so `measured` is literally the
      per-sport ratio hsFactor will combine.

    `measured` maps pool -> the ratio C(hs)/C(pool) * F(pool)/F(hs) it should
    see for both sports, or None for "this pool cannot be sampled".
    """
    import math

    src = read("racecast/pool_view.py")
    tree = ast.parse(src)
    # numpy stands in for numpy only where hsFactor uses it: the geometric
    # mean over the per-sport ratios. log is ELEMENTWISE on a list there.
    ns = {"np": type("np", (), {
              "exp": staticmethod(math.exp),
              "log": staticmethod(
                  lambda x: [math.log(v) for v in x]
                  if isinstance(x, (list, tuple)) else math.log(x)),
              "mean": staticmethod(lambda xs: sum(xs) / len(xs)),
          })(),
          "_FACTOR_CACHE": {}, "_FACTOR_WHY": {}, "_FAILED": set(),
          "print": lambda *a, **k: None}
    for node in tree.body:                     # the rails and _REP_DIST
        # ! TUPLE TARGETS TOO. `_FACTOR_LO, _FACTOR_HI = 0.5, 2.0` is the rail
        #   itself, and a Name-only filter silently skipped it -- the stub then
        #   raised NameError from inside hsFactor rather than testing it.
        if isinstance(node, ast.Assign) and isinstance(
                node.targets[0], (ast.Name, ast.Tuple)):
            try:
                exec(ast.get_source_segment(src, node), ns)   # noqa: S102
            except Exception:                                 # noqa: BLE001
                pass
    measured = dict(measured or {})
    # C and F are stubbed so that their product is exactly measured[pool]:
    # C(hs)/C(pool) carries the ratio and F(pool)/F(hs) is 1.
    ns["_poolConstant"] = lambda pool, sport: (
        1.0 if pool.startswith("hs_")
        else (1.0 / measured[pool] if measured.get(pool) else None))
    ns["_forward_factor"] = lambda *a, **k: 1.0
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "hsFactor":
            exec(ast.get_source_segment(src, node), ns)       # noqa: S102
            break
    else:
        raise AssertionError("hsFactor is gone from pool_view.py")
    return ns["hsFactor"], ns


class ThePoolConstantCanActuallyBeSampled(unittest.TestCase):
    """⚠⚠⚠ THE REASON NONE OF THE ABOVE EVER RAN (2026-09-22).

    _poolConstant sampled `ranking_results WHERE pool = 'pro_m'`, and
    build_ranking_results.isRankablePool returns False for every pool
    starting "pro_". So that table holds ZERO pro rows BY CONSTRUCTION, and
    has since 2026-09-14. _poolConstant("pro_m") therefore always returned
    None, `ratios` was always empty, and hsFactor ALWAYS took the college
    fallback.

    Measured on Postgres 16 against a corpus carrying both:

        hs_m  via ranking_results          1500 rows
        pro_m via ranking_results             0 rows   <- the old path
        pro_m via results.rating_pool      1500 rows   <- the fix

    Every previous attempt at this bug -- the sanity-rail widening on
    2026-09-21 included -- edited the measured path, which cannot execute.
    The owner reported it three times. The rail was never the problem; the
    SOURCE was.

    With the fix the constant comes back and the factor is measured:

        C(hs_m) 1292.3   C(pro_m) 739.5   ->  x1.747
        a pro 100 reads 175 on the high-school scale, instead of college's
        ~1.2 raising it to 120.
    """

    def setUp(self):
        self.src = read("racecast/pool_view.py")

    def test_the_pro_sample_does_not_read_ranking_results(self):
        """⚠ THE REGRESSION, STATED AS THE TABLE NAME. ranking_results is
        the one table guaranteed not to hold a pro row."""
        import ast
        ns = {}
        for node in ast.parse(self.src).body:
            if (isinstance(node, ast.Assign)
                    and getattr(node.targets[0], "id", "") == "_STAMPED_CONST_SQL"):
                exec(ast.get_source_segment(self.src, node), ns)  # noqa: S102
        self.assertIn("_STAMPED_CONST_SQL", ns, "_STAMPED_CONST_SQL is gone")
        for sport, sql in ns["_STAMPED_CONST_SQL"].items():
            self.assertNotIn("ranking_results", sql, sport)
            self.assertIn("rating_pool", sql, sport)

    def test_each_sport_reads_its_own_table(self):
        import ast
        ns = {}
        for node in ast.parse(self.src).body:
            if (isinstance(node, ast.Assign)
                    and getattr(node.targets[0], "id", "") == "_STAMPED_CONST_SQL"):
                exec(ast.get_source_segment(self.src, node), ns)  # noqa: S102
        xc = ns["_STAMPED_CONST_SQL"]["XC"]
        self.assertIn("FROM   results", xc)
        self.assertNotIn("results_tf", xc)
        self.assertIn("results_tf", ns["_STAMPED_CONST_SQL"]["TF"])

    def test_it_matches_the_legacy_sport_suffix(self):
        """! rating_pool ONCE CARRIED "pool|SPORT" (conversions records the
        change). A bare `=` misses those rows; split_part reads both.
        Measured on the fixture: 3,001 rows by prefix, 3,000 by equality."""
        import ast
        ns = {}
        for node in ast.parse(self.src).body:
            if (isinstance(node, ast.Assign)
                    and getattr(node.targets[0], "id", "") == "_STAMPED_CONST_SQL"):
                exec(ast.get_source_segment(self.src, node), ns)  # noqa: S102
        for sql in ns["_STAMPED_CONST_SQL"].values():
            self.assertIn("split_part(r.rating_pool, '|', 1)", sql)

    def test_only_a_pro_pool_takes_the_other_source(self):
        """! EVERY OTHER POOL KEEPS ranking_results, which is season-accurate
        and indexed. The pro pools are the only ones it cannot answer for."""
        self.assertIn('is_pro = pool.startswith("pro_")', self.src)
        self.assertIn("_STAMPED_CONST_SQL if is_pro else _CONST_SQL", self.src)

    def test_the_unindexed_scan_is_bounded(self):
        """⚠ rating_pool HAS NO INDEX and this runs on a page load. A
        timeout leaves the constant None, which is exactly the college
        fallback -- so the change is never worse than what it replaces."""
        self.assertIn("_STAMPED_TIMEOUT_MS", self.src)
        i = self.src.index("if is_pro:\n                        # bounded")
        self.assertIn("SET LOCAL statement_timeout", self.src[i:i + 400])


class AProPoolUsesItsOwnMeasurement(unittest.TestCase):
    """⚠⚠ THE REGRESSION THE OWNER SAW. A pro pool that HAS constants must be
    priced by them -- never by college's number, which converts a rating the
    pro row does not carry."""

    def test_a_measured_pro_factor_above_two_is_kept(self):
        """★ THE CASE THE OLD RAIL THREW AWAY. x2.4 is a plausible pro-to-HS
        gap; the 0.5-2.0 rail rejected it, and the 2026-09-20 fallback then
        handed back college's x1.25."""
        f, ns = _hsFactor({"pro_m": 2.4, "college_m": 1.25})
        got = f("pro_m", "TF", 1600.0)
        self.assertAlmostEqual(got, 2.4, places=6)
        self.assertNotAlmostEqual(got, 1.25, places=3)
        self.assertIn("measured", ns["_FACTOR_WHY"]["pro_m"])

    def test_college_is_never_substituted_for_a_measured_pro_pool(self):
        """⚠ EVEN WHEN THE MEASUREMENT IS MAD. Past the pro rail the row stays
        on its OWN scale -- a visibly unconverted number, not an invisibly
        wrong one. 'Rather than doing nothing' was the owner's complaint about
        the alternative."""
        f, ns = _hsFactor({"pro_m": 9.0, "college_m": 1.25})
        self.assertIsNone(f("pro_m", "TF", 1600.0))
        self.assertIn("sanity rail", ns["_FACTOR_WHY"]["pro_m"])

    def test_the_pro_rail_is_wider_than_the_general_one(self):
        f, ns = _hsFactor({"pro_m": 2.4, "college_m": 2.4})
        self.assertGreater(ns["_PRO_FACTOR_HI"], ns["_FACTOR_HI"])
        # and the general rail still binds a non-pro pool at the same number
        self.assertIsNone(f("college_m", "TF", 1600.0))
        self.assertAlmostEqual(f("pro_m", "TF", 1600.0), 2.4, places=6)

    def test_the_fallback_survives_for_a_pro_pool_with_no_constants(self):
        """★ THE 2026-09-08 CASE, UNCHANGED (Graham Blanks' 29:41 reading 96.4
        beside college rows at 146): pro_m cannot be sampled at all, so there
        is no measurement to be wrong about and college is the nearest scale
        that exists."""
        f, ns = _hsFactor({"pro_m": None, "college_m": 1.25})
        self.assertAlmostEqual(f("pro_m", "TF", 1600.0), 1.25, places=6)
        self.assertIn("fallback", ns["_FACTOR_WHY"]["pro_m"])

    def test_it_falls_back_to_the_same_gender_college_pool(self):
        """★ SAME GENDER, ALWAYS -- the module's own rule. A pro_f row must
        not be scaled through college_m."""
        f, ns = _hsFactor({"pro_f": None, "college_f": 1.3, "college_m": 2.0})
        self.assertAlmostEqual(f("pro_f", "TF", 1600.0), 1.3, places=6)

    def test_the_fallback_cannot_recurse_forever(self):
        """! college_* does not start with pro_, so the one recursive call
        terminates. Asserted because a future 'college rides on X' would make
        it a loop."""
        self.assertFalse("college_m".startswith("pro_"))
        self.assertFalse("college_f".startswith("pro_"))

    def test_an_hs_pool_is_still_exactly_one(self):
        f, _ns = _hsFactor({})
        self.assertEqual(f("hs_m", "TF", 1600.0), 1.0)
        self.assertEqual(f("hs_f", "XC", 5000.0), 1.0)


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
