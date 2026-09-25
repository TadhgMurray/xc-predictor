"""The model reads every normalized_time on ONE anchor (owner, 2026-09-25:
"can you implement the one scale thing?"). No database: a Riegel-shaped
stub curve stands in for the spline artifact.

    python -m pytest -q tests/test_model_one_scale.py
"""
import math
import os
import pickle
import sys
import types

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
os.environ.setdefault("XCP_DB_QUIET", "1")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "model")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
if "corrections" not in sys.modules:            # 165 MB, not in git
    _c = types.ModuleType("corrections")
    _c.distanceOverrideSQL = lambda *a, **k: ("", "")
    _c.__getattr__ = lambda name: {}
    sys.modules["corrections"] = _c

import pytest                                                # noqa: E402
import normalize_distance as nd                              # noqa: E402

K = [math.log(800), math.log(20000)]
ENTRY = {"knots": K, "values": [1.07 * k for k in K]}        # g = 1.07 log d
ART = {"kind": "distance_potential", "target": 5000.0,
       "pools": {"hs_m": ENTRY, "college_m": ENTRY, "ms_m": ENTRY},
       "global": ENTRY,
       "pool_targets": {"hs_m": 5000.0, "college_m": 8000.0, "ms_m": 3200.0}}


@pytest.fixture
def art(monkeypatch):
    monkeypatch.setattr(nd, "_SPLINES", ART)
    monkeypatch.setattr(nd, "_SHIFT_CACHE", {})


def test_the_shift_undoes_the_pool_anchor(art):
    """A college 8K-equivalent and a high-school 5K-equivalent of the same
    run land on the same number once both are on 5000 m."""
    t, d = 900.0, 5000.0                      # one 15:00 5K
    for pool in ("hs_m", "college_m", "ms_m"):
        anchor = ART["pool_targets"][pool]
        norm_pool = nd._normalizeWithPotential(t, d, pool)
        assert norm_pool == pytest.approx(t * (anchor / d) ** 1.07)
        assert norm_pool * nd.anchorShift(pool, "XC") == pytest.approx(t)
    assert nd.anchorShift("hs_m", "XC") == 1.0


def test_no_artifact_moves_nothing(monkeypatch):
    monkeypatch.setattr(nd, "_SPLINES", None)
    assert nd.anchorShift("college_m", "XC") == 1.0


def test_normpoolfor_is_the_backfills_precedence(monkeypatch):
    seen = []
    monkeypatch.setattr(nd, "poolFor", lambda g, gen, src, sch, season_level=None:
                        seen.append((g, season_level)) or "x")
    nd.normPoolFor("11", "M", "anet", "S", season_level="ms")
    nd.normPoolFor(None, "M", "anet", "S", season_level="ms")
    nd.normPoolFor("11", "M", "anet", "S", season_level="ms", fixed=("9", "hs"))
    nd.normPoolFor("11", "M", "anet", "S", season_level="ms", fixed=(None, "college"))
    assert seen == [("11", None), (None, "ms"), ("9", None), (None, "college")]
    src = open(os.path.join(_ROOT, "backfill", "backfill_normalize.py")).read()
    assert "pool = normPoolFor(row[_GRADE], gender, row[_SRC], row[_SCHOOL]," in src


def _fx(monkeypatch):
    """feature_extraction, importable without torch/sklearn (it only saves
    tensors with them; toCommonScale needs neither)."""
    for name in ("torch", "sklearn", "sklearn.preprocessing"):
        try:
            __import__(name)
        except ImportError:
            mod = types.ModuleType(name)
            mod.LabelEncoder = object
            monkeypatch.setitem(sys.modules, name, mod)
    import feature_extraction as fx
    return fx


def test_extraction_moves_each_row_with_its_own_pool(art, monkeypatch):
    fx = _fx(monkeypatch)
    monkeypatch.setattr(fx, "NORM_SCALE", "common5000")
    monkeypatch.setattr(nd, "poolFor",
                        lambda g, gen, src, sch, season_level=None:
                        "college_m" if g == "SO-2" else "hs_m")
    col = fx.toCommonScale({"normalized_time": 1500.0, "grade": "SO-2",
                            "gender": "M", "is_xc": True})
    hs = fx.toCommonScale({"normalized_time": 900.0, "grade": "11",
                           "gender": "M", "is_xc": True})
    assert col["normalized_time_pool"] == 1500.0
    assert col["normalized_time"] == pytest.approx(1500.0 * (5000 / 8000) ** 1.07)
    assert hs["normalized_time"] == 900.0
    old = fx.toCommonScale({"normalized_time": 1500.0, "grade": "SO-2"},
                           scale="pool")
    assert old["normalized_time"] == 1500.0          # an old model's input


def test_inference_puts_a_common_time_back_on_the_pool_anchor(art, monkeypatch):
    import predict as P
    monkeypatch.setattr(P, "_artifacts", {"norm_scale": "common5000"})
    import normalize_distance
    monkeypatch.setattr(normalize_distance, "getPool", lambda g, gen: "college_m")
    ctx = P._denormContext({"distance_meters": 8000, "sport": "XC",
                            "date": "2026-10-01"}, {"grade": "SO-2", "gender": "M"})
    assert ctx["scale_shift"] == pytest.approx((5000 / 8000) ** 1.07)
    seen = {}
    import conversions
    monkeypatch.setattr(conversions, "normalized_to_time",
                        lambda n, c: seen.setdefault("n", n))
    P._raceSeconds(800.0, ctx)
    assert seen["n"] == pytest.approx(800.0 / ctx["scale_shift"])


def test_the_scale_rides_from_extraction_to_inference():
    src = open(os.path.join(_ROOT, "model", "train.py")).read()
    assert 'NORM_SCALE_SEEN = meta.get("norm_scale", "pool")' in src
    assert '"norm_scale": NORM_SCALE_SEEN}' in src
    p = open(os.path.join(_ROOT, "racecast", "predict.py")).read()
    assert '"norm_scale": stats.get("norm_scale", "pool"),' in p
