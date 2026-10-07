"""The neighbour rule on a real Postgres (owner, 2026-09-29: Jack Moretta).

    XCP_TWIN_TEST_DSN="host=/tmp/pgtest port=54329 user=postgres dbname=<yours>" \
        python -m pytest -q tests/test_rating_outliers_pg.py

★ THE CASE. Athlete 29603084, Tufts. Bobby Doyle Classic, 9 Aug 2026, 80.4:
  "not real (easy LR)". Aldrich, 12 Sep 2026, 97.3: "real". Around them six
  2025 XC races and three spring 2026 track 5000s, one scale. The season rule
  saw a two-race 2026 season and had no opinion; the neighbour rule sees ten
  races with a median of 104.25 and flags the road race slow.

The corpus around him is 600 background athletes, two years each in both
sports, with a sprinkling of jogs and wrong distances -- enough that K, the
floor and both cuts are MEASURED here, not passed in.
"""
import contextlib
import os
import random
import sys

import _env  # noqa: F401  -- sets XCP_DB_PASSWORD, must precede config

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for d in ("scripts", "engine", "racecast"):
    p = os.path.join(ROOT, d)
    if p not in sys.path:
        sys.path.insert(0, p)

import rating_outliers as RO                                     # noqa: E402

MORETTA = 29603084
BOBBY_DOYLE, ALDRICH = 900001, 900002          # XC result ids
WRONG_DIST = 900101                            # the fast case (TF)
FAST_PERSON = 50000001
CROSS_PERSON, CROSS_JOG = 50000002, 900201     # a one-race season, slow
STEP_PERSON = 50000003                         # a real step up, across seasons
STEP_FIRST = 900301


def _pg():
    dsn = os.environ.get("XCP_TWIN_TEST_DSN")
    if not dsn:
        pytest.skip("set XCP_TWIN_TEST_DSN to a scratch Postgres")
    import importlib
    for name in [n for n in sys.modules if n == "psycopg2" or n.startswith("psycopg2.")]:
        if not hasattr(sys.modules[name], "__file__"):
            del sys.modules[name]
    return importlib.import_module("psycopg2").connect(dsn)


@contextlib.contextmanager
def _connect():
    conn = _pg()
    try:
        yield conn
    finally:
        conn.close()


XC_DATES = ("09-06", "09-20", "10-04", "10-18", "11-01", "11-15")
TF_DATES = ("03-14", "03-28", "04-11", "04-25", "05-09")


def _load(cur):
    cur.execute("""
        DROP TABLE IF EXISTS results, results_tf, result_twin,
             impossible_result, rating_outlier, rating_outlier_calib CASCADE;
        CREATE TABLE results (result_id bigint, person_id bigint, date text,
                              speed_rating double precision, meet_name text);
        CREATE TABLE results_tf (result_id bigint, person_id bigint,
                                 date text, speed_rating double precision,
                                 is_relay int, meet_name text);
        CREATE TABLE result_twin (sport text, result_id bigint, reason text);
        CREATE TABLE impossible_result (sport text, result_id bigint);
    """)
    rng = random.Random(29)
    xc, tf = [], []
    nid = [1]

    def add(rows, pid, date, rating, meet="", relay=None):
        rid = nid[0]
        nid[0] += 1
        if relay is None:
            rows.append((rid, pid, date, rating, meet))
        else:
            rows.append((rid, pid, date, rating, relay, meet))
        return rid

    # ★ THE BACKGROUND: ability, a steady drift, race-to-race noise of 2.5
    #   points, and the two kinds of thing the rule is for, at low rates.
    for p in range(1, 601):
        base, drift = rng.gauss(95, 8), rng.gauss(2, 2)
        for yr, season in ((2024, "XC"), (2025, "TF"), (2025, "XC")):
            dates = XC_DATES if season == "XC" else TF_DATES
            for md in dates:
                t = (yr - 2024) + int(md[:2]) / 12.0
                r = base + drift * t + rng.gauss(0, 2.5)
                u = rng.random()
                if u < 0.01:
                    r -= rng.uniform(20, 35)           # a jog
                elif u < 0.013:
                    r += rng.uniform(30, 45)           # a wrong distance
                if season == "XC":
                    add(xc, p, f"{yr}-{md}", r)
                else:
                    add(tf, p, f"{yr}-{md}", r, relay=0)
        # a relay leg, rated wildly: never judged, never a neighbour
        add(tf, p, "2025-04-11", 160.0, relay=1)

    # ★ MORETTA, as the owner gave him.
    for md, r in zip(XC_DATES, (97.2, 104.8, 105.1, 104.7, 105.7, 101.0)):
        add(xc, MORETTA, f"2025-{md}", r)
    for md, r in zip(("04-04", "04-18", "05-02"), (104.6, 103.9, 102.9)):
        add(tf, MORETTA, f"2026-{md}", r, relay=0)
    xc.append((BOBBY_DOYLE, MORETTA, "2026-08-09", 80.4, "Bobby Doyle Classic"))
    xc.append((ALDRICH, MORETTA, "2026-09-12", 97.3, "Aldrich Invitational"))

    # ★ THE FAST CASE: a steady track athlete with one mark at the wrong
    #   distance, forty points over everything around it.
    for i, md in enumerate(XC_DATES):
        add(xc, FAST_PERSON, f"2025-{md}", 92.0 + (i % 3) * 0.8)
    for i, md in enumerate(TF_DATES):
        if md == "04-11":
            tf.append((WRONG_DIST, FAST_PERSON, "2026-04-11", 134.0, 0, ""))
        else:
            add(tf, FAST_PERSON, f"2026-{md}", 93.0 + (i % 2), relay=0)

    # ★ ACROSS THE SEASON LINE: a full 2025 XC season, then ONE race in the
    #   2026 calendar year, a jog on New Year's Day. The season rule had no
    #   season to measure it against; its neighbours are last autumn's.
    for i, md in enumerate(XC_DATES):
        add(xc, CROSS_PERSON, f"2025-{md}", 99.0 + (i % 3))
    xc.append((CROSS_JOG, CROSS_PERSON, "2026-01-01", 72.0, "Resolution Run"))

    # ★ AND THE STEP THE RULE MUST NOT TAKE FOR AN ERROR: an athlete who
    #   comes back from the winter nine points better and stays there. The
    #   first race of the new level has the new level on its far side.
    for i, md in enumerate(XC_DATES):
        add(xc, STEP_PERSON, f"2025-{md}", 90.0 + (i % 2))
    tf.append((STEP_FIRST, STEP_PERSON, "2026-03-14", 99.5, 0, ""))
    for i, md in enumerate(TF_DATES[1:]):
        add(tf, STEP_PERSON, f"2026-{md}", 99.0 + (i % 2), relay=0)

    import psycopg2.extras
    psycopg2.extras.execute_values(
        cur, "INSERT INTO results VALUES %s", xc)
    psycopg2.extras.execute_values(
        cur, "INSERT INTO results_tf VALUES %s", tf)


@pytest.fixture(scope="module")
def judged():
    conn = _pg()
    with conn.cursor() as cur:
        _load(cur)
        conn.commit()
        # ★ THREE SHARDS ON TWO CONNECTIONS: the pipeline's path.
        rows, cal = RO.run(cur, shards=3, connect=_connect, streams=2)
        RO.dropStage(cur)
        RO.write(cur, rows, cal)
        conn.commit()
    yield conn, {(r[1], r[0]): r for r in rows}, cal
    conn.close()


def test_measured_not_given(judged):
    _conn, _rows, cal = judged
    assert cal["k"] in RO.K_CANDIDATES
    assert 1.5 < cal["floor"] < 5.0, cal["floor"]
    lo, hi = RO.OWNER_RANGE
    assert lo <= cal["slow_cut"] <= hi and lo <= cal["fast_cut"] <= hi
    assert cal["n_judged"] > 10000


def test_moretta_bobby_doyle_is_slow_and_aldrich_is_not(judged):
    _conn, rows, cal = judged
    bd = rows.get(("XC", BOBBY_DOYLE))
    assert bd is not None, "the easy long run must be flagged"
    assert bd[9] == "slow"
    assert bd[7] >= RO.MIN_NEIGHBOURS
    assert 103.0 < bd[5] < 105.0, "judged against his neighbours' ~104"
    assert ("XC", ALDRICH) not in rows, "Aldrich is real"


def test_the_fast_case(judged):
    _conn, rows, _cal = judged
    r = rows.get(("TF", WRONG_DIST))
    assert r is not None and r[9] == "fast"


def test_across_the_season_line(judged):
    _conn, rows, _cal = judged
    r = rows.get(("XC", CROSS_JOG))
    assert r is not None and r[9] == "slow", "a one-race season is judged"
    assert r[3] == 2026 and r[7] >= RO.MIN_NEIGHBOURS
    assert not any(k[1] == STEP_FIRST for k in rows), \
        "a real step up has the new level on its far side"


def test_relays_are_never_judged_or_neighbours(judged):
    conn, rows, _cal = judged
    with conn.cursor() as cur:
        cur.execute("SELECT result_id FROM results_tf WHERE is_relay = 1")
        relays = {r[0] for r in cur.fetchall()}
    assert not any(k == "TF" and rid in relays for k, rid in rows)


def test_the_python_rule_agrees_with_the_sql(judged):
    """The K nearest by date, picked and judged in Python, give the SQL's
    median and z for the road race."""
    import datetime as dt
    _conn, rows, cal = judged
    bd = rows[("XC", BOBBY_DOYLE)]
    career = ([(f"2025-{md}", r) for md, r in
               zip(XC_DATES, (97.2, 104.8, 105.1, 104.7, 105.7, 101.0))]
              + [(f"2026-{md}", r) for md, r in
                 zip(("04-04", "04-18", "05-02"), (104.6, 103.9, 102.9))]
              + [("2026-09-12", 97.3)])
    day = dt.date(2026, 8, 9)
    near = sorted(career, key=lambda c: abs(
        (dt.date.fromisoformat(c[0]) - day).days))[:cal["k"]]
    med, _s, _sigma, z = RO.neighbourZ(80.4, [r for _d, r in near],
                                       cal["floor"])
    assert abs(med - bd[5]) < 1e-4 and abs(z - bd[8]) < 1e-3
    assert z <= -cal["slow_cut"]


def test_explain_mode(judged, capsys):
    conn, _rows, cal = judged
    with conn.cursor() as cur:
        got = RO.explain(cur, MORETTA, cal["k"], cal["floor"],
                         cal["fast_cut"], cal["slow_cut"])
    conn.rollback()
    by = {g["result_id"]: g for g in got if g["sport"] == "XC"}
    assert by[BOBBY_DOYLE]["side"] == "slow"
    assert by[ALDRICH]["side"] is None
    out = capsys.readouterr().out
    assert "neighbours:" in out and "SLOW" in out


def test_calibration_is_stored_for_explain(judged):
    conn, _rows, cal = judged
    with conn.cursor() as cur:
        got = RO.latestCalib(cur)
    assert got["k"] == cal["k"] and abs(got["floor"] - cal["floor"]) < 1e-9


# ★ THE SEASON NUMBER, END TO END. The boards' own anti-join takes the slow
#   row out of the load; athlete_season's own aggregate then reads Aldrich
#   alone. With the road race in, the 80th percentile of {80.4, 97.3} is
#   93.9 -- the number the owner saw.
def test_the_season_number_leaves_the_slow_row_out(judged):
    conn, _rows, _cal = judged
    import build_ranking_results as B
    clause = B._outlierClause(conn, "XC")
    assert "rating_outlier ro" in clause
    with conn.cursor() as cur:
        cur.execute("""
            DROP TABLE IF EXISTS ro_load, ro_season;
            CREATE TABLE ro_season (person_id bigint, pool text, sport text,
                year int, mean_rating real, decayed_rating real,
                best_rating real, n_races int, first_race date,
                last_race date, state text, school text, grade text)""")
        cur.execute(f"""
            CREATE TABLE ro_load AS
            SELECT r.person_id, 'college_m'::text AS pool, 'XC'::text AS sport,
                   2026 AS year, r.speed_rating::real AS speed_rating,
                   r.date::date AS race_date, 'MA'::text AS state,
                   'Tufts'::text AS school, NULL::text AS division,
                   'JR-3'::text AS grade
            FROM results r
            WHERE r.person_id = {MORETTA} AND r.date >= '2026-01-01'
            {clause}""")
        cur.execute("SELECT count(*) FROM ro_load")
        assert cur.fetchone()[0] == 1
        cur.execute(B._ATHLETE_SEASON_SQL.format(load_table="ro_load",
                                                 season_table="ro_season",
                                                 shard_where="TRUE",
                                                 shard_where_base="TRUE"))
        cur.execute("SELECT mean_rating, n_races FROM ro_season")
        rating, n = cur.fetchone()
    conn.rollback()
    assert n == 1 and abs(rating - 97.3) < 1e-3
