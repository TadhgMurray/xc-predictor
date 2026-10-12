"""The abilities eliminated first (XCP_SCHUR) and the course-days block
preconditioner (XCP_PRECOND=arrow): a different route to the SAME solve
(owner, 2026-10-12, "prototype the first one").

    python -m pytest -q tests/test_schur_solve.py
"""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine"))
import joint_solve as js                                        # noqa: E402


def _world(seed=3, n_ath=1500, n_cell=60, days=5, k=7):
    rng = np.random.default_rng(seed)
    a = rng.normal(0, 0.1, n_ath)
    d = rng.normal(0, 0.04, n_cell)
    u = rng.normal(0, 0.02, n_cell * days)
    home = rng.integers(0, n_cell, n_ath)
    ath, rac = [], []
    for i in range(n_ath):
        m = rng.poisson(k) + 1
        cells = (home[i] + rng.integers(-4, 5, m)) % n_cell
        ath.append(np.full(m, i))
        rac.append(cells * days + rng.integers(0, days, m))
    ath, rac = np.concatenate(ath), np.concatenate(rac)
    cel = rac // days
    y = a[ath] + d[cel] + u[rac] + rng.normal(0, 0.03, ath.size)
    return y, ath, cel, rac


def _solve(monkeypatch, schur, pre, slope=False, **kw):
    monkeypatch.setenv("XCP_SCHUR", schur)
    monkeypatch.setenv("XCP_PRECOND", pre)
    y, ath, cel, rac = _world()
    lz = None
    if slope:
        # the endurance slope's per-row feature: a centred log distance
        lz = np.random.default_rng(9).normal(0, 0.3, ath.size)
    D = js.Design(ath, cel, rac, lz=lz)
    return js.solveJoint(y, design=D, n_outer=3, tilt=False, n_probe=1, **kw)


@pytest.mark.parametrize("slope", [False, True])
@pytest.mark.parametrize("schur,pre", [("1", "jacobi"), ("0", "arrow"), ("1", "arrow")])
def test_same_answer_as_the_plain_solve(monkeypatch, schur, pre, slope):
    base = _solve(monkeypatch, "0", "jacobi", slope=slope)
    got = _solve(monkeypatch, schur, pre, slope=slope)
    for k in ("ability",):
        assert np.max(np.abs(np.asarray(got[k]) - np.asarray(base[k]))) < 1e-6, k


def test_schur_steps_back_when_athletes_are_coupled():
    class Op:
        tie_w = np.ones(3)
        link_weight = 0.0

    class D:
        has_link = False
    assert js.schurEligible(D, Op) is False
    Op.tie_w = None
    assert js.schurEligible(D, Op) is True
    D.has_link, Op.link_weight = True, 1.0
    assert js.schurEligible(D, Op) is False


def test_arrow_preconditioner_inverts_one_course_exactly():
    # one course, three days: the block solve must match a dense solve of
    # the same arrowhead
    class D:
        n_cell, n_race, n_ath = 1, 3, 4
        o_d, o_u, o_mu = 4, 5, 8
        athlete = np.array([0, 1, 2, 3, 0, 1])
        race = np.array([0, 0, 1, 1, 2, 2])
        cell = np.zeros(6, dtype=np.int64)
    w, h = np.ones(6), np.ones(6)
    cell_of = np.zeros(3, dtype=np.int64)
    pen_c, pen_u = 2.0, 3.0
    C = 6 + pen_c
    U = np.array([2, 2, 2]) + pen_u
    diag = np.r_[np.ones(4), C, U]
    apply = js.arrowPrecond(D, diag, 0, w, h, cell_of)
    M = np.zeros((4, 4))
    M[0, 0] = C
    M[1:, 1:] = np.diag(U)
    M[0, 1:] = M[1:, 0] = 2.0
    r = np.r_[np.zeros(4), 1.0, 0.5, -0.25, 2.0]
    z = apply(r)
    assert np.allclose(M @ z[4:], r[4:])
