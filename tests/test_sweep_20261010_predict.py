# Project: xc-predictor / tests
# File:    test_sweep_20261010_predict.py
# Purpose: the 2026-10-10 sweep's inference-side fixes: the denormalisation
#          pool is the rated pool when the season has not moved (2), and a
#          target a season on carries the advanced grade with last season's
#          verdicts cleared, in the context row AND on the clock (5).
#
#     python -m pytest -q tests/test_sweep_20261010_predict.py
import os
import sys

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "model")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import predict as P                                          # noqa: E402

_SPEC = {"distance_meters": 8000, "sport": "XC", "is_xc": True,
         "date": "2026-10-03", "course_difficulty": 0.0}


def test_same_season_denorm_uses_the_rated_pool():
    # a senior rated in the college pool this season (anchor_repair put the
    # stored times there); the grade alone would say hs_m
    last = {"grade": "12", "gender": "M", "date": "2026-09-20",
            "rating_pool": "college_m|XC"}
    assert P._denormContext(_SPEC, last)["pool"] == "college_m"
    # a pro pool's scale is its college twin's
    last["rating_pool"] = "pro_m|XC"
    assert P._denormContext(_SPEC, last)["pool"] == "college_m"
    # no rating pool: the grade, as before
    del last["rating_pool"]
    assert P._denormContext(_SPEC, last)["pool"] == "hs_m"


def test_a_season_on_the_clock_reads_the_advanced_grade():
    # a spring junior predicted in the autumn: a senior, hs either way, but
    # last spring's rating pool no longer speaks for the new season
    last = {"grade": "11", "gender": "M", "date": "2026-05-01",
            "rating_pool": "college_m|XC"}
    assert P._denormContext(_SPEC, last)["pool"] == "hs_m"


def test_the_target_row_drops_last_seasons_verdicts():
    last = {"grade": "10", "gender": "M", "date": "2026-05-01",
            "grade_untrusted": True, "fixed_grade": "9", "fixed_level": None,
            "season_level": "ms", "rating_pool": "hs_m|TF",
            "is_pro": False, "college_first": None}
    row = P._targetRow(dict(_SPEC, distance_meters=5000.0), last)
    assert row["grade"] == "11"
    for f in ("grade_untrusted", "fixed_grade", "fixed_level",
              "season_level", "rating_pool"):
        assert row[f] is None, f
    # the last row itself is untouched
    assert last["fixed_grade"] == "9" and last["season_level"] == "ms"


def test_the_same_season_keeps_its_verdicts():
    last = {"grade": "10", "gender": "M", "date": "2026-09-05",
            "grade_untrusted": True, "fixed_grade": "9", "season_level": "hs"}
    row = P._targetRow(dict(_SPEC, distance_meters=5000.0), last)
    assert row["grade"] == "10"
    assert row["grade_untrusted"] is True and row["fixed_grade"] == "9"
    assert row["season_level"] == "hs"
