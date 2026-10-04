"""Breakouts this week (racecast/breakouts.py, owner 2026-10-04).

The definitions the page prints, pinned:
  * a breakout is a rating above the MEDIAN of the athlete's earlier rated
    races this season, and needs MIN_PRIOR of them -- a season opener is
    never one;
  * a new PR beats every earlier time at a standard distance and needs an
    earlier time -- a debut at a distance is not listed;
  * one line per athlete, their biggest; a state filter is a slice.
And the per-key cache under both pages (racecast/ttlcache.py)."""
import datetime
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "scripts"), os.path.dirname(os.path.abspath(__file__))):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import _env  # noqa: E402,F401  -- sets XCP_DB_PASSWORD, must precede config

import breakouts as B  # noqa: E402
import ttlcache  # noqa: E402


def test_breakout_is_over_the_median_not_the_best():
    # earlier races 100, 104, 118 (one great day): the median is 104
    b = B.breakoutOf(110.0, [100.0, 104.0, 118.0])
    assert b["base"] == 104.0
    assert b["jump"] == 6.0
    assert b["prev_best"] == 118.0 and b["beat_best"] is False
    assert b["n_prior"] == 3
    # even count: the middle two averaged
    assert B.breakoutOf(110.0, [100.0, 102.0, 104.0, 106.0])["base"] == 103.0
    # above everything before it
    assert B.breakoutOf(120.0, [100.0, 104.0, 118.0])["beat_best"] is True


def test_breakout_needs_enough_earlier_races():
    assert B.breakoutOf(130.0, []) is None              # a season opener
    assert B.breakoutOf(130.0, [100.0]) is None          # a jump over one draw
    assert B.MIN_PRIOR == 2
    assert B.breakoutOf(130.0, [100.0, 101.0]) is not None
    assert B.breakoutOf(None, [100.0, 101.0]) is None
    # unrated earlier rows do not count toward the minimum
    assert B.breakoutOf(130.0, [100.0, None]) is None


def test_standard_distances_use_the_race_pages_band():
    assert B.standardDistance(5000, "XC") == 5000
    assert B.standardDistance(5010, "XC") == 5000        # a remeasure, 0.2%
    assert B.standardDistance(5050, "XC") is None        # 1% off: not a 5K
    assert B.standardDistance(4828, "XC") == 4828        # three miles
    assert B.standardDistance(1609, "TF") == 1609
    assert B.standardDistance(1600, "TF") == 1600        # a different race
    assert B.standardDistance(None, "XC") is None
    assert B.DIST_TOL == 0.0025


def test_pr_beats_every_earlier_time_and_needs_one():
    pr = B.prOf(950.0, [1000.0, 980.0, 1010.0])
    assert pr["prev_best"] == 980.0
    assert abs(pr["gain"] - 30.0 / 980.0) < 1e-12
    assert pr["n_prev"] == 3
    assert B.prOf(980.0, [1000.0, 980.0]) is None        # equal is not a PR
    assert B.prOf(990.0, [1000.0, 980.0]) is None
    assert B.prOf(900.0, []) is None                     # a debut: not listed
    assert B.prOf(900.0, [0, None]) is None              # no real earlier time


def _row(pid, v, state="CA"):
    return {"person_id": pid, "jump": v, "state": state}


def test_one_line_per_athlete_and_the_state_slice():
    rows = [_row(1, 5.0), _row(1, 9.0), _row(2, 7.0, "TX"), _row(3, 3.0)]
    folded = B.foldPerPerson(rows, "jump")
    assert [(r["person_id"], r["jump"]) for r in folded] == [(1, 9.0), (2, 7.0),
                                                             (3, 3.0)]
    assert [r["person_id"] for r in B.pickRows(rows, "jump", "CA")] == [1, 3]
    assert [r["person_id"] for r in B.pickRows(rows, "jump", None, n_nat=2)] == [1, 2]


def test_default_sport_follows_the_newest_meet():
    assert B.defaultSport({"XC": "2026-10-03", "TF": "2026-06-10"}) == "XC"
    assert B.defaultSport({"XC": "2025-11-20", "TF": "2026-04-10"}) == "TF"
    assert B.defaultSport({}, today=datetime.date(2026, 10, 4)) == "XC"
    assert B.defaultSport({}, today=datetime.date(2026, 4, 4)) == "TF"


def test_race_links():
    assert B.raceHref("XC", {"meet_id": 7, "div_id": 9}) == "/race/xc/7/9"
    assert B.raceHref("TF", {"meet_id": 7, "div_id": 9, "event_id": 3}) == \
        "/race/tf/7/3/9"
    assert B.raceHref("XC", {"meet_id": None, "div_id": 9}) is None


def test_ttlcache_holds_recomputes_and_never_caches_a_failure():
    ttlcache.clear()
    clock = [1000.0]
    calls = []

    def compute():
        calls.append(1)
        return len(calls)

    now = lambda: clock[0]                                    # noqa: E731
    assert ttlcache.get("k", compute, ttl=60, now=now) == (1, 1000.0)
    clock[0] += 30
    assert ttlcache.get("k", compute, ttl=60, now=now)[0] == 1   # held
    clock[0] += 31
    assert ttlcache.get("k", compute, ttl=60, now=now)[0] == 2   # expired

    def boom():
        raise RuntimeError("db blip")
    try:
        ttlcache.get("bad", boom, now=now)
    except RuntimeError:
        pass
    assert ttlcache.get("bad", compute, now=now)[0] == 3       # not cached

    # ttl_of: a half-failed value is kept briefly
    ttlcache.get("half", lambda: {"err": True}, ttl=3600, now=now,
                 ttl_of=lambda v: 5 if v["err"] else 3600)
    clock[0] += 6
    assert ttlcache.get("half", lambda: {"err": False}, now=now)[0] == {"err": False}
    ttlcache.clear()
