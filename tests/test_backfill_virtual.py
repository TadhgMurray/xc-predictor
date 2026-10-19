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
