"""model/race_rows.py's pure parts: no input reads the race or anything
after it, the season and opener inputs, and the field's centring. No
database.

    python -m pytest -q tests/test_race_rows.py
"""
import datetime
import math
import os
import sys

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "model")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest                                                # noqa: E402
import race_rows as R                                        # noqa: E402

D = datetime.date


def _hist():
    return [(D(2024, 9, 7), 100.0, "hs_m", "XC"), (D(2024, 10, 5), 104.0, "hs_m", "XC"),
            (D(2024, 11, 2), 106.0, "hs_m", "XC"), (D(2025, 4, 12), 108.0, "hs_m", "TF"),
            (D(2025, 9, 6), 107.0, "hs_m", "XC"), (D(2025, 9, 20), 109.0, "hs_m", "XC"),
            (D(2025, 10, 4), 111.0, "hs_m", "XC")]


def _same(a, b):
    return all((math.isnan(a[k]) and math.isnan(b[k])) or a[k] == b[k] for k in a)


def test_no_input_reads_the_day_or_after():
    day = D(2025, 10, 11)
    base = R.runnerFeatures(_hist(), day, {"pool": "hs_m", "grade": "11"})
    later = _hist() + [(day, 140.0, "hs_m", "XC"), (D(2025, 11, 1), 150.0, "hs_m", "XC")]
    again = R.runnerFeatures(later, day, {"pool": "hs_m", "grade": "11"})
    assert base[0] == again[0] and _same(base[2], again[2])


def test_the_season_and_last_season():
    _r, _p, f = R.runnerFeatures(_hist(), D(2025, 10, 11), {"pool": "hs_m", "grade": "11"})
    assert f["season_n"] == 3 and f["opener"] == 0.0
    assert f["season_slope"] > 0                         # 107 -> 111 over four weeks
    assert f["prev_n"] == 3 and f["last_is_tf"] == 0.0
    assert f["prev_final_minus_form"] < 0                # last November's 106 is under today's form
    assert f["grade"] == 11.0 and f["level"] == 2.0 and f["level_change"] == 0.0


def test_a_season_opener_off_a_spring_of_track():
    _r, _p, f = R.runnerFeatures(_hist()[:4], D(2025, 9, 6), {"pool": "hs_m", "grade": "11"})
    assert f["opener"] == 1.0 and f["season_n"] == 0
    assert f["last_is_tf"] == 1.0 and f["tf_n365"] == 1
    assert math.isnan(f["season_mean_minus_form"])


def test_a_first_college_race():
    _r, pool, f = R.runnerFeatures(_hist(), D(2026, 9, 5), {"pool": "college_m", "grade": "FR"})
    assert pool == "hs_m" and f["grade"] == 13.0
    assert f["level"] == 3.0 and f["form_level"] == 2.0 and f["level_change"] == 1.0


def test_nothing_before_the_day_is_no_row():
    assert R.runnerFeatures(_hist(), D(2024, 9, 7), {"pool": "hs_m"}) is None


def test_meet_types():
    assert R.meetType("Nike Cross Nationals") == 6
    assert R.meetType("CIF State Cross Country Championships") == 5
    assert R.meetType("Big Ten Conference Championship") == 4
    assert R.meetType("Mt. SAC Invitational") == 2
    assert R.meetType("Ipswich vs Masconomet dual") == 1
    assert R.meetType("League JV Championship") == 0
    assert R.meetType("Saturday Meet") == 3


def test_the_field_is_centred_and_teammates_read_each_other():
    def runner(secs, base, school, slope):
        f = {k: math.nan for k in R.RUNNER_FEATURES}
        f.update(season_slope=slope, prev_final_minus_form=-0.01, opener=0.0)
        return {"secs": secs, "base_ln": base, "school": school, "f": f}
    rs = [runner(1000, 6.90, "A", 0.01), runner(1010, 6.91, "A", 0.03),
          runner(1020, 6.92, "B", 0.00), runner(1030, 6.93, "B", 0.02)]
    R.raceFeatures(rs, {"day": D(2025, 10, 11), "meet_name": "X Invitational",
                        "source": "anet", "distance": 5000, "cell": 0.04})
    assert sum(r["y"] for r in rs) == pytest.approx(0, abs=1e-9)
    assert sum(r["base"] for r in rs) == pytest.approx(0, abs=1e-9)
    assert rs[0]["f"]["team_slope"] == 0.03 and rs[0]["f"]["team_n"] == 1
    assert rs[0]["f"]["rank_pct"] == 0.0 and rs[3]["f"]["rank_pct"] == 1.0
    assert rs[0]["f"]["meet_type"] == 2 and rs[0]["f"]["has_cell"] == 1.0
    assert rs[2]["y_abs"] == pytest.approx(math.log(1020))
    assert set(rs[0]["f"]) == set(R.FEATURES)


def test_races_are_kept_whole():
    k = (1, 2, "anet", "2020-09-01")
    assert R.keepRace(k, D(2020, 9, 1), D(2024, 8, 1), 0.25) == \
        R.keepRace(k, D(2020, 9, 1), D(2024, 8, 1), 0.25)
    assert R.keepRace(k, D(2024, 9, 1), D(2024, 8, 1), 0.0)


def test_a_race_distance_is_the_rows_median_and_never_a_unit_slip():
    assert R.raceDistance([5000.0, 5000.0, 4990.0]) == 5000.0
    assert R.raceDistance([2.0, 3.0]) is None                # miles, or a typo
    assert R.raceDistance([2.0, 8000.0, 8000.0]) == 8000.0
    assert R.raceDistance([]) is None and R.raceDistance(None) is None


def test_head_to_head_reads_only_past_meetings_and_beyond_the_ratings():
    h1, h2 = R.raceHash(1, 1, "anet", "2025-09-06"), R.raceHash(2, 1, "anet", "2025-09-13")
    # the ratings now put runner 1 ahead of runner 0, but 0 beat 1 twice
    rs = [{"base_ln": 6.91, "past": [(h1, 6.90), (h2, 6.89)]},
          {"base_ln": 6.90, "past": [(h1, 6.92), (h2, 6.93)]},
          {"base_ln": 6.95, "past": []}]
    got = R.headToHead(rs)
    assert got[0] == (2, 1.0, 1.0) and got[1] == (2, 0.0, -1.0)
    assert 2 not in got                                     # never met anyone here
    assert R.raceHash(1, 1, "anet", "2025-09-06 00:00") == h1


def test_raw_times_are_riegel_only_and_never_read_the_day():
    t5 = math.log(1000.0)
    raw = [(D(2025, 9, 6), t5), (D(2025, 9, 20), t5 - 0.02), (D(2025, 10, 11), t5 - 0.5)]
    f = R.rawFeatures(raw, D(2025, 10, 11))
    assert f["raw_n"] == 2 and f["raw_last_minus"] < 0       # the day's own -0.5 unseen
    assert f["raw_season_best_minus"] == pytest.approx(t5 - 0.02 - f["raw_form"])
    assert math.isnan(R.rawFeatures([], D(2025, 10, 11))["raw_form"])
    # the raw clock carries raw_form to the race distance by Riegel
    rs = []
    for k, rf in enumerate((t5, t5 + 0.01, math.nan)):
        f = {x: math.nan for x in R.RUNNER_FEATURES}
        f.update(opener=0.0, raw_form=rf)
        rs.append({"secs": 1000.0 + k, "base_ln": 7.0 + k / 100, "school": None, "f": f})
    R.raceFeatures(rs, {"day": D(2025, 10, 11), "distance": 8000.0})
    assert rs[0]["base_raw_abs"] == pytest.approx(t5 + R.RIEGEL * math.log(1.6))
    assert rs[2]["base_raw_abs"] == pytest.approx(t5 + 0.005 + R.RIEGEL * math.log(1.6))
    assert sum(r["base_raw"] for r in rs) == pytest.approx(0.0, abs=1e-9)
