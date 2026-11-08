"""freshman_recheck: the links made before the college-namesake guard
(owner, 2026-11-08, /athlete/19764025)."""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))
import _env  # noqa: E402,F401

import freshman_recheck as FR  # noqa: E402
from link_freshmen import normName  # noqa: E402


def test_a_link_whose_college_has_a_main_feed_namesake_fails():
    hayden = ("XC", "tfrrs", 777, 29370341, "Hayden Buck", "East Texas Baptist", 6)
    ann = ("XC", "tfrrs", 778, 101, "Ann Lee", "Tufts", 4)
    ns = {normName("Hayden Buck"): [(19764025, "East Texas Baptist")],
          normName("Ann Lee"): [(101, "Tufts")]}           # she is the one linked: no rival
    hs = {19764025: {"gatlinburg-pittman"}, 29370341: {"guam"}, 101: {"rivers"}}
    bad = FR.failing([hayden, ann], ns, hs)
    assert [(lk[3], rivals) for lk, rivals in bad] == [(29370341, [19764025])]


def test_a_namesake_with_no_high_school_career_may_be_the_same_runner():
    # a college-only main-feed profile the site never joined to his own
    # high school career is no proof of a second person
    lk = ("XC", "tfrrs", 777, 29370341, "Hayden Buck", "East Texas Baptist", 6)
    ns = {normName("Hayden Buck"): [(33118532, "East Texas Baptist")]}
    assert FR.failing([lk], ns, {29370341: {"guam"}}) == []
    # nor is one who ran for the same high school
    assert FR.failing([lk], ns, {29370341: {"guam"}, 33118532: {"guam"}}) == []


def test_a_namesake_at_another_college_is_no_rival():
    lk = ("XC", "tfrrs", 1, 5, "Sam Ward", "Tufts", 3)
    assert FR.failing([lk], {normName("Sam Ward"): [(9, "Bates")]}, {9: {"x"}, 5: {"y"}}) == []


DSN = os.environ.get("XCP_TWIN_TEST_DSN")


def test_write_takes_the_identity_back_and_undo_restores_it():
    import pytest
    if not DSN:
        pytest.skip("needs XCP_TWIN_TEST_DSN")
    import psycopg2
    c = psycopg2.connect(DSN)
    cur = c.cursor()
    cur.execute("""
        DROP SCHEMA IF EXISTS frr CASCADE; CREATE SCHEMA frr; SET search_path = frr;
        CREATE TABLE results (result_id bigint, person_id bigint, source text, native_id bigint,
                              athlete_name text, school text);
        CREATE TABLE results_tf (LIKE results); ALTER TABLE results_tf ADD COLUMN id_system text;
        CREATE TABLE person_link_log (sport text, result_id bigint, from_person bigint,
                                      to_person bigint, rule text,
                                      PRIMARY KEY (sport, result_id, rule));
        -- the Guam person holds Hayden's college-feed id 777: one row by the
        -- freshman rule, two spread by fanout; a row of another id stays
        INSERT INTO results VALUES
          (1, 29370341, 'tfrrs', 777, 'Hayden Buck', 'East Texas Baptist'),
          (2, 29370341, 'tfrrs', 777, 'Hayden Buck', 'East Texas Baptist'),
          (3, 29370341, 'tfrrs', 777, 'Hayden Buck', 'East Texas Baptist'),
          (4, 29370341, 'anet',  NULL, 'Hayden Buck', 'Guam');
        INSERT INTO person_link_log VALUES
          ('XC', 1, 0, 29370341, 'freshman'), ('XC', 2, 0, 29370341, 'fanout'),
          ('XC', 3, 0, 29370341, 'fanout');
    """)
    found = FR.links(cur)
    assert [(lk[2], lk[3], lk[6]) for lk in found] == [(777, 29370341, 1)]
    c.commit()
    FR.write(c, [(found[0], [19764025])])
    cur.execute("SET search_path = frr")
    cur.execute("SELECT result_id, person_id FROM results ORDER BY 1")
    assert cur.fetchall() == [(1, None), (2, None), (3, None), (4, 29370341)]
    cur.execute("SELECT count(*) FROM person_link_log")
    assert cur.fetchone()[0] == 0
    FR.undo(c)
    cur.execute("SET search_path = frr")
    cur.execute("SELECT result_id, person_id FROM results ORDER BY 1")
    assert cur.fetchall() == [(1, 29370341), (2, 29370341), (3, 29370341), (4, 29370341)]
    cur.execute("SELECT count(*) FROM person_link_log")
    assert cur.fetchone()[0] == 3
    cur.execute("DROP SCHEMA frr CASCADE")
    c.commit()
    c.close()


def test_the_loader_finds_a_main_feed_college_namesake():
    """The first loader used the college feed's college test, false on every
    main-feed row: the recheck found 0 of 15,449."""
    import pytest
    if not DSN:
        pytest.skip("needs XCP_TWIN_TEST_DSN")
    import psycopg2
    import link_freshmen as LF
    c = psycopg2.connect(DSN)
    cur = c.cursor()
    cur.execute("""
        DROP SCHEMA IF EXISTS frl CASCADE; CREATE SCHEMA frl; SET search_path = frl;
        CREATE TABLE athletes (athlete_id bigint, first_name text, last_name text);
        CREATE TABLE results (athlete_id bigint, person_id bigint, source text, school text, grade text);
        CREATE TABLE results_tf (LIKE results);
        INSERT INTO athletes VALUES (33118532, 'Hayden', 'Buck'), (29370341, 'Hayden', 'Buck');
        INSERT INTO results VALUES
          (33118532, 19764025, 'anet', 'Gatlinburg-Pittman', '11'),
          (33118532, 19764025, 'anet', 'East Texas Baptist', 'FR-1'),
          (33118532, 19764025, 'anet', 'East Texas Baptist', NULL);
        INSERT INTO results_tf VALUES (29370341, 29370341, 'anet', 'Guam', '12');
    """)
    got = LF.anetCollegeNamesakes(cur, ["Hayden Buck"])
    hs = LF.hsSchools(cur, [19764025, 29370341])
    cur.execute("DROP SCHEMA frl CASCADE")
    c.commit()
    c.close()
    assert got == {LF.normName("Hayden Buck"): [(19764025, "East Texas Baptist")]}
    assert hs == {19764025: {"gatlinburg-pittman"}, 29370341: {"guam"}}
