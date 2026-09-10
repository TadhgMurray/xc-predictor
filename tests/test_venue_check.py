# Project: xc-predictor / tests
# File:    test_venue_check.py
# Purpose: For a named venue, recover a PLANTED difficulty and a PLANTED
#          disagreement with the board.
#
# ★ WHY A NAMED-VENUE TOOL (owner: "Foot Locker too low / NXN too high").
#   Complaints arrive as "this course is wrong", not as "the corpus has a
#   bias". This measures the named venue against the same athletes' races
#   ELSEWHERE -- a benchmark the venue cannot move -- and puts that beside
#   what course_difficulties publishes.
#
# ⚠ THE SPLIT-HALF COLUMNS ARE NOT DECORATION. A venue whose two halves
#   disagree has no stable difficulty to argue about, and the gap against
#   the board is then noise on both sides. test_an_unstable_venue_is_flagged
#   plants exactly that case.
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

_DDL = """
DROP TABLE IF EXISTS results, meets, dist_override, course_difficulties,
                     vc_rows;
CREATE TABLE meets (meet_id int, div_id int, source text, course_name text,
                    distance float);
CREATE TABLE dist_override (meet_id int, div_id int, distance float);
CREATE TABLE results (person_id int, date text, meet_id int, div_id int,
                      source text, normalized_time float);
CREATE TABLE course_difficulties (course_name text, difficulty float,
    n_results int, n_athletes int, distance_m float);
"""

NAME = "Foot Locker Nationals"


def plant(cur, true_diff=0.08, published=0.02, day_sd=0.0, races=12,
          n_ath=3000, per=40, seed=21):
    """Ordinary venues at difficulty 0 to be the benchmark, plus one named
    venue with a known difficulty and a known published value."""
    rng = random.Random(seed)
    ability = [rng.gauss(0, 0.06) for _ in range(n_ath)]
    mrows, rrows = [], []
    mid = 0
    for v in range(30):
        for k in range(10):
            mrows.append((mid, 1, "x", f"Regular {v}", 5000))
            for a in rng.sample(range(n_ath), per):
                rrows.append((a, f"2023-10-{1 + k % 28:02d}", mid, 1, "x",
                              math.exp(ability[a] + rng.gauss(0, 0.03)
                                       + math.log(1200))))
            mid += 1
    for k in range(races):
        mrows.append((mid, 1, "x", NAME, 5000))
        day = rng.gauss(0, day_sd) if day_sd else 0.0
        for a in rng.sample(range(n_ath), per):
            rrows.append((a, f"2023-12-{1 + k % 28:02d}", mid, 1, "x",
                          math.exp(ability[a] + true_diff + day
                                   + rng.gauss(0, 0.03) + math.log(1200))))
        mid += 1
    cur.executemany("INSERT INTO meets VALUES (%s,%s,%s,%s,%s)", mrows)
    cur.executemany("INSERT INTO results VALUES (%s,%s,%s,%s,%s,%s)", rrows)
    if published is not None:
        cur.execute("INSERT INTO course_difficulties VALUES (%s,%s,%s,%s,%s)",
                    (f"XC:{NAME}", published, races * per, n_ath, 5000))


def measure(cur, pat="Foot Locker"):
    import venue_check as vc
    cur.execute(f"DROP TABLE IF EXISTS {vc._SCRATCH}")
    cur.execute(vc._PASS_A, {"pats": [f"%{pat}%"], "cut": 10000})
    cur.execute(vc._MEASURE, {"min_rows": 20})
    rows = cur.fetchall()
    assert rows, "the venue produced no measurable cell"
    return rows[0]      # venue, dist, races, results, measured, se, ha, hb


@unittest.skipUnless(HOST and PORT and psycopg2,
                     "set XCP_TEST_PGHOST/XCP_TEST_PGPORT to a scratch cluster")
class VenueCheck(unittest.TestCase):
    def setUp(self):
        self.conn = psycopg2.connect(host=HOST, port=int(PORT),
                                     user="postgres", dbname="postgres")
        self.conn.autocommit = True
        self.cur = self.conn.cursor()
        self.cur.execute(_DDL)

    def tearDown(self):
        self.cur.close()
        self.conn.close()

    def test_recovers_the_planted_difficulty(self):
        plant(self.cur, true_diff=0.08)
        row = measure(self.cur)
        self.assertAlmostEqual(float(row[4]), 8.0, delta=0.6,
                               msg=f"measured {row[4]}")

    def test_an_easy_venue_reads_negative(self):
        """⚠ THE SIGN. normalized_time is a TIME, so a venue where people
        run FAST must come out NEGATIVE. Getting this backwards would
        invert every verdict the script prints."""
        plant(self.cur, true_diff=-0.05)
        self.assertAlmostEqual(float(measure(self.cur)[4]), -5.0, delta=0.6)

    def test_the_halves_agree_on_a_stable_venue(self):
        plant(self.cur, true_diff=0.08)
        row = measure(self.cur)
        self.assertLess(abs(float(row[6]) - float(row[7])), 1.5,
                        f"halves {row[6]} vs {row[7]}")

    def test_an_unstable_venue_is_flagged(self):
        """Same true difficulty, but every race day swings hugely. The
        venue's own number is then not worth arguing about, and the halves
        are how you can tell."""
        plant(self.cur, true_diff=0.08, day_sd=0.10, races=6)
        row = measure(self.cur)
        self.assertGreater(abs(float(row[6]) - float(row[7])), 3.0,
                           f"halves {row[6]} vs {row[7]} -- not flagged")

    def test_the_venue_does_not_benchmark_itself(self):
        """⚠ If the venue's own rows leaked into the benchmark, its
        difficulty would partly cancel against itself and read SMALLER
        than planted. +8% must come back near +8%, not near +4%."""
        plant(self.cur, true_diff=0.08)
        self.assertGreater(float(measure(self.cur)[4]), 7.0)

    def test_a_name_that_matches_nothing_is_not_a_crash(self):
        plant(self.cur)
        import venue_check as vc
        self.cur.execute(f"DROP TABLE IF EXISTS {vc._SCRATCH}")
        self.cur.execute(vc._PASS_A, {"pats": ["%nowhere at all%"],
                                      "cut": 10000})
        self.cur.execute(f"SELECT count(*) FROM {vc._SCRATCH}")
        self.assertEqual(self.cur.fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
