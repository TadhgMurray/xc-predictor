"""A college class word held three years is not a professional (owner,
2026-10-18, Ryan Wilson's fifth MIT season at DIII nationals).

    python -m pytest -q tests/test_stale_class.py
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in ("engine", "scripts"):
    sys.path.insert(0, os.path.join(_ROOT, _d))
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
os.environ.setdefault("XCP_DB_QUIET", "1")

import grade_sanity as G                                         # noqa: E402


def test_a_stale_class_word_is_relevelled_by_its_races():
    for g in ("SR", "SR-4", "Sr", "JR"):
        v = G.staleVerdict(g)
        assert v["method"] == "stale_class" and v["method"] in G._PAST_SCHOOL
    # a school grade held four years is still the unmaintained record
    assert G.staleVerdict("12")["method"] == "stale_grade"
    assert "stale_grade" not in G._PAST_SCHOOL


def test_the_relevel_makes_a_college_field_season_college():
    key = (24413256, 2023)
    # college start lines, no earlier professional season, out of school 2019
    assert G.pastSchoolLevel(key, {key: (9, 0)}, {}, {24413256: 2019}) == "college"
    # a professional field stays professional
    assert G.pastSchoolLevel(key, {key: (0, 5)}, {}, {24413256: 2019}) == "pro"
