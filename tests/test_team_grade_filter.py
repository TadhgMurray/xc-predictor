"""The teams board's grade control (owner, 2026-09-15).

    python -m pytest -q tests/test_team_grade_filter.py

Two complaints, one cause:
  "for teams choosing events still causes an error. same with graduating"
  "grade and graduating should be the same [control] ... You should be able
   to press for example all freshman teams"

Both filters genuinely need ONE season -- a restricted team board is raced
live, and an all-time board with the seniors removed is a field of squads
from thirty different years. Refusing was the wrong way to say so, because
the control could not express it. Now the season fills itself.
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in ("racecast", "scripts", "engine"):
    sys.path.insert(0, os.path.join(ROOT, _d))

os.environ.setdefault("XCP_DB_PASSWORD", "test-only")
T = pytest.importorskip("teams")


def parse(**args):
    return T.parseFilters(args, default_year=2026)


def test_a_grade_selection_fills_the_season_it_needs():
    f, err = parse(pool="hs_m", sport="XC", grade="9")
    assert err is None, err
    assert f["year"] == [2026] and f["year_defaulted"] is True
    assert f["grade"] == ["9"]


def test_an_event_window_fills_it_too():
    f, err = parse(pool="hs_m", sport="XC", dist_min="3200")
    assert err is None, err
    assert f["year"] == [2026] and f["year_defaulted"] is True


def test_the_old_graduating_filter_still_works():
    """exclude_grade left the UI but not the API: links are in the wild."""
    f, err = parse(pool="hs_m", sport="XC", exclude_grade="12")
    assert err is None, err
    assert f["exclude_grade"] == ["12"] and f["year"] == [2026]


def test_an_explicit_year_wins_and_is_not_flagged():
    f, err = parse(pool="hs_m", sport="XC", grade="9", year="2024")
    assert err is None, err
    assert f["year"] == [2024] and not f.get("year_defaulted")


def test_several_years_is_still_refused():
    """The one case that stays an error, because it cannot be answered:
    a live-raced board over thirty seasons is not one meet."""
    f, err = parse(pool="hs_m", sport="XC", grade="9", year="2024,2025")
    assert f is None and "ONE season" in err


def test_both_grade_directions_take_the_live_path():
    """team_season stores finished squads with no person and no grade
    behind them, so neither question can be answered from it."""
    assert T.gradeExcluded({"grade": ["9"]})
    assert T.gradeExcluded({"exclude_grade": ["12"]})
    assert not T.gradeExcluded({})


def test_the_two_controls_became_one_in_the_ui():
    js = open(os.path.join(ROOT, "racecast", "static", "rankings.js"),
              encoding="utf-8").read()
    html = open(os.path.join(ROOT, "racecast", "templates", "rankings.html"),
                encoding="utf-8").read()
    assert "exclude_grade" not in js
    assert 'data-field="exclude_grade"' not in html
    assert 'data-field="grade"' in html


def test_selecting_grades_keeps_only_them_and_excluding_keeps_the_ungraded():
    """The two directions differ on a row with NO grade, and each is right
    for its own reason: 'every freshman team' is a claim about who IS a
    freshman, while 'who returns' must not drop an athlete the feed simply
    never graded."""
    src = open(os.path.join(ROOT, "racecast", "teams.py"), encoding="utf-8").read()
    body = src.split("def teamGradeSql", 1)[-1] if "def teamGradeSql" in src else src
    assert "in_grade_keys" in body and "ex_grade_keys" in body
    # the selection is a plain equality: an ungraded row cannot match it
    assert "= ANY(%(in_grade_keys)s)" in body
    # the exclusion explicitly keeps NULL grades
    assert "IS NULL" in body and "<> ALL(%(ex_grade_keys)s)" in body
