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
    assert "PARTITION BY person_id, source, meet_id, div_id," in dup_xc
    assert "event_id" not in dup_xc and "div_id, event_id" in dup_tf, "track adds the event"
    assert "ORDER BY result_id" in dup_xc and "rn > 1" in dup_xc
    assert [r for r, _ in TF.RULES] == ["twin_race", "twin_person", "dup_same_feed"], \
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
