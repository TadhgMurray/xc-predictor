"""College rungs of /goal (owner, 2026-11-03: "What if I want collegiate goals?")."""
import os
import sys

os.environ.setdefault("XCP_DB_QUIET", "1")
for k, v in (("NAME", "x"), ("USER", "x"), ("PASSWORD", "x"),
             ("HOST", "127.0.0.1"), ("PORT", "5432")):
    os.environ.setdefault(f"XCP_DB_{k}", v)
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
for sub in ("racecast", "engine", "scripts"):
    sys.path.insert(0, os.path.join(ROOT, sub))

import college_goal as CG   # noqa: E402


def _row(year, meet, div, pid, t, rating, src="tfrrs"):
    return {"year": year, "meet_id": meet, "source": src, "div_id": div,
            "person_id": pid, "time": t, "rating": rating}


def test_the_championship_race_is_the_biggest_of_its_year():
    rows = [_row(2025, 1, 1, i, 1500 + i, 150 - i) for i in range(30)]        # varsity
    rows += [_row(2025, 1, 2, 100 + i, 1600 + i, 120 - i) for i in range(10)]  # JV
    races = CG.championshipRaces(rows)
    assert len(races[2025]) == 30 and races[2025][0]["person_id"] == 0


def test_the_mark_is_the_low_tenth_of_the_top_finishers_pooled():
    rows = []
    for y in (2023, 2024, 2025, 2026):
        rows += [_row(y, y, 1, (y, i), 1500 + i, 150 - i) for i in range(20)]
    got = CG.markFrom(CG.championshipRaces(rows), 7)
    assert got["years"] == [2026, 2025, 2024] and got["n"] == 21
    assert 144 <= got["rating"] <= 145 and not got["thin"]
    one = CG.markFrom({2026: CG.championshipRaces(rows)[2026]}, 7)
    assert one["thin"]                     # seven numbers is not a percentile


def test_a_runner_in_both_copies_counts_once():
    race = [_row(2026, 1, 1, 1, 1500, 150), _row(2026, 1, 1, 1, 1500.1, 150),
            _row(2026, 1, 1, 2, 1510, 140)]
    assert CG.markFrom({2026: race}, 2)["n"] == 2


def test_the_page_offers_the_college_rungs():
    import goal
    keys = [k for k, _l in goal.GOALS]
    assert {"conf7", "allconf", "nat40"} <= set(keys)
    t = open(os.path.join(ROOT, "racecast", "templates", "goal.html"), encoding="utf-8").read()
    assert 'name="conf"' in t and 'name="cdiv"' in t and "hs(pool" in t


def test_a_college_goal_renders(monkeypatch):
    import app
    import goal
    import database

    class Cur:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def execute(self, *a, **k): pass
        def fetchone(self): return None
        def fetchall(self): return []

    class Conn:
        def cursor(self, *a, **k): return Cur()
        def rollback(self): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass

    monkeypatch.setattr(database, "getConn", lambda: Conn())
    monkeypatch.setitem(goal._CHOICES, "at", 0.0)
    monkeypatch.setattr(goal.CG, "unitChoices", lambda cur: (["NESCAC", "UAA"], ["NCAA DIII"]))
    monkeypatch.setattr(goal.CG, "collegeGoal", lambda cur, rung, g, c, d: (
        {"rating": 120.0, "n": 21, "years": [2026, 2025, 2024], "thin": False, "places": 7,
         "where": "NESCAC", "kind": "conference", "whose": "the top 7 at the NESCAC championship",
         "href": "/unit/conference/NESCAC?pool=college_m", "distance": 8000.0,
         "venue": {"course_name": "Middlebury College", "distance": 8000.0, "year": 2026,
                   "canonical_id": 7, "difficulty": 0.068}}, None))
    monkeypatch.setattr(goal, "clocks", lambda *a, **k: {"course": 1500.0, "typical": 1480.0, "t1600": 250.0, "t3200": 530.0})
    monkeypatch.setitem(app.app.jinja_env.filters, "hs", lambda v, pool, sport=None: v)
    monkeypatch.setitem(app.app.jinja_env.globals, "hs_delta", lambda d, r, p, s=None: d)
    html = app.app.test_client().get("/goal?goal=conf7&g=boys&conf=NESCAC").get_data(as_text=True)
    assert "College men&#39;s cross country" in html or "College men's cross country" in html
    assert "Top 7 at my conference meet" in html and "NESCAC" in html
    assert "2026, 2025, 2024 championships" in html
    assert "8K at Middlebury College (the 2026 NESCAC championship" in html
    assert 'value="10000"' in html and 'value="NCAA DIII"' in html


def test_the_championship_venue_is_its_course_and_distance():
    race = [{"course_name": "Middlebury College", "distance": 8000.0, "day": "2026-10-31"},
            {"course_name": "Middlebury College", "distance": 8046.0, "day": "2026-10-31"},
            {"course_name": None, "distance": 8000.0, "day": "2026-10-31"}]
    assert CG.raceVenue(race) == {"course_name": "Middlebury College", "distance": 8000.0,
                                  "day": "2026-10-31"}
