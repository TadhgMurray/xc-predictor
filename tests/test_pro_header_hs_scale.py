# Project: xc-predictor / tests
# File:    test_pro_header_hs_scale.py
# Purpose: the athlete HEADER's HS-equivalent number is scaled by the factor
#          of the pool the header number is IN -- not by a median over a
#          career that spans two scales.
#
# ⚠⚠⚠ OWNER, 2026-09-22: "athlete rating for the pro pool ppl is completely
#     fucked up for hs-equivalent. However hs-equivalent now works for indiv
#     races."
#
#     Both halves of that sentence are the diagnosis. Per-race numbers are
#     right because pool_view.stampHsRatings gives every race ITS OWN pool's
#     factor. The header took a different path: seasonFactor(races) with no
#     label, no sport and no pool -- the median hs/own ratio over the WHOLE
#     CAREER -- applied to one number pulled from athlete_ratings.
#
#     For a career spanning pools that median is between 1.00 (the high
#     school races) and 0.78 (the pro ones): the factor of no pool the
#     athlete ever raced in, and the further the two scales sit apart the
#     more wrong it gets.
#
# ! WHY IT IS ALWAYS THE PRO ATHLETES. The header prefers athlete_season and
#   only falls back to athlete_ratings when there is no row. athlete_season
#   is built from ranking_results, and build_ranking_results.isRankablePool
#   refuses every pro_* pool -- so a pro-pooled athlete has no row, takes the
#   fallback branch, and gets the career blend, every single time. It is not
#   a corner case for them; it is the only path they have.
#
# ★ THE FIX IS NOT A BETTER MEDIAN. The fallback SELECT already reads `pool`
#   beside `speed_rating`; the scale was on the row all along. hsFactor is
#   ONE NUMBER PER POOL by construction, so the pool's factor is exact and a
#   median over that pool's races could only reproduce it with noise.
#
#   python -m unittest tests.test_pro_header_hs_scale
import ast
import io
import os
import textwrap
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _elseBlock():
    """The `else:` suite of `if season_rating:` in the athlete route, lifted
    from app.py and dedented.

    ! LIFTED, NOT TRANSCRIBED. A copy of these six lines in here would keep
      passing after the original changed, which is the failure mode this
      whole file exists to catch one layer up.
    """
    with io.open(os.path.join(_ROOT, "racecast", "app.py"),
                 encoding="utf-8") as fh:
        src = fh.read()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        if not (isinstance(node.test, ast.Name) and node.test.id == "season_rating"):
            continue
        if not node.orelse or isinstance(node.orelse[0], ast.If):
            continue
        body = node.orelse
        # the branch that assigns rating_note = None is the header fallback
        text = "\n".join(ast.get_source_segment(src, s) for s in body)
        if 'athlete["rating_note"] = None' in text:
            return textwrap.dedent(text)
    raise AssertionError("the athlete header's fallback branch is gone")


class _Spy(object):
    """Records every call, returns a per-pool factor."""

    def __init__(self, factors):
        self.factors = factors
        self.calls = []

    def repFactor(self, pool, sport=None):
        self.calls.append((pool, sport))
        return self.factors.get((pool or "").split("|", 1)[0])

    def seasonFactor(self, races, label=None, sport=None):
        raise AssertionError(
            "the header fallback called seasonFactor -- that is the career "
            "blend this file exists to keep out of it")


def _run(rating, factors, races=None):
    spy = _Spy(factors)
    athlete = {}
    ns = {"athlete": athlete, "rating": rating, "races": races or [],
          "repFactor": spy.repFactor, "seasonFactor": spy.seasonFactor}
    exec(compile(_elseBlock(), "<app.py:else>", "exec"), ns)   # noqa: S102
    return athlete, spy


# ! THE NUMBERS ARE THE OWNER'S OWN, off the pool_view diagnostic he pasted
#   on 2026-09-22: C(pro_m) = 1553.1 against C(hs_m) = 1211.5, which is the
#   0.78 the pro pool reads.
_PRO, _HS, _COLLEGE = 0.78, 1.0, 0.92


class TheHeaderUsesTheRatingsOwnScale(unittest.TestCase):

    def test_a_pro_pool_rating_is_scaled_by_the_pro_factor(self):
        athlete, spy = _run({"speed_rating": 100.0, "pool": "pro_m"},
                            {"pro_m": _PRO, "hs_m": _HS})
        self.assertAlmostEqual(athlete["rating_hs"], 78.0, places=6)
        self.assertEqual([p for p, _s in spy.calls], ["pro_m"])

    def test_a_career_spanning_two_scales_cannot_reach_the_factor(self):
        """⚠ THE BUG ITSELF. Eleven high school races and nine pro ones put
        the old median at 1.00; the header number is a PRO number, so the
        page showed a professional's rating as a high schooler's unchanged.

        The races are handed in exactly as before and must now be ignored.
        """
        races = ([{"speed_rating": 100.0, "hs_rating": 100.0}] * 11
                 + [{"speed_rating": 100.0, "hs_rating": 78.0}] * 9)
        athlete, _ = _run({"speed_rating": 100.0, "pool": "pro_m"},
                          {"pro_m": _PRO, "hs_m": _HS}, races=races)
        self.assertAlmostEqual(athlete["rating_hs"], 78.0, places=6)

    def test_a_namespaced_pool_keeps_its_bare_name(self):
        """! athlete_ratings.pool is stored bare ('hs_m') OR namespaced
        ('hs_m|XC') -- conversions._norm_from_athlete has tried both since
        it was written. The sport goes to repFactor separately."""
        athlete, spy = _run({"speed_rating": 200.0, "pool": "college_m|XC"},
                            {"college_m": _COLLEGE})
        self.assertAlmostEqual(athlete["rating_hs"], 184.0, places=6)
        self.assertEqual(spy.calls, [("college_m|XC", "XC")])

    def test_an_hs_rating_is_left_alone(self):
        athlete, _ = _run({"speed_rating": 140.0, "pool": "hs_m"},
                          {"hs_m": _HS})
        self.assertAlmostEqual(athlete["rating_hs"], 140.0, places=6)

    def test_no_factor_means_no_hs_number_not_a_shrugged_one(self):
        """★ hsFactor's contract: "callers must treat None as leave the
        rating on its own scale, never as 1.0-with-a-shrug". An
        unknown_gender pool has no HS twin."""
        athlete, _ = _run({"speed_rating": 111.0, "pool": "hs_unknown_gender"},
                          {})
        self.assertIsNone(athlete["rating_hs"])
        self.assertEqual(athlete["rating"], 111.0)

    def test_no_rating_row_at_all(self):
        athlete, _ = _run(None, {"hs_m": _HS})
        self.assertIsNone(athlete["rating"])
        self.assertIsNone(athlete["rating_hs"])

    def test_a_rating_of_zero_does_not_become_an_hs_number(self):
        """! `if athlete["rating"] is not None` is the guard, and 0.0 times
        a factor is 0.0 -- kept explicit because the sibling `and _sf`
        makes the reading easy to get wrong."""
        athlete, _ = _run({"speed_rating": 0.0, "pool": "pro_m"},
                          {"pro_m": _PRO})
        self.assertAlmostEqual(athlete["rating_hs"], 0.0, places=6)


class TheRealFunctionAgrees(unittest.TestCase):
    """! THE SPY IS A STAND-IN FOR ONE THING ONLY -- the pool constants,
    which need a database. That repFactor exists, takes (pool, sport) and
    returns None rather than 1.0 for a pool with no HS twin is checked
    against the real module."""

    def test_repfactor_has_the_signature_the_header_calls(self):
        import sys
        sys.path.insert(0, os.path.join(_ROOT, "racecast"))
        sys.path.insert(0, os.path.join(_ROOT, "scripts"))
        sys.path.insert(0, os.path.join(_ROOT, "engine"))
        import pool_view
        self.assertIsNone(pool_view.hsFactor(None, "XC", 5000.0))
        self.assertIsNone(pool_view.repFactor(None, "XC"))
        self.assertIsNone(pool_view.repFactor("hs_unknown_gender", "XC"))
        self.assertEqual(pool_view.repFactor("hs_m", "XC"), 1.0)
        self.assertEqual(pool_view.repFactor("hs_m|XC", "XC"), 1.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
