"""One time in one race, one public rating whatever the pool (owner,
2026-11-07, Lutkenhaus's two 1:42s)."""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))
import pool_equal_time as E  # noqa: E402


def test_pairs_of_two_pools_at_equal_time():
    rows = [("hs_m", 150.0, 102.27), ("pro_m", 145.0, 102.30),   # one performance
            ("pro_m", 140.0, 106.0), ("hs_m", 130.0, 110.0)]    # nobody near them
    got = E.gaps(rows)
    assert len(got) == 1
    a, b, g = got[0]
    assert (a, b) == ("hs_m", "pro_m") and abs(g - 100 * __import__("math").log(150 / 145)) < 1e-9


def test_one_pool_alone_gives_nothing():
    assert E.gaps([("hs_m", 150.0, 100.0), ("hs_m", 149.0, 100.2)]) == []


def test_bands():
    assert E.band("TF", 800) == "800" and E.band("TF", 1609) == "1500/mile"
    assert E.band("XC", 8000) == "XC 8-10k" and E.band("TF", 400) is None
