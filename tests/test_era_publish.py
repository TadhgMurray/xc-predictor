# Project: xc-predictor / tests
# File:    test_era_publish.py
# Purpose: With --era-years the solve keys cells '<key>@e<k>'. The page looks
#          a venue up by its BARE key, so the go-live publishes each venue's
#          latest solved era under it and the venue-key splitter drops the
#          suffix (2026-09-11, the owner: "are we currently doing the every
#          couple years split? I think that could help").
#
#   python -m pytest -q tests/test_era_publish.py
import os
import sys

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import joint_golive as jg                                      # noqa: E402

# the key splitter lives in the pack module, which imports the database
# driver at import time; no database here
try:
    import speed_ratings_db as sdb                             # noqa: E402
except ImportError:                                            # no psycopg2
    import types
    from unittest import mock
    _pg = mock.MagicMock()
    _pg.__path__ = []                                          # a package
    sys.modules.setdefault("psycopg2", _pg)
    for _sub in ("errors", "extras", "extensions", "sql", "pool"):
        sys.modules.setdefault(f"psycopg2.{_sub}", mock.MagicMock())
    sys.modules.setdefault("database", types.SimpleNamespace(getConn=None))
    import speed_ratings_db as sdb                             # noqa: E402


def test_the_latest_solved_era_publishes_under_the_bare_key():
    keys = ["XC:Ultimook:d5000@e0", "XC:Ultimook:d5000@e1", "XC:Ultimook:d5000@e2",
            "TF:loc:7:in@e0", "TF:loc:7:in@e1", "XC:Balboa:d5000", "TF:loc:9:out"]
    solved = np.array([True, True, False, True, True, True, False])
    bare, pub = jg.latestEraKeys(keys, solved)
    assert bare == ["XC:Ultimook:d5000"] * 3 + ["TF:loc:7:in"] * 2 + ["XC:Balboa:d5000", "TF:loc:9:out"]
    # Ultimook's latest SOLVED era is e1 (e2 is unsolved); the oval's is e1;
    # the un-split cells publish iff solved
    assert pub.tolist() == [False, True, False, False, True, True, False]
    # no eras at all: the mask is just `solved`
    bare2, pub2 = jg.latestEraKeys(["XC:A:d5000", "TF:loc:1:out"], np.array([True, False]))
    assert bare2 == ["XC:A:d5000", "TF:loc:1:out"] and pub2.tolist() == [True, False]


def test_the_venue_key_splitter_drops_the_era_suffix():
    assert sdb._splitVenueKey("TF:loc:7:in@e3", {}) == ("TF:loc:7:in", None, None)
    assert sdb._splitVenueKey("XC:123:d5000@e2", {"123": "Ultimook"}) == ("XC:Ultimook", 123, 5000)
    assert sdb._splitVenueKey("XC:123:d5000", {"123": "Ultimook"}) == ("XC:Ultimook", 123, 5000)


def test_the_era_index_grows_with_the_year():
    import run_joint as rj
    course = np.array([0, 0, 0, 1, -1])
    year = np.array([2016, 2019, 2024, 2024, 2024])
    new, n_new, group, keys, pairs, w, per_base = rj.eraCells(
        course, year, 2, 2, np.array([0, 0]), ["XC:A:d5000", "XC:B:d5000"])
    assert keys[new[0]] == "XC:A:d5000@e0"
    assert keys[new[2]] == "XC:A:d5000@e4"          # (2024 - 2016) // 2
    assert int(keys[new[2]].rpartition("@e")[2]) > int(keys[new[1]].rpartition("@e")[2])
