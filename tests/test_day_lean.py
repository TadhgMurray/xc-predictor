"""Courses whose race days all lean one way (owner, 2026-10-04: "some of
the courses are having their race day tilt being the same for every race
and not being put into course difficulty"). Pure: a tiny design."""
import os
import sys
import types

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
import run_joint as rj                                          # noqa: E402


def test_a_course_whose_days_all_run_slow_is_named():
    rng = np.random.default_rng(0)
    n_cell, races_per = 3, 12
    race_cell = np.repeat(np.arange(n_cell), races_per)
    n_race = race_cell.size
    race = np.repeat(np.arange(n_race), 5)
    D = types.SimpleNamespace(race=race, cell=race_cell[race], n_race=n_race, n_cell=n_cell)
    u = rng.normal(0, 0.005, n_race)
    u[race_cell == 1] += 0.03                  # course 1: every day 3% slow
    lines = rj.dayLeanReport(u, D, ["XC:1:d5000", "XC:2:d5000", "XC:3:d5000"])
    text = "\n".join(lines)
    assert "XC:2:d5000" in text and "+2.8" in text
    assert "XC:1:d5000" not in text and "XC:3:d5000" not in text
