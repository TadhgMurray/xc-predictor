"""XCP_MUD_SENS=eb (2026-10-02): a course with enough wet history may be
MUDDIER than the average course; the default 'capped' holds every course at
or below 1."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine"))
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
import fit_weather_correction as fw                                # noqa: E402


def _world(seed=0):
    rng = np.random.default_rng(seed)
    true_s = {"firm": 0.1, "grass": 1.0, "bog": 2.5}
    course, m, y = [], [], []
    for c, s in true_s.items():
        for _ in range(4000):                     # long wet-and-dry history
            mud = rng.normal(0, 0.02)             # the global curve's prediction
            course.append(c); m.append(mud)
            y.append(s * mud + rng.normal(0, 0.01))
    m = np.array(m); y = np.array(y)
    cols = {"course": np.array(course)}
    layout = {"soil_spline": [0]}
    return cols, np.array([1.0]), y, m[:, None], layout


def _run(mode, monkeypatch):
    monkeypatch.setenv("XCP_MUD_SENS", mode)
    cols, coef, yd, Xd, layout = _world()
    return {c: v["s"] for c, v in fw.fitCourseSoil(cols, coef, yd, Xd, layout).items()}


def test_capped_holds_the_bog_at_one(monkeypatch):
    s = _run("capped", monkeypatch)
    assert s["bog"] == 1.0 and s["firm"] < 0.3


def test_eb_lets_a_well_measured_bog_exceed_one(monkeypatch):
    s = _run("eb", monkeypatch)
    assert s["bog"] > 2.0 and s["firm"] < 0.3 and abs(s["grass"] - 1.0) < 0.15
