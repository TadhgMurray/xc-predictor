# Project: xc-predictor / tests
# File:    test_impossible_race.py
# Purpose: a time faster than the record condemns its whole race, outside
#          the college pools (owner, 2026-09-14): the pure judgement, the
#          exemption, and that the pack loader, the board query and the
#          fill all carry the anti-join. No database.
#
#   XCP_DB_PASSWORD=x python -m pytest -q tests/test_impossible_race.py
import os
import sys

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "engine"), os.path.join(_ROOT, "scripts"),
           os.path.join(_ROOT, "racecast")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import record_pace as rp                                       # noqa: E402
import impossible_race as ir                                   # noqa: E402
import build_ranking_results as brr                            # noqa: E402
import fill_ratings as fr                                      # noqa: E402
import speed_ratings_db as sdb                                 # noqa: E402


def test_college_pools_are_exempt_in_both_spellings():
    assert rp.exemptPool("college_f") and rp.exemptPool("college_m|TF")
    assert rp.exemptPool("pro_f")                 # professionals too, since master's per-pool floor
    assert not rp.exemptPool("hs_m") and not rp.exemptPool(None)
    assert rp.impossibleRow(738.0, 5000, "F", "hs_f")
    assert not rp.impossibleRow(738.0, 5000, "F", "college_f")


def test_the_prefilter_is_slower_than_every_record_at_every_pools_floor():
    assert ir.PREFILTER_PACE > rp.SLOWEST_RECORD_PACE * max(rp.POOL_PACE_FACTOR.values())
    assert rp.SLOWEST_RECORD_PACE == rp.recordPace(10000, "F")


def test_the_race_rule_uses_the_pools_own_floor():
    # a 1:50 800 (137.5 s/km) is 9% off the open record: fine for a high
    # schooler (floor 1.01), not for a middle-school pool (floor 1.12)
    ms = _row(time_seconds=110.0, distance=800.0, rating_pool="ms_m", event_id=1)
    assert set(ir.judge("TF", [ms])) == {("anet", 10, 3, 1)}
    hs = _row(time_seconds=110.0, distance=800.0, rating_pool="hs_m", event_id=1)
    assert ir.judge("TF", [hs]) == {}
    pro = _row(time_seconds=95.0, distance=800.0, rating_pool="pro_m", event_id=1)
    assert ir.judge("TF", [pro]) == {}                # pro is exempt now too


def test_a_row_without_a_pool_is_college_only_when_it_came_from_tfrrs():
    assert ir.poolIsCollege(None, "tfrrs")
    assert not ir.poolIsCollege(None, "anet")
    assert ir.poolIsCollege("college_m|XC", "anet")
    assert not ir.poolIsCollege("hs_m", "tfrrs")


def _row(**kw):
    base = {"result_id": 1, "source": "anet", "meet_id": 10, "div_id": 3, "event_id": None,
            "time_seconds": 900.0, "distance": 5000.0, "gender": "M", "rating_pool": "hs_m"}
    base.update(kw)
    return base


def test_one_impossible_row_names_its_race_and_a_college_row_does_not():
    rows = [_row(result_id=1, time_seconds=700.0),                       # 140 s/km: impossible
            _row(result_id=2),                                            # same race, fine
            _row(result_id=3, meet_id=11, time_seconds=700.0, rating_pool="college_m"),
            _row(result_id=4, meet_id=12, time_seconds=700.0, source="tfrrs", rating_pool=None),
            _row(result_id=5, meet_id=13, time_seconds=700.0, gender="F", rating_pool="hs_f")]
    bad = ir.judge("XC", rows)
    assert set(bad) == {("anet", 10, 3, None), ("anet", 13, 3, None)}
    hit, = bad[("anet", 10, 3, None)]
    assert hit[0] == 1 and abs(hit[3] - 140.0) < 1e-9
    assert hit[4] == rp.recordPace(5000, "M") * rp.poolFactor("hs_m")


def test_the_track_race_is_the_event_too():
    a = _row(event_id=7, time_seconds=95.0, distance=800.0)     # 119 s/km beats the 800 record
    b = _row(event_id=8, time_seconds=95.0, distance=800.0)
    bad = ir.judge("TF", [a, b])
    assert set(bad) == {("anet", 10, 3, 7), ("anet", 10, 3, 8)}
    assert ir.raceKey("XC", a) == ("anet", 10, 3, None)


class _Cur:
    def __init__(self, have): self.have = have; self.rows = []
    def execute(self, sql, params=None):
        if "to_regclass" in sql:
            self.rows = [("impossible_result" if self.have else None,)]
        else:
            self.rows = [(1,)] if self.have else []
    def fetchone(self): return self.rows[0] if self.rows else None
    def __enter__(self): return self
    def __exit__(self, *a): return False


class _Conn:
    def __init__(self, have): self.have = have
    def cursor(self): return _Cur(self.have)


def test_the_board_query_anti_joins_the_table_when_it_exists():
    for sport in ("XC", "TF"):
        assert "__IMPOSSIBLE__" in brr._SQL[sport]
        with_t = brr._sourceSql(_Conn(True), sport)
        without = brr._sourceSql(_Conn(False), sport)
        assert f"FROM impossible_result ir" in with_t and f"ir.sport = '{sport}'" in with_t
        assert "impossible_result" not in without
        assert "__IMPOSSIBLE__" not in with_t + without


def test_the_fill_prices_through_the_same_query():
    with_t = fr._sqlFor("XC", _Conn(True))
    assert "impossible_result" in with_t
    assert "WHERE r.speed_rating IS NULL AND r.normalized_time > 0" in with_t
    assert "__IMPOSSIBLE__" not in fr._sqlFor("XC")


def test_the_pack_loader_carries_the_anti_join(monkeypatch):
    monkeypatch.setitem(sdb._IMPOSSIBLE, "XC", 12)
    monkeypatch.setitem(sdb._IMPOSSIBLE, "TF", None)
    assert "ir.sport = 'XC'" in sdb._impossibleFilter("XC")
    assert sdb._impossibleFilter("TF") == ""
    src = open(os.path.join(_ROOT, "engine", "speed_ratings_db.py")).read()
    assert src.count("{_impossibleFilter('XC')}") == 1 and src.count("{_impossibleFilter('TF')}") == 1


def test_the_pipeline_finds_the_races_before_the_pack():
    sh = open(os.path.join(_ROOT, "deploy", "run_pipeline.sh")).read()
    assert sh.index("step 06c_impossible") < sh.index("step 07_pack")
    assert "impossible_race.py --write" in sh


class _ProbeCur:
    def execute(self, sql, params=None): pass
    def fetchone(self): return (1,)


def test_the_candidates_are_rated_distance_rows_at_a_race_distance():
    # run 23: the 60m dash read as 60 km condemned 5.8M track rows
    for sport in ("XC", "TF"):
        sql = ir.candidateSql(_ProbeCur(), sport)
        assert "r.normalized_time IS NOT NULL" in sql
        lo, hi = ir.XC_DIST if sport == "XC" else ir.TF_DIST
        assert f"BETWEEN {lo} AND {hi}" in sql
    assert ir.TF_DIST[0] >= 800 and ir.XC_DIST[1] <= 20000
