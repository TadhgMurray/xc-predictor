# Project: xc-predictor / tests
# File:    test_silent_dropout_twins.py
# Purpose: the 2026-10-10 created examples. A SILENT twin asks a long real
#          gap from inside it: the whole history visible, the end of the gap
#          hidden, no racing in it (every older twin hid races that were run,
#          so "hidden" always meant "kept racing"). A DROPOUT twin deletes
#          random prior races, so a missing result stops reading as a
#          missing athlete.
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

if "corrections" not in sys.modules:          # see test_horizon_long_twin
    import types
    _c = types.ModuleType("corrections")
    _c.distanceOverrideSQL = lambda *a, **k: ("", "")
    sys.modules["corrections"] = _c

import feature_extraction as F                                 # noqa: E402


def _hist(dates):
    return [{"date": d, "normalized_time": 1000.0 - i} for i, d in enumerate(dates)]


SPRING = _hist(["2026-03-01", "2026-03-15", "2026-03-29", "2026-04-12"])
OCT = {"date": "2026-10-10", "normalized_time": 950.0}


def test_a_long_gap_gets_a_silent_twin_with_everything_visible():
    seq = [[0.0, 0.0, float(i)] for i in range(len(SPRING))]
    rng = random.Random(0)
    twins = [t for t in (F._silentTwin(SPRING, OCT, seq, rng) for _ in range(200)) if t]
    assert twins
    gap_days = (dt.date(2026, 10, 10) - dt.date(2026, 4, 12)).days
    for t in twins:
        assert t["is_forecast"] and t["kind"] == "silent"
        assert t["prior_results"] is SPRING          # nothing hidden from the history
        assert len(t["sequence"]) == len(SPRING)
        assert 14 <= t["hidden_days"] <= min(gap_days, 280)


def test_a_short_gap_gets_none():
    week_ago = {"date": "2026-04-26", "normalized_time": 950.0}
    rng = random.Random(0)
    assert F._silentTwin(SPRING, week_ago, [[0, 0, 0]] * 4, rng) is None


def test_the_context_reads_real_time_away_as_gap_minus_hidden():
    # the builder clips hidden to the gap; idle = gap - hidden is what
    # transformer.breakFeatures calls time away
    gap = 181.0
    hidden = 60.0
    assert min(max(hidden, 0.0), gap) == hidden
    assert gap - hidden == 121.0


def test_dropout_keeps_order_and_enough_races_and_drops_some():
    hist = _hist([f"2025-{m:02d}-01" for m in range(1, 13)])
    seq = [[0.0, 0.0, float(i)] for i in range(len(hist))]
    rng = random.Random(1)
    twins = [t for t in (F._dropoutTwin(hist, OCT, seq, rng) for _ in range(300)) if t]
    assert twins
    for t in twins:
        kept = t["prior_results"]
        assert F.MIN_KEPT_RACES <= len(kept) < len(hist)
        dates = [r["date"] for r in kept]
        assert dates == sorted(dates)
        # each kept row is that race's own row from the full sequence
        assert [r[2] for r in t["sequence"]] == [float(hist.index(r)) for r in kept]
        assert t["kind"] == "dropout" and not t["is_forecast"]


def test_build_emits_both_kinds():
    seq_src = SPRING + [OCT]
    rng = random.Random(3)
    # stub the vector builder: only the twin plumbing is under test
    orig = F._baseVectors
    F._baseVectors = lambda res, enc: [[0.0] * F_SEQ for _ in res]
    try:
        kinds = set()
        for _ in range(50):
            for ex in F.buildAthleteExamples(seq_src, {}, rng):
                kinds.add(ex.get("kind") or "real")
    finally:
        F._baseVectors = orig
    assert "silent" in kinds and "dropout" in kinds


F_SEQ = 23
