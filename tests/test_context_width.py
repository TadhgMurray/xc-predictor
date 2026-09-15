"""The context vector's width and its three new features (2026-09-15).

    python -m pytest -q tests/test_context_width.py

★ THIS TEST EXISTS BECAUSE THE FAILURE IT CATCHES IS EXPENSIVE. Both
  feature_extraction.py and transformer.py carry the width as a constant,
  and the header of each says the same thing: a mismatch is not caught at
  extraction time, it surfaces as a shape error inside the first nn.Linear
  AFTER the extraction has written gigabytes. So the width is counted here,
  from the builder's actual return, against both constants -- which takes
  milliseconds and no database.

★ STUBS, SO IT RUNS ANYWHERE. feature_extraction imports `corrections`
  (engine/corrections.py: 51MB of dict literals, gitignored, server-only)
  and `database` (wants a password). Both are replaced with the shape the
  module needs before the import, the same pattern tests/test_accounts.py
  uses. Nothing here touches a database.
"""
import contextlib
import datetime
import os
import random
import sys
import types

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in ("model", "scripts", "engine", "racecast"):
    sys.path.insert(0, os.path.join(ROOT, _d))

if "corrections" not in sys.modules:
    _c = types.ModuleType("corrections")
    _c.distanceOverrideSQL = lambda *a, **k: ("", "")
    sys.modules["corrections"] = _c
if "database" not in sys.modules:
    _db = types.ModuleType("database")

    @contextlib.contextmanager
    def _noConn():
        yield None
    _db.getConn = _noConn
    _db.initPool = lambda *a, **k: None
    _db.closePool = lambda *a, **k: None
    sys.modules["database"] = _db

pytest.importorskip("torch")
pytest.importorskip("sklearn")
fx = pytest.importorskip("feature_extraction")
T = pytest.importorskip("transformer")


# ------------------------------------------------------------------ #
# A target row with every column the builder reads, and a sequence.
# ------------------------------------------------------------------ #

def targetRow(**over):
    row = {
        "date": "2025-10-04", "grade": "11", "gender": "M", "school": "Great Oak",
        "course_difficulty": 0.031, "distance_meters": 5000.0,
        "gps_lat": 33.5, "gps_long": -117.2, "altitude_meters": 380.0,
        "temp_c": 18.0, "dew_point_c": 9.0, "humidity": 55.0,
        "apparent_temp_c": 17.0, "precipitation_mm": 0.0, "pressure_hpa": 1015.0,
        "cloud_cover": 20.0, "wind_speed_km": 6.0, "wind_dir": 210.0,
        "normalized_time": 950.0, "is_xc": True, "source": None, "place": 3,
    }
    row.update(over)
    return row


class _Enc:
    """The two encoders the builder maps through, as the dict-backed shape
    _encoderMap caches onto them."""
    def __init__(self, classes):
        self.classes_ = list(classes)


def encoders():
    return {"grade": _Enc(["10", "11", "12", "None"]),
            "school": _Enc(["Great Oak", "None"])}


def buildCtx(**over):
    row = targetRow(**over)
    seq = [[0.0] * fx.SEQUENCE_FEATURES]
    seq[0][2] = 7.0                      # days_ago of the last prior race
    return fx._buildContextVector(row, seq, [row], encoders(), is_forecast=False)


# ------------------------------------------------------------------ #
# THE WIDTH
# ------------------------------------------------------------------ #

def test_the_builder_agrees_with_both_constants():
    ctx = buildCtx()
    assert len(ctx) == fx.CONTEXT_FEATURES, (
        f"_buildContextVector returns {len(ctx)}, "
        f"feature_extraction.CONTEXT_FEATURES says {fx.CONTEXT_FEATURES}")
    assert fx.CONTEXT_FEATURES == T.CONTEXT_FEATURES, (
        "feature_extraction and transformer disagree about the context width; "
        "this is the shape error that only appears after gigabytes")
    assert fx.SEQUENCE_FEATURES == T.SEQUENCE_FEATURES


def test_the_year_index_points_at_the_year():
    """The one index another module reads by name (predict.py clamps it)."""
    ctx = buildCtx(date="2019-09-14")
    assert ctx[fx.CONTEXT_YEAR_INDEX] == 2019.0
    assert buildCtx(date="2025-10-04")[fx.CONTEXT_YEAR_INDEX] == 2025.0


def test_the_new_features_sit_at_the_end_and_the_old_ones_did_not_move():
    """Appended, not inserted -- so every index that existed still means
    what it meant."""
    ctx = buildCtx()
    assert ctx[0] == 0.0                              # is_forecast
    assert ctx[1] == pytest.approx(0.031)             # course_difficulty
    assert ctx[2] == 5000.0                           # distance
    assert ctx[3] == float(datetime.date(2025, 10, 4).timetuple().tm_yday)
    assert ctx[4] == 7.0                              # days since last race
    assert buildCtx(is_forecast=True) is not None     # still accepts the flag


# ------------------------------------------------------------------ #
# THE GRADE ORDINAL
# ------------------------------------------------------------------ #

@pytest.mark.parametrize("raw,pool,want", [
    ("7", 1.0, 7.0), ("9", 2.0, 9.0), ("12", 2.0, 12.0),
    ("Sr", 2.0, 12.0), ("senior", 2.0, 12.0), ("Fr", 2.0, 9.0),
    ("Jr.", 2.0, 11.0),
    # the same four words, one rung ladder higher, told apart by the pool
    ("Fr", 3.0, 13.0), ("senior", 3.0, 16.0),
    # tfrrs eligibility codes are a college year whatever the pool says
    ("FR-1", 3.0, 13.0), ("SR-4", 3.0, 16.0), ("sr-4", 3.0, 16.0),
    ("SO-2", 3.0, 14.0), ("SO2", 3.0, 14.0),
    # a bare college-ladder number is already absolute
    ("14", 3.0, 14.0),
    # nothing usable -> 0.0, which the known-flag beside it disambiguates
    (None, 2.0, 0.0), ("", 2.0, 0.0), ("xx", 2.0, 0.0),
    ("99", 2.0, 0.0), ("4", 2.0, 0.0),
])
def test_grade_ordinal(raw, pool, want):
    assert fx._gradeOrdinal(raw, pool) == want


def test_the_known_flag_distinguishes_unknown_from_any_real_grade():
    known = buildCtx(grade="11")
    unknown = buildCtx(grade=None)
    gi = fx.CONTEXT_YEAR_INDEX - 2          # ordinal, then flag, then year
    assert known[gi] == 11.0 and known[gi + 1] == 1.0
    assert unknown[gi] == 0.0 and unknown[gi + 1] == 0.0


def test_grade_is_not_leakage():
    """place is excluded from the context because the outcome determines
    it. A grade is known before the gun, like the pool and the date."""
    src = open(os.path.join(ROOT, "model", "feature_extraction.py"),
               encoding="utf-8").read()
    ctx_body = src.split("def _buildContextVector", 1)[1].split("\ndef ", 1)[0]
    assert '"place"' not in ctx_body and "['place']" not in ctx_body


# ------------------------------------------------------------------ #
# THE HORIZON TWIN -- the new class of example
# ------------------------------------------------------------------ #

def career(n_seasons=4, per_season=6, start="2021-09-01"):
    """One race a fortnight for per_season races, once a year, n_seasons
    times -- a multi-year career, which is what a horizon twin needs."""
    d0 = datetime.date.fromisoformat(start)
    rows = []
    for s in range(n_seasons):
        for i in range(per_season):
            day = d0 + datetime.timedelta(days=365 * s + 14 * i)
            rows.append(targetRow(date=day.isoformat()))
    return rows


def test_a_horizon_twin_reaches_a_year_to_four_years_back():
    rows = career()
    target = rows[-1]
    prior = rows[:-1]
    seq = [[0.0] * fx.SEQUENCE_FEATURES for _ in prior]
    rng = random.Random(7)
    seen = []
    for _ in range(60):
        twin = fx._horizonTwin(prior, target, seq, rng)
        if twin:
            seen.append(twin)
    assert seen, "a four-season career must be able to produce a horizon twin"
    for t in seen:
        assert fx.HORIZON_GAP_MIN_WEEKS <= t["gap_weeks"] <= fx.HORIZON_GAP_MAX_WEEKS
        assert t["is_forecast"] is True
        assert t["kind"] == "horizon"
        assert len(t["prior_results"]) >= fx.MIN_KEPT_RACES
        assert len(t["prior_results"]) < len(prior)      # something is hidden
        # every kept race really does sit before the cut
        cut = (datetime.date.fromisoformat(target["date"])
               - datetime.timedelta(days=t["gap_weeks"] * 7.0))
        last_kept = datetime.date.fromisoformat(t["prior_results"][-1]["date"])
        assert last_kept < cut


def test_one_season_cannot_produce_a_horizon_twin():
    """The selection this class carries, made explicit: only an athlete
    whose history reaches back a year gets one."""
    rows = career(n_seasons=1, per_season=10)
    prior, target = rows[:-1], rows[-1]
    seq = [[0.0] * fx.SEQUENCE_FEATURES for _ in prior]
    rng = random.Random(3)
    assert all(fx._horizonTwin(prior, target, seq, rng) is None
               for _ in range(40))


def test_the_near_term_twin_did_not_change():
    """The horizon class is additional. The forecast twin must still draw
    inside its own window, or the near-term model paid for this."""
    rows = career()
    prior, target = rows[:-1], rows[-1]
    seq = [[0.0] * fx.SEQUENCE_FEATURES for _ in prior]
    rng = random.Random(11)
    got = [fx._forecastTwin(prior, target, seq, rng) for _ in range(60)]
    got = [g for g in got if g]
    assert got
    for t in got:
        assert fx.FORECAST_GAP_MIN_WEEKS <= t["gap_weeks"] <= fx.FORECAST_GAP_MAX_WEEKS
        assert t["kind"] == "forecast"


def test_the_two_windows_do_not_overlap():
    assert fx.FORECAST_GAP_MAX_WEEKS < fx.HORIZON_GAP_MIN_WEEKS


def test_both_twins_are_emitted_from_the_builder():
    src = open(os.path.join(ROOT, "model", "feature_extraction.py"),
               encoding="utf-8").read()
    body = src.split("def buildAthleteExamples", 1)[1].split("\ndef ", 1)[0]
    assert "_forecastTwin(" in body and "_horizonTwin(" in body


# ------------------------------------------------------------------ #
# THE INFERENCE SIDE
# ------------------------------------------------------------------ #

def test_inference_clamps_the_year_and_reads_the_shared_builder():
    src = open(os.path.join(ROOT, "racecast", "predict.py"), encoding="utf-8").read()
    # one builder, not a second copy that can drift
    assert "fx._buildContextVector(" in src
    assert "_clampYear(ctx, fx)" in src
    assert "CONTEXT_YEAR_INDEX" in src
    assert '"max_year"' in src
    tr = open(os.path.join(ROOT, "model", "train.py"), encoding="utf-8").read()
    # the ceiling the clamp needs has to be measured and written
    assert "from feature_extraction import CONTEXT_YEAR_INDEX" in tr
    assert '"max_year": float(stats.get("max_year") or 0.0)' in tr


def test_the_clamp_actually_clamps():
    """_clampYear by behaviour, not just by source: a model trained
    through 2026 must not be asked about 2029."""
    import importlib
    pytest.importorskip("flask")
    predict = importlib.import_module("predict")

    ctx = buildCtx(date="2029-10-04")
    assert ctx[fx.CONTEXT_YEAR_INDEX] == 2029.0

    predict._artifacts = {"max_year": 2026.0}
    predict._clampYear(ctx, fx)
    assert ctx[fx.CONTEXT_YEAR_INDEX] == 2026.0, "a future year must clamp"

    # a year inside the training range is left exactly alone
    past = buildCtx(date="2019-10-04")
    predict._clampYear(past, fx)
    assert past[fx.CONTEXT_YEAR_INDEX] == 2019.0

    # ...and a model saved before the year feature existed carries no
    # ceiling, so the clamp is a no-op rather than a crash
    fresh = buildCtx(date="2029-10-04")
    predict._artifacts = {}
    predict._clampYear(fresh, fx)
    assert fresh[fx.CONTEXT_YEAR_INDEX] == 2029.0
    predict._artifacts = None
    predict._clampYear(fresh, fx)
    assert fresh[fx.CONTEXT_YEAR_INDEX] == 2029.0
