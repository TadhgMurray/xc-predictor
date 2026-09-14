# Project: xc-predictor / tests
# File:    test_bracket_golive.py
# Purpose: --difficulty bracket publishes the bracket engine's course
#          numbers through the joint solve's go-live: the engine gets the
#          solve's residual with every term but course, day and ability
#          taken off, its numbers land on the solve's scale, the abilities
#          are recomputed to match, and the go-live runs on the result.
#
#   python -m pytest -q tests/test_bracket_golive.py
import contextlib
import io
import os
import sys

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "engine"), os.path.join(_ROOT, "tests")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import joint_golive as jg                                      # noqa: E402
import joint_solve as js                                       # noqa: E402
import run_joint as rj                                         # noqa: E402
import bracket_engine as be                                    # noqa: E402
from test_era_publish import _era_pack                         # noqa: E402


def _solve(cols, keep):
    with contextlib.redirect_stdout(io.StringIO()):
        D, athlete_pool, pool_names = rj.buildDesign(
            cols, keep, sport_offset=False, curve=False, rust=False, dist=False,
            slope=False, link=False, altitude=False, era_years=2,
            importance="none", indoor=True, dist_table=False)
        y = np.log(cols["norm"][keep])
        out = js.solveJoint(y, design=D, athlete_pool=athlete_pool, n_outer=3,
                            tilt=False, n_probe=0)
    return D, athlete_pool, pool_names, y, out


def test_the_swap_lands_on_the_solves_scale_and_the_go_live_publishes_it(capsys):
    cols, keep, keys, sport_of_course = _era_pack()
    D, athlete_pool, pool_names, y, out = _solve(cols, keep)
    delta_joint = out["delta"].copy()
    a_joint = out["ability"].copy()
    rating_joint = out["rating"].copy()
    info = rj.bracketDifficulties(out, D, cols, keep, y, athlete_pool, pool_names,
                                  window=60, top=1.0)
    text = capsys.readouterr().out
    assert "difficulty = BRACKET ENGINE" in text
    delta_b = out["delta"]
    assert delta_b.shape == delta_joint.shape and np.isfinite(delta_b).all()
    assert out["difficulty_source"] == "bracket"
    assert np.array_equal(out["delta_joint"], delta_joint)
    # ★ THE TRACE'S ARITHMETIC IS THE ENGINE'S: for a cell with votes,
    #   published = (raw * votes + prior_races * course history) / (votes
    #   + prior_races) - the recentring, plus the sport level
    votes = out["bracket_votes"]
    raw, base = out["bracket_cell_raw"], out["bracket_base"]
    pin, shift, fit = out["bracket_pin"], out["bracket_shift"], out["bracket_cell_fit"]
    mu_cell = out["mu"][np.asarray(D.group_of_cell)]
    m = votes > 0
    assert np.isfinite(raw[m]).all() and np.isfinite(base).all()
    rebuilt = (raw[m] * votes[m] + be.PRIOR_RACES * base[m]) / (votes[m] + be.PRIOR_RACES)
    assert np.allclose(rebuilt - pin[m], fit[m], atol=1e-12)
    # the fitted total is the published one to within the iteration's
    # tolerance (the engine returns its damped iterate), plus the level
    assert np.abs(fit[m] - shift[m] + mu_cell[m] - delta_b[m]).max() < 2e-4
    assert out["bracket_prior_group"].shape == (len(be.PRIOR_GROUP_NAMES),)
    # the two engines measure the same planted world: the numbers agree
    # course by course, and the sport level (the planted -5% on track) is
    # the joint solve's, carried over untouched
    solved = np.bincount(D.cell, minlength=D.n_cell) > 0
    assert info["corr"] > 0.9, info
    cell_keys = [str(k) for k in D.course_keys]
    is_tf = np.array([k.startswith("TF:") for k in cell_keys])
    is_in = np.array([k.split("@", 1)[0].endswith(":in") for k in cell_keys])
    outdoor_tf = solved & is_tf & ~is_in
    assert abs(delta_b[outdoor_tf].mean() - delta_joint[outdoor_tf].mean()) < 0.01
    assert abs(delta_b[solved & ~is_tf].mean() - delta_joint[solved & ~is_tf].mean()) < 0.01
    # ★ THE INDOOR CELLS ARE WHERE THE TWO PART, AND THE BRACKET ENGINE IS
    #   RIGHT. The world plants no indoor level, and its two indoor courses
    #   happen to be about 3.5% faster than the average track. The joint
    #   solve asserts +1.2% and pins the indoor cells' mean to it, so it
    #   reports them near -0.04 whatever they are; the engine brackets the
    #   same athletes across their indoor and outdoor rows and reads the
    #   planted numbers back. Under --difficulty bracket the indoor level
    #   is measured, not asserted.
    rng = np.random.default_rng(2)                       # _era_pack's first draws
    base_d = np.r_[rng.normal(0, 0.05, 20), rng.normal(0, 0.02, 10)]
    def _base(k):                                        # course index in _era_pack
        return 20 + int(k.split(":")[2]) if k.startswith("TF:") else int(k.split(":")[1]) - 100
    truth = np.array([base_d[_base(k)] + (-0.05 if k.startswith("TF:") else 0.0)
                      for k in cell_keys])
    xc = solved & ~is_tf
    rel_b = delta_b[solved & is_in].mean() - delta_b[xc].mean()
    rel_true = truth[solved & is_in].mean() - truth[xc].mean()
    rel_joint = delta_joint[solved & is_in].mean() - delta_joint[xc].mean()
    assert abs(rel_b - rel_true) < 0.012, (rel_b, rel_true)
    assert abs(rel_joint - rel_true) > 0.025, (rel_joint, rel_true)   # the joint cannot
    # and course by course, against the truth: both engines within a percent
    # on XC and outdoor tracks (rms), the bracket engine alone on indoor
    def _rms(a, m):
        return float(np.sqrt(np.mean(((a - a[xc].mean()) - (truth - truth[xc].mean()))[m] ** 2)))
    assert _rms(delta_b, xc) < 0.012 and _rms(delta_b, outdoor_tf) < 0.012
    assert _rms(delta_b, solved & is_in) < 0.012 < 0.03 < _rms(delta_joint, solved & is_in)
    # the drifting course (planted +5% over eight years) still drifts
    eras0 = sorted((int(k.rpartition("@e")[2]), i) for i, k in enumerate(cell_keys)
                   if k.startswith(keys[0] + "@e"))
    d0 = np.array([delta_b[i] for _, i in eras0])
    assert 0.02 < float(d0[-1] - d0[0]) < 0.06, d0
    # abilities recomputed against the new courses stay the same people
    assert info["ability_corr"] > 0.99 and info["ability_shift"] < 0.01, info
    assert np.isfinite(out["ability"]).all() and np.isfinite(out["rating"]).all()
    assert np.corrcoef(out["rating"], rating_joint)[0, 1] > 0.99
    # each ability IS the solve's weighted mean of its rows' residuals
    # against the new courses (the ability block has no penalty)
    b = D.unpack(out["theta"])
    h = np.asarray(out["h"], dtype=np.float64)
    if h.ndim == 0 or h.size != D.n:
        h = np.full(D.n, float(np.mean(h)))
    u_row = b["u"][D.race]
    resid = y - h * (delta_b[D.cell] + u_row)
    w = out["weights"]
    a_check = (np.bincount(D.athlete, weights=w * resid, minlength=D.n_ath)
               / np.maximum(np.bincount(D.athlete, weights=w, minlength=D.n_ath), 1e-12))
    assert np.allclose(out["ability"] - (a_joint - b["a"]), a_check, atol=1e-9)
    # the cell variance is the race-day variance over the races behind the cell
    assert (out["cell_var"][solved] > 0).all() and out["cell_se"].shape == delta_b.shape
    # and the go-live runs on the swapped solve and publishes every base course
    with contextlib.redirect_stdout(io.StringIO()):
        live = jg.buildLive(out, D, cols, keep)
    assert len(live["diffs"]) == len(keys) and not any("@e" in k for k in live["diffs"])


def test_the_engine_takes_a_given_response_and_tilt():
    cols, keep, keys, sport_of_course = _era_pack()
    z = np.log(cols["norm"])
    h = np.full(z.size, 1.0)
    with contextlib.redirect_stdout(io.StringIO()):
        f1 = be.fit(cols, None, window=60, top=1.0, era_years=2)
        f2 = be.fit(cols, None, window=60, top=1.0, era_years=2, z=z, h_row=h)
    assert np.allclose(f1["D"], f2["D"])
    # a response handed in with a course's rows shifted by +3% moves that course
    z3 = z.copy()
    z3[cols["course"] == 1] += 0.03
    with contextlib.redirect_stdout(io.StringIO()):
        f3 = be.fit(cols, None, window=60, top=1.0, era_years=2, z=z3, h_row=h)
    cells1 = [i for i, k in enumerate(f3["cell_keys"]) if k.startswith(keys[1] + "@e")]
    assert 0.02 < float((f3["D"][cells1] - f2["D"][cells1]).mean()) < 0.035


def test_the_solves_state_round_trips_and_is_checked_against_the_design(tmp_path):
    cols, keep, keys, sport_of_course = _era_pack()
    D, athlete_pool, pool_names, y, out = _solve(cols, keep)
    path = str(tmp_path / "joint_state.npz")
    rj.saveState(out, path)
    back = rj.loadState(path)
    for k, v in out.items():
        if isinstance(v, np.ndarray):
            assert k in back, k
            tol = 1e-6 if k in rj._STATE_F32 else 0.0
            assert np.allclose(back[k], v, atol=tol, equal_nan=True), k
        elif v is None:
            assert back.get(k) is None, k
        else:
            assert back[k] == v or (isinstance(v, float) and abs(back[k] - v) < 1e-12), k
    rj.checkState(back, D)                                   # the same design: fine
    # the swap runs from the loaded state exactly as from the live one
    info = rj.bracketDifficulties(back, D, cols, keep, y, athlete_pool, pool_names,
                                  window=60, top=1.0, verbose=False)
    assert info["corr"] > 0.9 and np.isfinite(back["delta"]).all()
    # a different design is refused before anything runs
    import pytest
    D2, *_ = _solve(cols, keep & (np.asarray(cols["course"]) != 0))[:1]
    with pytest.raises(SystemExit):
        rj.checkState(back, D2)


def test_each_host_populations_tracks_are_recentred_to_one_zero():
    """★ OWNER, 2026-09-13: "track difficulties are a lot more negative for
    college than for hs". Two populations that never share a track have no
    common level; each population's outdoor tracks are recentred to the
    same zero, indoor ovals move with their population, a mixed track
    counts as its own group, and the report names the shift."""
    keys = ([f"TF:loc:{i}:out" for i in range(30)] + [f"TF:loc:{100 + i}:in" for i in range(4)]
            + ["XC:7:d5000"])
    n_cell = len(keys)
    # cells 0-14 host high school rows, 15-29 college rows, 30-31 indoor hs,
    # 32-33 indoor college; the XC cell is untouched
    rows_cell, rows_level = [], []
    for c in range(15):
        rows_cell += [c] * 10; rows_level += ["hs"] * 10
    for c in range(15, 30):
        rows_cell += [c] * 10; rows_level += ["college"] * 10
    for c in (30, 31):
        rows_cell += [c] * 10; rows_level += ["hs"] * 10
    for c in (32, 33):
        rows_cell += [c] * 10; rows_level += ["college"] * 10
    rows_cell += [34] * 10; rows_level += ["hs"] * 10
    rng = np.random.default_rng(3)
    D_b = rng.normal(0, 0.005, n_cell)
    D_b[15:30] -= 0.02                       # the college cluster reads 2% easy
    D_b[32:34] += 0.003 - 0.02               # college ovals: the same level, plus indoor
    D_b[30:32] += 0.003
    D_b[34] = 0.09
    mc = np.zeros(len(rows_cell)); mc[150:300] = 3          # the college rows are finals
    shift, rows = rj.trackPopulationShift(D_b, keys, rows_cell, rows_level, mc, min_cells=5)
    after = D_b - shift
    assert abs(after[:15].mean()) < 1e-12 and abs(after[15:30].mean()) < 1e-12
    # the indoor ovals kept their level over their own population's outdoor
    assert abs((after[30:32].mean() - after[:15].mean()) - (D_b[30:32].mean() - D_b[:15].mean())) < 1e-12
    assert abs((after[32:34].mean() - after[15:30].mean()) - (D_b[32:34].mean() - D_b[15:30].mean())) < 1e-12
    assert after[34] == D_b[34]              # not a track
    by = {r["population"]: r for r in rows}
    assert abs(by["college"]["shift"] - D_b[15:30].mean()) < 1e-12
    assert by["college"]["champ_share"] == 1.0 and by["hs"]["champ_share"] == 0.0
    assert by["college"]["applied"] and by["hs"]["applied"]
    # a population under the cell floor is reported and left alone
    shift2, rows2 = rj.trackPopulationShift(D_b, keys, rows_cell, rows_level, mc, min_cells=16)
    assert not np.any(shift2) and all(not r["applied"] for r in rows2)
