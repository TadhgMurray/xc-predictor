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


def test_a_projected_round_is_ordered_by_predicted_place_not_clock_time():
    """2026-11-16: converted times put Illinois College ahead of Wartburg."""
    import upcoming_preview as U
    pred = {"available": True, "runners": [
        {"person_id": 1, "school": "Wartburg", "seconds": 70.2, "place": 1, "rating": 118.4},
        {"person_id": 2, "school": "Illinois College", "seconds": 69.1, "place": 9, "rating": 102.4},
        {"person_id": 3, "school": "Wartburg", "seconds": 71.1, "place": 2, "rating": 116.7},
        {"person_id": 4, "school": "Nobody", "seconds": None, "place": 3}]}
    rows = U._rowsOf(pred)
    assert [r[0] for r in rows] == [1, 3, 2]                  # place order
    assert [r[2] for r in rows] == [69.1, 70.2, 71.1]         # the same times, in that order
    unplaced = {"available": True, "runners": [{"person_id": 5, "school": "X", "seconds": 900.0}]}
    assert U._rowsOf(unplaced) == [(5, "X", 900.0, None)]


def test_a_college_is_not_merged_with_the_university_it_is_named_after():
    """2026-11-16: Illinois's, Penn's and Colorado's Division I runners were
    added to Illinois College, Penn College and Colorado College."""
    import upcoming_preview as U
    teams = [{"school": "Illinois College", "state": "IL", "runners": [{"person_id": 1}]},
             {"school": "Wesleyan", "state": "CT", "runners": [{"person_id": 2}]}]
    cands = [(10, "Illinois", "IL", 130.0, "college_m"),          # the Division I university
             (11, "Wesleyan (Conn.)", "CT", 120.0, "college_m"),  # the same school, other feed
             (12, "Illinois College", "IL", 100.0, "college_m")]  # already its own spelling
    got = U.spellingExtras(teams, cands)
    assert got == [(11, "Wesleyan", 120.0, "college_m")]
