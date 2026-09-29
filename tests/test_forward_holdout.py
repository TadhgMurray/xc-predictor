# Project: xc-predictor / tests
# File:    test_forward_holdout.py
# Purpose: the validation scorecard (engine/forward_holdout.py; run_joint
#          --holdout-kind forward) on a synthetic pack with KNOWN seasons.
#
# ★ WHAT HAS TO HOLD (owner, 2026-09-29: one validation scorecard, split
#   forward in time, a sealed test season looked at once):
#     - no row dated on or after the cutoff ever reaches the fit, and
#       nothing on or after the window's end reaches the fit OR the score;
#     - the sealed season is refused without --sealed, and loud with it;
#     - head-to-head is 1.0 when the prediction is right and ~0.5 when it
#       is shuffled, one number per race;
#     - the standard error is by RACE: rows that share a race's day are
#       not independent evidence.
#
#   XCP_DB_PASSWORD=x python -m pytest -q tests/test_forward_holdout.py
import _env  # noqa: F401  -- sets XCP_DB_PASSWORD, must precede config

import contextlib
import datetime as _dt
import io
import os
import sys

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "engine"), os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import forward_holdout as fh                                    # noqa: E402
import run_joint as rj                                          # noqa: E402

PACK_DATE = _dt.date(2026, 9, 15)       # season 2026 has just opened
SEASONS = (2021, 2022, 2023, 2024, 2025, 2026)
TRUE_M = -0.03                          # every athlete 3% faster a year


def _pack(seed=0, n_person=260, noise=0.02, day_sd=0.015, n_xc=12, n_tf=6,
          m=TRUE_M, years_per_person=4):
    """A pack the design builder takes: one pool, XC Sep-Nov of a season's
    first calendar year, outdoor track Mar-May of its second, season 2026
    only its August/September opening. Persons race `years_per_person`
    consecutive seasons, six XC and four TF races a season."""
    rng = np.random.default_rng(seed)
    keys_c = ([f"XC:{100 + i}:d5000" for i in range(n_xc)]
              + [f"TF:loc:{i}:out" for i in range(n_tf)])
    sport_of = np.r_[np.zeros(n_xc, int), np.ones(n_tf, int)]
    d_true = np.r_[rng.normal(0, 0.04, n_xc), rng.normal(-0.03, 0.015, n_tf)]
    # race days: per season, 8 XC days and 6 TF days, each a (cell, date)
    races = []                                    # (cell, date)
    for s in SEASONS:
        if s == SEASONS[-1]:
            for k in range(4):
                races.append((k % n_xc, _dt.date(s, 8, 20) + _dt.timedelta(days=5 * k)))
            continue
        for k in range(8):
            races.append((int(rng.integers(0, n_xc)),
                          _dt.date(s, 9, 5) + _dt.timedelta(days=10 * k)))
        for k in range(6):
            races.append((n_xc + int(rng.integers(0, n_tf)),
                          _dt.date(s + 1, 3, 10) + _dt.timedelta(days=12 * k)))
    races = np.array(races, dtype=object)
    r_cell = np.array([c for c, _ in races], dtype=np.int64)
    r_date = np.array([d for _, d in races])
    r_season = np.array([d.year if d.month >= 8 else d.year - 1 for d in r_date])
    r_u = rng.normal(0, day_sd, len(races))
    a0 = rng.normal(0, 0.08, n_person)
    first = rng.integers(0, len(SEASONS) - 1, n_person)
    ath, rac = [], []
    for p in range(n_person):
        for j in range(years_per_person):
            s_i = first[p] + j
            if s_i >= len(SEASONS):
                break
            here = np.flatnonzero(r_season == SEASONS[s_i])
            xc = here[sport_of[r_cell[here]] == 0]
            tf = here[sport_of[r_cell[here]] == 1]
            pick = list(rng.choice(xc, min(6, xc.size), replace=False))
            if tf.size:
                pick += list(rng.choice(tf, min(4, tf.size), replace=False))
            ath.extend([p] * len(pick)); rac.extend(pick)
    ath = np.array(ath); rac = np.array(rac)
    season = r_season[rac]
    date = r_date[rac]
    a_true = a0[ath] + m * (season - SEASONS[0])
    y = (np.log(1000.0) + a_true + d_true[r_cell[rac]] + r_u[rac]
         + rng.normal(0, noise, ath.size))
    n = y.size
    cols = {"athlete": ath.astype(np.int64),
            "course": r_cell[rac].astype(np.int64),
            "norm": np.exp(y),
            "days": np.array([(PACK_DATE - d).days for d in date], dtype=np.float64),
            "doy": np.array([d.timetuple().tm_yday for d in date], dtype=np.int64),
            "year": season.astype(np.int64),
            "sport": sport_of[r_cell[rac]].astype(np.int64),
            "result_id": np.arange(n) + 700_000,
            "athlete_keys": [(str(5000 + p), "hs_m") for p in range(n_person)],
            "course_keys": keys_c}
    return rj.sortRowsByAthlete(cols)


# the design flags a synthetic pack can carry (no distance, no database)
_FLAGS = ["--no-dist", "--no-slope", "--no-importance", "--no-indoor",
          "--no-dist-table", "--outer", "3", "--sigma-u-floor", "0,0",
          "--tau-max", "none"]


def _args(*extra):
    ap = rj.buildParser()
    with contextlib.redirect_stdout(io.StringIO()):
        return rj.applyImplications(ap.parse_args(list(_FLAGS) + list(extra)), ap)


def _holdout(cols, *extra, dump=None, monkeypatch=None):
    """rj.holdout on the pack, the output captured; the design builder's
    `keep` masks recorded (the first is the fit's)."""
    args = _args("--holdout-only", "--holdout-kind", "forward", *extra)
    keep = (cols["course"] >= 0) & (cols["norm"] > 0)
    seen = []
    real = rj.buildDesign

    def spy(c, k, *a, **kw):
        seen.append(np.asarray(k).copy())
        return real(c, k, *a, **kw)
    monkeypatch.setattr(rj, "buildDesign", spy)
    if dump:
        monkeypatch.setenv("XCP_HOLDOUT_DUMP", dump)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        _D, athlete_pool, _names = real(cols, keep, **rj.designKwargs(args))
        rj.holdout(cols, keep, args, athlete_pool, None)
    return buf.getvalue(), seen


# ------------------------------------------------------------------ #
# dates and windows
# ------------------------------------------------------------------ #

def test_row_dates_are_the_pack_dates():
    cols = _pack(n_person=40)
    dates, pack, agree = fh.rowDates(cols)
    assert pack == np.datetime64(PACK_DATE.isoformat())
    assert agree == 1.0
    expect = np.datetime64(PACK_DATE.isoformat()) - cols["days"].astype(np.int64)
    assert np.array_equal(dates, expect)


def test_the_windows_come_from_the_pack():
    """Latest race 2026-09: season 2026 is running, 2025 is the last
    complete one (SEALED), 2024 the validation season."""
    cols = _pack(n_person=40)
    dates, _p, _a = fh.rowDates(cols)
    w = fh.defaultWindows(dates)
    assert w["current"] == 2026
    assert w["sealed"][2] == 2025 and w["validation"][2] == 2024
    assert w["validation"][0] == np.datetime64("2024-08-01")
    assert w["validation"][1] == np.datetime64("2025-08-01")
    assert w["sealed"][1] == np.datetime64("2026-08-01")
    assert fh.resolveWindow(w) == (np.datetime64("2024-08-01"),
                                   np.datetime64("2025-08-01"))


def test_the_sealed_window_is_refused_without_sealed():
    cols = _pack(n_person=40)
    dates, _p, _a = fh.rowDates(cols)
    w = fh.defaultWindows(dates)
    for frm, until in (("2025-08-01", None), ("2024-08-01", "2026-01-01"),
                       ("2025-10-01", "2026-03-01")):
        with pytest.raises(SystemExit, match="SEALED"):
            fh.resolveWindow(w, frm, until)
    # a custom window wholly before the sealed season is fine, and a bare
    # --holdout-from runs up to the sealed season, never into it
    assert fh.resolveWindow(w, "2023-08-01")[1] == np.datetime64("2025-08-01")
    assert fh.resolveWindow(w, sealed=True) == (np.datetime64("2025-08-01"),
                                                np.datetime64("2026-08-01"))
    lines = fh.windowLines(w, *fh.resolveWindow(w, sealed=True), sealed=True)
    assert any("SEALED TEST SEASON 2025 IS BEING SCORED" in ln for ln in lines)


def test_forward_split_masks():
    d = np.array(["2024-07-31", "2024-08-01", "2025-07-31", "2025-08-01",
                  "2026-01-01"], dtype="datetime64[D]")
    keep = np.array([True, True, True, True, False])
    tr, te, ex = fh.forwardSplit(d, keep, np.datetime64("2024-08-01"),
                                 np.datetime64("2025-08-01"))
    assert tr.tolist() == [True, False, False, False, False]
    assert te.tolist() == [False, True, True, False, False]
    assert ex.tolist() == [False, False, False, True, False]


# ------------------------------------------------------------------ #
# the run, end to end
# ------------------------------------------------------------------ #

def test_no_post_cutoff_row_reaches_the_fit(monkeypatch, tmp_path):
    cols = _pack()
    dump = str(tmp_path / "fwd.npz")
    log, seen = _holdout(cols, dump=dump, monkeypatch=monkeypatch)
    dates, _p, _a = fh.rowDates(cols)
    cut, until = np.datetime64("2024-08-01"), np.datetime64("2025-08-01")
    keep_tr, keep_te = seen[0], seen[1]
    assert keep_tr.any() and keep_te.any()
    assert dates[keep_tr].max() < cut, "a row on or after the cutoff was fitted"
    assert not (keep_tr & keep_te).any()
    assert dates[keep_te].min() >= cut and dates[keep_te].max() < until
    # the sealed season and the running one are in neither
    assert not ((keep_tr | keep_te) & (dates >= until)).any()
    # and every row of the window was held out (all rated rows scored)
    rated = (cols["course"] >= 0) & (cols["norm"] > 0)
    assert np.array_equal(keep_te, rated & (dates >= cut) & (dates < until))
    with np.load(dump) as z:
        rows = z["row"]
        assert str(z["kind"][0]) == "forward"
        assert not bool(z["sealed"][0])
        assert list(z["window"]) == ["2024-08-01", "2025-08-01"]
        assert {"race", "group", "sport", "train_races", "status",
                "cell_seen"} <= set(z.files)
        cov, status = z["covered"], z["status"]
    # the dump's rows are the file's rows, all inside the window
    inv = np.empty_like(cols["_row_order"]); inv[cols["_row_order"]] = np.arange(inv.size)
    d_file = dates[inv]
    assert (d_file[rows] >= cut).all() and (d_file[rows] < until).all()
    # a cold start is never in the number
    assert not (cov & (status != fh.STATUS_KNOWN)).any()
    assert (status == fh.STATUS_NEW_PERSON).any(), "the world has newcomers"
    assert "[joint] scorecard:" in log and "pairwise" in log
    assert "VALIDATION = 2024" in log and "SEALED test = 2025" in log


def test_the_sealed_run_is_refused_then_loud(monkeypatch):
    cols = _pack(n_person=120)
    with pytest.raises(SystemExit, match="SEALED"):
        _holdout(cols, "--holdout-from", "2025-08-01", monkeypatch=monkeypatch)
    log, seen = _holdout(cols, "--sealed", monkeypatch=monkeypatch)
    assert "SEALED TEST SEASON 2025 IS BEING SCORED" in log
    dates, _p, _a = fh.rowDates(cols)
    assert dates[seen[0]].max() < np.datetime64("2025-08-01")
    assert dates[seen[1]].min() >= np.datetime64("2025-08-01")
    assert dates[seen[1]].max() < np.datetime64("2026-08-01")


def test_noiseless_world_orders_every_race(monkeypatch, tmp_path):
    """No noise and no race-day spread: the carried abilities put every
    held-out race in its finishing order, and shuffled they do not."""
    cols = _pack(noise=0.0, day_sd=0.0)
    dump = str(tmp_path / "clean.npz")
    _holdout(cols, "--no-robust", "--no-tilt", "--no-curve", "--no-rust",
             dump=dump, monkeypatch=monkeypatch)
    with np.load(dump) as z:
        d = {k: z[k] for k in z.files}
    cov = d["covered"]
    conc, pairs, _ = fh.pairwiseCounts(d["group"][cov], d["y"][cov], d["pred"][cov])
    acc, _pooled, n_r, _n_p = fh.pairwiseSummary(conc, pairs)
    assert n_r >= 10, n_r              # the season's 8 XC and 6 TF races
    assert acc > 0.995, acc
    rng = np.random.default_rng(3)
    shuffled = rng.permutation(d["pred"][cov])
    conc, pairs, _ = fh.pairwiseCounts(d["group"][cov], d["y"][cov], shuffled)
    acc_s = fh.pairwiseSummary(conc, pairs)[0]
    assert abs(acc_s - 0.5) < 0.05, acc_s


def test_the_season_tie_carries_the_improvement(monkeypatch, tmp_path):
    """Everyone is 3% faster a year. Carried unmoved, the held-out season
    reads about 3% faster than predicted (bias about -0.03); moved by the
    fitted walk, the bias is near zero."""
    cols = _pack(n_person=500, years_per_person=5)
    out = {}
    for name, extra in (("untied", ()), ("tied", ("--season-tie",))):
        dump = str(tmp_path / f"{name}.npz")
        log, _ = _holdout(cols, "--no-tilt", *extra, dump=dump,
                          monkeypatch=monkeypatch)
        with np.load(dump) as z:
            cov = z["covered"]
            out[name] = float(np.mean(z["y"][cov] - z["pred"][cov]))
        if name == "tied":
            assert "moved by the season tie" in log
    assert out["untied"] < -0.02, out
    assert abs(out["tied"]) < abs(out["untied"]) / 2, out


# ------------------------------------------------------------------ #
# the numbers
# ------------------------------------------------------------------ #

def test_pairwise_is_one_when_right_and_half_when_shuffled():
    rng = np.random.default_rng(0)
    group = np.repeat(np.arange(300), 12)
    y = rng.normal(0, 0.05, group.size)
    pred = y + np.repeat(rng.normal(0, 1, 300), 12)       # a per-race constant
    conc, pairs, _ = fh.pairwiseCounts(group, y, pred)
    assert fh.pairwiseSummary(conc, pairs)[0] == 1.0
    conc, pairs, _ = fh.pairwiseCounts(group, y, rng.permutation(pred))
    assert abs(fh.pairwiseSummary(conc, pairs)[0] - 0.5) < 0.03


def test_pairwise_is_per_race_not_per_pair():
    """One 300-runner race all wrong, ten 4-runner races all right: per race
    the mean is 10/11; pooled over pairs the big race swamps it."""
    y = np.r_[np.arange(300.0), np.tile(np.arange(4.0), 10)]
    group = np.r_[np.zeros(300, int), np.repeat(np.arange(1, 11), 4)]
    pred = np.r_[-np.arange(300.0), np.tile(np.arange(4.0), 10)]
    conc, pairs, _ = fh.pairwiseCounts(group, y, pred)
    per_race, pooled, n_r, n_p = fh.pairwiseSummary(conc, pairs)
    assert n_r == 11 and n_p == 300 * 299 // 2 + 60
    assert abs(per_race - 10 / 11) < 1e-12
    assert pooled < 0.01


def test_pair_labels_file_a_pair_under_its_lower_label():
    y = np.array([1.0, 2.0, 3.0])
    group = np.zeros(3, int)
    lab = np.array([0, 1, 1])
    conc, pairs, per = fh.pairwiseCounts(group, y, y, [lab], [2])
    mc, mp = per[0]
    assert mp[0].tolist() == [2, 1] and mc[0].tolist() == [2, 1]


def test_the_bootstrap_se_is_by_race():
    """Rows of one race share an error shock. Resampled by race, the SE of
    the mean error is the races' scatter; resampled as if rows were
    independent, it is about sqrt(field size) too small."""
    rng = np.random.default_rng(1)
    n_race, field = 400, 25
    race = np.repeat(np.arange(n_race), field)
    e = np.repeat(rng.normal(0, 0.03, n_race), field) + rng.normal(0, 0.003, race.size)
    by_race = [fh.errorStats(e, w_r[race])["bias"]
               for w_r in fh.raceBootstrap(race, 300, seed=2)]
    by_row = [fh.errorStats(e, w_r)["bias"]
              for w_r in fh.raceBootstrap(np.arange(race.size), 300, seed=2)]
    se_race, se_row = np.std(by_race, ddof=1), np.std(by_row, ddof=1)
    truth = 0.03 / np.sqrt(n_race)
    assert 0.8 * truth < se_race < 1.25 * truth, (se_race, truth)
    assert se_race > 3 * se_row, (se_race, se_row)
    # whole races: every row of a race carries its race's multiplicity
    w_r = next(fh.raceBootstrap(race, 1, seed=5))
    assert w_r.size == n_race and w_r.sum() == n_race


def test_the_paired_scorecard_sees_a_real_improvement():
    rng = np.random.default_rng(4)
    n_race, field = 300, 20
    race = np.repeat(np.arange(n_race), field)
    y = rng.normal(0, 0.05, race.size)
    shock = np.repeat(rng.normal(0, 0.02, n_race), field)
    base = {"y": y, "pred": y - shock - rng.normal(0, 0.03, y.size),
            "covered": np.ones(y.size, bool), "race": race, "group": race,
            "sport": np.zeros(y.size, np.int8), "pool": np.full(y.size, "hs_m"),
            "train_races": rng.integers(1, 20, y.size)}
    test = dict(base, pred=y - shock - rng.normal(0, 0.01, y.size))
    lines = fh.pairedLines(base, test, n_rep=60)
    text = "\n".join(lines)
    assert "SCORECARD" in text and "race" in text
    med = [ln for ln in lines if ln.strip().startswith("all")][1]   # median |err|
    assert med.rstrip().endswith("*"), med


def test_switch_scorecard_reads_the_forward_dump(tmp_path):
    import switch_scorecard as ss
    rng = np.random.default_rng(6)
    n = 4000
    race = np.repeat(np.arange(200), 20)
    y = rng.normal(0, 0.05, n)
    common = dict(row=np.arange(n), y=y, covered=np.ones(n, bool),
                  kind=np.array(["forward"]), sample_pct=np.array([15.0]),
                  sample_seed=np.array([11]), season_train_rows=np.full(n, 5),
                  pool=np.full(n, "hs_m"), race=race, group=race,
                  sport=np.zeros(n, np.int8), train_races=np.full(n, 7),
                  cell_seen=np.ones(n, bool), status=np.zeros(n, np.int8))
    base = dict(common, pred=y + rng.normal(0, 0.03, n))
    test = dict(common, pred=y + rng.normal(0, 0.02, n))
    lines = ss.compareHoldout(base, test)
    assert any("SCORECARD" in ln for ln in lines)
