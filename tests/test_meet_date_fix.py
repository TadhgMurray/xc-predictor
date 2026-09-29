"""An anet meet stored under the wrong year goes back to its own year (the
first level_conflict server run, 2026-09-29: meet 227716, a 2023 Middlesex
League race dated 2025-10-26, put four runners' own high school race on their
college seasons).

    python -m pytest -q tests/test_meet_date_fix.py

The arithmetic is pure and tested here; the survey, the vote, the record, the
move and the undo run on a real Postgres when XCP_TWIN_TEST_DSN names one.
"""
import datetime
import io
import os
import sys

import _env  # noqa: F401  -- sets XCP_DB_PASSWORD, must precede config

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for d in ("scripts", "engine"):
    p = os.path.join(ROOT, d)
    if p not in sys.path:
        sys.path.insert(0, p)

import meet_date_fix as MD                                       # noqa: E402

BASE = datetime.date(2023, 8, 1)


def read(*p):
    return io.open(os.path.join(ROOT, *p), encoding="utf-8").read()


def listings(n=600):
    """n meets in id order, four listed a day from 2023-08-01, a few days of
    jitter -- the shape anet's ids have: roughly chronological."""
    out = []
    for i in range(n):
        day = BASE + datetime.timedelta(days=i // 4 + (i * 7) % 11 - 5)
        out.append([1000 + i, day.isoformat(), day.isoformat(), 10])
    return out


# ---- the arithmetic ------------------------------------------------------ #

def test_the_neighbourhood_leaves_the_meet_out_and_slides_at_the_ends():
    days = list(range(0, 1000, 10))                     # 100 meets, 10 days apart
    nb = MD.neighbourDays(days, k=10)
    assert nb[50] == 500, "five each side of meet 50: median 500 without itself"
    assert nb[0] == 55, "the first meet's window is the next ten"
    assert nb[99] == 935
    days[50] = 99999                                    # one wild meet
    nb = MD.neighbourDays(days, k=10)
    assert nb[50] == 500, "its own wild date is not its neighbourhood"
    assert 510 <= nb[51] <= 530, "a median: one wild neighbour moves it one rank, no more"
    assert list(MD.neighbourDays([5, 7], k=200)) == [7, 5], "fewer meets than k"


def test_the_cut_is_halfway_from_the_normal_spread_to_a_year():
    shape = MD.measure([0] * 50 + [10] * 49 + [30, 2000])
    assert shape["n"] == 101
    assert 10 <= shape["spread"] <= 30
    assert shape["cut"] == (shape["spread"] + 365.25) / 2
    assert shape["separable"]
    loose = MD.measure([200] * 100)
    assert not loose["separable"], "a spread of half a year leaves no cut"
    assert MD.measure([]) == {"n": 0, "separable": False}


def test_only_a_whole_number_of_years_is_corrected():
    assert MD.yearShift(771, 60) == 2, "2025-10-26 against a 2023-09 neighbourhood"
    assert MD.yearShift(-1461, 30) == -4
    assert MD.yearShift(250, 30) is None, "not near a whole year"
    assert MD.yearShift(20, 30) is None, "no year to move"
    assert MD.shiftDay("2025-10-26", -2) == "2023-10-26"
    assert MD.shiftDay("2024-02-29", -1) == "2023-02-28", "as Postgres does"


def test_the_survey_finds_the_wrong_year_and_reports_what_it_cannot_fix():
    meets = listings()
    meets[300][1:3] = ["2025-09-30", "2025-09-30"]     # the review's shape
    meets[100][1:3] = ["2000-04-15", "2000-04-15"]     # a placeholder
    meets[450][1:3] = ["2023-01-01", "2024-01-01"]     # rows a year apart
    meets.append([2000, "TBA", "TBA", 3])              # not a date: skipped
    shape, rows = MD.survey(meets)
    assert shape["n"] == 600 and shape["separable"]
    assert shape["spread"] < 60, shape
    by = {r["meet_id"]: r for r in rows}
    assert set(by) == {1100, 1300, 1450}, "nothing normal is past the cut"
    fix = by[1300]
    assert fix["shift"] == 2 and fix["fixed"] == "2023-09-30"
    assert fix["neighbour"].startswith("2023-10"), "listed mid-October 2023"
    assert by[1100]["fixed"] is None
    assert by[1100]["reason"] == "no whole number of years brings it home"
    assert by[1450]["reason"] == "its rows' dates disagree with each other"


def test_the_athletes_grades_must_agree():
    ok, why, ff, fs, n = MD.vote([2023] * 7 + [2025], 2025, 2023)
    assert ok and (ff, fs, n) == (7, 1, 8)
    # an old season uploaded late: the grades say the stored year is right
    ok, why, *_ = MD.vote([2019] * 5, 2019, 2023)
    assert not ok and "stored year" in why
    ok, why, *_ = MD.vote([2023, 2023, 2025, 2025, 2024], 2025, 2023)
    assert not ok, "no majority"
    ok, why, *_ = MD.vote([], 2025, 2023)
    assert not ok and "no evidence" in why
    assert MD.seasonOf("2025-10-26") == 2025 and MD.seasonOf("2026-04-01") == 2025


# ---- the wiring ----------------------------------------------------------- #

def test_it_runs_first_and_is_not_always_run():
    sh = read("deploy", "run_pipeline.sh")
    assert 'step 00_meet_dates    "$PY" -u engine/meet_date_fix.py --write' in sh
    assert sh.index("step 00_meet_dates") < sh.index("step 01_season_year") \
        < sh.index("step 04c_twins")
    always = [l for l in sh.splitlines() if l.startswith("_ALWAYS=")][0]
    assert "00_meet_dates" not in always, "a --from run keeps last run's dates"


def test_only_anet_rows_inside_the_stored_days_move():
    sql = MD._moveSql("results", "date", "AND f.status = 'applied'",
                      "stored_first", "stored_last", "f.years",
                      "AND t.source = 'anet'")
    assert "make_interval(years => f.years)" in sql
    assert "BETWEEN f.stored_first AND f.stored_last" in sql, "idempotent"
    assert "AND t.source = 'anet'" in sql, "a tfrrs row with the same id is not this meet"
    assert "|| substr(t.date::text, 11)" in sql
    as_date = MD._moveSql("meets", "meet_date", "", "a", "b", "1", "", True)
    assert "::date" in as_date and "to_char" not in as_date


# ---- on a real Postgres, when one is offered ------------------------------ #
#   XCP_TWIN_TEST_DSN="host=/tmp/pgtest port=54329 user=postgres dbname=<yours>"

def _pg():
    import pytest
    dsn = os.environ.get("XCP_TWIN_TEST_DSN")
    if not dsn:
        pytest.skip("set XCP_TWIN_TEST_DSN to a scratch Postgres")
    import importlib
    for name in [n for n in sys.modules if n == "psycopg2" or n.startswith("psycopg2.")]:
        if not hasattr(sys.modules[name], "__file__"):
            del sys.modules[name]
    return importlib.import_module("psycopg2").connect(dsn)


def _load(cur):
    cur.execute("""
        DROP TABLE IF EXISTS results, results_tf, meets, meets_tf_meta, meet_date_fix;
        CREATE TABLE results (result_id bigint, person_id bigint, source text,
          meet_id bigint, date text, grade text);
        CREATE TABLE results_tf (LIKE results);
        CREATE TABLE meets (div_id bigint, meet_id bigint, meet_name text, meet_date text);
        CREATE TABLE meets_tf_meta (meet_id bigint, meet_name text, meet_date text,
          source text);""")
    rid = 0
    meets = listings()
    meets[300][1] = "2025-09-30"               # meet 1300: the Middlesex League
    meets[400][1] = "2019-11-09"               # meet 1400: an old season, uploaded late
    rows, mt = [], []
    for mid, day, _l, _n in meets:
        mt.append((mid, mid, f"Meet {mid}", day))
        for k in range(3):                     # three ungraded runners each
            rid += 1
            rows.append((rid, None, "anet", mid, day, None))
    mt[300] = (1300, 1300, "Middlesex League Championship", "2025-09-30")
    # meet 1300's juniors and seniors, each with another race that autumn
    for p in range(6):
        g = "12" if p % 2 else "11"
        rid += 1
        rows.append((rid, 500 + p, "anet", 1300, "2025-09-30", g))
        rid += 1
        rows.append((rid, 500 + p, "anet", 1100 + p, "2023-08-26", g))
    rows.append((9999, 777, "tfrrs", 1300, "2025-09-30", "SO-2"))   # not anet
    # meet 1400's seniors raced track that spring: the stored 2019 is right
    tf = []
    for p in range(4):
        rid += 1
        rows.append((rid, 600 + p, "anet", 1400, "2019-11-09", "12"))
        rid += 1
        tf.append((rid, 600 + p, "anet", 70, "2020-04-18", "12"))
    cur.executemany("INSERT INTO results VALUES (%s, %s, %s, %s, %s, %s)", rows)
    cur.executemany("INSERT INTO results_tf VALUES (%s, %s, %s, %s, %s, %s)", tf)
    cur.executemany("INSERT INTO meets VALUES (%s, %s, %s, %s)", mt)
    cur.execute("INSERT INTO meets_tf_meta VALUES (70, 'Spring Relays', '2020-04-18', 'anet')")


def _dates(cur, meet_id, source="anet"):
    cur.execute("SELECT DISTINCT date FROM results WHERE meet_id = %s AND source = %s",
                (meet_id, source))
    return sorted(r[0] for r in cur.fetchall())


def test_survey_record_apply_and_undo_on_postgres():
    conn = _pg()
    try:
        cur = conn.cursor()
        _load(cur)
        found = MD.examine(cur)
        rows = {r["meet_id"]: r for r in found["XC"][1]}
        assert set(rows) == {1300, 1400}
        fix, old = rows[1300], rows[1400]
        assert fix["apply"] and fix["fixed"] == "2023-09-30"
        assert (fix["for_fixed"], fix["for_stored"], fix["voters"]) == (6, 0, 6)
        assert fix["name"] == "Middlesex League Championship"
        assert not old["apply"] and "stored year" in old["why"], \
            "an old season with a new id keeps its date"
        MD.printReport(found)                              # it prints, no crash
        assert MD.record(cur, found) == 1
        conn.commit()
        MD.apply(conn)
        assert _dates(cur, 1300) == ["2023-09-30"]
        assert _dates(cur, 1300, "tfrrs") == ["2025-09-30"], "only anet rows move"
        assert _dates(cur, 1400) == ["2019-11-09"]
        cur.execute("SELECT meet_date FROM meets WHERE meet_id = 1300")
        assert cur.fetchone()[0] == "2023-09-30", "the meet's own date moves too"
        # idempotent: the survey no longer sees it, and nothing moves again
        found = MD.examine(cur)
        assert {r["meet_id"] for r in found["XC"][1]} == {1400}
        assert MD.record(cur, found) == 0
        cur.execute("UPDATE results SET date = '2025-09-30' "
                    "WHERE meet_id = 1300 AND source = 'anet' AND person_id = 500")
        conn.commit()                                  # a re-scrape writes it back
        MD.apply(conn)
        assert _dates(cur, 1300) == ["2023-09-30"], "the record re-applies it"
        # undo: back to the stored year, and no later run re-applies it
        MD.undo(conn, "XC", 1300)
        assert _dates(cur, 1300) == ["2025-09-30"]
        cur.execute("SELECT status FROM meet_date_fix WHERE meet_id = 1300")
        assert cur.fetchone()[0] == "reverted"
        found = MD.examine(cur)
        assert MD.record(cur, found) == 0
        conn.commit()
        MD.apply(conn)
        assert _dates(cur, 1300) == ["2025-09-30"], "a reverted meet stays as stored"
    finally:
        conn.rollback()
        cur = conn.cursor()
        cur.execute("DROP TABLE IF EXISTS results, results_tf, meets, "
                    "meets_tf_meta, meet_date_fix")
        conn.commit()
        conn.close()
