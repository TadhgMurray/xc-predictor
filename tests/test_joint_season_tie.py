"""The season tie (owner, 2026-09-29: "try to be safe and test it"):
consecutive athlete-seasons of one person tied by a random walk whose mean
change and width are FITTED per transition on the untied first pass.

A world with a KNOWN walk: every athlete improves m = -3%/yr (log-time)
with year-to-year sd 3%, most seasons are full (8 races) and a fifth are
thin (2 races). The planted case is Jack Moretta's shape: two full seasons
and then two races, one of them an easy long run.

    python -m pytest -q tests/test_joint_season_tie.py
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine"))
import joint_solve as js                                        # noqa: E402
import run_joint as rj                                          # noqa: E402

TRUE_M = -0.03          # log-time per year: 3% faster each year
TRUE_S = 0.03           # year-to-year sd per sqrt(year)
NOISE = 0.03
YEARS = (2023, 2024, 2025)


def world(seed=0, n_person=700, thin_frac=0.2, full_races=8, thin_races=2,
          plant=True):
    """Rows of a three-season world, the design's codes and the truth.

    Athlete-season code = person * 3 + year index; one pool (hs_m) so the
    raw athlete code is the person. Returns a dict."""
    rng = np.random.default_rng(seed)
    n_cell = 40
    d_true = rng.normal(0, 0.04, n_cell)
    a0 = rng.normal(0, 0.10, n_person)
    steps = rng.normal(TRUE_M, TRUE_S, (n_person, len(YEARS) - 1))
    truth = np.c_[a0, a0 + steps[:, 0], a0 + steps.sum(axis=1)]
    races = np.full((n_person, len(YEARS)), full_races)
    thin = rng.random((n_person, len(YEARS))) < thin_frac
    races[thin] = thin_races
    offset = np.zeros(n_person * len(YEARS))          # a planted bad day
    if plant:
        # person 0: full, full, then TWO races -- Jack's shape. The truth
        # keeps improving; one of the two is an easy long run, 15% slow.
        truth[0] = [0.0, TRUE_M, 2 * TRUE_M]
        races[0] = [full_races, full_races, thin_races]
    # race days: per year 80 (cell, day) races with their own day effect
    n_day = 80
    race_cell = rng.integers(0, n_cell, (len(YEARS), n_day))
    race_doy = rng.integers(240, 330, (len(YEARS), n_day))    # Sep-Nov
    race_u = rng.normal(0, 0.015, (len(YEARS), n_day))
    ath, cel, rac, days, yr, easy = [], [], [], [], [], []
    for p in range(n_person):
        for j, year in enumerate(YEARS):
            pick = rng.choice(n_day, size=races[p, j], replace=False)
            for q, r in enumerate(pick):
                ath.append(p * len(YEARS) + j)
                cel.append(race_cell[j, r])
                rac.append(j * n_day + r)
                # days ago from 1 Jan 2026
                days.append((2026 - year) * 365 - race_doy[j, r])
                yr.append(year)
                easy.append(plant and p == 0 and j == 2 and q == 0)
    ath = np.array(ath); cel = np.array(cel); rac = np.array(rac)
    days = np.array(days); yr = np.array(yr); easy = np.array(easy)
    j_of = ath % len(YEARS)
    y = (truth.reshape(-1)[ath] + d_true[cel]
         + race_u.reshape(-1)[rac] + rng.normal(0, NOISE, ath.size))
    y = y + np.where(easy, 0.15, 0.0)
    return dict(y=y, ath=ath, cel=cel, rac=rac, days=days, year=yr,
                person=ath // len(YEARS), j=j_of, truth=truth.reshape(-1),
                races=races.reshape(-1), n_person=n_person, n_cell=n_cell)


def _cols(w, pools=None, sport=None):
    """The pack columns seasonTiePairs reads: raw athlete code per row (the
    person here, or person x pool), days ago, athlete_keys (person, pool)."""
    raw = w["person"] if pools is None else w["raw"]
    keys = ([(str(1000 + p), "hs_m") for p in range(w["n_person"])]
            if pools is None else w["keys"])
    cols = {"athlete": raw, "days": w["days"], "athlete_keys": keys}
    if sport is not None:
        cols["sport"] = sport
    return cols


def tieDesign(w, tie=True):
    D = js.Design(w["ath"], w["cel"], w["rac"],
                  n_ath=w["n_person"] * len(YEARS))
    if tie:
        cols = _cols(w)
        keep = np.ones(w["ath"].size, dtype=bool)
        pool_of_raw = np.zeros(w["n_person"], dtype=np.int64)
        k0, k1, dt, typ, names, _floor = rj.seasonTiePairs(
            cols, keep, w["ath"], D.n_ath, pool_of_raw, ["hs_m"])
        js.attachSeasonTie(D, k0, k1, dt, typ, names)
    return D


def solve(w, tie=True, **kw):
    D = tieDesign(w, tie)
    pool = np.zeros(D.n_ath, dtype=np.int64)
    out = js.solveJoint(w["y"], design=D, athlete_pool=pool, n_outer=4,
                        tilt=False, n_probe=0, robust=False,
                        sigma_u_floor=0.0, **kw)
    return out, D


_CACHE = {}


def _pair(seed=0):
    if seed not in _CACHE:
        w = world(seed)
        _CACHE[seed] = (w, solve(w, tie=False), solve(w, tie=True))
    return _CACHE[seed]


def _centred(a, w):
    """Ability less its gauge: the constant that makes the full seasons'
    mean equal the truth's (the solve's zero is the courses', not ours)."""
    full = w["races"] >= 8
    return a - (a[full].mean() - w["truth"][full].mean())


def test_the_pairs_are_one_persons_seasons_in_date_order():
    w = world(n_person=5, plant=False)
    D = tieDesign(w)
    assert D.n_tie == 5 * 2, D.n_tie
    assert (D.tie_k0 // 3 == D.tie_k1 // 3).all(), "a pair crossed two people"
    assert (D.tie_k1 % 3 == D.tie_k0 % 3 + 1).all(), "not consecutive in time"
    # the dates are Sep-Nov of consecutive years: about a year apart
    assert np.all(np.abs(D.tie_dt - 1.0) < 0.25), D.tie_dt
    assert D.tie_type_names == ["hs_m>hs_m"]


def test_the_walk_is_measured_not_assumed():
    """m and s come back from the untied pass near the planted walk."""
    w, _untied, (out, D) = _pair()
    lines = "\n".join(out["season_tie_lines"])
    assert "hs_m>hs_m" in lines and "fitted" in lines, lines
    # recover the fitted numbers from the per-pair arrays: mean = m * dt,
    # inverse variance = 1 / (s^2 dt), in row units times sigma2
    tied = out["tie_w"] > 0
    m = np.median(out["tie_mean"][tied] / D.tie_dt[tied])
    s = np.sqrt(np.median(out["sigma2"] / (out["tie_w"][tied] * D.tie_dt[tied])))
    assert abs(m - TRUE_M) < 0.008, m
    assert 0.6 * TRUE_S < s < 1.4 * TRUE_S, s
    print(f"  fitted m {m:+.4f} (true {TRUE_M}), s {s:.4f} (true {TRUE_S}) ... OK")


def test_a_two_race_season_moves_toward_its_neighbours():
    """Jack's shape: full, full, then two races with an easy long run. Untied
    the season reads the easy run; tied it moves toward where last year and
    the measured improvement put it."""
    w, (a_u, _D), (a_t, _Dt) = _pair()[0], _pair()[1], _pair()[2]
    a_u = _centred(a_u["ability"], w)
    a_t = _centred(a_t["ability"], w)
    k = 2                                   # person 0, the third season
    err_u = a_u[k] - w["truth"][k]
    err_t = a_t[k] - w["truth"][k]
    assert err_u > 0.05, f"the planted easy run should read slow untied: {err_u}"
    # the Bayes share for two races against one full neighbour at this
    # world's sigma and walk is about a quarter of the deviation
    moved = a_u[k] - a_t[k]
    assert err_t < 0.85 * err_u, (err_u, err_t)
    assert moved > 0.15 * err_u, moved
    # and every thin season, not just the planted one, is closer to the truth
    thin = w["races"] == 2
    rmse_u = np.sqrt(np.mean((a_u[thin] - w["truth"][thin]) ** 2))
    rmse_t = np.sqrt(np.mean((a_t[thin] - w["truth"][thin]) ** 2))
    assert rmse_t < 0.9 * rmse_u, (rmse_u, rmse_t)
    print(f"  Jack-shaped season: {100 * err_u:+.2f}% off untied, "
          f"{100 * err_t:+.2f}% tied (moved {100 * moved:.2f}%); thin-season "
          f"rmse {rmse_u:.4f} -> {rmse_t:.4f} ... OK")


def test_a_full_season_barely_moves_and_improvement_is_not_damped():
    """⚠ THE ZERO-MEAN LINK'S FAILURE (2026-09-04), pinned. Full seasons move
    by a small fraction of their own standard error, and the population's
    measured improvement survives the tie: the mean change between
    consecutive full seasons is the same tied as untied."""
    w, (out_u, _D), (out_t, D) = _pair()
    a_u = _centred(out_u["ability"], w)
    a_t = _centred(out_t["ability"], w)
    full = w["races"] == 8
    se_full = np.sqrt(out_u["sigma2"] / 8.0)
    move = np.abs(a_t - a_u)[full]
    assert np.median(move) < 0.25 * se_full, (np.median(move), se_full)
    assert np.percentile(move, 90) < 0.6 * se_full, (np.percentile(move, 90), se_full)
    # improvement survives: consecutive full-full pairs, mean change
    both = full[D.tie_k0] & full[D.tie_k1]
    ch_u = (a_u[D.tie_k1] - a_u[D.tie_k0])[both].mean()
    ch_t = (a_t[D.tie_k1] - a_t[D.tie_k0])[both].mean()
    assert abs(ch_t - ch_u) < 0.002, (ch_u, ch_t)
    assert abs(ch_t - TRUE_M) < 0.006, ch_t
    # the thin seasons move several times as far as the full ones
    thin = w["races"] == 2
    assert np.median(np.abs(a_t - a_u)[thin]) > 3 * np.median(move)
    print(f"  full seasons: median move {100 * np.median(move):.3f}% against "
          f"their own se {100 * se_full:.2f}%; mean yearly change "
          f"{100 * ch_u:+.2f}% untied, {100 * ch_t:+.2f}% tied ... OK")


def test_the_move_report_matches_the_actual_move():
    """run_joint.seasonTieMoves reads the displacement off the solution:
    against the SAME courses, the tied ability less the rows' own mean."""
    w, _u, (out, D) = _pair()
    mv = rj.seasonTieMoves(out["ability_raw"] if "ability_raw" in out
                           else out["ability"], D, out["weights"],
                           out["tie_w"], out["tie_mean"])
    b = D.unpack(out["theta"])
    resid = w["y"] - js.rowPrediction(b, D, out["h"], out["amp"]) + b["a"][D.athlete]
    den = np.bincount(D.athlete, weights=out["weights"], minlength=D.n_ath)
    own = np.bincount(D.athlete, weights=out["weights"] * resid,
                      minlength=D.n_ath) / den
    np.testing.assert_allclose(b["a"] - own, mv, atol=2e-5)


def test_tied_abilities_is_the_mean_untied_and_the_solve_tied():
    """bracketDifficulties' ability step: exactly the weighted mean without a
    tie, and with one the solve's own tied ability given the same courses."""
    w, _u, (out, D) = _pair()
    b = D.unpack(out["theta"])
    resid = w["y"] - js.rowPrediction(b, D, out["h"], out["amp"]) + b["a"][D.athlete]
    den = np.bincount(D.athlete, weights=out["weights"], minlength=D.n_ath)
    num = np.bincount(D.athlete, weights=out["weights"] * resid, minlength=D.n_ath)
    plain = js.tiedAbilities(den, num, D, None, None, b["a"])
    np.testing.assert_allclose(plain, num / den)
    tied = js.tiedAbilities(den, num, D, out["tie_w"], out["tie_mean"], plain)
    np.testing.assert_allclose(tied, b["a"], atol=2e-5)


def test_a_pool_change_is_its_own_transition():
    """By PERSON, across pools: hs_m spring then college_m autumn of the same
    calendar year is one pair, half a year apart, and a transition of its
    own -- never averaged with hs_m>hs_m."""
    # person 7: hs_m 2025 (raw 0), hs_m 2026 spring (raw 0), college_m 2026
    # autumn (raw 1). Person 8: hs_m only.
    raw = np.array([0, 0, 0, 0, 1, 1, 2, 2])
    days = np.array([400, 380, 60 + 200, 50 + 200, 60, 40, 400, 30])
    year = np.array([2025, 2025, 2026, 2026, 2026, 2026, 2025, 2026])
    ath, n_ath = js_codes(raw, year)
    cols = {"athlete": raw, "days": days,
            "athlete_keys": [("7", "hs_m"), ("7", "college_m"), ("8", "hs_m")]}
    keep = np.ones(raw.size, dtype=bool)
    k0, k1, dt, typ, names, floor = rj.seasonTiePairs(
        cols, keep, ath, n_ath, np.array([0, 1, 0]), ["hs_m", "college_m"])
    pairs = sorted(zip(k0.tolist(), k1.tolist()))
    assert len(pairs) == 3, pairs
    by = {(int(a), int(b)): names[t] for a, b, t in zip(k0, k1, typ)}
    s_hs26, s_col26 = ath[2], ath[4]
    assert by[(int(s_hs26), int(s_col26))] == "hs_m>college_m", by
    i = [n for n in names].index("hs_m>college_m")
    assert np.all(dt[typ == i] < 0.75), dt


def test_a_change_of_pool_is_a_step_not_a_rate():
    """hs_m>college_m moves the ability into another pool's units once. Read
    as a rate, a step over a two-year gap looked half the size of one over
    six months and the walk widened to cover it; fitted as a step plus the
    college pool's own yearly change, the step and the width come back."""
    rng = np.random.default_rng(7)
    n = 3000
    names = ["hs_m>hs_m", "college_m>college_m", "hs_m>college_m"]
    typ = np.repeat([0, 1, 2], n)
    dt = np.r_[np.full(n, 1.0), np.full(n, 1.0), rng.uniform(0.5, 3.0, n)]
    step = np.r_[np.zeros(2 * n), np.full(n, 0.42)]
    rate = np.r_[np.full(n, -0.03), np.full(n, -0.01), np.full(n, -0.01)]
    delta = step + rate * dt + rng.normal(0, 0.03, 3 * n) * np.sqrt(dt)
    k0 = np.arange(3 * n) * 2
    k1 = k0 + 1
    a = np.zeros(6 * n)
    a[k1] = delta
    v = np.full(6 * n, 1e-6)
    fit = js.seasonTieFit(a, v, None, np.full(6 * n, 8), k0, k1, dt, typ, names)
    rows = {r[0]: r for r in fit["table"] if r[1] is None}
    _n, _b, _na, _nf, c, m, s, how = rows["hs_m>college_m"]
    assert how == "fitted" and abs(c - 0.42) < 0.005, (how, c)
    assert abs(m + 0.01) < 0.003, m          # the college pool's own rate
    assert abs(s - 0.03) < 0.004, s
    cross = typ == 2
    np.testing.assert_allclose(fit["mean"][cross], c + m * dt[cross])


def js_codes(raw, year):
    import pair_engine as pe
    return pe.athleteSeasonCodes(raw, year)


def test_off_is_the_old_solve():
    """No --season-tie: the design has no pairs and the solve is unchanged,
    bit for bit."""
    w = world(seed=1, n_person=120)
    D = tieDesign(w, tie=False)
    assert not getattr(D, "n_tie", 0)
    out = js.solveJoint(w["y"], design=D, n_outer=2, tilt=False, n_probe=0)
    assert out["tie_w"] is None and out["season_tie_lines"] is None
    parser = rj.buildParser()
    args = rj.applyImplications(parser.parse_args([]), parser)
    assert args.season_tie is False and args.tilt_scale == "own"
    assert rj.sharedTermKwargs(args)["season_tie"] is False
    assert rj.solveKwargs(args, None, verbose=False)["tilt_scale"] is None
