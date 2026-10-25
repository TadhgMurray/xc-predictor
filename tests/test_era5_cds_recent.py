"""backfill/era5_cds_recent.py (pipeline 04e2): the recent months' weather
from the CDS. The pure parts; needs the weather venv's xarray, else skipped.

    /srv/wxvenv/bin/python -m pytest -q tests/test_era5_cds_recent.py
"""
import os
import sys

import pytest

pytest.importorskip("xarray")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in ("backfill", "scripts"):
    sys.path.insert(0, os.path.join(_ROOT, _d))
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")

import era5_cds_recent as C                                      # noqa: E402


def test_longitudes_go_back_to_the_cds_convention():
    assert C.lon180(241.75) == -118.25 and C.lon180(10.0) == 10.0 and C.lon180(180.0) == -180.0


def test_touching_tiles_are_one_request_and_far_ones_their_own():
    cells = [(34.0, 241.75), (38.0, 250.0), (45.0, 262.0),      # CA, UT, SD: touching tiles
             (61.25, 210.0), (21.25, 202.25)]                    # Alaska, Hawaii
    got = C.regions(cells)
    assert len(got) == 3
    boxes = {len(g): box for g, box in got}
    assert boxes[3] == [45.25, -118.5, 33.75, -97.75]            # N, W, S, E with the margin


def test_the_request_asks_for_every_variable_all_day():
    r = C.request("2026-10-11", [45.0, -120.0, 30.0, -100.0])
    assert (r["year"], r["month"], r["day"]) == (["2026"], ["10"], ["11"])
    assert len(r["time"]) == 24 and len(r["variable"]) == len(C.CDS_VARS)
    assert r["area"] == [45.0, -120.0, 30.0, -100.0]
