"""app.meetWinners: each race row on a meet page names its winner and top
team (owner, 2026-09-26)."""
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


class Cur:
    def __init__(self, rows):
        self.rows, self.sql = rows, None

    def execute(self, sql, params=None):
        self.sql = sql

    def fetchall(self):
        return self.rows


def _rows():
    out = []
    # div 1: A wins; five-deep teams X and Y; X better
    order = ["X", "Y", "X", "X", "Y", "X", "Y", "X", "Y", "Y", "Z"]
    for i, sch in enumerate(order):
        out.append({"div_id": 1, "person_id": 100 + i, "time_seconds": 900 + i,
                    "school": sch, "name": f"Runner {i}"})
    out.append({"div_id": 2, "person_id": 7, "time_seconds": 1000.4,
                "school": "Q", "name": "Solo"})
    return out


@unittest.skipUnless(_HAVE, "flask not installed")
class Winners(unittest.TestCase):
    def setUp(self):
        import app as A
        self.A = A
        self.divs = [{"div_id": 1, "gender": "M"}, {"div_id": 2, "gender": "F"},
                     {"div_id": 3, "gender": "F"}]

    def test_fastest_wins_and_our_own_scoring_names_the_team(self):
        got = self.A.meetWinners(Cur(_rows()), 5, self.divs)
        self.assertEqual(got[1]["winner"]["name"], "Runner 0")
        self.assertEqual(got[1]["winner"]["time"], "15:00")
        self.assertEqual(got[1]["team"]["school"], "X")
        self.assertNotIn("team", got[2])           # one runner is no team
        self.assertNotIn(3, got)                   # no finishers, no row

    def test_published_first_place_wins_over_our_scoring(self):
        pub = {(1, "M"): [{"school": "Y (Concord)", "points": 30, "place": 1},
                          {"school": "X", "points": 40, "place": 2}]}
        got = self.A.meetWinners(Cur(_rows()), 5, self.divs, published=pub)
        self.assertEqual(got[1]["team"], {"school": "Y (Concord)", "points": 30})

    def test_non_finish_sentinel_is_not_a_winner(self):
        sql = Cur([])
        self.A.meetWinners(sql, 5, self.divs)
        self.assertIn("time_seconds < 90000", sql.sql)


if __name__ == "__main__":
    unittest.main()
