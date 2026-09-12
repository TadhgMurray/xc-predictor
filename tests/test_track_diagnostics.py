# Project: xc-predictor / tests
# File:    test_track_diagnostics.py
# Purpose: the two pack-based diagnostics run on a synthetic pack and read
#          the planted facts back: an indoor oval planted 1.5% slower, and
#          outdoor tracks whose only difference is what they host.
#
#   python -m pytest -q tests/test_track_diagnostics.py
import io
import contextlib
import os
import sys

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "engine"), os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import joint_solve as js                                       # noqa: E402
import indoor_outdoor_check as ioc                             # noqa: E402
import track_variance as tv                                    # noqa: E402


def _track_pack(seed=3, n_ath=4000, indoor_level=0.015, champ_slow=0.012,
                front_fast=-0.004):
    """One track season per athlete: three indoor races in Jan-Feb on one
    of 6 ovals, five outdoor races in Apr-May drawn from 360 races on 30
    tracks, all 1600 m. Ovals are `indoor_level` slower. Every outdoor
    RACE has a kind: ordinary, championship-class (its rows run
    `champ_slow` slower, tactical) or stacked (rows run `front_fast` per
    10 points of front faster). Tracks lean: 6-15 mostly ordinary, 16-25
    mostly championships, 26-35 mostly stacked, but every track hosts
    some of each, so a within-track comparison has something to compare.
    Every track's true surface is identical; the planted board is each
    track's mix, which is what a solver would find."""
    rng = np.random.default_rng(seed)
    a_true = rng.normal(0, 0.15, n_ath)
    rating = 100.0 * np.exp(a_true.mean()) / np.exp(a_true)
    # outdoor races: 12 per track, each with a kind
    race_track, race_doy, race_kind = [], [], []
    for t in range(6, 36):
        lean = (t - 6) // 10
        p = np.full(3, 0.1); p[lean] = 0.8
        for k in range(12):
            race_track.append(t); race_doy.append(100 + rng.integers(0, 45))
            race_kind.append(rng.choice(3, p=p))
    race_track = np.array(race_track); race_doy = np.array(race_doy)
    race_kind = np.array(race_kind)
    n_out_race = race_track.size
    rows = []                                                # (ath, course, doy, is_in, race)
    for i in range(n_ath):
        for k in range(3):                                   # indoor, Jan-Feb
            rows.append((i, rng.integers(0, 6), 20 + 12 * k + rng.integers(0, 5), 1, -1))
        for j in rng.choice(n_out_race, 5, replace=False):
            rows.append((i, race_track[j], race_doy[j], 0, j))
    ath = np.array([r[0] for r in rows]); course = np.array([r[1] for r in rows])
    doy = np.array([r[2] for r in rows]); is_in = np.array([r[3] for r in rows], dtype=bool)
    rj_ = np.array([r[4] for r in rows])
    n = ath.size
    days = (366 - doy).astype(np.float64)
    # the front of each outdoor race from its runners (indoor races: their own)
    race_key = np.where(rj_ >= 0, rj_, n_out_race + course * 1000 + doy)
    _u, race = np.unique(race_key, return_inverse=True)
    front = js.raceFront(rating[ath], race, race.max() + 1)[race]
    kind_row = np.where(rj_ >= 0, race_kind[np.maximum(rj_, 0)], -1)
    effect = (np.where(kind_row == 1, champ_slow, 0.0)
              + np.where(kind_row == 2, front_fast * (front - 100.0) / 10.0, 0.0))
    y = a_true[ath] + indoor_level * is_in + effect + rng.normal(0, 0.02, n)
    keys = [f"TF:loc:{i}:in" for i in range(6)] + [f"TF:loc:{i}:out" for i in range(6, 36)]
    cols = {"athlete": ath, "year": np.full(n, 2025), "course": course, "days": days,
            "doy": doy, "sport": np.ones(n, dtype=np.int64), "norm": np.exp(y),
            "dist_m": np.full(n, 1600.0),
            "meet_class": np.where(kind_row == 1, 3, 0),
            "athlete_keys": [(i, "hs_m") for i in range(n_ath)],
            "course_keys": keys}
    # the board a solver would find: each track's mean planted effect
    board = np.zeros(36)
    board[:6] = indoor_level
    s_ = np.bincount(course, weights=effect, minlength=36)
    c_ = np.bincount(course, minlength=36)
    board[6:] = s_[6:] / np.maximum(c_[6:], 1)
    npz = {"delta_anchored": board, "course_keys": np.array(keys), "rating": rating}
    return cols, npz, indoor_level


def test_the_indoor_check_reads_the_planted_level(capsys):
    cols, npz, level = _track_pack()
    res, pools = ioc.measure(cols, npz, windows=(49, 90), dists=(1600,), use_curve=False)
    assert pools == ["hs_m"]
    n, med, trim = res[("raw", "hs_m", 90, "1600")]["pairs"]
    assert n > 3000 and abs(med - level) < 0.004, (n, med)
    n2, med2, trim2 = res[("raw", "hs_m", 90, "1600")]["transition"]
    assert n2 > 3000 and abs(med2 - level) < 0.005, (n2, med2)
    # a 49-day window pairs nothing here (Feb to Apr is longer), and says so
    assert res[("raw", "hs_m", 49, "1600")]["pairs"][0] == 0
    ioc.report(res, pools, (49, 90), (1600,))
    assert "indoor slower" in capsys.readouterr().out


def test_the_track_diagnostic_blames_the_meets_not_the_tracks(capsys):
    cols, npz, _ = _track_pack()
    r = tv.analyse(cols, npz, era_years=0, min_rows=200, window=21, use_curve=False)
    a = r["across"]
    assert a["n_tracks"] == 30
    # within a track, championship-class rows run slower than ordinary ones
    # (the planted 1.2%, attenuated because the bracket's reference races
    # carry some of the same effect) and strong fronts run faster
    assert r["by_class"][3][1] - r["by_class"][0][1] > 0.006, r["by_class"]
    assert r["by_terc"][2][2] < r["by_terc"][0][2], r["by_terc"]
    # the mix explains the board: the planted board IS the mix (the
    # regressor is each track's mean front over all its races, the planted
    # effect only the stacked ones', so not all of it)
    assert a["r2_mix"] > 0.7, a
    assert a["corr_board_bracket"] > 0.9, a
    tv.report(r, show=3)
    out = capsys.readouterr().out
    assert "WITHIN a track" in out and "easiest" in out
    # a sample of athletes still indexes the ratings, so the fronts exist
    r2 = tv.analyse(cols, npz, era_years=0, min_rows=50, window=21, use_curve=False,
                    sample_pct=50, seed=3)
    assert r2["by_terc"] and r2["across"]["n_tracks"] == 30
