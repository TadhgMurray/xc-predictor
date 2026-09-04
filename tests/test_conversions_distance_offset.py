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
    assert "_bandOf(rating)" in d and 'm.get((_bare(pool), "TF", dm, 1), 0.0)' in d


def test_bands_match_the_solver():
    import re
    js_src = io.open(os.path.join(ROOT, "engine", "joint_solve.py"),
                     encoding="utf-8").read()
    a = re.search(r"DIST_BANDS = \(([^)]*)\)", js_src).group(1)
    b = re.search(r"_DIST_BANDS = \(([^)]*)\)", SRC).group(1)
    assert a == b, (a, b)


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
    assert "math.exp(distance_offset(pool, \"TF\", dist, rating=rating))" in res
    sql = SRC[SRC.index("_RESULT_SQL = {"):SRC.index("def _norm_from_result")]
    assert "NULL::real, rr.pool" in sql and "r.speed_rating" in sql
    body = _body("_norm_from_result")
    assert "return 100.0 * pm / float(rating)" in body
