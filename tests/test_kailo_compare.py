"""scripts/kailo_compare: the pure parts (2026-10-21).

    python -m pytest -q tests/test_kailo_compare.py
"""
import os
import sys

os.environ.setdefault("XCP_DB_PASSWORD", "x")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import kailo_compare as K                                       # noqa: E402


def test_final_version_only_if_published_before_start():
    race = {"final_seq": 3, "start_at": "2026-09-26T13:15:00Z",
            "published_log": [{"seq": 3, "published_at": "2026-09-26T05:03:23Z"}],
            "latest": {"seq": 3, "rows": [{"predicted_s": 1156.6}]}}
    assert K.finalVersion(race) is race["latest"]
    late = dict(race, published_log=[{"seq": 3, "published_at": "2026-09-26T14:00:00Z"}])
    assert K.finalVersion(late) is None
    assert K.finalVersion(dict(race, final_seq=2)) is None


def test_pick_division_by_distance_sex_and_words():
    divs = {11: (6000, "Women's Maize 6K"), 12: (6000, "Women's Navy Championship 6K"),
            13: (8000, "Men's Navy Championship 8K"), 14: (6000, "Men's 6K")}
    assert K.pickDivision(divs, 6000, "F", "Women's Navy Championship 6K") == 12
    assert K.pickDivision(divs, 6000, "F", "Women's Maize 6K") == 11
    assert K.pickDivision(divs, 8000, "M", "Men's Navy Championship 8K") == 13
    assert K.pickDivision(divs, 6000, "M", "Men's 6K") == 14
    assert K.pickDivision(divs, 10000, "M", "Men's 10K") is None


def test_scores_and_summary():
    rows = [(100.0, 103.0, 100.0), (110.0, 108.0, 112.0), (120.0, 119.0, 118.0)]
    mo, mt, (go, gt, n) = K.scores(rows)
    assert n == 3 and go == 3 and gt == 3
    s = K.summary(mo)
    assert abs(s["mean"] - (0 + 100 * 2 / 112 + 100 * 2 / 118) / 3) < 1e-9
    assert s["within2"] == 1.0


def test_match_runners_by_name_then_school():
    athletes = {"a1": {"display_name": "Isaiah Hammerand", "school_name": "Wartburg"},
                "a2": {"display_name": "Sam Smith", "school_name": "Oklahoma State"}}
    kailo_rows = [{"athlete_key": "a1", "predicted_s": 1450.0},
                  {"athlete_key": "a2", "predicted_s": 1500.0}]
    ours = [(7, K.nameKey("Hammerand, Isaiah"), "Wartburg", 1440.0),
            (8, K.nameKey("Smith, Sam"), "Iowa", 1520.0),
            (9, K.nameKey("Smith, Sam"), "Oklahoma St.", 1490.0)]
    got = K.matchRunners(kailo_rows, athletes, ours)
    assert got == [(1450.0, 7, 1440.0), (1500.0, 9, 1490.0)]
