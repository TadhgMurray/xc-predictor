"""Rule 5f: a lone season on a high school team, in high school fields,
pools high school whatever the grade below 9 says (owner, 2026-10-14,
/athlete/29642348: a freshman athletic.net lists in grade 8).

    python -m pytest -q tests/test_team_level_lone_season.py
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in ("racecast", "engine", "scripts"):
    sys.path.insert(0, os.path.join(_ROOT, _d))
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
os.environ.setdefault("XCP_DB_QUIET", "1")

from grade_sanity import teamLevelCandidates                     # noqa: E402
from pool_resolve import resolvePool                              # noqa: E402


def _v(grade, method="corroborated"):
    return {"grade": grade, "level": None, "method": method}


def test_a_lone_grade_8_season_is_a_candidate():
    acad = {(1, 2026): _v("8")}
    assert teamLevelCandidates(acad, {1: 1}) == [(1, 2026)]


def test_a_grade_with_other_seasons_is_left_alone():
    # Leiferman: 6, 7, 8, 9 across four years confirm themselves
    acad = {(2, 2022): _v("6"), (2, 2023): _v("7"),
            (2, 2024): _v("8"), (2, 2025): _v("9")}
    assert teamLevelCandidates(acad, {2: 4}) == []


def test_high_school_grades_pins_and_level_verdicts_are_not_candidates():
    acad = {(3, 2026): _v("9"), (4, 2026): _v("8", method="pinned"),
            (5, 2026): {"grade": None, "level": "ms", "method": "field"},
            (6, 2026): _v("SO")}
    assert teamLevelCandidates(acad, {3: 1, 4: 1, 5: 1, 6: 1}) == []


def test_the_level_verdict_pools_high_school_over_the_raw_grade_8():
    # what every caller passes for a grade_fix row: the raw grade untrusted,
    # no fixed grade, the verdict's level
    pool = resolvePool("8", "M", "anet", "Some High School", "XC",
                       grade_untrusted=True, fixed_level="hs",
                       grade_verdict="team_level", season=2026)
    assert pool == "hs_m|XC"
