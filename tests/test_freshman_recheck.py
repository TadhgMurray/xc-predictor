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
    hs = {19764025: {(2023, "gatlinburg-pittman"), (2024, "gatlinburg-pittman"),
                     (2025, "gatlinburg-pittman")},
          29370341: {(2023, "guam"), (2024, "guam"), (2025, "guam"), (2026, "guam")},
          101: {(2025, "rivers")}}
    bad = FR.failing([hayden, ann], ns, hs)
    assert [(lk[3], rivals) for lk, rivals in bad] == [(29370341, [19764025])]


def test_a_namesake_with_no_high_school_career_may_be_the_same_runner():
    # a college-only main-feed profile the site never joined to his own
    # high school career is no proof of a second person
    lk = ("XC", "tfrrs", 777, 29370341, "Hayden Buck", "East Texas Baptist", 6)
    ns = {normName("Hayden Buck"): [(33118532, "East Texas Baptist")]}
    assert FR.failing([lk], ns, {29370341: {(2025, "guam")}}) == []
    # nor is one who ran for the same high school
    assert FR.failing([lk], ns, {29370341: {(2025, "guam"), (2026, "guam")},
                                 33118532: {(2025, "guam"), (2026, "guam")}}) == []
    # nor one whose stray rows sit in other seasons (Beckett Fitch: two 2019
    # club rows on his own new college profile)
    assert FR.failing([lk], ns, {29370341: {(2025, "guam"), (2026, "guam")},
                                 33118532: {(2019, "so cal roadrunners")}}) == []


def test_a_namesake_at_another_college_is_no_rival():
    lk = ("XC", "tfrrs", 1, 5, "Sam Ward", "Tufts", 3)
    assert FR.failing([lk], {normName("Sam Ward"): [(9, "Bates")]},
                      {9: {(2024, "x"), (2025, "x")}, 5: {(2024, "y"), (2025, "y")}}) == []


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
                              athlete_name text, school text, date text DEFAULT '2026-09-05');
        CREATE TABLE results_tf (LIKE results); ALTER TABLE results_tf ADD COLUMN id_system text;
        CREATE TABLE person_link_log (sport text, result_id bigint, from_person bigint,
                                      to_person bigint, rule text,
                                      PRIMARY KEY (sport, result_id, rule));
        -- the Guam person holds Hayden's college-feed id 777: one row by the
        -- freshman rule, two spread by fanout; a row of another id stays
        INSERT INTO results (result_id, person_id, source, native_id, athlete_name, school) VALUES
          (1, 29370341, 'tfrrs', 777, 'Hayden Buck', 'East Texas Baptist'),
          (2, 29370341, 'tfrrs', 777, 'Hayden Buck', 'East Texas Baptist'),
          (3, 29370341, 'tfrrs', 777, 'Hayden Buck', 'East Texas Baptist'),
          (4, 29370341, 'anet',  NULL, 'Hayden Buck', 'Guam');
        INSERT INTO person_link_log VALUES
          ('XC', 1, 0, 29370341, 'freshman'), ('XC', 2, 0, 29370341, 'fanout'),
          ('XC', 3, 0, 29370341, 'fanout');
    """)
    found = FR.links(cur)
    assert [(lk[2], lk[3], lk[6], lk[7]) for lk in found] == [(777, 29370341, 1, 2027)]
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
        CREATE TABLE results (athlete_id bigint, person_id bigint, source text, school text,
                              grade text, date text);
        CREATE TABLE results_tf (LIKE results);
        INSERT INTO athletes VALUES (33118532, 'Hayden', 'Buck'), (29370341, 'Hayden', 'Buck');
        INSERT INTO results VALUES
          (33118532, 19764025, 'anet', 'Gatlinburg-Pittman', '11', '2023-09-16'),
          (33118532, 19764025, 'anet', 'East Texas Baptist', 'FR-1', '2026-09-05'),
          (33118532, 19764025, 'anet', 'East Texas Baptist', NULL, '2026-10-03');
        INSERT INTO results_tf VALUES (29370341, 29370341, 'anet', 'Guam', '12', '2026-05-14');
    """)
    got = LF.anetCollegeNamesakes(cur, ["Hayden Buck"])
    hs = LF.hsSchools(cur, [19764025, 29370341])
    cur.execute("DROP SCHEMA frl CASCADE")
    c.commit()
    c.close()
    assert got == {LF.normName("Hayden Buck"): [(19764025, "East Texas Baptist")]}
    assert hs == {19764025: {(2024, "gatlinburg-pittman", 11)}, 29370341: {(2026, "guam", 12)}}


def test_unattached_is_no_college_and_a_spelling_is_one_school():
    """The owner's dry run, 2026-11-08: five of 63 were 'Unattached'."""
    unat = ("TF", "tfrrs", 1, 16295187, "Declan McDonnell", "Unattached", 1)
    ns = {normName("Declan McDonnell"): [(29587104, "Unattached")]}
    two = lambda sch: {(2024, sch), (2025, sch)}  # noqa: E731
    assert FR.failing([unat], ns, {16295187: two("a"), 29587104: two("b")}) == []
    lk = ("XC", "tfrrs", 2, 10, "Sam Ward", "Tufts", 3)
    ns = {normName("Sam Ward"): [(11, "Tufts")]}
    assert FR.failing([lk], ns, {10: two("lake orion"), 11: two("lake orion high school (mi)")}) == []
    assert FR.failing([lk], ns, {10: two("lake orion"), 11: two("clarkston")}) != []


def test_a_namesake_still_in_high_school_is_not_the_college_runner():
    """The owner's recheck, 2026-11-08: Emelia Hesselgren, Fayetteville (AR)
    class of 2026, at Oral Roberts; the main-feed profile with the Oral
    Roberts rows also holds a New York club runner in 10th grade in 2024-25."""
    lk = ("XC", "tfrrs", 9404625, 21462703, "Emelia Hesselgren", "Oral Roberts", 2, 2027)
    ns = {normName("Emelia Hesselgren"): [(32773768, "Oral Roberts")]}
    hs = {21462703: {(2023, "haas hall - fayetteville", 9), (2024, "fayetteville", 10),
                     (2025, "fayetteville", 11), (2026, "fayetteville", 12)},
          32773768: {(2023, "tailwind", 8), (2024, "tailwind", 9), (2025, "tailwind", 10)}}
    assert FR.failing([lk], ns, hs) == []
    # the Hayden Buck case still fails: the Tennessee runner graduated in 2025
    lk = ("XC", "tfrrs", 9418365, 29370341, "Hayden Buck", "East Texas Baptist", 1, 2027)
    ns = {normName("Hayden Buck"): [(19764025, "East Texas Baptist")]}
    hs = {19764025: {(2023, "gatlinburg-pittman", 10), (2024, "gatlinburg-pittman", 11),
                     (2025, "gatlinburg-pittman", 12)},
          29370341: {(2023, "guam", 9), (2024, "guam", 10), (2025, "guam", 11), (2026, "guam", 12)}}
    assert [r for _lk, r in FR.failing([lk], ns, hs)] == [[19764025]]
