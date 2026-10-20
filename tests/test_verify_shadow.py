"""The new ranking tables are checked page by page before the swap, and a
damaged one is never swapped in (2026-10-19/20: two builds in a row went
live with an invalid page).

    python -m pytest -q tests/test_verify_shadow.py
"""
import os
import sys
import types

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("XCP_DB_PASSWORD", "x")
sys.path.insert(0, os.path.join(ROOT, "racecast"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
os.chdir(ROOT)

import build_ranking_results as B                               # noqa: E402
import integrity_check as IC                                    # noqa: E402


class _Cur:
    def __init__(self):
        self.last = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.last = sql

    def fetchone(self):
        if "block_size" in self.last:
            return (8192, 8192 * 100)            # 100 pages a segment
        return (250,)                            # 250 pages a table


class _Conn:
    autocommit = False

    def cursor(self):
        return _Cur()

    def commit(self):
        pass


def test_clean_tables_pass(monkeypatch):
    seen = []
    monkeypatch.setattr(IC, "checkRange",
                        lambda cur, t, a, b: seen.append((t, a, b)) or [])
    c = _Conn()
    B.verifyShadow(c, ("rr_new", "as_new"))
    assert seen[:3] == [("rr_new", 0, 99), ("rr_new", 100, 199), ("rr_new", 200, 249)]
    assert len(seen) == 6 and c.autocommit is False


def test_a_damaged_page_stops_the_swap(monkeypatch):
    monkeypatch.setattr(IC, "checkRange", lambda cur, t, a, b:
                        [(199, None, None, "invalid page in block 199")]
                        if t == "rr_new" and a <= 199 <= b else [])
    with pytest.raises(RuntimeError, match="NOT swapping"):
        B.verifyShadow(_Conn(), ("rr_new", "as_new"))
