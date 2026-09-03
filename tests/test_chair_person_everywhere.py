"""Chair athletes are excluded BY PERSON on the boards, in fill_ratings and
in the checklist, through wheelchair_person (2026-09-03). The engine had
the person-level rule since 04b; the pricer and the boards had only the
division labels, and the athletes the wider rule refused came back with
flat ratings. Text checks, no database.

    python -m pytest -q tests/test_chair_person_everywhere.py
"""
import io
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*p):
    return io.open(os.path.join(ROOT, *p), encoding="utf-8").read()


def test_boards_anti_join_by_person_in_both_sports():
    src = read("racecast", "build_ranking_results.py")
    i = src.index("_SQL = {")
    body = src[i:src.index("_SWAP_LOCK_TIMEOUT", i)]
    anti = re.findall(r"NOT EXISTS \(SELECT 1 FROM wheelchair_person wc\s+"
                      r"WHERE wc\.person_id = r\.person_id\)", body)
    assert len(anti) == 2, "one anti-join per sport"
    # after the twin anti-join in each half, inside the WHERE the pricer
    # inverts -- the mark fill_ratings replaces must survive untouched
    assert "WHERE r.speed_rating IS NOT NULL" in body
    assert body.index("x.sport = 'XC'") < body.index("wheelchair_person wc")
    assert "def ensureWheelchairPerson(conn)" in src
    main = src[src.index("def main():"):]
    assert main.index("ensureWheelchairPerson(conn)") < main.index("buildSport(conn")


def test_fill_ratings_ensures_the_table_before_inverting():
    src = read("engine", "fill_ratings.py")
    i = src.index("def main():")
    body = src[i:]
    assert body.index("B.ensureWheelchairPerson(conn)") < body.index("fillSport(conn")


def test_ensure_table_exists_and_the_engine_treats_empty_as_absent():
    wf = read("engine", "wheelchair_flag.py")
    assert "def ensureTable(cur)" in wf
    assert "CREATE TABLE IF NOT EXISTS wheelchair_person" in wf
    db = read("engine", "speed_ratings_db.py")
    i = db.index("def _chairFilter()")
    body = db[i:db.index("\ndef ", i + 1)]
    assert "SELECT count(*) FROM wheelchair_person" in body
    assert "_CHAIR_READY = cur.fetchone()[0] > 0" in body


def test_checklist_reads_the_person_list():
    src = read("scripts", "run_checklist.py")
    i = src.index("def checkWheelchairPeople(cur)")
    body = src[i:src.index("\ndef ", i + 1)]
    assert 'if _exists(cur, "wheelchair_person")' in body
    assert "SELECT w.person_id, NULL, NULL FROM wheelchair_person w" in body
    assert body.index("FROM wheelchair_person w") < body.index("bad = []")


def test_athlete_page_blanks_difficulty_on_a_corrected_division():
    src = read("racecast", "app.py")
    i = src.index("-- ================= XC half: results + meets")
    body = src[i:src.index("-- ================= TF half", i)]
    assert "THEN NULL" in body and "ELSE cd.difficulty END   AS difficulty" in body
    assert "abs(dov.distance::real" in body
