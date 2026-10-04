"""fill_ratings --venue: a row priced at its course is the fixed point of
the conversions' inverse, and falls back to the pool constant without a
scale."""
import math
import os
import sys
from collections import namedtuple

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for sub in ("engine", "scripts", "racecast"):
    sys.path.insert(0, os.path.join(_ROOT, sub))
os.environ.setdefault("XCP_DB_PASSWORD", "x")

import pytest  # noqa: E402

fr = pytest.importorskip("fill_ratings")


def _scale(pool, sport):
    return (300.0, 0.0, 0.0)


def _effect(pool, sport, rating, difficulty, distance):
    # a tilt that grows with the rating, like conversions._tilt
    tilt = 1.0 + 0.05 * (rating - 100.0) / 10.0
    return tilt * math.log1p(difficulty or 0.0)


def test_fixed_point_holds():
    nt = 310.0
    r = fr.venueRating(nt, "hs_m", "XC", 0.04, 5000, effect=_effect, scale=_scale)
    # the rating is 100 pm / adjusted at the effect evaluated AT that rating
    assert r == pytest.approx(100 * 300.0 / (nt / math.exp(_effect(0, 0, r, 0.04, 0))), rel=1e-9)
    # a hard course credits the time: higher than the same time on a flat one
    flat = fr.venueRating(nt, "hs_m", "XC", 0.0, 5000, effect=_effect, scale=_scale)
    assert flat == pytest.approx(100 * 300.0 / nt)
    assert r > flat


def test_no_scale_no_price():
    assert fr.venueRating(310.0, "hs_m", "XC", 0.0, 5000, effect=_effect,
                          scale=lambda p, s: None) is None


Row = namedtuple("Row", "source meet_id div_id event_id distance event_short")


def test_row_difficulty_prefers_the_championship_cell():
    races = {("anet", 1, 2): (77, "XC:Foot Locker Nationals")}
    cells = {(77, 5000): 0.03, ("XC:Foot Locker Nationals", 5000): 0.05}
    row = Row("anet", 1, 2, None, 5010.0, None)
    assert fr.rowDifficulty(row, "XC", races, cells) == 0.05
    races = {("anet", 1, 2): (77, None)}
    assert fr.rowDifficulty(row, "XC", races, cells) == 0.03
    assert fr.rowDifficulty(row._replace(distance=8000.0), "XC", races, cells) is None


def test_row_difficulty_track_location():
    races = {(5, 6, 7): "TF:loc:12:out"}
    cells = {"TF:loc:12:out": -0.02}
    assert fr.rowDifficulty(Row("anet", 5, 6, 7, None, "3200m"), "TF", races, cells) == -0.02
    assert fr.rowDifficulty(Row("anet", 5, 6, 8, None, "3200m"), "TF", races, cells) is None


def test_track_distance_from_the_event_name():
    assert fr.rowDistance(Row("anet", 5, 6, 7, None, "3200m"), "TF") == 3200.0
    assert fr.rowDistance(Row("anet", 5, 6, 7, 1609.0, "3200m"), "TF") == 1609.0
    assert fr.rowDistance(Row("anet", 5, 6, 7, None, None), "TF") is None
