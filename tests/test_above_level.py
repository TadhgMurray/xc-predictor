"""Ran above their level (above_level.py, 2026-10-05): the level is the
season statistic (80th percentile, outlier rule) over the athlete's races
in the 365 days BEFORE the race, same pool (2026-10-07; it was the other
races of this season, later ones included); the same race from the other feed is
not another race; the gap is a percent; above = beyond the measured swing."""
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import above_level as AL                                       # noqa: E402


class _Cur:
    # the query is a lateral over the runner ids; the fake answers it whole
    def __init__(self, season):
        self.season, self.rows, self.args = season, [], None

    def execute(self, sql, p=None):
        if "SAVEPOINT" in sql:
            return
        self.args = p
        self.rows = self.season

    def fetchall(self):
        return self.rows


def _s(pid, rating, day, dist=5000.0, pool="hs_m"):
    return {"person_id": pid, "pool": pool, "speed_rating": rating, "day": day, "distance": dist}


class AboveLevel(unittest.TestCase):
    def test_level_leaves_this_race_out(self):
        season = [_s(1, 100, "2025-09-06"), _s(1, 100, "2025-09-13"),
                  _s(1, 100, "2025-09-20"), _s(1, 110, "2025-10-04"),   # this race
                  _s(1, 110, "2025-10-04")]                             # its twin, other feed
        rows = [{"person_id": 1, "speed_rating": 110, "rating_pool": "hs_m", "result_id": 9}]
        out = AL.stampAboveLevel(_Cur(season), "XC", rows, "2025-10-04", 5000)
        self.assertAlmostEqual(rows[0]["level"], 100.0)
        self.assertAlmostEqual(rows[0]["vs_level"], 10.0)
        self.assertEqual(out["n"], 1)

    def test_other_pool_is_not_their_level(self):
        season = [_s(1, 60, "2025-09-06", pool="college_m"), _s(1, 100, "2025-10-04")]
        rows = [{"person_id": 1, "speed_rating": 100, "rating_pool": "hs_m"}]
        self.assertIsNone(AL.stampAboveLevel(_Cur(season), "XC", rows, "2025-10-04", 5000))
        self.assertNotIn("vs_level", rows[0])

    def test_surprise_is_beyond_the_measured_swing(self):
        season = []
        rows = []
        # ten steady runners (swing ~1%), one who jumps 8% today
        for pid in range(1, 11):
            for d, r in (("2025-09-06", 99), ("2025-09-13", 101), ("2025-09-20", 100)):
                season.append(_s(pid, r, d))
            season.append(_s(pid, 100, "2025-10-04"))
            rows.append({"person_id": pid, "speed_rating": 100, "rating_pool": "hs_m",
                         "result_id": pid})
        season[-1]["speed_rating"] = 108
        rows[-1]["speed_rating"] = 108
        out = AL.stampAboveLevel(_Cur(season), "XC", rows, "2025-10-04", 5000)
        self.assertEqual([r["person_id"] for r in out["surprises"]], [10])
        self.assertTrue(rows[-1]["above_level"])
        self.assertNotIn("above_level", rows[0])
        self.assertLess(out["sigma"], 8.0)

    def test_season_quantile_matches_postgres(self):
        self.assertAlmostEqual(AL._quantile([1, 2, 3, 4, 5], 0.8), 4.2)

    def test_outlier_rule(self):
        self.assertEqual(AL._seasonRows([100, 101, 102, 60]), [100, 101, 102])

    def test_no_query_failure_reaches_the_page(self):
        class Boom(_Cur):
            def execute(self, sql, p=None):
                if "SELECT" in sql:
                    raise RuntimeError("no table")

            class connection:
                @staticmethod
                def rollback():
                    pass
        rows = [{"person_id": 1, "speed_rating": 100, "rating_pool": "hs_m"}]
        self.assertIsNone(AL.stampAboveLevel(Boom([]), "XC", rows, "2025-10-04", 5000))


class TheLevelIsTheYearBeforeTheRace(unittest.TestCase):
    """★ OWNER, 2026-10-07: "should prolly be career rating before the race".
    The query asks for races strictly before the race day and no more than
    a year back -- nothing later in the season can lift the yardstick."""

    def test_the_query_window(self):
        cur = _Cur([_s(1, 100, "2025-05-01")])
        AL.stampAboveLevel(cur, "XC",
                           [{"result_id": "9", "person_id": 1, "speed_rating": 104.0,
                             "rating_pool": "hs_m"}], "2025-10-04", 5000)
        self.assertEqual(cur.args["day"], "2025-10-04")
        self.assertEqual(cur.args["back"], AL.LOOKBACK_DAYS)
        self.assertEqual(AL.LOOKBACK_DAYS, 365)
        src = open(AL.__file__, encoding="utf-8").read()
        self.assertIn("race_date <  %(day)s::date", src)
        self.assertIn("race_date >= %(day)s::date - %(back)s", src)
        self.assertNotIn("AND year = %(yr)s", src)

    def test_last_springs_races_give_an_opener_a_level(self):
        # a September opener, no earlier race this season: last spring's
        # track-season form is in the window (same sport passed by the caller)
        cur = _Cur([_s(1, 100, "2025-04-12"), _s(1, 102, "2025-05-03")])
        rows = [{"result_id": "9", "person_id": 1, "speed_rating": 108.0, "rating_pool": "hs_m"}]
        AL.stampAboveLevel(cur, "XC", rows, "2025-09-06", 5000)
        self.assertIsNotNone(rows[0].get("level"))


if __name__ == "__main__":
    unittest.main()


try:
    import flask                                               # noqa: F401
    _HAVE_FLASK = True
except ImportError:
    _HAVE_FLASK = False


@unittest.skipUnless(_HAVE_FLASK, "flask not installed")
class AboveLevelApi(unittest.TestCase):
    """The race page posts its rows after load; the answer is per result id."""
    def setUp(self):
        os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
        sys.path.insert(0, _ROOT)
        import app as A
        self.A = A
        self.saved = (A._inMaintenance, A.getConn)
        A._inMaintenance = lambda: False
        season = [_s(1, 100, "2025-09-06"), _s(1, 100, "2025-09-13"), _s(1, 110, "2025-10-04")]

        class _C(_Cur):
            def __enter__(s): return s
            def __exit__(s, *a): return False

        class _Conn:
            def __enter__(s): return s
            def __exit__(s, *a): return False
            def cursor(s, **k): return _C(season)
            def rollback(s): pass
        A.getConn = lambda *a, **k: _Conn()
        self.c = A.app.test_client()

    def tearDown(self):
        self.A._inMaintenance, self.A.getConn = self.saved

    def test_levels_by_result_id(self):
        r = self.c.post("/api/above-level", json={
            "sport": "XC", "date": "2025-10-04", "distance": 5000,
            "rows": [{"rid": "77", "pid": 1, "rating": 110, "pool": "hs_m"},
                     {"rid": "x", "pid": "bad"}]})
        d = r.get_json()
        self.assertEqual(r.status_code, 200)
        self.assertAlmostEqual(d["levels"]["77"], 10.0)

    def test_bad_sport_is_400(self):
        self.assertEqual(self.c.post("/api/above-level", json={"sport": "XX"}).status_code, 400)

    def test_empty_is_empty(self):
        d = self.c.post("/api/above-level", json={"sport": "XC", "rows": []}).get_json()
        self.assertEqual(d["levels"], {})
