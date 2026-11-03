"""export_anonymous.py: no names, no Racecast numbers, ids that cannot be
turned back (owner, 2026-11-03)."""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))
os.environ.setdefault("XCP_DB_PASSWORD", "x")
import export_anonymous as E  # noqa: E402


def test_the_columns_carry_nothing_identifying_or_calculated():
    for bad in ("name", "school", "meet", "rating", "normalized", "difficulty", "person_id"):
        assert not any(bad in c for c in E.COLUMNS), bad


def test_ids_are_stable_in_one_export_and_unlinkable_across_two():
    a, b = E.Anonymiser(), E.Anonymiser()
    assert a.num("person", 30049651) == a.num("person", 30049651)
    assert a.num("person", 30049651) != a.num("person", 30049652)
    assert a.num("person", 30049651) != b.num("person", 30049651)
    assert "30049651" not in a.num("person", 30049651)
    assert a.num("person", None) == ""


def test_row():
    anon = E.Anonymiser(key=b"k" * 32)
    r = {"sport": "XC", "person_id": 1, "pool": "hs_f", "race_date": "2026-10-15", "year": 2026,
         "state": "ma", "grade": "11", "meet_id": 5, "div_id": 7, "event_id": None,
         "time_seconds": 1100.456, "distance": 5000.0, "event_kind": None, "mark": None,
         "course_name": "Franklin Park", "speed_rating": 140.0}
    row = E.toRow(r, anon)
    assert set(row) == set(E.COLUMNS)
    assert row["sex"] == "F" and row["level"] == "hs" and row["state"] == "MA"
    assert row["week"] == "2026-10-12"                       # the Monday, not the day
    assert E.toRow(r, anon, exact=True)["week"] == "2026-10-15"
    assert row["event"] == "5000" and row["time_seconds"] == "1100.46"
    assert row["course"] and row["race"] and "Franklin" not in str(row.values())
    assert "140" not in row.values()
    assert E.poolParts("college_m") == ("M", "college")
    assert E.eventOf("TF", 110, "hurdles") == "110m hurdles"
    assert E.eventOf("TF", None, "long_jump") == "long_jump"
