"""Issue 142: the career best race is chosen on the scale the page shows.

A college row with a lower own-pool rating but a higher HS-equivalent must
win over an HS row when the HS view is stamped; without the stamp the own
rating decides as before.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "racecast"))

from athlete_bests import all_time_bests, _is_better_rating  # noqa: E402


def _race(rid, sport, own, hs=None):
    r = {"result_id": rid, "sport": sport, "speed_rating": own,
         "is_field": False, "event": "5K" if sport == "XC" else "1600",
         "time_raw": 900.0, "result": "15:00"}
    if hs is not None:
        r["hs_rating"] = hs
    return r


def test_hs_equivalent_wins_when_stamped():
    hs_row = _race(1, "XC", 128.0, 128.0)          # hs pool, factor 1
    college = _race(2, "XC", 121.0, 132.0)         # college_m, factor > 1
    best = all_time_bests([hs_row, college])
    assert best["rating"]["result_id"] == 2
    assert best["XC"]["rating"]["result_id"] == 2


def test_own_rating_decides_without_the_stamp():
    a = _race(1, "XC", 128.0)
    b = _race(2, "XC", 121.0)
    best = all_time_bests([a, b])
    assert best["rating"]["result_id"] == 1


def test_unrated_never_wins():
    assert not _is_better_rating({"speed_rating": None}, None)
    assert _is_better_rating(_race(1, "TF", 100.0), None)
