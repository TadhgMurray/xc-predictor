"""A shared school name never lends one level's identity to another
(owner, 2026-10-05: a grade-5 runner from Amherst, Wisconsin read
"NCAA DIII · NESCAC", labelled "Amherst (MA)", and joined Amherst
College's roster)."""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for sub in ("racecast", "scripts", "engine"):
    sys.path.insert(0, os.path.join(_ROOT, sub))
os.environ.setdefault("XCP_DB_PASSWORD", "x")

import pytest  # noqa: E402

B = pytest.importorskip("build_ranking_results")
import school_identity as SI  # noqa: E402

N = len(B._UNIT_COLS)
COLLEGE = ("NCAA DIII", "New England", "NESCAC") + (None,) * (N - 3)
MA_HS = (None, None, None, "Suburban League") + (None,) * (N - 4)
NE_HS = (None, None, None, "Fort Kearney") + (None,) * (N - 4)
LONELY = (None, None, None, "Solo League") + (None,) * (N - 4)


@pytest.fixture
def units(monkeypatch):
    monkeypatch.setitem(B._UNITS, "by_key", {
        ("Amherst", "MA", True): COLLEGE, ("Amherst", "MA", False): MA_HS,
        ("Amherst", "NE", False): NE_HS, ("Lonely HS", "OR", False): LONELY})
    monkeypatch.setitem(B._UNITS, "by_school", {
        ("Amherst", True): COLLEGE, ("Amherst", False): MA_HS,
        ("Lonely HS", False): LONELY})
    monkeypatch.setitem(B._UNITS, "hs_unique", {"Lonely HS"})
    monkeypatch.setattr(B, "_campusState", lambda s: "MA" if s == "Amherst" else None)


def test_a_school_row_never_takes_the_college(units):
    assert B._unitsOf("Amherst", "WI", "elem_m") == (None,) * N     # no WI unit, name shared
    assert B._unitsOf("Amherst", "MA", "hs_f") == MA_HS             # the high school, not the college
    assert B._unitsOf("Amherst", "NE", "hs_m") == NE_HS


def test_a_college_row_takes_the_campus(units):
    assert B._unitsOf("Amherst", "CT", "college_m") == COLLEGE      # raced away, campus in MA
    assert B._unitsOf("Amherst", "MA", "college_f|XC") == COLLEGE


def test_a_unique_school_name_is_its_own_out_of_state(units):
    assert B._unitsOf("Lonely HS", "WA", "hs_m") == LONELY


def test_a_school_season_keeps_a_small_clusters_state(monkeypatch):
    monkeypatch.setitem(SI._LABELS, "clusters", {"Amherst": {"MA": 0.9, "WI": 0.02}})
    monkeypatch.setitem(SI._LABELS, "map", {"Amherst": "MA"})
    monkeypatch.setattr(SI, "_collegeState", lambda s: "MA")
    assert SI.teamState("Amherst", "elem_m", "WI") == "WI"
    assert SI.teamState("Amherst", "hs_m", "TX") == "MA"            # no school there: primary
    assert SI.teamState("Amherst", "college_m", "WI") == "MA"       # a college keeps its campus
