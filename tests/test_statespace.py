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
    np.savez(path, __scalars__=np.array([repr(scal)]), result_id=np.arange(n),
             athlete=athlete, course=course, norm=np.exp(D["y"]).astype(np.float32),
             days=days.astype(np.int32), sport=(1 - D["xc"]).astype(np.int8),
             doy=doy.astype(np.int16), year=year.astype(np.int16),
             dist_m=np.where(D["xc"] == 1, 5000.0, 1600.0).astype(np.float32),
             meet_id=D["day"].astype(np.int64), div_id=D["race"].astype(np.int64))


def test_ss_eval_runs_on_a_pack_shaped_file(tmp_path):
    D = sw.worldHard(seed=4, n_regions=4, schools=8)
    path = tmp_path / "packed_XC_TF.npz"
    _packFromWorld(D, path)
    script = os.path.join(HERE, "..", "scripts", "ss_eval.py")
    env = dict(os.environ)
    r = subprocess.run([sys.executable, script, "--pack", str(path), "--mode", "gap",
                        "--outer", "6", "--jack-outer", "3", "--repo", str(tmp_path / "repo")],
                       capture_output=True, text=True, timeout=900, env=env)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
    assert "levels against the flat outdoor gauge" in r.stdout
    assert "without fall_track" in r.stdout

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
