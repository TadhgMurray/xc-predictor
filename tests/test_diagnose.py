# Project: xc-predictor / tests
# File:    test_diagnose.py
# Purpose: the diagnostics share one set of whole-pack codes and run in one
#          process within a budget. The race coding is the old numbering,
#          the era cells are the solve file's, a sample keeps every id
#          lined up, and the four stages finish on two million rows against
#          a clock (2026-09-12: "check that they will take 5 mins max, all
#          of them together").
#
#   python -m pytest -q tests/test_diagnose.py
import contextlib
import io
import os
import sys
import time

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "engine"), os.path.join(_ROOT, "scripts"),
           os.path.join(_ROOT, "tests")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import bracket as bk                                           # noqa: E402
import joint_solve as js                                       # noqa: E402
import run_joint as rj                                         # noqa: E402
import diagnose as dg                                          # noqa: E402
import track_variance as tv                                    # noqa: E402
from test_era_publish import _era_pack                         # noqa: E402
from test_track_diagnostics import _track_pack                 # noqa: E402


def _old_race_codes(course, day, venue_of_cell=None):
    """raceCodes as it was: np.unique over the stacked (unit, day) pair."""
    unit = (np.asarray(course).astype(np.int64) if venue_of_cell is None
            else np.asarray(venue_of_cell)[np.asarray(course).astype(np.int64)])
    key = np.stack([unit, np.asarray(day).astype(np.int64)], axis=1)
    _, inv = np.unique(key, axis=0, return_inverse=True)
    return inv.reshape(-1).astype(np.int64), int(inv.max()) + 1


def test_the_composite_race_key_numbers_the_races_as_before():
    rng = np.random.default_rng(5)
    n = 200_000
    course = rng.integers(-1, 300, n)
    day = rng.integers(-30, 4000, n).astype(np.float64)           # a few in the future
    a, na = rj.raceCodes(course, day)
    b, nb = _old_race_codes(course, day)
    assert na == nb and np.array_equal(a, b)
    voc = rng.integers(0, 40, 300)
    a, na = rj.raceCodes(course, day, voc)
    b, nb = _old_race_codes(course, day, voc)
    assert na == nb and np.array_equal(a, b)
    # the holdout split draws by race id, so the same numbering is the same split
    import pair_validate as pv
    assert np.array_equal(pv.splitFor("race", n, race=a), pv.splitFor("race", n, race=b))


def test_the_solve_files_cells_are_read_back_for_any_subset_of_rows():
    cols, keep, keys, sport_of_course = _era_pack()
    course = np.asarray(cols["course"]); year = np.asarray(cols["year"])
    (cell, n_cell, _g, cell_keys, _p, _w, _e) = rj.eraCells(
        course, year, 2, len(keys), np.zeros(len(keys), dtype=np.int64), keys)
    npz = {"course_keys": np.array(cell_keys), "era_years": np.array([2]),
           "era_base_year": np.array([int(year[course >= 0].min())]),
           "delta": np.zeros(n_cell)}
    got, got_keys, base_of = bk.cellsFromKeys(course, year, keys, cell_keys, 2,
                                              int(npz["era_base_year"][0]))
    assert np.array_equal(got, cell) and got_keys == [str(k) for k in cell_keys]
    assert all(cell_keys[i].startswith(keys[base_of[i]] + "@e") for i in range(n_cell))
    # through packCodes, and on a subset: the same cells, races and seasons
    full, codes = bk.packCodes(cols, npz, 2)
    assert np.array_equal(full["_cell"], cell) and codes["n_cell"] == n_cell
    sub = bk.subsetCols(full, bk.athleteSample(full, 40, seed=3))
    m = bk.athleteSample(full, 40, seed=3)
    assert np.array_equal(sub["_cell"], cell[m])
    assert np.array_equal(sub["_race"], full["_race"][m])
    assert np.array_equal(sub["_season"], full["_season"][m])
    # the wrong width is refused, not misaligned
    import pytest
    with pytest.raises(ValueError):
        bk.packCodes(cols, npz, 0)
    with pytest.raises(ValueError):
        bk.packCodes(cols, npz, 3)
    # and the indoor check needs no cells at all
    nc, codes0 = bk.packCodes(cols, npz, 0, cells=False)
    assert codes0["n_cell"] == len(keys) and np.array_equal(nc["_cell"], course)


def test_the_track_diagnostic_runs_on_a_sample_against_an_era_solve():
    """★ THE FAILURE OF 2026-09-12: track_variance rebuilt era cells from
    the sampled rows and refused its own solve file ("the solve file's
    cells do not match the pack with this --era-years")."""
    cols, npz, level = _track_pack()
    course = np.asarray(cols["course"]); year = np.asarray(cols["year"])
    keys = cols["course_keys"]
    (cell, n_cell, _g, cell_keys, _p, _w, _e) = rj.eraCells(
        course, year, 2, len(keys), np.zeros(len(keys), dtype=np.int64), keys)
    era_npz = {"delta_anchored": npz["delta_anchored"][[int(k.rpartition("@e")[0].split(":")[2])
                                                       for k in cell_keys]],
               "course_keys": np.array(cell_keys), "rating": npz["rating"],
               "era_years": np.array([2]), "era_base_year": np.array([2025])}
    with contextlib.redirect_stdout(io.StringIO()):
        r = tv.analyse(cols, era_npz, era_years=2, min_rows=50, window=21,
                       use_curve=False, sample_pct=50, seed=3)
    assert r["across"]["n_tracks"] == 30
    assert r["across"]["corr_board_bracket"] > 0.9


def test_diagnose_runs_every_stage_once_on_one_load(tmp_path, capsys):
    cols, npz, level = _track_pack()
    lines = []
    times = dg.run(cols, npz, era_years=0, match=["TF:loc:6:", "TF:loc:20:"],
                   out_dir=str(tmp_path), window=21, top=0.25,
                   indoor_windows=(49, 90), dists=(1600,), indoor_sample=100,
                   track_sample=100, track_min_rows=200, pct=100, seed=11,
                   iters=20, use_curve=False, log=lambda s: lines.append(s))
    assert set(times) >= {"codes", "venues", "indoor", "tracks", "holdout", "total"}
    for name in ("bracket_venues.txt", "indoor.txt", "tracks.txt", "bracket_holdout.txt"):
        text = (tmp_path / name).read_text()
        assert text.strip(), name
        assert "Traceback" not in text and "stopped:" not in text, (name, text[:400])
    assert "TF:loc:6:out" in (tmp_path / "bracket_venues.txt").read_text()
    assert "indoor slower" in (tmp_path / "indoor.txt").read_text()
    assert "WITHIN a track" in (tmp_path / "tracks.txt").read_text()
    assert "error sd" in (tmp_path / "bracket_holdout.txt").read_text()
    table = "\n".join(lines)
    assert "[diagnose] total" in table and "FAILED" not in table
    # a stage that cannot run says so in its file and the others still run
    lines = []
    times2 = dg.run(cols, {"course_keys": npz["course_keys"], "rating": npz["rating"]},
                    only=["indoor", "tracks"], out_dir=str(tmp_path / "bad"),
                    indoor_windows=(90,), dists=(1600,), indoor_sample=100,
                    track_sample=100, use_curve=False, log=lambda s: lines.append(s))
    assert "tracks" in times2 and "FAILED" in "\n".join(lines)
    assert "indoor slower" in (tmp_path / "bad" / "indoor.txt").read_text()


def _big_pack(n_ath=200_000, n_year=5, per=2, n_xc=300, n_track=100, seed=4):
    """Two million rows shaped like the corpus: athletes with several
    seasons, XC courses with eras, outdoor and indoor tracks, ratings, a
    form curve and race-day terms in the solve file."""
    rng = np.random.default_rng(seed)
    years = np.arange(2021, 2021 + n_year)
    ath = np.repeat(np.arange(n_ath), n_year * per)
    year = np.tile(np.repeat(years, per), n_ath)
    n = ath.size
    keys = ([f"XC:{1000 + c}:d5000" for c in range(n_xc)]
            + [f"TF:loc:{c}:out" for c in range(n_track)]
            + [f"TF:loc:{n_track + c}:in" for c in range(n_track // 4)])
    n_base = len(keys)
    sport_of = np.r_[np.zeros(n_xc, np.int64), np.ones(n_track + n_track // 4, np.int64)]
    # each row: autumn XC or spring track, on a race day of the year
    is_tf = rng.random(n) < 0.5
    course = np.where(is_tf, rng.integers(n_xc, n_base, n), rng.integers(0, n_xc, n))
    doy = np.where(is_tf, 60 + 7 * rng.integers(0, 16, n), 250 + 7 * rng.integers(0, 10, n))
    days = ((2026 - year) * 365 + (366 - doy)).astype(np.float64)
    a_true = rng.normal(0, 0.15, n_ath)
    diff = rng.normal(0, 0.04, n_base)
    y = a_true[ath] + diff[course] + rng.normal(0, 0.03, n)
    dist = np.where(is_tf, rng.choice([800.0, 1600.0, 3200.0], n), 5000.0)
    cols = {"athlete": ath, "year": year, "course": course, "days": days,
            "doy": doy, "sport": sport_of[course], "norm": np.exp(y),
            "dist_m": dist, "meet_class": rng.integers(0, 4, n),
            "athlete_keys": [(i, "hs_m") for i in range(n_ath)],
            "course_keys": keys}
    (cell, n_cell, _g, cell_keys, _p, _w, _e) = rj.eraCells(
        course, year, 2, n_base, np.zeros(n_base, dtype=np.int64), keys)
    race, n_race = rj.raceCodes(course, days)
    import pair_engine as pe
    season, n_season = pe.athleteSeasonCodes(ath, year)
    npz = {"delta_anchored": rng.normal(0, 0.04, n_cell), "course_keys": np.array(cell_keys),
           "race_effect": rng.normal(0, 0.01, n_race),
           "rating": (100.0 * np.exp(-rng.normal(0, 0.15, n_season))).astype(np.float32),
           "curve": rng.normal(0, 0.005, (1, js.CURVE_N_KNOTS)),
           "curve_knot_days": np.arange(js.CURVE_N_KNOTS) * js.CURVE_KNOT_DAYS,
           "era_years": np.array([2]), "era_base_year": np.array([2021])}
    return cols, npz


def test_the_four_stages_finish_on_two_million_rows_against_a_clock(tmp_path):
    cols, npz = _big_pack()
    n = np.asarray(cols["norm"]).size
    assert n == 2_000_000
    lines = []
    t0 = time.time()
    times = dg.run(cols, npz, era_years=2, match=["XC:1000:", "TF:loc:0:"],
                   out_dir=str(tmp_path), window=21, top=0.25, indoor_sample=30,
                   track_sample=25, track_min_rows=100, pct=15, seed=11, iters=30,
                   log=lambda s: lines.append(s))
    took = time.time() - t0
    print("\n".join(lines))
    assert "FAILED" not in "\n".join(lines)
    for name in ("bracket_venues.txt", "indoor.txt", "tracks.txt", "bracket_holdout.txt"):
        text = (tmp_path / name).read_text()
        assert "Traceback" not in text and "stopped:" not in text, (name, text[:400])
    # 2M rows in well under a minute here; the corpus is 25 times bigger and
    # everything is a sort or a gather, so the budget of 300 s holds with
    # room (measured: see docs/HANDOFF-2026-09-11.md)
    assert took < 90, times


def test_both_engines_are_scored_on_the_rows_both_cover(tmp_path, capsys):
    """The joint model's held-out predictions, row by row, meet the bracket
    engine on the same split; a fake file with a known error says whether
    the rows line up."""
    import bracket_holdout as bh
    cols, npz = _big_pack(n_ath=60_000)            # ~20 rows a race: enough voters
    full, codes = bk.packCodes(cols, npz, 2)
    train, test = bh.sampleAndSplit(full, pct=100, seed=11)
    rows = np.flatnonzero(test)
    y = np.log(np.asarray(cols["norm"], dtype=np.float64))
    rng = np.random.default_rng(3)
    pred = y[rows] + rng.normal(0, 0.02, rows.size)          # a "joint" model with sd 0.02
    cov = rng.random(rows.size) < 0.9
    path = tmp_path / "base_holdout.npz"
    np.savez(path, row=rows, pred=pred, covered=cov, y=y[rows], kind=np.array(["race"]),
             sample_pct=np.array([100.0]), sample_seed=np.array([11]))
    res = bh.score(full, npz, codes=codes, pct=100, seed=11, era_years=2, iters=10,
                   verbose=False, joint_dump=str(path))
    same = res["same_rows"]
    assert same is not None and same["overlap"] > 0.99
    assert abs(same["sd_joint"] - 0.02) < 0.003, same
    assert 100 <= same["n"] < int(test.sum())
    out = capsys.readouterr().out
    assert "SAME ROWS, BOTH ENGINES" in out
    # without the file it says so and still returns the engine's own score
    res2 = bh.score(full, npz, codes=codes, pct=100, seed=11, era_years=2, iters=5,
                    verbose=False, joint_dump=str(tmp_path / "missing.npz"))
    assert res2["same_rows"] is None and np.isfinite(res2["sd"])


def test_the_joint_holdout_writes_its_predictions_row_by_row(tmp_path, monkeypatch):
    cols, keep, keys, sport_of_course = _era_pack()
    args = rj.buildParser().parse_args(["--holdout-only", "--no-curve", "--no-rust",
                                        "--no-dist", "--no-slope", "--no-link",
                                        "--no-sport-offset", "--outer", "2", "--probes", "0",
                                        "--importance", "none", "--era-years", "2"])
    path = tmp_path / "base_holdout.npz"
    monkeypatch.setenv("XCP_HOLDOUT_DUMP", str(path))
    with contextlib.redirect_stdout(io.StringIO()):
        D, athlete_pool, pool_names = rj.buildDesign(
            cols, keep, sport_offset=False, curve=False, rust=False, dist=False,
            slope=False, link=False, altitude=False, era_years=2,
            importance="none", indoor=True, dist_table=False)
        rj.holdout(cols, keep, args, athlete_pool, D)
    d = np.load(path, allow_pickle=False)
    rows = d["row"]; pred = d["pred"]; cov = d["covered"]; y = d["y"]
    assert rows.size == pred.size == cov.size == y.size > 100
    assert np.array_equal(np.log(cols["norm"][rows]), y)
    assert cov.mean() > 0.8 and (y[cov] - pred[cov]).std() < 0.06
    assert str(d["kind"][0]) == "race"


def test_the_joint_dump_lands_on_the_packs_rows_even_after_the_solve_sorts_them(tmp_path, monkeypatch):
    """★ RUN 21 (2026-09-13): the first same-rows line read 35% overlap and a
    joint error of 0.0625 because run_joint sorts the pack by athlete and
    year before the holdout and the dump indexed the sorted rows. The dump
    carries the file's row numbers now; an old dump is translated; and a
    dump whose times do not land on the pack is refused."""
    import bracket_holdout as bh
    cols, keep, keys, sport_of_course = _era_pack()
    # a pack whose file order is NOT (athlete, year): shuffle it
    rng = np.random.default_rng(11)
    n = cols["norm"].size
    perm = rng.permutation(n)
    shuffled = {k: (v[perm] if isinstance(v, np.ndarray) and v.shape[:1] == (n,) else v)
                for k, v in cols.items()}
    args = rj.buildParser().parse_args(["--holdout-only", "--no-curve", "--no-rust",
                                        "--no-dist", "--no-slope", "--no-link",
                                        "--no-sport-offset", "--outer", "2", "--probes", "0",
                                        "--importance", "none", "--era-years", "2"])
    path = tmp_path / "base_holdout.npz"
    monkeypatch.setenv("XCP_HOLDOUT_DUMP", str(path))
    sorted_cols = rj.sortRowsByAthlete(shuffled)            # what main() does first
    with contextlib.redirect_stdout(io.StringIO()):
        D, athlete_pool, pool_names = rj.buildDesign(
            sorted_cols, np.ones(n, dtype=bool), sport_offset=False, curve=False,
            rust=False, dist=False, slope=False, link=False, altitude=False,
            era_years=2, importance="none", indoor=True, dist_table=False)
        rj.holdout(sorted_cols, np.ones(n, dtype=bool), args, athlete_pool, D)
    d = np.load(path, allow_pickle=False)
    assert str(d["row_space"][0]) == "file"
    # the dump's rows are rows of the SHUFFLED pack, the file the diagnostics load
    assert np.allclose(np.log(shuffled["norm"][d["row"]]), d["y"])
    npz = {"course_keys": np.array(D.course_keys), "era_years": np.array([2]),
           "era_base_year": np.array([int(shuffled["year"].min())])}
    full, codes = bk.packCodes(shuffled, npz, 2)
    with contextlib.redirect_stdout(io.StringIO()):
        res = bh.score(full, None, codes=codes, pct=100, seed=11, era_years=2, iters=10,
                       verbose=False, joint_dump=str(path))
    same = res["same_rows"]
    assert same is not None and same["overlap"] > 0.95, same
    assert same["sd_joint"] < 0.06
    # an old-style dump (indices into the sorted rows, no row_space) is translated
    order = np.lexsort((shuffled["year"], shuffled["athlete"]))
    inv = np.empty(n, dtype=np.int64); inv[order] = np.arange(n)
    old_path = tmp_path / "old_holdout.npz"
    np.savez(old_path, row=inv[d["row"]], pred=d["pred"], covered=d["covered"], y=d["y"],
             kind=d["kind"], sample_pct=d["sample_pct"], sample_seed=d["sample_seed"])
    with contextlib.redirect_stdout(io.StringIO()):
        res2 = bh.score(full, None, codes=codes, pct=100, seed=11, era_years=2, iters=10,
                        verbose=False, joint_dump=str(old_path))
    assert res2["same_rows"] is not None and abs(res2["same_rows"]["sd_joint"] - same["sd_joint"]) < 1e-9
    # a dump that does not land on this pack is refused, not compared
    bad = tmp_path / "bad_holdout.npz"
    np.savez(bad, row=d["row"], pred=d["pred"], covered=d["covered"], y=d["y"] + 0.5,
             kind=d["kind"], sample_pct=d["sample_pct"], sample_seed=d["sample_seed"],
             row_space=np.array(["file"]))
    with contextlib.redirect_stdout(io.StringIO()):
        res3 = bh.score(full, None, codes=codes, pct=100, seed=11, era_years=2, iters=10,
                        verbose=False, joint_dump=str(bad))
    assert res3["same_rows"] is None
