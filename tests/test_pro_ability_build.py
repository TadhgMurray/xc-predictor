# Project: xc-predictor / tests
# File:    test_pro_ability_build.py
# Purpose: the ability table is built on ONE curve, from the loader the solve
#          itself uses, and it holds exactly the athlete-seasons that cleared
#          the owner's bar.
#
# ★ OWNER, 2026-09-22: "if they're sub 14:00? for men, or sub 15:30? for
#   women put in pro, otherwise trust grade."
#
# ⚠⚠⚠ THE TRAP THIS FILE EXISTS FOR. normalize_distance.targetFor gives every
#     pool a DIFFERENT anchor distance -- 5000 for hs and pro, 8000 for
#     college men, 6000 for college women, 3200 for middle school. So the
#     stored normalized_time means a different performance in every pool, and
#     testing a college man's stored number against 840 seconds asks him for
#     a 14:00 EIGHT thousand. Every assertion below is about the builder
#     re-expressing marks on one common curve instead.
#
#   python -m pytest -q tests/test_pro_ability_build.py
import os
import sys

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
os.environ.setdefault("XCP_DB_QUIET", "1")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "engine"), os.path.join(_ROOT, "scripts"),
           os.path.join(_ROOT, "racecast")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import build_pro_ability as B                                  # noqa: E402
import pool_resolve as pr                                      # noqa: E402
from speed_ratings_db import COLUMNS                           # noqa: E402


def _row(person_id, date, sport, gender, dist_m, time_seconds):
    """A streamResults tuple, laid out by COLUMNS rather than by position.

    ! BY NAME, NOT BY INDEX. COLUMNS has grown three times this month
      (dist_m, meet_class, time_seconds all arrived mid-September); a test
      holding a 15-tuple of positional Nones would have silently started
      asserting about the wrong field each time."""
    row = [None] * len(COLUMNS)
    i = {n: k for k, n in enumerate(COLUMNS)}
    row[i["person_id"]] = person_id
    row[i["date"]] = date
    row[i["sport"]] = sport
    row[i["gender"]] = gender
    row[i["dist_m"]] = dist_m
    row[i["time_seconds"]] = time_seconds
    return tuple(row)


def _scan(rows_by_sport, monkeypatch=None):
    """Run scan() against a fake stream."""
    def fake(sport, min_time=200.0, max_time=6000.0, batch=200_000):
        rows = rows_by_sport.get(sport, [])
        if rows:
            yield rows
    old = B.streamResults
    B.streamResults = fake
    try:
        return B.scan()
    finally:
        B.streamResults = old


# ------------------------------------------------------------------ #
#  THE BAR
# ------------------------------------------------------------------ #

def test_a_sub_14_man_is_able_and_a_15_00_man_is_not():
    able, best, _h, _nr, _nu = _scan({"TF": [
        _row(1, "2026-04-11", "TF", "M", 5000.0, 810.0),       # 13:30
        _row(2, "2026-04-11", "TF", "M", 5000.0, 900.0),       # 15:00
    ]})
    assert (1, 2025) in able
    assert (2, 2025) not in able
    assert (2, 2025) in best          # seen, and judged -- not skipped


def test_the_womens_bar_is_its_own():
    """! A 15:00 5K is under the women's bar and over the men's. The same
    mark on the same curve, two verdicts -- which is the whole reason the
    threshold is keyed by gender rather than being one number."""
    able, _b, _h, _nr, _nu = _scan({"TF": [
        _row(1, "2026-04-11", "TF", "F", 5000.0, 900.0),       # 15:00
        _row(2, "2026-04-11", "TF", "M", 5000.0, 900.0),
        _row(3, "2026-04-11", "TF", "F", 5000.0, 960.0),       # 16:00
    ]})
    assert (1, 2025) in able
    assert (2, 2025) not in able
    assert (3, 2025) not in able


def test_the_bar_is_strict_at_the_exact_second():
    able, _b, _h, _nr, _nu = _scan({"TF": [
        _row(1, "2026-04-11", "TF", "M", 5000.0, pr.PRO_ABILITY_5K["M"]),
    ]})
    assert (1, 2025) not in able


# ------------------------------------------------------------------ #
#  ONE CURVE, NOT THE ATHLETE'S OWN POOL
# ------------------------------------------------------------------ #

def test_an_800_is_converted_not_compared_raw():
    """A 1:45 800 is a professional mark; 105 seconds is not under 840 by
    accident of being a small number. It clears because the curve puts it
    at 13:42."""
    able, best, _h, _nr, _nu = _scan({"TF": [
        _row(1, "2026-04-11", "TF", "M", 800.0, 105.0),        # 1:45
        _row(2, "2026-04-11", "TF", "M", 800.0, 110.0),        # 1:50
    ]})
    assert (1, 2025) in able
    assert (2, 2025) not in able
    assert 800.0 < best[(1, 2025)][0] < 840.0     # converted, not the raw 105


def test_the_distance_is_what_makes_a_mark_qualify():
    """⚠ THE COLLEGE-ANCHOR TRAP, stated as arithmetic. An 8000m in 1,380s
    (23:00) is a strong college run and nowhere near professional; a 5000m
    in 1,380s is a jog. Same seconds, and the builder must not confuse
    them -- which is exactly what reading the stored normalized_time would
    have done."""
    _a, best, _h, _nr, _nu = _scan({"XC": [
        _row(1, "2025-10-11", "XC", "M", 8000.0, 1380.0),
        _row(2, "2025-10-11", "XC", "M", 5000.0, 1380.0),
    ]})
    assert best[(1, 2025)][0] < best[(2, 2025)][0]


# ------------------------------------------------------------------ #
#  THE SEASON, AND BOTH SPORTS IN IT
# ------------------------------------------------------------------ #

def test_one_athlete_season_takes_the_best_of_both_sports():
    """! A season is one season of one person's life. An October 13:30 and
    an April 15:00 are evidence about the same body, already on the same
    scale, so the season is able."""
    able, _b, _h, _nr, _nu = _scan({
        "XC": [_row(1, "2025-10-11", "XC", "M", 5000.0, 810.0)],
        "TF": [_row(1, "2026-04-11", "TF", "M", 5000.0, 900.0)],
    })
    assert (1, 2025) in able


def test_seasons_are_judged_separately():
    """★ THE SAME DESIGN AS pro_flag. Ability in 2025 says nothing about
    2023 -- an absorbing verdict is the bug Engelhardt's college seasons
    caught."""
    able, _b, _h, _nr, _nu = _scan({"XC": [
        _row(1, "2025-10-11", "XC", "M", 5000.0, 810.0),
        _row(1, "2023-10-11", "XC", "M", 5000.0, 1020.0),
    ]})
    assert (1, 2025) in able
    assert (1, 2023) not in able


def test_a_spring_race_belongs_to_the_autumn_that_opened_it():
    """! THE ACADEMIC YEAR, from season_year -- not left(date,4). April 2026
    is the 2025 season, which is the key pool_resolve looks up."""
    able, _b, _h, _nr, _nu = _scan({"TF": [
        _row(1, "2026-04-11", "TF", "M", 5000.0, 810.0),
    ]})
    assert list(able) == [(1, 2025)]


# ------------------------------------------------------------------ #
#  ROWS THAT CANNOT BE JUDGED
# ------------------------------------------------------------------ #

def test_unusable_rows_are_skipped_not_guessed():
    able, best, _h, _nr, n_used = _scan({"TF": [
        _row(None, "2026-04-11", "TF", "M", 5000.0, 810.0),    # no person
        _row(2, None, "TF", "M", 5000.0, 810.0),               # no date
        _row(3, "2026-04-11", "TF", "M", None, 810.0),         # no distance
        _row(4, "2026-04-11", "TF", "M", 5000.0, None),        # no time
        _row(5, "2026-04-11", "TF", "unknown_gender", 5000.0, 810.0),
        _row(6, "2026-04-11", "TF", "M", 5000.0, 810.0),       # the good one
    ]})
    assert list(able) == [(6, 2025)]
    assert list(best) == [(6, 2025)]
    assert n_used == 1


def test_an_unknown_gender_has_no_bar_to_clear():
    """! There is no hs_unknown_gender threshold and there must not be a
    default one. A pool with no HS twin gets no verdict -- the same posture
    pool_view.hsFactor takes."""
    assert "unknown_gender" not in pr.PRO_ABILITY_5K
    assert set(pr.PRO_ABILITY_5K) == {"M", "F"}


# ------------------------------------------------------------------ #
#  THE HISTOGRAM THE REPORT IS READ FROM
# ------------------------------------------------------------------ #

def test_the_bars_fall_on_a_bin_edge():
    """! A histogram whose bar straddles the threshold it was drawn to
    explain is not evidence. Both thresholds must BE edges."""
    assert pr.PRO_ABILITY_5K["M"] in B._BINS
    assert pr.PRO_ABILITY_5K["F"] in B._BINS


def test_every_judged_season_lands_in_exactly_one_bin():
    rows = [_row(i, "2026-04-11", "TF", "M", 5000.0, t)
            for i, t in enumerate((700.0, 800.0, 860.0, 1000.0, 2000.0), 1)]
    _a, best, hist, _nr, _nu = _scan({"TF": rows})
    assert sum(hist["M"]) == len(best) == 5
    assert sum(hist["F"]) == 0


def test_the_builder_reuses_the_solves_own_loader_and_scale():
    """! NOT A SECOND IMPLEMENTATION. The row set is streamResults -- with
    its band, its cross-source dedup, its wheelchair and field-event
    exclusions -- and the conversion is speed_ratings._scaleFactor, the
    multiplier rescaleToPool identifies scales with. Writing either again
    here is the failure pool_resolve's own header records."""
    import ast
    import io
    src = io.open(os.path.join(_ROOT, "engine", "build_pro_ability.py"),
                  encoding="utf-8").read()
    imported = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.ImportFrom):
            imported.update(a.name for a in node.names)
    for name in ("streamResults", "_scaleFactor", "COLUMNS",
                 "seasonYearFromIso", "PRO_ABILITY_5K"):
        assert name in imported, name
    # and it must not grow its own SQL over the result tables
    assert "FROM results" not in src
    assert "normalized_time" not in src.split('"""')[2]     # body, not header
