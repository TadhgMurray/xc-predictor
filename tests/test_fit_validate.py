"""
Self-contained tests for engine/fit_validate.py. No database, no fixtures.

The synthetic world mirrors the real one: every athlete carries their own
ability offset, and a shared curve (here a slope) is what we are trying to
recover. That is the structure that makes a by-row split leak.
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine"))
from fit_validate import coreMask, crossValidate, bootstrapBands   # noqa: E402


TRUE_SLOPE = 1.75


def _world(n_athletes=120, per_athlete=8, ability_sd=3.0, noise_sd=0.4, seed=0):
    """Each athlete: own ability offset, several (x, y) rows on a shared slope."""
    rng = np.random.default_rng(seed)
    athlete, x, y = [], [], []
    for a in range(n_athletes):
        ability = rng.normal(0, ability_sd)
        xs = rng.uniform(-1, 1, size=per_athlete)
        for xi in xs:
            athlete.append(a)
            x.append(xi)
            y.append(ability + TRUE_SLOPE * xi + rng.normal(0, noise_sd))
    return np.array(athlete), np.array(x), np.array(y)


def _fitSlopeWithAthleteEffects(athlete, x, y):
    """Return a fit_fn/score_fn pair over an index set.

    The model has a free intercept per athlete plus one shared slope -- the
    same shape as the real engine (athlete effects + a shared curve). It is
    exactly this per-athlete capacity that a by-row split leaks.
    """
    def fit_fn(idx):
        seen = np.unique(athlete[idx])
        col = {int(a): j for j, a in enumerate(seen)}
        A = np.zeros((len(idx), len(seen) + 1))
        for r, i in enumerate(idx):
            A[r, col[int(athlete[i])]] = 1.0
        A[:, -1] = x[idx]
        coeffs, *_ = np.linalg.lstsq(A, y[idx], rcond=None)
        return {"slope": coeffs[-1],
                "intercepts": {int(a): coeffs[col[int(a)]] for a in seen},
                "mean_intercept": float(np.mean(coeffs[:-1]))}

    def score_fn(params, idx):
        # An unseen athlete has no intercept, so the honest prediction uses the
        # population mean. A seen athlete's own intercept is used -- which is
        # precisely how a by-row split flatters itself.
        pred = np.array([
            params["intercepts"].get(int(athlete[i]), params["mean_intercept"])
            + params["slope"] * x[i]
            for i in idx])
        return float(np.sqrt(np.mean((y[idx] - pred) ** 2)))

    return fit_fn, score_fn


def test_folds_never_share_an_athlete():
    athlete, x, y = _world()
    fit_fn, score_fn = _fitSlopeWithAthleteEffects(athlete, x, y)

    seen_train, seen_test = [], []

    def spy_fit(train_idx):
        seen_train.append(set(np.unique(athlete[train_idx]).tolist()))
        return fit_fn(train_idx)

    def spy_score(params, test_idx):
        seen_test.append(set(np.unique(athlete[test_idx]).tolist()))
        return score_fn(params, test_idx)

    crossValidate(athlete, spy_fit, spy_score, k=5, seed=1)

    for tr, te in zip(seen_train, seen_test):
        assert not (tr & te), "an athlete appeared in both train and test"
    print("  folds never share an athlete .......... OK")


def test_by_row_split_is_optimistic():
    """The reason the module splits by athlete at all."""
    athlete, x, y = _world()
    fit_fn, score_fn = _fitSlopeWithAthleteEffects(athlete, x, y)

    _, by_athlete = crossValidate(athlete, fit_fn, score_fn, k=5, seed=1)

    # The same K-fold, but split by ROW: athletes land on both sides.
    rng = np.random.default_rng(1)
    fold = rng.integers(0, 5, size=len(athlete))
    row_scores = []
    for f in range(5):
        test = np.flatnonzero(fold == f)
        train = np.flatnonzero(fold != f)
        row_scores.append(score_fn(fit_fn(train), test))
    by_row = float(np.mean(row_scores))

    assert by_row < by_athlete["mean"], (by_row, by_athlete["mean"])
    print(f"  by-row RMSE {by_row:.3f} flatters vs by-athlete "
          f"{by_athlete['mean']:.3f} ... OK")


def test_bootstrap_bands_cover_the_truth():
    """Nominal 95% bands should cover the true slope about 95% of the time."""
    covered = 0
    trials = 40
    for t in range(trials):
        athlete, x, y = _world(n_athletes=80, per_athlete=6, seed=100 + t)
        fit_fn, _ = _fitSlopeWithAthleteEffects(athlete, x, y)
        lo, hi, _ = bootstrapBands(
            athlete, fit_fn, lambda p: np.array([p["slope"]]),
            n_boot=80, alpha=0.05, seed=t)
        if lo[0] <= TRUE_SLOPE <= hi[0]:
            covered += 1
    rate = covered / trials
    assert rate >= 0.85, f"coverage {rate:.2f} is too low"
    print(f"  bootstrap coverage {rate:.0%} of nominal 95% ...... OK")


def test_cluster_bootstrap_is_wider_than_row_bootstrap():
    """Rows within an athlete are correlated; a row bootstrap under-reports."""
    athlete, x, y = _world(n_athletes=60, per_athlete=12, ability_sd=6.0, seed=7)
    fit_fn, _ = _fitSlopeWithAthleteEffects(athlete, x, y)
    ev = lambda p: np.array([p["slope"]])                       # noqa: E731

    lo, hi, _ = bootstrapBands(athlete, fit_fn, ev, n_boot=120, seed=3)
    cluster_width = hi[0] - lo[0]

    rng = np.random.default_rng(3)
    draws = [fit_fn(rng.integers(0, len(athlete), size=len(athlete)))["slope"]
             for _ in range(120)]
    row_width = float(np.percentile(draws, 97.5) - np.percentile(draws, 2.5))

    assert cluster_width > row_width, (cluster_width, row_width)
    print(f"  cluster band {cluster_width:.3f} > row band {row_width:.3f} .. OK")


def test_core_mask_keeps_the_connected_core():
    # Cells 0-3 form a well-linked core; 10-11 are an isolated island; the
    # edge 3-99 is carried by a single athlete and must not survive.
    cell_a, cell_b, ath = [], [], []
    for a in range(6):                                   # core, 6 athletes
        for (u, v) in [(0, 1), (1, 2), (2, 3), (0, 3)]:
            cell_a.append(u); cell_b.append(v); ath.append(a)
    for a in range(6, 12):                               # island, 6 athletes
        cell_a.append(10); cell_b.append(11); ath.append(a)
    cell_a.append(3); cell_b.append(99); ath.append(99)  # one-athlete tendril

    keep, info = coreMask(np.array(cell_a), np.array(cell_b), np.array(ath),
                          min_athletes=3)

    assert not keep[-1], "a one-athlete edge was kept"
    assert keep[:24].all(), "the core was dropped"
    assert not keep[24:30].any(), "the smaller island was kept"
    assert info["core_cells"] == 4, info
    print(f"  core kept {info['core_rows']} rows, {info['core_cells']} cells, "
          f"dropped {info['dropped_rows']} ... OK")


if __name__ == "__main__":
    for fn in [test_folds_never_share_an_athlete,
               test_by_row_split_is_optimistic,
               test_bootstrap_bands_cover_the_truth,
               test_cluster_bootstrap_is_wider_than_row_bootstrap,
               test_core_mask_keeps_the_connected_core]:
        fn()
    print("\nall fit_validate tests passed")
