"""A course board built before the go-live is served with today's ratings
(owner, 2026-11-09: Tom Rankin 160.7 on /course/Hereford HS, 145.8 on his
profile -- --fast had skipped the course pages)."""
import datetime
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in ("racecast", "scripts", "engine"):
    sys.path.insert(0, os.path.join(_ROOT, _d))
os.environ.setdefault("XCP_DB_PASSWORD", "unused")

DSN = os.environ.get("XCP_TEST_DSN") or os.environ.get("XCP_TWIN_TEST_DSN")


def test_fresh_is_built_after_the_golive(monkeypatch):
    import app as A
    t = datetime.datetime(2026, 11, 9, 2, 0, tzinfo=datetime.timezone.utc)
    monkeypatch.setattr(A, "_goliveAt", lambda cur: t)
    assert A._boardFresh(None, t + datetime.timedelta(seconds=1))
    assert not A._boardFresh(None, t - datetime.timedelta(hours=3))
    monkeypatch.setattr(A, "_goliveAt", lambda cur: None)
    assert A._boardFresh(None, t - datetime.timedelta(hours=3))   # no stamp yet


def test_the_pipeline_keeps_fast_runs_fast_and_stamps_the_golive():
    sh = open(os.path.join(_ROOT, "deploy", "run_pipeline.sh")).read()
    assert "12b_course_pages" in sh.split('FAST_SKIP="$FAST_SKIP,10f2_projection')[1].split("\n")[0]
    gl = open(os.path.join(_ROOT, "engine", "joint_golive.py")).read()
    assert gl.index("writeGoliveStamp()") < gl.index("writeEngineScale(live")


@pytest.mark.skipif(not DSN, reason="needs XCP_TEST_DSN (a scratch Postgres)")
def test_a_stale_board_takes_the_live_ratings(monkeypatch):
    import psycopg2
    import psycopg2.extras
    import app as A
    monkeypatch.setattr(A, "_ratingPoolCol", lambda cur, table, alias="r": "r.rating_pool")
    monkeypatch.setattr(A, "stampRowsHs", lambda cur, sport, rows, **k: [
        r.__setitem__("hs_rating", r["speed_rating"]) for r in rows] and False)
    monkeypatch.setattr(A, "stampBoardRows", lambda rows, rating_keys, sport=None: [
        r.__setitem__("hs_" + rating_keys[0], r[rating_keys[0]]) for r in rows] and False)
    conn = psycopg2.connect(DSN)
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("CREATE TEMP TABLE results (result_id bigint, meet_id bigint, div_id bigint,"
                    " school text, speed_rating real, rating_pool text)")
        rows = [(1, 7, 8, "Glenelg", 115.0, "hs_m|x"), (2, 7, 8, "Liberty", 133.4, "hs_m")]
        rows += [(10 + i, 7, 8, "Oakland Mills", 120.0 + i, "hs_m") for i in range(6)]
        cur.executemany("INSERT INTO results VALUES (%s,%s,%s,%s,%s,%s)", rows)
        ctx = {"rating_bests": {"M": [
                   {"result_id": 1, "person_id": 5, "gender": "M", "speed_rating": 160.7,
                    "hs_rating": 160.7, "rating_pool": "hs_f", "distance": 4828},
                   {"result_id": 2, "person_id": 6, "gender": "M", "speed_rating": 133.4,
                    "hs_rating": 133.4, "rating_pool": "hs_m", "distance": 4828}], "F": []},
               "records": {"M": [], "F": []},
               "team_rating": {"M": [{"meet_id": 7, "div_id": 8, "school": "Oakland Mills",
                                      "gender": "M", "avg5": 99.0, "hs_avg5": 99.0}], "F": []}}
        out = A._refreshCourseRatings(cur, ctx)
        best = out["rating_bests"]["M"]
        assert [r["result_id"] for r in best] == [2, 1]          # re-ranked
        assert best[1]["speed_rating"] == 115.0 and best[1]["rating_pool"] == "hs_m"
        assert best[1]["hs_rating"] == 115.0
        assert out["team_rating"]["M"][0]["avg5"] == pytest.approx(123.0)   # 125..121
        assert ctx["rating_bests"]["M"][0]["result_id"] == 1     # the board's own dict untouched
    finally:
        conn.close()
