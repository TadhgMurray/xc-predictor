"""engine/speed_ratings_db._stagingLines (2026-11-19): the go-live's staging
COPY rendered a chunk at a time is byte-for-byte the payload the row-by-row
path (_asPairs -> _copyChunk) sent, so sr_staging_* holds the same rows."""
import io
import os
import sys

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in ("engine", "scripts"):
    sys.path.insert(0, os.path.join(ROOT, p))
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")

D = pytest.importorskip("speed_ratings_db")


class _Cur:
    def __init__(self):
        self.out = []

    def copy_expert(self, sql, f):
        self.out.append(f.read())


def test_same_payload_as_row_by_row():
    rng = np.random.default_rng(7)
    n = 5003
    rid = rng.integers(1, 10**12, n).astype(np.int64)
    cases = [
        (rid, np.round(rng.random(n) * 150, 2),
         np.array(["hs_m", "", None, "college_f"], dtype=object)[rng.integers(0, 4, n)]),
        (rid, (rng.random(n) * 150).astype(np.float32)),                 # no pool
        (rid, np.array([np.nan, 0.1, 1e-7, 123456.789] * (n // 4) + [5.0] * (n % 4)),
         np.array(["hs_f"] * n)),                                        # numpy str_
    ]
    for pairs in cases:
        old = _Cur()
        D._copyInto(old, "t", ("result_id", "val", "pool"), D._asPairs(pairs), chunk=1000)
        new = [payload for _k, payload in D._stagingLines(pairs, chunk=1000)]
        assert "".join(new) == "".join(old.out)
        assert sum(k for k, _p in D._stagingLines(pairs, chunk=1000)) == n
