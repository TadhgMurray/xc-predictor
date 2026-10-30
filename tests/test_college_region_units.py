"""College regionals vote a region, in the college's own state (owner,
2026-10-30: Wake Forest read "NCAA DI · ACC" with no region).

    python -m pytest -q tests/test_college_region_units.py
"""
import contextlib
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for d in ("scripts", "engine", "racecast"):
    sys.path.insert(0, os.path.join(ROOT, d))
if "database" not in sys.modules:
    _db = types.ModuleType("database")

    @contextlib.contextmanager
    def _noConn():
        yield None
    _db.getConn = _noConn
    sys.modules["database"] = _db

import check_school_units as C                                    # noqa: E402


def test_the_prefilter_lets_college_regionals_through():
    sql = C._champSql("m.meet_name")
    assert "champ|finals|meet of champions" in sql
    assert "'regional'" in sql and "ncaa|naia|njcaa|uscaa|cccaa" in sql
    assert C._collegeUnits("NCAA DI Southeast Regional") == [("division", "NCAA DI"),
                                                            ("region", "SOUTHEAST")]


def test_a_college_votes_in_its_own_state(monkeypatch):
    rows = [("Wake Forest", "2025", "NCAA DI Southeast Regional", None, "KY", "tfrrs", 1.0),
            ("Wake Forest", "2025", "ACC Cross Country Championships", None, "VA", "tfrrs", 1.0),
            ("Wake Forest", "2025", "NCHSAA 4A Championships", None, "NC", "anet", 0.0)]
    monkeypatch.setattr(C, "_rows", lambda cur, sport, lo, hi: iter(rows))
    monkeypatch.setattr(C, "_levelMaps", lambda cur: {})
    import school_identity as S
    monkeypatch.setattr(S, "teamState", lambda school, pool, st: "NC" if pool.startswith("college") else st)
    votes, *_ = C.buildVotes(None, "XC", split_level=True)
    coll = votes[("Wake Forest", "NC", True)]
    assert ("SOUTHEAST", "2025") in coll["region"] and ("ACC", "2025") in coll["conference"]
    assert ("Wake Forest", "KY", True) not in votes and ("Wake Forest", "VA", True) not in votes
    assert ("Wake Forest", "NC", False) in votes          # the high school stays its own row
