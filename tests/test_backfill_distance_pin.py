"""backfill_normalize reads distance_pin (wrong_distance's writer, 2026-10-26):
a pin outranks the dicts, a veto puts the listed distance back, and one side
of a combined division is pinned by the row's own pool, downward only."""
import os
import sys
import types

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in ("backfill", "engine", "scripts"):
    sys.path.insert(0, os.path.join(_ROOT, _d))
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
os.environ.setdefault("XCP_DB_QUIET", "1")
if "corrections" not in sys.modules:            # 165 MB, not in git
    _c = types.ModuleType("corrections")
    _c.__getattr__ = lambda name: (
        {} if not name.startswith("__") else getattr(object(), name))
    sys.modules["corrections"] = _c
_stub = sys.modules.get("database")
if _stub is not None and not hasattr(_stub, "dbJobs"):
    del sys.modules["database"]
import backfill_normalize as B                                  # noqa: E402
if _stub is not None:
    sys.modules["database"] = _stub


class _Cur:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, sql, *a):
        self.sql = sql

    def fetchone(self):
        return ("distance_pin",)

    def fetchall(self):
        return self.rows


def test_parts_pin_their_own_side_only_and_only_down(monkeypatch):
    monkeypatch.setattr(B, "_DPIN_DIV", {})
    monkeypatch.setattr(B, "_DPIN_PART", {})
    n = B._loadDistancePins(_Cur([
        ("anet", 1, 2, "", "pin", 5000.0), ("tfrrs", 1, 2, "", "veto", None),
        ("anet", 3, 4, "g:f", "pin", 4000.0), ("anet", 3, 4, "lvl:ms", "pin", 3218.0)]))
    assert n == 4
    assert B._DPIN_DIV == {("anet", 1, 2): ("pin", 5000.0), ("tfrrs", 1, 2): ("veto", None)}
    key = ("anet", 3, 4)
    assert B._partPin(key, "hs_f", 5000.0) == 4000.0          # the girls' side
    assert B._partPin(key, "hs_m", 5000.0) == 5000.0          # the boys ran the listed
    assert B._partPin(key, "ms_m|XC", 5000.0) == 3218.0       # by level
    assert B._partPin(key, "hs_f", 3000.0) == 3000.0          # never up
    assert B._partPin(("anet", 9, 9), "hs_f", 5000.0) == 5000.0


def test_the_resolver_takes_the_pin_and_the_veto(monkeypatch):
    monkeypatch.setattr(B, "_DPIN_DIV", {("anet", 1, 2): ("pin", 5000.0),
                                         ("anet", 5, 6): ("veto", None)})
    monkeypatch.setattr(B, "_RESULT_OVERRIDE_BY_SPORT", {"XC": {}})
    monkeypatch.setattr(B, "_DISTANCE_DROP_BY_SPORT", {"XC": set()})
    monkeypatch.setattr(B, "_DISTANCE_OVERRIDES_BY_SPORT", {"XC": {
        (5, 6): 4000.0,                          # an override the times reject
        (1, 2): 4500.0}})                        # outranked by the pin
    monkeypatch.setattr(B, "_anetXcDistance", lambda row, md: (6000.0, None))
    cfg = types.SimpleNamespace(sport="XC", distance_src="meets")
    row = [None] * 64
    row[B._SRC], row[B._ID] = "anet", 99

    row[B._MEET], row[B._DIV] = 1, 2
    assert B._resolveDistanceGender(cfg, row, {}, {})[0] == 5000.0
    row[B._MEET], row[B._DIV] = 5, 6
    assert B._resolveDistanceGender(cfg, row, {}, {})[0] == 6000.0      # vetoed: listed
