"""The Conferences board with grades ticked ranks squads of those grades
(owner, 2026-11-03: "make it work there")."""
import os
import sys

os.environ.setdefault("XCP_DB_QUIET", "1")
for k, v in (("NAME", "x"), ("USER", "x"), ("PASSWORD", "x"),
             ("HOST", "127.0.0.1"), ("PORT", "5432")):
    os.environ.setdefault(f"XCP_DB_{k}", v)
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
for sub in ("racecast", "engine", "scripts"):
    sys.path.insert(0, os.path.join(ROOT, sub))

from werkzeug.datastructures import MultiDict   # noqa: E402


def test_grade_squads_carry_their_units_and_honour_the_narrowing(monkeypatch):
    import pages3
    import teams
    seen = {}

    def fake_race(cur, f):
        seen["grade"] = f.get("grade")
        return [{"school": "A", "state": "MA", "top5_mean": 120.0,
                 "units": {"conference": "NESCAC", "division": "NCAA DIII"}},
                {"school": "B", "state": "CT", "top5_mean": 118.0,
                 "units": {"conference": "NESCAC", "division": "NCAA DII"}},
                {"school": "C", "state": "VT", "top5_mean": 110.0,
                 "units": {"conference": None, "division": "NCAA DIII"}}], 3
    monkeypatch.setattr(teams, "raceReturning", fake_race)
    args = MultiDict([("grade", "9,10")])
    rows = pages3.gradeSquads(None, args, "college_m", "XC", 2026, "conference", None,
                              {"division": "NCAA DIII"})
    assert [r["school"] for r in rows] == ["A"] and rows[0]["conference"] == "NESCAC"
    assert seen["grade"] and set(seen["grade"]) >= {"9", "10"}


def test_the_page_offers_grade_on_the_conferences_tab():
    t = open(os.path.join(ROOT, "racecast", "templates", "rankings.html"), encoding="utf-8").read()
    i = t.index('<label for="grade-input">Grade</label>')
    assert "not-units" not in t[i - 200:i]
    js = open(os.path.join(ROOT, "racecast", "static", "rankings.js"), encoding="utf-8").read()
    assert '["state", "year", "grade"]' in js
