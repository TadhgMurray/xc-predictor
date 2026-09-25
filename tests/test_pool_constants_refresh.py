"""Step 13b must MEASURE the pool constants, not read them back from disk.

⚠⚠ 2026-09-25: after the ability gate went live, pro_m's HS-equivalent
   factor was still x0.7491 -- a number measured on the contaminated pool.
   warm_pool_constants called _poolConstant, which answers from the JSON
   sidecar for its week-long TTL, so the step that follows every solve
   re-saved the previous solve's constants.

    python -m pytest -q tests/test_pool_constants_refresh.py
"""
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in ("scripts", "engine", "racecast"):
    sys.path.insert(0, os.path.join(_ROOT, _d))
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
os.environ.setdefault("XCP_DB_QUIET", "1")

import pool_view as PV                                          # noqa: E402


def _fake(fresh, seen):
    """A _poolConstant that measures `fresh` -- and records whether the
    cache still held an answer when it was asked (it must not)."""
    def f(pool, sport):
        key = (pool, sport)
        seen.append(key in PV._CONST_CACHE)
        v = fresh.get(key)
        if v is not None:
            PV._CONST_CACHE[key] = v
        return v
    return f


def test_a_refresh_measures_the_database_not_the_sidecar(monkeypatch, tmp_path):
    monkeypatch.setattr(PV, "_constFile", lambda: str(tmp_path / "c.json"))
    monkeypatch.setattr(PV, "_CONST_CACHE", {("pro_m", "XC"): 1553.1,
                                             ("hs_m", "XC"): 1211.5})
    monkeypatch.setattr(PV, "_FACTOR_CACHE", {("pro_m", "XC", 5000): 0.78})
    seen = []
    monkeypatch.setattr(PV, "_poolConstant",
                        _fake({("pro_m", "XC"): 870.0,
                               ("hs_m", "XC"): 1211.0}, seen))
    got = PV.refreshConstants(["hs_m", "pro_m"], sports=("XC",))
    assert seen and not any(seen), "the stale cache answered"
    assert PV._CONST_CACHE[("pro_m", "XC")] == 870.0
    assert PV._FACTOR_CACHE == {}                 # factors re-derive too
    assert ("pro_m", "XC", 1553.1, 870.0, False) in got
    on_disk = json.load(open(tmp_path / "c.json"))
    assert on_disk["pro_m|XC"] == 870.0


def test_a_failed_sample_keeps_the_old_value_and_says_so(monkeypatch, tmp_path):
    monkeypatch.setattr(PV, "_constFile", lambda: str(tmp_path / "c.json"))
    monkeypatch.setattr(PV, "_CONST_CACHE", {("college_m", "TF"): 300.0})
    monkeypatch.setattr(PV, "_FACTOR_CACHE", {})
    monkeypatch.setattr(PV, "_poolConstant", _fake({}, []))
    got = PV.refreshConstants(["college_m"], sports=("TF",))
    assert got == [("college_m", "TF", 300.0, 300.0, True)]
    assert PV._CONST_CACHE[("college_m", "TF")] == 300.0


def test_the_pipeline_step_refreshes():
    src = open(os.path.join(_ROOT, "scripts", "warm_pool_constants.py")).read()
    assert "pool_view.refreshConstants(" in src
    assert "_poolConstant(" not in src
