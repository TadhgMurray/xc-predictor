"""A national team's row supplies no grade, a club outranks a country, and a
college field verdict past eligibility is professional (owner, 2026-10-07).

★ JOHN RIVERA, /athlete/12652858: Lakewood Ranch HS 2013-2016, Ole Miss
  FR-1 2017 to senior 2021, a Brooks Beasts professional since. His page
  read "Puerto Rico · Grade 12": his World Indoors rows (school 'Puerto
  Rico') carry grade '12', his 2023 Worlds row '10', and the season blocks
  quoted them. His 2026 rows -- one for Brooks Beasts, one for Puerto Rico,
  grade '-' -- named the country, and pooled college_m on a field verdict
  nine years after his first college season.

    python -m pytest -q tests/test_national_team_grade.py
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in ("racecast", "engine", "scripts"):
    sys.path.insert(0, os.path.join(_ROOT, _d))
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
os.environ.setdefault("XCP_DB_QUIET", "1")

import pytest                                                    # noqa: E402


def _race(date, school, grade, label, sport="TF", rating=None, pool=None):
    return {"date": date, "school": school, "grade": grade, "sport": sport,
            "season_label": label, "speed_rating": rating, "pool": pool}


# his rows, newest first as get_races returns them
RIVERA = [
    _race("2026-08-19", "Puerto Rico",   "-",      "2027", rating=124.1, pool="college_m"),
    _race("2026-08-15", "Brooks Beasts", "-",      "2027", rating=123.05, pool="college_m"),
    _race("2025-03-21", "Puerto Rico",   "12",     "2025"),
    _race("2025-03-21", "Puerto Rico",   "12",     "2025"),
    _race("2023-12-02", "Unattached",    "Senior", "2024"),
    _race("2023-08-27", "Puerto Rico",   "10",     "2024", rating=117.62, pool="pro_m"),
    _race("2022-04-16", "Ole Miss",      "Sr",     "2022", rating=125.11, pool="college_m"),
]


def _app():
    pytest.importorskip("flask")
    import app as A
    return A


def _block(A, label):
    return [r for r in RIVERA if r["season_label"] == label]


# ---------------------------------------------------------------- display

def test_a_country_row_supplies_no_grade():
    A = _app()
    assert A._season_grade(_block(A, "2025")) is None          # '12' x2, Puerto Rico
    assert A._season_grade(_block(A, "2024")) == "Senior"      # not the Worlds '10'
    assert A._season_grade(_block(A, "2022")) == "Sr"


def test_a_dash_is_not_a_grade():
    A = _app()
    assert A._season_grade(_block(A, "2027")) is None          # '-', '-'
    assert A._season_grade([_race("2026-01-01", "Herriman", "-", "2026"),
                            _race("2026-01-02", "Herriman", "12", "2026")]) == "12"


def test_a_club_outranks_a_country():
    A = _app()
    # one race each; the country used to win because isTeamName refuses clubs
    assert A._season_school(_block(A, "2027")) == "Brooks Beasts"
    # a country still names a season nothing else names
    assert A._season_school(_block(A, "2025")) == "Puerto Rico"
    # and unattached never beats it
    assert A._season_school(_block(A, "2024")) == "Puerto Rico"
    # an ordinary school is untouched
    assert A._season_school(_block(A, "2022")) == "Ole Miss"


def test_the_header_trades_a_country_for_the_club():
    A = _app()
    assert A._clubOverCountry("Puerto Rico", RIVERA, since_label="2024") == "Brooks Beasts"
    # nothing newer than the window: the country stands
    assert A._clubOverCountry("Puerto Rico", RIVERA[2:], since_label="2024") == "Puerto Rico"
    # not a country: untouched
    assert A._clubOverCountry("Ole Miss", RIVERA) == "Ole Miss"


def test_a_professional_verdict_outranks_the_row_grade():
    A = _app()
    seasons = A.enrich_seasons(A.group_into_seasons(RIVERA))
    A._attach_season_verdicts(seasons, [
        {"season": 2026, "method": "field", "trust": "high", "level": "college"},
        {"season": 2024, "method": "graduated", "trust": "high", "level": "pro"},
        {"season": 2023, "method": "field", "trust": "low", "level": "college"},
        {"season": 2021, "method": "corroborated", "trust": "high", "level": None},
    ])
    assert seasons[("2025", "TF")]["pro_verdict"] is True
    assert seasons[("2025", "TF")]["grade"] is None
    assert not seasons[("2027", "TF")].get("pro_verdict")
    assert seasons[("2022", "TF")]["grade"] == "Sr"


def test_a_school_pool_keeps_its_grade_under_a_pro_verdict():
    """pool_resolve's school veto refuses a 'pro' verdict on a school season;
    the rows then pool hs and the grade is the one the boards use."""
    A = _app()
    rows = [_race("2025-04-01", "Herriman", "12", "2025", rating=140.0, pool="hs_m")]
    seasons = A.enrich_seasons(A.group_into_seasons(rows))
    A._attach_season_verdicts(seasons, [{"season": 2024, "method": "graduated",
                                         "trust": "high", "level": "pro"}])
    assert seasons[("2025", "TF")]["grade"] == "12"
    assert not seasons[("2025", "TF")].get("pro_verdict")


def _blk(dates, pro=False, pool="college_m"):
    return {"races": [{"date": d} for d in dates], "pool": pool,
            "school": "Brooks Beasts", "pro_verdict": pro}


def test_the_newest_season_names_the_level():
    A = _app()
    head = {"year": 2021, "sport": "TF"}
    ordered = sorted({
        ("2027", "TF"): _blk(["2026-08-15"], pro=True, pool=None),
        ("2022", "TF"): _blk(["2022-04-16"]),
    }.items(), reverse=True)
    (label, _sport), _b = A._proVerdictHeader(ordered, head)
    assert label == "2027"
    # pro is not absorbing: an older pro verdict under a newer college season
    ordered = sorted({
        ("2027", "TF"): _blk(["2026-08-15"]),
        ("2025", "TF"): _blk(["2025-03-21"], pro=True, pool=None),
        ("2022", "TF"): _blk(["2022-04-16"]),
    }.items(), reverse=True)
    assert A._proVerdictHeader(ordered, head) is None


def test_the_season_line_says_professional():
    path = os.path.join(_ROOT, "racecast", "templates", "athlete.html")
    src = open(path, encoding="utf-8").read()
    assert "{% if season.pro_verdict or" in src and " · Professional" in src
    assert "{% elif season.grade %} · Grade" in src            # no "Grade None"


def test_the_chart_band_names_the_club():
    import athlete_chart_data as C
    schools = C._seasonSchools(RIVERA)
    assert schools[("2027", "TF")] == "Brooks Beasts"
    assert schools[("2025", "TF")] == "Puerto Rico"


# ----------------------------------------------------------------- engine

def test_a_college_field_past_eligibility_is_professional():
    import grade_sanity as GS
    acad = {
        (1, 2026): {"grade": None, "level": "college", "method": "field"},
        (1, 2023): {"grade": None, "level": "college", "method": "field"},
        (1, 2021): {"grade": "SR", "level": None, "method": "corroborated"},
        (1, 2024): {"grade": None, "level": "pro", "method": "graduated"},
        # a sixth-year collegian (COVID year) on a field verdict stays
        (2, 2023): {"grade": None, "level": "college", "method": "field"},
        # nobody's college start known: nothing to measure from
        (3, 2026): {"grade": None, "level": "college", "method": "field"},
        # a high school field verdict is 5b's, not this rule's
        (4, 2026): {"grade": None, "level": "hs", "method": "field"},
    }
    start = {1: 2017, 2: 2017, 4: 2010}
    assert GS.POST_ELIGIBILITY_SEASONS == 7
    assert GS.pastEligibilitySeasons(acad, start) == [(1, 2026)]
    # the gap is the only knob
    assert GS.pastEligibilitySeasons(acad, start, gap=6) == [(1, 2023), (1, 2026),
                                                            (2, 2023)]
