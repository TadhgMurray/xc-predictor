"""One cross country runner held by an anet person and a tfrrs person (or
two anet placeholders and a minted tfrrs person) becomes one person
(owner, 2026-10-08: "Seems there is a lot of duplication recently!" --
Rachael Withrow, Marshall, on the 2026 college women's board as herself and
twice as "Unknown").

    python -m pytest -q tests/test_link_feed_twins.py

The decision is pure and tested on small lists; gather -> write -> undo runs
on a scratch Postgres (in its own schema) when one is offered:

    XCP_TWIN_TEST_DSN="host=/tmp/pgtest port=54329 user=postgres dbname=postgres"
"""
import os
import sys

import _env  # noqa: F401  -- sets XCP_DB_PASSWORD, must precede config

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for d in ("scripts", "engine"):
    p = os.path.join(ROOT, d)
    if p not in sys.path:
        sys.path.insert(0, p)

import link_feed_twins as L                                      # noqa: E402

R, M = L.Run, L.Member

# the board of 2026-10-08, cut down: tfrrs athlete 9408205 minted, and the
# two athletic.net placeholders, one per meet upload
RACHAEL_T = M(1009408205, "Rachael Withrow", ["F"], L.MINTED, [
    R(-1, "2026-09-04", 971.2, "tfrrs"), R(-2, "2026-09-11", 978.9, "tfrrs")])
UNKNOWN_A = M(33400001, None, [], L.STRAY, [R(101, "2026-09-11", 978.9, "anet")])
UNKNOWN_B = M(33400002, None, [], L.STRAY, [R(102, "2026-09-04", 971.16, "anet")])


# ---- one run: the time and the school ------------------------------------ #

def test_the_same_time_as_precisely_as_both_feeds_print_it():
    assert L.sameTime(971.16, 971.2)          # anet hundredths, tfrrs tenths
    assert L.sameTime(978.9000244, 978.9)     # a real column's float
    assert L.sameTime(801.0, 801.4)           # tfrrs whole seconds: cut
    assert L.sameTime(801.0, 800.6)           # ... or rounded
    assert not L.sameTime(971.16, 971.3)
    assert not L.sameTime(801.0, 802.2)
    assert not L.sameTime(None, 801.0)


def test_one_schools_words_inside_the_others():
    assert L.sameSchool("Middle Tennessee", "Middle Tennessee State University")
    assert L.sameSchool("Marshall", "Marshall University")
    assert not L.sameSchool("Marshall", "Huntington High School")
    assert not L.sameSchool("", "Marshall")
    assert not L.sameSchool("University", "College")     # nothing left


# ---- the decision -------------------------------------------------------- #

def test_rachael_two_placeholders_join_the_minted_tfrrs_person():
    v = L.decideGroup([UNKNOWN_A, UNKNOWN_B, RACHAEL_T])
    assert v.reason == L.MATCH, v
    assert v.target == 1009408205
    assert v.movers == (33400001, 33400002)


def test_a_career_is_the_target_over_a_minted_person():
    career = M(8585424, "Abigail Beville", ["F"], L.CAREER,
               [R(-11, "2026-09-11", 1044.9, "tfrrs")])
    minted = M(1000000123, "Abigail Beville", ["F"], L.MINTED,
               [R(-12, "2026-09-18", 1050.0, "tfrrs")])
    stray = M(33400010, "", [], L.STRAY, [R(202, "2026-09-11", 1044.9, "anet"),
                                          R(203, "2026-09-18", 1050.04, "anet")])
    v = L.decideGroup([stray, minted, career])
    assert (v.reason, v.target, v.movers) == (L.MATCH, 8585424, (33400010, 1000000123))


def test_two_careers_is_reported_not_merged():
    a = M(500, "Kim Lee", ["F"], L.CAREER, [R(1, "2026-09-11", 1000.0, "anet")])
    b = M(600, "Kim Lee", ["F"], L.CAREER, [R(-1, "2026-09-11", 1000.0, "tfrrs")])
    assert L.decideGroup([a, b]).target is None


def test_names_that_disagree_are_refused():
    t = M(1000000999, "Ann Smith", ["F"], L.MINTED, [R(-1, "2026-09-11", 1100.0, "tfrrs")])
    a = M(500, "Beth Jones", ["F"], L.STRAY, [R(1, "2026-09-11", 1100.0, "anet")])
    assert L.decideGroup([t, a]).reason == "names disagree"


def test_sexes_that_disagree_are_refused():
    t = RACHAEL_T
    a = UNKNOWN_A._replace(genders=["M"])
    assert L.decideGroup([t, a]).reason == "sexes disagree"


def test_two_times_on_one_day_is_two_runners():
    a = UNKNOWN_A._replace(runs=[R(101, "2026-09-11", 978.9, "anet"),
                                 R(109, "2026-09-04", 1003.0, "anet")])
    assert L.decideGroup([RACHAEL_T, a]).reason == "two times on one day: two runners"


def test_groups_chain_through_shared_persons():
    gs = L.groupsOf([(1, 2), (1, 3), (4, 5)])
    assert sorted(sorted(g) for g in gs) == [[1, 2, 3], [4, 5]]


# ---- the SQL, on a scratch Postgres (own schema) ------------------------ #

def _pg():
    import pytest
    dsn = os.environ.get("XCP_TWIN_TEST_DSN")
    if not dsn:
        pytest.skip("set XCP_TWIN_TEST_DSN to a scratch Postgres")
    import importlib
    for name in [n for n in sys.modules if n == "psycopg2" or n.startswith("psycopg2.")]:
        if not hasattr(sys.modules[name], "__file__"):
            del sys.modules[name]
    conn = importlib.import_module("psycopg2").connect(dsn)
    cur = conn.cursor()
    cur.execute("DROP SCHEMA IF EXISTS feed_twin_test CASCADE; CREATE SCHEMA feed_twin_test")
    conn.commit()
    cur.execute("SET search_path = feed_twin_test")
    return conn


FIXTURE = """
CREATE TABLE results (result_id bigint, athlete_id bigint, person_id bigint,
  source text, date text, time_seconds real, school text, athlete_name text,
  team_slug text);
CREATE TABLE results_tf (result_id bigint, athlete_id bigint, person_id bigint,
  source text, date text);
CREATE TABLE athletes (athlete_id bigint, first_name text, last_name text,
  gender text, school text, person_id bigint);
INSERT INTO athletes VALUES
  (33400001, '', '', '', 'Marshall', 33400001),
  (33400002, '', '', '', 'Marshall University', 33400002),
  (33400003, '', '', '', 'Marshall', 33400003),
  (33400004, '', '', '', 'Marshall', 33400004),
  (8585424, 'Abigail', 'Beville', 'F', 'Elon', 8585424),
  (33400010, '', '', '', 'Elon', 33400010),
  (33400020, 'Hana', 'Park', 'F', 'Huntington High', 33400020);
INSERT INTO results VALUES
  -- Rachael: minted tfrrs person, two anet placeholders
  (-1, NULL, 1009408205, 'tfrrs', '2026-09-04', 971.2, 'Marshall', 'Rachael Withrow',
   'WV_college_f_Marshall'),
  (-2, NULL, 1009408205, 'tfrrs', '2026-09-11', 978.9, 'Marshall', 'Rachael Withrow',
   'WV_college_f_Marshall'),
  (101, 33400001, 33400001, 'anet', '2026-09-11', 978.9, 'Marshall', NULL, NULL),
  (102, 33400002, 33400002, 'anet', '2026-09-04', 971.16, 'Marshall University', NULL, NULL),
  -- a high schooler the same day and time, another school: not a partner
  (301, 33400020, 33400020, 'anet', '2026-09-11', 978.9, 'Huntington High', NULL, NULL),
  -- a pack: one tfrrs row, two anet teammates a few hundredths apart
  (-3, NULL, 1000000777, 'tfrrs', '2026-09-04', 1000.1, 'Marshall', 'Jane Roe',
   'WV_college_f_Marshall'),
  (103, 33400003, 33400003, 'anet', '2026-09-04', 1000.1, 'Marshall', NULL, NULL),
  (104, 33400004, 33400004, 'anet', '2026-09-04', 1000.14, 'Marshall', NULL, NULL),
  -- Abigail: a career (anet, its tfrrs id linked) and a placeholder
  (201, 8585424, 8585424, 'anet', '2025-10-01', 1100.0, 'Elon', NULL, NULL),
  (-10, NULL, 8585424, 'tfrrs', '2025-10-10', 1090.0, 'Elon', 'Abigail Beville', NULL),
  (-11, NULL, 8585424, 'tfrrs', '2026-09-11', 1044.9, 'Elon', 'Abigail Beville', NULL),
  (202, 33400010, 33400010, 'anet', '2026-09-11', 1044.9, 'Elon', NULL, NULL),
  -- already one person: nothing to do
  (-30, NULL, 8585424, 'tfrrs', '2026-09-18', 1050.0, 'Elon', 'Abigail Beville', NULL),
  (203, 8585424, 8585424, 'anet', '2026-09-18', 1050.0, 'Elon', NULL, NULL);
"""


def test_gather_write_undo_on_postgres():
    conn = _pg()
    cur = conn.cursor()
    try:
        cur.execute(FIXTURE)
        conn.commit()
        groups, pairs = L.gather(cur, "2026-08-01")
        conn.rollback()
        verdicts = L.judge(groups)
        by = {tuple(m.pid for m in groups[i]): v for i, v in verdicts.items()}
        assert set(by) == {(33400001, 33400002, 1009408205), (8585424, 33400010)}, by
        rach = by[(33400001, 33400002, 1009408205)]
        assert rach.reason == L.MATCH and rach.target == 1009408205
        abi = by[(8585424, 33400010)]
        assert abi.reason == L.MATCH and abi.target == 8585424
        kinds = {m.pid: m.kind for ms in groups.values() for m in ms}
        assert kinds == {33400001: L.STRAY, 33400002: L.STRAY, 1009408205: L.MINTED,
                         8585424: L.CAREER, 33400010: L.STRAY}
        assert {m.name for m in groups[0] + groups[1]} >= {"Rachael Withrow"}

        # --person narrows to that person's group
        g1, _p1 = L.gather(cur, "2026-08-01", 33400002)
        conn.rollback()
        assert [[m.pid for m in ms] for ms in g1.values()] == [[33400001, 33400002, 1009408205]]

        dec = L.decisionsOf(groups, verdicts)
        assert sorted(d[:3] for d in dec) == [(33400001, 1009408205, L.STRAY),
                                              (33400002, 1009408205, L.STRAY),
                                              (33400010, 8585424, L.STRAY)]
        out = L.write(conn, dec)
        assert out["XC rows moved"] == 3 and out["TF rows moved"] == 0
        assert out["athletes rows repointed"] == 3 and out["redirects written"] == 3
        cur = conn.cursor()
        cur.execute("SELECT result_id, person_id FROM results "
                    "WHERE result_id IN (101, 102, 202, 103, 104, 301) ORDER BY 1")
        assert cur.fetchall() == [(101, 1009408205), (102, 1009408205), (103, 33400003),
                                  (104, 33400004), (202, 8585424), (301, 33400020)]
        cur.execute("SELECT rule, count(*) FROM person_link_log GROUP BY 1")
        assert cur.fetchall() == [("feed_twin", 3)]

        # sticky: a re-scrape seeds the placeholder's next row with its own id
        cur.execute("INSERT INTO results VALUES (105, 33400001, 33400001, 'anet', "
                    "'2026-10-03', 986.4, 'Marshall', NULL, NULL)")
        conn.commit()
        assert L.write(conn, [])["XC rows moved"] == 1

        # undo one: back and vetoed; the next gather leaves it alone
        L.undo(conn, "33400001")
        cur = conn.cursor()
        cur.execute("SELECT result_id, person_id FROM results "
                    "WHERE result_id IN (101, 105) ORDER BY 1")
        assert cur.fetchall() == [(101, 33400001), (105, 33400001)]
        cur.execute("SELECT DISTINCT person_id FROM athletes WHERE athlete_id = 33400001")
        assert cur.fetchall() == [(33400001,)]
        g2, _p2 = L.gather(cur, "2026-08-01")
        conn.rollback()
        assert not any(m.pid == 33400001 for ms in g2.values() for m in ms), "vetoed"
    finally:
        conn.rollback()
        cur = conn.cursor()
        cur.execute("DROP SCHEMA IF EXISTS feed_twin_test CASCADE")
        conn.commit()
        conn.close()
