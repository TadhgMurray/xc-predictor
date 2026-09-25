"""Who is on a squad when a past race is run this year (owner, 2026-09-25):
freshmen who have raced for the school are added from the results, and a
runner who has not raced yet this season shows last season's grade, a year
on. No database: a fake cursor answers the one query.

    python -m pytest -q tests/test_predict_squads.py
"""
import os
import sys

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "model")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import predict as P                                          # noqa: E402
from grade_label import advanceGrade                         # noqa: E402


def test_grades_advance_on_their_own_level():
    assert advanceGrade("10", 1, "hs_m") == "11"
    assert advanceGrade("So", 1, "hs_f") == "11"
    assert advanceGrade("8", 1, "ms_m") == "9"
    assert advanceGrade("JR-3", 1, "college_m") == "SR-4"
    assert advanceGrade("SR-4", 1, "college_m") == "SR-5"
    assert advanceGrade("FR-1", 2, "college_f") == "JR-3"
    assert advanceGrade("12", 1, "hs_m") is None          # graduated
    assert advanceGrade("11", 0, "hs_m") == "11"
    assert advanceGrade(None, 1) is None


class _Cur:
    def __init__(self, rows):
        self.rows, self.sql, self.params = rows, None, None

    def execute(self, sql, params=None):
        self.sql, self.params = sql, params

    def fetchall(self):
        return self.rows


ROWS = [
    {"school": "Campolindo", "person_id": 1, "grade": "9", "pool": "hs_m",
     "rating": 118.44, "n_races": 3, "gender": "M", "name": "New Frosh"},
    {"school": "Campolindo", "person_id": 2, "grade": "9", "pool": None,
     "rating": None, "n_races": 1, "gender": "M", "name": "Unrated Frosh"},
    {"school": "Campolindo", "person_id": 3, "grade": "10", "pool": "hs_f",
     "rating": 120.0, "n_races": 4, "gender": "F", "name": "A Girl"},
    {"school": "Campolindo", "person_id": 4, "grade": "8", "pool": "ms_m",
     "rating": 125.0, "n_races": 2, "gender": "M", "name": "Middle Schooler"},
]


def test_race_entrants_come_off_the_results(monkeypatch):
    import pool_view
    monkeypatch.setattr(pool_view, "stampBoardRows", lambda rows, **k: None)
    cur = _Cur(ROWS)
    got = P._raceEntrants(cur, ["Campolindo"], "XC", 2026, gender="M",
                          levels={"hs"})
    ids = [e["person_id"] for e in got["Campolindo"]]
    assert ids == [1, 2]                    # the girl and the ms boy filtered
    assert got["Campolindo"][0]["rating"] == 118.4
    assert got["Campolindo"][1]["rating"] is None
    assert cur.params["lo"] == "2026-08-01" and cur.params["hi"] == "2027-08-01"
    assert "FROM   results r" in cur.sql and "< 19999" in cur.sql


def test_track_entrants_skip_field_and_relays(monkeypatch):
    import pool_view
    monkeypatch.setattr(pool_view, "stampBoardRows", lambda rows, **k: None)
    cur = _Cur([])
    P._raceEntrants(cur, ["Campolindo"], "TF", 2025)
    assert "results_tf" in cur.sql and "is_relay" in cur.sql and "is_field" in cur.sql


def test_the_level_of_a_grade():
    assert P._levelOfGrade("9") == "hs" and P._levelOfGrade("7") == "ms"
    assert P._levelOfGrade("SO-2") == "college" and P._levelOfGrade("") is None


def test_an_unnamed_person_takes_the_name_on_their_own_rows():
    """! "all freshman are unknown" (owner, 2026-09-25): a tfrrs-only person
    has no athletes row; their result rows carry the name."""
    class Cur:
        def execute(self, sql, params=None):
            self.tf = "results_tf" in sql

        def fetchall(self):
            return ([] if self.tf else
                    [{"person_id": 9, "name": "Jane Frosh", "school": "Tufts"}])
    rows = [{"person_id": 9, "name": "Unknown", "school": None},
            {"person_id": 3, "name": "Named Person", "school": "Tufts"}]
    P._fillNames(Cur(), rows)
    assert rows[0]["name"] == "Jane Frosh" and rows[0]["school"] == "Tufts"
    assert rows[1]["name"] == "Named Person"


def test_the_same_name_is_not_added_twice(monkeypatch):
    """An unlinked runner is two person_ids; the squad found one, the
    results supplement must not add the other."""
    now = 2026
    import season_year
    monkeypatch.setattr(season_year, "academicYear", lambda d: now)
    monkeypatch.setattr(P, "_squadsForYear", lambda *a, **k: {
        "Tufts": [{"person_id": 1, "name": "Meba Henok", "rating": 110.0,
                   "pool": "college_m"}]})
    import roster
    monkeypatch.setattr(roster, "carryingSchools", lambda *a, **k: set())
    monkeypatch.setattr(P, "_fillNames", lambda cur, rows: None)
    monkeypatch.setattr(P, "_raceEntrants", lambda *a, **k: {
        "Tufts": [{"person_id": 77, "name": "Meba  HENOK", "rating": 111.0,
                   "pool": "college_m"},
                  {"person_id": 78, "name": "New Frosh", "rating": 105.0,
                   "pool": "college_m"}]})
    got = P._currentSquads(None, ["Tufts"], "XC", now)
    assert [r["person_id"] for r in got["Tufts"]] == [1, 78]


def test_as_it_ran_carries_the_grade_and_rating_they_raced_with(monkeypatch):
    """! owner, 2026-09-25: "for the as it ran grades/ratings we should use
    their grade at that race not their current grade"."""
    import pool_view
    monkeypatch.setattr(pool_view, "stampBoardRows", lambda rows, **k: None)
    monkeypatch.setattr(P, "_stampCrests", lambda *a, **k: None)
    monkeypatch.setattr(P, "_stateOf", lambda s: None)
    monkeypatch.setattr(P, "_teamStates", lambda cur, rows, *a: {})
    monkeypatch.setattr(P, "_meetState", lambda *a: None)
    rows = [{"person_id": 5, "school": "Campolindo", "grade": "9",
             "name": "Cody De la Cruz", "row_name": None,
             "mean_rating": 118.44, "pool": "hs_m", "season": 2024}]
    cur = _Cur(rows)
    field = P.meetField(cur, 1, 2, "XC", season_year=2026, when="asran")
    r = field["teams"][0]["runners"][0]
    assert r["grade"] == "9" and r["rating"] == 118.4 and r["pool"] == "hs_m"
    # the season of the RACE, off the row's own date
    assert "r.grade" in cur.sql and "x.year =" in cur.sql and "r.date" in cur.sql


def test_the_page_lineup_in_as_it_ran_mode_reads_the_race(monkeypatch):
    monkeypatch.setattr(P, "_currentSeason", lambda cur, sport: 2026)
    monkeypatch.setattr(P, "_athleteEntries", lambda *a, **k: [
        {"person_id": 5, "name": "Cody De la Cruz", "grade": "11",
         "rating": 131.0, "pool": "hs_m"}])
    monkeypatch.setattr(P, "_exactField", lambda *a, **k: [
        {"person_id": 5, "name": "Cody De la Cruz", "grade": "9",
         "rating": 118.4, "pool": "hs_m", "hs_rating": 118.4}])
    monkeypatch.setattr(P, "_stateOf", lambda s: None)
    target = {"mode": "rerun_exact", "sport": "XC", "meet_id": 1,
              "div_id": "2", "field": [["Campolindo", ["5"], 7]]}
    got = P._teamRosters(None, None, target)
    assert got[0]["grade"] == "9" and got[0]["rating"] == 118.4
    target["mode"] = "rerun"                          # this year: current
    got = P._teamRosters(None, None, target)
    assert got[0]["grade"] == "11" and got[0]["rating"] == 131.0


def test_a_team_takes_its_runners_state_not_the_names(monkeypatch):
    """! owner, 2026-09-25: "the predict takes the wrong antioch while the
    athletes in it have the correct antioch (CA)"."""
    import meet_compile
    def stamp(cur, rows):
        for r in rows:
            if r["person_id"] in (1, 2, 3):
                r["school_state"] = "CA"
            elif r["person_id"] == 4:
                r["school_state"] = "IL"
    monkeypatch.setattr(meet_compile, "stampSchoolStates", stamp)
    rows = [{"school": "Antioch", "person_id": i} for i in (1, 2, 3, 4, 5)]
    assert P._teamStates(None, rows) == {"Antioch": "CA"}
    monkeypatch.setattr(P, "_stateOf", lambda s: "IL")
    monkeypatch.setattr(P, "_teamStates", lambda cur, rows, *a: {"Antioch": "CA"})
    target = {"mode": "rerun", "sport": "XC",
              "field": [["Antioch", ["1", "2"], 7]]}
    monkeypatch.setattr(P, "_currentSeason", lambda cur, sport: 2026)
    monkeypatch.setattr(P, "_athleteEntries", lambda *a, **k: [])
    got = P._teamRosters(None, None, target)
    assert {e["school_state"] for e in got} == {"CA"}


def test_with_no_runner_resolved_the_meets_state_decides(monkeypatch):
    """! "Still antioch (IL) and Washington (WA)": the race page's CA is the
    meet's state (row.school_state or header.state), not a per-athlete one."""
    import meet_compile
    import school_identity
    monkeypatch.setattr(meet_compile, "stampSchoolStates", lambda cur, rows: None)
    monkeypatch.setattr(school_identity, "contextState",
                        lambda sch, st, trusted=False:
                        st if (sch, st) in {("Antioch", "CA"),
                                            ("Washington", "CA")} else "ZZ")
    rows = [{"school": "Antioch", "person_id": 1},
            {"school": "Washington", "person_id": 2}]
    assert P._teamStates(None, rows, "CA") == {"Antioch": "CA",
                                               "Washington": "CA"}
    assert P._teamStates(None, rows) == {}
