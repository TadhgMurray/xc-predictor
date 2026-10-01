"""The race-day trial (2026-10-01): each rating's day without its own row
(js.raceEffectLeaveOneOut), and the go-live's consistency check
(joint_golive.dayConsistencyReport)."""
import io
import os
import sys
from contextlib import redirect_stdout
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine"))
sys.path.insert(0, os.path.dirname(__file__))
import joint_solve as js                                        # noqa: E402
from test_joint_solve import _world                              # noqa: E402


def _fit(seed=3):
    ath, cel, rac, y, _td, _ta, true_u = _world(seed=seed)
    D = js.Design(ath, cel, rac)
    out = js.solveJoint(y, design=D, n_outer=6, tilt=False, tau_max=None, verbose=False)
    return D, out, y, true_u


def test_leave_one_out_matches_dropping_the_row():
    D, out, y, _ = _fit()
    u_loo, info = js.raceEffectLeaveOneOut(out, D, y)
    assert u_loo is not None and u_loo.shape == (D.n,)
    b = D.unpack(out["theta"])
    h = np.asarray(out["h"], dtype=np.float64)
    h = np.full(D.n, float(h)) if h.ndim == 0 else h
    w = out["weights"]
    r = (y - js.rowPrediction(b, D, h, out["amp"]) - D.fixedOffset(h)
         + h * b["u"][D.race])
    pen = out["sigma2"] / np.asarray(out["sigma_u2"]).reshape(-1)[js.groupOfRace(D)]
    for i in (0, 5, 100, 777, D.n - 1):
        j = D.race[i]
        m = D.race == j
        rest = m.copy()
        rest[i] = False
        full = (w[m] * h[m] * r[m]).sum() / ((w[m] * h[m] ** 2).sum() + pen[j])
        drop = (w[rest] * h[rest] * r[rest]).sum() / ((w[rest] * h[rest] ** 2).sum() + pen[j])
        assert abs(b["u"][j] + drop - full - u_loo[i]) < 1e-10


def test_own_pull_is_small_and_the_day_still_tracks_the_truth():
    D, out, y, true_u = _fit()
    u_loo, info = js.raceEffectLeaveOneOut(out, D, y)
    # a dozen runners a race: one runner is a small share of the day
    assert info["own_pull_median"] < 0.003
    assert np.corrcoef(u_loo, true_u[D.race])[0, 1] > 0.8


def test_one_runner_race_gets_the_prior_not_its_own_residual():
    D, out, y, _ = _fit()
    # move one row into a race of its own: its day must come from the
    # prior alone, i.e. no part of its own residual
    race = D.race.copy()
    race[0] = race.max() + 1
    D1 = js.Design(D.athlete, D.cell, race)
    out1 = js.solveJoint(y, design=D1, n_outer=6, tilt=False, tau_max=None, verbose=False)
    u_loo, _ = js.raceEffectLeaveOneOut(out1, D1, y)
    b = D1.unpack(out1["theta"])
    h = np.asarray(out1["h"], dtype=np.float64)
    h0 = float(h) if h.ndim == 0 else float(h[0])
    w0 = float(out1["weights"][0])
    s_u2 = np.asarray(out1["sigma_u2"]).reshape(-1)[js.groupOfRace(D1)][race[0]]
    pen = float(out1["sigma2"]) / s_u2
    # the conditional day of a lone row is w h r / (w h^2 + pen); without it, 0
    r0 = (y[0] - js.rowPrediction(b, D1, np.full(D1.n, h0), out1["amp"])[0]
          - D1.fixedOffset(np.full(D1.n, h0))[0] + h0 * b["u"][race[0]])
    cond = w0 * h0 * r0 / (w0 * h0 * h0 + pen)
    assert abs(u_loo[0] - (b["u"][race[0]] - cond)) < 1e-10


def test_state_without_the_solve_terms_is_refused():
    D, out, y, _ = _fit()
    old = dict(out)
    old.pop("weights")
    u, why = js.raceEffectLeaveOneOut(old, D, y)
    assert u is None and "weights" in why


def _report(z_noise, day_sd, seed=0):
    import joint_golive as jg
    rng = np.random.default_rng(seed)
    n_ath, n_race, per = 300, 60, 6
    true_u = rng.normal(0, day_sd, n_race)
    ath = np.repeat(np.arange(n_ath), per)
    race = rng.integers(0, n_race, ath.size)
    y = rng.normal(0, 0.1, n_ath)[ath] + true_u[race] + rng.normal(0, z_noise, ath.size)
    D = SimpleNamespace(athlete=ath, race=race)
    out = {"h": np.ones(ath.size)}
    u_row = true_u[race]
    day_on = np.ones(ath.size, dtype=bool)
    buf = io.StringIO()
    with redirect_stdout(buf):
        jg.dayConsistencyReport(y, np.zeros(ath.size), out, D, u_row, u_row,
                                day_on, np.zeros(ath.size, dtype=bool),
                                np.zeros(ath.size, dtype=np.int8))
    return buf.getvalue()


def test_consistency_says_lower_when_days_are_real():
    text = _report(z_noise=0.01, day_sd=0.04)
    assert "LOWER" in text and "day in, leave-self-out" in text


def test_consistency_says_higher_when_the_day_is_noise():
    import joint_golive as jg
    rng = np.random.default_rng(1)
    n = 1800
    ath = np.repeat(np.arange(300), 6)
    race = rng.integers(0, 60, n)
    y = rng.normal(0, 0.02, n)                   # no day effect at all
    fake_u = rng.normal(0, 0.03, 60)[race]       # a "day" that is pure noise
    buf = io.StringIO()
    with redirect_stdout(buf):
        jg.dayConsistencyReport(y, np.zeros(n), {"h": np.ones(n)},
                                SimpleNamespace(athlete=ath, race=race),
                                fake_u, fake_u, np.ones(n, dtype=bool),
                                np.zeros(n, dtype=bool), np.zeros(n, dtype=np.int8))
    assert "HIGHER" in buf.getvalue()


def test_pipeline_and_scorecard_carry_the_switches():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sh = open(os.path.join(root, "deploy", "run_pipeline.sh"), encoding="utf-8").read()
    golive = sh[sh.index("step 08_golive"):sh.index("step 08a_holdout")]
    holdout = sh[sh.index("step 08a_holdout"):sh.index("step 08b_ladder")]
    assert '--race-key "$XCP_RACE_KEY"' in golive
    assert '--race-effect-own "$XCP_RACE_EFFECT_OWN"' in golive
    # the venue key changes the solve, so the holdout fits it too
    assert '--race-key "$XCP_RACE_KEY"' in holdout
    sys.path.insert(0, os.path.join(root, "scripts"))
    import scorecard as sc
    assert ("XCP_RACE_KEY", "--race-key", True) in sc.PRODUCTION_ENV
    assert "XCP_RACE_EFFECT_OWN" in sc.PUBLISH_ONLY
