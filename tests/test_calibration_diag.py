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


def test_a_sigma_that_falls_with_the_horizon_is_not_flagged_when_the_error_falls_too():
    """★ THE FALSE ALARM THE FIRST HONEST RUN TRIPPED. sigma fell from 0.0588
    at 20-44w to 0.0501 at 44-104w and the naive monotonicity test called it
    a lie -- but |err| fell with it (0.0487 -> 0.0422) and cover68 held at
    0.664. A one-year gap is the same athlete at the same meet a season
    later, which is genuinely easier than six months out at another
    distance."""
    rng = np.random.default_rng(2)
    n = 8000
    gaps = np.concatenate([np.full(n, 30.0), np.full(n, 60.0)])
    sigma = np.concatenate([np.full(n, 0.0588), np.full(n, 0.0501)])
    resid = rng.standard_normal(2 * n) * sigma      # honest at both widths
    lines = []
    C.report(gaps, resid, sigma, resid, resid, out=lines.append)
    text = "\n".join(lines)
    assert "NOT TRACKING" not in text, text
    assert "MONOTONIC" not in text


def test_a_sigma_that_stops_tracking_its_error_is_flagged():
    """The failure that matters: the band is too tight for the error the
    model actually makes, whatever order the bands come in."""
    rng = np.random.default_rng(5)
    n = 8000
    gaps = np.concatenate([np.full(n, 30.0), np.full(n, 60.0)])
    true = np.concatenate([np.full(n, 0.05), np.full(n, 0.12)])
    claimed = np.concatenate([np.full(n, 0.05), np.full(n, 0.05)])
    resid = rng.standard_normal(2 * n) * true
    lines = []
    C.report(gaps, resid, claimed, resid, resid, out=lines.append)
    text = "\n".join(lines)
    assert "SIGMA IS NOT TRACKING THE ERROR" in text
    assert "44-104w" in text


def test_the_ratio_column_is_the_gaussian_constant_when_sigma_is_right():
    rng = np.random.default_rng(6)
    n = 60_000
    sig = np.full(n, 0.04)
    resid = rng.standard_normal(n) * sig
    rows = C.report(np.full(n, 10.0), resid, sig, resid, resid,
                    out=lambda _s: None)
    assert abs(rows[0]["ratio"] - C.GAUSS_MAD) < 0.01
    assert abs(C.GAUSS_MAD - 0.7979) < 1e-4


def test_the_real_run_passes_its_own_check():
    """The numbers the server actually printed, band by band. If a future
    change makes this table fail, the change is wrong -- this is a model
    that measured honest."""
    bands = [(1.0, 0.0404, 0.0330), (5.0, 0.0417, 0.0336),
             (14.0, 0.0466, 0.0371), (30.0, 0.0588, 0.0487),
             (60.0, 0.0501, 0.0422)]
    rng = np.random.default_rng(7)
    gaps, resid, sigma = [], [], []
    for g, s, _e in bands:
        n = 6000
        gaps.append(np.full(n, g))
        sigma.append(np.full(n, s))
        resid.append(rng.standard_normal(n) * s)
    gaps = np.concatenate(gaps)
    sigma = np.concatenate(sigma)
    resid = np.concatenate(resid)
    lines = []
    rows = C.report(gaps, resid, sigma, resid, resid, out=lines.append)
    assert len(rows) == 5
    assert "⚠" not in "\n".join(lines)
    # and every band the server reported was inside the tolerance
    for _g, s, e in bands:
        assert abs(e / s - C.GAUSS_MAD) < C.RATIO_TOLERANCE, (s, e, e / s)


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


def test_the_venue_count_comes_from_the_checkpoint_not_the_default():
    """The run died here: XCPredictor's n_venues defaults to 1, so a bare
    constructor plus a real checkpoint is
      size mismatch for venue_embedding.weight: [36571,16] vs [1,16].
    The count has to be read off the saved embedding, the way
    predict._loadModel reads it."""
    class Fake:
        shape = (36_571, 16)
    assert C.venueCount({"venue_embedding.weight": Fake()}) == 36_571
    src = open(os.path.join(_ROOT, "scripts", "diag_calibration.py")).read()
    assert "XCPredictor()" not in src
    assert "XCPredictor(n_venues=venueCount(state))" in src


def test_it_unwraps_every_spelling_train_and_predict_write():
    """train.py saves model.pt as a bare state_dict and its resume file under
    "model"; predict.py also accepts "state_dict"."""
    weights = {"venue_embedding.weight": object()}
    assert C.unwrapState(weights) is weights
    assert C.unwrapState({"epoch": 7, "model": weights}) is weights
    assert C.unwrapState({"state_dict": weights}) is weights
    # a state dict that happens to contain neither key is returned whole
    assert C.unwrapState({"a": 1}) == {"a": 1}


def test_only_the_baseline_buffers_may_be_missing():
    """strict=False accepts ANY missing key, which would report calibration
    for a model that is partly random."""
    assert C.surpriseKeys(["baseline_mode", "baseline_half_life"], []) == []
    assert C.surpriseKeys(["encoder.layers.0.self_attn.in_proj_weight"], []) \
        == ["encoder.layers.0.self_attn.in_proj_weight"]
    assert C.surpriseKeys([], ["leftover"]) == ["leftover"]
    src = open(os.path.join(_ROOT, "scripts", "diag_calibration.py")).read()
    assert "load_state_dict(state, strict=False)" in src   # with the check
    assert "surpriseKeys(missing, unexpected)" in src


def test_the_default_checkpoint_is_the_one_train_writes():
    """T.MODEL_PATH does not exist; the name is MODEL_OUT, and getattr of the
    wrong one silently fell through to a path that only happened to match."""
    import train as T
    assert hasattr(T, "MODEL_OUT")
    src = open(os.path.join(_ROOT, "scripts", "diag_calibration.py")).read()
    assert 'getattr(T, "MODEL_OUT"' in src


def test_the_target_is_seconds_and_is_never_exponentiated():
    """★ WHAT THE FIRST RUN PRINTED: cover68 0.000 in every band, |err| inf,
    spread nan -- because the target was treated as a z-score and
    exponentiated. train.py says "the chunks store raw targets in seconds"
    and z-scores them at the loss with model.targetZ, so the target IS the
    answer in seconds."""
    train = open(os.path.join(_ROOT, "model", "train.py")).read()
    assert "The chunks store raw targets in seconds" in train
    assert "model.targetZ(sequences, masks, targets)" in train
    src = open(os.path.join(_ROOT, "scripts", "diag_calibration.py")).read()
    assert "torch.exp(\n                tgt" not in src
    assert "tgt.to(dev) * model.target_std" not in src
    # the target is taken as seconds, and every non-finite row is dropped
    assert "true_secs = tgt.to(dev).to(torch.float32).reshape(-1)" in src
    assert "torch.isfinite(secs)" in src and "torch.isfinite(true_secs)" in src


def test_a_nonfinite_residual_cannot_masquerade_as_zero_coverage():
    """coverage() of nan is False, so an unfiltered inf reads as a model
    whose band covers nothing -- indistinguishable from a real failure."""
    sig = np.full(100, 0.03)
    resid = np.full(100, np.inf)
    assert C.coverage(resid, sig, 1.0) == 0.0     # why the mask has to be upstream
    src = open(os.path.join(_ROOT, "scripts", "diag_calibration.py")).read()
    assert "dropped" in src and "TOO MANY TO IGNORE" in src


def test_the_sample_spans_the_whole_split_not_its_first_half_percent():
    """The first run scored range(50_000) of 10,000,322 -- the corpus's first
    0.5% in athlete order -- and topped out at a 50-week horizon, leaving the
    44w+ bands that gate the projection measured on 2,503 rows and 104w+
    empty."""
    idx = C.sampleIndices(10_000_322, 50_000)
    assert len(idx) == 50_000
    assert len(set(idx)) == 50_000
    assert min(idx) < 1_000                      # still starts at the front
    assert max(idx) > 9_900_000                  # and reaches the very end
    # spread across the split, not clustered
    assert len(set(i // 100_000 for i in idx)) >= 100


def test_the_sample_stays_chunk_cache_friendly():
    """A random 50k of 10M is ~50k whole-chunk torch.loads, since
    ChunkedRaceDataset caches exactly one chunk. Contiguous runs avoid that."""
    idx = C.sampleIndices(1_000_000, 10_000, blocks=100)
    runs = 1 + sum(1 for a, b in zip(idx, idx[1:]) if b != a + 1)
    assert runs <= 100, runs                      # 100 runs, not 10,000
    assert len(idx) == 10_000


def test_the_sampler_degenerates_sanely():
    assert C.sampleIndices(10, 50) == list(range(10))
    assert C.sampleIndices(0, 50) == []
    assert C.sampleIndices(100, 0) == []
    one = C.sampleIndices(100, 7, blocks=1)
    assert one == list(range(7))
    # never out of range, whatever the shape
    for total, want, blocks in ((100, 99, 200), (1000, 3, 50), (50, 50, 7)):
        got = C.sampleIndices(total, want, blocks)
        assert got and max(got) < total and len(set(got)) == len(got)
