"""A college name two colleges share still labels as one of THEIR states
(owner, 2026-10-19: the college men's team board read "Georgetown (TX)").

Georgetown is DC and KY in the college directory, so the plain lookup
abstains; teamState used to fall through to the name's biggest cluster of
athletes, Georgetown High School in Texas.

    python -m pytest -q tests/test_shared_college_name.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "racecast"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import school_identity as si                                    # noqa: E402
import build_college_directory as bcd                           # noqa: E402

DIRECTORY = {
    "georgetown": [("DC", "DC"), ("KY", "KY")],
    "amherst": [("MA", "MA")],
    "oregon": [("OR", "OR")],
    "oregon state": [("OR", "OR")],
}


def _labels():
    si._LABELS["loaded"] = True
    si._LABELS["map"] = {"Georgetown": "TX", "Amherst": "WI", "Highland": "UT"}
    si._LABELS["clusters"] = {
        "Georgetown": {"TX": 0.55, "DC": 0.26, "KY": 0.04, "SC": 0.15},
        "Amherst": {"WI": 0.7, "MA": 0.3},
        "Highland": {"UT": 0.6, "CA": 0.4},
    }
    si._LABELS["college"] = DIRECTORY
    bcd._ANSWERS.clear()


def test_candidates():
    bcd._ANSWERS.clear()
    assert bcd.candidateStates(DIRECTORY, "Georgetown") == ["DC", "KY"]
    assert bcd.candidateStates(DIRECTORY, "Georgetown (Ky.)") == ["KY"]
    assert bcd.candidateStates(DIRECTORY, "Amherst") == ["MA"]
    assert bcd.candidateStates(DIRECTORY, "Highland") == []
    assert bcd.candidateStates(DIRECTORY, "Georgetown Running Club") == []


def test_shared_name_college_pool():
    _labels()
    # raced away (PA): the candidate its athletes cluster in most, not TX
    assert si.teamState("Georgetown", "college_m", "PA") == "DC"
    assert si.teamState("Georgetown", "college_f", None) == "DC"
    # Georgetown College racing at home keeps KY
    assert si.teamState("Georgetown", "college_m", "KY") == "KY"
    assert si.teamState("Georgetown", "college_m", "TX") == "DC"


def test_high_school_untouched():
    _labels()
    assert si.teamState("Georgetown", "hs_m", "TX") == "TX"
    assert si.teamState("Highland", "college_m", "CA") == "CA"


def test_known_college_still_directory():
    _labels()
    assert si.teamState("Amherst", "college_m", "WI") == "MA"
