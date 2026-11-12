"""Race entrants are shared per school (predict._raceEntrantRowsShared,
2026-11-12): a second field reads only the schools the first did not, and
gets the same rows as one query over its whole list."""
import os
import random
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "scripts"), os.path.dirname(os.path.abspath(__file__))):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import _env  # noqa: E402,F401

import predict as P  # noqa: E402


def _rows_for(schools, n=150):
    out = []
    for sc in schools:
        rnd = random.Random(sc)
        for k in range(n if sc != "Empty" else 0):
            out.append({"school": sc, "person_id": hash((sc, k)) & 0xFFFFFF, "grade": "11",
                        "pool": "hs_m", "rating": rnd.uniform(100, 180), "n_races": 4,
                        "gender": "M", "pg_split": False, "pg_gender": None, "name": f"R {k}"})
    return out


def test_a_second_field_reads_only_new_schools(tmp_path, monkeypatch):
    import sharedcache
    monkeypatch.setattr(sharedcache, "DIR", str(tmp_path / "shared"))
    asked = []

    def fake(cur, schools, sport, year):
        asked.append(sorted(schools))
        return _rows_for(schools)
    monkeypatch.setattr(P, "_raceEntrantRows", fake)
    boys = ("Alpha", "Bravo", "Charlie", "Empty")
    girls = ("Bravo", "Charlie", "Delta", "Empty")
    a = P._raceEntrantRowsShared(None, (boys, "XC", 2025))
    b = P._raceEntrantRowsShared(None, (girls, "XC", 2025))
    assert asked == [sorted(boys), ["Delta"]]          # Empty is known to be empty
    key = lambda r: (r["school"], r["person_id"])       # noqa: E731
    assert sorted(a, key=key) == sorted(_rows_for(boys), key=key)
    assert sorted(b, key=key) == sorted(_rows_for(girls), key=key)


def test_a_cached_forty_school_field_reads_quickly(tmp_path, monkeypatch):
    import sharedcache
    monkeypatch.setattr(sharedcache, "DIR", str(tmp_path / "shared"))
    monkeypatch.setattr(P, "_raceEntrantRows", lambda cur, s, sp, y: _rows_for(s))
    schools = tuple(f"School {i}" for i in range(40))
    P._raceEntrantRowsShared(None, (schools, "XC", 2025))
    # other fields filed alongside, as on a busy afternoon
    for k in range(20):
        P._raceEntrantRowsShared(None, (tuple(f"Other {k}-{i}" for i in range(40)), "XC", 2025))
    t = time.perf_counter()
    rows = P._raceEntrantRowsShared(None, (schools, "XC", 2025))
    took = time.perf_counter() - t
    assert len(rows) == 40 * 150
    assert took < 0.25, took
