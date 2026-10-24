"""scripts/diag_order_headroom.py (E0)'s pure parts. No database.

    python -m pytest -q tests/test_diag_order_headroom.py
"""
import os
import random
import sys

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "model")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest                                                # noqa: E402
import diag_order_headroom as H                              # noqa: E402

ROWS = [("2025-09-06", 100.0, "hs_m"), ("2025-09-13", 102.0, "hs_m"),
        ("2025-09-20", 104.0, "hs_m"), ("2025-09-27", 106.0, "hs_m"),
        ("2025-10-04", 108.0, "hs_m"), ("2025-04-01", 80.0, "ms_m")]


def test_pairs_count_ties_half_and_skip_equal_times():
    assert H.pairShare([1, 2, 3], [10, 20, 30]) == 1.0
    assert H.pairShare([3, 2, 1], [10, 20, 30]) == 0.0
    assert H.pairShare([1, 1], [10, 20]) == 0.5
    assert H.pairShare([1, 2], [10, 10]) is None


def test_hindsight_uses_both_sides_but_never_the_day():
    r, pool = H.seasonForm(ROWS, "2025-09-20")
    assert pool == "hs_m" and r == pytest.approx((100 + 102 + 106 + 108) / 4)
    r, _p = H.nearestForm(ROWS, "2025-09-20")
    assert r == pytest.approx(104.0)          # symmetric neighbours average to the day
    assert H.lastForm(ROWS, "2025-09-20") == (102.0, "hs_m")
    assert H.lastForm(ROWS, "2024-01-01") is None


def test_a_jog_is_not_form():
    rows = ROWS[:5] + [("2025-09-16", 70.0, "hs_m")]
    r, _p = H.seasonForm(rows, "2025-09-20")
    assert r == pytest.approx((100 + 102 + 106 + 108) / 4)


def _race(rng, noise_form, noise_day=0.02, n=30):
    """True form f; prior sees f + noise_form, nearest sees f exactly;
    the day adds noise_day."""
    got = []
    for _j in range(n):
        f = rng.uniform(900, 1100)
        act = f * (1 + rng.gauss(0, noise_day))
        got.append((act, {"last": f * (1 + rng.gauss(0, 2 * noise_form)),
                          "prior": f * (1 + rng.gauss(0, noise_form)),
                          "season": f * (1 + rng.gauss(0, noise_form / 2)),
                          "nearest": f}))
    return got


def test_the_ceiling_reads_above_the_prior_and_the_split_recovers_the_form_error():
    rng = random.Random(0)
    races = [{"scores": H.raceScores(_race(rng, 0.025)), "n": 30,
              "level": "hs_m", "month": "2025-10"} for _k in range(60)]
    text = "\n".join(H.summarize(races))
    near = statistics_mean([r["scores"]["nearest"][0] for r in races])
    prior = statistics_mean([r["scores"]["prior"][0] for r in races])
    assert near > prior
    assert "nearest - prior" in text and "form error" in text
    # rms spread prior ~ sqrt(.025^2 + .02^2), nearest ~ .02: form error ~ 2.5%
    line = next(ln for ln in text.splitlines() if "form error" in ln)
    est = float(line.split("<= ~")[1].split("%")[0])
    assert est == pytest.approx(2.5 * 0.6745, abs=0.6)     # a median, not an sd


def statistics_mean(xs):
    return sum(xs) / len(xs)
