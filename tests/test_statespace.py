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


def test_ss_eval_runs_on_a_pack_shaped_file(tmp_path):
    D = sw.world(seed=4, n_regions=3, schools=8)
    n = D["y"].size
    pack_date = np.datetime64("2026-10-20")
    # seasons 0..4 are 2021..2025; the pack closes in the 2026 season
    dates = np.array([np.datetime64(f"{2021 + s}-08-01") + np.timedelta64(int(p), "D")
                      for s, p in zip(D["season"], D["phase"])], dtype="datetime64[D]")
    # one row in the running season so the windows resolve
    keys_a = [(int(p), ("hs_m" if lv == 0 else "college_m")) for p, lv in
              sorted(set(zip(D["person"].tolist(), D["lv"].tolist())))]
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
    norm = np.exp(D["y"]) * np.where(D["lv"] == 1, 1.0, 1.0)
    scal = {"athlete_keys": keys_a, "course_keys": ckeys}
    path = tmp_path / "packed_XC_TF.npz"
    np.savez(path, __scalars__=np.array([repr(scal)]), result_id=np.arange(n),
             athlete=athlete, course=course, norm=norm.astype(np.float32),
             days=days.astype(np.int32), sport=(1 - D["xc"]).astype(np.int8),
             doy=doy.astype(np.int16), year=year.astype(np.int16),
             dist_m=np.full(n, 5000.0, dtype=np.float32),
             meet_id=D["day"].astype(np.int64), div_id=D["race"].astype(np.int64))
    out = tmp_path / "ss.npz"
    r = subprocess.run([sys.executable, os.path.join(HERE, "..", "scripts", "ss_eval.py"),
                        "--pack", str(path), "--outer", "5", "--out", str(out)],
                       capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
    assert "scorecard" in r.stdout and out.exists()

    # a "current engine" dump on those rows, a little worse, then paired
    z = dict(np.load(out))
    rng = np.random.default_rng(0)
    m = z["row"].size
    base = tmp_path / "base.npz"
    np.savez(base, row=z["row"], y=z["y"], pred=z["pred"] + rng.normal(0, 0.02, m),
             covered=np.ones(m, dtype=bool), group=z["group"], race=z["group"],
             sport=z["sport"], pool=np.array(["hs_m"] * m), window=z["window"],
             sample_pct=np.array([100.0]), sample_seed=np.array([11]))
    r = subprocess.run([sys.executable, os.path.join(HERE, "..", "scripts", "ss_eval.py"),
                        "--pack", str(path), "--outer", "5", "--base-dump", str(base),
                        "--out", str(tmp_path / "ss2.npz")],
                       capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
    assert "SCORECARD on" in r.stdout
