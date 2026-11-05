"""wrong_distance --refresh: the divisions a write changes are re-priced by
the nightly (owner, 2026-11-05: Lou Regions Splits, pinned only on Sundays)."""
import os
import sys

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in ("engine", "scripts"):
    sys.path.insert(0, os.path.join(_ROOT, p))
import wrong_distance as W                                      # noqa: E402


def _row(action, dist, status="active"):
    return {"action": action, "distance": dist, "status": status}


def test_new_moved_and_retired_pins_change_and_a_confirmed_one_does_not():
    rows = {("anet", 1, 11, ""): _row("pin", 3218.0),
            ("anet", 2, 21, ""): _row("pin", 4000.0),
            ("anet", 3, 31, ""): _row("pin", 3000.0, "retired"),
            ("anet", 4, 41, ""): _row("pin", 3218.0)}
    upserts = [(("anet", 1, 11, ""), "pin", 3218.0, {}),         # confirmed: no change
               (("anet", 2, 21, ""), "pin", 3218.0, {}),         # moved
               (("anet", 3, 31, ""), "pin", 3000.0, {}),         # re-activated
               (("anet", 289197, 3000002938985, ""), "pin", 3218.0, {})]   # new
    retires = [(("anet", 4, 41, ""), "ran true")]
    assert W.changedKeys(upserts, retires, rows) == sorted([
        ("anet", 2, 21), ("anet", 289197, 3000002938985), ("anet", 3, 31), ("anet", 4, 41)])


def test_the_nightly_runs_it_before_normalising():
    sh = open(os.path.join(_ROOT, "deploy", "nightly_update.sh")).read()
    i = sh.index("wrong_distance.py --write --refresh")
    assert i < sh.index("step 05_normalize_new") and i > sh.index("06b_course_canonical")


def test_only_rows_the_nightly_renormalises_are_cleared():
    import datetime
    assert W.refreshSince(datetime.date(2026, 11, 5)) == "2025-08-15"
    assert W.refreshSince(datetime.date(2026, 3, 1)) == "2024-08-15"
    assert "r.date >= %(since)s" in W._REFRESH_SQL
