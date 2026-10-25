"""data_checks.venue_label: a division stored at its venue's longer label
(Mt. SAC's "3 miles", 4828) whose field ran like the shorter one (4715)
is LISTED with the shorter label as evidence -- never pinned (owner,
2026-10-24: "NONE OF THOSE SHOULD BE CORRECTED") -- and the pins it once
wrote come back out (labelSized).

    python -m pytest -q tests/test_venue_label.py
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in ("engine", "scripts"):
    sys.path.insert(0, os.path.join(_ROOT, _d))
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")

import data_checks as D                                          # noqa: E402

MTSAC = [(4715.0, 11288, 9), (4828.0, 14222, 6), (5000.0, 50, 1)]


def test_a_long_label_running_like_the_short_one_is_pinned():
    d, ev = D.venueLabelVerdict(4828.0, 40, 0.025, 0.8, MTSAC)
    assert d == 4715.0 and "4715" in ev


def test_left_alone():
    # the field ran at its level: the cell and the day absorbed the label
    assert D.venueLabelVerdict(4828.0, 40, 0.003, 0.5, MTSAC)[0] is None
    # most of the field not high
    assert D.venueLabelVerdict(4828.0, 40, 0.025, 0.4, MTSAC)[0] is None
    # a thin field
    assert D.venueLabelVerdict(4828.0, 5, 0.025, 0.9, MTSAC)[0] is None
    # never upward: the stored label is already the short one
    assert D.venueLabelVerdict(4715.0, 40, 0.03, 0.9, MTSAC)[0] is None
    # 5000 vs 4828 is 3.6% -- inside the window -- but the gap implies
    # ~4900, nearer the stored 5000 than 4828: no pin
    assert D.venueLabelVerdict(5000.0, 40, 0.01, 0.9, MTSAC + [(4828.0, 500, 3)])[0] is None
    # a label with too little history behind it is not a ruler
    assert D.venueLabelVerdict(4828.0, 40, 0.025, 0.8, [(4715.0, 40, 1)])[0] is None


def test_label_sized_pins_come_out():
    assert D.labelSized(4828.0, 4715.0)          # Mt. SAC
    assert D.labelSized(4828.0, 4747.0)          # Hayward
    assert D.labelSized(5000.0, 4828.0)          # CIF State, Woodward
    assert not D.labelSized(6000.0, 5000.0)      # Miner: a 5k listed as a 6k
    assert not D.labelSized(4506.0, 5000.0)      # upward (Hole in the Wall)
    assert not D.labelSized(None, 5000.0)


def test_race_field_never_pins_a_label_sized_move():
    # 9%+ off its level, snapping to a distance only 3.4% shorter: that move
    # cannot explain the gap, so it is listed, not pinned
    row = (1, 2, "anet", 5000.0, 40, 0.09, 0.9, 0.0)
    act, d, ev = D.fieldVerdict(row, {1: [4828.0, 5000.0]})
    assert (act, d) == ("review", None) and "too small" in ev
