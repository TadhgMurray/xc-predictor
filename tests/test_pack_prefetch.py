"""The pack's prefetch thread must change WHEN batches are fetched, never
WHICH batches arrive or in what order -- and a failed fetch must surface.

    python -m pytest -q tests/test_pack_prefetch.py
"""
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in ("scripts", "engine"):
    sys.path.insert(0, os.path.join(_ROOT, _d))
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
os.environ.setdefault("XCP_DB_QUIET", "1")

import pytest                                                    # noqa: E402
import speed_ratings as S                                        # noqa: E402


def test_same_batches_same_order():
    batches = [[(i, j) for j in range(50)] for i in range(40)]
    assert list(S._prefetched(iter(batches))) == batches


def test_it_overlaps_fetching_with_work():
    def slow():
        for i in range(8):
            time.sleep(0.05)                 # the database
            yield [i]
    t0 = time.time()
    for _b in S._prefetched(slow()):
        time.sleep(0.05)                     # the row loop
    assert time.time() - t0 < 0.8 * (8 * 0.1)   # serial would be 0.8 s


def test_a_failed_fetch_raises_rather_than_ending_the_data():
    def broken():
        yield [1]
        raise RuntimeError("server closed the connection unexpectedly")
    got = S._prefetched(broken())
    assert next(got) == [1]
    with pytest.raises(RuntimeError, match="server closed"):
        next(got)


def test_it_can_be_switched_off(monkeypatch):
    monkeypatch.setenv("XCP_PACK_PREFETCH", "0")
    it = iter([[1]])
    assert S._prefetched(it) is it
