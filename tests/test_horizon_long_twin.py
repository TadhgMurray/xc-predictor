# Project: xc-predictor / tests
# File:    test_horizon_long_twin.py
# Purpose: the two-year twin. ⚠ THE MEASURED FAILURE it exists for: sampling
#          the WHOLE validation split, realized horizons topped out at 52.1
#          weeks with the 104w+ band EMPTY -- horizon twins were being made in
#          quantity, but the gap they landed on was almost always just under a
#          year, because the draw was uniform over a window whose mass sits
#          there. So any projection past a year was extrapolation off the end
#          of the measured range, which is exactly what the recruiting page
#          wants to do.
#
#   python -m pytest -q tests/test_horizon_long_twin.py
import datetime as dt
import os
import random
import sys

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "model"), os.path.join(_ROOT, "scripts"),
           os.path.join(_ROOT, "engine"), os.path.join(_ROOT, "racecast")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ! corrections.py IS NOT IN THE CHECKOUT (it is generated), and
#   feature_extraction imports one name from it at module scope. One targeted
#   stub, not a catch-all: a __getattr__ on a stub module shadows real
#   lookups and broke torch's own import when I tried it.
if "corrections" not in sys.modules:
    import types
    _c = types.ModuleType("corrections")
    _c.distanceOverrideSQL = lambda *a, **k: ("", "")
    sys.modules["corrections"] = _c

import feature_extraction as F                                 # noqa: E402


def _career(years=4, per_year=8, end=dt.date(2026, 9, 1)):
    """An athlete racing `per_year` times a year for `years` years, oldest
    first, as _gapTwin expects."""
    out = []
    n = years * per_year
    for i in range(n):
        d = end - dt.timedelta(days=int((n - i) * 365.0 / per_year))
        out.append({"date": d.isoformat(), "normalized_time": 1000.0 - i})
    return out


def test_the_long_twin_reaches_past_two_years():
    hist = _career()
    target = {"date": "2026-09-01", "normalized_time": 940.0}
    seq = [[0.0, 0.0, 0.0] for _ in hist]
    rng = random.Random(0)
    gaps = [t["gap_weeks"] for t in
            (F._horizonLongTwin(hist, target, seq, rng) for _ in range(200))
            if t]
    assert gaps, "no long twin from a four-year career"
    assert min(gaps) >= F.HORIZON_LONG_MIN_WEEKS, min(gaps)
    assert max(gaps) <= F.HORIZON_GAP_MAX_WEEKS
    assert max(gaps) > 104.0, max(gaps)


def test_it_is_labelled_so_the_extraction_can_count_it():
    hist = _career()
    target = {"date": "2026-09-01", "normalized_time": 940.0}
    seq = [[0.0, 0.0, 0.0] for _ in hist]
    t = F._horizonLongTwin(hist, target, seq, random.Random(1))
    assert t is not None
    assert t["kind"] == "horizon_long"
    assert t["is_forecast"] is True
    # it hides races rather than inventing any, and keeps at least two
    assert len(t["prior_results"]) >= F.MIN_KEPT_RACES
    assert len(t["prior_results"]) < len(hist)
    assert len(t["sequence"]) == len(t["prior_results"])


def test_a_one_season_athlete_produces_none_rather_than_a_bad_one():
    """! IT RETURNS None FOR ALMOST EVERYONE, and that is the design: lo is
    clipped up to 104 weeks and hi is the gap back to their second race."""
    short = _career(years=1, per_year=8, end=dt.date(2026, 9, 1))
    target = {"date": "2026-09-01", "normalized_time": 940.0}
    seq = [[0.0, 0.0, 0.0] for _ in short]
    for s in range(20):
        assert F._horizonLongTwin(short, target, seq, random.Random(s)) is None


def test_the_ordinary_horizon_draw_now_favours_the_long_end():
    """weeks = lo + random()**skew * (hi - lo), so skew below 1 biases toward
    hi. Flat over a short window was the bug."""
    assert F.HORIZON_GAP_SKEW < 1.0
    assert F.HORIZON_LONG_SKEW < 1.0
    # the near-term twin still favours SHORT, which is what it is for
    assert F.FORECAST_GAP_SKEW > 1.0


def test_skewing_long_actually_moves_the_mass():
    """Not just that the constant is below 1: that it changes the draw."""
    hist = _career(years=3)
    target = {"date": "2026-09-01", "normalized_time": 940.0}
    seq = [[0.0, 0.0, 0.0] for _ in hist]

    def sample(skew, n=300):
        rng = random.Random(7)
        return [t["gap_weeks"] for t in
                (F._gapTwin(hist, target, seq, rng,
                            min_weeks=F.HORIZON_GAP_MIN_WEEKS,
                            max_weeks=F.HORIZON_GAP_MAX_WEEKS,
                            skew=skew, kind="horizon") for _ in range(n)) if t]
    flat = sample(1.0)
    long_ = sample(F.HORIZON_GAP_SKEW)
    assert flat and long_
    assert sum(long_) / len(long_) > sum(flat) / len(flat)


def test_the_extraction_warns_when_the_long_band_would_be_empty():
    """Their absence is invisible in the horizon count: twins were being
    produced in quantity while every long gap was missing."""
    src = open(os.path.join(_ROOT, "model", "feature_extraction.py")).read()
    assert 'kinds.get("horizon_long")' in src
    assert "NO TWO-YEAR TWINS" in src
    # and the generator is called, not merely defined
    assert "_horizonLongTwin(prior_results, target_result," in src
    assert "HORIZON_LONG_TWIN_RATE" in src
