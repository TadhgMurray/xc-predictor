"""The state-space prototype (engine/statespace.py; docs/REVIEW-ENGINE-
2026-10.md §5): on a planted world it must (1) beat the same code held to
the current engine's shape on held-out races and on the spring track,
(2) rate a mis-measured division about as well as an ordinary one, and
(3) recover the tilt with the right sign. And scripts/ss_eval.py must run
end to end on a pack-shaped file built from that world.

    python -m pytest -q tests/test_statespace.py
"""
import os
import subprocess
import sys

import numpy as np
import pytest

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "engine"))
import statespace as ss                                         # noqa: E402
import statespace_world as sw                                   # noqa: E402

GAP = {ss.GROUP_XC: sw.TRUE["mu_xc"]}
A = dict(dynamic=False, nested=False, heavy=False)


@pytest.fixture(scope="module")
def world():
    D = sw.world(seed=3, n_regions=6, schools=16)
    return D, sw.splits(D)


def test_walk_beats_season_ability_on_held_out(world):
    D, S = world
    for split in ("race", "sport"):
        tr, sc = S[split]
        ra = ss.score(D["y"], ss.fit(D, tr, outer=20, fix_mu=GAP, **A)["pred"], D["race"], sc)
        rb = ss.score(D["y"], ss.fit(D, tr, outer=20, fix_mu=GAP)["pred"], D["race"], sc)
        assert rb["h2h"] > ra["h2h"] + 0.005, (split, ra, rb)
        assert rb["med_abs"] < ra["med_abs"], (split, ra, rb)


def test_a_short_division_is_absorbed_by_its_own_race(world):
    D, _ = world
    R = ss.fit(D, np.ones(D["y"].size, dtype=bool), outer=20, fix_mu=GAP)
    fig = D["y"] - R["h"] * (R["mu"][D["group"]] + R["cell"][D["cell"]]
                             + R["day"][D["day"]] + R["race"][D["race"]])
    err = fig - D["truth_perf"]
    bad = D["bad_race"]
    err -= np.median(err[~bad])
    assert np.median(np.abs(err[bad])) < 1.5 * np.median(np.abs(err[~bad]))


def test_the_tilt_comes_back_with_its_sign(world):
    D, _ = world
    R = ss.fit(D, np.ones(D["y"].size, dtype=bool), outer=20, fix_mu=GAP)
    assert 0.1 < R["kappa"] < 0.45


def _realised(D):
    """The planted gaps as the model defines them: against the AVERAGE
    planted course of each group in this world (finite-sample draws)."""
    tc = np.asarray(D["truth_course"]); g = D["group"]
    _, first = np.unique(D["cell"], return_index=True)
    cm = {gg: tc[first[g[first] == gg]].mean() for gg in range(3)}
    T = D["truth"]
    return T["mu_xc"] + cm[0] - cm[1], T["mu_in"] + cm[2] - cm[1]


def test_the_gap_is_measured_through_the_runners_trajectories():
    """The hard world: tapers, December breaks, only the best racing
    December XC, the gap planted at 6% XC / 1% indoor. Naive pair
    differences miss by up to 3.5%; the GLS through each runner's filter
    lands within 1% of the realised gap, and holds without any one kind of
    bridge but the fall / summer track ones (review §4.4)."""
    D = sw.worldHard(seed=0)
    rx, ri = _realised(D)
    R = ss.fitGLS(D, np.ones(D["y"].size, dtype=bool), outer=15)
    assert abs(R["gap_ref"][ss.GROUP_XC] - rx) < 0.01, (R["gap_ref"], rx)
    assert abs(R["gap_ref"][ss.GROUP_IN] - ri) < 0.01, (R["gap_ref"], ri)
    assert -0.02 < R["b"][R["blocks"]["peak"][0]] < 0.0      # the taper, planted -1%
    masks = ss.bridgeMasks(D)
    E = ss.subsetRows(D, ~masks["december_xc"])
    Rk = ss.fitGLS(E, np.ones(E["y"].size, dtype=bool), outer=15)
    assert abs(Rk["gap_ref"][ss.GROUP_XC] - rx) < 0.01


def _packFromWorld(D, path):
    """A pack-shaped npz from a planted world (what speed_ratings writes)."""
    n = D["y"].size
    pack_date = np.datetime64("2026-10-20")
    dates = np.array([np.datetime64(f"{2021 + int(s)}-08-01") + np.timedelta64(int(p), "D")
                      for s, p in zip(D["season"], D["phase"])], dtype="datetime64[D]")
    keys_a = sorted({(int(p), "hs_m" if lv == 0 else "college_m")
                     for p, lv in zip(D["person"].tolist(), D["lv"].tolist())})
    a_code = {k: i for i, k in enumerate(keys_a)}
    athlete = np.array([a_code[(int(p), "hs_m" if lv == 0 else "college_m")]
                        for p, lv in zip(D["person"], D["lv"])])
    grp_name = {ss.GROUP_XC: "XC:v{}:d5000", ss.GROUP_OUT: "TF:loc:{}:out",
                ss.GROUP_IN: "TF:loc:{}:in"}
    ckeys, cmap = [], {}
    for c, g in zip(D["cell"], D["group"]):
        if c not in cmap:
            cmap[c] = len(ckeys)
            ckeys.append(grp_name[int(g)].format(int(c)))
    course = np.array([cmap[c] for c in D["cell"]])
    days = (pack_date - dates).astype(np.int64)
    year = dates.astype("datetime64[Y]").astype(np.int64) + 1970
    doy = (dates - dates.astype("datetime64[Y]").astype("datetime64[D]")).astype(np.int64) + 1
    scal = {"athlete_keys": keys_a, "course_keys": ckeys}
    # ! STORED ON THE POOL'S OWN ANCHOR, as the real pack is: ss_eval moves
    #   every row back onto 5000 m with the pool's curve, so the planted
    #   5000 m times are divided by that same move here (a college row
    #   stored as its 5000 m time made the HS gap read 9% against 7%).
    import normalize_distance as nd
    pool = np.where(D["lv"] == 0, "hs_m", "college_m")
    shift = np.array([nd.anchorShift(pl, "XC" if x else "TF") for pl, x in zip(pool, D["xc"])])
    rng = np.random.default_rng(0)
    clat = rng.uniform(30, 45, len(ckeys)); clon = rng.uniform(-120, -75, len(ckeys))
    np.savez(path, __scalars__=np.array([repr(scal)]), result_id=np.arange(n),
             course_lat=clat, course_lon=clon,
             athlete=athlete, course=course, norm=(np.exp(D["y"]) / shift).astype(np.float32),
             days=days.astype(np.int32), sport=(1 - D["xc"]).astype(np.int8),
             doy=doy.astype(np.int16), year=year.astype(np.int16),
             dist_m=np.where(D["xc"] == 1, 5000.0, 1600.0).astype(np.float32),
             meet_id=D["day"].astype(np.int64), div_id=D["race"].astype(np.int64),
             team=D["team"].astype(np.int32))


def test_a_mislabelled_race_is_rated_at_its_likely_distance_never_relabelled():
    """5% of divisions ran another distance than listed (a 5000 that was 3
    miles, a 1600 that was 2000). The mixture flags them, finds the right
    distance for most, and the ratings of their runners come back close;
    the listed distance is never changed."""
    D = sw.worldHard(seed=3, n_regions=6, schools=12, mislabel_p=0.05)
    listed = D["dist_m"].copy()
    allr = np.ones(D["y"].size, dtype=bool)
    terms = ("peak", "recovery", "event", "mu_by_level")
    mis, bad, xc = D["mislabel"], D["bad_race"], D["group"] == ss.GROUP_XC

    def p90(R):
        err = R["perf"] - D["truth_perf"]
        err = err - np.median(err[~bad & ~xc & ~mis])
        return np.percentile(np.abs(err[mis]), 90), np.median(np.abs(err[~bad & ~mis]))
    t_only = p90(ss.fitGLS(D, allr, outer=15, terms=terms))
    R = ss.fitGLS(D, allr, outer=15, terms=terms + ("dist_mix",))
    mixed = p90(R)
    assert np.array_equal(D["dist_m"], listed)
    assert mixed[0] < 0.7 * t_only[0]                 # the mislabelled rows' tail
    assert mixed[1] < t_only[1] + 0.0005              # and nothing else pays for it
    nr = R["race_p_mis"].size
    rm = np.bincount(D["race"], weights=mis.astype(float), minlength=nr) > 0
    rb = np.bincount(D["race"], weights=bad.astype(float), minlength=nr) > 0
    seen = np.bincount(D["race"], minlength=nr) > 0
    flagged = (R["race_p_mis"] > 0.5) & seen
    assert (flagged & rm).sum() > 0.6 * rm.sum()
    assert (flagged & ~rm & ~rb).sum() < 0.005 * (seen & ~rm & ~rb).sum()
    true_d = np.bincount(D["race"], weights=D["true_dist"], minlength=nr) / np.maximum(
        np.bincount(D["race"], minlength=nr), 1)
    right = np.abs(R["race_alt_dist"] - true_d) < 50
    assert right[flagged & rm].mean() > 0.85


def test_one_course_under_two_keys_is_found_from_runners_and_nearby_courses_are_not():
    """Some meets key a course under a second name, half of them listed at
    4828 for the same 5000; some different courses sit 150-600 m from
    another. Pairs are judged on runners who raced both: the aliases are
    found, and no nearby different course is tied."""
    D = sw.worldHard(seed=2, n_regions=24, schools=8, alias_p=0.15, near_p=0.3)
    terms = ("peak", "recovery", "event", "mu_by_level", "course_tie")
    R = ss.fitGLS(D, np.ones(D["y"].size, dtype=bool), outer=12, terms=terms)
    a, b, p = R["pair_a"], R["pair_b"], R["pair_p"]
    tcell = np.zeros(D["cell"].max() + 1, dtype=np.int64)
    tcell[D["cell"]] = D["truth_cell"]
    same = tcell[a] == tcell[b]
    assert same.sum() > 20 and (~same).sum() > 5
    assert (p[same] > 0.5).mean() > 0.8
    assert (p[~same] > 0.5).sum() == 0


def test_a_courses_number_walks_and_the_walk_is_measured():
    """Each course drifts 0.8% a season (and is now and then re-routed): a
    cell per course-season tied by a walk finds about that drift and
    forecasts the next season at least as well as one number per course."""
    D = sw.worldHard(seed=1, n_regions=12, schools=10, era_sd=0.008, reroute_p=0.05)
    E = ss.eraCells(D)
    base = ("peak", "recovery", "event", "mu_by_level")
    last = D["season"] == D["season"].max()
    R = ss.fitGLS(E, ~last, outer=12, terms=base + ("era_walk",))
    assert 0.004 < R["era_sd"] < 0.016
    one = ss.fitGLS(D, ~last, outer=12, terms=base)
    f_walk = ss.score(D["y"], R["pred"], D["race"], last)["med_abs"]
    f_one = ss.score(D["y"], one["pred"], D["race"], last)["med_abs"]
    assert f_walk <= f_one + 0.0005


def _ratingErr(D, R, m):
    bad, xc = D["bad_race"], D["group"] == ss.GROUP_XC
    err = R["perf"] - D["truth_perf"]
    err = err - np.median(err[~bad & ~xc])
    return float(np.median(np.abs(err[m & ~bad])))


def test_a_team_at_tempo_is_taken_out_of_its_runners_ratings():
    D = sw.worldHard(seed=5, n_regions=6, schools=12, team_p=0.04)
    allr = np.ones(D["y"].size, dtype=bool)
    base = ("peak", "recovery", "event", "mu_by_level")
    tempo = D["tempo"] > 0
    off = _ratingErr(D, ss.fitGLS(D, allr, outer=12, terms=base), tempo)
    on = _ratingErr(D, ss.fitGLS(D, allr, outer=12, terms=base + ("team_race",)), tempo)
    assert on < 0.85 * off


def test_the_runners_own_distance_curve_moves_track_results_to_their_5000():
    """800s, 1600s and 3200s, each runner with their own slope (sd 3% per
    unit ln distance): the fourth state finds the spread and the track
    ratings come back near the planted 5000."""
    D = sw.worldHard(seed=5, n_regions=6, schools=12, track_mix=True)
    allr = np.ones(D["y"].size, dtype=bool)
    base = ("peak", "recovery", "event", "mu_by_level")
    track = D["group"] != ss.GROUP_XC
    off = _ratingErr(D, ss.fitGLS(D, allr, outer=12, terms=base), track)
    R = ss.fitGLS(D, allr, outer=12, terms=base + ("endurance",))
    assert _ratingErr(D, R, track) < 0.6 * off
    assert 0.02 < R["eta_sd"] < 0.04


def test_weather_fitted_inside_beats_a_correction_keyed_on_the_wrong_temperature():
    """The stored correction reads the day's peak for a race run up to 15 F
    cooler. Taken off and fitted from the runners themselves, the ratings
    land closer, hot days most of all."""
    D = sw.worldHard(seed=5, n_regions=6, schools=12, weather=True)
    allr = np.ones(D["y"].size, dtype=bool)
    base = ("peak", "recovery", "event", "mu_by_level")
    hot = np.nan_to_num(D["wx_temp"]) > 75
    stored = ss.fitGLS(D, allr, outer=12, terms=base)
    U = dict(D); U["y"] = D["y"] - D["wx_log_mult"]
    fitted = ss.fitGLS(U, allr, outer=12, terms=base + ("weather",))
    every = np.ones(D["y"].size, dtype=bool)
    assert _ratingErr(D, fitted, every) < _ratingErr(D, stored, every)
    assert _ratingErr(D, fitted, hot) < 0.8 * _ratingErr(D, stored, hot)


def test_ss_eval_runs_on_a_pack_shaped_file(tmp_path):
    D = sw.worldHard(seed=4, n_regions=4, schools=8)
    path = tmp_path / "packed_XC_TF.npz"
    _packFromWorld(D, path)
    script = os.path.join(HERE, "..", "scripts", "ss_eval.py")
    env = dict(os.environ)
    elev = tmp_path / "elev.csv"
    elev.write_text("".join(f"XC:v{c},{1600 if c % 7 == 0 else 100}\n" for c in range(60)))
    r = subprocess.run([sys.executable, script, "--pack", str(path), "--mode", "gap",
                        "--outer", "6", "--jack-outer", "3", "--repo", str(tmp_path / "repo"),
                        "--elevation", str(elev)],
                       capture_output=True, text=True, timeout=900, env=env)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
    assert "levels against the flat outdoor gauge" in r.stdout
    assert "without fall_track" in r.stdout
    assert "regional check" in r.stdout and "altitude per km" in r.stdout

    # a "current engine" dump on the forward window, a little worse, then paired
    sys.path.insert(0, os.path.join(HERE, "..", "engine"))
    import forward_holdout as fh
    sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
    import ss_eval
    cols = ss_eval.loadPack(str(path))
    w = fh.forwardWindow(cols, verbose=False)
    dates, _, _ = fh.rowDates(cols)
    rows = np.flatnonzero((dates >= w["from"]) & (dates < w["until"]))
    rng = np.random.default_rng(0)
    y = np.log(cols["norm"][rows].astype(np.float64))
    base = tmp_path / "base.npz"
    race = cols["div_id"][rows]
    np.savez(base, row=rows, y=y, pred=y + rng.normal(0, 0.03, rows.size),
             covered=np.ones(rows.size, dtype=bool), group=race, race=race,
             sport=cols["sport"][rows], pool=np.array(["hs_m"] * rows.size),
             window=np.array([str(w["from"]), str(w["until"])]),
             sample_pct=np.array([100.0]), sample_seed=np.array([11]))
    r = subprocess.run([sys.executable, script, "--pack", str(path), "--mode", "all",
                        "--outer", "6", "--jack-outer", "3", "--no-jackknife",
                        "--base-dump", str(base), "--repo", str(tmp_path / "repo")],
                       capture_output=True, text=True, timeout=900, env=env)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
    assert "SCORECARD on" in r.stdout and "ratings ->" in r.stdout
    out = list((tmp_path / "repo" / "engine" / "data" / "ss").glob("ratings_*.npz"))
    assert out
    z = np.load(out[0])
    assert np.all(np.isfinite(z["five_k_s"])) and z["result_id"].size > 0
    assert np.all(np.isfinite(z["perf_sd"]))

    # the real run's flags: the weather file, the course walk, a term dropped
    n = D["y"].size
    rng = np.random.default_rng(1)
    wx = tmp_path / "weather.npz"
    np.savez(wx, result_id=np.arange(n), sport=(1 - D["xc"]).astype(np.int8),
             temp=rng.uniform(40, 90, n), wind=rng.gamma(2.0, 3.0, n),
             wet=rng.uniform(0, 0.5, n), log_mult=np.zeros(n))
    r = subprocess.run([sys.executable, script, "--pack", str(path), "--mode", "gap",
                        "--outer", "4", "--no-jackknife", "--repo", str(tmp_path / "repo"),
                        "--weather", str(wx), "--era-walk", "--drop", "endurance"],
                       capture_output=True, text=True, timeout=900, env=env)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
    assert "weather" in r.stdout.split("[ss] terms:")[1].splitlines()[0]
    assert "era_walk" in r.stdout and "endurance" not in r.stdout.split("[ss] terms:")[1].splitlines()[0]
    assert "[ss] distance:" in r.stdout and "[ss] courses:" in r.stdout
