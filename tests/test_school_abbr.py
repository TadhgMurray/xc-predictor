"""link_feed_twins.schoolWords: a timer's HS / Co. reads as the words it
stands for (owner, 2026-10-11: an uploaded GHSA sectional)."""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "racecast")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import _env  # noqa: F401,E402

from link_feed_twins import sameSchool  # noqa: E402


def test_abbreviations_match_the_spelled_out_school():
    assert sameSchool("Archer HS", "Archer High School")
    assert sameSchool("Rockdale Co.", "Rockdale County High School")


def test_an_abbreviation_does_not_join_another_level():
    assert not sameSchool("Archer HS", "Archer Middle School")


def test_youth_club_years_do_not_count_as_high_school_years():
    import datetime as dt
    import link_uploads as LU
    rows = [LU.Row(dt.date(y, 9, 15), "XC", gr, "Brookwood", None)
            for y, gr in ((2020, "5"), (2022, "7"), (2023, "8"), (2024, "9"), (2025, "10"), (2026, "11"))]
    u = LU.UpRow(*([None] * len(LU.UpRow._fields)))._replace(
        sport="XC", date=dt.date(2024, 11, 2), grade=None, school="Brookwood") \
        if hasattr(LU, "UpRow") else None
    if u is None:
        import pytest
        pytest.skip("no UpRow type")
    assert LU.fits(u, rows, hold_rows=rows) is None
