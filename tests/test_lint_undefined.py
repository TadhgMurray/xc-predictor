"""No undefined names in the site and engine modules.

An edit that lands a line in the wrong function is a NameError on the
first request (2026-09-06: unitKinds in the home route, every page 500).
pyflakes sees it without running anything."""
import glob
import os

import pytest

_ROOT = os.path.join(os.path.dirname(__file__), "..")


def test_no_undefined_names():
    pyflakes = pytest.importorskip("pyflakes.api")
    from pyflakes.reporter import Reporter
    import io
    # the site and the engine, which every request and every pipeline
    # step import. scripts/ has old one-offs with dead code paths
    # (scrape_results, atmost_oneshot, database.Json), logged as 248, and
    # engine/fit_geometry_correction references a legacy helper it no
    # longer defines; they are excluded, not blessed.
    skip = {"fit_geometry_correction.py"}
    files = [f for f in glob.glob(os.path.join(_ROOT, "racecast", "*.py"))
             + glob.glob(os.path.join(_ROOT, "engine", "*.py"))
             if os.path.basename(f) not in skip]
    out = io.StringIO()
    rep = Reporter(out, out)
    for f in files:
        pyflakes.checkPath(f, reporter=rep)
    bad = [ln for ln in out.getvalue().splitlines() if "undefined name" in ln]
    assert not bad, "\n".join(bad)
