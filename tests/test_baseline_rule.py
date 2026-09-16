"""What a prediction is anchored on.

    python -m pytest -q tests/test_baseline_rule.py

★ THE BASELINE IS NOT A DETAIL, IT IS THE PREDICTION. predictInterval returns
  baselineSeconds * exp(mu), and diag_model_quality measured exp(mu) on a real
  152-athlete championship: interquartile range 0.972-1.023, about one race's
  noise. So the baseline decides, and the network adjusts.

  The same run scored the ordering against what the day actually did:

      their last race alone     spearman 0.792
      THE MODEL                 spearman 0.899
      season mean rating        spearman 0.904

  Beating the last race and losing to the season average is exactly what
  being anchored on one race looks like. Owner, 2026-09-17: "it has me losing
  to my teamate who I beat in every single race that season."

⚠ AND THE RULE HAS TWO CALLERS IN TWO LAYOUTS -- the model's padded [B,S,F]
  and train.py's ragged [R,F] chunks. Two spellings of one rule is how they
  come to disagree, and a disagreement here is silent: the target would be
  ln(t/base_a) while inference divides by base_b. The first test below builds
  the same history in both shapes and asserts the baselines match.
"""
import math
import os
import sys

import torch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "model"))
import transformer as T                                       # noqa: E402
import train as TR                                            # noqa: E402

F = T.SEQUENCE_FEATURES


def _row(norm, days):
    v = [0.0] * F
    v[T.SEQ_NORM_TIME] = float(norm)
    v[T.SEQ_DAYS_AGO] = float(days)
    return v


def _padded(histories):
    """[B,S,F] + masks from a list of [(norm, days), ...]."""
    s = max(len(h) for h in histories)
    seqs = torch.zeros(len(histories), s, F)
    masks = torch.zeros(len(histories), s, dtype=torch.bool)
    for i, h in enumerate(histories):
        for j, (n, d) in enumerate(h):
            seqs[i, j] = torch.tensor(_row(n, d))
        masks[i, :len(h)] = True
    return seqs, masks


def _ragged(histories):
    """The chunk shape: rows concatenated, with an offsets index."""
    rows, offs = [], [0]
    for h in histories:
        for n, d in h:
            rows.append(_row(n, d))
        offs.append(len(rows))
    return {"sequences": torch.tensor(rows, dtype=torch.float32),
            "offsets": torch.tensor(offs, dtype=torch.long)}


def _model(mode, half_life=T.BASELINE_HALF_LIFE_DAYS):
    m = T.XCPredictor(n_venues=4)
    m.setBaselineRule(mode, half_life)
    m.eval()
    return m


HIST = [
    [(1000.0, 200), (980.0, 120), (995.0, 40), (1050.0, 7)],   # fluke last
    [(900.0, 300), (890.0, 60), (885.0, 10)],                  # improving
    [(1200.0, 14)],                                            # one race
]


def test_the_two_layouts_agree():
    """⚠ THE ONE THAT MATTERS. A disagreement is silent and poisons the
    target: training would fit ln(t/base_a) and inference divide by base_b.
    """
    seqs, masks = _padded(HIST)
    for mode in (T.BASELINE_LAST, T.BASELINE_EWMA):
        m = _model(mode)
        padded = m.baselineSeconds(seqs, masks)
        ragged, _rows, _len = TR._chunkBaselines(
            _ragged(HIST), mode, T.BASELINE_HALF_LIFE_DAYS)
        assert torch.allclose(padded, ragged, atol=1e-3), (mode, padded,
                                                           ragged)


def test_last_is_still_exactly_what_it_was():
    """! THE DEFAULT CANNOT MOVE. Every checkpoint trained so far was fitted
    against the last race, and its buffers default to this mode, so a model
    loaded today must anchor exactly as it did yesterday."""
    seqs, masks = _padded(HIST)
    got = T.XCPredictor(n_venues=4).baselineSeconds(seqs, masks)
    assert abs(float(got[0]) - 1050.0) < 1e-3, got
    assert abs(float(got[1]) - 885.0) < 1e-3, got
    assert abs(float(got[2]) - 1200.0) < 1e-3, got


def test_ewma_is_a_recency_weighted_geometric_mean():
    """★ GEOMETRIC, because the target is a LOG ratio: the natural centre of
    ln(t) is the geometric mean, so the network predicts a deviation from the
    middle of its own distribution."""
    hist = [(1000.0, 200), (980.0, 120), (995.0, 40), (1050.0, 7)]
    hl = 60.0
    lam = math.log(2.0) / hl
    w = [math.exp(-lam * d) for _n, d in hist]
    want = math.exp(sum(wi * math.log(n) for wi, (n, _d) in zip(w, hist))
                    / sum(w))
    seqs, masks = _padded([hist])
    got = float(_model(T.BASELINE_EWMA, hl).baselineSeconds(seqs, masks)[0])
    assert abs(got - want) < 1e-3, (got, want)
    # a race 60 days back counts half as much as one today, by construction
    assert abs(w[2] / w[3] - math.exp(-lam * 33)) < 1e-9


def test_a_fluke_last_race_no_longer_decides():
    """THE WHOLE POINT. Athlete ran 980-995 all season and then a bad 1050.
    Anchored on the last race the prediction starts from 1050; the EWMA keeps
    the season in view."""
    hist = [(1000.0, 200), (980.0, 120), (995.0, 40), (1050.0, 7)]
    seqs, masks = _padded([hist])
    last = float(_model(T.BASELINE_LAST).baselineSeconds(seqs, masks)[0])
    ewma = float(_model(T.BASELINE_EWMA).baselineSeconds(seqs, masks)[0])
    assert abs(last - 1050.0) < 1e-3, last
    assert ewma < last, (ewma, last)
    # ...and it is still pulled toward the recent race, not a flat average
    flat = math.exp(sum(math.log(n) for n, _d in hist) / len(hist))
    assert flat < ewma < last, (flat, ewma, last)


def test_the_half_life_spans_the_two_extremes():
    hist = [(1000.0, 200), (900.0, 5)]
    seqs, masks = _padded([hist])
    # a very short half-life: only the most recent race carries weight
    short = float(_model(T.BASELINE_EWMA, 0.5).baselineSeconds(seqs, masks)[0])
    assert abs(short - 900.0) < 1.0, short
    # a very long one: an unweighted geometric mean
    long_ = float(_model(T.BASELINE_EWMA, 1e6).baselineSeconds(seqs,
                                                               masks)[0])
    assert abs(long_ - math.sqrt(1000.0 * 900.0)) < 1.0, long_


def test_padding_and_missing_times_are_not_zero_second_races():
    """! A ROW WITH NO TIME IS NOT A RACE. Padding is zeros and _orZero
    writes 0.0 for a missing normalized_time, so the mask alone is not
    enough -- `t > 0` is part of what makes a row real."""
    seqs, masks = _padded([[(1000.0, 30), (0.0, 10)]])
    got = float(_model(T.BASELINE_EWMA).baselineSeconds(seqs, masks)[0])
    assert abs(got - 1000.0) < 1e-3, got
    # an empty history falls back rather than dividing by zero
    seqs = torch.zeros(1, 3, F)
    masks = torch.zeros(1, 3, dtype=torch.bool)
    m = _model(T.BASELINE_EWMA)
    assert float(m.baselineSeconds(seqs, masks)[0]) == \
        float(m.fallback_seconds)


def test_the_rule_rides_in_the_state_dict():
    """⚠ OR INFERENCE ANCHORS DIFFERENTLY FROM TRAINING, silently. Both are
    buffers, so they are in the state_dict with no extra plumbing."""
    m = _model(T.BASELINE_EWMA, 45.0)
    sd = m.state_dict()
    assert "baseline_mode" in sd and "baseline_half_life" in sd, list(sd)
    fresh = T.XCPredictor(n_venues=4)
    # the default is LAST, so a checkpoint without the keys behaves as before
    assert float(fresh.baseline_mode) == T.BASELINE_LAST
    fresh.load_state_dict(sd)
    assert float(fresh.baseline_mode) == T.BASELINE_EWMA
    assert abs(float(fresh.baseline_half_life) - 45.0) < 1e-6


def test_an_old_checkpoint_still_loads():
    """A file written before these buffers existed is short exactly two keys
    and nothing else; predict._loadModel tolerates those two BY NAME."""
    sd = T.XCPredictor(n_venues=4).state_dict()
    del sd["baseline_mode"]
    del sd["baseline_half_life"]
    missing, unexpected = T.XCPredictor(n_venues=4).load_state_dict(
        sd, strict=False)
    assert set(missing) == {"baseline_mode", "baseline_half_life"}, missing
    assert not unexpected, unexpected

    src = open(os.path.join(ROOT, "racecast", "predict.py"),
               encoding="utf-8").read()
    i = src.index("def _loadModel(")
    body = src[i:src.index("\ndef ", i + 10)]
    assert 'strict=False' in body, body
    assert '"baseline_mode", "baseline_half_life"' in body, body
    assert "raise RuntimeError" in body, body


if __name__ == "__main__":
    for fn in [test_the_two_layouts_agree,
               test_last_is_still_exactly_what_it_was,
               test_ewma_is_a_recency_weighted_geometric_mean,
               test_a_fluke_last_race_no_longer_decides,
               test_the_half_life_spans_the_two_extremes,
               test_padding_and_missing_times_are_not_zero_second_races,
               test_the_rule_rides_in_the_state_dict,
               test_an_old_checkpoint_still_loads]:
        fn()
    print("  ok")
