# Project: xc-predictor / tests
# File:    test_distance_between_venues.py
# Purpose: The between-venue distance estimator recovers a PLANTED bias,
#          and reports ZERO when there is none.
#
# ★ WHY A REAL POSTGRES. The whole estimator is SQL -- a leave-own-venue-out
#   mean built from two GROUP BYs and a regr_slope over it. Every way it can
#   be wrong (the benchmark including the row's own venue, the residual
#   sign, an athlete with only one venue leaking a zero into the mean) lives
#   in that SQL, so a mock proves nothing. It runs against whatever cluster
#   XCP_TEST_PGHOST/XCP_TEST_PGPORT point at, and skips when there is none.
#
# ⚠ THE SIGN IS THE POINT. normalized_time is a TIME. A row that is
#   OVERRATED has a normalized_time that is too SMALL, i.e. a NEGATIVE
#   residual -- so "short races overrated" is a POSITIVE slope on
#   ln(distance). test_short_overrated_reads_as_a_positive_slope is the
#   test that pins that down; an earlier diagnostic in this repo shipped
#   with its verdict inverted and read -1.00 on a fixture built to be +1.
import math
import os
import random
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "engine"), os.path.join(_ROOT, "scripts"),
           os.path.join(_ROOT, "racecast")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

HOST = os.environ.get("XCP_TEST_PGHOST")
PORT = os.environ.get("XCP_TEST_PGPORT")

try:
    import psycopg2
except ImportError:                                        # pragma: no cover
    psycopg2 = None

_DDL = """
DROP TABLE IF EXISTS results, meets, dist_override, course_difficulties, bv_rows;
CREATE TABLE course_difficulties (
    course_name text, difficulty float, distance_m float
);
CREATE TABLE meets (
    meet_id int, div_id int, source text,
    course_name text, distance float
);
CREATE TABLE dist_override (meet_id int, div_id int, distance float);
CREATE TABLE results (
    result_id serial, person_id int, date date,
    meet_id int, div_id int, source text,
    normalized_time float, rating_pool text
);
"""

N_ATHLETES = 600
N_VENUES = 14
RACES_PER = 5
SEED = 20260910


def _plant(cur, bias, noise=0.01):
    """Build a corpus where the ONLY distance-linked effect is `bias`.

    lnt = ability + venue difficulty + bias * (ln d - ln 5000) + noise

    Venue difficulty and venue distance are drawn INDEPENDENTLY, so a
    correct estimator has to return `bias` and nothing else; one that
    confuses a hard venue for a long one will not.
    """
    rng = random.Random(SEED)
    venues = []
    for v in range(N_VENUES):
        venues.append({
            "name": f"Venue {v}",
            "dist": rng.choice([3000, 3200, 4000, 4800, 5000, 5000,
                                6000, 6400, 8000]),
            "diff": rng.gauss(0.0, 0.05),
        })
    for v_i, v in enumerate(venues):
        cur.execute("INSERT INTO meets VALUES (%s, 1, 'x', %s, %s)",
                    (v_i, v["name"], v["dist"]))
        # the difficulty the solver would have found, planted exactly right
        cur.execute("INSERT INTO course_difficulties VALUES (%s, %s, %s)",
                    ("XC:" + v["name"], v["diff"], v["dist"]))

    rows = []
    for p in range(1, N_ATHLETES + 1):
        ability = math.log(1200.0) + rng.gauss(0.0, 0.08)
        picks = rng.sample(range(N_VENUES), RACES_PER)
        for v_i in picks:
            v = venues[v_i]
            lnt = (ability + v["diff"]
                   + bias * (math.log(v["dist"]) - math.log(5000.0))
                   + rng.gauss(0.0, noise))
            rows.append((p, "2024-10-01", v_i, 1, "x",
                         math.exp(lnt), "hs_m"))
    cur.executemany(
        "INSERT INTO results (person_id, date, meet_id, div_id, source,"
        " normalized_time, rating_pool) VALUES (%s,%s,%s,%s,%s,%s,%s)", rows)
    return venues


def _slope(cur):
    """Run the script's own SQL -- pass A then the slope -- and return the
    all-pools slope. Importing the constants means the test breaks if the
    query changes, which is the point of testing the shipped SQL."""
    import distance_between_venues as bv
    cur.execute(f"DROP TABLE IF EXISTS {bv._SCRATCH}")
    cur.execute(bv._PASS_A.format(pool=""), {"cut": 10000, "pool": None})
    cur.execute(bv._SLOPE, {"min_n": 1})
    rows = cur.fetchall()
    allrow = [r for r in rows if r[0] == "(all)"]
    assert allrow, "no rollup row"
    return float(allrow[0][2]), float(allrow[0][5]), rows


@unittest.skipUnless(HOST and PORT and psycopg2,
                     "set XCP_TEST_PGHOST/XCP_TEST_PGPORT to a scratch cluster")
class BetweenVenues(unittest.TestCase):
    def setUp(self):
        self.conn = psycopg2.connect(host=HOST, port=int(PORT),
                                     user="postgres", dbname="postgres")
        self.conn.autocommit = True
        self.cur = self.conn.cursor()
        self.cur.execute(_DDL)

    def tearDown(self):
        self.cur.close()
        self.conn.close()

    def test_the_raw_slope_is_confounded_by_venue_difficulty(self):
        # ⚠⚠ THE FINDING THIS FILE EXISTS FOR. Plant NO distance bias at
        #    all and the RAW between-venue slope still comes back large,
        #    because a venue's difficulty and its distance are one
        #    observation each: whatever correlation they happen to have
        #    lands in the slope. This is why the raw number cannot be read
        #    as "the curve is wrong" -- and why the adjusted column exists.
        _plant(self.cur, bias=0.0)
        raw, adj, _ = _slope(self.cur)
        self.assertGreater(abs(raw), 0.01,
                           "fixture no longer demonstrates the confound; "
                           f"raw={raw}. Re-draw the venue difficulties.")
        # ! NOT ZERO, AND IT CANNOT BE. The benchmark is a mean over the
        #   athlete's OTHER venues, which carries THEIR difficulties; with
        #   14 venues that leaves real noise. The claim is an order of
        #   magnitude, not a zero: 0.065 -> ~0.005.
        self.assertLess(abs(adj), abs(raw) / 5,
                        f"the control barely helped: raw={raw} adj={adj}")
        self.assertLess(abs(adj), 0.008,
                        f"difficulty-adjusted should read flat, got {adj}")

    def test_short_overrated_reads_as_a_positive_slope(self):
        # a curve that makes SHORT races too fast: their normalized_time is
        # too small, so the residual is negative at small distance and the
        # slope on ln(distance) is POSITIVE
        _plant(self.cur, bias=0.03)
        _, adj, _ = _slope(self.cur)
        self.assertGreater(adj, 0.02, f"expected about +0.03, got {adj}")
        self.assertLess(adj, 0.04, f"expected about +0.03, got {adj}")
        import distance_between_venues as bv
        self.assertIn("SHORT races are overrated", bv._verdict(adj))

    def test_long_overrated_reads_as_a_negative_slope(self):
        _plant(self.cur, bias=-0.03)
        _, adj, _ = _slope(self.cur)
        self.assertLess(adj, -0.02, f"expected about -0.03, got {adj}")
        import distance_between_venues as bv
        self.assertIn("LONG races are overrated", bv._verdict(adj))

    def test_the_benchmark_excludes_the_rows_own_venue(self):
        # ⚠ THE FAILURE THIS CATCHES. If the benchmark included the row's own
        #   venue, a venue's difficulty would partly cancel against itself and
        #   the cell would read SMALLER than it is. Plant one very hard venue
        #   and check the cell reports its full difficulty.
        venues = _plant(self.cur, bias=0.0)
        self.cur.execute("UPDATE meets SET course_name = 'HARD' "
                         "WHERE course_name = %s", (venues[0]["name"],))
        # rebuild that venue's rows a fixed 10% slow
        self.cur.execute(
            "UPDATE results SET normalized_time = normalized_time * 1.10 "
            "WHERE meet_id = 0")
        import distance_between_venues as bv
        self.cur.execute(f"DROP TABLE IF EXISTS {bv._SCRATCH}")
        self.cur.execute(bv._PASS_A.format(pool=""),
                         {"cut": 10000, "pool": None})
        self.cur.execute(bv._CELLS, {"cells": 50, "cell_n": 10})
        cells = {r[0]: float(r[3]) for r in self.cur.fetchall()}
        self.assertIn("HARD", cells)
        # ! WHAT THIS CELL ACTUALLY ESTIMATES, worked out rather than
        #   guessed. The benchmark is the mean over the athlete's OTHER
        #   venues, so with V venues drawn difficulty d_i and corpus mean m:
        #
        #       residual = d_0 - (V*m - d_0)/(V-1) = V/(V-1) * (d_0 - m)
        #
        #   i.e. the cell reports difficulty RELATIVE TO THE CORPUS MEAN
        #   (correct -- difficulty is only ever relative) with a V/(V-1)
        #   inflation from leaving one venue out of a finite pool. The 10%
        #   bump raises the mean by ln(1.10)/V, and the two terms combine
        #   so the bump itself survives at full size.
        V = len(venues)
        m = sum(v["diff"] for v in venues) / V
        want = 100 * (math.log(1.10) + (V / (V - 1.0)) * (venues[0]["diff"] - m))
        # the row's own venue is out of its own benchmark, so the cell
        # reports the WHOLE difficulty rather than a shrunken share
        self.assertGreater(cells["HARD"], want - 1.5,
                           f"own venue is leaking into its own benchmark: "
                           f"{cells['HARD']} vs {want:.2f}")
        self.assertLess(cells["HARD"], want + 1.5,
                        f"{cells['HARD']} vs {want:.2f}")

    def test_an_athlete_with_one_venue_is_dropped_not_zeroed(self):
        # a single-venue athlete has NO between-venue evidence; including
        # them with a residual of 0 would drag every estimate toward zero
        _plant(self.cur, bias=0.03)
        self.cur.execute(
            "INSERT INTO results (person_id, date, meet_id, div_id, source,"
            " normalized_time, rating_pool) "
            "SELECT 900000 + g, '2024-10-01', 0, 1, 'x', 1200.0, 'hs_m' "
            "FROM generate_series(1, 5000) g")
        _, adj, _ = _slope(self.cur)
        self.assertGreater(adj, 0.02,
                           f"single-venue athletes diluted the slope: {adj}")


if __name__ == "__main__":
    unittest.main()
