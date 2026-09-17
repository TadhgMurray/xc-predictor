# Project: xc-predictor / tests
# File:    test_calibration_diag.py
# Purpose: the calibration arithmetic, on synthetic data where the answer is
#          known -- an honest sigma reads 0.683, a sigma half the truth reads
#          low, and a model shrinking toward the mean reads a spread well
#          under 1. No model, no database, no GPU.
#
#   python -m pytest -q tests/test_calibration_diag.py
import os
import sys

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "model"),
           os.path.join(_ROOT, "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np                                             # noqa: E402
import diag_calibration as C                                   # noqa: E402


def test_an_honest_sigma_reads_two_thirds():
    rng = np.random.default_rng(0)
    sig = np.full(60_000, 0.03)
    resid = rng.standard_normal(60_000) * sig
    assert abs(C.coverage(resid, sig, 1.0) - 0.6827) < 0.01
    assert abs(C.coverage(resid, sig, 1.645) - 0.90) < 0.01


def test_a_sigma_half_the_truth_reads_low():
    """The failure that makes a win probability confidently wrong."""
    rng = np.random.default_rng(1)
    true = 0.06
    resid = rng.standard_normal(60_000) * true
    claimed = np.full(60_000, true / 2)
    got = C.coverage(resid, claimed, 1.0)
    assert got < 0.45, got          # claims 68%, delivers under a half


def test_an_empty_band_is_none_not_a_crash():
    assert C.coverage(np.array([]), np.array([]), 1.0) is None


def test_the_bands_are_cut_where_the_twins_are_generated():
    """feature_extraction draws a FORECAST twin over 2-40 weeks and a
    HORIZON twin over 44-208, the second existing so high-school-to-college
    projection is in distribution. The bands must not straddle that seam or
    the two questions get averaged together."""
    assert 2 in C.DEFAULT_BANDS and 44 in C.DEFAULT_BANDS
    assert 208 in C.DEFAULT_BANDS
    # and the gap really is context feature 3
    fe = open(os.path.join(_ROOT, "model", "feature_extraction.py")).read()
    order = fe[fe.index("#     [course_difficulty, distance_meters, day_of_year,"):]
    order = order[:order.index("school_encoded")]
    assert order.index("course_difficulty") < order.index("distance_meters") \
        < order.index("day_of_year") < order.index("days_since_last_race")
    assert C.GAP_FEATURE == 3


def test_the_report_flags_a_sigma_that_does_not_grow_with_the_horizon():
    """A band further out that claims to be MORE certain is lying, and
    anything published past it is overconfident."""
    rng = np.random.default_rng(2)
    n = 8000
    gaps = np.concatenate([np.full(n, 5.0), np.full(n, 100.0)])
    # the far band claims a TIGHTER sigma than the near one
    sigma = np.concatenate([np.full(n, 0.05), np.full(n, 0.02)])
    resid = rng.standard_normal(2 * n) * sigma
    lines = []
    C.report(gaps, resid, sigma, resid, resid, out=lines.append)
    text = "\n".join(lines)
    assert "SIGMA DOES NOT GROW MONOTONICALLY" in text
    # and it does not cry wolf when sigma does grow
    lines2 = []
    C.report(gaps, resid, np.concatenate([np.full(n, 0.02), np.full(n, 0.05)]),
             resid, resid, out=lines2.append)
    assert "MONOTONICALLY" not in "\n".join(lines2)


def test_the_spread_column_catches_regression_to_the_mean():
    """★ THE SECOND QUESTION, and it is not calibration. A model that
    predicts the pool mean far out has excellent loss and perfect coverage
    and ranks nobody."""
    rng = np.random.default_rng(3)
    n = 20_000
    actual = rng.standard_normal(n) * 0.10          # the real spread
    shrunk = actual * 0.25                           # what a shrinker predicts
    sigma = np.full(n, 0.10)
    rows = C.report(np.full(n, 100.0), actual - shrunk, sigma, shrunk, actual,
                    out=lambda _s: None)
    assert len(rows) == 1
    assert 0.20 < rows[0]["spread"] < 0.30
    honest = C.report(np.full(n, 100.0), np.zeros(n), sigma, actual, actual,
                      out=lambda _s: None)
    assert abs(honest[0]["spread"] - 1.0) < 1e-6


def test_every_band_reported_carries_its_count():
    rng = np.random.default_rng(4)
    gaps = rng.uniform(0, 300, 5000)
    sigma = np.full(5000, 0.04)
    resid = rng.standard_normal(5000) * sigma
    rows = C.report(gaps, resid, sigma, resid, resid, out=lambda _s: None)
    assert sum(r["n"] for r in rows) == 5000
    assert all(r["n"] > 0 for r in rows)


def test_it_calls_the_names_train_actually_exports():
    """I guessed ChunkDataset and collate; they are ChunkedRaceDataset and
    collateRagged, and the run died on the first line that touched them."""
    src = open(os.path.join(_ROOT, "scripts", "diag_calibration.py")).read()
    train = open(os.path.join(_ROOT, "model", "train.py")).read()
    for name in ("ChunkedRaceDataset", "collateRagged", "splitTrainVal"):
        assert f"T.{name}" in src, name
        assert (f"class {name}(" in train or f"def {name}(" in train), name
    assert "T.ChunkDataset(" not in src and "T.collate)" not in src
