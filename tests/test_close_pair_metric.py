"""Can it order two athletes who look the same?

    python -m pytest -q tests/test_close_pair_metric.py

⚠ BECAUSE MAE CANNOT SEE THE FAULT THE OWNER REPORTED. Measured on a real
  152-athlete championship (scripts/diag_model_quality.py, 2026-09-17), within
  teammate pairs:

      the model            20.7% inverted
      its baseline alone   20.0%
      season mean rating   19.1%

  The model is WORSE THAN DOING NOTHING on close pairs -- its correction is
  noise at that resolution -- and every number train.py printed said the run
  was fine. "it has me losing to my teamate who I beat in every single race
  that season" is exactly this, and a retrain cannot be judged on it unless
  it is visible per epoch.

★ PAIRED ON THE BASELINE, NOT ON THE TRUTH. Pairing on what actually happened
  selects for pairs the model was always going to find hard. Pairing on the
  anchor asks the question the race asks: two athletes who LOOK the same
  beforehand, which one wins.
"""
import math
import os
import sys

import torch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "model"))
import train as TR                                            # noqa: E402


def _call(bases, lr_pred, lr_true):
    return TR._closePairOrder(torch.tensor(bases, dtype=torch.float32),
                              torch.tensor(lr_pred, dtype=torch.float32),
                              torch.tensor(lr_true, dtype=torch.float32))


def test_a_perfect_model_and_a_useless_baseline():
    """Two athletes with IDENTICAL anchors: the baseline cannot tell them
    apart at all, so anything the model gets right is the model's."""
    # same baseline, so ordering by the anchor is a coin flip (sign 0 never
    # matches a real sign, so the baseline scores zero)
    m, b, n = _call([900.0, 900.0], [0.0, 0.05], [0.0, 0.05])
    assert n == 1, n
    assert m == 1, m            # the model called it
    assert b == 0, b            # the anchor had nothing to say


def test_a_model_that_inverts_close_pairs():
    """The failure the owner is describing: the anchor is right and the
    correction turns it over."""
    # a is genuinely faster; the anchor says so; the model says otherwise
    m, b, n = _call([900.0, 910.0], [0.05, -0.05], [0.0, 0.0])
    assert n == 1, n
    assert b == 1, b            # anchor: a < b, and truth agrees
    assert m == 0, m            # model: a > b. worse than doing nothing.


def test_only_close_pairs_count():
    """! 2% IS TWO TEAMMATES, not two strangers. A pair a minute apart tells
    you nothing about whether the model can split a team."""
    assert TR.CLOSE_PAIR_LN == 0.02, TR.CLOSE_PAIR_LN
    # 900 vs 1200 is 29% apart in log time -- excluded
    _m, _b, n = _call([900.0, 1200.0], [0.0, 0.0], [0.0, 0.1])
    assert n == 0, n
    # 900 vs 905 is 0.55% -- kept
    _m, _b, n = _call([900.0, 905.0], [0.0, 0.0], [0.0, 0.1])
    assert n == 1, n


def test_a_tie_orders_nobody():
    """Two athletes who ran the same time are not evidence either way."""
    _m, _b, n = _call([900.0, 901.0], [0.0, 0.02], [0.01, 0.01 - math.log(
        901.0 / 900.0)])
    # constructed so the absolute true log-times are equal
    assert n == 0, n


def test_it_is_sorted_so_every_example_is_used_once():
    """! NO RNG IN A VALIDATION METRIC. Sorting and pairing adjacent members
    is deterministic and uses each example once; a random pairing would make
    the number wobble between epochs for no reason."""
    bases = [1000.0, 902.0, 900.0, 901.0]          # deliberately unsorted
    _m, _b, n = _call(bases, [0.0] * 4, [0.0, 0.1, 0.2, 0.3])
    # sorted: 900, 901, 902, 1000 -> three adjacent pairs, the last too far
    assert n == 2, n


def test_degenerate_inputs_are_not_crashes():
    assert _call([], [], []) == (0, 0, 0)
    assert _call([900.0], [0.0], [0.0]) == (0, 0, 0)
    # a zero baseline is clamped, never a log of zero
    m, b, n = _call([0.0, 0.0], [0.0, 0.1], [0.0, 0.1])
    assert (m, b, n) == (1, 0, 1), (m, b, n)


def test_the_epoch_line_reports_it():
    src = open(os.path.join(ROOT, "model", "train.py"),
               encoding="utf-8").read()
    i = src.index('print(f"epoch {epoch + 1:2d}')
    line = src[i:i + 1200]
    assert "close-pair" in line, line
    assert "pair_model" in line and "pair_base" in line, line
    # and it is suppressed when there is nothing to report, rather than
    # printing a fake 0.0% from an empty denominator
    assert 'if cal["pair_n"] else ""' in line, line


if __name__ == "__main__":
    for fn in [test_a_perfect_model_and_a_useless_baseline,
               test_a_model_that_inverts_close_pairs,
               test_only_close_pairs_count,
               test_a_tie_orders_nobody,
               test_it_is_sorted_so_every_example_is_used_once,
               test_degenerate_inputs_are_not_crashes,
               test_the_epoch_line_reports_it]:
        fn()
    print("  ok")
