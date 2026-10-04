"""A deep field reads a course as easy (owner, 2026-10-04: Mt. SAC's 4828
at +6.2% "way too easy"). Pure: bracket_engine.fieldAdjust."""
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
import bracket_engine as be                                     # noqa: E402


def test_the_slope_is_measured_within_courses_and_elite_races_move_to_the_typical_field():
    rng = np.random.default_rng(0)
    n_course, per = 200, 6
    base = np.repeat(np.arange(n_course), per)
    level = rng.normal(0.05, 0.03, n_course)[base]       # course levels differ
    x = rng.normal(105, 4, base.size)                    # field depth
    true_slope = -0.006                                  # deeper = reads easier
    D = level + true_slope * (x - 105) + rng.normal(0, 0.005, base.size)
    w = np.ones(base.size)
    ok = np.ones(base.size, dtype=bool)
    x_ref = np.full(base.size, 105.0)
    rep = {}
    adj = be.fieldAdjust(D, w, ok, x, x_ref, base, np.zeros(base.size, dtype=int), rep)
    assert abs(rep[0][0] - true_slope) < 0.0008
    # every reading lands on its course's level at the typical field
    assert np.std(adj - level) < 0.007 < np.std(D - level)


def test_one_kind_of_field_is_unchanged():
    base = np.repeat(np.arange(100), 3)
    x = np.full(base.size, 104.0)
    D = np.linspace(0, 0.1, base.size)
    out = be.fieldAdjust(D, np.ones(base.size), np.ones(base.size, bool), x,
                         np.full(base.size, 104.0), base, np.zeros(base.size, int))
    assert np.allclose(out, D)
