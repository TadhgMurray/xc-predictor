"""Rows of one race normalised at another distance than their group
(2026-11-01, Sagebrush 2021): the straggler test and the nightly wiring."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import diag_div_stragglers as D                                 # noqa: E402


def test_the_one_woman_left_at_the_mens_distance():
    key = ("tfrrs", 18170, 0)
    rows = [(key, "college_f", 1.161, ("a",)), (key, "college_f", 1.161, ("b",)),
            (key, "college_f", 1.160, ("c",)), (key, "college_f", 0.854, ("paytin",)),
            (key, "college_m", 1.100, ("m1",)), (key, "college_m", 1.100, ("m2",)),
            (key, "college_m", 1.101, ("m3",)), (key, "", 0.83, ("unknown",))]
    got = [r[0][0] for r in D.stragglers(rows)]
    assert got == ["paytin"]


def test_nightly_runs_it_between_normalising_and_pricing():
    sh = open(os.path.join(ROOT, "deploy", "nightly_update.sh"), encoding="utf-8").read()
    assert sh.index("05_normalize_new") < sh.index("05b_stragglers") < sh.index("09b_fill_venue")
    assert "fix_stragglers.py --apply || true" in sh
