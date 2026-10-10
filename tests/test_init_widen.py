# Project: xc-predictor / tests
# File:    test_init_widen.py
# Purpose: train.py --init pours an OLDER model into a wider one (break
#          features, Student-t, the context token). Widened with break
#          features only, the new model must predict EXACTLY what the old
#          one did: its new input columns start at zero. ⚠ The first version
#          inserted the break features before the venue block, so the old
#          venue weights landed on the new inputs -- this would have caught it.
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "model"))

try:
    import torch
    _HAVE_TORCH = True
except ImportError:
    _HAVE_TORCH = False


def _batch(B=4, S=6):
    from transformer import SEQUENCE_FEATURES, CONTEXT_FEATURES, SEQ_DAYS_AGO, CONTEXT_GAP_INDEX
    g = torch.Generator().manual_seed(0)
    seq = torch.randn(B, S, SEQUENCE_FEATURES, generator=g).abs() * 50 + 900
    seq[..., SEQ_DAYS_AGO] = torch.linspace(400, 20, S).expand(B, S)
    mask = torch.ones(B, S, dtype=torch.bool)
    ctx = torch.randn(B, CONTEXT_FEATURES, generator=g)
    ctx[:, CONTEXT_GAP_INDEX] = 20.0
    ven = torch.randint(1, 9, (B,), generator=g)
    return seq, mask, ctx, ven


@unittest.skipUnless(_HAVE_TORCH, "torch not installed")
class InitWiden(unittest.TestCase):
    def setUp(self):
        import train as T
        from transformer import XCPredictor
        self.T, self.X = T, XCPredictor
        torch.manual_seed(0)
        self.old = XCPredictor(n_venues=9).eval()
        # non-trivial venue weights, so a misplaced venue block shows
        with torch.no_grad():
            self.old.venue_embedding.weight.normal_()

    def test_break_features_widen_without_changing_a_prediction(self):
        new = self.X(n_venues=9, derived_features=3, student_t=True).eval()
        self.T._initFrom(new, self.old.state_dict())
        seq, mask, ctx, ven = _batch()
        with torch.no_grad():
            a = self.old.forwardDist(seq, mask, ctx, ven)
            b = new.forwardDist(seq, mask, ctx, ven)
        for x, y in zip(a, b):
            self.assertTrue(torch.allclose(x, y, atol=1e-5), (x, y))
        self.assertEqual(int(new.n_derived), 3)

    def test_the_context_token_loads_and_runs(self):
        new = self.X(n_venues=9, derived_features=3, student_t=True,
                     context_token=True).eval()
        self.T._initFrom(new, self.old.state_dict())
        self.assertTrue(torch.all(new.context_token_proj.weight == 0))
        seq, mask, ctx, ven = _batch()
        with torch.no_grad():
            mu, logvar = new.forwardDist(seq, mask, ctx, ven)
        self.assertTrue(torch.isfinite(mu).all() and torch.isfinite(logvar).all())
        # and the site rebuilds the same shape from the saved file
        again = self.X.fromState(new.state_dict())
        self.assertTrue(again.context_token and again.derived_features == 3)
        again.load_state_dict(new.state_dict())


if __name__ == "__main__":
    unittest.main()
