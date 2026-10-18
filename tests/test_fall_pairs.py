"""diag_fall_pairs: buckets and the summary are what the report prints.

    python -m pytest -q tests/test_fall_pairs.py
"""
import math
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))

import diag_fall_pairs as D                                      # noqa: E402


def test_buckets():
    assert D.bucket(800) == "800"
    assert D.bucket(1609.34) == "1500/1600/mile"
    assert D.bucket(3218.69) == "3000/3200/2 mile"
    assert D.bucket(400) is None


def test_summary_median():
    rows = [("a", math.log(1.03))] * 5 + [("b", 0.0)] * 3
    got = {k: (n, med) for k, n, med, _lo, _hi in D.summarise(rows)}
    assert got["a"][0] == 5 and abs(got["a"][1] - 100 * math.log(1.03)) < 1e-9
    assert got["b"] == (3, 0.0)
