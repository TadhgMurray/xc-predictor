"""twin_flag.dup_cross_gender (owner, 2026-11-05: Trey Davidson, 1st in the
men's 8K and 27th in the women's 6K at one 25:36.3). Needs a scratch
Postgres: XCP_TWIN_TEST_DSN."""
import os
import sys

import pytest

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in ("engine", "scripts"):
    sys.path.insert(0, os.path.join(_ROOT, p))
DSN = os.environ.get("XCP_TWIN_TEST_DSN")


@pytest.mark.skipif(not DSN, reason="no scratch database (XCP_TWIN_TEST_DSN)")
def test_the_copy_in_the_other_genders_race_is_flagged():
    import psycopg2
    import twin_flag as T
    c = psycopg2.connect(DSN)
    try:
        cur = c.cursor()
        cur.execute("CREATE TEMP TABLE results (result_id bigint, person_id bigint, source text, "
                    "meet_id bigint, div_id bigint, date text, athlete_name text, time_seconds real)")
        cur.execute("CREATE TEMP TABLE person_gender (person_id bigint, gender text, n_m int, "
                    "n_f int, split boolean)")
        rows, pg, rid = [], [], 1
        for i in range(10):
            rows.append((rid, 100 + i, "anet", 1, 56, "2026-10-31", f"Man {i}", 1500 + i))
            pg.append((100 + i, "M", 5, 0, False))
            rid += 1
            rows.append((rid, 200 + i, "anet", 1, 57, "2026-10-31", f"Woman {i}", 1350 + i))
            pg.append((200 + i, "F", 0, 5, False))
            rid += 1
        rows += [(900, 7, "anet", 1, 57, "2026-10-31", "Trey Davidson", 1536.3),   # the copy
                 (901, 8, "anet", 1, 56, "2026-10-31", "Trey Davidson", 1536.3),   # the run
                 (902, 9, "anet", 1, 56, "2026-10-31", "Pat Dnf", 999999),
                 (903, 10, "anet", 1, 57, "2026-10-31", "Pat Dnf", 999999)]
        pg += [(7, "M", 40, 0, False), (8, "F", 0, 1, False)]
        cur.executemany("INSERT INTO results VALUES (%s,%s,%s,%s,%s,%s,%s,%s)", rows)
        cur.executemany("INSERT INTO person_gender VALUES (%s,%s,%s,%s,%s)", pg)
        T.prepareRule(cur, "results", "XC", "dup_cross_gender")      # staged since 2026-11-10
        cur.execute(T.dupCrossGenderSql("results", "XC"))
        assert sorted(r[0] for r in cur.fetchall()) == [900]
        assert "WHERE false" in T.dupCrossGenderSql("results_tf", "TF")
    finally:
        c.rollback()
        c.close()


def test_registered():
    import twin_flag as T
    assert "dup_cross_gender" in dict(T.RULES)
