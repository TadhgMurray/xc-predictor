"""A virtual meet's races get no normalized time (owner, 2026-10-19: "covid
inflation" -- Nike Cross Virtual 2020, self-timed anywhere, filed under Alton
Baker Park and credited its +5.5%: a 17:13 rated 149.1 for a runner whose
real seasons read ~115). As WHEELCHAIR: no value for anything to price."""
import os
import re
import sys
import types

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
os.environ.setdefault("XCP_DB_QUIET", "1")
if "corrections" not in sys.modules:            # 165 MB, not in git
    _c = types.ModuleType("corrections")
    _c.__getattr__ = lambda name: (
        {} if not name.startswith("__") else getattr(object(), name))
    sys.modules["corrections"] = _c
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in ("backfill", "engine", "scripts"):
    sys.path.insert(0, os.path.join(_ROOT, _p))

import pytest                                                     # noqa: E402
import backfill_normalize as B                                    # noqa: E402


def _row(rid, src, meet, div):
    r = [None] * 13
    r[B._ID], r[B._SRC], r[B._MEET], r[B._DIV] = rid, src, meet, div
    r[B._TIME], r[B._PERSON], r[B._CANON] = 1033.0, 7, None
    r[B._AID], r[B._DATE] = 5, "2020-11-08"
    return tuple(r)


def test_a_virtual_meet_is_withheld_and_a_real_one_is_not(monkeypatch):
    monkeypatch.setattr(B, "_distanceSane", lambda _d: False)   # past the gate: INSANE
    monkeypatch.setattr(B, "_makeTrace", lambda *a, **k: None)
    for name in ("_RESULT_DROP_BY_SPORT", "_GENDER_OVERRIDES_BY_SPORT",
                 "_RESULT_OVERRIDE_BY_SPORT", "_DISTANCE_DROP_BY_SPORT",
                 "_DISTANCE_OVERRIDES_BY_SPORT"):
        monkeypatch.setattr(B, name, {"XC": {}, "TF": {}}, raising=False)
    cfg = B._configFor("XC", "write") if hasattr(B, "_configFor") else None
    if cfg is None:
        pytest.skip("no XC config")
    fn = B._makeRowFn(cfg, None, {}, ({}, {}, {}), {900: 5000.0}, {},
                      set(), {}, None, virtual_meets={("anet", 190001)})
    assert fn(_row(1, "anet", 190001, 900))[1:3] == (None, B._SkipReason.VIRTUAL)
    assert fn(_row(2, "anet", 190002, 900))[2] != B._SkipReason.VIRTUAL
    assert fn(_row(3, "tfrrs", 190001, 900))[2] != B._SkipReason.VIRTUAL   # other feed's id
    assert B._SkipReason.VIRTUAL in B._SKIP_ORDER


def test_the_name_rule_is_the_word():
    rx = re.compile(B._VIRTUAL_RX.replace(r"\m", r"\b").replace(r"\M", r"\b"), re.I)
    for name in ("Nike Cross Virtual Week 7", "Virtual Time Trial", "XC VIRTUAL challenge"):
        assert rx.search(name), name
    for name in ("Virtually Anything Invitational", "Virtus Classic", "Arcadia Invitational"):
        assert not rx.search(name), name


def _fn(monkeypatch, **kw):
    monkeypatch.setattr(B, "_makeTrace", lambda *a, **k: None)
    for name in ("_RESULT_DROP_BY_SPORT", "_GENDER_OVERRIDES_BY_SPORT",
                 "_RESULT_OVERRIDE_BY_SPORT", "_DISTANCE_DROP_BY_SPORT",
                 "_DISTANCE_OVERRIDES_BY_SPORT"):
        monkeypatch.setattr(B, name, {"XC": {}, "TF": {}}, raising=False)
    cfg = B._configFor("XC", "write") if hasattr(B, "_configFor") else None
    if cfg is None:
        pytest.skip("no XC config")
    return B._makeRowFn(cfg, None, {}, ({}, {}, {}), kw.pop("meet_distances", {900: 5000.0}),
                        {}, set(), kw.pop("canon_distances", {}), None, **kw)


def test_a_repost_of_a_withheld_time_is_withheld_with_it(monkeypatch):
    # owner, 2026-10-24: Ridgefield's 15:03 track time trial, reposted three
    # days later in a weekly "Ridgefield 5K" sheet and rated 147.0
    monkeypatch.setattr(B, "_distanceSane", lambda _d: False)
    fn = _fn(monkeypatch, virtual_meets={("anet", 185494), ("result", 35351051)})
    assert fn(_row(35351051, "anet", 185388, 900))[2] == B._SkipReason.VIRTUAL
    assert fn(_row(35243793, "anet", 185069, 900))[2] != B._SkipReason.VIRTUAL


def test_a_track_time_trial_filed_as_cross_country_is_named():
    rx = re.compile(B._TRACK_IN_XC_RX.replace(r"\m", r"\b").replace(r"\M", r"\b"), re.I)
    for name in ("Ridgefield 5K Track TT", "Varsity Track Time Trial", "5K time trial on the track"):
        assert rx.search(name), name
    for name in ("Hawks Track Club XC Invitational", "Time Trial", "Trackside Invitational",
                 "Ridgefield 5K"):
        assert not rx.search(name), name


def test_a_borrowed_college_distance_is_lent_to_college_rows_only(monkeypatch):
    # owner, 2026-10-24: Dan Conway Classic 2021, an hs_m 16:34 with no
    # distance of its own took the college race's and rated 156.9
    monkeypatch.setattr(B, "_rawTimePlausible", lambda *a: False)   # past the borrow rule: INSANE_RAW
    seen = {}
    for pool in ("hs_m", "college_m"):
        monkeypatch.setattr(B, "_seasonPool", lambda *a, _p=pool: _p)
        fn = _fn(monkeypatch, meet_distances={}, canon_distances={(55, "M"): 6000.0})
        r = list(_row(1, "anet", 196059, 787032))
        r[B._CANON] = 55
        seen[pool] = fn(tuple(r))[2]
    assert seen["hs_m"] == B._SkipReason.NO_DISTANCE
    assert seen["college_m"] != B._SkipReason.NO_DISTANCE
