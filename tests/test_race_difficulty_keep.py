"""diag_race_difficulty._keep: what a one-race course keeps of its reading."""
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import diag_race_difficulty as D  # noqa: E402


def test_keep_matches_cellstep_arithmetic():
    w, k, p = 4 / 9, 1.0, 2.0
    base = (w * 1.0 + k * 0.0) / (w + k)          # history: the race shrunk by k
    cell = (w * 1.0 + p * base) / (w + p)         # cell: race pulled to history
    assert math.isclose(D._keep(w, k, p), cell)


def test_keep_falls_as_k_rises_and_stays_in_range():
    w = 3 / 8
    vals = [D._keep(w, k) for k in (0.25, 1, 4, 8)]
    assert all(0 < v < 1 for v in vals)
    assert vals == sorted(vals, reverse=True)
