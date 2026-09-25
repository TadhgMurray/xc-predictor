"""anet TF's non-finish sentinel, 20,000 s (owner, 2026-09-25).

A Diamond League mile showed Kidder, Rudolf, Birnbaum and Abdilaahi at
"5:33:20" with PR/SR flags and a 1.7 rating: anet TF writes a non-finish as
SortInt 20,000,000 ms and the savers kept anything under 100,000,000.

    python -m pytest -q tests/test_tf_sentinel.py
"""
import os
import re
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in ("racecast", "engine", "scripts"):
    sys.path.insert(0, os.path.join(_ROOT, _d))
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
os.environ.setdefault("XCP_DB_QUIET", "1")

import pytest                                                    # noqa: E402
import result_status as R                                        # noqa: E402


def test_the_sortint_sentinel_is_no_time():
    assert R.timeFromSortInt(20_000_000) is None
    assert R.timeFromSortInt(99_999_999) is None
    assert R.timeFromSortInt(None) is None
    assert R.timeFromSortInt(0) is None


def test_a_real_sortint_is_seconds():
    assert R.timeFromSortInt(225_690) == pytest.approx(225.69)     # 3:45.69
    assert R.timeFromSortInt(1_620_000) == pytest.approx(1620.0)   # a 27-min 10K


def test_a_stored_sentinel_is_a_non_finish():
    assert R.isSentinelTime(20000.0) and R.isSentinelTime(20000.002)
    assert R.isSentinelTime(999999)
    assert not R.isSentinelTime(225.69) and not R.isSentinelTime(None)
    assert R.kind(None, 20000.0) == "dns"
    assert not R.isRated(None, 20000.0)
    assert R.kind("DNF", 20000.0) == "dnf"          # the letters still win
    assert R.isRated(None, 225.69)


def test_the_backfill_skips_it():
    pytest.importorskip("psycopg2")
    sys.path.insert(0, os.path.join(_ROOT, "backfill"))
    if "corrections" not in sys.modules:            # 165 MB, not in git
        import types
        _c = types.ModuleType("corrections")
        _c.__getattr__ = lambda name: {}
        sys.modules["corrections"] = _c
    import backfill_normalize as B
    assert B._isSentinelTime(20000.0)
    assert not B._isSentinelTime(225.69)


def test_the_savers_read_sortint_through_the_one_rule():
    src = open(os.path.join(_ROOT, "scripts", "database.py")).read()
    assert "sort_int < 100000000" not in src
    assert src.count("_timeFromSortInt(result.get(\"SortInt\"))") == 2


def test_the_page_mirror_matches_the_rule():
    app = open(os.path.join(_ROOT, "racecast", "app.py")).read()
    m = re.search(r"^_TF_SENTINEL = (\d+)$", app, re.M)
    assert m and float(m.group(1)) == R.TF_SENTINEL


def test_format_time_hides_it():
    pytest.importorskip("flask")
    import app as A
    assert A.format_time(20000.002) == " - "
    assert A.format_time(225.69) == "3:45.69"


# ---- the header of a career that turned professional (same day, Nuguse) ----

def _blk(pool, dates, rating=208.0):
    return {"pool": pool, "rating": rating, "rating_hs": rating,
            "school": "United States" if pool.startswith("pro") else "Notre Dame",
            "races": [{"date": d, "speed_rating": rating} for d in dates]}


def test_a_newer_pro_season_heads_the_page():
    pytest.importorskip("flask")
    import app as A
    ordered = sorted({
        (2027, "TF"): _blk("pro_m", ["2026-08-26", "2026-09-04", "2026-09-11"]),
        (2026, "TF"): _blk("pro_m", ["2026-05-31", "2026-07-18", "2026-07-04"]),
        (2022, "TF"): _blk("college_m", ["2022-05-01", "2022-06-10"], 147.5),
    }.items(), reverse=True)
    season = {"year": 2021, "sport": "TF"}
    (label, sport), blk = A._proHeaderSeason(ordered, season)
    assert (label, sport) == (2027, "TF") and blk["pool"] == "pro_m"


def test_a_school_season_newer_than_any_pro_one_keeps_the_header():
    pytest.importorskip("flask")
    import app as A
    ordered = sorted({
        (2026, "TF"): _blk("college_m", ["2026-05-01", "2026-06-10", "2026-06-12"]),
        (2025, "TF"): _blk("pro_m", ["2025-02-01", "2025-02-08", "2025-03-01"]),
    }.items(), reverse=True)
    assert A._proHeaderSeason(ordered, {"year": 2025, "sport": "TF"}) is None
