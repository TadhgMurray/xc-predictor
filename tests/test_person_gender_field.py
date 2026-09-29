"""04d: a runner whose division names no gender takes the field's (2026-09-29:
45 college first-years normalised on the women's anchor at the Bates Preview)."""
import os
import sys

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in ("engine", "scripts"):
    sys.path.insert(0, os.path.join(_ROOT, _p))

import person_gender as PG                                     # noqa: E402

_FIXTURE = """
DROP TABLE IF EXISTS results, results_tf, meets, meets_tfrrs, athletes,
                     person_gender, person_gender_new, person_gender_changed;
CREATE TABLE results (result_id bigint, person_id bigint, athlete_id bigint, source text,
                      meet_id bigint, div_id bigint);
CREATE TABLE results_tf (result_id bigint, person_id bigint, athlete_id bigint, source text,
                         event_short text);
CREATE TABLE meets (meet_id bigint, div_id bigint, division text);
CREATE TABLE meets_tfrrs (meet_id bigint, division_distances jsonb);
CREATE TABLE athletes (athlete_id bigint, gender text);
CREATE TABLE person_gender (person_id bigint, gender text, n_m int, n_f int, split bool);
INSERT INTO meets VALUES (1, 11, 'Collegiate'), (2, 21, 'Collegiate'), (3, 31, 'Varsity');
INSERT INTO athletes VALUES (101,'M'),(102,'M'),(103,'M'),(201,'F'),(202,'F'),(301,'M'),(302,'F');
INSERT INTO results VALUES (1,101,101,'anet',1,11),(2,102,102,'anet',1,11),(3,103,103,'anet',1,11),
                           (4,900,999,'anet',1,11),
                           (5,201,201,'anet',2,21),(6,202,202,'anet',2,21),(7,901,998,'anet',2,21),
                           (8,301,301,'anet',3,31),(9,302,302,'anet',3,31),(10,902,997,'anet',3,31);
"""


def test_the_field_vote_is_in_the_build():
    assert "count(*) FILTER (WHERE fa.gender = 'M') AS fm" in PG._BUILD
    assert f"f.fm >= {PG.FIELD_MIN} AND f.fm >= {PG.FIELD_SHARE} * f.ff" in PG._BUILD


def test_field_vote_on_a_scratch_database():
    import pytest
    dsn = os.environ.get("XCP_TWIN_TEST_DSN")
    if not dsn:
        pytest.skip("set XCP_TWIN_TEST_DSN to a scratch Postgres")
    import importlib
    psycopg2 = importlib.import_module("psycopg2")
    conn = psycopg2.connect(dsn)
    cur = conn.cursor()
    cur.execute(_FIXTURE)
    cur.execute(PG._BUILD)
    cur.execute("SELECT person_id, gender FROM person_gender_new WHERE person_id >= 900")
    got = dict(cur.fetchall())
    conn.rollback()
    assert got == {900: "M"}, got     # 901: two known runners; 902: a mixed field
