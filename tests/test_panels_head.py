"""panels.py --head (2026-11-13): the season years, spans and homepage_recent
written FIRST, so 13f..13g can run beside the full panels builds; and the
one grouped scan (ratedScan) that replaced _yearCounts' and _ratedSpan's two.

Proves, on a scratch Postgres (XCP_TEST_PG_DSN; skipped without one):
  * ratedScan gives exactly the numbers the two old queries gave (their SQL
    is copied here verbatim), junk and edge years included;
  * --head writes exactly the keys and rows the full build wrote for them
    (season_year_<sport>, span_<sport>, homepage_recent), the full build's
    values computed the OLD way, through the full build's own writer;
  * --after-head reads back the same season and span, and its write leaves
    homepage_recent and those keys as the head wrote them.
"""
import contextlib
import datetime
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, d) for d in ("racecast", "scripts", "engine")]
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
DSN = os.environ.get("XCP_TEST_PG_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="XCP_TEST_PG_DSN not set")

# ---- the pre-2026-11-13 queries, verbatim ------------------------------- #
OLD_YEAR_COUNTS = """
        SELECT {season} AS yr, COUNT(*) AS n
        FROM   {table}
        WHERE  speed_rating IS NOT NULL
          AND  date ~ %s
          AND  substring(date, 1, 4) BETWEEN '1995' AND '2026'
        GROUP  BY 1
        ORDER  BY 1 DESC
        LIMIT  %s
    """
OLD_FACT_SPAN = """
    SELECT count(*)                    AS n,
           min(substring(date, 1, 4))  AS y0,
           max(substring(date, 1, 4))  AS y1
    FROM   {table}
    WHERE  speed_rating IS NOT NULL
      AND  date ~ %s
      AND  substring(date, 1, 4) BETWEEN '1990' AND '2036'
"""

TODAY = datetime.date.today()


def _d(days_ago):
    return (TODAY - datetime.timedelta(days=days_ago)).isoformat()


def _seed(cur):
    cur.execute("""
        DROP TABLE IF EXISTS results, results_tf, meets, meets_tf, meets_tfrrs,
                             homepage_meta, homepage_recent, homepage_panels,
                             pro_athlete_season;
        CREATE TABLE results (meet_id bigint, source text, date text,
                              time_seconds real, speed_rating real);
        CREATE TABLE results_tf (meet_id bigint, date text, time_seconds real,
                                 mark real, speed_rating real);
        CREATE TABLE meets (meet_id bigint, source text, meet_name text,
                            course_name text, state text);
        CREATE TABLE meets_tf (meet_id bigint, meet_name text, state text,
                               is_indoor int);
        CREATE TABLE meets_tfrrs (meet_id bigint, sport text, meet_name text,
                                  venue_name text, state text);
    """)
    # every year 1988..2037 with a different count, a Nov/Dec slice (TF rolls
    # it into the season ahead), unrated rows, malformed and junk dates
    for table in ("results", "results_tf"):
        cols = ("(meet_id, source, date, time_seconds, speed_rating)"
                if table == "results" else
                "(meet_id, date, time_seconds, mark, speed_rating)")
        cur.execute(f"""
            INSERT INTO {table} {cols}
            SELECT 1, {"'anet', " if table == "results" else ""}
                   y::text || CASE WHEN g % 5 = 0 THEN '-12-02'
                                   WHEN g % 3 = 0 THEN '-09-20'
                                   ELSE '-04-11' END,
                   900, {"NULL, " if table == "results_tf" else ""}
                   CASE WHEN g % 11 = 0 THEN NULL ELSE 100 END
            FROM   generate_series(1988, 2037) y,
                   generate_series(1, 20 + ((y * 7) % 13) * 9) g
        """)
        for junk in ("2222-10-01", "2223-01-01", "0023-05-05", "1899-01-01",
                     "garbage", "", "2019-1-5"):
            vals = ((1, "anet", junk, 900, 100) if table == "results"
                    else (1, junk, 900, None, 100))
            cur.execute(f"INSERT INTO {table} {cols} VALUES %s", (vals,))
    # recent meets: distinct dates (the SQL orders by date alone)
    for i in range(1, 41):
        n = 20 + i                          # some below RECENT_MIN_RESULTS
        src = "tfrrs" if i % 4 == 0 else "anet"
        cur.execute("""INSERT INTO results (meet_id, source, date, time_seconds, speed_rating)
                       SELECT %s, %s, %s, 950, NULL FROM generate_series(1, %s)""",
                    (1000 + i, src, _d(i * 3), n))
        if src == "anet":
            cur.execute("INSERT INTO meets VALUES (%s, 'anet', %s, %s, 'OR')",
                        (1000 + i, None if i % 7 == 0 else f"XC Meet {i}", f"Course {i}"))
        else:
            cur.execute("INSERT INTO meets_tfrrs VALUES (%s, 'XC', %s, %s, 'WA')",
                        (1000 + i, f" TF Feed Meet {i} " if i % 8 else " ", f"Venue {i}"))
        cur.execute("""INSERT INTO results_tf (meet_id, date, time_seconds, mark, speed_rating)
                       SELECT %s, %s, NULL, 5.5, NULL FROM generate_series(1, %s)""",
                    (2000 + i, _d(i * 5), n))
        cur.execute("INSERT INTO meets_tf VALUES (%s, %s, 'CA', %s)",
                    (2000 + i, f"Track Meet {i}", i % 2))
    # a future junk date beyond today + 2 and one too old for the window
    cur.execute("INSERT INTO results (meet_id, source, date, time_seconds) "
                "SELECT 9001, 'anet', '2223-10-01', 900 FROM generate_series(1, 50)")
    cur.execute("INSERT INTO meets VALUES (9001, 'anet', 'Junk', 'x', 'OR')")


@pytest.fixture()
def db(monkeypatch):
    import psycopg2
    import panels
    conn = psycopg2.connect(DSN)
    with conn.cursor() as cur:
        _seed(cur)
    conn.commit()

    @contextlib.contextmanager
    def fakeGetConn():
        c = psycopg2.connect(DSN)
        try:
            yield c
        finally:
            c.close()
    monkeypatch.setattr(panels, "getConn", fakeGetConn)
    yield conn
    conn.rollback()
    conn.close()


def _oldYearCounts(conn, sport):
    import panels
    table = "results" if sport == "XC" else "results_tf"
    with conn.cursor() as cur:
        cur.execute(OLD_YEAR_COUNTS.format(season=panels.seasonYearSql(sport, "date"),
                                           table=table),
                    (panels._SANE_YEAR, panels.SEASON_LOOKBACK))
        return cur.fetchall()


def _oldSpan(conn, sport):
    import panels
    table = "results" if sport == "XC" else "results_tf"
    with conn.cursor() as cur:
        cur.execute(OLD_FACT_SPAN.format(table=table), (panels._SANE_YEAR,))
        n, y0, y1 = cur.fetchone()
    return (n or 0, int(y0) if y0 else None, int(y1) if y1 else None)


@pytest.mark.parametrize("sport", ["XC", "TF"])
def test_merged_scan_gives_the_old_two_queries_numbers(db, sport):
    import panels
    scan = panels.ratedScan(db, sport)
    assert panels._yearCounts(db, sport, scan) == _oldYearCounts(db, sport)
    assert panels._yearCounts(db, sport) == _oldYearCounts(db, sport)
    assert panels._ratedSpan(db, sport, scan) == _oldSpan(db, sport)
    assert panels._ratedSpan(db, sport) == _oldSpan(db, sport)
    # and the year it picks
    old = panels.pickSeason(_oldYearCounts(db, sport), sport, TODAY,
                            lambda yr, c: panels._rowsToDate(db, sport, yr, c))
    assert panels._seasonYear(db, sport, scan) == old


def test_merged_scan_on_an_empty_table(db):
    import panels
    with db.cursor() as cur:
        cur.execute("TRUNCATE results")
    assert panels._yearCounts(db, "XC") == _oldYearCounts(db, "XC") == []
    assert panels._ratedSpan(db, "XC") == _oldSpan(db, "XC") == (0, None, None)


def _snapshot(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT key, value FROM homepage_meta "
                    "WHERE key LIKE 'season\\_year\\_%' OR key LIKE 'span\\_%' ORDER BY key")
        meta = cur.fetchall()
        cur.execute("SELECT * FROM homepage_recent ORDER BY sport, rank")
        recent = cur.fetchall()
    return meta, recent


def _oldFullBuildWrite(conn, sports):
    """What the full build wrote for these keys before --head: its old
    season and span queries, its _recentMeets, through _writePanels."""
    import panels
    meta, recent = {}, []
    for sport in sports:
        sy = panels.pickSeason(_oldYearCounts(conn, sport), sport, TODAY,
                               lambda yr, c, s=sport: panels._rowsToDate(conn, s, yr, c))
        label = sy
        if sport == "TF" and sy:
            label = str(int(sy) + 1)
        meta[f"season_year_{sport}"] = label or ""
        n, y0, y1 = _oldSpan(conn, sport)
        meta[f"span_{sport}"] = f"{int(n)},{y0 or ''},{y1 or ''}"
        recent += panels._recentMeets(conn, sport)
    meta["built_at"] = "x"
    panels._writePanels(conn, {}, meta, recent, sports=sports)


def test_head_writes_exactly_what_the_full_build_wrote(db):
    import panels
    _oldFullBuildWrite(db, ["XC", "TF"])
    full = _snapshot(db)
    assert full[1] and len(full[0]) == 4
    with db.cursor() as cur:
        cur.execute("DELETE FROM homepage_meta; DELETE FROM homepage_recent")
        # a stale row the head must replace, and another key it must not touch
        cur.execute("INSERT INTO homepage_recent VALUES ('XC', 999, 1, 'old', '', '', '2000-01-01', 1)")
        cur.execute("INSERT INTO homepage_meta VALUES ('fact_results', '9M')")
    db.commit()
    panels.buildHead(["XC", "TF"])
    assert _snapshot(db) == full
    with db.cursor() as cur:
        cur.execute("SELECT value FROM homepage_meta WHERE key = 'fact_results'")
        assert cur.fetchone()[0] == "9M"
        cur.execute("SELECT count(*) FROM homepage_meta")
        assert cur.fetchone()[0] == 5      # 4 head keys + the untouched one


def test_one_sport_head_leaves_the_other_sport(db):
    import panels
    _oldFullBuildWrite(db, ["XC", "TF"])
    full = _snapshot(db)
    panels.buildHead(["TF"])
    assert _snapshot(db) == full


def test_after_head_reads_the_head_back_and_writes_none_of_it(db):
    import panels
    meta, recent = panels.buildHead(["XC", "TF"])
    head = _snapshot(db)
    got = panels.readHead(db, ["XC", "TF"])
    for sport in ("XC", "TF"):
        scan = panels.ratedScan(db, sport)
        assert got[sport] == (panels._seasonYear(db, sport, scan),
                              panels._ratedSpan(db, sport, scan))
        assert panels.seasonLabel(sport, got[sport][0]) == meta[f"season_year_{sport}"]
    # the full build's meta in --after-head: no season or span keys, and its
    # write keeps the head's homepage_recent
    facts = panels._siteFacts(db, ["XC"], spans={"XC": got["XC"][1]})
    assert not any(k.startswith("span_") for k in facts)
    full_meta = {"built_at": "y", "default_sport": "XC", **facts}
    panels._writePanels(db, {}, full_meta, [], sports=["XC"], recent_sports=[])
    assert _snapshot(db) == head


def test_after_head_without_a_head_computes_it(db):
    import panels
    with db.cursor() as cur:
        cur.execute(panels._DDL)
        cur.execute("INSERT INTO homepage_meta VALUES ('season_year_XC', '2025')")  # no span_XC
    db.commit()
    assert panels.readHead(db, ["XC", "TF"]) == {}


def test_label_round_trip():
    import panels
    for sport in ("XC", "TF"):
        for sy in ("2024", "2025", None):
            assert panels.seasonFromLabel(sport, panels.seasonLabel(sport, sy)) == sy
    assert panels.seasonLabel("TF", "2025") == "2026"
    assert panels.spanFromText(panels.spanText((5, 1990, None))) == (5, 1990, None)
