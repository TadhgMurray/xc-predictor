# The school page's "Next race" line keeps to the page's level (owner,
# 2026-10-28: the University of Washington's page showed Washington High
# School's meet). upcoming_preview.schoolNextRaces, level=.
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for sub in ("racecast", "engine", "scripts"):
    sys.path.insert(0, os.path.join(_ROOT, sub))

import upcoming_preview as U                                   # noqa: E402


def _stub(monkeypatch, pools):
    meet = {"meet_id": 1, "source": "anet", "name": "Westside Classic",
            "date": "2026-10-31", "venue": None}
    plan = {"edition": {"date": "2025-11-01"}}
    races = [{"div_id": 10, "label": "Girls Varsity", "gender": "F", "school_n": 7}]
    monkeypatch.setattr(U, "schoolMatches", lambda *a, **k: [(meet, plan, races)])
    monkeypatch.setattr(U, "racePrediction", lambda *a, **k: {
        "available": True, "n_field": 80,
        "runners": [{"person_id": i, "name": f"R{i}", "school": "Washington",
                     "seconds": 1300, "place": i, "rating": 100, "pool": p}
                    for i, p in enumerate(pools)],
        "teams": [{"team": "Washington", "state": "WA", "score": 193}]})
    monkeypatch.setattr(U, "conditions", lambda *a, **k: {})


def test_a_college_page_skips_the_high_schools_meet(monkeypatch):
    _stub(monkeypatch, ["hs_f", "hs_f"])
    assert U.schoolNextRaces(None, None, "Washington", "WA", level="college") == {
        "available": False, "races": []}
    # the high school's page, and a page with no level, still show it
    assert U.schoolNextRaces(None, None, "Washington", "WA", level="hs")["available"]
    assert U.schoolNextRaces(None, None, "Washington", "WA")["available"]


def test_a_college_page_keeps_its_own_meet(monkeypatch):
    _stub(monkeypatch, ["college_f", "college_f"])
    got = U.schoolNextRaces(None, None, "Washington", "WA", level="college")
    assert got["available"] and got["races"][0]["team_place"] == 1
