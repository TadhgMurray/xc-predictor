"""backfill/atmost_era5_zarr._notDone (2026-11-12): a day already stored is
not fetched again however its feed spells the date; a date no parser reads
is dropped; spellings of one day fold to one work unit."""
import datetime as dt
import importlib.util
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
pytest.importorskip("xarray")
pd = pytest.importorskip("pandas")


def _mod():
    sys.path[:0] = [os.path.join(ROOT, "backfill"), os.path.join(ROOT, "scripts")]
    os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
    spec = importlib.util.spec_from_file_location("wx", os.path.join(ROOT, "backfill", "atmost_era5_zarr.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_stored_days_are_skipped_whatever_the_spelling():
    m = _mod()
    work = pd.DataFrame({"cellLat": [34.25] * 6 + [40.0], "cellLon": [241.75] * 6 + [280.0],
                         "date": ["2021-09-25", "2021-09-25 00:00:00", "09/26/2021",
                                  "2021-09-27T08:00:00", dt.date(2021, 9, 28), "garbage",
                                  "2022-10-01"]})
    done = {(34.25, 241.75, "2021-09-25"), (40.0, 280.0, dt.date(2022, 10, 1))}
    got = m._notDone(work, done)
    assert sorted(got["date"]) == ["2021-09-26", "2021-09-27", "2021-09-28"]
