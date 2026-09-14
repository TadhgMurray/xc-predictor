# Project: xc-predictor / tests
# File:    test_team_level_pool.py
# Purpose: the feeds' word on a team decides the pool where the school
#          string could not: a gradeless row on a club is professional, a
#          college team's row is a college season, a club runner with a
#          school grade keeps their grade's pool.
#
#   python -m pytest -q tests/test_team_level_pool.py
import os
import sys

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "engine"), os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pool_resolve as pr                                      # noqa: E402


def _poolfor(grade, gender, source, school, season_level=None):
    """A stand-in for normalize_distance.poolFor: the grade's level, else
    the season's, else high school."""
    import normalize_distance as nd
    lv = nd.GRADE_TO_LEVEL.get(nd.normalizeGrade(grade)) if grade is not None else None
    lv = lv or season_level or "hs"
    return f"{lv}_{'m' if gender == 'M' else 'f'}"


def test_the_slug_and_the_table_name_a_level():
    assert pr.teamLevelFromSlug("CT_college_f_Conn_College") == "college"
    assert pr.teamLevelFromSlug("tfrrs") is None and pr.teamLevelFromSlug(None) is None
    levels = {123: "club", 456: "college"}
    assert pr.teamLevelOf(123, None, levels) == "club"
    assert pr.teamLevelOf("456", None, levels) == "college"
    assert pr.teamLevelOf(0, "CT_college_f_Conn_College", levels) == "college"
    assert pr.teamLevelOf(None, None, levels) is None


def test_a_gradeless_club_row_is_professional_and_a_graded_one_is_not():
    kw = dict(gender="M", source="anet", school="Nike Swoosh TC", sport="TF",
              poolfor=_poolfor, season=2025)
    # the field rule alone would say college (the club races college fields)
    assert pr.resolvePool(None, season_level="college", **kw) == "college_m|TF"
    assert pr.resolvePool(None, season_level="college", team_level="club", **kw) == "pro_m|TF"
    # a youth club runner with a grade keeps the grade's pool
    assert pr.resolvePool("10", team_level="club", **kw) == "hs_m|TF"


def test_a_college_teams_row_is_a_college_season():
    kw = dict(gender="F", source="tfrrs", school="Conn College", sport="XC",
              poolfor=_poolfor, season=2025)
    assert pr.resolvePool(None, **kw) == "hs_f|XC"                     # no evidence: the default
    assert pr.resolvePool(None, team_level="college", **kw) == "college_f|XC"
    # an eighth grader on a college-named team keeps her grade (one step only)
    assert pr.resolvePool("8", team_level="college", **kw) == "ms_f|XC"


def test_a_club_with_professionals_has_no_middle_schoolers():
    """★ OWNER, 2026-09-14: club runners labelled ms because they are in
    their "6th" pro year. On a team with a professional in it, a grade of
    1-8 or none is professional; a high-school grade is kept; a college
    team is never touched."""
    kw = dict(gender="M", source="anet", school="Nomad Intl Elite", sport="TF",
              poolfor=_poolfor, season=2025)
    assert pr.resolvePool("6", **kw) == "ms_m|TF"                          # the old answer
    assert pr.resolvePool("6", team_has_pros=True, **kw) == "pro_m|TF"
    assert pr.resolvePool(None, team_has_pros=True, **kw) == "pro_m|TF"
    assert pr.resolvePool("11", team_has_pros=True, **kw) == "hs_m|TF"      # a youth squad's junior
    assert pr.resolvePool("6", team_has_pros=True, team_level="college", **kw) == "ms_m|TF"
    # a grade_sanity verdict of hs on the season stops the rule (the grade
    # itself still decides the pool as it always did: ms here, never pro)
    assert pr.resolvePool("6", team_has_pros=True, fixed_level="hs", **kw) != "pro_m|TF"


def test_the_club_rules_fire_only_in_a_season_raced_mostly_for_the_club(monkeypatch):
    """★ OWNER, 2026-09-14: "only if they run the majority of their races
    with their club / national team; a collegiate runner running the
    Euros would be fine". The pack gates both club rules on the
    athlete-year's majority."""
    import speed_ratings as sr
    monkeypatch.setattr(sr, "_CLUB_MAJORITY", {(1, 2025)})
    assert sr.clubSeason(1, 2025) and not sr.clubSeason(1, 2024) and not sr.clubSeason(2, 2025)
    assert not sr.clubSeason(None, 2025)
