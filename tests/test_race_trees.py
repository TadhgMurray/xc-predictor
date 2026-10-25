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
