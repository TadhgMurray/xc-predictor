# Project: xc-predictor / tests
# File:    test_difficulty_reliability.py
# Purpose: The split-half estimator must recover a PLANTED reliability --
#          high where venues really differ, near zero where only the days do.
#
# ★ WHY THIS NEEDS PLANTED WORLDS. The output of
#   scripts/difficulty_reliability.py is a number the engine is meant to be
#   TUNED BY: "shrink every venue's deviation to r_full of what it is".
#   A shrinkage factor that is itself wrong is worse than no shrinkage, so
#   the estimator has to be shown recovering an answer that is known.
#
# ⚠ AND ONE INTUITION IT CORRECTED, recorded because it is easy to get
#   backwards. Planting real_sd = day_sd = 0.03 does NOT give reliability
#   0.5. Reliability is about the venue's MEAN over its races, and with a
#   dozen races the day noise averages down by sqrt(n) -- so it comes back
#   at 0.92 and the recovered real sd is 3.38% against a planted 3.0%.
#   Reliability is a function of EVIDENCE, which is why the by-race-count
#   bands are the part worth reading.
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
DROP TABLE IF EXISTS results, meets, rel_rows;
CREATE TABLE meets (meet_id int, div_id int, source text, course_name text);
CREATE TABLE results (person_id int, date date, meet_id int, div_id int,
                      source text, normalized_time float);
"""


def plant(cur, real_sd, day_sd, races=12, n_ven=60, per=25, noise=0.04,
          seed=11):
    """A world where each venue has a TRUE difficulty and each race day has
    its own noise. real_sd is the signal, day_sd is what must not survive."""
    rng = random.Random(seed)
    ability = [rng.gauss(0, 0.06) for _ in range(4000)]
    mrows, rrows = [], []
    mid = 0
    for v in range(n_ven):
        vd = rng.gauss(0, real_sd)
        for k in range(races):
            mrows.append((mid, 1, "x", f"V{v}"))
            day = rng.gauss(0, day_sd)
            for a in rng.sample(range(len(ability)), per):
                lnt = ability[a] + vd + day + rng.gauss(0, noise)
                rrows.append((a, f"2020-{1 + k % 12:02d}-15", mid, 1, "x",
                              math.exp(lnt + math.log(1200))))
            mid += 1
    cur.executemany("INSERT INTO meets VALUES (%s,%s,%s,%s)", mrows)
    cur.executemany("INSERT INTO results VALUES (%s,%s,%s,%s,%s,%s)", rrows)


def measure(cur, half_races=2, half_rows=30):
    """Run the script's own SQL and return (reliability, real_sd, rows)."""
    import difficulty_reliability as dr
    sql, why = dr._passA(cur, "XC")
    assert sql, why
    cur.execute(f"DROP TABLE IF EXISTS {dr._SCRATCH}")
    cur.execute(f"CREATE UNLOGGED TABLE {dr._SCRATCH} AS {sql}",
                {"cut": 10000})
    cur.execute(dr._SPLIT, {"half_races": half_races, "half_rows": half_rows})
    rows = cur.fetchall()
    d0 = [float(r[3]) for r in rows]
    d1 = [float(r[4]) for r in rows]
    r = dr._pearson(d0, d1)
    r_full = max(0.0, min(1.0, 2 * r / (1 + r))) if r is not None else 0.0
    obs = [(a + b) / 2 for a, b in zip(d0, d1)]
    return r_full, dr._sd(obs) * math.sqrt(r_full), rows


@unittest.skipUnless(HOST and PORT and psycopg2,
                     "set XCP_TEST_PGHOST/XCP_TEST_PGPORT to a scratch cluster")
class Reliability(unittest.TestCase):
    def setUp(self):
        self.conn = psycopg2.connect(host=HOST, port=int(PORT),
                                     user="postgres", dbname="postgres")
        self.conn.autocommit = True
        self.cur = self.conn.cursor()
        self.cur.execute(_DDL)

    def tearDown(self):
        self.cur.close()
        self.conn.close()

    def test_real_differences_read_as_reliable(self):
        plant(self.cur, real_sd=0.05, day_sd=0.01)
        rel, real_sd, _ = measure(self.cur)
        self.assertGreater(rel, 0.9, f"reliability only {rel:.3f}")
        self.assertAlmostEqual(real_sd, 0.05, delta=0.01,
                               msg=f"recovered real sd {100*real_sd:.2f}%")

    def test_pure_day_noise_reads_as_unreliable(self):
        """⚠ THE ONE THAT MATTERS. Every venue is identical; all the spread
        on the board is which days it hosted. If this reads reliable, the
        diagnostic would licence keeping pure noise."""
        plant(self.cur, real_sd=0.0, day_sd=0.05)
        rel, real_sd, _ = measure(self.cur)
        self.assertLess(rel, 0.4, f"pure noise read as {rel:.3f} reliable")
        self.assertLess(real_sd, 0.012,
                        f"invented a real sd of {100*real_sd:.2f}%")

    def test_reliability_rises_with_races(self):
        """The band table is the part that sets per-venue shrinkage, so the
        monotonicity has to hold: a venue raced four times is not as
        trustworthy as one raced eighty times."""
        rng = random.Random(5)
        ability = [rng.gauss(0, 0.06) for _ in range(6000)]
        mrows, rrows = [], []
        mid = 0
        for races in (4, 40):
            for v in range(40):
                vd = rng.gauss(0, 0.03)
                for k in range(races):
                    mrows.append((mid, 1, "x", f"V{races}_{v}"))
                    day = rng.gauss(0, 0.05)
                    for a in rng.sample(range(len(ability)), 25):
                        rrows.append(
                            (a, f"2020-{1 + k % 12:02d}-15", mid, 1, "x",
                             math.exp(ability[a] + vd + day
                                      + rng.gauss(0, 0.04) + math.log(1200))))
                    mid += 1
        self.cur.executemany("INSERT INTO meets VALUES (%s,%s,%s,%s)", mrows)
        self.cur.executemany(
            "INSERT INTO results VALUES (%s,%s,%s,%s,%s,%s)", rrows)

        import difficulty_reliability as dr
        _, _, rows = measure(self.cur)
        thin = [r for r in rows if int(r[1]) <= 5]
        thick = [r for r in rows if int(r[1]) >= 26]
        self.assertTrue(thin and thick, "bands did not populate")

        def rel(band):
            d0 = [float(r[3]) for r in band]
            d1 = [float(r[4]) for r in band]
            r = dr._pearson(d0, d1)
            return max(0.0, min(1.0, 2 * r / (1 + r))) if r else 0.0

        self.assertLess(rel(thin), rel(thick) - 0.15,
                        f"thin {rel(thin):.3f} vs thick {rel(thick):.3f}")


if __name__ == "__main__":
    unittest.main()
