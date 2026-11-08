"""The recruit page's college placements (recruiting.collegePlacements),
2026-11-08: one shared read per division board and a binary search per
recruit, in place of two counts per division per view. Pinned here: the
rank and total are exactly what the old per-recruit SQL counted, ties and
all, and a second view reads the shared copy instead of the database.

    XCP_TWIN_TEST_DSN="host=... dbname=postgres" runs the SQL half
"""
import os
import random
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "scripts"), os.path.dirname(os.path.abspath(__file__))):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import _env  # noqa: E402,F401

import pytest  # noqa: E402

import recruiting as R  # noqa: E402


@pytest.fixture
def pg(monkeypatch, tmp_path):
    dsn = os.environ.get("XCP_TWIN_TEST_DSN")
    if not dsn:
        pytest.skip("set XCP_TWIN_TEST_DSN to a scratch Postgres to run the SQL")
    import psycopg2
    import psycopg2.extras
    import sharedcache
    monkeypatch.setattr(sharedcache, "DIR", str(tmp_path / "shared"))
    monkeypatch.setattr(R, "_STAMP", {"at": 0.0, "oid": None})
    # the scale maps and the unit probe read the site's own pool; here the
    # HS scale is the college scale and athlete_season carries `division`
    import pool_view
    import rankings
    monkeypatch.setattr(pool_view, "fromHs", lambda h, pool, sport=None: float(h))
    monkeypatch.setattr(pool_view, "toHs", lambda r, pool, sport=None: float(r))
    monkeypatch.setattr(rankings, "_rowHasUnit", lambda col, table="ranking_results": col == "division")
    conn = psycopg2.connect(dsn)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("DROP SCHEMA IF EXISTS t_recruit CASCADE; CREATE SCHEMA t_recruit;"
                " SET search_path TO t_recruit")
    # mean_rating is double precision, as percentile_cont writes it
    cur.execute("""CREATE TABLE athlete_season (
        person_id bigint, pool text, sport text, year int,
        mean_rating double precision, best_rating double precision,
        n_races int, last_race date, school text, state text, grade text,
        division text, region text, conference text, state_div text,
        class text, section text, section_div text, league text)""")
    rnd = random.Random(11)
    rows = []
    pid = 0
    for pool in ("college_m", "college_f"):
        for sport in ("XC", "TF"):
            for year in (2024, 2025):
                for div in R.COLLEGE_DIVISIONS + ("NJCAA",):
                    for _ in range(120):
                        pid += 1
                        # coarse values so many seasons tie exactly
                        rating = round(rnd.uniform(800, 1200) / 5) * 5 if rnd.random() < 0.95 else None
                        rows.append((pid, pool, sport, year, rating, rating,
                                     rnd.choice((1, 2, 3, 5)), f"School {pid % 40}",
                                     "CA", div))
    psycopg2.extras.execute_values(cur, """INSERT INTO athlete_season
        (person_id, pool, sport, year, mean_rating, best_rating, n_races,
         school, state, division) VALUES %s""", rows)
    yield conn, cur
    conn.rollback()
    cur.execute("DROP SCHEMA IF EXISTS t_recruit CASCADE")
    conn.commit()
    conn.close()


def _oldPlacements(cur, sport, hs_pool, rating, label_year):
    """The per-recruit SQL collegePlacements ran before 2026-11-08."""
    from werkzeug.datastructures import MultiDict
    from rankings import parseFilters, countOf, _whereClauses
    from pool_view import fromHs
    from season_floor import floorSql
    college = "college_m" if hs_pool.endswith("_m") else "college_f"
    equiv = fromHs(rating, college, sport)
    if not equiv:
        return []
    cur.execute("""SELECT max(year) AS y FROM athlete_season
                   WHERE pool = %s AND sport = %s AND year <= %s
                     AND mean_rating IS NOT NULL""",
                (college, sport, R.storedYear(label_year, sport)))
    stored = cur.fetchone()["y"]
    out = []
    for div in R.COLLEGE_DIVISIONS:
        f, err = parseFilters(MultiDict({"board": "ability", "pool": college, "sport": sport,
                                         "year": str(stored), "division": div}))
        assert not err
        params = {"min_races": f["min_races"], "equiv": equiv}
        where = _whereClauses(f, params, with_dates=False)
        with cur.connection.cursor() as plain:
            plain.execute(f"""SELECT count(*) FROM athlete_season s
                              WHERE {floorSql(f['min_races_explicit'])} {where}
                                AND s.mean_rating > %(equiv)s""", params)
            above = plain.fetchone()[0]
            total = countOf(plain, f)
        if total:
            out.append((div, int(above) + 1, int(total)))
    return out


def test_placements_match_the_old_counts_ties_included(pg):
    _conn, cur = pg
    from pool_view import toHs
    cur.execute("SELECT DISTINCT mean_rating FROM athlete_season"
                " WHERE pool = 'college_m' AND mean_rating IS NOT NULL ORDER BY 1")
    exact = [r["mean_rating"] for r in cur.fetchall()]
    for sport in ("XC", "TF"):
        # HS ratings that land exactly on a college season's value (a tie:
        # > must not count it) and ones between and beyond the board
        probes = [toHs(v, "college_m", sport) for v in exact[::7]] + [0.0, 500.0, 2000.0, 1003.3]
        label = R.labelYear(2025, sport)
        for hs in probes:
            if hs is None:
                continue
            new = [(p["division"], p["rank"], p["total"])
                   for p in R.collegePlacements(cur, sport, "hs_m", hs, label)]
            assert new == _oldPlacements(cur, sport, "hs_m", hs, label), (sport, hs)
            assert len(new) == (4 if hs else 0)


def test_a_second_view_reads_the_shared_copy(pg):
    _conn, cur = pg
    label = R.labelYear(2025, "XC")
    first = R.collegePlacements(cur, "XC", "hs_f", 1000.0, label)
    seen = []
    real = cur.connection.cursor

    class Spy:
        def __init__(self, c):
            self._c = c

        def __getattr__(self, k):
            return getattr(self._c, k)

        def __enter__(self):
            self._c.__enter__()
            return self

        def __exit__(self, *a):
            return self._c.__exit__(*a)

        def execute(self, sql, *a):
            seen.append(sql)
            return self._c.execute(sql, *a)

    class ConnSpy:
        def __getattr__(self, k):
            return getattr(_conn, k)

        def cursor(self, *a, **kw):
            return Spy(real(*a, **kw))

    class CurSpy:
        connection = ConnSpy()

        def __getattr__(self, k):
            return getattr(cur, k)

    again = R.collegePlacements(CurSpy(), "XC", "hs_f", 1000.0, label)
    assert again == first
    assert not [s for s in seen if "count(" in s or "mean_rating" in s], seen


def test_a_rebuilt_table_is_a_new_copy(pg):
    _conn, cur = pg
    label = R.labelYear(2025, "XC")
    before = R.collegePlacements(cur, "XC", "hs_m", 1000.0, label)
    cur.execute("CREATE TABLE athlete_season_new AS SELECT * FROM athlete_season")
    cur.execute("UPDATE athlete_season_new SET mean_rating = 5000 WHERE pool = 'college_m'"
                " AND sport = 'XC' AND year = 2025 AND n_races >= 3 AND mean_rating IS NOT NULL")
    cur.execute("DROP TABLE athlete_season; ALTER TABLE athlete_season_new RENAME TO athlete_season")
    R._STAMP["at"] = 0.0                       # the minute's memo has passed
    after = R.collegePlacements(cur, "XC", "hs_m", 1000.0, label)
    assert [p["rank"] for p in after] != [p["rank"] for p in before]
    assert [(p["division"], p["rank"], p["total"]) for p in after] == \
        _oldPlacements(cur, "XC", "hs_m", 1000.0, label)
