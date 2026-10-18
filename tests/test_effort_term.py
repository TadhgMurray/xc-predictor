# Project: xc-predictor / tests
# File:    test_effort_term.py
# Purpose: the effort (shortfall) term and the per-division race key
#          (owner, 2026-10-18; joint_solve.THETA_RACE_PRIOR_N, run_joint
#          raceCodes). The published day credit ran too big for fast
#          runners and too small for slow ones because one day number per
#          venue-day is read off the whole field -- including league meets
#          where most of the field jogs at tempo -- and credited to all.
#            1. the E-step (Jondrow et al. 1982) against numerical
#               integration of the posterior, extreme residuals included;
#            2. a synthetic season through run_joint.buildDesign and
#               js.solveJoint: a tempo meet (90% of the field +6%, the
#               front at its level) reads as ~0 day under --effort and as
#               a slow day without it; a genuinely slow day (+3% for all)
#               reads +3% either way;
#            3. --race-key division: one race per (meet, div, day), and
#               the (cell, day) fallback for rows without a meet.
#
#   python -m pytest -q tests/test_effort_term.py
import contextlib
import io
import os
import sys

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import joint_solve as js                                       # noqa: E402
import run_joint as rj                                         # noqa: E402


# ------------------------------------------------------------------ #
# 1. the E-step
# ------------------------------------------------------------------ #

def _posterior_by_quadrature(e, sv, th):
    """E[s|e], Var[s|e] by integrating p(s|e) ~ exp(-s/th) phi((e-s)/sv)
    over s >= 0 on a fine grid, in the log domain."""
    mu = e - sv * sv / th
    z = mu / sv
    hi = (max(mu, 0.0) + 14.0 * sv) if z > -3.0 else 60.0 * sv / abs(z)
    s = np.linspace(0.0, hi, 400_001)
    lp = -s / th - 0.5 * ((e - s) / sv) ** 2
    p = np.exp(lp - lp.max())
    Z = np.trapezoid(p, s)
    m = np.trapezoid(s * p, s) / Z
    v = np.trapezoid((s - m) ** 2 * p, s) / Z
    return m, v


CASES = [
    # (e, sigma_v, theta): ordinary, tempo, fast, far fast, far slow,
    # either side of the continued-fraction switch, tiny theta
    (0.0, 0.01, 0.01), (0.06, 0.01, 0.04), (-0.05, 0.01, 0.01),
    (-0.5, 0.01, 0.003), (0.3, 0.02, 0.001), (1.0, 0.01, 0.05),
    (0.02, 0.03, 0.0001), (-0.019, 0.01, 0.01), (-0.021, 0.01, 0.01),
    (-0.29, 0.01, 0.01), (0.005, 0.03, 0.02), (0.1, 0.05, 0.2),
]


@pytest.mark.parametrize("e,sv,th", CASES)
def test_the_e_step_matches_quadrature(e, sv, th):
    m, v = js.effortPosterior(e, sv, th)
    M, V = _posterior_by_quadrature(e, sv, th)
    assert m == pytest.approx(M, rel=2e-6, abs=1e-12)
    assert v == pytest.approx(V, rel=2e-5, abs=1e-14)


def test_the_e_step_is_vectorised_finite_and_ordered():
    e = np.linspace(-5.0, 5.0, 20_001)
    m, v = js.effortPosterior(e, 0.02, 0.01)
    assert np.all(np.isfinite(m)) and np.all(np.isfinite(v))
    assert np.all(m >= 0) and np.all(v >= 0)
    # a slower row is never read as less short
    assert np.all(np.diff(m) >= -1e-15)
    # far fast: s|e is an exponential with rate |z|/sv -> mean sv/|z|,
    # var (sv/|z|)^2; far slow: s = e - sv^2/theta, var sv^2
    z = (e[0] - 0.02 ** 2 / 0.01) / 0.02
    assert m[0] == pytest.approx(0.02 / abs(z), rel=1e-4)
    assert v[0] == pytest.approx((0.02 / z) ** 2, rel=1e-3)
    assert m[-1] == pytest.approx(e[-1] - 0.04, rel=1e-12)
    assert v[-1] == pytest.approx(0.02 ** 2, rel=1e-9)


def test_the_mills_ratio_is_continuous_at_the_switch():
    z = np.array([js._MILLS_CF_Z - 1e-9, js._MILLS_CF_Z + 1e-9])
    lam = js.millsRatio(z)
    assert abs(lam[0] - lam[1]) < 1e-6
    d, vf = js.truncNormalMoments(z)
    assert abs(vf[0] - vf[1]) < 1e-6


def test_the_inner_em_recovers_a_composed_error():
    """e = v + s simulated, one group, no races to speak of: the law comes
    back (sigma_v, theta) within a few percent."""
    rng = np.random.default_rng(5)
    n = 200_000
    e = rng.normal(0, 0.015, n) + rng.exponential(0.02, n)
    race = np.arange(n) // 1000
    fit = js.effortFit(e, np.zeros(n, dtype=np.int64), race, 1, race.max() + 1,
                       *js.effortStart(e), iters=200)
    assert np.sqrt(fit["sigma_v2"]) == pytest.approx(0.015, rel=0.05)
    assert fit["theta_g"][0] == pytest.approx(0.02, rel=0.05)


# ------------------------------------------------------------------ #
# 2. a synthetic season through the solve
# ------------------------------------------------------------------ #

N_ATH = 40
NOISE = 0.01
TEMPO_SLOW = 0.06
SLOW_DAY = 0.03


def _season(seed=0, jog_sd=0.005, n_front=3):
    """Forty runners, four XC courses, 24 ordinary races of 30 (day sd
    1%, row noise 1%), one TEMPO race (a league meet: 27 of 30 run +6%
    +- 0.5%, the 3 who race at their level) and one genuinely SLOW day
    (+3% for everyone, +- 1%)."""
    rng = np.random.default_rng(seed)
    keys = [f"XC:{200 + c}:d5000" for c in range(4)]
    d_cell = np.array([0.0, 0.02, -0.015, 0.035])
    a_true = rng.normal(0.0, 0.05, N_ATH)
    rows = dict(ath=[], cell=[], days=[], meet=[], div=[], y=[], mc=[])
    kinds = []
    for j in range(26):
        c = j % 4
        who = rng.choice(N_ATH, 30, replace=False)
        days_ago = 200 - 7 * j
        if j == 24:                                    # the tempo meet
            u, kind, mc = 0.0, "tempo", 1
            jog = (TEMPO_SLOW + rng.normal(0, jog_sd, 30 - n_front) if jog_sd >= 0
                   else rng.uniform(0.02, 2 * TEMPO_SLOW - 0.02, 30 - n_front))
            slow = np.r_[np.zeros(n_front), jog]
            noise = np.r_[rng.normal(0, NOISE, n_front), np.zeros(30 - n_front)]
        elif j == 25:                                  # a slow day
            u, kind, mc = SLOW_DAY, "slow", 0
            slow = np.zeros(30)
            noise = rng.normal(0, NOISE, 30)
        else:
            u, kind, mc = rng.normal(0, 0.01), "ordinary", 0
            slow = np.zeros(30)
            noise = rng.normal(0, NOISE, 30)
        y = a_true[who] + d_cell[c] + u + slow + noise
        rows["ath"] += who.tolist()
        rows["cell"] += [c] * 30
        rows["days"] += [days_ago] * 30
        rows["meet"] += [5000 + j] * 30
        rows["div"] += [1] * 30
        rows["mc"] += [mc] * 30
        rows["y"] += y.tolist()
        kinds.append((kind, u))
    n = len(rows["y"])
    cols = {"athlete": np.array(rows["ath"]), "year": np.full(n, 2026),
            "course": np.array(rows["cell"]),
            "days": np.array(rows["days"], dtype=np.float64),
            "sport": np.zeros(n, dtype=np.int64),
            "norm": np.exp(np.array(rows["y"])),
            "doy": np.full(n, 270), "dist_m": np.full(n, 5000.0, dtype=np.float32),
            "meet_id": np.array(rows["meet"], dtype=np.int64),
            "div_id": np.array(rows["div"], dtype=np.int64),
            "meet_class": np.array(rows["mc"], dtype=np.int8),
            "result_id": np.arange(n) + 900_000,
            "athlete_keys": [(7000 + i, "hs_m") for i in range(N_ATH)],
            "course_keys": keys}
    return cols, kinds


def _solve(cols, effort, race_key="cell", n_outer=8):
    old = rj._RACE_KEY["by"]
    rj._RACE_KEY["by"] = race_key
    try:
        keep = np.ones(cols["norm"].size, dtype=bool)
        with contextlib.redirect_stdout(io.StringIO()):
            D, ap, _pn = rj.buildDesign(
                cols, keep, sport_offset=False, curve=False, rust=False,
                dist=False, slope=False, link=False, altitude=False,
                era_years=0, importance="none", indoor=False, dist_table=False)
        y = np.log(cols["norm"][keep])
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            out = js.solveJoint(y, design=D, athlete_pool=ap, n_outer=n_outer,
                                tilt=False, n_probe=0, effort=effort,
                                verbose=True)
    finally:
        rj._RACE_KEY["by"] = old
    return D, y, out, buf.getvalue()


def _days(D, cols, out):
    """Per synthetic race j (meet 5000 + j), its fitted day, less the mean
    of the ordinary races' (u is centred per sport by recentreLevels)."""
    u = np.empty(26)
    for j in range(26):
        u[j] = out["race_effect"][int(D.race[np.flatnonzero(cols["meet_id"] == 5000 + j)[0]])]
    return u - u[:24].mean()


def _check_tempo_and_slow_day(seed, n_front, tempo_tol, slow_tol=0.01):
    cols, kinds = _season(seed, n_front=n_front)
    true_u = np.array([u for _k, u in kinds])
    true_u = true_u - true_u[:24].mean()
    D, y, off, _log = _solve(cols, effort=False)
    u_off = _days(D, cols, off)
    # the bug, reproduced: the joggers' slowness is read as the day
    assert u_off[24] > 0.03, u_off[24]
    assert abs(u_off[25] - true_u[25]) < slow_tol, u_off[25]
    assert off["effort"] is None
    D, y, on, log = _solve(cols, effort=True)
    u_on = _days(D, cols, on)
    assert abs(u_on[24] - true_u[24]) < tempo_tol, u_on[24]   # the tempo meet: ~0
    assert abs(u_on[25] - true_u[25]) < slow_tol, u_on[25]    # the slow day: ~+3%
    # the joggers carry the shortfall, the front does not
    tempo = np.flatnonzero(cols["meet_id"] == 5024)
    s = on["effort"][tempo]
    assert np.median(s[n_front:]) > 0.04 and np.max(s[:n_front]) < 0.02
    return D, cols, on, log


def test_the_tempo_meet_is_not_a_slow_day_under_the_effort_term():
    """The owner's case: 27 of 30 jog +6% +- 0.5%, the 3 in front race.

    ⚠ 10% IS THE EDGE OF WHAT THE MODEL CAN SEE, and the tolerance says
      so. Three runners hold the day against 27 whose shortfall is a tight
      block, not the exponential spread the law expects; the race's own
      likelihood, profiled over theta with the day's prior, peaks near
      +1 to +1.5%, and across seeds the solve lands +0.6% to +4.3% (median
      +1.2%; one seed in five walks back toward the slow-day reading).
      This seed reads +0.97%. With a fifth of the field in front (the next
      test) every seed tried lands within 1%."""
    D, cols, on, log = _check_tempo_and_slow_day(0, 3, tempo_tol=0.015)
    # what the go-live could read later, and what the log says each pass
    assert on["effort"].shape == (D.n,)
    assert on["effort_var"].shape == (D.n,)
    assert on["effort_theta_race"].shape == (D.n_race,)
    r_t = int(D.race[np.flatnonzero(cols["meet_id"] == 5024)[0]])
    assert int(np.argmax(on["effort_theta_race"])) == r_t
    assert on["effort_group_names"][1] == "XC:league"
    assert "effort, for pass 2" in log and "largest theta_race" in log
    assert "XC:league" in log and "of rows E[s] > 0.02" in log


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_a_tempo_meet_with_a_fifth_in_front_reads_within_one_percent(seed):
    _check_tempo_and_slow_day(seed, 6, tempo_tol=0.01, slow_tol=0.0125)


def test_the_effort_term_keeps_its_reading_over_more_passes():
    """The fixed point, not a stopping point: the tempo day does not walk
    back toward the joggers with more outer passes (before the fast side
    was judged before the day, it read +1.6% at 8 passes and +5.0% at 40)."""
    cols, _k = _season(0)
    u = []
    for n_outer in (8, 20):
        D, y, on, _l = _solve(cols, effort=True, n_outer=n_outer)
        u.append(_days(D, cols, on)[24])
    assert abs(u[1] - u[0]) < 0.002, u


def test_the_slow_side_keeps_weight_one_under_the_effort_term():
    r = np.r_[np.zeros(50), 0.5, -0.5, np.linspace(-0.01, 0.01, 20)]
    w0, sc = js.robustWeights(r)
    w1, sc1 = js.robustWeights(r, slow=False)
    assert sc == sc1
    assert w0[50] < 1 and w1[50] == 1.0                 # slow: forgiven
    assert w0[51] == w1[51] < 1                         # fast: cut as before
    assert np.array_equal(w0[r <= 0], w1[r <= 0])


def test_held_out_prediction_adds_the_expected_shortfall():
    cols, _k = _season(seed=2)
    D, y, on, _l = _solve(cols, effort=True, n_outer=4)
    pred, cov = js.predictHeldOut(on, D, D)
    # every race was fitted: each row's prediction carries its race's theta
    off = dict(on)
    off["effort_theta_group"] = None
    pred0, _c = js.predictHeldOut(off, D, D)
    assert np.allclose(pred - pred0, on["effort_theta_race"][D.race])


# ------------------------------------------------------------------ #
# 3. --race-key division
# ------------------------------------------------------------------ #

def test_division_race_codes_and_the_cell_fallback():
    course = np.array([0, 0, 0, 0, 1, 1, 0, 0, 1])
    day = np.array([5, 5, 5, 5, 5, 5, 9, 5, 5])
    meet = np.array([7, 7, 7, 7, 7, -1, 7, -1, -1])
    div = np.array([1, 1, 2, 2, 1, 3, 1, -1, 4])
    race, n = rj.raceCodes(course, day, meet=meet, div=div)
    # (7,1,5) rows 0,1,4 (two cells: one division); (7,2,5) rows 2,3;
    # (7,1,9) row 6; fallbacks: (cell 0, 5) row 7; (cell 1, 5) rows 5, 8
    # -- a meet-less row never joins a division, whatever its div_id
    groups = {}
    for i, r in enumerate(race):
        groups.setdefault(int(r), []).append(i)
    assert sorted(groups.values()) == sorted([[0, 1, 4], [2, 3], [6], [7], [5, 8]])
    assert n == 5
    # the fallback races are numbered first, in (cell, day) order
    assert race[7] == 0 and race[5] == race[8] == 1
    # dense
    assert set(race.tolist()) == set(range(n))


def test_the_division_key_through_build_design():
    cols, _k = _season()
    # one meet with two divisions on one day, and a meet-less race
    cols = dict(cols)
    cols["div_id"] = cols["div_id"].copy()
    cols["meet_id"] = cols["meet_id"].copy()
    m0 = np.flatnonzero(cols["meet_id"] == 5000)
    cols["div_id"][m0[:15]] = 2
    m1 = np.flatnonzero(cols["meet_id"] == 5001)
    cols["meet_id"][m1] = -1
    keep = np.ones(cols["norm"].size, dtype=bool)
    old = rj._RACE_KEY["by"]
    try:
        out = {}
        for key in ("cell", "division"):
            rj._RACE_KEY["by"] = key
            with contextlib.redirect_stdout(io.StringIO()):
                out[key] = rj.buildDesign(
                    cols, keep, sport_offset=False, curve=False, rust=False,
                    dist=False, slope=False, link=False, altitude=False,
                    era_years=0, importance="none", indoor=False,
                    dist_table=False)[0]
    finally:
        rj._RACE_KEY["by"] = old
    Dc, Dd = out["cell"], out["division"]
    assert Dd.race_key == "division"
    assert Dd.n_race == Dc.n_race + 1                  # meet 5000 split in two
    assert np.unique(Dd.race[m0]).size == 2
    assert np.unique(Dd.race[m1]).size == 1            # the fallback: (cell, day)
    # each division race is one division, so the day guard has nothing to
    # compare inside it: subrace is one per race
    per_race = np.bincount(np.unique(np.c_[Dd.race, Dd.subrace], axis=0)[:, 0])
    assert per_race.max() == 1
    # and the guard leaves every race alone (no flag, u = the plain sums)
    rng = np.random.default_rng(0)
    r = rng.normal(0, 0.03, Dd.n) + np.where(np.isin(np.arange(Dd.n), m0[:15]), 0.4, 0.0)
    dd = js.raceDayDivisions(r, np.ones(Dd.n), 1.0, Dd.race, Dd.subrace,
                             Dd.n_race, 10.0)
    assert not dd["flag"].any()


def test_the_division_key_solves():
    cols, _k = _season(seed=3)
    D, y, out, _l = _solve(cols, effort=False, race_key="division", n_outer=3)
    assert D.n_race == 26 and np.all(np.isfinite(out["race_effect"]))
    u_loo, info = js.raceEffectLeaveOneOut(out, D, y)
    assert np.all(np.isfinite(u_loo)) and info["max_conditional_gap"] < 0.01


def test_the_leave_one_out_day_reads_the_solve_under_the_effort_term():
    """raceEffectLeaveOneOut re-takes u from the same sums the solve did,
    y - E[s]: without the offset a jogger's shortfall would be its pull."""
    cols, _k = _season(0)
    D, y, on, _l = _solve(cols, effort=True)
    u_loo, info = js.raceEffectLeaveOneOut(on, D, y)
    assert np.all(np.isfinite(u_loo)) and info["max_conditional_gap"] < 0.01
    bare = dict(on)
    bare["effort"] = None
    _u, info_bare = js.raceEffectLeaveOneOut(bare, D, y)
    assert info_bare["max_conditional_gap"] > info["max_conditional_gap"]


def test_the_switches_reach_the_solve(monkeypatch):
    ap = rj.buildParser()
    monkeypatch.delenv("XCP_EFFORT", raising=False)
    args = ap.parse_args([])
    assert args.effort is False and args.race_key == "cell"
    args = ap.parse_args(["--effort", "--race-key", "division"])
    assert args.effort is True and args.race_key == "division"
    monkeypatch.setenv("XCP_EFFORT", "1")
    assert rj.buildParser().parse_args([]).effort is True
    args = rj.applyImplications(ap.parse_args(["--effort"]), ap)
    assert rj.solveKwargs(args, None, False)["effort"] is True
