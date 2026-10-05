"""Runners like you (comps.py, 2026-10-05): outcomes count only once their
season is over, the band is the season's own standard error, and the
model's spread is shown as the same middle half the comps' rows use."""
import math
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "scripts"),
           os.path.join(_ROOT, "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    import psycopg2                                            # noqa: F401
    _HAVE = True
except ImportError:
    _HAVE = False


def _comp(pid, year, rating=92.0, next_r=None, senior=None, college=None, div=None):
    return {"person_id": pid, "year": year, "grade": 10, "rating": rating, "n_races": 5,
            "next_rating": next_r, "senior_rating": senior, "college": college,
            "college_division": div, "dist": abs(rating - 92.0), "sport": "XC", "pool": "hs_m"}


class _Cur:
    def __init__(self, subject, comps, proj=None):
        self.subject, self.comps, self.proj, self.rows, self.sql = subject, comps, proj, [], []

    def execute(self, sql, p=None):
        self.sql.append((sql, p))
        if "SAVEPOINT" in sql:
            self.rows = []
        elif "FROM season_comps_meta" in sql:
            self.rows = [{"sigma": 3.3, "newest": 2026}]
        elif "min(n_races)" in sql:
            self.rows = [{"n": 3}]
        elif "ORDER BY year DESC LIMIT 1" in sql:
            self.rows = [self.subject] if self.subject else []
        elif "recruit_projection" in sql:
            self.rows = [self.proj] if self.proj else []
        else:
            self.rows = self.comps

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


SUBJ = {"person_id": 1, "sport": "XC", "pool": "hs_m", "year": 2026, "grade": 10,
        "rating": 92.0, "n_races": 5, "prev_rating": 88.0, "prev_n": 6}


@unittest.skipUnless(_HAVE, "psycopg2 not installed")
class Comps(unittest.TestCase):
    def test_only_finished_futures_count(self):
        import comps
        cur = _Cur(SUBJ, [
            _comp(2, 2020, next_r=95, senior=99, college="X", div="NCAA DI"),
            _comp(3, 2023, next_r=94, senior=None),     # senior 2025 < 2026: closed, did not run
            _comp(4, 2024, next_r=96, senior=None),     # senior 2026: not over yet
            _comp(5, 2025, next_r=None)])               # next season 2026: not over yet
        r = comps.runnersLikeYou(cur, 1, "XC")
        self.assertEqual(r["n"], 4)
        self.assertEqual(r["next_pool"], 3)              # 2020, 2023, 2024
        self.assertEqual(r["next"]["n"], 3)
        self.assertEqual(r["senior_pool"], 2)            # 2020, 2023
        self.assertEqual(r["senior"]["n"], 1)
        # college: a full year past the senior season -> 2020 (2023: senior
        # 2025, college 2026 not over) only
        self.assertEqual(r["college"]["n"], 1)
        self.assertEqual(r["college"]["ran"], 1)

    def test_band_is_the_seasons_standard_error(self):
        import comps
        cur = _Cur(SUBJ, [])
        r = comps.runnersLikeYou(cur, 1, "XC")
        self.assertAlmostEqual(r["se"], 3.3 / math.sqrt(5))
        self.assertAlmostEqual(r["gain"], 4.0)
        sql, p = [x for x in cur.sql if "c.rating BETWEEN" in x[0]][0]
        # the trajectory clause is there because the subject has a season before
        self.assertIn("c.prev_rating IS NOT NULL", sql)
        self.assertAlmostEqual(p["var_ds"], 3.3 ** 2 / 5 + 3.3 ** 2 / 6)

    def test_no_prev_no_trajectory_clause(self):
        import comps
        cur = _Cur({**SUBJ, "prev_rating": None, "prev_n": None}, [])
        comps.runnersLikeYou(cur, 1, "XC")
        sql = [x[0] for x in cur.sql if "c.rating BETWEEN" in x[0]][0]
        self.assertNotIn("prev_rating IS NOT NULL", sql)

    def test_model_spread_as_middle_half(self):
        import comps
        cur = _Cur(SUBJ, [], proj={"proj_rating": 100.0, "proj_sigma_pct": 4.0,
                                   "horizon_weeks": 52})
        m = comps.runnersLikeYou(cur, 1, "XC")["model"]
        self.assertAlmostEqual(m["p75"] - m["p25"], 2 * 0.6745 * 4.0, places=3)

    def test_no_season_is_none(self):
        import comps
        self.assertIsNone(comps.runnersLikeYou(_Cur(None, []), 1, "XC"))

    def test_percentile(self):
        import comps
        s = comps._spread([1, 2, 3, 4, None])
        self.assertEqual((s["n"], s["median"], s["p25"], s["p75"]), (4, 2.5, 1.75, 3.25))


if __name__ == "__main__":
    unittest.main()
