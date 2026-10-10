# Project: xc-predictor / tests
# File:    test_chunk_reads.py
# Purpose: an epoch reads each chunk file once per DataLoader worker slot,
#          not once per batch (owner, 2026-10-10: the first full run on a
#          network volume sat at 0% GPU and 32% iowait -- the batch order
#          sent every worker a different chunk almost every batch).
import os
import subprocess
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "model")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    import torch                                    # noqa: F401
    _HAVE_TORCH = True
except ImportError:
    _HAVE_TORCH = False


def _reads(sampler, subset, workers):
    """Chunk loads for one epoch, each worker holding one chunk and the
    DataLoader handing batch p to worker p % workers (its round robin)."""
    base = getattr(subset, "dataset", subset)
    g = subset.indices
    cache = [None] * workers
    loads = n = 0
    for p, batch in enumerate(sampler):
        cids = {g[i] // base.chunk_size for i in batch}
        assert len(cids) == 1, "a batch spans two chunk files"
        c = cids.pop()
        if cache[p % workers] != c:
            cache[p % workers] = c
            loads += 1
        n += 1
    return loads, n


@unittest.skipUnless(_HAVE_TORCH, "torch not installed")
class OneReadPerChunk(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        subprocess.run([sys.executable, os.path.join(_ROOT, "model", "fake_chunks.py"),
                        "--out", cls.tmp.name, "--chunks", "120", "--chunk-size", "256"],
                       check=True, capture_output=True)
        import train as T
        T.DATA_DIR = cls.tmp.name
        cls.T = T
        cls.ds = T.ChunkedRaceDataset(cls.tmp.name)
        cls.train, cls.val = T.splitTrainVal(cls.ds)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_each_chunk_is_read_once(self):
        for workers in (1, 4, 15):
            for sub, shuffle in ((self.train, True), (self.val, False)):
                s = self.T.ChunkAwareBatchSampler(sub, 32, shuffle, slots=workers)
                loads, n = _reads(s, sub, workers)
                self.assertEqual(n, len(s))
                chunks = len(s.by_chunk)
                per = max((len(v) + 31) // 32 for v in s.by_chunk.values())
                # ! THE TAIL IS ALLOWED ITS SLACK: once the chunk queue runs
                #   dry the slots end at different times and the last rounds
                #   lose alignment -- at most one chunk's batches per slot
                #   re-read. Real runs hold ~400 chunks per slot, so that is
                #   a couple of percent; the old order re-read per BATCH.
                self.assertLessEqual(loads, chunks + workers * per,
                                     f"{workers} workers, shuffle={shuffle}")
                if workers < 15:
                    self.assertEqual(loads, chunks)
                self.assertLess(loads, n / 2)

    def test_every_example_once_per_epoch(self):
        s = self.T.ChunkAwareBatchSampler(self.train, 32, True, slots=4)
        seen = [i for b in s for i in b]
        self.assertEqual(sorted(seen), list(range(len(self.train))))

    def test_the_order_changes_between_epochs(self):
        s = self.T.ChunkAwareBatchSampler(self.train, 32, True, slots=4)
        self.assertNotEqual(list(s), list(s))


if __name__ == "__main__":
    unittest.main()
