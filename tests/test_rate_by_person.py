"""predict._rateByPerson (owner, 2026-11-16: WashU left out of the D3 nationals
projection). Runners found under a school's old name ("Washington U.") take
their own season rating, filed under the new one ("Washington (Mo.)")."""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in ("racecast", "scripts", "engine"):
    sys.path.insert(0, os.path.join(_ROOT, _p))
os.environ.setdefault("XCP_DB_PASSWORD", "unused")

import predict as P  # noqa: E402


class _Cur:
    def __init__(self, rows):
        self.rows, self.sql, self.params = rows, None, None

    def execute(self, sql, params=None):
        self.sql, self.params = sql, params

    def fetchall(self):
        ids = set(self.params["ids"])
        return [r for r in self.rows if r["person_id"] in ids]


def test_an_unrated_entrant_takes_their_own_season_rating():
    cur = _Cur([{"person_id": 7, "pool": "college_m", "mean_rating": 121.44, "n_races": 6}])
    rows = [{"person_id": 7, "school": "Washington U.", "rating": None, "pool": None},
            {"person_id": 8, "school": "Washington U.", "rating": 118.0, "pool": "college_m"}]
    P._rateByPerson(cur, rows, "XC", 2026, gender="M", levels={"college"})
    assert rows[0]["rating"] == 121.4 and rows[0]["pool"] == "college_m" and rows[0]["n_races"] == 6
    assert rows[1]["rating"] == 118.0                      # a rated row is left alone
    assert cur.params["ids"] == [7] and "athlete_season" in cur.sql
    assert "= ANY(%(levels)s)" in cur.sql and "%(gender)s" in cur.sql


def test_nothing_to_fill_asks_nothing():
    cur = _Cur([])
    P._rateByPerson(cur, [{"person_id": 1, "rating": 110.0}], "XC", 2026)
    assert cur.sql is None
