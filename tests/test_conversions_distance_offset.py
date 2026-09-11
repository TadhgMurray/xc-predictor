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
    # continuous in the rating whatever the table holds (issue #21): a
    # missing band borrows its nearest neighbour, never a hard step
    assert "_BAND_ANCHORS" in d and "min(have, key=" in d
    assert "_bandOf(rating)" not in d


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
    assert "adjusted = norm / math.exp(eff)" in f
    assert f.index("(1.0 + difficulty)") < f.index("math.exp(off)")
    # the forward is a SOLVED fixed point (issue #21): the effect divided
    # out is evaluated at the rating the result implies, on both paths
    assert f.count("x = 0.5 * (x + x_new)") == 2
    inv = _body("normalized_to_time")
    assert "(1.0 + difficulty) * math.exp(off)" in inv


def test_recovery_and_result_paths_divide_it_out():
    r = _body("_recover_pool_mean")
    assert "/ math.exp(off) / 100.0" in r
    assert "m.distance_meters" in SRC[SRC.index("_MEAN_SQL"):SRC.index("def _recover_pool_mean")]
    res = _body("_norm_from_result")
    assert "math.exp(distance_offset(pool, \"TF\", dist, rating=rating))" in res
    sql = SRC[SRC.index("_RESULT_SQL = {"):SRC.index("def _norm_from_result")]
    assert "rr.pool" in sql and "r.speed_rating" in sql and "r.rating_pool" in sql
    body = _body("_norm_from_result")
    assert "return 100.0 * pm / float(rating)" in body
    # the '|' test that routed every stored row through its raw time is gone
    assert '"|" not in rating_pool' not in body
