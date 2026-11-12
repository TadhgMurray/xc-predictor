"""Prediction history is shared per runner (predict._historyShared,
2026-11-12): a second batch queries only the runners the first did not
read, returns the same rows (legacy rows keyed by athlete_id included), and
a cached row edited by a caller does not change the next caller's."""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "scripts"), os.path.dirname(os.path.abspath(__file__))):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import _env  # noqa: E402,F401

import predict as P  # noqa: E402


def _rows(ids):
    out = []
    for p in ids:
        for k in range(3 if p != 5 else 0):              # runner 5 has no history
            legacy = p == 7
            out.append({"person_id": None if legacy else p, "athlete_id": p,
                        "date": f"2025-09-{10 + k:02d}", "normalized_time": 900.0 + k})
    return out


def test_runners_read_once(tmp_path, monkeypatch):
    import sharedcache
    monkeypatch.setattr(sharedcache, "DIR", str(tmp_path / "shared"))
    monkeypatch.setattr(sharedcache, "tableOid", lambda cur, t, now=None: 11)
    # the feature module needs sklearn; only its floor constant is read here
    monkeypatch.setattr(P, "_fx", lambda: type("FX", (), {"MIN_NORMALIZED_TIME": 120.0}))
    asked = []

    def read(c, sport, hour, some):
        asked.append(sorted(some))
        return _rows(some)
    a = P._historyShared(None, "XC", 15, [1, 2, 5, 7], True, "pool", read)
    a[0]["normalized_time"] = -1                          # a caller's edit
    b = P._historyShared(None, "XC", 15, [2, 3, 5, 7], True, "pool", read)
    assert asked == [[1, 2, 5, 7], [3]]
    key = lambda r: (r["athlete_id"], r["date"])         # noqa: E731
    assert sorted(b, key=key) == sorted(_rows([2, 3, 5, 7]), key=key)
    # another sport, scale or hour is its own copy
    P._historyShared(None, "TF", 15, [2], True, "pool", read)
    P._historyShared(None, "XC", 15, [2], True, "common5000", read)
    assert asked[2:] == [[2], [2]]
