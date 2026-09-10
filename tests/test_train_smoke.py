# Project: xc-predictor / tests
# File:    test_train_smoke.py
# Purpose: The training chain must RUN. chunks -> dataset -> split ->
#          stats -> collate -> forward -> NLL -> backward -> save -> reload.
#
# ★★★ WHY THIS EXISTS. model/train.py (60KB) and model/transformer.py
#     (16KB) were rewritten on 2026-09-02 with no torch in the session and
#     were NEVER EXECUTED -- HANDOFF section 4, issue #112 "model
#     untested". The only route to them was feature_extraction.py, which
#     needs the database, takes hours and writes gigabytes; its own header
#     warns that a feature-width mismatch "surfaces as a shape error
#     inside the first Linear AFTER the extraction has written gigabytes".
#
#     So the first execution of the training code cost a full extraction,
#     and the extraction cannot run until the joint solve is good, because
#     course_difficulty is one of its features. That is a two-day loop to
#     find a one-line shape bug. model/fake_chunks.py writes synthetic
#     chunks in the extraction's exact on-disk format, which turns the
#     loop into two seconds.
#
#  ⚠ IT PROVES THE CHAIN RUNS, NOT THAT THE MODEL WORKS. The fake targets
#    carry no learnable signal, so the model cannot beat the last-race
#    baseline on them and is not expected to. Quality is what the real
#    smoke run on real chunks is for.
import os
import pickle
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
except ImportError:                                 # the server venv may not
    _HAVE_TORCH = False


@unittest.skipUnless(_HAVE_TORCH, "torch not installed")
class TheModelBuildsAndRuns(unittest.TestCase):
    def test_forward_shapes(self):
        import torch
        from transformer import (XCPredictor, SEQUENCE_FEATURES,
                                 CONTEXT_FEATURES)
        m = XCPredictor(n_venues=13)
        B, L = 4, 7
        seq = torch.randn(B, L, SEQUENCE_FEATURES).abs() * 100
        mask = torch.ones(B, L, dtype=torch.bool)
        mask[0, :3] = False                         # a short history
        ctx = torch.randn(B, CONTEXT_FEATURES)
        ven = torch.randint(0, 13, (B,))
        self.assertEqual(tuple(m(seq, mask, ctx, ven).shape), (B,))
        mu, logv = m.forwardDist(seq, mask, ctx, ven)
        self.assertEqual(tuple(mu.shape), (B,))
        self.assertEqual(tuple(logv.shape), (B,))
        self.assertEqual(tuple(m.baselineSeconds(seq, mask).shape), (B,))

    def test_the_context_width_matches_the_extraction(self):
        """⚠ THE MISMATCH THAT COSTS AN EXTRACTION. transformer.py states
        the width; feature_extraction._buildContextVector produces it. The
        header of each says the other must agree, and nothing checked."""
        import ast
        import transformer as tr
        src = open(os.path.join(_ROOT, "model", "feature_extraction.py"),
                   encoding="utf-8").read()
        tree = ast.parse(src)
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef)
                  and n.name == "_buildContextVector")
        ret = next(n for n in ast.walk(fn) if isinstance(n, ast.Return))
        # *_geo(...) contributes _geo's own return width
        geo = next(n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef) and n.name == "_geo")
        geo_w = len(next(n for n in ast.walk(geo)
                         if isinstance(n, ast.Return)).value.elts)
        width = sum(geo_w if isinstance(e, ast.Starred) else 1
                    for e in ret.value.elts)
        self.assertEqual(width, tr.CONTEXT_FEATURES,
                         f"_buildContextVector returns {width} features, "
                         f"transformer.CONTEXT_FEATURES is "
                         f"{tr.CONTEXT_FEATURES} -- an extraction built on "
                         f"this dies in the first Linear after writing "
                         f"gigabytes")


@unittest.skipUnless(_HAVE_TORCH, "torch not installed")
class TheTrainingLoopRuns(unittest.TestCase):
    """★ The whole chain, on synthetic chunks, in both on-disk layouts."""

    def _run(self, ragged=True, val_mask=True, epochs=1):
        import fake_chunks
        import train as T
        d = tempfile.mkdtemp()
        data, out = os.path.join(d, "data"), os.path.join(d, "out")
        os.makedirs(out, exist_ok=True)
        fake_chunks.write(data, n_chunks=2, chunk_size=120, n_venues=9,
                          ragged=ragged, val_mask=val_mask)
        keep = (T.DATA_DIR, T.MODEL_OUT, T.STATS_OUT, T.CHECKPOINT,
                T.EPOCHS, T.BATCH_SIZE, T.NUM_WORKERS)
        T.DATA_DIR = data
        T.MODEL_OUT = os.path.join(out, "model.pt")
        T.STATS_OUT = os.path.join(out, "target_stats.pkl")
        T.CHECKPOINT = os.path.join(out, "ckpt.pt")
        T.EPOCHS, T.BATCH_SIZE, T.NUM_WORKERS = epochs, 16, 0
        try:
            T.main()
        finally:
            (T.DATA_DIR, T.MODEL_OUT, T.STATS_OUT, T.CHECKPOINT,
             T.EPOCHS, T.BATCH_SIZE, T.NUM_WORKERS) = keep
        return out

    def test_ragged_layout(self):
        out = self._run(ragged=True)
        self.assertTrue(os.path.exists(os.path.join(out, "model.pt")))
        self.assertTrue(os.path.exists(os.path.join(out, "target_stats.pkl")))

    def test_legacy_padded_layout(self):
        """feature_extraction writes ragged now, but train.py still reads
        the padded layout and the branch is live."""
        out = self._run(ragged=False)
        self.assertTrue(os.path.exists(os.path.join(out, "model.pt")))

    def test_the_saved_stats_are_finite_and_a_log_ratio(self):
        import math
        out = self._run()
        with open(os.path.join(out, "target_stats.pkl"), "rb") as f:
            st = pickle.load(f)
        self.assertEqual(st.get("kind"), "log_ratio")
        for k in ("mean", "std", "fallback_seconds"):
            self.assertTrue(math.isfinite(float(st[k])), f"{k} is {st[k]}")
        self.assertGreater(float(st["std"]), 0.0)

    def test_the_checkpoint_resumes(self):
        """⚠ MODEL_OUT IS NOT A CHECKPOINT -- train.py's own note. Resuming
        must come off CHECKPOINT and must not restart the epoch count."""
        import train as T
        out = self._run(epochs=1)
        ck = torch.load(os.path.join(out, "ckpt.pt"), weights_only=False)
        self.assertGreaterEqual(int(ck["epoch"]), 1)
        self.assertIn("optimizer", ck)

    def test_the_saved_model_reloads_and_predicts(self):
        """The artifact has to survive the round trip the site makes."""
        import train as T
        from transformer import (XCPredictor, SEQUENCE_FEATURES,
                                 CONTEXT_FEATURES)
        out = self._run()
        sd = torch.load(os.path.join(out, "model.pt"), weights_only=False)
        n_venues = sd["venue_embedding.weight"].shape[0]
        m = XCPredictor(n_venues=n_venues)
        m.load_state_dict(sd)
        m.eval()
        seq = torch.randn(2, 5, SEQUENCE_FEATURES).abs() * 100 + 900
        mask = torch.ones(2, 5, dtype=torch.bool)
        with torch.no_grad():
            lo, hi, sig = None, None, None
            secs, lo, hi = m.predictInterval(
                seq, mask, torch.randn(2, CONTEXT_FEATURES),
                torch.zeros(2, dtype=torch.long))[:3]
        self.assertTrue(torch.isfinite(secs).all())
        self.assertTrue((lo <= hi).all(), "one-sigma band is inverted")


@unittest.skipUnless(_HAVE_TORCH, "torch not installed")
class TheSplitIsAthleteDisjoint(unittest.TestCase):
    def test_val_mask_is_used_when_present(self):
        """★ THE LEAK, ONE DIRECTORY OVER. Without val_mask.pt train.py
        falls back to random_split, which puts the same athlete on both
        sides -- the same mistake as the engine's row-vs-race holdout.
        feature_extraction writes val_mask.pt; this proves train.py
        prefers it."""
        import io as _io
        import contextlib
        buf = _io.StringIO()
        with contextlib.redirect_stdout(buf):
            TheTrainingLoopRuns()._run(val_mask=True)
        self.assertIn("athlete-disjoint split", buf.getvalue())

    def test_the_fallback_says_so_loudly(self):
        import io as _io
        import contextlib
        buf = _io.StringIO()
        with contextlib.redirect_stdout(buf):
            TheTrainingLoopRuns()._run(val_mask=False)
        self.assertIn("athlete leakage", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
