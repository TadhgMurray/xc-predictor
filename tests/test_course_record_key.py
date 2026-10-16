"""Course records are kept per course and distance, not per meet (owner,
2026-10-16: a starred CR on nearly every race of a De La Salle senior).

    python -m pytest -q tests/test_course_record_key.py
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in ("racecast", "engine", "scripts"):
    sys.path.insert(0, os.path.join(_ROOT, _d))
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
os.environ.setdefault("XCP_DB_QUIET", "1")

import app                                                       # noqa: E402


def _r(rid, date, meet, course, event, t):
    return {"result_id": rid, "date": date, "meet": meet, "course": course,
            "event": event, "time_raw": t}


RACES = [
    _r(1, "2023-09-16", "De La Salle Nike Invitational", "Newhall Community Park", "4828m", 1029.6),
    _r(2, "2023-11-04", "EBAL Championship", "Newhall Community Park", "4828m", 993.2),
    _r(3, "2024-09-21", "De La Salle Nike Invitational", "Newhall Community Park", "4828m", 928.0),
    _r(4, "2024-11-09", "EBAL Championships", "Newhall Community Park", "4828m", 941.5),
    _r(5, "2023-10-13", "Roughrider Invitational", "Woodward Park", "5000m", 1079.2),
    _r(6, "2023-11-25", "2023 CIF State", "Woodward Park", "5000m", 999.0),
    _r(7, "2024-11-30", "2024 CIF State", "Woodward Park", "5000m", 939.6),
    _r(8, "2024-10-26", "76th Mt. SAC Invitational", "Mt. San Antonio College", "4828m", 940.0),
    _r(9, "2024-12-07", "Foot Locker West", "Mt. San Antonio College", "5000m", 994.0),
]


def _time(r):
    return r["time_raw"]


def test_one_star_per_course_and_distance():
    stars = app.find_current_records(RACES, app._courseKey, _time,
                                     lambda new, best: new < best)
    # Newhall: the 15:28; Woodward: the 15:39.6; Mt. SAC: one per distance
    assert stars == {3, 7, 8, 9}


def test_was_a_record_walks_the_course_not_the_meet():
    recs = app.flag_records(RACES, app._courseKey, _time,
                            lambda new, best: new < best)
    # Newhall's Nov 2024 (941.5) was slower than Sep 2024 (928.0): no record
    assert 4 not in recs and {1, 2, 3} <= recs


def test_a_row_with_no_course_keeps_its_meet():
    assert app._courseKey({"meet": "Dual", "course": None, "event": "5000m"}) == ("Dual", "5000m")
    assert app._courseKey({"meet": "A", "course": " Woodward  Park ", "event": "5000m"}) == \
        ("woodward park", "5000m")
