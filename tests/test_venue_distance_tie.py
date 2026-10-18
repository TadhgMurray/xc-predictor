"""One venue's cross country distances are tied by the era spring (owner,
2026-10-18): a thin cell rests on its siblings, not on the flat typical
course. Neighbours only, per venue and era; track keys untouched.

    python -m pytest -q tests/test_venue_distance_tie.py
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "engine"))

import numpy as np                                               # noqa: E402

import run_joint as rj                                           # noqa: E402


def test_neighbours_of_one_venue_and_era():
    keys = ["XC:13433:d4700@e3", "XC:13433:d5000@e3", "XC:13433:d4800@e3",
            "XC:13433:d4800@e2", "XC:115:d6000@e3", "TF:loc:9:out@e3",
            "XC:115:d8000@e3"]
    pairs, w = rj.venueDistancePairs(keys, sd=0.025, era_sd=0.01)
    got = {tuple(sorted((keys[a], keys[b]))) for a, b in pairs.T}
    assert got == {("XC:13433:d4700@e3", "XC:13433:d4800@e3"),
                   ("XC:13433:d4800@e3", "XC:13433:d5000@e3"),
                   ("XC:115:d6000@e3", "XC:115:d8000@e3")}
    # the tie's sd grows with the log-distance gap, floored for near-same
    sd = {tuple(sorted((keys[a], keys[b]))): 0.01 / np.sqrt(x) for (a, b), x in zip(pairs.T, w)}
    assert sd[("XC:115:d6000@e3", "XC:115:d8000@e3")] > sd[("XC:13433:d4800@e3", "XC:13433:d5000@e3")]
    g = max(np.log(4800 / 4700) / rj.VENUE_DIST_REF, rj.VENUE_DIST_MIN_GAP)
    assert abs(sd[("XC:13433:d4700@e3", "XC:13433:d4800@e3")] - 0.025 * np.sqrt(g)) < 1e-9
    near = rj.venueDistancePairs(["XC:1:d5000", "XC:1:d5050"], sd=0.025, era_sd=0.01)[1][0]
    assert abs(0.01 / np.sqrt(near) - 0.025 * np.sqrt(rj.VENUE_DIST_MIN_GAP)) < 1e-9


def test_off_and_empty():
    assert rj.venueDistancePairs(["XC:1:d5000"], sd=0.025) == (None, None)
    assert rj.venueDistancePairs(["XC:1:d5000", "XC:1:d4000"], sd=0) == (None, None)
