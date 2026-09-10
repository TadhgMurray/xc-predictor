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
-- ! speed_rating IS REAL AND THE SCRIPT NEEDS IT: the row model applies
--   h x difficulty (joint_solve.amplitudeFromRating), so a raw residual
--   measures mean(h) x d, not d.
CREATE TABLE results (person_id int, date text, meet_id int, div_id int,
                      source text, normalized_time float,
                      speed_rating float);
CREATE TABLE course_difficulties (course_name text, difficulty float,
    n_results int, n_athletes int, distance_m float);
"""

NAME = "Foot Locker Nationals"


# ★ EVERY XC COURSE CARRIES THIS BEFORE IT IS HARD AT ALL. Published
#   difficulty is anchored on the median TRACK, so the whole sport sits
#   about +6.9 up the scale. A comparison that does not cancel it reports
#   the anchor as an error -- which is how a published +8.69 and a measured
#   -1.39 once looked like a ten-point scandal over a three-point
#   disagreement.
TRACK_ANCHOR = 0.069


def plant(cur, true_diff=0.08, published=None, day_sd=0.0, races=12,
          n_ath=3000, per=40, seed=21, anchor=TRACK_ANCHOR,
          board_error=0.0, rating=100.0):
    """Ordinary venues scattered around the XC average, plus one named
    venue at a known difficulty.

    Every course gets a published difficulty on the TRACK-anchored scale
    (true + anchor), and the named venue's is offset by `board_error` so a
    genuine board mistake can be planted separately from the anchor.
    """
    rng = random.Random(seed)
    # ★ THE FIXTURE HAS TO GENERATE WHAT THE MODEL GENERATES. The row model
    #   is ability + h * difficulty, so planting the difficulty un-tilted
    #   would make the raw residual already equal d and there would be
    #   nothing for untilted_pct to undo -- the test would pass on a script
    #   that ignored the tilt entirely.
    h = min(1.80, max(0.15, 1.0 - 0.01135 * (rating - 100.0)))
    ability = [rng.gauss(0, 0.06) for _ in range(n_ath)]
    mrows, rrows, drows = [], [], []
    mid = 0
    for v in range(30):
        base = rng.gauss(0.0, 0.02)
        drows.append((f"XC:Regular {v}",
                      math.expm1(math.log1p(base) + anchor), 400, 300, 5000))
        for k in range(10):
            mrows.append((mid, 1, "x", f"Regular {v}", 5000))
            for a in rng.sample(range(n_ath), per):
                rrows.append((a, f"2023-10-{1 + k % 28:02d}", mid, 1, "x",
                              math.exp(ability[a] + base + rng.gauss(0, 0.03)
                                       + math.log(1200)), rating))
            mid += 1
    for k in range(races):
        mrows.append((mid, 1, "x", NAME, 5000))
        day = rng.gauss(0, day_sd) if day_sd else 0.0
        for a in rng.sample(range(n_ath), per):
            rrows.append((a, f"2023-12-{1 + k % 28:02d}", mid, 1, "x",
                          math.exp(ability[a] + h * true_diff + day
                                   + rng.gauss(0, 0.03) + math.log(1200)),
                          rating))
        mid += 1
    drows.append((f"XC:{NAME}",
                  math.expm1(math.log1p(true_diff + board_error) + anchor),
                  races * per, n_ath, 5000))
    cur.executemany("INSERT INTO meets VALUES (%s,%s,%s,%s,%s)", mrows)
    cur.executemany("INSERT INTO results VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    rrows)
    cur.executemany("INSERT INTO course_difficulties VALUES "
                    "(%s,%s,%s,%s,%s)", drows)


def measure(cur, pat="Foot Locker"):
    import venue_check as vc
    cur.execute(f"DROP TABLE IF EXISTS {vc._SCRATCH}")
    cur.execute(vc._PASS_A, {"pats": [f"%{pat}%"], "cut": 10000})
    cur.execute(vc._MEASURE, {"min_rows": 20})
    rows = cur.fetchall()
    assert rows, "the venue produced no measurable cell"
    # venue, dist, races, results, measured, field_tilt, untilted,
    # board_says, se, half_a, half_b
    return rows[0]


# ! NAMED, NOT INDEXED. Adding board_says_pct as column 5 shifted the halves
#   from 6,7 to 7,8 and two tests failed on the OLD columns while still
#   looking like they were about halves.
def _halves(row):
    return float(row[9]), float(row[10])


def _board(row):
    return float(row[7])


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
        ha, hb = _halves(measure(self.cur))
        self.assertLess(abs(ha - hb), 1.5, f"halves {ha} vs {hb}")

    def test_an_unstable_venue_is_flagged(self):
        """Same true difficulty, but every race day swings hugely. The
        venue's own number is then not worth arguing about, and the halves
        are how you can tell."""
        plant(self.cur, true_diff=0.08, day_sd=0.10, races=6)
        ha, hb = _halves(measure(self.cur))
        self.assertGreater(abs(ha - hb), 3.0,
                           f"halves {ha} vs {hb} -- not flagged")

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


    def test_the_track_anchor_cancels(self):
        """★★ THE BUG THIS FILE EXISTS TO PREVENT A SECOND TIME. Published
        difficulty lives on a track-anchored scale; the measured residual
        is against the athlete's other XC courses, whose zero is that same
        anchor. Comparing them raw reports the anchor as a scandal.

        Here the board is CORRECT and the anchor is large. measured and
        board_says must agree, and both must be far from the published
        number."""
        plant(self.cur, true_diff=0.05, board_error=0.0)
        row = measure(self.cur)
        measured, board = float(row[4]), float(row[7])
        self.assertAlmostEqual(measured, 5.0, delta=0.8)
        self.assertAlmostEqual(board, measured, delta=0.8,
                               msg=f"anchor did not cancel: {board} vs "
                                   f"{measured}")
        # and the raw published number is a full anchor away from both
        self.cur.execute("SELECT difficulty FROM course_difficulties "
                         "WHERE course_name = %s", (f"XC:{NAME}",))
        raw = 100 * float(self.cur.fetchone()[0])
        self.assertGreater(raw - measured, 5.0,
                           "the fixture no longer carries a real anchor")

    def test_a_genuinely_wrong_board_is_still_caught(self):
        """⚠ The anchor correction must not swallow real errors too. Plant a
        board that is 4 points too hard ON TOP of the anchor."""
        plant(self.cur, true_diff=0.05, board_error=0.04)
        row = measure(self.cur)
        measured, board = float(row[4]), float(row[7])
        self.assertAlmostEqual(board - measured, 4.0, delta=1.0,
                               msg=f"board {board} vs measured {measured}")


    def test_adjacent_100m_cells_do_not_borrow_each_other(self):
        """⚠⚠ THE OFF-BY-ONE. Morley races 4700, 4800, 4900 and 5000 at the
        same venue -- cells 100m APART. The published lookup used a 150m
        tolerance, so every cell printed its NEIGHBOUR's difficulty: the
        4800 row showed 4700's number, 4900 showed 4800's, all the way
        down. It looked like data.

        Here each cell gets a deliberately distinct published value and the
        measured difficulty differs between them too, so a borrowed number
        is unmistakable."""
        rng = random.Random(4)
        ability = [rng.gauss(0, 0.06) for _ in range(2000)]
        mrows, rrows, drows = [], [], []
        mid = 0
        # the benchmark: other venues, so the athletes have races elsewhere
        for v in range(20):
            drows.append((f"XC:Regular {v}",
                          math.expm1(TRACK_ANCHOR), 400, 300, 5000))
            for k in range(6):
                mrows.append((mid, 1, "x", f"Regular {v}", 5000))
                for a in rng.sample(range(2000), 30):
                    rrows.append((a, f"2023-10-{1 + k % 28:02d}", mid, 1,
                                  "x", math.exp(ability[a]
                                                + rng.gauss(0, 0.03)
                                                + math.log(1200)), 100.0))
                mid += 1
        # one venue, four cells 100m apart, each with its own difficulty
        planted = {4700: 0.09, 4800: 0.01, 4900: 0.05, 5000: -0.03}
        for dist, diff in planted.items():
            drows.append((f"XC:{NAME}",
                          math.expm1(math.log1p(diff) + TRACK_ANCHOR),
                          300, 300, dist))
            for k in range(6):
                mrows.append((mid, 1, "x", NAME, dist))
                for a in rng.sample(range(2000), 30):
                    rrows.append((a, f"2023-11-{1 + k % 28:02d}", mid, 1,
                                  "x", math.exp(ability[a] + diff
                                                + rng.gauss(0, 0.03)
                                                + math.log(1200)), 100.0))
                mid += 1
        self.cur.executemany("INSERT INTO meets VALUES (%s,%s,%s,%s,%s)",
                             mrows)
        self.cur.executemany("INSERT INTO results VALUES "
                             "(%s,%s,%s,%s,%s,%s,%s)", rrows)
        self.cur.executemany("INSERT INTO course_difficulties VALUES "
                             "(%s,%s,%s,%s,%s)", drows)

        import venue_check as vc
        self.cur.execute(f"DROP TABLE IF EXISTS {vc._SCRATCH}")
        self.cur.execute(vc._PASS_A, {"pats": [f"%{NAME}%"], "cut": 10000})
        self.cur.execute(vc._MEASURE, {"min_rows": 20})
        rows = {int(r[1]): r for r in self.cur.fetchall()}
        for dist, diff in planted.items():
            self.assertIn(dist, rows, f"cell {dist} vanished")
            measured = float(rows[dist][4])
            self.assertAlmostEqual(measured, 100 * diff, delta=1.5,
                                   msg=f"{dist}m measured {measured}, "
                                       f"planted {100 * diff}")
            board = rows[dist][7]
            self.assertIsNotNone(board,
                                 f"{dist}m has no board_says -- the "
                                 f"published join missed the cell")
            self.assertAlmostEqual(float(board), measured, delta=2.0,
                                   msg=f"{dist}m board {board} vs measured "
                                       f"{measured} -- borrowed a neighbour?")


    def test_the_tilt_is_undone(self):
        """★★ THE THIRD SCALE. The row model applies h x difficulty, so a
        RAW residual measures mean(h) x d. Give the venue a fast field
        (rating 150 -> h = 0.43) and the raw number reads far below the
        planted difficulty; untilted_pct must recover it."""
        plant(self.cur, true_diff=0.10, rating=150.0)
        row = measure(self.cur)
        measured, tilt, untilted = (float(row[4]), float(row[5]),
                                    float(row[6]))
        self.assertAlmostEqual(tilt, 0.4325, delta=0.02, msg=f"tilt {tilt}")
        self.assertLess(measured, 6.0,
                        f"a fast field should read LOW: {measured}")
        self.assertAlmostEqual(untilted, 10.0, delta=1.5,
                               msg=f"untilted {untilted}")

    def test_a_neutral_field_leaves_the_tilt_at_one(self):
        plant(self.cur, true_diff=0.08, rating=100.0)
        row = measure(self.cur)
        self.assertAlmostEqual(float(row[5]), 1.0, delta=0.02)
        self.assertAlmostEqual(float(row[6]), float(row[4]), delta=0.3)


if __name__ == "__main__":
    unittest.main()
