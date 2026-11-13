"""The team tracker scores its field's weeks once (team_tracker.
fieldStandings, 2026-11-12): every school's weekly points are the same as
the replay that scored the whole field for that school alone -- runners
who change school mid-season, small teams, ties and late starters included."""
import datetime
import os
import random
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "scripts"), os.path.dirname(os.path.abspath(__file__))):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import _env  # noqa: E402,F401

import pytest  # noqa: E402

import team_tracker as T  # noqa: E402


def _season(seed):
    rnd = random.Random(seed)
    schools = [f"S{i}" for i in range(14)]
    start = datetime.date(2025, 8, 23)
    rows = []
    for pid in range(1, 260):
        school = rnd.choice(schools)
        first = rnd.randint(0, 40)
        for k in range(rnd.randint(1, 8)):
            day = start + datetime.timedelta(days=first + 7 * k + rnd.randint(0, 2))
            if k == 3 and rnd.random() < 0.05:
                school = rnd.choice(schools)            # moved mid-season
            rows.append({"person_id": pid, "school": school, "grade": rnd.choice(["9", "10", "11", "12"]),
                         "day": day.isoformat(), "rating": round(rnd.uniform(110, 170), 0),
                         "meet_id": 100 + (first + 7 * k) // 7})
    return rows


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_shared_standings_give_every_school_the_same_points(seed):
    rows = _season(seed)
    days = [r["day"] for r in rows]
    standings = T.fieldStandings(rows, T.weekEnds(min(days), max(days)))
    checked = 0
    for school in sorted({r["school"] for r in rows}):
        mine = [r for r in rows if r["school"] == school]
        weeks = T.weekEnds(min(r["day"] for r in mine), max(days))
        assert T.replay(rows, school, weeks, standings) == T.replay(rows, school, weeks)
        checked += 1
    assert checked >= 10
