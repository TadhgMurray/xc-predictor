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


# ---------------------------------------------------------------- 2 --

# getReturningField's query before 2026-11-07: every athlete-season named
_OLD_RETURNING = """
        SELECT s.person_id, s.school, s.state, s.grade, s.pool, s.year,
               s.mean_rating AS rating{units},
               COALESCE(a.name, 'Unknown') AS name
        FROM   {source}
        {names}
        WHERE  TRUE {where}
        LIMIT  %(cap)s
"""


def test_the_returning_field_is_not_named():
    src = _read("racecast", "teams.py")
    i = src.index("def getReturningField")
    body = src[i:src.index("def nameRacedPage")]
    assert "nameLateral" not in body and "NULL::text AS name" in body
    i = src.index("if gradeExcluded(f) or eventRestricted(f):")
    raced = src[i:src.index("size = countField(cur, f)")]
    assert raced.index("sortAndPage(shown, f)") < raced.index("nameRacedPage(cur, rows)")


@pytest.fixture
def team_db(pgconn, monkeypatch):
    import time as _t
    import teams
    import school_identity
    cur = _dictCur(pgconn)
    cur.execute("""
        CREATE TEMP TABLE athlete_season AS
        SELECT p AS person_id, 'School ' || (p % 23) AS school, 'CA'::text AS state,
               (9 + p % 4)::text AS grade, 'hs_m'::text AS pool, 'XC'::text AS sport,
               2025 AS year, (100 + (p * 7919 % 400) / 10.0)::real AS mean_rating,
               (100 + (p * 7919 % 400) / 10.0 + 2)::real AS best_rating, 4 AS n_races
        FROM generate_series(1, 400) p;
        CREATE TEMP TABLE athletes (athlete_id bigint, person_id bigint, first_name text,
            last_name text, gender text);
        INSERT INTO athletes SELECT p, p, 'Ann' || p, 'Lee', 'M'
        FROM generate_series(1, 400) p WHERE p % 3 <> 0;
        CREATE TEMP TABLE results (person_id bigint, athlete_name text);
        INSERT INTO results SELECT p, 'Row ' || p FROM generate_series(1, 400) p
        WHERE p % 6 = 0;
        CREATE TEMP TABLE results_tf (person_id bigint, athlete_name text);
    """)
    monkeypatch.setitem(teams._ATHLETE_COLS, "at", _t.time() + 3600)
    monkeypatch.setitem(teams._ATHLETE_COLS, "cols", frozenset())
    monkeypatch.setitem(school_identity._LABELS, "loaded", True)
    return teams, cur


def _teamFilters(teams, qs):
    from urllib.parse import parse_qsl
    from werkzeug.datastructures import MultiDict
    f, err = teams.parseFilters(MultiDict(parse_qsl(qs)))
    assert not err, err
    return f


def _oldReturning(teams):
    from rankings import nameLateral

    def old(cur, f):
        params = {"cap": teams.RETURN_CAP}
        where = teams._athleteFieldWhere(f, params)
        source, have = teams._athleteSource(f, params)
        units = "".join((f', s."{c}"' if c in have else f', NULL::text AS "{c}"')
                        for c in sorted(teams.TEAM_UNIT_COLS))
        cur.execute(_OLD_RETURNING.format(units=units, source=source,
                                          names=nameLateral("s"), where=where), params)
        return cur.fetchall()
    return old


@pytest.mark.parametrize("qs", [
    "sport=XC&pool=hs_m&year=2025&grade=9,10",
    "sport=XC&pool=hs_m&year=2025&exclude_grade=12&limit=5&offset=5",
    "sport=XC&pool=hs_m&year=2025&grade=11&sort=best&dir=DESC&limit=7",
])
def test_the_raced_board_serves_the_same_names(team_db, monkeypatch, qs):
    teams, cur = team_db
    new_rows, new_info = teams.serveBoard(cur, _teamFilters(teams, qs))
    monkeypatch.setattr(teams, "getReturningField", _oldReturning(teams))
    monkeypatch.setattr(teams, "nameRacedPage", lambda cur, rows: rows)
    old_rows, old_info = teams.serveBoard(cur, _teamFilters(teams, qs))
    assert new_info["raced"] and new_rows
    assert (json.dumps(new_rows, sort_keys=True, default=str)
            == json.dumps(old_rows, sort_keys=True, default=str))
    assert new_info == old_info
    if "limit" not in qs:            # the whole board: every kind of name
        names = {sc["name"] for r in new_rows for sc in r["scorers"]}
        assert "Unknown" in names and any(n.startswith("Row ") for n in names)
        assert any(n.startswith("Ann") for n in names)
        assert all(r["best_name"] for r in new_rows)


# ---------------------------------------------------------------- 3 --

def test_the_recruit_search_cuts_the_page_before_naming():
    import recruiting
    src = _read("racecast", "recruiting.py")
    q = " ".join(src[src.index("def searchRecruits"):src.index("def searchOrTimeout")].split())
    assert q.index("LIMIT %(limit)s OFFSET %(offset)s) j") < q.index("{nameLateral('j')}")
    # no order reads the name, and every one ends on the unique person_id
    for order in recruiting.SORTS.values():
        assert "name" not in order and order.endswith("j.person_id")


def _recruitFilters(**kw):
    f = {"sport": "XC", "pool": "hs_m", "label": 2025, "year": 2025, "grad": None,
         "states": [], "schools": [], "min_rating": None, "max_rating": None,
         "min_gain": None, "min_races": 1, "min_races_explicit": True,
         "sort": "rating", "limit": 100, "offset": 0}
    f.update(kw)
    return f


@pytest.mark.parametrize("sort", ["rating", "gain", "best", "grad"])
def test_a_recruit_page_is_the_same_slice_of_the_named_list(pgconn, sort):
    import recruiting
    cur = _dictCur(pgconn)
    cur.execute("""
        CREATE TEMP TABLE athlete_season AS
        SELECT p AS person_id, 'School ' || (p % 9) AS school, 'CA'::text AS state,
               (9 + p % 4)::text AS grade, 'hs_m'::text AS pool, 'XC'::text AS sport,
               y AS year, (100 + (p * 7919 % 60) / 4.0 + y - 2024)::real AS mean_rating,
               (110 + (p * 31 % 50) / 4.0)::real AS best_rating, 4 AS n_races
        FROM generate_series(1, 300) p, generate_series(2024, 2025) y;
        CREATE TEMP TABLE athletes (athlete_id bigint, person_id bigint, first_name text,
            last_name text, gender text);
        INSERT INTO athletes SELECT p, p, 'Ann' || p, 'Lee', 'F'
        FROM generate_series(1, 300) p WHERE p % 4 <> 0;
        CREATE TEMP TABLE results (person_id bigint, athlete_name text);
        CREATE TEMP TABLE results_tf (person_id bigint, athlete_name text);
    """)
    everyone = recruiting.searchRecruits(cur, _recruitFilters(sort=sort, limit=1000))
    assert len(everyone) == 300
    page = recruiting.searchRecruits(cur, _recruitFilters(sort=sort, limit=20, offset=37))
    assert page == everyone[37:57]
    assert any(r["name"] is None for r in everyone)


# ---------------------------------------------------------------- 4 --

_COVER = ("(person_id) INCLUDE (sport, race_date, year, distance, time_seconds, "
          "speed_rating, event_kind)")


def _canonical(table):
    import build_ranking_results as B
    return dict(B._CANONICAL_INDEXES[table])


def _wanted():
    import add_page_indexes as W
    return {name: (table, spec) for table, _c, name, spec in W.WANTED}


def test_the_person_cover_is_one_index_with_the_flags_columns():
    import build_ranking_results as B
    canon = _canonical("ranking_results")
    assert canon["idx_rr_person_cover"] == _COVER
    assert "rr_person_cover_idx" not in canon
    assert "rr_person_cover_idx" in B._RETIRED_INDEXES["ranking_results"]
    assert _wanted()["idx_rr_person_cover"] == ("ranking_results", _COVER)
    # no other canonical index has the narrower signature any more
    old = B._indexSig("(person_id) INCLUDE (sport, race_date, year, distance, time_seconds)")
    assert old not in {B._indexSig(c) for c in canon.values()}


class _CatalogueConn:
    def __init__(self, rows):
        self.rows = rows

    def cursor(self):
        conn = self

        class C:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def execute(self, *a):
                pass

            def fetchall(self):
                return list(conn.rows)
        return C()


def test_a_rebuild_drops_the_old_covers_and_builds_the_new_one():
    import build_ranking_results as B
    old = "(person_id) INCLUDE (sport, race_date, year, distance, time_seconds)"
    defs = B.indexDefs(_CatalogueConn([
        ("ranking_results_idx_rr_person_cover",
         f"CREATE INDEX ranking_results_idx_rr_person_cover ON public.ranking_results USING btree {old}"),
        ("ranking_results_rr_person_cover_idx",
         f"CREATE INDEX ranking_results_rr_person_cover_idx ON public.ranking_results USING btree {old}"),
    ]), "ranking_results")
    sigs = {B._indexSig(d): n for n, d in defs}
    assert B._indexSig(old) not in sigs
    assert sigs[B._indexSig(_COVER)] == "ranking_results_idx_rr_person_cover"


def test_the_school_season_indexes():
    w = _wanted()
    assert w["idx_results_school_date"] == ("results", "(school, date) INCLUDE (meet_id)")
    assert w["idx_results_tf_school_date"] == ("results_tf", "(school, date) INCLUDE (meet_id)")


class _Rec:
    """A cursor that keeps the statements it runs."""
    def __init__(self, cur):
        self.cur, self.seen = cur, []

    def execute(self, sql, params=None):
        self.seen.append((sql, params))
        return self.cur.execute(sql, params)

    def fetchall(self):
        return self.cur.fetchall()

    @property
    def connection(self):
        return self.cur.connection


def _plan(cur, sql, params):
    cur.execute("EXPLAIN (ANALYZE, COSTS OFF) " + sql, params)
    return "\n".join(r["QUERY PLAN"] for r in cur.fetchall())


def test_the_rating_and_record_flags_read_only_the_index(pgconn, monkeypatch):
    import app as A
    import rankings
    pgconn.autocommit = True
    cur = _dictCur(pgconn)
    cur.execute("""
        CREATE TEMP TABLE ranking_results AS
        SELECT g AS result_id, g % 20000 AS person_id,
               CASE WHEN g % 3 = 0 THEN 'TF' ELSE 'XC' END AS sport,
               (2015 + g % 10)::text || '-0' || (1 + g % 9) || '-11' AS race_date,
               2015 + g % 10 AS year, (CASE WHEN g % 2 = 0 THEN 5000 ELSE 1600 END)::real AS distance,
               (900 + g % 300)::real AS time_seconds, (90 + g % 60)::real AS speed_rating,
               CASE WHEN g % 7 = 0 THEN 'hurdles' END AS event_kind,
               'hs_m'::text AS pool, 'School'::text AS school
        FROM generate_series(1, 300000) g""")
    cur.execute(f"CREATE INDEX rr_cover ON ranking_results {_canonical('ranking_results')['idx_rr_person_cover']}")
    cur.execute("VACUUM ANALYZE ranking_results")
    monkeypatch.setattr(rankings, "_EVENT_KIND_PRESENT", True)
    rows = [{"person_id": p, "speed_rating": 120.0, "time_seconds": 950.0,
             "result_id": 10 ** 9 + p} for p in range(1, 400, 13)]
    rec = _Rec(cur)
    A.stampRatingFlags(rec, "XC", [dict(r) for r in rows], "2024-03-01")
    A.stampRecordFlags(rec, "TF", [dict(r) for r in rows], 1600, "2024-03-01")
    A.stampRecordFlags(rec, "XC", [dict(r) for r in rows], 5000, "2024-03-01")
    careers = [(s, p) for s, p in rec.seen if "GROUP  BY person_id" in s]
    assert len(careers) == 3
    for sql, params in careers:
        plan = _plan(cur, sql, params)
        assert "Index Only Scan using rr_cover" in plan, plan
        assert "Heap Fetches: 0" in plan, plan


def test_a_school_season_reads_one_season_and_the_meet_list_only_the_index(pgconn):
    import meets_filter
    pgconn.autocommit = True
    cur = _dictCur(pgconn)
    cur.execute("""
        CREATE TEMP TABLE results AS
        SELECT g AS result_id, g % 50000 AS person_id, 'School ' || (g % 2000) AS school,
               (2010 + (g / 2000) % 16)::text || '-' || lpad((1 + g % 12)::text, 2, '0')
                   || '-15' AS date,
               g / 120 AS meet_id, (900 + g % 300)::real AS time_seconds
        FROM generate_series(1, 1000000) g""")
    w = _wanted()
    cur.execute("CREATE INDEX plain_school ON results (school)")
    cur.execute(f"CREATE INDEX school_date ON results {w['idx_results_school_date'][1]}")
    cur.execute("VACUUM ANALYZE results")
    season = _plan(cur, """
        SELECT r.school, r.person_id, count(*) FROM results r
        WHERE  r.school = ANY(%(s)s) AND r.person_id IS NOT NULL
          AND  r.date >= %(lo)s AND r.date < %(hi)s
          AND  r.time_seconds > 0 AND r.time_seconds < 19999
        GROUP  BY 1, 2""", {"s": ["School 7", "School 99"], "lo": "2024-07-01",
                            "hi": "2025-07-01"})
    assert "school_date" in season and re.search(
        r"Index Cond: .*school = ANY.*date >= .*date < ", season), season
    meets = _plan(cur, meets_filter._SCHOOL_SQL.split("SELECT x.meet_id")[0]
                  .replace("WITH mine AS (", "").rstrip().rstrip(")")
                  .format(table="results", year_clause=""),
                  {"school": "School 7", "sane": meets_filter._SANE_DATE, "lim": 400})
    assert "Index Only Scan using school_date" in meets and "Heap Fetches: 0" in meets, meets


# ---------------------------------------------------------------- 5 --

class _UnitCur:
    def __init__(self):
        self.n = 0

    def execute(self, sql, params=None):
        self.n += 1

    def fetchall(self):
        return [{"v": "BIG TEN", "c": 14, "h": 0}, {"v": "Ivy League", "c": 8, "h": 1},
                {"v": "CIF SOUTHERN", "c": 0, "h": 90}]


def test_valid_keys_are_held_per_process():
    import ttlcache
    import unit_board as UB
    ttlcache.clear()
    cur = _UnitCur()
    a = UB.validKeys(cur, "conference", True)
    b = UB.validKeys(cur, "conference", True)
    assert cur.n == 1 and a == b == UB._validKeysUncached(_UnitCur(), "conference", True)
    assert UB.validKeys(cur, "conference", False) != a and cur.n == 2
    assert UB.VALID_TTL == 600.0
    ttlcache.clear()


def test_projections_are_computed_once_for_the_box(tmp_path, monkeypatch):
    import projections as P
    import sharedcache
    import ttlcache
    monkeypatch.setattr(sharedcache, "DIR", str(tmp_path / "c"))
    n = []
    monkeypatch.setattr(P, "currentYear", lambda cur: n.append(1) or 2025)
    monkeypatch.setattr(P, "_fieldUncached", lambda *a: [])
    monkeypatch.setattr(P, "stateCourse", lambda *a: None)
    monkeypatch.setattr(P, "_courseSpec", lambda *a: None)
    key = ("proj", "ZZ", "all", "boys")
    ttlcache._store.pop(key, None)
    a = P.project(None, "ZZ", None, "boys")
    ttlcache._store.pop(key, None)                  # another worker: no memory copy
    b = P.project(None, "ZZ", None, "boys")
    c = P.project(None, "ZZ", None, "boys")         # this worker's memory copy
    ttlcache._store.pop(key, None)
    assert n == [1]
    assert a == b == c and "_shared_at" not in a
    assert a["year"] == 2025 and a["teams"] == [] and a["n_field"] == 0
    assert isinstance(a["computed_at"], float)
