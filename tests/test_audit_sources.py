"""scripts/audit_sources.py: the pure checks (owner, 2026-11-02)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import audit_sources as A                                         # noqa: E402


def test_order_breaks():
    rows = [("r", 1, 900.0), ("r", 2, 905.0), ("r", 3, 880.0), ("r", 4, 910.0),
            ("ok", 1, 900.0), ("ok", 2, 900.5), ("ok", 3, 950.0)]
    assert A.orderBreaks(rows) == {"r": 2}       # places 1 and 2 behind a faster 3rd


def test_order_ignores_non_finishes():
    assert A.orderBreaks([("r", 1, 900.0), ("r", 2, 999999.0), ("r", 3, 950.0)]) == {}


def test_pace():
    assert A.paceVerdict([989, 1050, 1100], 5000) is None
    assert A.paceVerdict([600, 650, 700], 5000).startswith("winner")
    assert A.paceVerdict([1513, 1600, 3000, 3100, 3200], 5000).startswith("median")
    assert A.paceVerdict([900], 0) is None


def test_twins():
    rows = [(1, 9, 5, 100, 900.0, 900.1), (1, 9, 5, 101, 910.0, 925.0),
            (2, 8, 6, 102, 800.0, 800.0)]
    got = A.twinDiffs(rows)
    assert list(got) == [(1, 5)] and got[(1, 5)][:2] == (2, 1)
    assert abs(got[(1, 5)][2] - 7.55) < 1e-9
