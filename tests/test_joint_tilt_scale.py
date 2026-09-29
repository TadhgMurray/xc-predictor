"""The tilt on one scale (owner, 2026-09-29): under --tilt-scale hs /
XCP_TILT_SCALE=hs the course tilt reads the HS-equivalent rating, so the
same athlete is charged the same course whatever pool label they carry.
Off ('own') is the shipped tilt, bit for bit.

The world: two pools on shared courses, college_m normalised in different
units (x1.6, an 8k against a 5k) and faster on average; one twin in each
pool with the SAME ability in HS units.

    python -m pytest -q tests/test_joint_tilt_scale.py
"""
import os
import sys
import types

import numpy as np
import pytest

_ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(_ROOT, "engine"))
sys.path.insert(0, os.path.join(_ROOT, "racecast"))
import joint_solve as js                                        # noqa: E402
import run_joint as rj                                          # noqa: E402

F_UNITS = 1.6            # college_m's normalised time in hs_m's units


def world(seed=0, n_hs=300, n_col=200, races=12, twin_races=40, noise=0.01):
    rng = np.random.default_rng(seed)
    n_cell = 30
    d_true = rng.normal(0, 0.04, n_cell)
    a_hs_units = np.r_[rng.normal(0.0, 0.08, n_hs), rng.normal(-0.10, 0.06, n_col)]
    pool = np.r_[np.zeros(n_hs, dtype=np.int64), np.ones(n_col, dtype=np.int64)]
    twin_hs, twin_col = 0, n_hs                  # the two twins
    a_hs_units[twin_hs] = a_hs_units[twin_col] = -0.15
    a = a_hs_units + np.where(pool == 1, np.log(F_UNITS), 0.0)
    ath, cel = [], []
    for i in range(n_hs + n_col):
        k = twin_races if i in (twin_hs, twin_col) else races
        ath += [i] * k
        cel += list(rng.integers(0, n_cell, k))
    ath = np.array(ath); cel = np.array(cel)
    rac = cel * 7 + rng.integers(0, 7, ath.size)        # 7 days per course
    y = a[ath] + d_true[cel] + rng.normal(0, noise, ath.size)
    return dict(y=y, ath=ath, cel=cel, rac=rac, pool=pool, twins=(twin_hs, twin_col))


def _inputs():
    """tiltScaleInputs with the distance factors of this world."""
    def factor_fn(d, pool, sport):
        return F_UNITS if pool == "college_m" else 1.0
    return rj.tiltScaleInputs(["hs_m", "college_m"], factor_fn=factor_fn,
                              verbose=False)


def _solve(w, scale):
    D = js.Design(w["ath"], w["cel"], w["rac"])
    return js.solveJoint(w["y"], design=D, athlete_pool=w["pool"], n_outer=3,
                         tilt=True, n_probe=0, robust=False, sigma_u_floor=0.0,
                         tilt_scale=scale), D


def _h_of(out, D, i):
    return float(out["h"][np.flatnonzero(D.athlete == i)[0]])


def test_the_factor_is_the_site_formula():
    """C(hs)/C(pool) x F(pool)/F(hs): pool means read back off the ratings."""
    a = np.array([0.0, 0.1, -0.1, 0.5, 0.6, 0.4])
    pool = np.array([0, 0, 0, 1, 1, 1])
    pm = np.array([1.0, 1.3])                       # each pool's mean, own units
    rating = 100 * pm[pool] / np.exp(a)
    fac = js.tiltPoolFactors(rating, a, pool, 2, np.array([0, 0]),
                             np.array([1.0, 1.5]))
    assert fac[0] == pytest.approx(1.0)
    assert fac[1] == pytest.approx(1.0 / 1.3 * 1.5)


def test_the_same_hs_equivalent_gets_the_same_tilt():
    """The unit statement: two pools, one HS-equivalent rating, one h."""
    r_hs, fac_col = 150.0, 1.2063                  # the owner's college_m factor
    r_col = r_hs / fac_col                         # 124.3 on its own scale
    h_own = js.tiltRows(np.array([r_hs, r_col]))
    h_hs = js.tiltRows(np.array([r_hs, r_col]), np.array([1.0, fac_col]))
    assert abs(h_own[0] - h_own[1]) > 0.07          # 150 against 124.3
    assert h_hs[0] == pytest.approx(h_hs[1])


def test_the_solve_charges_the_twins_alike_under_hs_only():
    w = world()
    out_own, D = _solve(w, None)
    out_hs, _ = _solve(w, _inputs())
    t_hs, t_col = w["twins"]
    gap_own = abs(_h_of(out_own, D, t_hs) - _h_of(out_own, D, t_col))
    gap_hs = abs(_h_of(out_hs, D, t_hs) - _h_of(out_hs, D, t_col))
    assert gap_own > 0.02, gap_own
    assert gap_hs < 0.003, gap_hs
    # the factor the solve used is the planted one: pool means in HS units
    # differ by the college pool's speed, and the units by F_UNITS
    fac = out_hs["tilt_pool_factor"]
    assert fac[0] == pytest.approx(1.0)
    r = out_hs["tilt_rating"]
    assert abs(r[t_hs] - r[t_col]) < 1.0, (r[t_hs], r[t_col])
    print(f"  twins: h gap {gap_own:.4f} on own scales, {gap_hs:.5f} on HS; "
          f"college_m factor x{fac[1]:.4f} ... OK")


def test_own_is_the_shipped_tilt_bit_for_bit():
    """tilt_scale None: h is exactly the old line at the own rating."""
    w = world(n_hs=80, n_col=60)
    out, D = _solve(w, None)
    assert out["tilt_pool_factor"] is None and out["tilt_rating"] is None
    # the pre-2026-09-29 line, at the ratings the last pass set h from
    r = np.clip(out["rating"][D.athlete], js.TILT_RATING_LO, js.TILT_RATING_HI)
    h_old = 1.0 + js.TILT_K * (r - 100.0) / 10.0
    np.testing.assert_array_equal(out["h"], h_old)


def test_the_holdout_reads_the_same_rating_as_the_fit():
    w = world(n_hs=80, n_col=60)
    out, D = _solve(w, _inputs())
    pred, cov = js.predictHeldOut(out, D, D, athlete_pool=w["pool"], tilt=True)
    b = D.unpack(out["theta"])
    h_hold = js.tiltRows(out["tilt_rating"][D.athlete])
    np.testing.assert_allclose(pred, js.rowPrediction(b, D, h_hold, out["amp"]),
                               atol=1e-12)


def test_inputs_pair_each_pool_with_its_hs_twin():
    calls = []

    def factor_fn(d, pool, sport):
        calls.append((d, pool, sport))
        return {"college_m": {"XC": 1.65, "TF": 1.63},
                "ms_f": {"XC": 0.62, "TF": 0.62}}.get(pool, {}).get(sport, 1.0)
    got = rj.tiltScaleInputs(["hs_m", "college_m", "ms_f", "unknown", "hs_f"],
                             factor_fn=factor_fn, verbose=False)
    assert got["hs_of_pool"].tolist() == [0, 0, 4, -1, 4]
    assert got["f_ratio"][1] == pytest.approx(np.sqrt(1.65 * 1.63))
    assert got["f_ratio"][2] == pytest.approx(0.62)
    assert np.isnan(got["f_ratio"][3])
    # at the site's representative distances, both sports
    assert (5000.0, "college_m", "XC") in calls and (1600.0, "college_m", "TF") in calls


@pytest.fixture
def fake_pool_view(monkeypatch):
    """racecast's HS factor without a database: college_m x1.2063."""
    mod = types.ModuleType("pool_view")
    mod.repFactor = lambda pool, sport: {"college_m": 1.2063, "ms_m": 0.8729,
                                         "hs_m": 1.0}.get(pool)
    monkeypatch.setitem(sys.modules, "pool_view", mod)
    return mod


def test_the_site_tilt_follows_the_switch(monkeypatch, fake_pool_view):
    """racecast/tilt.py and conversions._tilt read XCP_TILT_SCALE and agree
    with the engine's tiltRows on the same HS factor."""
    import tilt
    monkeypatch.delenv("XCP_TILT_SCALE", raising=False)
    assert tilt.h(124.3, pool="college_m") == pytest.approx(
        float(js.tiltRows(np.array([124.3]))[0]))
    monkeypatch.setenv("XCP_TILT_SCALE", "hs")
    for pool, r in (("college_m", 124.3), ("ms_m", 169.1), ("hs_m", 156.6)):
        f = fake_pool_view.repFactor(pool, None)
        want = float(js.tiltRows(np.array([r]), np.array([f]))[0])
        assert tilt.h(r, pool=pool) == pytest.approx(want)
    # a pool with no factor keeps its own scale
    assert tilt.h(120.0, pool="unknown") == pytest.approx(
        float(js.tiltRows(np.array([120.0]))[0]))
    # and the page's ratingFor moves with it
    assert tilt.ratingFor(124.3, 0.056, pool="college_m") != pytest.approx(
        tilt.ratingFor(124.3, 0.056))


def test_conversions_tilt_follows_the_switch(monkeypatch, fake_pool_view):
    try:
        import conversions as cv
    except Exception as exc:                                # noqa: BLE001
        pytest.skip(f"conversions not importable here: {exc}")
    monkeypatch.delenv("XCP_TILT_SCALE", raising=False)
    own = cv._tilt(124.3, "college_m")
    assert own == pytest.approx(cv._tilt(124.3))
    monkeypatch.setenv("XCP_TILT_SCALE", "hs")
    assert cv._tilt(124.3, "college_m") == pytest.approx(
        float(js.tiltRows(np.array([124.3]), np.array([1.2063]))[0]))
