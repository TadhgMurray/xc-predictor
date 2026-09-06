"""The meets page filters (2026-09-06): kind, level, date range, sport."""
import os
import sys

_ROOT = os.path.join(os.path.dirname(__file__), "..")
for d in ("racecast", "scripts", "engine"):
    sys.path.insert(0, os.path.join(_ROOT, d))
os.environ.setdefault("XCP_DB_PASSWORD", "x")

import meets_filter as mf                                        # noqa: E402


def test_parse_accepts_the_new_filters_and_refuses_junk():
    f = mf.parseFilters({"sport": "XC", "state": "ca", "kind": "section", "unit": "ncs",
                         "level": "hs", "from": "2025-09-01", "to": "2025-11-30"})
    assert (f["kind"], f["level"], f["from"], f["to"]) == ("section", "hs", "2025-09-01", "2025-11-30")
    assert f["champ"] and f["active"]
    junk = mf.parseFilters({"sport": "XC", "kind": "DROP TABLE", "level": "pro", "from": "nope"})
    assert not junk["active"] and junk["kind"] == "" and junk["level"] == "" and junk["from"] == ""


def test_describe_reads_as_a_sentence():
    f = mf.parseFilters({"sport": "XC", "state": "CA", "kind": "section", "unit": "NCS",
                         "level": "hs", "from": "2025-09-01", "to": "2025-11-30"})
    assert mf.describe(f) == "NCS section championships in CA high school from 2025-09-01 to 2025-11-30"
    assert mf.describe(mf.parseFilters({"sport": "TF", "level": "college", "from": "2026-01-01"})) \
        == "college since 2026-01-01"


def test_level_clause_shapes():
    class Cur:
        connection = type("C", (), {"rollback": lambda self: None})()
        def execute(self, *a): pass
        def fetchone(self): return (1,)
    mf._LEVEL_COL.clear()
    params = {}
    assert "level_mask & %(level_bit)s" in mf.levelClause(Cur(), "XC", "m", params, "hs")
    assert params["level_bit"] == 4 and "NOT EXISTS (SELECT 1 FROM meets_tfrrs" in \
        mf.levelClause(Cur(), "XC", "m", params, "hs")
    college = mf.levelClause(Cur(), "TF", "m", params, "college")
    assert "meets_tf_meta" in college and "meets_tfrrs" in college and " OR " in college
    # no mask column: hs cannot be told, college still can
    mf._LEVEL_COL.clear()
    class NoCol(Cur):
        def fetchone(self): return None
    assert mf.levelClause(NoCol(), "XC", "m", {}, "hs") == ""
    assert "meets_tfrrs" in mf.levelClause(NoCol(), "XC", "m", {}, "college")


def test_unit_kind_narrows_the_exists():
    params = {}
    clause, _cols = mf.unitSql({"sport": "XC", "champ": True, "unit": "NCS", "kind": "section"},
                               "m", params, present=True)
    assert "u.unit = %(unit)s" in clause and "u.kind = %(kind)s" in clause
    assert params["kind"] == "section"


def test_events_group_by_event_prelims_first():
    from tf_points import eventSortKey
    names = ["Men's 3000 Meters", "Men's 200 Meters Finals", "Men's 200 Meters Preliminaries",
             "Men's Mile Finals", "Men's Mile Preliminaries", "Men's 800 Meters Semifinals"]
    assert sorted(names, key=eventSortKey) == [
        "Men's 200 Meters Preliminaries", "Men's 200 Meters Finals", "Men's 3000 Meters",
        "Men's 800 Meters Semifinals", "Men's Mile Preliminaries", "Men's Mile Finals"]
