"""model/race_trees.py: the scoring pieces, and an end-to-end fit on a
synthetic table where season openers move with a known input -- the trees
must find it and beat the ratings on order. No database.

    python -m pytest -q tests/test_race_trees.py
"""
import datetime
import os
import subprocess
import sys

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "model")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest                                                # noqa: E402

np = pytest.importorskip("numpy")
pytest.importorskip("sklearn")
import race_rows as RR                                       # noqa: E402
import race_trees as T                                       # noqa: E402


def test_pairs_and_slices():
    assert T.pairShare(np.array([1., 2, 3]), np.array([10., 20, 30])) == 1.0
    assert T.pairShare(np.array([1., 1]), np.array([10., 20])) == 0.5
    assert T.pairShare(np.array([1., 2]), np.array([10., 10])) is None
    assert T.raceSlices(np.array([0, 0, 1, 2, 2])) == [(0, 2), (2, 3), (3, 5)]
    assert T.bandOf(0, 7) == T.BANDS[0] and T.bandOf(1, 200) == T.BANDS[3]


def _table(path):
    rng = np.random.default_rng(0)
    F = list(RR.FEATURES)
    fi = {f: i for i, f in enumerate(F)}
    out = {k: [] for k in ("X", "y", "base", "y_abs", "base_abs", "race", "day", "tfrrs")}
    d0 = datetime.date(2019, 9, 1).toordinal()
    d1 = datetime.date(2024, 9, 1).toordinal()
    for k in range(900):
        day = d0 + int(rng.integers(0, 1500)) if k < 700 else d1 + int(rng.integers(0, 80))
        n = int(rng.integers(15, 40))
        X = np.full((n, len(F)), np.nan, np.float32)
        form = rng.normal(0, 0.05, n)
        opener = (rng.random(n) < 0.2).astype(float)
        trend = rng.normal(0, 0.03, n)
        X[:, fi["form_ln"]] = 4.6 - form
        X[:, fi["opener"]] = opener
        X[:, fi["prev_late_minus_early"]] = trend
        X[:, fi["last_days"]] = np.where(opener > 0, 200, 7)
        X[:, fi["distance"]] = 5000.0
        X[:, fi["level"]] = 2
        t = form - opener * trend + rng.normal(0, 0.015, n)
        out["X"].append(X)
        out["y"].append(t - t.mean())
        out["base"].append(form - form.mean())
        out["y_abs"].append(7.0 + t)
        out["base_abs"].append(7.0 + form)
        out["race"] += [k] * n
        out["day"] += [day] * n
        out["tfrrs"] += [0] * n
    np.savez_compressed(
        path, X=np.vstack(out["X"]),
        **{k: np.concatenate(out[k]).astype(np.float32) for k in ("y", "base", "y_abs", "base_abs")},
        race=np.array(out["race"], np.int32), day=np.array(out["day"], np.int32),
        tfrrs=np.array(out["tfrrs"], np.int8), pid=np.arange(len(out["race"])),
        features=np.array(F), race_keys=np.array([str(i) for i in range(900)]),
        args=np.array(["synthetic"]))


def test_the_trees_find_the_openers_and_beat_the_ratings(tmp_path):
    path = tmp_path / "rows.npz"
    _table(str(path))
    got = subprocess.run([sys.executable, os.path.join(_ROOT, "model", "race_trees.py"),
                          "--rows", str(path)], capture_output=True, text=True, timeout=300)
    assert got.returncode == 0, got.stderr[-2000:]
    line = next(ln for ln in got.stdout.splitlines() if "model - ratings, pairs" in ln)
    gain = float(line.split(":")[1].split("points")[0])
    assert gain > 0.5, got.stdout


def test_field_inputs_rebuilt_from_another_clock():
    fi = {"rank_pct": 0, "gap_to_median": 1, "gap_ahead": 2, "gap_behind": 3}
    X = np.zeros((3, 4))
    T.fieldFromBase(X, np.array([0.02, -0.01, 0.0]), [(0, 3)], fi)
    assert list(X[:, 0]) == [1.0, 0.0, 0.5]
    assert X[1, 1] == pytest.approx(-0.01) and np.isnan(X[1, 2]) and np.isnan(X[0, 3])
    assert X[0, 2] == pytest.approx(0.02)


def test_no_ratings_needs_raw_times_and_is_never_saved(tmp_path):
    path = tmp_path / "rows.npz"
    _table(str(path))
    for extra in (["--no-ratings"], ["--no-ratings", "--out", str(tmp_path / "x.pkl")]):
        got = subprocess.run([sys.executable, os.path.join(_ROOT, "model", "race_trees.py"),
                              "--rows", str(path)] + extra, capture_output=True, text=True)
        assert got.returncode != 0


def test_own_record_reads_only_earlier_days_and_carries_to_the_page():
    pid = np.array([7, 7, 7, 8, 7])
    day = np.array([100, 110, 110, 105, 130])          # two races of 7's on day 110
    resid = np.array([0.02, 0.04, -0.01, 0.5, 0.0])
    champ = np.array([False, True, False, False, False])
    f, states = T.ownHistory(pid, day, resid, champ)
    fi = {k: i for i, k in enumerate(T.OWN_FEATURES)}
    assert f[0, fi["own_n"]] == 0                       # the first race knows nothing
    assert f[1, fi["own_n"]] == 1 and f[2, fi["own_n"]] == 1     # not each other's
    assert f[1, fi["own_last"]] == pytest.approx(0.02)
    assert f[4, fi["own_n"]] == 3 and f[4, fi["own_champ_n"]] == 1
    assert f[4, fi["own_mean"]] == pytest.approx(0.05 / (3 + T.OWN_SHRINK))
    assert f[3, fi["own_n"]] == 0                       # 8's own 0.5 never leaks to 7
    pack = T.ownPack(states, 0)
    idx = {int(p): i for i, p in enumerate(pack["pid"])}
    again = T.ownFeatures(T.ownState(pack, idx, 7), 130)
    assert again[fi["own_n"]] == 4 and again[fi["own_last"]] == pytest.approx(0.0)
    assert T.ownState(pack, idx, 99) is None
