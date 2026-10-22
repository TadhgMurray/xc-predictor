"""scripts/kailo_ratings: the pure parts (2026-10-22).

    python -m pytest -q tests/test_kailo_ratings.py
"""
import os
import sys

os.environ.setdefault("XCP_DB_PASSWORD", "x")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import kailo_ratings as R                                       # noqa: E402


def test_native_key():
    assert R.nativeKey("906936") == "906936"
    assert R.nativeKey("906936-rocky-hansen") == "906936"
    assert R.nativeKey(None) is None and R.nativeKey("abc") is None


def test_match_by_native_id_then_name_and_school():
    kailo = [{"athlete_id": 906936, "name": "Rocky Hansen", "school": "Wake Forest"},
             {"athlete_id": 1, "name": "Sam Smith", "school": "Oklahoma State"},
             {"athlete_id": 2, "name": "Jo Lee", "school": "Iowa"},
             {"athlete_id": 3, "name": "Jo Lee", "school": "Iowa State"}]
    ours = [(10, "Hansen, Rocky", "Wake Forest", "906936"),
            (11, "Smith, Sam", "Iowa", None),
            (12, "Smith, Sam", "Oklahoma St.", None),
            (13, "Lee, Jo", "Iowa", None)]
    got = R.matchAthletes(kailo, ours)
    assert got[906936] == 10 and got[1] == 12
    # two Kailo Jo Lees both land on person 13: neither is kept
    assert 2 not in got and 3 not in got


def test_native_id_with_a_different_name_is_not_trusted():
    kailo = [{"athlete_id": 5, "name": "Ann Other", "school": "X"}]
    assert R.matchAthletes(kailo, [(9, "Body, Some", "X", "5")]) == {}


def test_race_scores_order_and_level_free_spread():
    # Kailo has the order right; ours swaps the first two. Every runner 5%
    # slower than both ratings: the race's level, not a miss.
    rows = [(100.0 * 1.05, 100.0, 101.0), (101.0 * 1.05, 101.0, 100.0),
            (110.0 * 1.05, 110.0, 110.0)]
    gk, go, n, rk, ro = R.raceScores(rows)
    assert n == 3 and gk == 3 and go == 2
    assert max(abs(x) for x in rk) < 1e-9
    assert max(abs(x) for x in ro) > 0.4


def test_race_sex_and_bootstrap():
    assert R.raceSex([1, 2, 3], {1: "M", 2: "M", 3: "F"}) == "M"
    assert R.raceSex([1], {}) is None
    lo, hi = R.bootstrapOrder([(80, 90, 100), (70, 75, 100), (85, 88, 100)])
    assert 0 < lo <= hi
