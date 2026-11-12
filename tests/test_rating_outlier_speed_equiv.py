"""09d's speed-ups write the same rating_outlier rows as the old path.

    XCP_TEST_PG_DSN="postgresql://postgres:pw@localhost/<scratch db>" \\
        python -m pytest -q tests/test_rating_outlier_speed_equiv.py

(XCP_TWIN_TEST_DSN works too.) The old path: the calibration sample taken
after the window, the rules one after another on the main connection after
the judging, one INSERT a row. The new: the sample before the window, the
rules on their own connections from the start, the inserts in pages. One
synthetic corpus, the same starting rating_outlier, every column compared.
"""
import contextlib
import os
import random
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in ("engine", "scripts"):
    sys.path.insert(0, os.path.join(_ROOT, _p))
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")

import rating_outliers as RO  # noqa: E402

DSN = os.environ.get("XCP_TEST_PG_DSN") or os.environ.get("XCP_TWIN_TEST_DSN")


def _pg():
    import importlib
    for name in [n for n in sys.modules if n == "psycopg2" or n.startswith("psycopg2.")]:
        if not hasattr(sys.modules[name], "__file__"):
            del sys.modules[name]
    return importlib.import_module("psycopg2").connect(DSN)


@contextlib.contextmanager
def _connect():
    c = _pg()
    try:
        yield c
    finally:
        c.close()


_SCHEMA = """
    DROP TABLE IF EXISTS results, results_tf, meets, person_gender, athlete_named,
         race_day_suspect_division, result_twin, impossible_result,
         rating_outlier, rating_outlier_calib, ro_seed CASCADE;
    CREATE TABLE results (result_id bigint, person_id bigint, source text, meet_id bigint,
                          div_id bigint, date text, speed_rating real, rating_pool text,
                          grade text, time_seconds real, is_relay int, athlete_name text);
    CREATE TABLE results_tf (result_id bigint, person_id bigint, date text,
                             speed_rating real, is_relay int);
    CREATE TABLE meets (div_id bigint, meet_id bigint, distance real);
    CREATE TABLE person_gender (person_id bigint PRIMARY KEY, gender text, split boolean);
    CREATE TABLE athlete_named (person_id bigint PRIMARY KEY, name text);
    CREATE TABLE race_day_suspect_division (sport text, meet_id bigint, div_id bigint,
                                            log_excess double precision, excess double precision,
                                            sample_result_id bigint);
    CREATE TABLE result_twin (sport text, result_id bigint, reason text);
    CREATE TABLE impossible_result (sport text, result_id bigint);
"""

_MEN = ("Henry", "Tucker", "Aksel", "Malav")
_WOMEN = ("Emma", "Olivia", "Grace", "Nora")
_BOTH = ("Taylor", "Jordan")
_LEVEL_BASE = {"ms": 95.0, "hs": 115.0, "college": 135.0}
_LEVEL_M = {"ms": 3000.0, "hs": 5000.0, "college": 8000.0}


def _corpus(cur):
    """Persons racing a season of meets, with every kind of row the rules
    are for planted at low rates."""
    import psycopg2.extras
    rng = random.Random(1112)
    cur.execute(_SCHEMA)
    xc, tf, meets, genders, names = [], [], [], [], []
    rid = [0]

    def nid():
        rid[0] += 1
        return rid[0]

    # the meets: 40 meet-days, a division per level and gender, distances known
    divs = {}
    div_id = 0
    for m in range(1, 41):
        yr = 2023 + (m % 2)
        day = f"{yr}-{9 + m % 3:02d}-{1 + (m * 7) % 27:02d}"
        for lvl in ("ms", "hs", "college"):
            for g in ("M", "F"):
                div_id += 1
                dist = _LEVEL_M[lvl] if g == "M" or lvl != "college" else 6000.0
                # a few labels shorter than the course
                if m % 13 == 0 and lvl == "ms":
                    dist = dist * 0.75
                meets.append((div_id, m, dist))
                divs[(m, lvl, g)] = (div_id, day, dist)
    persons = []
    for p in range(1, 901):
        g = "M" if p % 2 else "F"
        lvl = ("ms", "hs", "college")[p % 3]
        first = rng.choice(_BOTH) if p % 11 == 0 else rng.choice(_MEN if g == "M" else _WOMEN)
        names.append((p, f"{first} P{p}"))
        genders.append((p, g, False))
        persons.append((p, g, lvl, _LEVEL_BASE[lvl] + rng.gauss(0, 8) + (6 if g == "M" else 0)))
    for p, g, lvl, base in persons:
        n_races = 2 if p % 37 == 0 else rng.randint(5, 12)
        for m in rng.sample(range(1, 41), n_races):
            d, day, dist = divs[(m, lvl, g)]
            r = base + rng.gauss(0, 3)
            u = rng.random()
            if u < 0.02:
                r *= 1.45                       # a wrong distance
            elif u < 0.04:
                r *= 0.7                        # a jog
            if dist < _LEVEL_M[lvl] * 0.9:
                r *= 1.15                       # a short course under a long label
            grade = {"ms": "8", "hs": "11", "college": "SR-4"}[lvl]
            if lvl == "ms" and p % 29 == 0:
                grade = "2026"                  # a graduation year: 10th grade
            if lvl == "ms" and p % 31 == 0:
                grade = ""
            t = dist / (r / 25.0)
            src = "anet" if m % 5 else "tfrrs"
            xc.append((nid(), p, src, m, d, day, r, f"{lvl}_{g.lower()}|XC", grade, t, 0,
                       names[p - 1][1]))
            if p % 23 == 0 and r >= 125:
                # the same time a fortnight later, rated far lower
                xc.append((nid(), p, src, m + 100, d + 1000, day[:8] + "28", r * 0.75,
                           f"{lvl}_{g.lower()}|XC", grade, t * 1.001, 0, names[p - 1][1]))
        for k in range(rng.randint(0, 4)):
            tf.append((nid(), p, f"2024-0{3 + k}-15", base + rng.gauss(0, 3), 0))
        if p % 17 == 0:
            tf.append((nid(), p, "2024-04-20", 190.0, 1))       # a relay
    # a combined list: college men filed in the college women's division of meet 3
    d, day, dist = divs[(3, "college", "F")]
    for p, g, lvl, base in persons:
        if g == "M" and lvl == "college" and p % 7 == 0:
            xc.append((nid(), p, "anet", 3, d, day, base * 1.2, "college_f|XC", "SR-4",
                       dist / (base / 25.0), 0, names[p - 1][1]))
    # tiny fields: two runners in a division of their own
    for i, (p, g, lvl, base) in enumerate(persons[:60]):
        xc.append((nid(), p, "anet", 500 + i // 2, 5000 + i // 2, "2023-10-30",
                   base * (1.3 if i % 4 == 0 else 1.0), f"{lvl}_{g.lower()}|XC",
                   "8", 900.0, 0, names[p - 1][1]))
    psycopg2.extras.execute_values(cur, "INSERT INTO results VALUES %s", xc)
    psycopg2.extras.execute_values(cur, "INSERT INTO results_tf VALUES %s", tf)
    psycopg2.extras.execute_values(cur, "INSERT INTO meets VALUES %s", meets)
    psycopg2.extras.execute_values(cur, "INSERT INTO person_gender VALUES %s", genders)
    psycopg2.extras.execute_values(cur, "INSERT INTO athlete_named VALUES %s", names)
    cur.execute("""
        INSERT INTO race_day_suspect_division
        SELECT 'XC', meet_id, div_id, -0.08, NULL, NULL FROM meets WHERE meet_id IN (4, 9);
        INSERT INTO result_twin SELECT 'XC', result_id, 'twin' FROM results WHERE result_id % 97 = 0;
        INSERT INTO impossible_result SELECT 'TF', result_id FROM results_tf WHERE result_id % 89 = 0;
    """)
    # last night's table: some fast rows (no one's neighbour), some rule rows
    cur.execute(RO.DDL)
    cur.execute("""
        INSERT INTO rating_outlier (result_id, sport, person_id, season, rating, med, side)
        SELECT result_id, 'XC', person_id, 2023, speed_rating, NULL,
               CASE WHEN result_id % 2 = 0 THEN 'fast' ELSE 'race' END
        FROM results WHERE result_id % 53 = 0;
        CREATE TABLE ro_seed AS SELECT * FROM rating_outlier;
    """)


def _pass(conn, new):
    """One --write pass, the old or the new way, from the seeded table."""
    RO.CAL_SAMPLE_FIRST = new
    RO.BATCH_INSERT = new
    cur = conn.cursor()
    cur.execute("TRUNCATE rating_outlier; INSERT INTO rating_outlier SELECT * FROM ro_seed;"
                "TRUNCATE rating_outlier_calib")
    conn.commit()
    rules = RO.ExtraRules(cur, connect=_connect if new else None, streams=4 if new else 1)
    try:
        # ! THE CUTS GIVEN, so this small corpus flags slow rows too; the
        #   derived cuts and the histogram they come from are compared below
        rows, cal = RO.run(cur, shards=3, connect=_connect, streams=2,
                           fast_sigma=6.0, slow_sigma=6.0)
        extra = rules.collect()
    finally:
        rules.close()
    RO.write(cur, rows, cal)
    RO.writeExtra(cur, extra)
    RO.dropStage(cur)
    conn.commit()
    cur.execute("""SELECT result_id, sport, person_id, season, rating, med, sigma, n_races,
                          z, side, span_days, cut
                   FROM rating_outlier ORDER BY result_id, sport""")
    return cur.fetchall(), cal, len(extra)


@pytest.mark.skipif(not DSN, reason="needs XCP_TEST_PG_DSN or XCP_TWIN_TEST_DSN")
def test_the_new_path_writes_the_old_paths_rows(monkeypatch):
    monkeypatch.setattr(RO, "CAL_ROWS", 1500)     # a sample of 1 in several, as on the box
    monkeypatch.setattr(RO, "INSERT_PAGE", 97)    # many pages, keys repeated across them
    conn = _pg()
    try:
        with conn.cursor() as cur:
            _corpus(cur)
        conn.commit()
        try:
            old, cal_old, n_old = _pass(conn, new=False)
            new, cal_new, n_new = _pass(conn, new=True)
        finally:
            RO.CAL_SAMPLE_FIRST = True
            RO.BATCH_INSERT = True
    finally:
        conn.rollback()
        with conn.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS ro_seed")
        conn.commit()
        conn.close()
    assert cal_old["detail"]["sample_mod"] > 1
    assert (cal_old["k"], cal_old["floor"]) == (cal_new["k"], cal_new["floor"])
    for key in ("curve", "hist", "fast_derived", "slow_derived", "persons"):
        assert cal_old["detail"][key] == cal_new["detail"][key], key
    assert cal_old["n_judged"] == cal_new["n_judged"]
    assert n_old == n_new
    assert old == new
    sides = {r[9] for r in old}
    # the rules' sides and the neighbourhood's, and rows two rules both name
    assert {"fast", "slow", "mixed", "race", "day", "field", "level"} <= sides, sides
    assert n_old > len([r for r in old if r[6] is None])   # some keys named twice


class _FakeCur:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, *a, **k):
        pass


class _FakeConn:
    def cursor(self):
        return _FakeCur()

    def rollback(self):
        pass

    def cancel(self):
        pass


def test_the_rules_keep_their_order_and_a_failure_surfaces(monkeypatch):
    import time as _t

    @contextlib.contextmanager
    def connect():
        yield _FakeConn()

    def slow(cur):
        _t.sleep(0.2)
        return [("slow",)]

    def quick(cur):
        return [("quick",)]

    def boom(cur):
        raise RuntimeError("boom")

    monkeypatch.setattr(RO, "slowRule", slow, raising=False)
    monkeypatch.setattr(RO, "quickRule", quick, raising=False)
    monkeypatch.setattr(RO, "boomRule", boom, raising=False)
    monkeypatch.setattr(RO, "EXTRA_RULES", (("slow", "slowRule"), ("quick", "quickRule")))
    assert RO.ExtraRules(None, connect=connect, streams=4).collect() == [("slow",), ("quick",)]
    assert RO.ExtraRules(None, streams=1).collect() == [("slow",), ("quick",)]
    monkeypatch.setattr(RO, "EXTRA_RULES", (("slow", "slowRule"), ("boom", "boomRule")))
    rules = RO.ExtraRules(None, connect=connect, streams=4)
    with pytest.raises(RuntimeError):
        try:
            rules.collect()
        finally:
            rules.close()
