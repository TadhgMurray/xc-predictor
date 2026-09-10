# Project: xc-predictor / tests
# File:    test_tf_event_bias.py
# Purpose: The track curve-error estimator recovers a PLANTED tilt.
#
# ★ WHY IT MATTERS THAT IT IS EXACT. The output is meant to be applied --
#   "the exponent is off by k per e-fold" is a correction, not a hint -- so
#   an estimator that is 20% short would fix 80% of the problem and leave
#   the rest looking like a new one.
#
# ⚠ AND WHY THE HEADLINE IS THE SLOPE, NOT THE BAND TABLE. The per-band
#   means are deviations from each athlete's OWN season mean, so how large
#   they look depends on which events that athlete entered: on a planted
#   0.030 per e-fold, the 800-to-10000 band gap read 6.36 points where the
#   true span is 7.58. Regressing the residual on the athlete's own centred
#   ln(distance) removes the mix and lands on 0.030 exactly. Both are
#   printed; only the slope is safe to act on.
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
DROP TABLE IF EXISTS results_tf, meets_tf, tfe_rows;
CREATE TABLE meets_tf (meet_id int, div_id int, distance_meters real,
                       is_indoor int);
CREATE TABLE results_tf (person_id int, date date, meet_id int, div_id int,
                         event_short text, normalized_time float,
                         rating_pool text);
"""

# ! 'Mile' AND '10,000m' ARE IN HERE ON PURPOSE. The metres-from-event-name
#   rule has to handle the word mile and an embedded comma; a fixture of
#   tidy '5000m' strings would not exercise either.
_EVENTS = [(800, "800m"), (1609.34, "Mile"), (3200, "3200m"),
           (5000, "5000m"), (10000, "10,000m")]


def plant(cur, tilt, n_ath=2000, per=3, noise=0.01, seed=9):
    """lnt = fitness + tilt * (ln d - ln 3000) + noise.

    tilt > 0 means long events normalise SLOW, i.e. the curve does not
    charge enough for distance and short events rate too fast.
    """
    rng = random.Random(seed)
    cur.executemany("INSERT INTO meets_tf VALUES (%s,%s,%s,%s)",
                    [(i, 1, d, 0) for i, (d, _) in enumerate(_EVENTS)])
    rows = []
    for p in range(1, n_ath + 1):
        fit = rng.gauss(math.log(1000), 0.08)
        for d, name in rng.sample(_EVENTS, per):
            j = next(k for k, (dd, _) in enumerate(_EVENTS) if dd == d)
            lnt = (fit + tilt * (math.log(d) - math.log(3000))
                   + rng.gauss(0, noise))
            rows.append((p, "2024-04-15", j, 1, name, math.exp(lnt),
                         "college_m"))
    cur.executemany("INSERT INTO results_tf VALUES "
                    "(%s,%s,%s,%s,%s,%s,%s)", rows)


def slope(cur):
    import tf_event_bias as tb
    cur.execute(f"DROP TABLE IF EXISTS {tb._SCRATCH}")
    cur.execute(f"CREATE UNLOGGED TABLE {tb._SCRATCH} AS "
                + tb._passA(cur, None), {"cut": 10000, "pool": None})
    cur.execute(tb._SLOPE.format(grp="'all'", where="WHERE indoor = 0"),
                {"min_n": 10})
    rows = cur.fetchall()
    assert rows, "no slope row"
    return float(rows[0][2])


@unittest.skipUnless(HOST and PORT and psycopg2,
                     "set XCP_TEST_PGHOST/XCP_TEST_PGPORT to a scratch cluster")
class EventBias(unittest.TestCase):
    def setUp(self):
        self.conn = psycopg2.connect(host=HOST, port=int(PORT),
                                     user="postgres", dbname="postgres")
        self.conn.autocommit = True
        self.cur = self.conn.cursor()
        self.cur.execute(_DDL)

    def tearDown(self):
        self.cur.close()
        self.conn.close()

    def test_a_correct_curve_reads_flat(self):
        plant(self.cur, tilt=0.0)
        self.assertLess(abs(slope(self.cur)), 0.002)

    def test_short_events_overrated(self):
        plant(self.cur, tilt=0.03)
        self.assertAlmostEqual(slope(self.cur), 0.03, delta=0.003)

    def test_long_events_overrated(self):
        plant(self.cur, tilt=-0.02)
        self.assertAlmostEqual(slope(self.cur), -0.02, delta=0.003)

    def test_the_mile_and_the_comma_parse(self):
        """⚠ The metres rule is regex over an event NAME. If 'Mile' or
        '10,000m' failed to parse, those rows would silently vanish and the
        estimator would still return a confident number off the rest."""
        plant(self.cur, tilt=0.03)
        import tf_event_bias as tb
        self.cur.execute(f"DROP TABLE IF EXISTS {tb._SCRATCH}")
        self.cur.execute(f"CREATE UNLOGGED TABLE {tb._SCRATCH} AS "
                         + tb._passA(self.cur, None),
                         {"cut": 10000, "pool": None})
        self.cur.execute(f"SELECT count(DISTINCT round(dist::numeric)) "
                         f"FROM {tb._SCRATCH}")
        self.assertEqual(self.cur.fetchone()[0], len(_EVENTS),
                         "an event name did not survive the metres rule")


if __name__ == "__main__":
    unittest.main()
