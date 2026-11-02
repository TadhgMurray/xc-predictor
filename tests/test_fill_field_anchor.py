"""A race with no course is anchored to its own field (owner, 2026-11-02:
Middlesex League 2023 on tfrrs, ratings ~5% high at the median race)."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in ("engine", "scripts", "racecast"):
    sys.path.insert(0, os.path.join(ROOT, p))
os.environ.setdefault("XCP_DB_PASSWORD", "x")
os.environ.setdefault("XCP_DB_QUIET", "1")


def test_offset_is_the_fields_median_gap():
    import fill_ratings as F
    k = {"hs_m": 100000.0}
    # every runner reads 5% above their season here
    rows = [(1000.0 / (b * 1.05) * 100, "hs_m", b) for b in (120, 125, 130, 118, 122, 127)]
    off = F.fieldOffset(rows, k)
    assert abs(off - 1.05) < 1e-9


def test_too_few_runners_say_nothing():
    import fill_ratings as F
    assert F.fieldOffset([(900.0, "hs_m", 120.0)] * 4, {"hs_m": 100000.0}) is None


def test_the_fill_applies_it_only_without_a_course():
    src = open(os.path.join(ROOT, "engine", "fill_ratings.py")).read()
    assert "if offsets and d is None" in src
    assert "NULLIF(btrim(COALESCE(m.course_name, mt.venue_name)), '') IS NULL" in src
