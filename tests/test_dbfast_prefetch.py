"""dbfast.dictRows(prefetch=True) (2026-11-19): the same rows in the same
order as the plain loop, a reader error raised in the caller, and an early
close that leaves no reader running."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "racecast"))

import dbfast  # noqa: E402


class _Named:
    """A named cursor's fetchmany over a fixed row list."""
    name = "fake"
    description = (("a",), ("b",))

    def __init__(self, n, fail_at=None):
        self.rows = [(i, -i) for i in range(n)]
        self.pos, self.fail_at, self.fetches = 0, fail_at, 0

    def fetchmany(self, k):
        self.fetches += 1
        if self.fail_at is not None and self.pos >= self.fail_at:
            raise RuntimeError("boom")
        out = self.rows[self.pos:self.pos + k]
        self.pos += len(out)
        return out


def test_prefetch_matches_the_plain_loop_and_stops_cleanly():
    plain = list(dbfast.dictRows(_Named(1003), batch=10))
    got = list(dbfast.dictRows(_Named(1003), batch=10, prefetch=True))
    assert got == plain and len(got) == 1003 and got[5] == {"a": 5, "b": -5}

    try:
        list(dbfast.dictRows(_Named(100, fail_at=30), batch=10, prefetch=True))
        assert False, "the reader's error must reach the caller"
    except RuntimeError as exc:
        assert str(exc) == "boom"

    cur = _Named(10_000)
    gen = dbfast.dictRows(cur, batch=10, prefetch=True)
    next(gen)
    gen.close()                     # stops and joins the reader
    seen = cur.fetches
    assert seen <= 2 + dbfast.PREFETCH_DEPTH + 1
    assert cur.fetches == seen      # nothing fetches after close

    os.environ["XCP_DB_PREFETCH"] = "0"
    try:
        assert list(dbfast.dictRows(_Named(25), batch=10, prefetch=True)) == \
            list(dbfast.dictRows(_Named(25), batch=10))
    finally:
        del os.environ["XCP_DB_PREFETCH"]


def test_prefetched_rows_keep_the_cursor_rows_and_hold_two_batches():
    """prefetchedRows (the rankings streams, 2026-11-19): the cursor's own
    rows in order, and the reader at most one batch ahead of the walk."""
    import time
    cur = _Named(1003)
    walked = 0
    gen = dbfast.prefetchedRows(cur, 10, ahead=1)
    got = []
    for row in gen:
        got.append(row)
        walked += 1
        if walked % 10 == 1:
            time.sleep(0.002)               # the reader gets every chance
            assert cur.pos <= (walked // 10 + 2) * 10   # walked batch + one
    assert got == _Named(1003).rows
    os.environ["XCP_DB_PREFETCH"] = "0"
    try:
        assert list(dbfast.prefetchedRows(_Named(25), 10)) == _Named(25).rows
    finally:
        del os.environ["XCP_DB_PREFETCH"]
