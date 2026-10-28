"""racecast/race_model.py: grades carried forward, one field through fitted
trees, and XCP_PREDICT_BASIS=race in _servedTimes. No database.

    python -m pytest -q tests/test_race_model.py
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
import race_model as M                                       # noqa: E402

D = datetime.date


def test_grades_carry_forward_by_season():
    assert M.gradeAt("10", D(2024, 10, 1), D(2026, 9, 5)) == "12"
    assert M.gradeAt("12", D(2025, 10, 1), D(2026, 9, 5)) == "FR"
    assert M.gradeAt("SO", D(2025, 10, 1), D(2026, 9, 5)) == "JR"
    assert M.gradeAt("SR", D(2025, 10, 1), D(2027, 9, 5)) == "SR"
    assert M.gradeAt("11", D(2026, 9, 1), D(2026, 10, 5)) == "11"     # same season
    assert M.gradeAt("", D(2026, 9, 1), D(2026, 10, 5)) is None


def test_a_college_race_makes_a_high_school_form_a_first_college_season():
    assert M._targetPool("hs_f", {"source": "tfrrs"}) == "college_f"
    assert M._targetPool("college_m", {"source": "tfrrs"}) == "college_m"
    assert M._targetPool("hs_m", {"source": "anet"}) == "hs_m"


def _model():
    np = pytest.importorskip("numpy")
    pytest.importorskip("sklearn")
    from sklearn.ensemble import HistGradientBoostingRegressor
    import race_rows as RR
    import race_trees as RT
    rng = np.random.default_rng(0)
    X = rng.normal(size=(400, len(RR.FEATURES))).astype(np.float32)
    zero = HistGradientBoostingRegressor(max_iter=5).fit(X, np.zeros(400))
    L = rng.normal(size=(400, len(RT.LEVEL_FEATURES))).astype(np.float32)
    lvl = HistGradientBoostingRegressor(max_iter=5).fit(L, np.full(400, 0.05))
    return {"runner": zero, "level": lvl, "features": list(RR.FEATURES),
            "level_features": list(RT.LEVEL_FEATURES),
            "sigmas": {"_all": 0.02, "_level": 0.015, RT.BANDS[0]: 0.018}}


def _field(n=10):
    import race_rows as RR
    out = []
    for i in range(n):
        hist = [(D(2026, 9, 6), 100.0 + i, "hs_m", "XC"), (D(2026, 9, 20), 101.0 + i, "hs_m", "XC")]
        _r, _p, f = RR.runnerFeatures(hist, D(2026, 10, 4), {"pool": "hs_m", "grade": "11"})
        out.append({"pid": i, "school": f"S{i % 3}", "base_ln": math.log(1000.0 - 5 * i), "f": f})
    return out


def test_one_field_through_the_trees():
    model = _model()
    race = {"distance": 5000.0, "day": D(2026, 10, 4), "meet_name": "X Invitational",
            "source": "anet", "cell": 0.03, "page_base": True}
    got = M.predictField(model, _field(), race)
    secs = [got[i]["seconds"] for i in range(10)]
    assert secs == sorted(secs, reverse=True)           # a zero correction keeps the order
    mean_ln = sum(math.log(1000.0 - 5 * i) for i in range(10)) / 10
    assert math.exp(sum(math.log(s) for s in secs) / 10) == pytest.approx(
        math.exp(mean_ln + 0.05), rel=2e-3)              # the level moves the whole field
    e = got[0]
    assert e["basis"] == "race" and e["sigma_pct"] == pytest.approx(1.8)
    assert e["hi"] / e["seconds"] == pytest.approx(math.exp(math.hypot(0.018, 0.015)), rel=1e-3)


def test_basis_race_replaces_the_rated_runners_only(monkeypatch):
    import predict as P
    rated = {1: 900.0, 2: 950.0}
    monkeypatch.setattr(P, "_targetSpec", lambda cur, t: {"date": "2026-10-24", "sport": "XC"})
    monkeypatch.setattr(P, "_ratingTimes", lambda cur, ids, spec, cut: {
        p: {"seconds": s, "lo": s * 0.97, "hi": s * 1.03, "sigma_pct": 3.0,
            "is_race_time": True, "basis": "rating", "form_pool": "hs_m"} for p, s in rated.items()})
    monkeypatch.setattr(P, "_predictTimes", lambda cur, ids, target, spec=None: [
        {"seconds": 1200.0, "sigma_pct": 3.0, "is_race_time": True} for _i in ids])
    monkeypatch.setattr(M, "predictRace", lambda cur, ids, spec, cut, rated: {
        1: {"seconds": 910.0, "basis": "race"}, 2: {"seconds": 940.0, "basis": "race"}})
    monkeypatch.setenv("XCP_PREDICT_BASIS", "race")
    out = P._servedTimes(None, [1, 2, 3], {"mode": "rerun"})
    assert [e["seconds"] for e in out] == [910.0, 940.0, 1200.0]
    assert out[0]["basis"] == "race" and out[0]["form_pool"] == "hs_m"


def test_no_model_file_leaves_the_ratings(monkeypatch, tmp_path):
    monkeypatch.setattr(M, "MODEL_PATH", str(tmp_path / "none.pkl"))
    assert M.predictRace(None, [1, 2, 3], {"distance_meters": 5000}, D(2026, 10, 4), {}) == {}


class _Cur:
    """Records statements; raises on any that is not a savepoint."""
    def __init__(self):
        self.sql = []
        self.connection = self

    def execute(self, sql, args=None):
        self.sql.append(sql.split()[0] + " " + sql.split()[-1])
        if "SAVEPOINT" not in sql:
            raise RuntimeError("bad statement")

    def rollback(self):
        self.sql.append("ROLLBACK")


def test_a_failed_read_rolls_back_to_its_own_savepoint_only(monkeypatch):
    import predict as P
    cur = _Cur()
    monkeypatch.setattr(P, "_divisionLabel", lambda c, *a: c.execute("SELECT x"))
    assert M._division(cur, {"meet_id": 1}) is None
    assert cur.sql == ["SAVEPOINT race_div", "SELECT x", "ROLLBACK race_div",
                       "RELEASE race_div"]            # never the caller's transaction


def test_the_forecast_is_asked_without_the_cursor(monkeypatch):
    # forecast.py's table cache commits, which ends the caller's savepoints
    import predict as P
    seen = {}

    class _Fc:
        @staticmethod
        def raceHour(sport):
            return 9

        @staticmethod
        def forecastAt(lat, lon, day, hour, cur=None):
            seen["cur"] = cur
            return {"temp_c": 12.0}
    monkeypatch.setattr(P, "_fc", lambda: _Fc)
    assert M._weather(_Cur(), {"gps_lat": 1.0, "gps_long": 2.0, "date": "2026-10-31"}) == \
        {"temp_c": 12.0}
    assert seen["cur"] is None
