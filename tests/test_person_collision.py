"""person_collision: a person id a newer anet athlete also owns (owner,
2026-09-29: Cole Sprout's Stanford career under "April Gienger")."""
import os
import sys

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in ("engine", "scripts"):
    sys.path.insert(0, os.path.join(_ROOT, _p))

import person_collision as PC                                   # noqa: E402

_FIXTURE = """
DROP TABLE IF EXISTS results, results_tf, athletes, person_link_log;
CREATE TABLE results (result_id bigint, person_id bigint, athlete_id bigint, source text,
                      athlete_name text, date text);
CREATE TABLE results_tf (result_id bigint, person_id bigint, athlete_id bigint, source text,
                         athlete_name text, date text);
CREATE TABLE athletes (athlete_id bigint, first_name text, last_name text, school text,
                       person_id bigint);
INSERT INTO athletes VALUES
    (12267305, 'Cole', 'Sprout', 'Stanford', 32773608),
    (32773608, 'April', 'Gienger', 'Valley Royals', 32773608),
    (500, 'Jane', 'Smith', 'Oak HS', 500),                 -- married name on tfrrs
    (700, 'Sam', 'Lee', 'Elm HS', 700),                    -- a normal link
    (32542417, 'Ronan', 'McMahon-Staggs', 'Washington', 32791413),
    (32791413, 'Regan', 'Holmes', 'Walker-Grant', 32791413),
    (4000, 'Pat', 'Kim', 'Ash HS', 4000),
    (294720, 'Hanna', 'Mosley', 'Oak HS', 294720),        -- a typo, one person
    (9100, 'Old', 'Timer', 'Elm HS', 9100);                -- older than the rest
INSERT INTO results VALUES
    (1, 32773608, 12267305, 'anet', NULL, '2024-09-27'),
    (2, 32773608, NULL, 'tfrrs', 'Cole Sprout', '2024-09-27'),
    (3, 32773608, 12267305, 'anet', NULL, '2024-10-19'),
    (4, 500, 500, 'anet', NULL, '2020-10-01'),
    (5, 500, NULL, 'tfrrs', 'Jane Smith-Doe', '2023-10-01'),
    (6, 700, 700, 'anet', NULL, '2021-10-01'),
    (7, 700, NULL, 'tfrrs', 'Sam Lee', '2022-10-01'),
    (8, 32791413, 32542417, 'anet', NULL, '2024-09-03'),
    (9, 32791413, NULL, 'tfrrs', 'Ronan McMahon-Staggs', '2024-09-03'),
    (10, 4000, 4000, 'anet', NULL, '2024-10-01'),
    (20, 294720, 294720, 'anet', NULL, '2012-10-01'),
    (21, 294720, NULL, 'tfrrs', 'Hannah Mosely', '2015-10-01'),
    (22, 294720, NULL, 'tfrrs', 'Hannah Mosely', '2015-11-01'),
    (30, 9100, 9100, 'anet', NULL, '2010-10-01'),
    (31, 9100, NULL, 'tfrrs', 'Someone Else', '2015-10-01'),
    (32, 9100, NULL, 'tfrrs', 'Someone Else', '2015-11-01');
INSERT INTO results_tf VALUES
    (11, 32773608, 32773608, 'anet', NULL, '2026-07-03'),
    (12, 32791413, 32791413, 'anet', NULL, '2026-03-21'),
    (13, 32542417, 999, 'anet', NULL, '2020-01-01');   -- 32542417 is taken as a person
"""


def test_one_typo_is_the_same_name():
    assert PC.namesNear(["hanna", "mosley"], ["hannah", "mosely"])
    assert PC.namesNear(["jon"], ["john"])
    assert not PC.namesNear(["cole", "sprout"], ["april", "gienger"])
    assert not PC.namesNear(["regan", "holmes"], ["ronan", "mcmahon", "staggs"])


def test_tokens_and_sql_shape():
    assert "regexp_split_to_array" in PC.tokensSql("x")
    sql = PC.gatherSql()
    assert "NOT (nt.toks && o.toks)" in sql and "n.n < o.n_other" in sql


def test_on_a_scratch_database():
    import pytest
    dsn = os.environ.get("XCP_TWIN_TEST_DSN")
    if not dsn:
        pytest.skip("set XCP_TWIN_TEST_DSN to a scratch Postgres")
    import psycopg2
    conn = psycopg2.connect(dsn)
    cur = conn.cursor()
    cur.execute(_FIXTURE)
    sus = PC.gather(cur)
    assert sorted(int(s[0]) for s in sus) == [32773608, 32791413], sus
    moves = PC.targets(cur, sus)
    assert moves[32773608] == 12267305          # Cole goes home to his own anet id
    assert moves[32791413] >= 2_000_000_000     # Ronan's own id is someone's person
    PC.write(cur, moves)
    cur.execute("SELECT result_id, person_id FROM results UNION ALL "
                "SELECT result_id, person_id FROM results_tf ORDER BY 1")
    got = dict(cur.fetchall())
    assert got[1] == got[2] == got[3] == 12267305
    assert got[11] == 32773608 and got[12] == 32791413     # the newcomers keep theirs
    assert got[8] == got[9] == moves[32791413]
    assert got[4] == got[5] == 500 and got[6] == got[7] == 700
    cur.execute("SELECT person_id FROM athletes WHERE athlete_id = 12267305")
    assert cur.fetchone()[0] == 12267305
    # nothing left to find, and undo puts it all back
    assert PC.gather(cur) == []
    PC.undo(cur)
    cur.execute("SELECT person_id FROM results WHERE result_id IN (1, 2, 8) ORDER BY result_id")
    assert [r[0] for r in cur.fetchall()] == [32773608, 32773608, 32791413]
    conn.rollback()
