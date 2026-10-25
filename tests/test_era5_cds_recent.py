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


def test_the_lower_48_is_one_request_and_far_ones_their_own():
    cells = [(34.0, 241.75), (45.0, 262.0), (40.0, 285.0),      # CA, SD, NJ: one box
             (61.25, 210.0), (61.5, 210.25), (21.25, 202.25)]    # Alaska (touching), Hawaii
    got = C.regions(cells)
    assert len(got) == 3
    boxes = {len(g): box for g, box in got}
    assert boxes[3] == [45.25, -118.5, 33.75, -74.75]            # N, W, S, E with the margin
    assert boxes[2] == [61.75, -150.25, 61.0, -149.5]


def test_the_request_asks_for_every_variable_all_day():
    r = C.request("2026-10-11", [45.0, -120.0, 30.0, -100.0])
    assert (r["year"], r["month"], r["day"]) == (["2026"], ["10"], ["11"])
    assert len(r["time"]) == 24 and len(r["variable"]) == len(C.CDS_VARS)
    assert r["area"] == [45.0, -120.0, 30.0, -100.0]


def _fake(monkeypatch, bad=(), slow=0.0):
    import threading
    import time
    clients = []

    def fetchDay(client, day, cells):
        time.sleep(slow)
        if day in bad:
            raise RuntimeError("queue timeout")
        return [{"day": day, "client": client}]
    monkeypatch.setattr(C, "fetchDay", fetchDay)

    def make():
        clients.append(threading.get_ident())
        return len(clients)
    return make, clients


def test_days_run_side_by_side_with_a_client_per_thread(monkeypatch):
    make, clients = _fake(monkeypatch, slow=0.05)
    days = {f"2026-09-{d:02d}": [(40.0, 260.0)] for d in range(1, 13)}
    got = []
    failed, left, _ = C.fetchDays(make, sorted(days), days, threads=4, budget_s=600,
                                  write=got.extend)
    assert (failed, left) == (0, 0)
    assert sorted(r["day"] for r in got) == sorted(days)
    assert len(clients) == len(set(clients)) <= 4             # one client per thread


def test_a_failed_day_is_counted_and_the_rest_written(monkeypatch):
    make, _ = _fake(monkeypatch, bad={"2026-09-03"})
    days = {f"2026-09-{d:02d}": [(40.0, 260.0)] for d in range(1, 6)}
    got = []
    failed, left, _ = C.fetchDays(make, sorted(days), days, threads=2, budget_s=600,
                                  write=got.extend)
    assert (failed, left) == (1, 0) and len(got) == 4


def test_past_the_budget_no_new_day_starts(monkeypatch):
    make, _ = _fake(monkeypatch)
    days = {f"2026-09-{d:02d}": [(40.0, 260.0)] for d in range(1, 6)}
    got = []
    failed, left, _ = C.fetchDays(make, sorted(days), days, threads=2, budget_s=-1,
                                  write=got.extend)
    assert (failed, left, got) == (0, 5, [])
