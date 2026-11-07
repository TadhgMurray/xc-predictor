"""The 2026-11-07 site speed pass: the same pages, faster.

    python -m pytest -q tests/test_perf_2026_11_07.py
    XCP_TWIN_TEST_DSN="host=/tmp/pgtest port=54329 user=postgres dbname=postgres" \\
        python -m pytest -q tests/test_perf_2026_11_07.py      # + Postgres

1. Track venue pages are precomputed (venue_tf_boards, step 12c) by the
   function the live route falls back to, and render byte for byte the same.
2. Raced team boards name only the runners on the served page.
3. The recruiting search names the page it returns, not every candidate.
4. Indexes: ranking_results' person cover carries the rating flags' columns;
   results / results_tf (school, date) for school-season reads.
5. unit_board.validKeys is held 10 minutes per process; /projections'
   compute is shared across workers.
"""
import contextlib
import io
import json
import os
import re
import sys

import _env  # noqa: F401  -- sets XCP_DB_PASSWORD, must precede config

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "scripts"),
           os.path.join(_ROOT, "engine"), os.path.dirname(os.path.abspath(__file__))):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest                                                  # noqa: E402


def _read(*parts):
    with io.open(os.path.join(_ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


@pytest.fixture
def pgconn():
    dsn = os.environ.get("XCP_TWIN_TEST_DSN")
    if not dsn:
        pytest.skip("set XCP_TWIN_TEST_DSN to a scratch Postgres")
    import psycopg2
    conn = psycopg2.connect(dsn)
    yield conn
    conn.rollback()
    conn.close()


def _dictCur(conn):
    import psycopg2.extras
    return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)


# ---------------------------------------------------------------- 1 --

_VENUE_SQL = """
CREATE TEMP TABLE meets_tf (meet_id bigint, div_id bigint, event_id bigint,
    meet_name text, location_id bigint, is_indoor integer, venue_name text);
CREATE TEMP TABLE meets_tf_meta (meet_id bigint, gender text, venue_name text,
    city text, meet_date text);
CREATE TEMP TABLE results_tf (result_id bigint, person_id bigint, athlete_id bigint,
    athlete_name text, school text, time_seconds real, mark text, is_field integer,
    is_relay integer, date text, grade text, speed_rating real, rating_pool text,
    event_short text, meet_id bigint, div_id bigint, event_id bigint);
CREATE TEMP TABLE athletes (athlete_id bigint, person_id bigint, first_name text,
    last_name text, gender text, school text);
CREATE TEMP TABLE person_gender (person_id bigint, gender text);
CREATE TEMP TABLE course_difficulties (course_name text, difficulty real,
    n_results integer, n_athletes integer);
CREATE TEMP TABLE ranking_results (sport text, result_id bigint, pool text);

INSERT INTO meets_tf
SELECT m, m * 10 + e, e, 'Meet ' || m, 77, 0, NULL
FROM generate_series(1, 4) m, generate_series(1, 6) e;
INSERT INTO meets_tf VALUES (99, 990, 1, 'Elsewhere', 78, 0, NULL);
INSERT INTO meets_tf_meta VALUES (1, 'M', 'Hughes Stadium', 'Sacramento', '2025-04-01'),
    (2, NULL, 'Hughes Stadium', 'Sacramento', '2025-04-08'),
    (3, 'F', 'Hughes Stadium', 'Sacramento', '2025-04-15');
INSERT INTO course_difficulties VALUES ('TF:loc:77:out', 1.013, 4321, 1234);
-- runners: 40 people, two events of flat races, a field event, a hurdle race
INSERT INTO results_tf
SELECT 1000 * m + 10 * e + p % 10 + 100 * (p / 10), p, p, NULL,
       'School ' || (p % 7), (CASE e WHEN 1 THEN 600 WHEN 2 THEN 280 WHEN 3 THEN 130
                                     ELSE 15 END) + (p * 37 % 50) / 3.0 + m,
       CASE WHEN e = 4 THEN (40 + p % 9) || '-' || (p % 12) END,
       CASE WHEN e = 4 THEN 1 ELSE 0 END, 0,
       '2025-04-0' || m, (9 + p % 4)::text,
       CASE WHEN e = 4 THEN NULL ELSE 90 + (p * 13 % 40) + m END,
       CASE WHEN p % 5 = 0 THEN 'college_m' WHEN p % 2 = 0 THEN 'hs_f' ELSE 'hs_m' END,
       CASE e WHEN 1 THEN '3200 Meters' WHEN 2 THEN 'Boys 1600m' WHEN 3 THEN '300m Hurdles'
              ELSE 'Shot Put' END,
       m, m * 10 + e, e
FROM generate_series(1, 4) m, generate_series(1, 4) e, generate_series(1, 40) p;
-- relays: squads by school
INSERT INTO results_tf
SELECT 900000 + 100 * m + s, NULL, NULL, 'Relay', 'School ' || s, 200 + s * 3 + m,
       NULL, 0, 1, '2025-04-0' || m, NULL, NULL, NULL,
       CASE WHEN s % 2 = 0 THEN 'Girls 4x400 Relay' ELSE '4x400m Relay' END,
       m, m * 10 + 5, 5
FROM generate_series(1, 4) m, generate_series(1, 7) s;
INSERT INTO athletes
SELECT p, p, 'First' || p, 'Lé' || p, CASE WHEN p % 2 = 0 THEN 'F' ELSE 'M' END, NULL
FROM generate_series(1, 35) p;
INSERT INTO person_gender SELECT p, CASE WHEN p % 2 = 0 THEN 'F' ELSE 'M' END
FROM generate_series(1, 40) p;
INSERT INTO ranking_results SELECT 'TF', result_id, rating_pool FROM results_tf
WHERE speed_rating IS NOT NULL;
"""


@pytest.fixture
def venue_db(pgconn, monkeypatch):
    import app as A
    cur = _dictCur(pgconn)
    cur.execute(_VENUE_SQL)
    pgconn.commit()                          # temp tables outlive a rollback

    @contextlib.contextmanager
    def fake():
        yield pgconn
        pgconn.rollback()
    monkeypatch.setattr(A, "getConn", fake)
    yield A, pgconn, cur
    pgconn.rollback()
    cur.execute("DROP TABLE IF EXISTS pg_temp.venue_tf_boards")
    pgconn.commit()


def _storeBoard(A, conn, cur, loc, indoor):
    """What build_venue_tf_boards writes, into a temp venue_tf_boards."""
    import build_venue_tf_boards as B
    cur.execute("""CREATE TEMP TABLE IF NOT EXISTS venue_tf_boards (
                       location_id bigint, indoor smallint, fp text, ctx jsonb,
                       PRIMARY KEY (location_id, indoor))""")
    conn.commit()
    fp, payload = B.buildOne(cur, loc, indoor)
    conn.rollback()
    cur.execute("INSERT INTO venue_tf_boards VALUES (%s, %s, %s, %s::jsonb)",
                (loc, indoor, fp, payload))
    conn.commit()


def test_the_venue_page_renders_the_same_from_the_board(venue_db, monkeypatch):
    A, conn, cur = venue_db
    c = A.app.test_client()
    live = c.get("/venue/tf/77/out")
    assert live.status_code == 200
    html = live.get_data(as_text=True)
    # the fixture reaches every section the page has
    for bit in ("Hughes Stadium, Sacramento", "First3 Lé3", "4x400", "Shot Put",
                "Meet 4", "Records"):
        assert bit in html, bit

    _storeBoard(A, conn, cur, 77, 0)
    calls = []
    real = A.buildVenueTfCtx
    monkeypatch.setattr(A, "buildVenueTfCtx",
                        lambda *a: calls.append(a) or real(*a))
    board = c.get("/venue/tf/77/out")
    assert calls == []                          # served from the board
    assert board.get_data(as_text=True) == html


def test_a_venue_with_new_meets_renders_live(venue_db, monkeypatch):
    A, conn, cur = venue_db
    _storeBoard(A, conn, cur, 77, 0)
    cur.execute("INSERT INTO meets_tf VALUES (5, 51, 1, 'Meet 5', 77, 0, NULL)")
    conn.commit()
    calls = []
    real = A.buildVenueTfCtx
    monkeypatch.setattr(A, "buildVenueTfCtx",
                        lambda *a: calls.append(a) or real(*a))
    assert A.app.test_client().get("/venue/tf/77/out").status_code == 200
    assert len(calls) == 1


def test_an_unbuilt_venue_and_an_unbuilt_table_render_live(venue_db, monkeypatch):
    A, conn, cur = venue_db
    assert A.loadVenueTfBoard(cur, 77, False) is None       # no table at all
    conn.rollback()
    _storeBoard(A, conn, cur, 77, 0)
    assert A.loadVenueTfBoard(cur, 77, True) is None        # the other side
    assert A.loadVenueTfBoard(cur, 78, False) is None       # another venue
    assert A.loadVenueTfBoard(cur, 77, False) is not None


def test_the_payload_keeps_accents_and_refuses_nan():
    import build_venue_tf_boards as B
    assert "Lé" in B.payloadJson({"x": "Lé"})
    with pytest.raises(ValueError):
        B.payloadJson({"x": float("nan")})


def test_the_venue_build_is_a_pipeline_step_after_the_course_pages():
    sh = _read("deploy", "run_pipeline.sh")
    assert (sh.index("12b_finish") < sh.index("12c_prepare")
            < sh.index('shards 12c_venue_pages "$XCP_COURSE_SHARDS"')
            < sh.index("12c_finish"))
    src = _read("racecast", "build_venue_tf_boards.py")
    assert "from app import buildVenueTfCtx, venueTfFingerprint" in src
    assert "swapTable(conn, TABLE" in src
