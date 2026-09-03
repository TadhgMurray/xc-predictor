"""The conversions page carries the track distance offset the joint solve
fitted (issues 148 / 150): forward divides by exp(offset), inverse
multiplies, the pool-mean recovery and the stored-result path divide it
out, and everything reads 0 without the table. Text checks: the module
needs a database to import.

    python -m pytest -q tests/test_conversions_distance_offset.py
"""
import io
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = io.open(os.path.join(ROOT, "racecast", "conversions.py"),
              encoding="utf-8").read()


def _body(name):
    i = SRC.index(f"def {name}(")
    return SRC[i:SRC.index("\ndef ", i + 1)]


def test_lookup_degrades_to_zero_and_keys_like_the_engine():
    b = _body("_loadDistanceOffsets")
    assert "FROM distance_offset" in b and "out = {}" in b
    d = _body("distance_offset")
    assert 'if (sport or "").upper() != "TF":' in d
    assert "int(round(float(distance_meters) / 100.0)) * 100" in d
    assert "m.get(key, 0.0)" in d


def test_forward_divides_inverse_multiplies():
    f = _body("_norm_from_time")
    assert "norm = norm / math.exp(off)" in f
    assert f.index("(1.0 + difficulty)") < f.index("math.exp(off)")
    inv = _body("normalized_to_time")
    assert "(1.0 + difficulty) * math.exp(off)" in inv


def test_recovery_and_result_paths_divide_it_out():
    r = _body("_recover_pool_mean")
    assert "/ math.exp(off) / 100.0" in r
    assert "m.distance_meters" in SRC[SRC.index("_MEAN_SQL"):SRC.index("def _recover_pool_mean")]
    res = _body("_norm_from_result")
    assert "math.exp(distance_offset(pool, \"TF\", dist))" in res
    sql = SRC[SRC.index("_RESULT_SQL = {"):SRC.index("def _norm_from_result")]
    assert "NULL::real, NULL::text" in sql and "rr.pool" in sql
