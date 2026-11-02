"""scripts/field_audit.py: the finishers we missed, split by why. No
database.

    python -m pytest -q tests/test_field_audit.py
"""
import os
import sys

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "model")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import field_audit as F                                      # noqa: E402


def test_each_missed_finisher_gets_one_cause():
    fc = {"runners": [{"person_id": 1, "school": "A"}, {"person_id": 2, "school": "A"},
                      {"person_id": 3, "school": "B"}, {"person_id": 9, "school": "B"}]}
    fin = [{"person_id": 1, "school": "A"}, {"person_id": 2, "school": "a "},
           {"person_id": 4, "school": "A"},                 # A ran three, we had two
           {"person_id": 5, "school": "B"},                 # B ran two, we had two: 5 not 9
           {"person_id": 3, "school": "B"},
           {"person_id": 6, "school": "C"},                 # C was not in our field
           {"person_id": 7, "school": None}]
    got, prec = F.auditRace(fc, fin)
    assert got == {"listed": 3, "school not in our field": 1, "team ran more than we had": 1,
                   "the wrong runners": 1, "no school": 1}
    assert prec == {"listed_n": 4, "listed_ran": 3}


def test_field_rules_per_race():
    import upcoming_preview as U
    plan = {"races": [
        {"div_id": 1, "label": "Varsity Boys 5K", "gender": "M",
         "schools": {("A", "NY"): 7, ("B", "NY"): 3}},
        {"div_id": 2, "label": "JV Boys 5K", "gender": "M",
         "schools": {("A", "NY"): 12, ("C", "NY"): 4}},
        {"div_id": 3, "label": "Varsity Girls 5K", "gender": "F", "schools": {("A", "NY"): 7}},
        {"div_id": 4, "label": "Middle School Boys", "gender": "M", "schools": {("D", "NY"): 9}}]}
    assert U.fieldRules(plan, 1) == {"cap_rule": "brought", "varsity": True, "skip_by_school": {}}
    jv = U.fieldRules(plan, 2)
    assert jv["varsity"] is False and jv["skip_by_school"] == {"A": 7, "B": 3}
    assert U.fieldRules(plan, 4)["skip_by_school"] == {}          # another level, not a JV
    assert U.fieldRules(plan, 9) == {} and U.fieldRules(None, 1) == {}


def test_as_many_as_they_brought():
    import predict as P
    assert P.broughtCap(0) == P.MAX_PER_TEAM
    assert P.broughtCap(3) == 3 and P.broughtCap(6) == 7 and P.broughtCap(10) == 10
    assert P.broughtCap(15, varsity=False) == 15 and P.broughtCap(2, varsity=False) == 2


def test_grade_races():
    import upcoming_preview as U
    assert U.raceGrades("Freshman Boys 2 Mile") == (9,)
    assert U.raceGrades("Sophomore Girls") == (10,)
    assert U.raceGrades("Frosh/Soph Boys") == (9, 10) and U.raceGrades("F/S Girls") == (9, 10)
    assert U.raceGrades("Fr-So Boys") == (9, 10)
    assert U.raceGrades("7th/8th Grade Boys") == (7, 8) and U.raceGrades("6th grade") == (6,)
    assert U.raceGrades("JV Boys") == () and U.raceGrades("Varsity 5K") == ()
    plan = {"races": [
        {"div_id": 1, "label": "Varsity Boys", "gender": "M", "schools": {("A", ""): 7}},
        {"div_id": 2, "label": "Freshman Boys", "gender": "M", "schools": {("A", ""): 9}},
        {"div_id": 3, "label": "8th Grade Boys", "gender": "M", "schools": {("A MS", ""): 9}}]}
    fr = U.fieldRules(plan, 2)
    assert fr["grades"] == [9] and fr["skip_by_school"] == {"A": 7}
    ms = U.fieldRules(plan, 3)
    assert ms["grades"] == [8] and ms["skip_by_school"] == {}     # not the varsity's leftovers


def test_a_middle_school_race_by_its_initials():
    import upcoming_preview as U
    assert U._OTHER_LEVEL.search("MS Boys 2 Mile") and not U._OTHER_LEVEL.search("Varsity Boys")
