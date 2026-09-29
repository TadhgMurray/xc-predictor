"""The grid's wind is already km/h (atmost_era5_zarr._deriveWindSpeed); the
page table must not multiply it by 3.6 again (2026-09-29: "wind 48 mph")."""
import importlib.util
import os
import sys

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))


def _mod():
    spec = importlib.util.spec_from_file_location(
        "wfg", os.path.join(_ROOT, "scripts", "weather_from_grid.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_wind_is_copied_not_converted_again():
    m = _mod()
    for stream in m._STREAMS:
        q = m._sql(*stream[1:], True)
        assert "g.wind_speed_10m * 3.6" not in q and "g.wind_speed_10m," in q
        assert m.MARK in q


def test_the_grid_writer_is_the_one_converting():
    src = open(os.path.join(_ROOT, "backfill", "atmost_era5_zarr.py")).read()
    assert "np.hypot(u, v) * 3.6" in src, "if this moves, weather_from_grid must follow"
