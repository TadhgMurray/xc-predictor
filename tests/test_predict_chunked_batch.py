"""The encoder gives each runner the same answer whether the field is run
in one batch padded to its longest history or in length-sorted chunks
(racecast/predict.py "BY LENGTH, IN CHUNKS", 2026-10-18). Padding is
masked, so only float noise may differ.

    python -m pytest -q tests/test_predict_chunked_batch.py
"""
import os
import sys

import pytest

torch = pytest.importorskip("torch")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "model"))

from transformer import XCPredictor, SEQUENCE_FEATURES, CONTEXT_FEATURES  # noqa: E402


def _padded(seqs, idx):
    longest = max(len(seqs[i]) for i in idx)
    x = torch.zeros(len(idx), longest, SEQUENCE_FEATURES)
    m = torch.zeros(len(idx), longest, dtype=torch.bool)
    for j, i in enumerate(idx):
        x[j, :len(seqs[i])] = seqs[i]
        m[j, :len(seqs[i])] = True
    return x, m


def test_chunked_equals_one_batch():
    torch.manual_seed(3)
    model = XCPredictor()
    model.eval()
    lengths = [3, 250, 7, 40, 1, 12, 90, 5, 60, 2]
    seqs = []
    for n in lengths:
        s = torch.randn(n, SEQUENCE_FEATURES)
        s[:, 0] = 900 + 60 * torch.rand(n)          # a normalized time
        s[:, 2] = torch.arange(n, 0, -1).float() * 20  # days before the target
        seqs.append(s)
    ctx = torch.randn(len(lengths), CONTEXT_FEATURES)
    ven = torch.zeros(len(lengths), dtype=torch.long)
    with torch.no_grad():
        x, m = _padded(seqs, list(range(len(lengths))))
        one = model(x, m, ctx, ven)
        order = sorted(range(len(lengths)), key=lambda i: lengths[i])
        got = torch.zeros(len(lengths))
        for k in range(0, len(order), 3):
            idx = order[k:k + 3]
            xc, mc = _padded(seqs, idx)
            got[idx] = model(xc, mc, ctx[idx], ven[idx]).reshape(-1)
    assert torch.allclose(one.reshape(-1), got, atol=1e-4, rtol=1e-4)


class _FakeModel:
    """Answers from each runner's own history only, so a mix-up between
    runners (wrong slot, wrong chunk) shows as a wrong number."""
    def baselineSeconds(self, seqs, masks):
        return seqs[:, :, 0].sum(1) / masks.sum(1).clamp(min=1)

    def predictInterval(self, seqs, masks, ctxs, vens):
        n = masks.sum(1).float()
        secs = 1000.0 + n + ctxs[:, 0]
        return secs, secs - 5, secs + 5, torch.full_like(secs, 0.02)


def test_predict_times_puts_every_answer_on_its_own_runner(monkeypatch):
    sys.path.insert(0, os.path.join(_ROOT, "racecast"))
    sys.path.insert(0, os.path.join(_ROOT, "scripts"))
    os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
    import predict as P
    lengths = {11: 3, 12: 200, 13: 7, 14: 1, 15: 40}
    hist = {pid: [{"date": "2026-09-%02d" % (1 + k % 28), "meet_id": 1}
                  for k in range(n)] for pid, n in lengths.items()}
    monkeypatch.setattr(P, "_loadModel", lambda: None)
    monkeypatch.setattr(P, "_model", _FakeModel())
    monkeypatch.setattr(P, "_artifacts", {"kind": "log_ratio", "encoders": {},
                                          "vocab": {}, "sequence_features": 2})
    monkeypatch.setattr(P, "_historyRows", lambda cur, ids: hist)
    monkeypatch.setattr(P, "_targetSpec", lambda cur, t: {"date": "2026-10-17"})
    monkeypatch.setattr(P, "_weatherVariants", lambda cur, spec, w: {"none": None})
    monkeypatch.setattr(P, "_targetRow", lambda spec, last, weather=None: {})
    monkeypatch.setattr(P, "_targetClock", lambda rows, spec: None)

    class _Fx:
        SEQUENCE_FEATURES = 2
        @staticmethod
        def venueIndex(row, vocab):
            return 0
    monkeypatch.setattr(P, "_fx", lambda: _Fx)
    monkeypatch.setattr(P, "_forecastExample",
                        lambda fx, h, tr, enc, as_of=None:
                        ([[900.0 + i, 0.0] for i in range(len(h))], [float(len(h))]))
    monkeypatch.setattr(P, "PRED_CHUNK", 2)
    ids = [12, 99, 11, 15, 14, 13]                # 99 has no history
    out = P._predictTimes(None, ids, {"mode": "predict"})
    assert out[1]["seconds"] is None
    for pid, e in zip(ids, out):
        if pid == 99:
            continue
        n = lengths[pid]
        assert e["normalized"] == round(1000.0 + n + n, 1), (pid, e)
        assert e["n_races"] == n
        assert abs(e["baseline"] - (900.0 + (n - 1) / 2.0)) < 1e-3
