"""model/race_blend.py: rank labels, the blend's weights, and a run with
the members this machine has. No database.

    python -m pytest -q tests/test_race_blend.py
"""
import os
import subprocess
import sys

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "model"),
           os.path.join(_ROOT, "tests")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest                                                # noqa: E402

np = pytest.importorskip("numpy")
pytest.importorskip("sklearn")
import race_blend as B                                       # noqa: E402


def test_rank_labels_put_the_winner_at_30():
    y = np.array([0.02, -0.03, 0.0, 0.5, 0.1])
    race = np.array([0, 0, 0, 1, 1])
    lab = B.rankLabels(y, race)
    assert list(lab[:3]) == [0, 30, 15] and list(lab[3:]) == [0, 30]


def test_the_blend_weights_recover_a_known_mix():
    rng = np.random.default_rng(0)
    a, b = rng.normal(size=5000), rng.normal(size=5000)
    y = 0.7 * a + 0.3 * b
    w = B.blendWeights(np.stack([a, b], 1), y, np.ones(5000, dtype=bool))
    assert w == pytest.approx([0.7, 0.3], abs=1e-6)


def test_a_blend_run_beats_or_ties_its_best_member(tmp_path):
    from test_race_trees import _table
    path = tmp_path / "rows.npz"
    _table(str(path))
    got = subprocess.run([sys.executable, os.path.join(_ROOT, "model", "race_blend.py"),
                          "--rows", str(path), "--members", "hgb"],
                         capture_output=True, text=True, timeout=600)
    assert got.returncode == 0, got.stderr[-2000:]
    rows = [ln.split() for ln in got.stdout.splitlines()]
    rows = [r for r in rows if len(r) > 1 and r[0] in ("hgb", "BLEND") and r[1].endswith("%")]
    pairs = {r[0]: float(r[1].rstrip("%")) for r in rows[:2]}
    assert pairs["BLEND"] >= pairs["hgb"] - 0.05
