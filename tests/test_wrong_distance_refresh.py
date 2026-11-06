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


def test_the_breaker_writes_the_strongest_and_holds_the_rest():
    """owner, 2026-11-06: America East and Lou Regions judged 'pin, high',
    never written, because the breaker wrote nothing at all."""
    def v(points, direction="down", n=20):
        return {"points": points, "direction": direction, "div": {"n_ref": n}}
    rows = {("anet", 9, 91, ""): _row("pin", 4000.0)}
    upserts = [(("anet", 9, 91, ""), "pin", 3218.0, v(1)),          # a change: always kept
               (("anet", 1, 11, ""), "pin", 5000.0, v(5, n=88)),    # America East
               (("anet", 2, 21, ""), "novote", None, v(5, "up")),
               (("anet", 3, 31, ""), "pin", 3218.0, v(2)),
               (("anet", 4, 41, ""), "pin", 3218.0, v(5, n=28))]    # Lou Regions
    retires = [(("anet", 8, 81, ""), "ran true")] * 3
    keep, ret, held = W.trimToBreaker(upserts, retires, rows, cap=2)
    assert [u[0][1] for u in keep] == [9, 1, 4]
    assert ret == [] and held == 2 + 3
    keep, ret, held = W.trimToBreaker(upserts, retires[:1], rows, cap=2)
    assert len(ret) == 1 and held == 2


def test_a_key_is_written_once():
    """2026-11-06: a runner twice in a division's rows gave its part twice and
    the upsert failed ('cannot affect row a second time')."""
    v = {"div": {"source": "anet", "meet_id": 1, "div_id": 2, "listed": 7000.0},
         "action": "split", "kind": "combined", "proposed": None, "x": -0.3,
         "confidence": "medium", "points": 3, "evidence": "e",
         "mix": {"by": None, "fast_ids": [5, 5, 6],
                 "clusters": [{"name": "fast", "snap": 5000.0, "implied": 5010.0},
                              {"name": "slow", "snap": 7000.0, "implied": 7000.0}]}}
    up, _ret, _ref = W.plan([v, v], {})
    keys = [u[0] for u in up]
    assert len(keys) == len(set(keys))
