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
    # a zero names no level -- the pro repool is resolvePool's `no_team`
    assert pr.teamLevelOf(0, None, levels) is None
    assert pr.teamLevelOf(0, None, {0: "hs"}) is None


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


def test_a_club_with_professionals_has_no_schoolchildren_at_all():
    """★ OWNER, 2026-09-14: club runners labelled ms because they are in
    their "6th" pro year. ★ AND 2026-09-16, asked whether a grade 9-12 row
    on such a team should be swept in too: "DO do this, only when they run
    a majority of races at that club". The majority is the only gate and it
    is upstream (clubSeason), so by the time team_has_pros is True here the
    season is the club's and every row of it is professional."""
    kw = dict(gender="M", source="anet", school="Nomad Intl Elite", sport="TF",
              poolfor=_poolfor, season=2025)
    assert pr.resolvePool("6", **kw) == "ms_m|TF"                          # the old answer
    assert pr.resolvePool("6", team_has_pros=True, **kw) == "pro_m|TF"
    assert pr.resolvePool(None, team_has_pros=True, **kw) == "pro_m|TF"
    # ★ WHAT CHANGED: a school grade on the team no longer keeps the row
    assert pr.resolvePool("11", team_has_pros=True, **kw) == "pro_m|TF"
    assert pr.resolvePool("12", team_has_pros=True, **kw) == "pro_m|TF"
    assert pr.resolvePool("6", team_has_pros=True, fixed_level="hs", **kw) == "pro_m|TF"
    # a college is never a club
    assert pr.resolvePool("6", team_has_pros=True, team_level="college", **kw) == "ms_m|TF"
    # (an hs grade on a college team is a college season -- the older rule)
    assert pr.resolvePool("11", team_has_pros=True,
                          team_level="college", **kw) == "college_m|TF"


def test_the_club_rules_fire_only_in_a_season_raced_mostly_for_the_club(monkeypatch):
    """★ OWNER, 2026-09-14: "only if they run the majority of their races
    with their club / national team; a collegiate runner running the
    Euros would be fine". The pack gates both club rules on the
    athlete-year's majority."""
    import speed_ratings as sr
    monkeypatch.setattr(sr, "_CLUB_MAJORITY", {(1, 2025)})
    assert sr.clubSeason(1, 2025) and not sr.clubSeason(1, 2024) and not sr.clubSeason(2, 2025)
    assert not sr.clubSeason(None, 2025)


def test_two_schools_with_one_name_get_two_levels(monkeypatch):
    """★ OWNER, 2026-09-16: "Oregon(IL) and Oregon(or) are colliding
    despite hs vs college, and this happens to Williams (CA) vs (MA)".
    The school-level map is keyed on the NORMALISED NAME, so one string
    is one level for every school wearing it. The team's own id carries
    the level the name cannot."""
    import normalize_distance as nd
    monkeypatch.setattr(nd, "_GRAPH_LEVELS", {})             # no database
    monkeypatch.setattr(nd, "_SCHOOL_LEVELS", {nd.normSchoolKey("Oregon"): "college"})
    kw = dict(gender="M", source="anet", school="Oregon", sport="XC", season=2025)

    # the collision: with only the name to go on, the Illinois tenth
    # grader's gradeless row is pooled against college runners
    assert pr.resolvePool(None, **kw) == "college_m|XC"
    # anet's own level for HIS team says otherwise, and wins
    assert pr.resolvePool(None, team_level="hs", **kw) == "hs_m|XC"
    assert pr.resolvePool(None, team_level="ms", **kw) == "ms_m|XC"
    # ...and the university's rows still read college
    assert pr.resolvePool(None, team_level="college", **kw) == "college_m|XC"
    # an untrusted grade is the other way into the name map: same fix
    assert pr.resolvePool("10", grade_untrusted=True, team_level="hs", **kw) == "hs_m|XC"
    # a TRUSTED grade still decides alone -- the team level is a fallback,
    # not a fifth opinion
    assert pr.resolvePool("11", team_level="ms", **kw) == "hs_m|XC"
    # and grade_sanity's own per-season verdict still outranks it
    assert pr.resolvePool(None, fixed_level="college", team_level="hs", **kw) == "college_m|XC"


def test_a_stated_team_level_answers_the_verdicts_that_killed_the_row(monkeypatch):
    """★ OWNER, 2026-09-16: "for college runners a lot of them have all
    their races killed bcs their grade is untrusted". The three "we do not
    know" verdicts return no pool because the fallback is the school name.
    A level the feed states for the team is not a guess."""
    import normalize_distance as nd
    monkeypatch.setattr(nd, "_GRAPH_LEVELS", {})
    monkeypatch.setattr(nd, "_SCHOOL_LEVELS", {})
    kw = dict(gender="F", source="anet", school="Williams", sport="XC", season=2025)
    for verdict in ("no_evidence", "contradicted", "thin_field", "lone_word"):
        assert pr.resolvePool(None, grade_verdict=verdict, **kw) is None
        assert pr.resolvePool(None, grade_verdict=verdict,
                              team_level="hs", **kw) == "hs_f|XC"
        assert pr.resolvePool(None, grade_verdict=verdict,
                              team_level="college", **kw) == "college_f|XC"
    # ! A CLUB IS NOT A SCHOOL LEVEL -- but the row is already professional,
    #   and the kill may not drop a professional (ISSUES M). It used to.
    assert pr.resolvePool(None, grade_verdict="no_evidence",
                          team_level="club", **kw) == "pro_f|XC"


def test_anets_zero_team_is_no_school_at_all():
    """★ OWNER, 2026-09-16: "If any school has id == 0 we should just put
    them in pro", and asked whether to gate it on the season majority like
    the club rules: "unconditional". So it is not the club path -- it is
    decided before every grade, verdict and field rule."""
    kw = dict(gender="M", source="anet", school="Unattached", sport="XC",
              poolfor=_poolfor, season=2025)
    assert pr.resolvePool(None, no_team=True, **kw) == "pro_m|XC"
    assert pr.resolvePool("10", no_team=True, **kw) == "pro_m|XC"
    assert pr.resolvePool("10", no_team=True, fixed_level="hs", **kw) == "pro_m|XC"
    assert pr.resolvePool("10", no_team=True, season_level="hs", **kw) == "pro_m|XC"
    assert pr.resolvePool("10", no_team=True, grade_verdict="no_evidence", **kw) == "pro_m|XC"
    assert pr.resolvePool("10", **kw) == "hs_m|XC"              # the same row with a team
    # and the pack decides it from the id, not from the level
    assert pr.teamLevelOf(0, None, None) is None


def test_a_professional_with_an_unlevellable_school_is_still_a_professional():
    """Stage 2 repools a pro by swapping the LEVEL of the pool the grade or
    the school produced, so when neither produced one there was nothing to
    swap and the row was dropped -- silently undoing the rule for the rows
    it matters most for: an unattached entry whose school is whatever the
    athlete typed, and the elite squads in _PRO_TEAMS, which are in no
    school level map because they are not schools."""
    import normalize_distance as nd
    kw = dict(source="anet", sport="XC", season=2025)
    assert pr.resolvePool(None, gender="F", school="HOKA NAZ Elite", **kw) == "pro_f|XC"
    assert pr.resolvePool(None, gender="M", school="Zzz Nothing Club",
                          no_team=True, **kw) == "pro_m|XC"
    # ! ONLY WITH A KNOWN SEX: pro_unknown_gender is not a pool
    assert pr.resolvePool(None, gender=None, school="Zzz Nothing Club",
                          no_team=True, **kw) is None
    # and a row that is NOT professional still drops, as before
    assert pr.resolvePool(None, gender="M", school="Zzz Nothing Club", **kw) is None
    assert pr._proPoolFor("f") == "pro_f" and pr._proPoolFor("") is None


def test_the_pack_passes_the_zero_through(monkeypatch):
    """The rule is only real if packResults reads the id."""
    src = open(os.path.join(_ROOT, "engine", "speed_ratings.py")).read()
    assert "UNATTACHED_TEAM_ID" in src and "no_team=no_team" in src
    assert 'census["no_team_pro"]' in src
