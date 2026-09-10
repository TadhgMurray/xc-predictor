# Project: xc-predictor / tests
# File:    test_season_reliability.py
# Purpose: Recover a PLANTED true spread from two sports measured with
#          different amounts of noise, and put the board back on one ruler.
#
# ★ THE REAL FINDING THIS SERVES (sport_gap_from_top.py on college_m):
#   mean rating XC 101.63 / TF 104.32 -- track is HIGHER -- but sd XC 10.17
#   / TF 7.61, and the top 200 is 95% XC. Cross country is not rated
#   higher, it is rated WIDER, and the extremes of a wider distribution own
#   the top of a board. A single --sport-gap-delta cannot fix that: there
#   is no offset to remove, and the balancing shift drifts 2.8x with depth.
#
# ⚠⚠ AND THE THING THAT IS EASY TO GET WRONG. The board factor is
#    sqrt(reliability), NOT reliability. Kelley shrinkage gives the
#    posterior mean -- right for one athlete's best guess -- but its spread
#    is sqrt(r) x the true spread, so it under-disperses, and it
#    under-disperses the NOISIER sport more. On the fixture below, shrinking
#    by r took the top 200 from 89% XC past even to 15% XC: one lopsided
#    board swapped for the opposite one. sqrt(r) lands it on 50.5%.
import math
import os
import random
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

HOST = os.environ.get("XCP_TEST_PGHOST")
PORT = os.environ.get("XCP_TEST_PGPORT")

try:
    import psycopg2
except ImportError:                                        # pragma: no cover
    psycopg2 = None

# ! date AS TEXT, which is how results_tf really is.
_DDL = """
DROP TABLE IF EXISTS results, results_tf, sr_rows;
CREATE TABLE results (person_id int, date text, speed_rating float,
                      rating_pool text);
CREATE TABLE results_tf (person_id int, date text, speed_rating float,
                         rating_pool text);
"""


def plant(cur, true_sd=6.0, xc_races=4, xc_noise=12.0,
          tf_races=10, tf_noise=2.0, n=2000, seed=3):
    """ONE fitness per athlete, measured twice with different noise.

    Any difference the estimator reports between the sports' TRUE spreads
    is its own error -- there is only one spread here.
    """
    rng = random.Random(seed)
    xc, tf = [], []
    for p in range(1, n + 1):
        fit = rng.gauss(100, true_sd)
        for k in range(xc_races):
            xc.append((p, f"2024-{9 + k % 4:02d}-15",
                       fit + rng.gauss(0, xc_noise), "college_m"))
        for k in range(tf_races):
            tf.append((p, f"2025-{3 + k % 4:02d}-15",
                       fit + rng.gauss(0, tf_noise), "college_m"))
    cur.executemany("INSERT INTO results VALUES (%s,%s,%s,%s)", xc)
    cur.executemany("INSERT INTO results_tf VALUES (%s,%s,%s,%s)", tf)


def halves(cur):
    import season_reliability as sr
    cur.execute(f"DROP TABLE IF EXISTS {sr._SCRATCH}")
    cur.execute(sr._PASS_A, {"cut": 10000})
    cur.execute(sr._HALVES, {"half_min": 2})
    by = {}
    for _pool, sport, _n, r0, r1 in cur.fetchall():
        by.setdefault(sport, []).append((float(r0), float(r1)))
    return by


@unittest.skipUnless(HOST and PORT and psycopg2,
                     "set XCP_TEST_PGHOST/XCP_TEST_PGPORT to a scratch cluster")
class SeasonReliability(unittest.TestCase):
    def setUp(self):
        self.conn = psycopg2.connect(host=HOST, port=int(PORT),
                                     user="postgres", dbname="postgres")
        self.conn.autocommit = True
        self.cur = self.conn.cursor()
        self.cur.execute(_DDL)

    def tearDown(self):
        self.cur.close()
        self.conn.close()

    def test_recovers_one_true_spread_from_two_noise_levels(self):
        import season_reliability as sr
        plant(self.cur)
        by = halves(self.cur)
        xc = sr._reliability(by["XC"])
        tf = sr._reliability(by["TF"])
        # observed spreads must DIFFER, or the fixture proves nothing
        self.assertGreater(xc[1] / tf[1], 1.2,
                           "fixture no longer shows XC measured wider")
        # ...and the recovered TRUE spreads must agree, at the planted 6.0
        self.assertAlmostEqual(xc[2], 6.0, delta=0.5, msg=f"XC true {xc[2]}")
        self.assertAlmostEqual(tf[2], 6.0, delta=0.5, msg=f"TF true {tf[2]}")

    def test_the_noisier_sport_reads_less_reliable(self):
        import season_reliability as sr
        plant(self.cur)
        by = halves(self.cur)
        self.assertLess(sr._reliability(by["XC"])[0],
                        sr._reliability(by["TF"])[0] - 0.2)

    def test_sqrt_reliability_balances_the_board(self):
        import season_reliability as sr
        plant(self.cur)
        by = halves(self.cur)
        stats = {s: sr._reliability(p) for s, p in by.items()}
        before, fixed = [], []
        for sport, pairs in by.items():
            rel, _, _, mean = stats[sport]
            root = math.sqrt(rel)
            for a, b in pairs:
                full = (a + b) / 2
                before.append((full, sport))
                fixed.append((mean + root * (full - mean), sport))
        # ! 0.7, not 0.8: with 2,000 athletes the top 200 is a deeper
        #   slice of the distribution than it was at 6,000, so the raw
        #   imbalance is milder. Still plainly lopsided, which is all this
        #   guard needs to assert.
        self.assertGreater(sr._mix(before, 200), 0.7,
                           "fixture no longer produces a lopsided board")
        self.assertAlmostEqual(sr._mix(fixed, 200), 0.5, delta=0.12)

    def test_kelley_overshoots_the_other_way(self):
        """⚠ THE TRAP, kept as a test so nobody 'simplifies' sqrt(r) away.
        Shrinking by r itself does not merely under-correct -- it flips the
        board past even, to the opposite imbalance."""
        import season_reliability as sr
        plant(self.cur)
        by = halves(self.cur)
        stats = {s: sr._reliability(p) for s, p in by.items()}
        kelley = []
        for sport, pairs in by.items():
            rel, _, _, mean = stats[sport]
            for a, b in pairs:
                kelley.append((mean + rel * ((a + b) / 2 - mean), sport))
        self.assertLess(sr._mix(kelley, 200), 0.35,
                        "Kelley no longer overshoots; re-check the header")

    def test_equal_noise_leaves_the_board_alone(self):
        """The correction must do nothing when there is nothing to correct."""
        import season_reliability as sr
        plant(self.cur, xc_races=8, xc_noise=3.0, tf_races=8, tf_noise=3.0)
        by = halves(self.cur)
        stats = {s: sr._reliability(p) for s, p in by.items()}
        self.assertAlmostEqual(stats["XC"][0], stats["TF"][0], delta=0.06)
        fixed = []
        for sport, pairs in by.items():
            rel, _, _, mean = stats[sport]
            root = math.sqrt(rel)
            for a, b in pairs:
                fixed.append((mean + root * ((a + b) / 2 - mean), sport))
        self.assertAlmostEqual(sr._mix(fixed, 200), 0.5, delta=0.10)


if __name__ == "__main__":
    unittest.main()
