# Project: xc-predictor / tests
# File:    test_board_sanity.py
# Purpose: the board rails of 2026-09-14 -- a row is ranked in the pool that
#          rated it, professional pools have no board, a pace faster than the
#          record is a wrong distance, a club is a club by its rows -- and
#          the sanity script's pure checks over them. No database.
#
#   XCP_DB_PASSWORD=x python -m pytest -q tests/test_board_sanity.py
import os
import sys

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "engine"), os.path.join(_ROOT, "scripts"),
           os.path.join(_ROOT, "racecast")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import build_ranking_results as brr                            # noqa: E402
import board_sanity as bs                                      # noqa: E402
import speed_ratings_db as sdb                                 # noqa: E402
import speed_ratings as sr                                     # noqa: E402
import normalize_distance as nd                                # noqa: E402


# ---- the builder's rails ------------------------------------------------ #

def test_professional_pools_are_not_boards():
    assert not brr.isRankablePool("pro_m")
    assert not brr.isRankablePool("pro_f|TF")
    assert not brr.isRankablePool("hs_unknown_gender")
    assert not brr.isRankablePool(None)
    assert brr.isRankablePool("college_f") and brr.isRankablePool("hs_m|XC")


def test_record_pace_interpolates_in_log_distance_and_holds_flat_beyond():
    p800 = brr.recordPace(800, "M"); p1500 = brr.recordPace(1500, "M")
    assert p800 < p1500 < brr.recordPace(5000, "M") < brr.recordPace(10000, "M")
    # ⚠ THIS USED TO ASSERT recordPace(400) == p800, WHICH PINNED THE BUG.
    #   The table began at 800 m and recordPace clamps below its first
    #   point, so every sprint was judged against the 800 m record pace --
    #   12.6 s for 100 m, which made every real sprint "impossible" and
    #   every impossible one indistinguishable from a real one (owner,
    #   2026-09-14: wrong sprint times on the best-times board). The table
    #   reaches 55 m now, so a 400 has its own record and it is faster.
    assert brr.recordPace(400, "M") < p800
    # and the curve FALLS then RISES: 100 m is the fastest pace ever run
    assert brr.recordPace(100, "M") < brr.recordPace(400, "M")
    assert brr.recordPace(100, "M") < brr.recordPace(60, "M")
    assert brr.recordPace(42195, "M") == brr.recordPace(10000, "M")
    # below the first point it still clamps -- to the 55 m record now
    assert brr.recordPace(40, "M") == brr.recordPace(55, "M")
    assert brr.recordPace(3000, "F") > brr.recordPace(3000, "M")


def test_a_row_faster_than_the_record_is_impossible_and_a_real_one_is_not():
    # a 12:18 "5k" by a college woman: 148 s/km against a 168 s/km record
    assert brr.impossiblePace(738.0, 5000, "F")
    # a 14:30 5k is fast and real
    assert not brr.impossiblePace(870.0, 5000, "F")
    # a 3:43.13 mile is exactly the record: within the slack, allowed
    assert not brr.impossiblePace(223.13, 1609.34, "M")
    # a 5:12 mile is slow, whatever the board said about it
    assert not brr.impossiblePace(312.0, 1609.34, "F")
    # unanswerable is not a finding
    assert not brr.impossiblePace(None, 5000, "M")
    assert not brr.impossiblePace(300.0, None, "M")
    assert not brr.impossiblePace(0.0, 5000, "M")


def test_the_ranking_query_carries_the_rating_pool_only_when_the_table_has_it():
    class Cur:
        def __init__(self, have): self.have = have; self.rows = []
        def execute(self, sql, params=None):
            if "to_regclass" in sql:                 # the impossible-race probe
                self.rows = [("impossible_result" if self.have else None,)]
            else:
                self.rows = [(1,)] if self.have else []
        def fetchone(self): return self.rows[0] if self.rows else None
        def __enter__(self): return self
        def __exit__(self, *a): return False
    class Conn:
        def __init__(self, have): self.have = have
        def cursor(self): return Cur(self.have)
    for sport in ("XC", "TF"):
        assert "__RATING_POOL__" in brr._SQL[sport]
        with_col = brr._sourceSql(Conn(True), sport)
        without = brr._sourceSql(Conn(False), sport)
        assert "r.rating_pool AS rating_pool" in with_col
        assert "NULL::text AS rating_pool" in without
        assert "__RATING_POOL__" not in with_col + without


# ---- a club by its name -------------------------------------------------- #

def test_club_names_are_not_schools_colleges_or_unattached():
    assert sdb.isClubName("Nike Swoosh TC")
    assert sdb.isClubName("ASICS Furman Elite")
    assert sdb.isClubName("Nomad Intl Elite")
    assert not sdb.isClubName("Furman University")
    assert not sdb.isClubName("Furman", colleges={"furman"})
    assert not sdb.isClubName("Unattached")
    assert not sdb.isClubName("Unattached - Oregon")
    assert not sdb.isClubName("Loyola Blakefield High School")
    assert not sdb.isClubName("A.I. Root Middle")
    assert not sdb.isClubName("")
    assert not sdb.isClubName(None)


# ---- the pack's scale identification ------------------------------------- #

_F = {"hs_m": 3.4, "hs_f": 3.5, "college_m": 5.474, "college_f": 4.1, "pro_f": 4.4}


def _fake(t, d, pool, sport=None, **_k):
    f = _F.get(pool)
    return None if f is None else round(t * f, 2)


def test_two_near_factors_of_different_size_are_ambiguous_and_left_alone(monkeypatch):
    monkeypatch.setattr(nd, "normalizeTime", _fake)
    sr._scaleFactorCache.clear()
    t, d = 300.0, 1609.34
    # college_f 4.1 and pro_f 4.4 differ by 7%; a value between them is
    # either one with a 3-4% correction -- not identified, not moved
    stored = t * 4.25
    new, tag = sr.rescaleToPool(stored, t, d, "hs_m", "TF")
    assert tag == "scale_ambiguous" and new == stored
    # hs_m 3.4 and hs_f 3.5 are one size: the nearer one is taken
    stored = t * 3.45
    new, tag = sr.rescaleToPool(stored, t, d, "college_m", "TF")
    assert tag == "rescaled"


# ---- the sanity script's pure checks -------------------------------------- #

def _row(**kw):
    base = {"pool": "college_f", "rating_pool": "college_f", "time_seconds": 900.0,
            "distance": 5000.0, "normalized_time": None, "speed_rating": 120.0,
            "school": "Furman University", "team_id": None, "gender": "F",
            "rk": 1, "result_id": 1, "person_id": 1, "race_date": "2026-05-01"}
    base.update(kw)
    return base


def test_a_clean_row_has_no_findings():
    assert bs.checkRow(_row(), "TF", {}, {}, {"college_f": 130.0}) == []


def test_a_row_rated_in_another_pool_is_a_hard_finding():
    got = bs.checkRow(_row(rating_pool="pro_f|TF"), "TF", {}, {}, {})
    assert [(k, h) for k, h, _ in got] == [("pool", True)]
    got = bs.checkRow(_row(pool="pro_f", rating_pool="pro_f"), "TF", {}, {}, {})
    assert ("pool", True) in [(k, h) for k, h, _ in got]


def test_a_record_pace_is_hard_a_club_and_a_margin_are_soft():
    got = bs.checkRow(_row(time_seconds=738.0, pool="hs_f", rating_pool="hs_f"), "TF", {}, {}, {})
    assert [(k, h) for k, h, _ in got] == [("pace", True)]
    # a college row is outside the rule (owner: "every but college")
    assert bs.checkRow(_row(time_seconds=738.0), "TF", {}, {}, {}) == []
    got = bs.checkRow(_row(school="Nike Swoosh TC", speed_rating=166.0), "TF",
                      {"nike swoosh tc": "3 professional(s)"}, {}, {"college_f": 140.0})
    assert [(k, h) for k, h, _ in got] == [("club", False), ("margin", False)]
    # the team id names the club even when the school string does not
    got = bs.checkRow(_row(team_id=77), "TF", {}, {77: "no school grades on 500 rows"}, {})
    assert [(k, h) for k, h, _ in got] == [("club", False)]


def test_the_anchor_check_runs_on_the_published_pool(monkeypatch):
    import anchor_check
    # anchor_check binds the function at import; patch its own name
    monkeypatch.setattr(anchor_check, "normalizeTime", _fake)
    anchor_check._FACTOR.clear()
    # normalised as pro_f, published on college_f: 4.4/4.1 = 7% off -- the
    # gate's tolerance is 10%, so that alone passes; a hs_m scale does not
    got = bs.checkRow(_row(normalized_time=900.0 * 3.4), "TF", {}, {}, {})
    assert [(k, h) for k, h, _ in got] == [("anchor", True)]
    assert bs.checkRow(_row(normalized_time=900.0 * 4.1), "TF", {}, {}, {}) == []


def test_top_mean_and_gap_report():
    assert bs.topMean([1, 5, 3, None], n=2) == 4.0
    assert bs.topMean([]) is None
    pairs = [("hs_m", 100.0, 101.5)] * bs.MIN_GAP_PAIRS + [("hs_f", 100.0, 99.0)]
    got = bs.gapReport(pairs)
    assert got == {"hs_m": (bs.MIN_GAP_PAIRS, 1.5)}
