"""app.raceDayShift: the equivalents ruler on a race page is the race as it
ran that day (owner, 2026-09-26: "the weather was bad that day")."""
import math
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")

try:
    import flask                                               # noqa: F401
    _HAVE = True
except ImportError:
    _HAVE = False


def ruler(pool, dist, target, course_difficulty=None, course=None, **_k):
    # an ordinary day here: rating r runs 1000 * 100 / r seconds
    return [(r, 1000.0 * 100 / r, 900.0 * 100 / r) for r in range(60, 171)]


@unittest.skipUnless(_HAVE, "flask not installed")
class DayShift(unittest.TestCase):
    def setUp(self):
        import app as A
        import conversions as C
        self.A, self.C = A, C
        self._saved = C.equivalenceLine
        C.equivalenceLine = ruler

    def tearDown(self):
        self.C.equivalenceLine = self._saved

    def rows(self, slower, n=8, pool="hs_m"):
        return [{"speed_rating": r, "rating_pool": pool,
                 "time_seconds": 1000.0 * 100 / r * slower}
                for r in range(120, 120 + n)]

    def test_a_slow_day_reads_slow(self):
        got = self.A.raceDayShift(self.rows(1.03), "hs_m", 4828, 0.048, "X")
        self.assertAlmostEqual(got, math.log(1.03), places=4)

    def test_an_ordinary_day_is_zero(self):
        self.assertAlmostEqual(
            self.A.raceDayShift(self.rows(1.0), "hs_m", 4828, 0.0, "X"), 0.0, 6)

    def test_too_few_rows_or_another_group_says_nothing(self):
        self.assertEqual(self.A.raceDayShift(self.rows(1.05, n=3), "hs_m",
                                             4828, 0.0, "X"), 0.0)
        self.assertEqual(self.A.raceDayShift(self.rows(1.05, pool="hs_f"),
                                             "hs_m", 4828, 0.0, "X"), 0.0)

    def test_the_api_puts_the_day_on_the_course_side_only(self):
        c = self.A.app.test_client()
        base = c.get("/api/equivalence?pool=hs_m&dist=4828&difficulty=0.048"
                     "&course=Y").get_json()["points"]
        slow = c.get("/api/equivalence?pool=hs_m&dist=4828&difficulty=0.048"
                     "&course=Y&day=0.03").get_json()["points"]
        self.assertAlmostEqual(slow[0][1] / base[0][1], math.exp(0.03), places=3)
        self.assertEqual(slow[0][2], base[0][2])


if __name__ == "__main__":
    unittest.main()
