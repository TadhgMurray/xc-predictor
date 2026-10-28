# Project: xc-predictor / tests
# File:    test_weather_fetch_threads.py
# Purpose: 04e's tiles fetched side by side write what the serial loop wrote,
#          in the same order, and one bad tile fails only itself.
#
# ★ WHY (2026-10-25). 04e took 9,797 s and the run waits for it before 04f.
#   The tiles are read FETCH_THREADS at a time now. Checked against the live
#   store (30 tiles, 8 threads: the same 8,256 rows in the same order, 2.6x
#   faster); this test keeps the ordering and failure rules without it.
import os
import sys
import threading
import time
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backfill"))
# stand-ins only while the module loads; the real ones go back after, so no
# other test imports a stub
_db = types.ModuleType("database")
_db.getConn = _db.initPool = _db.closePool = lambda *a, **k: None
_saved = {m: sys.modules.get(m) for m in ("xarray", "database")}
sys.modules["xarray"] = sys.modules.get("xarray") or types.ModuleType("xarray")
sys.modules["database"] = _db
try:
    import atmost_era5_zarr as W                      # noqa: E402
finally:
    for _m, _v in _saved.items():
        if _v is None:
            sys.modules.pop(_m, None)
        else:
            sys.modules[_m] = _v


class Conn:
    def commit(self):
        pass


def _run(monkeypatch, threads, bad=()):
    seen = set()

    def fetch(ds, tile):
        seen.add(threading.get_ident())
        k = tile[0][0][0]
        time.sleep(0.02 * (k % 3))                     # finish out of order
        if k in bad:
            raise RuntimeError("store hiccup")
        return [{"tile": k, "hour": h} for h in range(3)]
    got = []
    monkeypatch.setattr(W, "fetchTileWeather", fetch)
    monkeypatch.setattr(W, "writeWeatherRows", lambda conn, rows: got.extend(rows))
    monkeypatch.setattr(W, "WRITE_BATCH", 5)
    tiles = [[((k, 0.0), ["2025-10-01"])] for k in range(20)]
    W._runBackfill(None, tiles, Conn(), threads=threads)
    return got, len(seen)


def test_threads_write_the_serial_rows_in_tile_order(monkeypatch):
    serial, n1 = _run(monkeypatch, 1)
    threaded, n8 = _run(monkeypatch, 8)
    assert threaded == serial and n1 == 1 and n8 > 1
    assert [r["tile"] for r in threaded[::3]] == list(range(20))


def test_one_bad_tile_fails_only_itself(monkeypatch, capsys):
    got, _ = _run(monkeypatch, 8, bad={4, 9})
    assert sorted({r["tile"] for r in got}) == [k for k in range(20) if k not in (4, 9)]
    assert capsys.readouterr().out.count("tile failed") == 2


def test_every_tile_failing_still_fails_the_step(monkeypatch):
    try:
        _run(monkeypatch, 8, bad=set(range(20)))
    except SystemExit as e:
        assert e.code == 2
    else:
        raise AssertionError("an all-failed fetch must fail the step")
