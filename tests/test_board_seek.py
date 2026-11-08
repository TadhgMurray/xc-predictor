"""Deep board pages seek from a stored position (rankings "DEEP PAGES",
racecast/build_board_seek.py), and a filtered board stops at LIVE_DEEP_MAX.

    python -m pytest -q tests/test_board_seek.py
    XCP_TWIN_TEST_DSN="host=/tmp/pgtest port=54329 user=postgres dbname=postgres" \\
        python -m pytest -q tests/test_board_seek.py        # + the Postgres ones

1. On Postgres, every default board family (performance and ability; a
   sport and both; a season and all; a distance and none; a sprint; the two
   floors) answers the same rows from board_seek as live, at the first
   seekable page, middle pages, the last page, the end exactly and past it.
2. Every stored position is where its key starts: exactly `pos` rows of the
   board come before it, ties and all.
3. A table built from another ranking_results / athlete_season / code is
   never read.
4. A filtered (or re-sorted) board past LIVE_DEEP_MAX is refused with a
   message the board shows; a default board never is.
5. The pipeline builds it beside the Events presets, run and nightly.
"""
import io
import json
import os
import sys

import _env  # noqa: F401  -- sets XCP_DB_PASSWORD, must precede config

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "scripts"),
           os.path.join(_ROOT, "engine"), os.path.dirname(os.path.abspath(__file__))):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest                                                  # noqa: E402
from werkzeug.datastructures import MultiDict                  # noqa: E402
from urllib.parse import parse_qsl                             # noqa: E402


def _read(*parts):
    with io.open(os.path.join(_ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


def _f(qs):
    import rankings as R
    f, err = R.parseFilters(MultiDict(parse_qsl(qs)))
    assert not err, err
    return f


# ------------------------------------------------------------------ 4 --

def test_which_boards_seek():
    import rankings as R
    fam = lambda qs: R.seekFamily(_f(qs))                       # noqa: E731
    assert fam("board=performance&pool=hs_m&sport=XC&scale=hs&distance=5000") \
        == "performance|hs_m|XC|*|5000|-"
    assert fam("board=performance&pool=hs_f&sport=both&year=2024") == "performance|hs_f|both|2024|*|-"
    assert fam("board=ability&pool=hs_m&sport=XC&year=2012&min_races=3") == "ability|hs_m|XC|2012|*|e3"
    assert fam("board=ability&pool=ms_f&sport=TF") == "ability|ms_f|TF|*|*|d"
    for qs in ("board=performance&pool=hs_m&sport=XC&state=CA",
               "board=performance&pool=hs_m&sport=XC&year=2023,2024",
               "board=performance&pool=hs_m&sport=XC&sort=date",
               "board=performance&pool=hs_m&sport=XC&dir=asc",
               "board=performance&pool=hs_m&sport=XC&scope=all",
               "board=performance&pool=hs_m&sport=XC&grade=12",
               "board=performance&pool=hs_m&sport=XC&date_from=2024-01-01",
               "board=ability&pool=hs_m&sport=XC&min_races=5",
               "board=ability&pool=hs_m&sport=XC&dist_min=3000",
               "board=ability&pool=hs_m&sport=XC&conference=BIG",
               "board=pr&pool=hs_m&sport=XC&distance=5000"):
        assert fam(qs) is None, qs


def test_deep_pages_of_a_filtered_board_are_refused():
    import rankings as R
    deep = R.LIVE_DEEP_MAX + 50
    for qs in ("board=performance&pool=hs_m&sport=XC&state=CA",
               "board=ability&pool=hs_m&sport=XC&sort=best",
               "board=ability&pool=hs_m&sport=XC&year=2023,2024",
               "board=pr&pool=hs_m&sport=XC&distance=5000&state=TX",
               "board=pr&pool=hs_m&sport=XC&distance=5000&sort=date"):
        msg = R.deepPageRefusal(_f(qs + f"&offset={deep}"))
        assert msg and f"{R.LIVE_DEEP_MAX:,}" in msg, qs
        assert R.deepPageRefusal(_f(qs + f"&offset={R.LIVE_DEEP_MAX}")) is None, qs
    # the boards people and crawlers page through: never
    for qs in ("board=performance&pool=hs_m&sport=XC&scope=usa&scale=hs&sort=rating&distance=5000",
               "board=ability&pool=hs_m&sport=XC&scope=usa&scale=hs&year=2012&sort=rating&min_races=3",
               "board=ability&pool=college_f&sport=both",
               "board=pr&pool=hs_m&sport=XC&distance=5000",
               "board=pr&pool=all&gender=f&sport=TF&event=high_jump&year=2024"):
        assert R.deepPageRefusal(_f(qs + "&offset=5000000")) is None, qs


def test_the_offset_ceiling_moved_only_for_the_seek():
    """parseFilters lets a default board page past 100,000 (the seek reaches
    it); a board paged live still reads a deeper offset as 100,000."""
    import rankings as R
    f = _f("board=performance&pool=hs_m&sport=XC&offset=150850")
    assert f["offset"] == 150850
    R._liveOffset(f)
    assert f["offset"] == R.LEGACY_MAX_OFFSET == 100000
    assert _f("board=ability&offset=99999999")["offset"] == R.MAX_OFFSET


class _NoTable:
    """A cursor whose board_seek is stale: every board pages live."""
    def __init__(self):
        self.sql = []

    def execute(self, sql, params=None):
        self.sql.append((sql, dict(params) if isinstance(params, dict) else params))

    def fetchone(self):
        return (None, 1, 2)

    def fetchall(self):
        return []


def test_a_stale_table_pages_live_with_the_old_ceiling():
    import rankings as R
    R._SEEK_FRESH.update(at=0.0, ok=False)
    try:
        cur = _NoTable()
        f = _f("board=performance&pool=hs_m&sport=XC&offset=150850")
        assert R.getPerformanceRankings(cur, f) == []
        assert "board_seek" not in cur.sql[-1][0] and "WITH candidates" in cur.sql[-1][0]
        assert cur.sql[-1][1]["offset"] == 100000 and f["offset"] == 100000
    finally:
        R._SEEK_FRESH.update(at=0.0, ok=False)


class _Stub:
    """The rankings route with the database stubbed out."""
    def __enter__(self):
        import app as A
        import school_units
        self.A, self.su = A, school_units
        self.saved = {k: getattr(A, k) for k in ("_inMaintenance", "getConn",
                                                 "getPerformanceRankings")}
        self.units = school_units.applyUnitFilters
        A._inMaintenance = lambda: False

        class _Cur:
            def __enter__(s): return s
            def __exit__(s, *a): return False

        class _Conn:
            def __enter__(s): return s
            def __exit__(s, *a): return False
            def cursor(s, **k): return _Cur()
            def rollback(s): pass
        A.getConn = lambda *a, **k: _Conn()
        self.ran = []
        A.getPerformanceRankings = lambda cur, f: self.ran.append(f["offset"]) or []
        school_units.applyUnitFilters = lambda cur, f, args: None
        return self

    def __exit__(self, *a):
        for k, v in self.saved.items():
            setattr(self.A, k, v)
        self.su.applyUnitFilters = self.units


def test_the_route_answers_a_refusal_the_board_can_show():
    pytest.importorskip("flask")
    import rankings as R
    with _Stub() as s:
        c = s.A.app.test_client()
        r = c.get("/api/rankings?board=performance&pool=hs_m&sport=XC&state=CA&offset=20000")
        assert r.status_code == 400
        body = json.loads(r.data)
        assert body["deep_limit"] == R.LIVE_DEEP_MAX and "row 10,000" in body["error"]
        assert s.ran == []                                   # nothing was ranked
        r = c.get("/api/rankings?board=performance&pool=hs_m&sport=XC&offset=150850")
        assert r.status_code == 200 and s.ran == [150850]


def test_the_board_keeps_its_way_back_after_a_refusal():
    js = _read("racecast", "static", "rankings.js")
    assert "data.deep_limit" in js and "state.deepLimit" in js


# ------------------------------------------------------------------ 5 --

def test_the_pipeline_builds_it_beside_the_events_presets():
    run = _read("deploy", "run_pipeline.sh")
    i = run.index("bgstep 10f4_season_events")
    j = run.index("bgstep 10f8_board_seek")
    assert 0 < j - i < 2500 and run.index("step 10_rankings_finish") < j
    night = _read("deploy", "nightly_update.sh")
    assert night.index("10f4_season_events") < night.index("step 10f8_board_seek") \
        < night.index("13z_prewarm")


# ------------------------------------------------------------- Postgres --

@pytest.fixture
def pg():
    dsn = os.environ.get("XCP_TWIN_TEST_DSN")
    if not dsn:
        pytest.skip("set XCP_TWIN_TEST_DSN to a scratch Postgres to run the boards")
    import psycopg2
    import psycopg2.extras
    import rankings as R
    conn = psycopg2.connect(dsn)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    saved = (R.SEEK_EVERY, dict(R._SEEK_FRESH), dict(R._SEEK_SIG))
    R.SEEK_EVERY = 7                     # many stored positions on a small board
    R._SEEK_SIG.clear()
    R._SEEK_FRESH.update(at=0.0, ok=False)
    yield conn, cur
    R.SEEK_EVERY = saved[0]
    R._SEEK_FRESH.clear(); R._SEEK_FRESH.update(saved[1])
    R._SEEK_SIG.clear(); R._SEEK_SIG.update(saved[2])
    conn.rollback()
    conn.close()


_UNITS = ", ".join(f"NULL::text AS {u}" for u in (
    "division", "region", "conference", "league", "state_div", "section_div",
    "district", "county", "class", "area", "section"))


def _fixture(cur, ties=False):
    """Two pools, two sports, three seasons (2026 is open: no floor),
    distances in and out of the 5000 band, a sprint, foreign and feedless
    states, a tfrrs copy of one race in nine (same rating or 0.3 lower),
    rows nobody can name. `ties`: ratings and ids that tie across sports
    and across one person's seasons, for the position check."""
    rid = "g::bigint" if not ties else "(g / 2)::bigint"
    rg = "g" if not ties else "(g / 2)"
    cur.execute(f"""CREATE TEMP TABLE ranking_results AS
        SELECT CASE WHEN g % 5 = 0 THEN 'ms_m' ELSE 'hs_m' END AS pool,
               CASE WHEN g % 3 = 0 THEN 'TF' ELSE 'XC' END AS sport,
               {rid} AS result_id, (g % 97)::bigint AS person_id,
               CASE WHEN g % 3 = 0 AND g % 7 = 0 THEN NULL
                    ELSE (140 + ({rg} * 37) % 41)::real END AS speed_rating,
               date '2023-09-01' + (g % 3) * 365 AS race_date,
               (ARRAY[2023, 2024, 2026])[1 + g % 3] AS year,
               (ARRAY['WI','CA','TX','ON', NULL])[1 + (g / 3) % 5] AS state,
               'School ' || g % 9 AS school, (9 + g % 4)::text AS grade,
               (g / 20)::bigint AS meet_id, 1::bigint AS div_id,
               CASE WHEN g % 4 = 0 THEN NULL ELSE (g / 20)::bigint END AS canon_meet_id,
               CASE WHEN g % 3 = 0 AND g % 7 = 0 THEN (10 + g % 13 / 10.0)::real
                    ELSE (900 + g % 300)::real END AS time_seconds,
               g::bigint AS event_id,
               CASE WHEN g % 3 = 0 AND g % 7 = 0 THEN 100
                    ELSE (ARRAY[5000, 4999.9, 3200, 5000, 8047])[1 + g % 5] END::real AS distance,
               {_UNITS}, NULL::text AS event_kind, NULL::real AS mark
        FROM generate_series(1, 6000) g""")
    # the tfrrs copies: negative ids, no state, same race and person
    cur.execute("""INSERT INTO ranking_results
        SELECT pool, sport, -result_id - 100000, person_id,
               CASE WHEN result_id % 2 = 0 THEN speed_rating ELSE speed_rating - 0.3 END,
               race_date, year, NULL, school, grade, meet_id + 1000, div_id, canon_meet_id,
               time_seconds + 0.04, distance, event_id, division, region, conference,
               league, state_div, section_div, district, county, class, area, section,
               event_kind, mark
        FROM ranking_results WHERE canon_meet_id IS NOT NULL AND result_id % 9 = 1""")
    cur.execute(f"""CREATE TEMP TABLE athlete_season AS
        SELECT person_id, pool, sport, year,
               CASE WHEN person_id % 31 = 0 THEN NULL
                    ELSE ({'' if ties else 'year % 7 / 10.0 + '}(max(speed_rating) - 2))::real END
                                                 AS mean_rating,
               max(speed_rating) AS best_rating,
               (count(*) % 6)::int + (person_id % 2)::int AS n_races,
               min(race_date) AS first_race, max(race_date) AS last_race,
               min(state) AS state, min(school) AS school, min(grade) AS grade, {_UNITS}
        FROM ranking_results WHERE result_id > 0 AND speed_rating IS NOT NULL
        GROUP BY person_id, pool, sport, year""")
    for t, cols in (("ranking_results", "(pool, sport, speed_rating DESC)"),
                    ("ranking_results", "(person_id)"),
                    ("athlete_season", "(pool, sport, year, mean_rating DESC NULLS LAST, person_id)"),
                    ("athlete_season", "(pool, sport, mean_rating DESC NULLS LAST, person_id)")):
        cur.execute(f"CREATE INDEX ON {t} {cols}")
    cur.execute("CREATE TEMP TABLE athletes (athlete_id bigint, person_id bigint,"
                " first_name text, last_name text, gender text)")
    # every 11th person has no name anywhere: left off the board, after the page
    cur.execute("INSERT INTO athletes SELECT g, g, 'A', 'B' || g, 'M'"
                " FROM generate_series(0, 96) g WHERE g % 11 <> 3")
    cur.execute("CREATE TEMP TABLE results (person_id bigint, athlete_name text)")
    cur.execute("CREATE TEMP TABLE results_tf (person_id bigint, athlete_name text)")
    cur.execute("ANALYZE ranking_results; ANALYZE athlete_season")


def _buildSeek(cur):
    import build_board_seek as B
    import rankings as R
    cur.execute("CREATE TEMP TABLE board_seek (fam text NOT NULL, pos integer NOT NULL,"
                " k1 double precision, k2 bigint NOT NULL)")
    for job in B._jobs():
        sql, params = B.jobSql(job)
        cur.execute(f"INSERT INTO board_seek (fam, pos, k1, k2) {sql}", params)
    cur.execute("CREATE UNIQUE INDEX ON board_seek (fam, pos)")
    cur.execute("""SELECT 'ranking_results'::regclass::oid AS a,
                          'athlete_season'::regclass::oid AS b""")
    row = cur.fetchone()
    cur.execute("COMMENT ON TABLE board_seek IS %s", (R.seekComment(row["a"], row["b"]),))
    R._SEEK_FRESH.update(at=0.0, ok=False)


_FAMILIES = (
    "board=performance&pool=hs_m&sport=XC",
    "board=performance&pool=hs_m&sport=both&scale=hs",
    "board=performance&pool=hs_m&sport=XC&distance=5000",
    "board=performance&pool=hs_m&sport=XC&year=2024",
    "board=performance&pool=hs_m&sport=both&year=2026&distance=5000",
    "board=performance&pool=hs_m&sport=TF&distance=100",          # a sprint: the clock
    "board=performance&pool=ms_m&sport=TF",
    "board=ability&pool=hs_m&sport=XC",
    "board=ability&pool=hs_m&sport=both",
    "board=ability&pool=hs_m&sport=XC&min_races=3",                  # 2026 is floored here
    "board=ability&pool=hs_m&sport=both&year=2024&min_races=3",
    "board=ability&pool=ms_m&sport=XC&year=2026",
)


def _page(cur, qs, offset, limit, seek):
    import rankings as R
    f = _f(f"{qs}&offset={offset}&limit={limit}")
    R._SEEK_FRESH.update(at=1e18, ok=seek)
    fn = R.getPerformanceRankings if f["board"] == "performance" else R.getAbilityRankings
    rows = [dict(r) for r in fn(cur, f)]
    return rows, f


def _size(cur, qs):
    import rankings as R
    cur.execute("SELECT k2 FROM board_seek WHERE fam = %s AND pos = -1",
                (R.seekFamily(_f(qs)),))
    row = cur.fetchone()
    return int(row["k2"]) if row else 0


def test_every_page_of_the_seek_is_the_live_page(pg):
    import rankings as R
    _conn, cur = pg
    _fixture(cur)
    _buildSeek(cur)
    for qs in _FAMILIES:
        n = _size(cur, qs)
        assert n > 4 * R.SEEK_EVERY, (qs, n)
        # the live board's length, read off the live board itself
        last, _f0 = _page(cur, qs, n - 3, 50, seek=False)
        past, _f1 = _page(cur, qs, n, 50, seek=False)
        assert past == [] and 0 < len(last) <= 3, qs
        offsets = sorted({R.SEEK_EVERY, R.SEEK_EVERY + 1, 2 * R.SEEK_EVERY - 1,
                          n // 2, n // 2 + 3, n - 50, n - 10, n - 1, n, n + 1, n + 500})
        for off in offsets:
            if off < 0:
                continue
            for limit in (10, 50):
                live, fl = _page(cur, qs, off, limit, seek=False)
                seek, fs = _page(cur, qs, off, limit, seek=True)
                assert seek == live, (qs, off, limit)
                assert fs == fl, (qs, off, limit)        # the echoed filters too
        # and the seek really ran (a stored position below the offset)
        f = _f(f"{qs}&offset={n // 2}")
        R._SEEK_FRESH.update(at=1e18, ok=True)
        pt = R._seekPoint(cur, f)
        assert pt[0] == "seek" and 0 < n // 2 - pt[1] < R.SEEK_EVERY, (qs, pt)
        assert R._seekPoint(cur, _f(f"{qs}&offset={n}")) == ("end",)


def test_every_stored_position_starts_its_key(pg):
    """With ties in the key (one id raced in both sports at one rating, one
    person's seasons at one mean): exactly `pos` rows of the board come
    before every stored key, so a seek from it reads board[pos:]."""
    import rankings as R
    _conn, cur = pg
    _fixture(cur, ties=True)
    _buildSeek(cur)
    cur.execute("SELECT fam, pos, k1, k2 FROM board_seek WHERE pos > 0 ORDER BY fam, pos")
    stored = [dict(r) for r in cur.fetchall()]
    assert len(stored) > 50
    checked = set()
    for s in stored:
        board, pool, sport, year, dist, floor = s["fam"].split("|")
        qs = f"board={board}&pool={pool}&sport={sport}"
        qs += "" if year == "*" else f"&year={year}"
        qs += "" if dist == "*" else f"&distance={dist}"
        qs += "&min_races=3" if floor == "e3" else ""
        f = _f(qs)
        params = {"min_races": 3, "k1": s["k1"], "k2": s["k2"]}
        where = R._whereClauses(f, params, with_dates=(board == "performance"))
        if board == "ability":
            cur.execute(f"""SELECT count(*) AS n FROM athlete_season s
                            WHERE {R.floorSql(floor == 'e3')} {where}
                              AND (s.mean_rating > %(k1)s::float8 OR (s.mean_rating = %(k1)s::float8
                                   AND s.person_id < %(k2)s))""", params)
        else:
            sprint = dist != "*" and int(dist) < R.UNRATED_BELOW
            col, cw, before = (("time_seconds", "time_seconds > 0", "<") if sprint else
                               ("speed_rating", "speed_rating IS NOT NULL", ">"))
            cur.execute(f"""SELECT count(*) AS n FROM (
                              SELECT *, row_number() OVER (PARTITION BY {", ".join(R.PERF_DUP_KEY)}
                                  ORDER BY {col} {"ASC" if sprint else "DESC"}, result_id) AS dn
                              FROM ranking_results WHERE {cw} {where}) q
                            WHERE dn = 1 AND ({col} {before} %(k1)s::float8
                                  OR ({col} = %(k1)s::float8 AND result_id < %(k2)s))""", params)
        assert cur.fetchone()["n"] == s["pos"], s
        checked.add(s["fam"])
    assert any("|both|" in fam for fam in checked)
    assert any(fam.startswith("ability|") and "|*|" in fam for fam in checked)
    # ties really were there: some key is shared by two rows of a board
    cur.execute("""SELECT count(*) AS n FROM (
                     SELECT speed_rating, result_id FROM ranking_results
                     WHERE pool = 'hs_m' AND speed_rating IS NOT NULL
                     GROUP BY 1, 2 HAVING count(DISTINCT sport) > 1) q""")
    assert cur.fetchone()["n"] > 0


def test_a_table_from_another_build_is_not_read(pg):
    import rankings as R
    _conn, cur = pg
    _fixture(cur)
    _buildSeek(cur)
    f = _f("board=performance&pool=hs_m&sport=XC&offset=100")
    R._SEEK_FRESH.update(at=0.0, ok=False)
    assert R._seekPoint(cur, dict(f))[0] == "seek"
    cur.execute("COMMENT ON TABLE board_seek IS 'rr_oid=1;as_oid=2;sig=x'")
    R._SEEK_FRESH.update(at=0.0, ok=False)
    assert R._seekPoint(cur, dict(f)) is None
    # a swap inside the minute _seekFresh is cached: the lookup itself checks
    R._SEEK_FRESH.update(at=1e18, ok=True)
    assert R._seekPoint(cur, dict(f)) is None
    R._SEEK_SIG["sig"] = "another-code"               # a deploy that moved a filter
    cur.execute("SELECT 'ranking_results'::regclass::oid AS a, 'athlete_season'::regclass::oid AS b")
    row = cur.fetchone()
    cur.execute("COMMENT ON TABLE board_seek IS %s",
                (f"rr_oid={row['a']};as_oid={row['b']};sig=old-code",))
    R._SEEK_FRESH.update(at=0.0, ok=False)
    assert R._seekPoint(cur, dict(f)) is None
