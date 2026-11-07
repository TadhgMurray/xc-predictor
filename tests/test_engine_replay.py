"""engine/run_joint --asof --snapshot and deploy/engine_replay.sh: the
engine as of a date, written to a file and never to the database
(docs/PLAN-MODEL-2026-10.md §9). No database.

    python -m pytest -q tests/test_engine_replay.py
"""
import os
import subprocess
import sys

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in ("engine", "scripts", "racecast", "model"):
    p = os.path.join(_ROOT, _p)
    if p not in sys.path:
        sys.path.insert(0, p)

import numpy as np                                           # noqa: E402
import pytest                                                # noqa: E402

RJ = pytest.importorskip("run_joint")


def test_a_snapshot_keeps_rated_rows_sorted_and_canonical_cells():
    live = {"per_sport": {"XC": (np.array([5, 3, 9]), np.array([150.0, np.nan, 140.0]),
                                 np.array(["hs_m", "hs_m", "hs_f"]))},
            "diffs": {"XC:77:d5000": {"difficulty": 0.04},
                      "XC:name:Foo:d5000": {"difficulty": 0.1},
                      "TF:loc:3:x": {"difficulty": 0.0}}}
    out = RJ.snapshotArrays(live, "2025-10-01", {"XC": np.array([9, 5, 3])})
    assert out["XC_result_id"].tolist() == [5, 9]              # sorted, the unrated row out
    assert out["XC_pool"].tolist() == ["hs_m", "hs_f"]
    assert out["cell_canonical_id"].tolist() == [77]           # only canonical XC cells
    assert out["cell_distance_m"].tolist() == [5000]
    assert RJ.snapshotArrays(live, "2025-10-01", {"XC": np.array([9])})["XC_result_id"].tolist() == [9]


def test_a_snapshot_keeps_the_rows_of_its_window_only():
    cols = {"sport": np.array([0, 0, 1, 0]), "result_id": np.array([5, 9, 5, 3])}
    keep = np.array([True, True, True, False])
    dates = np.array(["2025-09-01", "2022-01-01", "2025-09-01", "2025-09-01"],
                     dtype="datetime64[D]")
    got = RJ.recentIds(cols, keep, dates, "2025-10-01")
    assert got["XC"].tolist() == [5] and got["TF"].tolist() == [5]


def test_the_replay_lists_its_cutoffs_with_the_go_lives_flags(tmp_path):
    env = dict(os.environ, XCP_ENV_FILE="/nonexistent", XCP_PRED_DIR=str(tmp_path),
               XCP_REPLAY_SEASONS="2024", XCP_DIFFICULTY="bracket")
    (tmp_path / "asof").mkdir()
    (tmp_path / "asof" / "asof_2024-09-01.npz").write_bytes(b"")
    out = subprocess.run(["bash", os.path.join(_ROOT, "deploy", "engine_replay.sh"), "--list"],
                         env=env, capture_output=True, text=True, check=True).stdout
    assert "2024-08-15  to do" in out and "2024-09-01  done" in out
    assert "--difficulty bracket" in out
