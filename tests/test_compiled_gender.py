"""Compiled races group a row by its RACE's gender (owner, 2026-09-29, NCAA
DI 2024: men under a woman's profile made a "Girls 10000m", runners with no
gender a "- 10000m"). Division label first, then the field's 9:1 vote,
the athlete only in a genuinely mixed race."""
import os
import sys

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in ("engine", "scripts", "racecast"):
    sys.path.insert(0, os.path.join(_ROOT, _p))

import meet_compile as MC                                       # noqa: E402

_FIXTURE = """
DROP TABLE IF EXISTS results, meets, meets_tfrrs, athletes;
CREATE TABLE results (result_id bigint, person_id bigint, athlete_name text, team_id bigint,
                      place int, time_seconds real, grade text, school text,
                      speed_rating real, div_id bigint, meet_id bigint, source text,
                      athlete_id bigint);  -- the real table has it; the name lookup reads it
CREATE TABLE meets (meet_id bigint, div_id bigint, source text, distance real, division text);
CREATE TABLE meets_tfrrs (meet_id bigint, sport text, division_distances jsonb);
CREATE TABLE athletes (athlete_id bigint, first_name text, last_name text, gender text,
                       person_id bigint);
INSERT INTO meets_tfrrs VALUES (1, 'XC',
  '{"10": {"distance": 10000, "div_name": "Men''s 10k"},
    "20": {"distance": 6000,  "div_name": "Women''s 6k"}}');
-- anet meet 2: "Varsity" says nothing; its field is all boys but one blank
INSERT INTO meets VALUES (2, 30, 'anet', 5000, 'Varsity'), (2, 40, 'anet', 5000, 'Open Mixed');
INSERT INTO athletes VALUES (1,'A','One','M'),(2,'B','Two','M'),(3,'C','Three','M'),
  (4,'April','Gienger','F'),(6,'D','Six','F'),(7,'E','Seven','F'),
  (11,'F','x','M'),(12,'G','x','M'),(13,'H','x','M'),(14,'I','x','M'),
  (21,'J','x','M'),(22,'K','x','F');
UPDATE athletes SET person_id = athlete_id;
INSERT INTO results VALUES
  (1,1,NULL,1,1,1700,'SR-4','A',120,10,1,'tfrrs'),
  (2,2,NULL,1,2,1710,'SR-4','A',120,10,1,'tfrrs'),
  (3,3,NULL,1,3,1720,'SR-4','A',120,10,1,'tfrrs'),
  (4,4,NULL,1,4,1781,'SR-4','Stanford',120,10,1,'tfrrs'),   -- a man under a woman's profile
  (5,NULL,'Unknown Man',1,5,1795,'SR-4','CBU',120,10,1,'tfrrs'),   -- no person
  (6,6,NULL,1,1,1160,'SR-4','B',120,20,1,'tfrrs'),
  (7,7,NULL,1,2,1170,'SR-4','B',120,20,1,'tfrrs'),
  (8,11,NULL,1,1,960,'12','C',100,30,2,'anet'),
  (9,12,NULL,1,2,970,'12','C',100,30,2,'anet'),
  (10,13,NULL,1,3,980,'12','C',100,30,2,'anet'),
  (11,14,NULL,1,4,990,'12','C',100,30,2,'anet'),
  (12,NULL,'Blank',1,5,995,'12','C',100,30,2,'anet'),
  (13,21,NULL,1,1,1000,'-','D',100,40,2,'anet'),
  (14,22,NULL,1,2,1100,'-','D',100,40,2,'anet');
"""


def test_the_division_decides():
    sql = MC.rowGenderSql()
    assert "m.division" in sql and "FIELD" not in sql
    assert f">= {MC._personGender().FIELD_SHARE} *" in sql


def test_compiled_index_on_a_scratch_database():
    import pytest
    dsn = os.environ.get("XCP_TWIN_TEST_DSN")
    if not dsn:
        pytest.skip("set XCP_TWIN_TEST_DSN to a scratch Postgres")
    import psycopg2
    conn = psycopg2.connect(dsn)
    cur = conn.cursor()
    cur.execute(_FIXTURE)
    got = {(g["distance"], g["gender"]): g["n_results"]
           for g in MC.compiledIndex(cur, 1, source="tfrrs")}
    assert got == {(10000, "M"): 5, (6000, "F"): 2}, got      # one race each, as run
    got = {(g["distance"], g["gender"]): g["n_results"]
           for g in MC.compiledIndex(cur, 2, source="anet")}
    # Varsity: four boys and a blank are one boys' race; the mixed race splits
    assert got == {(5000, "M"): 6, (5000, "F"): 1}, got
    conn.rollback()


def test_compiled_results_run_in_time_order():
    """The query said ORDER BY 9, 11 -- div_id and the NAME -- so every
    compiled race was alphabetical (NCAA DI 2024: Abel, Abraham, Adam)."""
    import pytest
    dsn = os.environ.get("XCP_TWIN_TEST_DSN")
    if not dsn:
        pytest.skip("set XCP_TWIN_TEST_DSN to a scratch Postgres")
    import psycopg2
    import psycopg2.extras
    conn = psycopg2.connect(dsn)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(_FIXTURE)
    groups = {(g["distance"], g["gender"]): g for g in MC.compiledResults(cur, 1, source="tfrrs")}
    men = groups[(10000, "M")]["results"]
    assert [r["time_seconds"] for r in men] == sorted(r["time_seconds"] for r in men)
    assert [r["place"] for r in men] == [1, 2, 3, 4, 5]
    assert [r["name"] for r in men][:2] == ["A One", "B Two"]
    conn.rollback()


def test_a_college_race_is_never_split_by_home_state():
    """NCAA DI 2025: "Butler (IN)" / "Butler (NC)" -- a tfrrs race's team is
    the college's own name, whatever its runners' home states."""
    class NoCur:
        def execute(self, *a, **k):
            raise AssertionError("a tfrrs race must not look anything up")
    rows = [{"school": "Butler", "person_id": i} for i in range(7)]
    MC.splitCollisionTeams(NoCur(), rows, source="tfrrs")
    assert {r["school"] for r in rows} == {"Butler"}
