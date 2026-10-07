# Project: xc-predictor / tests
# File:    test_thin_field_sql.py
# Purpose: rule 5d (grade_sanity.thinFieldSeasons) in SQL picks exactly the
#          seasons the Python loop it replaced picked.
#
# ★ WHY (run 20261006_120609): the loop fetched every graded allraces row
#   (most of 225M) into Python, and the step sat five hours between rule 7
#   and the verdicts. The SQL counts only the events holding a contradicted
#   season; this checks the answer did not move, on random fields with
#   repeats, ungraded rows and events at the 70% and 4-entrant edges.
#
# Needs a scratch Postgres, like test_twin_flag.py:
#   XCP_TWIN_TEST_DSN="host=/tmp/pgtest port=54329 user=postgres dbname=postgres"
import os
import random
import sys

import pytest

import _env  # noqa: F401  -- sets XCP_DB_PASSWORD, must precede config

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in ("engine", "scripts"):
    sys.path.insert(0, os.path.join(ROOT, p))

import grade_sanity as GS                                        # noqa: E402

DB = "xcp_thin_field_test"


def _reference(rows, nuked):
    """The loop as it was before 2026-10-07, on (pid, acad, m, d, src, ek, graded)."""
    by_event = {}
    for pid, ay, m, d, src, ek, graded in rows:
        if graded:
            by_event.setdefault((m, d, src, ek), []).append((pid, ay))
    out = set()
    for entrants in by_event.values():
        if len(entrants) < GS.MIN_FIELD_GRADED:
            continue
        bad = sum(1 for k in entrants if k in nuked)
        if bad < GS.NUKED_FIELD_SHARE * len(entrants):
            continue
        out.update(entrants)
    return out


@pytest.fixture
def cur():
    dsn = os.environ.get("XCP_TWIN_TEST_DSN")
    if not dsn:
        pytest.skip("set XCP_TWIN_TEST_DSN to a scratch Postgres to run rule 5d")
    import psycopg2
    admin = psycopg2.connect(dsn)
    admin.autocommit = True
    with admin.cursor() as c:
        c.execute(f"DROP DATABASE IF EXISTS {DB}")
        c.execute(f"CREATE DATABASE {DB}")
    test_dsn = " ".join(p for p in dsn.split() if not p.startswith("dbname=")) + f" dbname={DB}"
    cx = psycopg2.connect(test_dsn)
    c = cx.cursor()
    c.execute("""CREATE TABLE allraces (person_id bigint, acad int, meet_id bigint,
                                        div_id bigint, source text, event_key bigint,
                                        grade_folded text)""")
    yield c
    cx.close()
    with admin.cursor() as c2:
        c2.execute(f"DROP DATABASE IF EXISTS {DB}")
    admin.close()


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_the_sql_matches_the_loop(cur, seed):
    rnd = random.Random(seed)
    people = [(p, a) for p in range(1, 120) for a in (2023, 2024)]
    nuked = set(rnd.sample(people, 90))
    rows = []
    for m in range(1, 60):                       # events of 1..12 rows
        ev = (m, rnd.choice([1, 2]), rnd.choice(["anet", "tfrrs"]), rnd.choice([-1, 7]))
        # some fields mostly contradicted, some barely
        pool = list(nuked) if rnd.random() < 0.5 else people
        for _ in range(rnd.randint(1, 12)):
            pid, ay = rnd.choice(pool if rnd.random() < 0.8 else people)
            rows.append((pid, ay) + ev + (rnd.random() < 0.85,))
        if rnd.random() < 0.3 and rows:          # a person twice in one event
            rows.append(rows[-1])
    from psycopg2.extras import execute_values
    execute_values(cur, "INSERT INTO allraces VALUES %s",
                   [(p, a, m, d, s, e, "11" if g else None) for p, a, m, d, s, e, g in rows])
    want = _reference(rows, nuked)
    got = GS.thinFieldSeasons(cur, nuked)
    assert got == want
    assert want, "the seed should produce at least one thin field"


def test_the_seventy_percent_and_four_entrant_edges(cur):
    from psycopg2.extras import execute_values
    nuked = {(1, 2024), (2, 2024), (3, 2024), (4, 2024), (5, 2024), (6, 2024), (7, 2024)}
    rows = []
    # event 1: 7 of 10 contradicted -> exactly 70%, in
    rows += [(i, 2024, 1, 1, "anet", -1, "9") for i in range(1, 11)]
    # event 2: 3 of 3 -> under four graded entrants, out
    rows += [(i, 2024, 2, 1, "anet", -1, "9") for i in (1, 2, 3)]
    # event 3: 2 of 3 graded contradicted plus an ungraded one -> out (3 graded)
    rows += [(1, 2024, 3, 1, "anet", -1, "9"), (2, 2024, 3, 1, "anet", -1, "9"),
             (50, 2024, 3, 1, "anet", -1, "9"), (51, 2024, 3, 1, "anet", -1, None)]
    # event 4: 6 of 9 -> 66.7%, out
    rows += [(i, 2024, 4, 1, "anet", -1, "9") for i in (1, 2, 3, 4, 5, 6, 60, 61, 62)]
    execute_values(cur, "INSERT INTO allraces VALUES %s", rows)
    got = GS.thinFieldSeasons(cur, nuked)
    assert got == {(i, 2024) for i in range(1, 11)}


def test_no_contradicted_seasons_no_query(cur):
    assert GS.thinFieldSeasons(cur, set()) == set()
