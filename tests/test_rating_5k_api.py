# Project: xc-predictor / tests
# File:    test_rating_5k_api.py
# Purpose: /api/rating_5k -- a rating as a 5K time (owner, 2026-09-29: "For
#          the ratings, should we attach 5K times to them?"), and the
#          equivalents card by ability converting on the HS twin with no
#          span shopping. No database: the pool means are stubbed.
#
#   XCP_DB_PASSWORD=x python -m pytest -q tests/test_rating_5k_api.py
import os
import sys

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
os.environ.setdefault("XCP_DB_QUIET", "1")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "scripts"),
           os.path.join(_ROOT, "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest                                                 # noqa: E402
import app as A                                               # noqa: E402
import conversions as cv                                      # noqa: E402
import normalize_distance as nd                               # noqa: E402

_MEAN = {"hs_m": 1000.0, "college_m": 900.0, "hs_f": 1200.0}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(cv, "pool_mean", lambda pool, sport=None: _MEAN.get(pool.split("|")[0]))
    monkeypatch.setattr(cv, "engineScale", lambda pool, sport=None: None)
    monkeypatch.setattr(cv, "distance_offset", lambda *a, **k: 0.0)
    monkeypatch.setitem(cv._DEFAULT_DIFFICULTY_CACHE, "XC", 0.05)
    monkeypatch.setitem(cv._DEFAULT_DIFFICULTY_CACHE, "TF", 0.0)
    A._RATING5K_CACHE.clear()
    return A.app.test_client()


def test_a_rating_is_a_5k(client):
    body = client.get("/api/rating_5k", query_string={"rating": 150, "pool": "hs_m"}).get_json()
    # 100 * mean / rating on a neutral course; the average XC course is 5% slower
    assert body["norm"] == pytest.approx(100 * 1000.0 / 150, abs=0.01)
    assert body["xc"] == pytest.approx(body["norm"] * 1.05, rel=1e-3)
    assert body["track"] == pytest.approx(body["norm"], rel=1e-3)


def test_an_hs_equivalent_reads_on_the_hs_scale(client):
    own = client.get("/api/rating_5k", query_string={"rating": 150, "pool": "college_m"}).get_json()
    hs = client.get("/api/rating_5k", query_string={"rating": 150, "pool": "college_m",
                                                   "scale": "hs"}).get_json()
    assert hs["pool"] == "hs_m" and own["pool"] == "college_m"
    assert own["norm"] == pytest.approx(100 * 900.0 / 150, abs=0.01)
    assert hs["norm"] == pytest.approx(100 * 1000.0 / 150, abs=0.01)


def test_bad_requests_are_refused(client):
    for q in ({"rating": 5, "pool": "hs_m"}, {"rating": 150, "pool": "nope"},
              {"rating": 150, "pool": "hs_m", "scale": "college"}):
        r = client.get("/api/rating_5k", query_string=q)
        assert r.status_code == 400 and "error" in r.get_json()


def test_by_ability_the_card_converts_on_the_hs_twin(monkeypatch):
    monkeypatch.setattr(nd, "_ABILITY", {"kind": "distance_ability", "families": {}})
    assert A._equivOnHs("hs_m", 10000, "XC") == "hs_m"
    assert A._equivOnHs("college_f", 10000, "XC") == "hs_f"
