"""A professional's late-summer track races close the season behind them
(owner-approved, 2026-11-10: Grant Fisher's Lausanne, Zurich, Brussels and
Diamond League final, Aug 21 - Sep 11 2026, headed "2027 Track ·
professional"). School and college athletes keep the Aug 15 seam."""
import os
import sys

import pytest

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in ("engine", "racecast", "scripts"):
    sys.path.insert(0, os.path.join(_ROOT, p))
import season_year as SY  # noqa: E402

DSN = os.environ.get("XCP_TWIN_TEST_DSN")
FISHER, COLLEGE = 1, 2


@pytest.fixture
def pros():
    saved = SY._PRO["seasons"]
    SY.setProSeasons([(FISHER, 2025)])
    yield
    SY.setProSeasons(saved)


def test_the_python_forms(pros):
    assert SY.seasonYearForPerson("TF", "2026-08-21", FISHER) == 2025
    assert SY.seasonYearForPerson("TF", "2026-09-30", FISHER) == 2025
    assert SY.seasonYearForPerson("TF", "2026-10-01", FISHER) == 2026
    assert SY.seasonYearForPerson("XC", "2026-09-05", FISHER) == 2026
    assert SY.seasonYearForPerson("TF", "2026-08-21", COLLEGE) == 2026
    assert SY.seasonYearForPerson("TF", "2026-08-21", None) == 2026


def test_no_registered_seasons_is_the_plain_rule():
    saved = SY._PRO["seasons"]
    SY.setProSeasons(None)
    try:
        assert SY.seasonYearForPerson("TF", "2026-08-21", FISHER) == 2026
    finally:
        SY.setProSeasons(saved)


def test_the_page_label(pros):
    import app
    app._PRO_SUMMER["until"] = 1e18          # no database read in this test
    assert app.season_label("TF", "2026-08-21", FISHER) == "2026"
    assert app.season_label("TF", "2026-08-21", COLLEGE) == "2027"


def test_every_season_deciding_reader_passes_the_person():
    read = lambda *p: open(os.path.join(_ROOT, *p), encoding="utf-8").read()  # noqa: E731
    assert "seasonYearForPerson(sport, row.date, row.person_id)" in read("racecast", "build_ranking_results.py")
    assert "seasonYearForPersonDate(r[_SPORT], d, r[_PID], _rollover)" in read("engine", "speed_ratings.py")
    assert "seasonYearForPerson(sport, row.date, row.person_id)" in read("engine", "fill_ratings.py")
    assert 'season_label(race["sport"], race["date"], person_id)' in read("racecast", "app.py")
    assert "seasonYearForPerson(sport, row.get(\"date\"), row.get(\"person_id\"))" in read("racecast", "panels.py")


@pytest.mark.skipif(not DSN, reason="needs XCP_TWIN_TEST_DSN")
def test_the_sql_form_agrees():
    import psycopg2
    c = psycopg2.connect(DSN)
    try:
        cur = c.cursor()
        cur.execute("""CREATE TEMP TABLE pro_athlete_season (person_id bigint, season int,
                                                             pro_races int, round int);
                       INSERT INTO pro_athlete_season VALUES (1, 2025, 4, 0);
                       CREATE TEMP TABLE r (person_id bigint, sport text, date text);
                       INSERT INTO r VALUES (1, 'TF', '2026-08-21'), (1, 'TF', '2026-10-01'),
                                            (1, 'XC', '2026-09-05'), (2, 'TF', '2026-08-21'),
                                            (1, 'TF', '2026-03-01')""")
        cur.execute(f"SELECT person_id, sport, date, "
                    f"{SY.seasonYearSqlPersonInt('r.sport', 'r.date', 'r.person_id')} FROM r")
        got = cur.fetchall()
        SY.setProSeasons([(1, 2025)])
        for pid, sport, d, season in got:
            assert season == SY.seasonYearForPerson(sport, d, pid), (pid, sport, d, season)
    finally:
        SY.setProSeasons(None)
        c.rollback()
        c.close()
