# The predictions page's rating column is the predicted race's own rating
# (owner, 2026-10-28: "why is the rating the season rating and not the
# rating of that predicted race?" -- a 140.9 predicted ahead of a 141.3).
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for sub in ("racecast", "engine", "scripts", ""):
    sys.path.insert(0, os.path.join(_ROOT, sub))
os.environ.setdefault("XCP_DB_PASSWORD", "x")

import predict                                                 # noqa: E402


def test_the_column_is_the_time_read_back_on_the_course():
    field = [{"person_id": 1, "name": "Palmer", "school": "Middlebury", "rating": 140.9,
              "pool": "college_f"},
             {"person_id": 2, "name": "Byl-Brann", "school": "Williams", "rating": 141.3,
              "pool": "college_f"}]
    # rating-based times the race model refined (basis stays 'rating'),
    # each with the rating its time is worth here
    preds = [{"seconds": 1320.4, "basis": "rating", "form_rating": 140.9,
              "form_pool": "college_f", "time_rating": 142.0, "time_pool": "college_f"},
             {"seconds": 1324.5, "basis": "rating", "form_rating": 141.3,
              "form_pool": "college_f", "time_rating": 141.6, "time_pool": "college_f"}]
    _teams, finishers = predict._score(field, preds)
    by = {r["name"]: r for r in finishers}
    assert by["Palmer"]["rating"] == 142.0 and by["Byl-Brann"]["rating"] == 141.6
    assert by["Palmer"]["season_rating"] == 140.9          # the season figure rides beside
    # a time that could not be read back keeps the form rating, as before
    preds2 = [dict(preds[0], time_rating=None), dict(preds[1], time_rating=None)]
    _t, fin2 = predict._score(field, preds2)
    assert {r["name"]: r["rating"] for r in fin2} == {"Palmer": 140.9, "Byl-Brann": 141.3}


def test_every_runner_is_read_on_the_race_s_pool(monkeypatch):
    # owner, 2026-10-31: 25:17.0 rated 136.0 above 25:18.4 rated 136.8 -- one
    # runner's time read on another pool than the field's
    seen = []

    def fake(seconds, pool, spec, season):
        seen.append(pool)
        return round(200000.0 / seconds, 1)          # faster -> higher, one scale
    monkeypatch.setattr(predict, "_secondsToRating", fake)
    preds = [{"seconds": 1517.0, "is_race_time": True, "form_pool": "open_m"},
             {"seconds": 1518.4, "is_race_time": True, "form_pool": "college_m"},
             {"seconds": 1532.1, "is_race_time": True, "time_pool": "college_m"},
             {"seconds": 1100.0, "is_race_time": True, "form_pool": "college_f"},
             {"seconds": None, "is_race_time": True, "form_pool": "college_m"}]
    predict._stampRaceRatings(preds, {"date": "2026-11-01"})
    assert seen == ["college_m", "college_m", "college_m", "college_f"]
    r = [p.get("time_rating") for p in preds]
    assert r[0] > r[1] > r[2] and r[4] is None          # the order is the times'
    assert preds[0]["race_pool"] == "college_m" and preds[3]["race_pool"] == "college_f"
