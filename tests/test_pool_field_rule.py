"""A season raced at college or pro fields is a college or pro season
(owner, 2026-09-06). Below college the grade still decides."""
import os
import sys

_ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(_ROOT, "engine"))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))
os.environ.setdefault("XCP_DB_PASSWORD", "x")

from pool_resolve import resolvePool                              # noqa: E402


def _pool(grade, level, **kw):
    return resolvePool(grade, "M", "anet", "Amherst", "XC", season_level=level, **kw)


def test_a_college_field_outranks_a_high_school_grade():
    assert _pool("10", "college") == "college_m|XC"
    assert _pool("SO", "college") == "college_m|XC"


def test_a_college_field_rescues_a_contradicted_season():
    assert _pool("JR-3", "college", grade_verdict="contradicted") == "college_m|XC"
    assert _pool(None, "college", grade_verdict="no_evidence") == "college_m|XC"
    # and without the field the verdict still means no pool
    assert _pool("JR-3", None, grade_verdict="contradicted") is None


def test_one_step_only_the_junior_olympic_third_grader_stays_elem():
    assert _pool("3", "college") == "elem_m|XC"
    assert _pool("8", "college") == "ms_m|XC"


def test_below_college_nothing_changes():
    assert _pool("8", "hs") == "ms_m|XC"           # Luke Morelli
    assert _pool("10", "hs") == "hs_m|XC"
    assert _pool("10", None) == "hs_m|XC"


def test_a_pro_field_is_a_pro_season():
    assert _pool("11", "pro") == "pro_m|XC"


def test_the_athlete_page_names_a_tfrrs_track_meet():
    src = open(os.path.join(_ROOT, "racecast", "app.py"), encoding="utf-8").read()
    assert "COALESCE(m.meet_name, mt.meet_name) AS meet" in src
    assert "FROM   meets_tfrrs mt" in src and "tfrrs_meet_geometry g" in src
