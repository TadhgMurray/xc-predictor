"""speed_ratings_db._TWIN_AHEAD (2026-11-19): the twin keys built on a thread
ahead of the stream are taken once, a failed build is rebuilt in line, and
XCP_PACK_TWIN_AHEAD=0 builds nothing ahead."""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
D = pytest.importorskip("speed_ratings_db")


def test_twin_keys_ahead(monkeypatch):
    calls = []

    def build(table, sport):
        calls.append((table, sport))
        if sport == "XC" and len(calls) <= 2:
            raise RuntimeError("boom")
        return f"sr_twins_{sport.lower()}", len(calls)

    monkeypatch.setattr(D, "_buildTwinKeys", build)
    monkeypatch.setattr(D, "_TWIN_AHEAD", {})
    D.buildTwinKeysAhead(("XC", "TF"))
    D.joinTwinKeysAhead()
    assert sorted(calls) == [("results", "XC"), ("results_tf", "TF")]
    got_tf = D._twinKeysFor("results_tf", "TF")
    assert got_tf[0] == "sr_twins_tf" and len(calls) == 2      # taken, not rebuilt
    assert D._twinKeysFor("results", "XC") == ("sr_twins_xc", 3)  # failed -> rebuilt
    assert D._twinKeysFor("results_tf", "TF") == ("sr_twins_tf", 4)  # taken once
    monkeypatch.setenv("XCP_PACK_TWIN_AHEAD", "0")
    D.buildTwinKeysAhead(("TF",))
    assert D._TWIN_AHEAD == {} and len(calls) == 4
