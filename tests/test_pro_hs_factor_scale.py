"""The pro pool's HS-equivalent factor is its college twin's, everywhere
(2026-09-30, Jackson Spencer, https://racecast.co/athlete/30178075).

    XCP_DB_PASSWORD=x python -m pytest -q tests/test_pro_hs_factor_scale.py

His page stamped one professional-rated row x1.83 (the World XC U20 race,
113.3 -> 207.5; the Bowerman Mile, 120.0 -> 219.8), most senior rows x1.0000
(ranking_results, from before a failed step 10, still said hs_m) and the
season headers ~x1.10 (a median over the two). Three numbers for one scale.

★ THE SCALE, AS THE SOLVE DEFINES IT. A pro row is rated 100 * pm(college) /
  adjusted, on the college anchor (pair_write_results._proScaleMap, the mean;
  speed_ratings._scalePool = normalize_distance.ratedScalePool, the anchor).
  So its HS multiplier is college's: C(hs)/C(college) x F(college)/F(hs).

★ THE CHECK FROM HIS OWN ROWS. The same athlete, the same event, one season
  apart: his junior 4:02.56 mile read 143.0 in hs_m, his senior 3:57.34 at
  the Bowerman Mile read 120.0 on the pro (college) scale, his 3:57.24 at
  the Festival of Miles 120.1. A rating is a pool mean over an adjusted time,
  so at one distance the HS-equivalent of a mark is 143.0 x 242.56 / t --
  146.1 for the 3:57.34. Constants that reproduce his hs row and his Pre row
  must price his Festival row inside the rounding of the ratings, and the
  old measured constant (C(hs) / 1.83) must not be what prices it.
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in ("scripts", "engine", "racecast"):
    _p = os.path.join(_ROOT, _d)
    if _p not in sys.path:
        sys.path.insert(0, _p)
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
os.environ.setdefault("XCP_DB_QUIET", "1")

import pytest                                                   # noqa: E402

import pool_view as PV                                          # noqa: E402

# his marks, as the page shows them (seconds, rating)
JUNIOR_MILE = (242.56, 143.0)            # 4:02.56, hs_m, TF 2025
PRE_MILE = (237.34, 120.0)               # 3:57.34, Eugene Diamond League
FESTIVAL_MILE = (237.24, 120.1)          # 3:57.24, HOKA Festival of Miles
MEASURED_PRO = 1.83                      # what the page stamped
ROUNDING = 0.05                          # ratings are shown to 0.1


def _constants():
    """C per (pool, sport) that reproduce his hs row and his Pre row, the
    way the engine writes them: rating = 100 * C / adjusted, adjusted = the
    time times one common factor at one distance (it cancels), and the pro
    row's C is the COLLEGE mean. The pro pool's own 'constant' is the
    broken measurement the page used."""
    t_hs, r_hs = JUNIOR_MILE
    t_pro, r_pro = PRE_MILE
    c_hs = r_hs * t_hs / 100.0
    c_col = r_pro * t_pro / 100.0
    c = {}
    for sp in ("XC", "TF"):
        c[("hs_m", sp)] = c_hs
        c[("college_m", sp)] = c_col
        c[("pro_m", sp)] = c_hs / MEASURED_PRO
    return c


@pytest.fixture
def scale(monkeypatch):
    c = _constants()
    monkeypatch.setattr(PV, "_poolConstant", lambda pool, sport: c.get((pool, sport)))
    monkeypatch.setattr(PV, "_forward_factor", lambda *a, **k: 1.0)
    monkeypatch.setattr(PV, "_FACTOR_CACHE", {})
    monkeypatch.setattr(PV, "_FACTOR_WHY", {})
    monkeypatch.setattr(PV, "_FAILED", set())
    return c


def test_the_pro_factor_is_the_one_his_rows_imply(scale):
    f = PV.hsFactor("pro_m", "TF", 1609.0)
    assert f == PV.hsFactor("college_m", "TF", 1609.0)
    # the Festival row: priced by the constants, against the time ratio
    t_hs, r_hs = JUNIOR_MILE
    t, r = FESTIVAL_MILE
    want = r_hs * t_hs / t
    tol = ROUNDING * f + ROUNDING * t_hs / t      # both ratings' rounding
    assert abs(r * f - want) <= tol, (r * f, want)
    # and the page's x1.83 is nowhere near it
    assert abs(r * MEASURED_PRO - want) > tol * 100


def test_a_faster_mile_reads_faster_than_his_junior_one_and_no_more(scale):
    """The monotone check a reader makes: 3:57 beats 4:02.56 on one scale
    by exactly the time, not by 77 points."""
    f = PV.hsFactor("pro_m", "TF", 1609.0)
    t_hs, r_hs = JUNIOR_MILE
    t, r = PRE_MILE
    assert r_hs < r * f <= r_hs * t_hs / t + ROUNDING


class _Cur:
    """ranking_results as a failed step 10 left it: his senior rows hs_m."""
    def __init__(self, rows):
        self.rows = rows
        self.connection = self

    def execute(self, sql, params=None):
        pass

    def fetchall(self):
        return self.rows

    def rollback(self):
        pass


def test_header_rows_and_boards_carry_one_factor(scale):
    f = PV.repFactor("pro_m", "TF")
    # the race and compiled pages: the row's own rating_pool wins over a
    # stale ranking_results
    rows = [{"result_id": 1, "speed_rating": 120.0, "rating_pool": "pro_m"},
            {"result_id": 2, "speed_rating": 117.0, "rating_pool": "pro_m|TF"}]
    PV.stampRowsHs(_Cur([{"result_id": 1, "pool": "hs_m"},
                         {"result_id": 2, "pool": "hs_m"}]), "TF", rows,
                   distance=1609.0)
    assert [round(r["hs_rating"] / r["speed_rating"], 9) for r in rows] \
        == [round(f, 9)] * 2
    # the athlete page: the same, through stampHsRatings
    races = [{"sport": "TF", "result_id": 1, "speed_rating": 120.0,
              "rating_pool": "pro_m", "season_label": 2026,
              "event": "Mile", "is_field": False},
             {"sport": "TF", "result_id": 2, "speed_rating": 119.8,
              "rating_pool": "pro_m", "season_label": 2026,
              "event": "1500", "is_field": False}]
    stale = [{"sport": "TF", "result_id": 1, "pool": "hs_m", "year": 2025},
             {"sport": "TF", "result_id": 2, "pool": "hs_m", "year": 2025}]
    assert PV.stampHsRatings(stale, races)
    assert all(abs(r["hs_rating"] / r["speed_rating"] - f) < 1e-12 for r in races)
    assert [r["pool"] for r in races] == ["pro_m", "pro_m"]
    # the season header's median over its rows, and a board row
    assert abs(PV.seasonFactor(races, label=2026, sport="TF") - f) < 1e-12
    board = [{"pool": "pro_m", "sport": "TF", "rating": 117.0}]
    PV.stampBoardRows(board)
    assert board[0]["hs_rating"] == round(117.0 * f, 1)


def test_a_row_without_rating_pool_still_falls_back(scale):
    """ranking_results, then the table's mode, exactly as before, for a row
    the go-live has not stamped."""
    rows = [{"result_id": 7, "speed_rating": 130.0}]
    PV.stampRowsHs(_Cur([{"result_id": 7, "pool": "college_m"}]), "TF", rows,
                   distance=1609.0)
    assert rows[0]["hs_rating"] == 130.0 * PV.hsFactor("college_m", "TF", None)
    races = [{"sport": "TF", "result_id": 7, "speed_rating": 130.0,
              "season_label": 2026, "event": "Mile", "is_field": False}]
    PV.stampHsRatings([{"sport": "TF", "result_id": 7, "pool": "hs_m",
                        "year": 2025}], races)
    assert races[0]["pool"] == "hs_m" and races[0]["hs_rating"] == 130.0


def test_the_page_queries_carry_rating_pool():
    """The rows stampRowsHs prices must bring the column it reads first."""
    src = open(os.path.join(_ROOT, "racecast", "app.py"), encoding="utf-8").read()
    for fn in ("def get_races(", "def get_race_results(",
               "def get_tf_race_results(", "def get_tf_meet_scoring_rows(",
               "def get_tf_venue_bests("):
        i = src.index(fn)
        body = src[i:src.index("\ndef ", i + 10)]
        assert "_ratingPoolCol(cur" in body or "rp_xc" in body, fn
    mc = open(os.path.join(_ROOT, "racecast", "meet_compile.py"),
              encoding="utf-8").read()
    i = mc.index("def compiledResults(")
    body = mc[i:mc.index("\ndef ", i + 10)]
    assert "_ratingPoolSql(cur)" in body and '"rating_pool": row.get(' in body
