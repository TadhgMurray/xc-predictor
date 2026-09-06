"""Issue 94 / 15 / 68: one physical race is flagged once, and every reader
anti-joins the flag.

    python -m pytest -q tests/test_twin_flag.py
"""
import io
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for d in ("scripts", "engine", "racecast"):
    sys.path.insert(0, os.path.join(ROOT, d))
for name in ("database", "config", "psycopg2", "psycopg2.extras", "psycopg2.errors"):
    sys.modules.setdefault(name, types.ModuleType(name))
sys.modules["database"].getConn = lambda: None
sys.modules["psycopg2"].extras = sys.modules["psycopg2.extras"]
sys.modules["psycopg2"].errors = sys.modules["psycopg2.errors"]

import twin_flag as TF                                           # noqa: E402


def read(*p):
    return io.open(os.path.join(ROOT, *p), encoding="utf-8").read()


def test_rules_key_on_the_right_things():
    race = TF.twinRaceSql("results", "XC")
    assert "a.place = t.place" in race and "round(t.time_seconds::numeric, 1)" in race
    assert "person_id" not in race.split("SELECT t.result_id")[1], \
        "the race rule is blind to who the rows are attached to"
    assert "t.source = 'tfrrs'" in race and "a.source = 'anet'" in race
    person = TF.twinPersonSql("results", "XC")
    assert "a.person_id = t.person_id" in person and "a.canon_meet_id = t.canon_meet_id" in person
    dup_xc = TF.dupSameFeedSql("results", "XC")
    dup_tf = TF.dupSameFeedSql("results_tf", "TF")
    assert "GROUP  BY person_id, source, meet_id, div_id, date" in dup_xc
    assert "event_id" not in dup_xc and "div_id, event_id" in dup_tf, "track adds the event"
    assert "min(result_id) AS keep" in dup_xc and "r.result_id <> k.keep" in dup_xc, \
        "the lowest result_id survives, every later copy goes"
    assert [r for r, _ in TF.RULES] == ["twin_race", "twin_person", "dup_same_feed",
                                        "dup_cross_date", "dup_race_copy"], \
        "cross-feed reasons file first; the primary key keeps the first"


def test_every_reader_anti_joins_the_flag():
    eng = read("engine", "speed_ratings_db.py")
    assert "LEFT JOIN result_twin rtw" in eng and "AND rtw.result_id IS NULL" in eng
    assert "_dedupJoin(tw, 'XC')" in eng and "_dedupJoin(tw, 'TF')" in eng
    assert "_dedupFilter(tw, 'XC')" in eng and "_dedupFilter(tw, 'TF')" in eng
    brr = read("racecast", "build_ranking_results.py")
    assert brr.count("FROM result_twin x") == 2 and "def ensureResultTwin" in brr
    fill = read("engine", "fill_ratings.py")
    assert "B.ensureResultTwin(conn)" in fill
    app = read("racecast", "app.py")
    assert "_hasResultTwin(cur)" in app and "{twin_xc}" in app and "{twin_tf}" in app
    sh = read("deploy", "run_pipeline.sh")
    assert "04c_twins" in sh and sh.index("04c_twins") < sh.index("07_pack")


def test_linker_refuses_a_gender_contradiction():
    src = read("scripts", "link_idless_by_name.py")
    assert "r.gender IS NULL OR u.gender IS NULL OR r.gender = u.gender" in src
    assert "AS gender" in src


# ---- the rules on a real Postgres, when one is offered ---------------- #
# tests/fixtures/twin_rules.sql builds every shape the rules exist for; the
# expected sets below were the OLD rules' answers on 2026-09-06, before the
# rewrite for speed (231), and the rewrite had to match them exactly.
#   XCP_TWIN_TEST_DSN="host=/tmp/pgtest port=54329 user=postgres dbname=postgres"
_EXPECTED = {
    ("XC", "twin_race"):      [701],
    ("XC", "twin_person"):    [702],
    ("XC", "dup_same_feed"):  [502],
    ("XC", "dup_cross_date"): [401, 402, 502],
    ("XC", "dup_race_copy"):  [201, 202, 203, 204, 205, 206, 207, 208],
    ("TF", "twin_race"):      [],
    ("TF", "twin_person"):    [],
    ("TF", "dup_same_feed"):  [1602],
    ("TF", "dup_cross_date"): [1101, 1102, 1103, 1104, 1105, 1106, 1107, 1108,
                               1401, 1402, 1403, 1502, 1602],
    ("TF", "dup_race_copy"):  [1101, 1102, 1103, 1104, 1105, 1106, 1107, 1108],
}


def test_rules_on_fixtures():
    import pytest
    dsn = os.environ.get("XCP_TWIN_TEST_DSN")
    if not dsn:
        pytest.skip("set XCP_TWIN_TEST_DSN to a scratch Postgres to run the rules")
    # the stubs above stand in for psycopg2 so the module imports without a
    # database; this test wants the real driver
    import importlib
    for name in [n for n in sys.modules if n == "psycopg2" or n.startswith("psycopg2.")]:
        if not hasattr(sys.modules[name], "__file__"):
            del sys.modules[name]
    psycopg2 = importlib.import_module("psycopg2")
    conn = psycopg2.connect(dsn)
    cur = conn.cursor()
    cur.execute(open(os.path.join(os.path.dirname(__file__), "fixtures",
                                  "twin_rules.sql"), encoding="utf-8").read())
    for sport, table in TF.TABLES.items():
        for reason, fn in TF.RULES:
            if reason == "dup_cross_date":
                TF.prepareCrossDate(cur, table, sport)   # the staged meet pairs (third cut)
            cur.execute(f"SELECT result_id FROM ({fn(table, sport)}) s ORDER BY 1")
            got = [r[0] for r in cur.fetchall()]
            assert got == _EXPECTED[(sport, reason)], (sport, reason, got)
    # and the build writes the union, earlier reasons winning the key
    TF.build(conn, write=True)
    cur.execute("SELECT reason, count(*) FROM result_twin GROUP BY 1 ORDER BY 1")
    by = dict(cur.fetchall())
    assert by["twin_race"] == 1 and by["twin_person"] == 1
    assert by["dup_same_feed"] == 2          # 502 and 1602 file here, not as cross-date
    conn.rollback()

