# Project: xc-predictor / tests
# File:    test_course_scale.py
# Purpose: 2026-09-15 -- the per-sport course scale the voters' brackets
#          imply, the tilt-by-races diagnostic, the sport level per pool
#          level at go-live, and the two board rails. No database.
#
#   XCP_DB_PASSWORD=x python -m pytest -q tests/test_course_scale.py
import os
import sys

import numpy as np

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "engine"), os.path.join(_ROOT, "scripts"),
           os.path.join(_ROOT, "racecast")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import bracket_engine as be                                    # noqa: E402
import joint_solve as js                                       # noqa: E402
import run_joint as rj                                         # noqa: E402
import sport_level_fit as slf                                  # noqa: E402


def test_the_course_scale_is_the_voter_weighted_ratio_over_trusted_bands():
    rows = [("XC", "<100", 500_000, 1.012, 1.151, 0.001),
            ("XC", "100-120", 4_000_000, 0.970, 1.075, 0.000),
            ("XC", "140-150", 9_000, 0.867, 0.986, 0.009),
            ("XC", "150-160", 2_000, 0.83, 1.5, 0.05),        # thin and noisy: ignored
            ("TF", "100-120", 2_000_000, 0.963, 0.968, 0.001)]
    s_xc = be.courseScaleFromBands(rows, "XC")
    s_tf = be.courseScaleFromBands(rows, "TF")
    assert 1.09 < s_xc < 1.14 and abs(s_tf - 0.968 / 0.963) < 1e-9
    assert be.courseScaleFromBands([], "XC") == 1.0 and be.courseScaleFromBands(None, "TF") == 1.0


def test_course_scale_specs():
    rows = [("XC", "100-120", 4_000_000, 1.0, 1.1, 0.0)]
    assert rj.courseScales("fit", rows) == {0: 1.1, 1: 1.0}
    assert rj.courseScales("off", rows) == {0: 1.0, 1: 1.0}
    assert rj.courseScales("1.05", rows) == {0: 1.05, 1: 1.05}
    assert rj.courseScales("XC=1.1,TF=1", rows) == {0: 1.1, 1: 1.0}
    assert rj.parseLevelGains("college=0,hs=0.008, ms=0.012") == {"college": 0.0, "hs": 0.008, "ms": 0.012}
    assert rj.parseLevelGains(None) is None and rj.parseLevelGains("") is None


def test_tilt_by_races_reads_a_planted_ratio_per_bucket():
    rng = np.random.default_rng(3)
    n_cell = 40
    D = rng.normal(0, 0.05, n_cell)
    races = np.array([1] * 20 + [10] * 20)
    cell_sport = np.zeros(n_cell, dtype=np.int64)
    cell = np.repeat(np.arange(n_cell), 200)
    ratio = np.where(races[cell] == 1, 1.3, 1.0)       # thin cells read shrunk: implied 1.3
    a_local = rng.normal(0, 0.01, cell.size)
    z = a_local + ratio * D[cell] + rng.normal(0, 0.002, cell.size)
    h = np.ones(cell.size)
    vote = np.ones(cell.size, dtype=bool)
    rows = be.tiltByRaces(z, a_local, h, D, cell, vote, races, cell_sport, min_rows=100)
    by = {lab: implied for s, lab, n, ha, implied, se in rows}
    assert abs(by["1"] - 1.3) < 0.03 and abs(by["10+"] - 1.0) < 0.03
    assert be.tiltRaceLines(rows)[0].startswith("sport")


def test_the_gain_per_pool_shifts_only_the_pools_it_names():
    # two athletes per pool, both sports, ratings in the middle band
    log_adj = np.array([5.0, 4.9, 5.0, 4.9, 5.0, 4.95, 5.0, 4.95])   # XC, TF per athlete
    sport = np.array([0, 1, 0, 1, 0, 1, 0, 1])
    athlete = np.array([0, 0, 1, 1, 2, 2, 3, 3])
    rating = np.array([112.0, 112.0, 112.0, 112.0])
    pool = np.array([0, 0, 1, 1])
    gains = np.full((2, 3), np.nan); gains[0, :] = 0.02          # pool 0 named, pool 1 not
    shift, gap, n = js.sportGainShift(log_adj, sport, athlete, rating, pool, 2, gains,
                                      min_athletes=1)
    assert abs(gap[0, 1] + 0.1) < 1e-9 and abs(gap[1, 1] + 0.05) < 1e-9
    assert abs(shift[0, 1] - (-0.1 + 0.02)) < 1e-9
    assert shift[1].tolist() == [0.0, 0.0, 0.0]
    # the one-dimensional form still means one gain per band for every pool
    shift1, _g, _n = js.sportGainShift(log_adj, sport, athlete, rating, pool, 2,
                                       np.array([0.0, 0.02, 0.0]), min_athletes=1)
    assert abs(shift1[1, 1] - (-0.05 + 0.02)) < 1e-9


def test_the_level_fit_summarises_by_level_and_prints_the_env_line():
    gaps = [("hs_m", -0.01)] * 3 + [("hs_f", -0.005)] * 2 + [("college_m", -0.012)]
    growths = [("hs_m", 0.02)] * 1500 + [("hs_f", 0.03)] * 1000 + [("college_m", 0.0)] * 1200
    s = slf.summarise(gaps, growths, share=0.5)
    assert s["hs"]["n_gap"] == 5 and abs(s["hs"]["growth"] - 0.02) < 1e-9
    assert abs(s["hs"]["gain"] - 0.01) < 1e-9 and s["college"]["gain"] == 0.0
    line = slf.envLine(s, min_pairs=1000)
    assert line == "XCP_SPORT_LEVEL_POOLS=college=0.0000,hs=0.0100"


def test_the_rails_and_the_plumbing_are_in_place():
    brr = open(os.path.join(_ROOT, "racecast", "build_ranking_results.py")).read()
    assert 'if row.speed_rating is not None and distance is None:' in brr
    assert brr.index('if row.speed_rating is not None and distance is None:') < brr.index("if impossibleRow(")
    assert brr.count('"no_distance": 0') == 2
    sdb = open(os.path.join(_ROOT, "engine", "speed_ratings_db.py")).read()
    body = sdb[sdb.index("def loadClubPros"):sdb.index("def loadClubMajority")]
    assert "_collegeNames(cur)" in body and "school in colleges" in body
    sh = open(os.path.join(_ROOT, "deploy", "run_pipeline.sh")).read()
    assert '--course-scale "$XCP_COURSE_SCALE"' in sh and '--sport-level-pools "$XCP_SPORT_LEVEL_POOLS"' in sh
    cb = open(os.path.join(_ROOT, "scripts", "course_bracket.py")).read()
    assert '"scale": float(scale[c])' in cb


def test_the_sanity_step_checks_the_level_and_the_tilt():
    import board_sanity as bs
    # the level: log(TF/XC) of season means; the gain 0.01 wants the gap -0.01
    pairs = [("hs_m", 100.0, 101.0)] * 40 + [("college_f", 100.0, 100.0)] * 40 + [("ms_m", 100.0, 105.0)] * 40
    got = bs.levelCheck(pairs, {"hs": 0.01, "college": 0.0, "ms": 0.0}, tol=0.005)
    by = {lvl: (ok, read) for lvl, n, read, target, ok in got}
    assert by["hs"][0] and by["college"][0] and not by["ms"][0]
    assert abs(by["hs"][1] + 0.00995) < 1e-4
    assert bs.levelGains("college=0, hs=0.008") == {"college": 0.0, "hs": 0.008}
    # the tilt: run 23's XC rows with the 1.1 scale applied read within 6%
    bands = be.tiltBandArray([("XC", "<100", 575_000, 1.012, 1.151, 0.001),
                              ("XC", "140-150", 9_000, 0.867, 0.986, 0.009),
                              ("XC", "150-160", 2_000, 0.83, 1.5, 0.05),
                              ("TF", "100-120", 2_000_000, 0.963, 0.968, 0.001)])
    assert bands.shape == (4, 6) and bands[0, 1] == -np.inf and bands[1, 1] == 140.0
    got = bs.tiltCheck(bands, np.array([1.10, 1.0]))
    assert [(s, ok) for s, lo, n, r, ok in got] == [("XC", True), ("XC", True), ("TF", True)]
    got = bs.tiltCheck(bands, np.array([1.0, 1.0]))          # unscaled, XC is a tenth off
    assert [(s, ok) for s, lo, n, r, ok in got] == [("XC", False), ("XC", False), ("TF", True)]
