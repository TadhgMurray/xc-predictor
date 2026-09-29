# Project: xc-predictor / tests
# File:    test_rating_outliers.py
# Purpose: the outlier rule's judgement, without a database.
#
#   python -m pytest -q tests/test_rating_outliers.py
#
# ⚠⚠ THE POINT OF THE WHOLE MODULE, as arithmetic. Owner (2026-09-18): "if a
#    result is more than 5-15 sigma away (from their season's median or mean or
#    whatever) do not rate or rank." With MEAN and standard deviation that
#    range catches nothing, because one outlier inflates the deviation it is
#    measured against: a 400 among five ratings near 150 scores z = 2.24.
#    Against median and MAD the same row scores z = 124.6. Pinned below, since
#    a future "simplification" to mean/sd would silently disable the rule.
#
# ★★ AND THE NEIGHBOURS, NOT THE SEASON (owner, 2026-09-29, Jack Moretta).
#    The Postgres half -- the window, the shards, the season number -- is
#    tests/test_rating_outliers_pg.py.
import math
import os
import statistics as st
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, "engine"), os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")

import rating_outliers as RO                                    # noqa: E402

with open(os.path.join(_ROOT, "engine", "rating_outliers.py"),
          encoding="utf-8") as _fh:
    SRC = _fh.read()


class TheRobustSpreadIsTheWholeDesign(unittest.TestCase):

    SEASON = [150.0, 151.0, 149.5, 150.5, 152.0, 400.0]

    def test_mean_and_sd_would_hide_the_outlier(self):
        mean, sd = st.mean(self.SEASON), st.pstdev(self.SEASON)
        self.assertLess((400.0 - mean) / sd, 3.0)

    def test_median_and_mad_catch_it(self):
        _med, _s, _sigma, z = RO.neighbourZ(400.0, self.SEASON[:-1], 2.0)
        self.assertGreater(z, 15.0)

    def test_the_scaling_constant_is_the_standard_one(self):
        self.assertAlmostEqual(RO.MAD_TO_SIGMA, 1.4826, places=4)

    # ! A ZERO MAD MUST NOT DIVIDE BY ZERO: the floor is the corpus's own
    #   spread, measured, and applied in the SQL too.
    def test_a_zero_mad_is_floored_not_infinite(self):
        med, s_local, sigma, z = RO.neighbourZ(90.0, [100.0] * 5, 3.0)
        self.assertEqual((med, s_local, sigma), (100.0, 0.0, 3.0))
        self.assertAlmostEqual(z, -10.0 / 3.0)
        self.assertIn("GREATEST(", RO.zSql("x", 6, 3.0))

    def test_the_fixed_spread_floor_is_gone(self):
        self.assertFalse(hasattr(RO, "MIN_SPREAD"))
        self.assertFalse(hasattr(RO, "MIN_RACES"))


class Moretta(unittest.TestCase):
    """Owner, 2026-09-29: Bobby Doyle 80.4 "not real (easy LR)", Aldrich 97.3
    "real". His ten other rated races, both sports, one scale."""

    OTHERS = [97.2, 104.8, 105.1, 104.7, 105.7, 101.0, 104.6, 103.9, 102.9]

    def test_bobby_doyle_sits_far_under_his_neighbours(self):
        med, _s, _sig, z = RO.neighbourZ(80.4, self.OTHERS + [97.3], 3.0)
        self.assertAlmostEqual(med, 104.25)
        self.assertLess(z, -RO.OWNER_RANGE[0])

    def test_aldrich_does_not(self):
        _med, _s, _sig, z = RO.neighbourZ(97.3, self.OTHERS + [80.4], 3.0)
        self.assertGreater(z, -RO.OWNER_RANGE[0])

    # ⚠ WHAT THE SEASON RULE SAW: two races in "2026". No median of two is
    #   robust, which is why a neighbourhood needs at least three.
    def test_a_median_needs_three(self):
        self.assertEqual(RO.MIN_NEIGHBOURS, 3)


class TheCutIsMeasured(unittest.TestCase):

    @staticmethod
    def normalHist(n, outliers=0, at=10.0):
        """|z| histogram of n half-normal rows plus some far rows."""
        h = {}
        edges = [i * RO.BIN for i in range(0, 41)]
        cdf = [math.erfc(e / math.sqrt(2)) for e in edges]     # 2-sided tail
        for i in range(len(edges) - 1):
            c = round(n * (cdf[i] - cdf[i + 1]))
            if c:
                h[i] = c
        if outliers:
            b = int(at / RO.BIN)
            h[b] = h.get(b, 0) + outliers
        return h

    # ! NOT QUITE THE BOTTOM, AND ON PURPOSE: an exponential through a
    #   normal's 2-3 sigma band expects ~30 genuine rows past 5 where a
    #   normal has 0.6, so the cut errs high -- 5.75 here.
    def test_a_clean_normal_corpus_cuts_near_the_bottom_of_the_range(self):
        cut, _why, _rows = RO.deriveCut(self.normalHist(1_000_000, 500))
        self.assertGreaterEqual(cut, RO.OWNER_RANGE[0])
        self.assertLessEqual(cut, 6.0)

    # ★ THE FIT MUST NOT SEE THE OUTLIERS. A survival fit through "past 2"
    #   and "past 3" counted every jog twice and raised the cut for having
    #   them; the density fit is blind to rows outside [2, 3).
    def test_outliers_do_not_raise_the_cut(self):
        few = RO.deriveCut(self.normalHist(100_000, 10))[0]
        many = RO.deriveCut(self.normalHist(100_000, 5000))[0]
        self.assertLessEqual(many, few)

    def test_a_heavy_tail_raises_the_cut(self):
        # an exponential (Laplace) tail, falling e-fold per sigma
        h = {i: round(100_000 * math.exp(-i * RO.BIN)) for i in range(0, 80)}
        h[int(14 / RO.BIN)] += 200
        cut, _why, rows = RO.deriveCut(h)
        self.assertGreater(cut, 8.0)
        self.assertLessEqual(cut, 14.0, "the 200 far rows are still caught")
        _c, seen, genuine = next(r for r in rows if abs(r[0] - cut) < 1e-9)
        self.assertGreater(seen, 0)
        self.assertLessEqual(genuine, RO.FDR * seen)

    def test_too_few_rows_is_no_opinion(self):
        cut, why, _ = RO.deriveCut({8: 5, 40: 3})
        self.assertEqual(cut, RO.OWNER_RANGE[1])
        self.assertIn("under the", why)

    def test_the_owner_range(self):
        self.assertEqual(RO.OWNER_RANGE, (5.0, 15.0))

    def test_k_is_the_best_predictor_and_ties_go_large(self):
        curve = [(3, 10, 2.0), (6, 10, 1.8), (8, 10, 1.8), (16, 10, 1.9)]
        self.assertEqual(RO.pickK(curve), 8)
        self.assertIsNone(RO.pickK([]))


class TheSql(unittest.TestCase):

    def test_neighbours_come_from_both_sides_of_the_race(self):
        sql = RO.nearSql("s", 6)
        self.assertIn("ROWS BETWEEN 6 PRECEDING", sql)
        self.assertIn("AND 6 FOLLOWING", sql)
        self.assertIn("w.ks[i] <> w.key", sql, "the race is not its own neighbour")

    def test_no_calendar_season_in_the_judgement(self):
        self.assertNotIn("substr(r.date, 1, 4)::int AS season", SRC)
        self.assertNotIn("PARTITION BY s.person_id, s.season", SRC)

    # ⚠ OFFSET 0 on every lateral: without it the ARRAY sort runs once per
    #   reference, 132 s instead of 16 s per million rows.
    def test_the_laterals_are_fenced(self):
        sql = RO.zSql("s", 6, 3.0)
        self.assertGreaterEqual(sql.count("OFFSET 0"), 4)

    def test_relays_twins_and_impossible_rows_are_left_out(self):
        self.assertIn("COALESCE(r.is_relay, 0) = 0", SRC)
        self.assertIn('("result_twin", "impossible_result")', SRC)

    def test_last_runs_fast_rows_are_no_ones_neighbour(self):
        self.assertIn("FILTER (WHERE s.nb)", RO.nearSql("s", 6))
        self.assertIn("po.side = ", SRC)

    def test_it_writes_a_table_and_does_not_touch_speed_rating(self):
        self.assertIn("CREATE TABLE IF NOT EXISTS rating_outlier", SRC)
        code = SRC.split('"""', 2)[2]
        for danger in ("UPDATE results", "UPDATE results_tf",
                       "speed_rating =", "mergeIntoResults"):
            self.assertNotIn(danger, code, danger)

    def test_both_sides_are_written(self):
        self.assertIn("CASE WHEN z > 0 THEN 'fast' ELSE 'slow' END", SRC)


if __name__ == "__main__":
    unittest.main(verbosity=2)
