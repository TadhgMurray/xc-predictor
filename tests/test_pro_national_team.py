"""03 pro_flag: a country-named team at a senior international championship
is a national team, and one race makes the season professional (owner,
2026-09-29: N'Tyamba "Angola IN" at the 1992 Olympics on the all-time high
school list). Angola High School at a dual meet, a junior championship and
a US junior at the Worlds stay as they were."""
import os
import re
import sys

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in ("engine", "scripts"):
    sys.path.insert(0, os.path.join(_ROOT, _p))

import pro_flag as PF                                          # noqa: E402

_FIXTURE = """
DROP TABLE IF EXISTS results, results_tf, meets, meets_tf;
CREATE TABLE results (person_id bigint, meet_id bigint, div_id bigint, school text, date text);
CREATE TABLE results_tf (person_id bigint, meet_id bigint, div_id bigint, school text, date text);
CREATE TABLE meets (meet_id bigint, div_id bigint, meet_name text);
CREATE TABLE meets_tf (meet_id bigint, div_id bigint, meet_name text);
INSERT INTO meets_tf VALUES (1, 1, 'Games of the XXV Olympiad'), (2, 1, 'NE8 Dual Meet'),
                            (3, 1, 'World U20 Championships'), (4, 1, 'IAAF World Championships');
INSERT INTO results_tf VALUES
    (10, 1, 1, 'Angola',       '1992-08-09'),   -- the Olympian
    (11, 2, 1, 'Angola',       '2024-04-09'),   -- Angola HS, Indiana
    (12, 3, 1, 'Jamaica',      '2024-07-20'),   -- a junior championship
    (13, 4, 1, 'USA',          '2013-08-12'),   -- a US junior at the Worlds
    (14, 4, 1, 'Russia (RUS)', '1995-08-10'),   -- suffixed country
    (15, 4, 1, 'Kenya National Team', '1995-08-10');
"""


def test_patterns():
    ok = lambda n: bool(re.search(PF._SENIOR_CHAMP, n, re.I)) and not re.search(
        PF._SENIOR_EXCLUDE, n, re.I)
    assert ok("Games of the XXV Olympiad") and ok("IAAF World Championships")
    assert ok("Pan American Games") and ok("Commonwealth Games")
    for n in ("World U20 Championships", "AAU Junior Olympic Games", "World University Games",
              "Pan American Junior Championships", "Special Olympics World Games",
              "World Masters Athletics Championships"):
        assert not ok(n), n
    names = PF.nationalTeamNames()
    assert "angola" in names and "kenya" in names and "usa" not in names


def test_seed_on_a_scratch_database():
    import pytest
    dsn = os.environ.get("XCP_TWIN_TEST_DSN")
    if not dsn:
        pytest.skip("set XCP_TWIN_TEST_DSN to a scratch Postgres")
    import psycopg2
    conn = psycopg2.connect(dsn)
    cur = conn.cursor()
    cur.execute(_FIXTURE)
    cur.execute(PF.nationalTeamSeedSql("results_tf", "meets_tf"),
                (PF._SENIOR_CHAMP, PF._SENIOR_EXCLUDE, PF._NT_STRIP, PF.nationalTeamNames()))
    got = sorted(r[0] for r in cur.fetchall())
    conn.rollback()
    assert got == [10, 14, 15], got
