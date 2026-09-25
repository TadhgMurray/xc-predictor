"""engine/joint_kernels.fusedMatvec must be the numpy matvec, faster.

★ 2026-09-25: the joint solve spent 9,830 s in _Operator.matvec. The fused
  kernel does the same Z'W(Z theta) in one compiled pass. These hold it to
  the numpy path: every term switched on, the two asymmetric index pairs
  (mu, curve) exercised, and a whole solve run both ways.

    python -m pytest -q tests/test_joint_kernels.py
"""
import os
import sys
import types

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "engine"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import joint_solve as js                                        # noqa: E402
import joint_kernels as jk                                      # noqa: E402

pytestmark = pytest.mark.skipif(not jk._HAVE_NUMBA, reason="needs numba")


def _fakeDesign(n=60_000, n_ath=6_000, n_cell=500, n_race=3_000, seed=3,
                sort=True):
    rng = np.random.default_rng(seed)
    n_group, n_pool, n_knot = 3, 6, 12
    ath = rng.integers(0, n_ath, n)
    if sort:
        ath = np.sort(ath)
    D = types.SimpleNamespace(n=n, n_ath=n_ath, n_cell=n_cell, n_race=n_race,
                              n_group=n_group, n_pool=n_pool, n_knot=n_knot)
    D.athlete = ath
    D.cell = rng.integers(0, n_cell, n)
    D.race = rng.integers(0, n_race, n)
    D.group_row = rng.integers(0, n_group, n_cell)[D.cell]
    D.mu_idx = np.maximum(D.group_row - 1, 0)
    D.mu_w = (D.group_row > 0).astype(float)
    D.n_mu = n_group - 1
    D.sc, D.n_beta = rng.choice([-0.5, 0.5], n), n_ath
    D.pool_row = rng.integers(0, n_pool, n)
    D.k0 = D.pool_row * n_knot + rng.integers(0, n_knot - 1, n)
    D.k1 = D.k0 + 1
    grid = np.arange(n_pool * n_knot)
    ref = (grid % n_knot) == 3
    col = np.full(grid.size, -1)
    col[~ref] = np.arange((~ref).sum())
    D.n_c, D.free_grid, D.has_curve = int((~ref).sum()), np.flatnonzero(~ref), True
    D.c0, D.c1 = np.maximum(col[D.k0], 0), np.maximum(col[D.k1], 0)
    w1 = rng.random(n)
    D.w0 = np.where(col[D.k0] < 0, 0.0, 1 - w1)
    D.w1 = np.where(col[D.k1] < 0, 0.0, w1)
    D.first, D.has_rust, D.n_r = rng.random(n), True, n_pool
    D.n_e, D.e_idx = 17, rng.integers(0, 17, n)
    D.e_w = (rng.random(n) > .3).astype(float)
    D.n_g, D.lz = n_ath, rng.normal(size=n)
    D.n_k, D.alt = n_group, rng.random(n) * .01
    D.n_imp, D.imp_idx, D.imp_w = 5, rng.integers(0, 5, n), rng.random(n)
    D.n_ind, D.ind_idx = n_pool, D.pool_row
    D.ind_w = (rng.random(n) > .8).astype(float)
    D.o_a, D.o_d = 0, n_ath
    D.o_u = D.o_d + n_cell
    D.o_mu = D.o_u + n_race
    D.o_beta = D.o_mu + D.n_mu
    D.o_c = D.o_beta + D.n_beta
    D.o_r = D.o_c + D.n_c
    D.o_e = D.o_r + D.n_r
    D.o_g = D.o_e + D.n_e
    D.o_k = D.o_g + D.n_g
    D.o_imp = D.o_k + D.n_k
    D.o_ind = D.o_imp + D.n_imp
    D.n_total = D.o_ind + D.n_ind
    D.unpack = types.MethodType(js.Design.unpack, D)
    return D, rng


def _numpy(D, w, h, amp, b):
    me = types.SimpleNamespace(D=D, h=h, amp=amp,
                               _reduce=lambda jobs: [j() for j in jobs])
    return js._Operator.adjoint(me, w * js.rowPrediction(b, D, h, amp))


def test_every_term_matches_the_numpy_matvec():
    D, rng = _fakeDesign()
    b = D.unpack(rng.normal(size=D.n_total))
    w, h, amp = rng.random(D.n), 1 + .1 * rng.normal(size=D.n), \
        1 + .1 * rng.normal(size=D.n)
    got = jk.fusedMatvec(D, w, h, amp, b)
    ref = _numpy(D, w, h, amp, b)
    assert got.shape == ref.shape
    assert np.max(np.abs(got - ref)) <= 1e-10 * np.max(np.abs(ref))


def test_optional_terms_off_still_match():
    D, rng = _fakeDesign(seed=5)
    D.n_beta = D.n_g = D.n_imp = D.n_ind = 0
    D.o_beta = D.o_mu + D.n_mu
    D.o_c = D.o_beta
    D.o_r = D.o_c + D.n_c
    D.o_e = D.o_r + D.n_r
    D.o_g = D.o_e + D.n_e
    D.o_k = D.o_g
    D.o_imp = D.o_k + D.n_k
    D.o_ind = D.o_imp
    D.n_total = D.o_ind
    b = D.unpack(rng.normal(size=D.n_total))
    w, h, amp = rng.random(D.n), np.ones(D.n), np.ones(D.n)
    got = jk.fusedMatvec(D, w, h, amp, b)
    ref = _numpy(D, w, h, amp, b)
    assert np.max(np.abs(got - ref)) <= 1e-10 * np.max(np.abs(ref))


def test_unsorted_rows_fall_back_to_numpy():
    D, rng = _fakeDesign(sort=False)
    b = D.unpack(rng.normal(size=D.n_total))
    assert jk.fusedMatvec(D, np.ones(D.n), np.ones(D.n), np.ones(D.n), b) is None


def test_a_whole_solve_is_the_same_either_way(monkeypatch):
    import test_joint_year as T
    y, D0, truth, raw = T.world(indoor=True, seed=11)
    o = np.argsort(raw["ath"], kind="stable")
    D = js.Design(raw["ath"][o], raw["cel"][o], raw["rac"][o],
                  group_of_cell=raw["sport_of_cell"], sc=raw["sc"][o],
                  pool_row=raw["pool_row"][o], day=raw["doy"][o],
                  first=raw["first"][o], n_ath=D0.n_ath, n_cell=D0.n_cell,
                  n_race=D0.n_race, n_pool=D0.n_pool)
    ys = y[o]

    def solve(flag):
        monkeypatch.setenv("XCP_JOINT_KERNELS", flag)
        return js.solveJoint(ys, design=D, athlete_pool=truth["pool_of_ath"],
                             n_outer=3, tilt=True, n_probe=0)
    on, off = solve("1"), solve("0")
    assert getattr(D, "_kern_edges", None) is not None     # the kernel ran
    for key in ("theta", "ability", "d", "delta", "race_effect", "mu",
                "beta", "curve", "rating", "tau2", "sigma_u2"):
        np.testing.assert_allclose(on[key], off[key], rtol=1e-7, atol=1e-9,
                                   err_msg=key)
