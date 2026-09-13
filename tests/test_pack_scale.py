# Project: xc-predictor / tests
# File:    test_pack_scale.py
# Purpose: the pack puts every row on the scale of the pool it is RATED in
#          (speed_ratings.rescaleToPool): a row the backfill normalised as
#          hs_m and the pack pools as college_m is moved by the ratio of
#          the two pool factors and nothing else -- the 230 ratings.
#
#   python -m pytest -q tests/test_pack_scale.py
import os
import sys

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "engine"), os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import normalize_distance as nd                                # noqa: E402
import speed_ratings as sr                                     # noqa: E402
import speed_ratings_db as sdb                                 # noqa: E402

_F = {"hs_m": 3.4, "college_m": 5.474, "ms_m": 2.1, "hs_f": 3.5}


def _fake(t, d, pool, sport=None, **_k):
    f = _F.get(pool)
    return None if f is None else round(t * f, 2)


def _setup(monkeypatch):
    monkeypatch.setattr(nd, "normalizeTime", _fake)
    sr._scaleFactorCache.clear()


def test_a_row_on_another_pools_scale_is_moved_by_the_factor_ratio(monkeypatch):
    _setup(monkeypatch)
    t, d = 242.58, 1609.34                      # a 4:02 mile
    stored = t * _F["hs_m"] * 1.01              # normalised as hs_m, a 1% correction baked in
    new, tag = sr.rescaleToPool(stored, t, d, "college_m", "TF")
    assert tag == "rescaled"
    # the correction survives: only the pool factor changed
    assert abs(new / (t * _F["college_m"] * 1.01) - 1) < 1e-9


def test_a_row_already_on_its_pools_scale_is_untouched(monkeypatch):
    _setup(monkeypatch)
    t, d = 242.58, 1609.34
    stored = t * _F["college_m"] * 0.98
    new, tag = sr.rescaleToPool(stored, t, d, "college_m", "TF")
    assert tag is None and new == stored


def test_an_unidentified_scale_is_left_alone_and_counted(monkeypatch):
    _setup(monkeypatch)
    t, d = 242.58, 1609.34
    stored = t * 4.0                            # no pool's factor
    new, tag = sr.rescaleToPool(stored, t, d, "college_m", "TF")
    assert tag == "scale_not_identified" and new == stored


def test_uncheckable_rows_pass_through(monkeypatch):
    _setup(monkeypatch)
    assert sr.rescaleToPool(800.0, None, 1609.34, "hs_m", "TF") == (800.0, None)
    assert sr.rescaleToPool(800.0, 240.0, 0, "hs_m", "TF") == (800.0, None)
    assert sr.rescaleToPool(800.0, 240.0, None, "hs_m", "TF") == (800.0, None)


def test_the_loader_carries_the_raw_time_where_the_pack_reads_it():
    assert sdb.COLUMNS[sr._TIME] == "time_seconds"
    assert sdb.COLUMNS[sr._DIST] == "dist_m"
    assert "r.time_seconds::real AS time_seconds" in sdb._xcQuery(200, 6000)
    assert "r.time_seconds::real AS time_seconds" in sdb._tfQuery(200, 6000)
