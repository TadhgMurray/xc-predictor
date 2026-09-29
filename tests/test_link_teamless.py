"""A teamless anet profile joins the named-school athlete it really is
(owner, 2026-09-28: Tadhg Murray's Foot Locker West Regional on
/athlete/13642378, "Unknown (WA)", while his career is /athlete/29603086).

    python -m pytest -q tests/test_link_teamless.py

The decision is pure and tested on small row lists. The SQL -- the teamless
predicate, the name key, and gather -> write -> 13c0's resolve -> undo -- runs
on a scratch Postgres when one is offered:

    XCP_TWIN_TEST_DSN="host=/tmp/pgtest port=54329 user=postgres dbname=<yours>"
"""
import os
import sys

import _env  # noqa: F401  -- sets XCP_DB_PASSWORD, must precede config

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for d in ("scripts", "engine"):
    p = os.path.join(ROOT, d)
    if p not in sys.path:
        sys.path.insert(0, p)

import link_teamless as T                                       # noqa: E402

R, P = T.Row, T.Person
FL_WEST = "Foot Locker Cross Country West Regional Championships"


def dls(date, rating, meet, grade="12"):
    """a De La Salle row of Tadhg Murray's real career"""
    return R(date, "XC", grade, rating, "CA", meet, "De La Salle", "hs", "CA")


# the live page, 2024 XC season, as racecast shows it (2026-09-28)
TADHG_REAL = P(29603086, "Tadhg Murray", "M", [
    dls("2023-11-25", 123.9, "2023 CIF State Cross Country Championships", "11"),
    dls("2024-08-31", 127.5, "2024 Gaucho Invitational"),
    dls("2024-09-21", 131.4, "De La Salle Nike Invitational"),
    dls("2024-10-12", 128.6, "Crystal Springs Invitational"),
    dls("2024-10-26", 129.9, "76th Annual Mt. SAC Cross Country Invitational"),
    dls("2024-11-09", 129.0, "EBAL Championships"),
    dls("2024-11-23", 123.7, "CIF North Coast Section Championships"),
    dls("2024-11-30", 131.2, "2024 CIF State Cross Country Championships"),
])
TADHG_FL = P(13642378, "Tadhg Murray", "M", [
    R("2024-12-07", "XC", None, 129.4, "CA", FL_WEST, "Danville CA")])


# ---- what a teamless row is ---------------------------------------------- #

TEAM_CASES = [
    # (school, team_id, source, teamless?)
    ("Danville CA", 90001, "anet", True),        # a Foot Locker hometown
    ("Salt Lake City UT", 90002, "anet", True),
    ("Walnut Creek, CA", 90003, "anet", True),
    ("Unknown", 12, "anet", True),               # the scraper's placeholder
    ("Unattatched", 12, "anet", True),           # panels' spellings
    ("Un-attached", 12, "anet", True),
    ("Unattached (OR)", 12, "anet", True),
    ("Individual", 12, "anet", True),
    ("Club", 12, "anet", True),
    ("", 12, "anet", True),
    ("De La Salle", 0, "anet", True),            # anet team 0: no team
    ("De La Salle", None, "anet", True),
    ("De La Salle", 55, "anet", False),
    ("Union High", 55, "anet", False),           # 'un' inside a real name
    ("Unity", 55, "anet", False),
    ("Danville High School CA", 55, "anet", False),   # a school word
    ("Danville Running Club CA", 55, "anet", False),  # a club is a team
    ("Carmel", 55, "anet", False),
    ("Bates", None, "tfrrs", False),             # tfrrs has no anet team id
    ("Unattached", None, "tfrrs", True),
    ("Fresno CA", None, "tfrrs", True),
]


def test_the_teamless_rule():
    for school, tid, src, want in TEAM_CASES:
        assert T.isTeamlessRow(school, tid, src) is want, (school, tid, src)


def test_a_hometown_gives_its_state():
    assert T.hometownState("Danville CA") == "CA"
    assert T.hometownState("West Bountiful UT") == "UT"
    assert T.hometownState("De La Salle") is None
    assert T.hometownState("Danville XX") is None      # not a state


def test_all_star_and_postseason_meets():
    assert T.isAllStar(FL_WEST)
    assert T.isAllStar("Nike Cross Regionals Southwest")
    assert T.isAllStar("Brooks PR Invitational")
    assert not T.isAllStar("East Bay Athletic League Championships")
    assert T.isPostseason("2024 CIF State Cross Country Championships")
    assert T.isPostseason("CIF North Coast Section Championships")
    assert not T.isPostseason("Crystal Springs Invitational")
    assert not T.isPostseason(FL_WEST), "the all-star race is not its own qualifier"


def test_class_year():
    assert T.classYear("2024-12-07", "12") == 2025
    assert T.classYear("2025-04-10", "11") == 2026
    assert T.classYear("2024-12-07", None) is None
    assert T.classYear("2024-12-07", "SR") is None


# ---- the decision ------------------------------------------------------- #

def test_the_tadhg_murray_shape_is_accepted():
    v = T.decide(TADHG_FL, [TADHG_REAL])
    assert v.target == 29603086 and v.reason == T.MATCH
    kinds = {e.split()[0] for e in v.evidence}
    assert kinds == {"rating", "region", "qualifier"}, v.evidence
    assert v.school == "De La Salle"
    assert any("129.4 vs median 129.0 of 5" in e for e in v.evidence)
    assert any("7d before" in e for e in v.evidence), "CIF State, a week before"


def test_an_ambiguous_name_is_refused():
    other = TADHG_REAL._replace(pid=31000001, rows=[
        R("2024-11-02", "XC", "10", 101.0, "WA", "WIAA District", "Mead", "hs", "WA")])
    v = T.decide(TADHG_FL, [TADHG_REAL, other])
    assert v.target is None and v.reason.startswith("ambiguous")
    assert v.touched == (29603086, 31000001)


def test_ambiguity_is_counted_before_the_fit_filters():
    """A namesake who would fail the grade test still makes the name
    ambiguous: a wrong merge costs more than a missed one."""
    fl = TADHG_FL._replace(rows=[TADHG_FL.rows[0]._replace(grade="12")])
    seventh = TADHG_REAL._replace(pid=31000002, rows=[
        R("2024-11-02", "XC", "7", 90.0, "CA", "Middle School Champs", "Diablo MS", None, "CA")])
    assert T.decide(fl, [TADHG_REAL, seventh]).reason.startswith("ambiguous")


def test_a_namesake_of_the_other_gender_is_not_a_namesake():
    girl = TADHG_REAL._replace(pid=31000003, gender="F")
    v = T.decide(TADHG_FL, [TADHG_REAL, girl])
    assert v.target == 29603086


def test_a_generation_mismatch_is_refused():
    kid = TADHG_FL._replace(rows=[TADHG_FL.rows[0]._replace(grade="7")])
    assert T.decide(kid, [TADHG_REAL]).reason == "generation mismatch"


def test_a_grade_that_reads_as_an_age_is_refused():
    adult = TADHG_FL._replace(rows=[TADHG_FL.rows[0]._replace(grade="45")])
    assert T.decide(adult, [TADHG_REAL]).reason == "grade reads as an age"


def test_a_high_school_all_star_never_joins_a_college_namesake():
    college = TADHG_REAL._replace(rows=[
        r._replace(level="college", school="Cal", grade="FR-1")
        for r in TADHG_REAL.rows])
    v = T.decide(TADHG_FL, [college])
    assert v.reason.startswith("level mismatch")


def test_no_evidence_is_refused():
    """Same name, same season -- but unrated, another state, an ordinary
    road race: nothing says it is him."""
    road = TADHG_FL._replace(rows=[
        R("2024-11-16", "XC", None, None, "TX", "Turkey Trot 5K", "Unattached")])
    v = T.decide(road, [TADHG_REAL])
    assert v.target is None and v.reason == "weak evidence"


def test_one_piece_of_evidence_is_not_enough():
    near = TADHG_FL._replace(rows=[
        R("2024-11-16", "XC", None, 128.0, "NV", "Reno Open", "Unattached")])
    v = T.decide(near, [TADHG_REAL])
    assert v.reason == "weak evidence" and len(v.evidence) == 1


def test_a_rating_far_off_contradicts_whatever_else_agrees():
    slow = TADHG_FL._replace(rows=[TADHG_FL.rows[0]._replace(rating=105.0)])
    assert T.decide(slow, [TADHG_REAL]).reason == "rating contradicts"


def test_rows_outside_the_namesakes_career_are_refused():
    old = TADHG_FL._replace(rows=TADHG_FL.rows + [
        R("2019-06-01", "TF", None, 80.0, "CA", "All Comers", "Unattached")])
    assert T.decide(old, [TADHG_REAL]).reason == "rows outside the namesake's career"


def test_a_cross_country_race_the_same_day_is_someone_else():
    same = TADHG_FL._replace(rows=[TADHG_FL.rows[0]._replace(date="2024-11-30")])
    assert T.decide(same, [TADHG_REAL]).reason == "same day as the namesake's own race"


def test_no_namesake_in_the_window():
    later = TADHG_FL._replace(rows=[TADHG_FL.rows[0]._replace(date="2026-12-05")])
    assert T.decide(later, [TADHG_REAL]).reason.startswith("no named-school")


def test_two_teamless_namesakes_claiming_one_target_are_both_refused():
    twin = TADHG_FL._replace(pid=13600000)
    vs = {c.pid: T.decide(c, [TADHG_REAL]) for c in (TADHG_FL, twin)}
    assert all(v.target == 29603086 for v in vs.values())
    out = T.resolveClaims(vs, {13642378: "Tadhg Murray", 13600000: "Tadhg Murray"})
    assert all(v.target is None for v in out.values())
    assert {v.reason for v in out.values()} == {"two teamless namesakes claim one target"}


def test_the_pipeline_runs_it_between_the_snapshot_and_the_redirects():
    """01a sees the old id, 04a2 moves it, 13c0 301s it -- and before
    grade_sanity, twins, gender and the pack, which read who a row is."""
    import re
    sh = open(os.path.join(ROOT, "deploy", "run_pipeline.sh"), encoding="utf-8").read()
    order = ["step 01a_person_probe", "step 04a_link_tfrrs",
             "step 04a2_link_teamless", "step 04_grade_sanity", "step 04c_twins",
             "step 04d_gender", "step 13c0_person_redirects"]
    at = [sh.index(s) for s in order]
    assert at == sorted(at), order
    assert "scripts/link_teamless.py --apply" in sh
    always = re.search(r'_ALWAYS="([^"]*)"', sh).group(1).split()
    assert "04a2_link_teamless" in always, "a --from 7 run must still re-home rows"


def test_the_window_since_is_the_academic_year():
    import datetime
    assert T.sinceFor(3, datetime.date(2026, 9, 29)) == "2024-08-01"
    assert T.sinceFor(1, datetime.date(2026, 3, 1)) == "2025-08-01"


# ---- on a real Postgres, when one is offered ------------------------------ #

def _pg():
    import pytest
    dsn = os.environ.get("XCP_TWIN_TEST_DSN")
    if not dsn:
        pytest.skip("set XCP_TWIN_TEST_DSN to a scratch Postgres")
    import importlib
    for name in [n for n in sys.modules if n == "psycopg2" or n.startswith("psycopg2.")]:
        if not hasattr(sys.modules[name], "__file__"):
            del sys.modules[name]
    return importlib.import_module("psycopg2").connect(dsn)


def test_the_sql_rules_agree_with_the_python_ones():
    conn = _pg()
    cur = conn.cursor()
    try:
        for school, tid, src, want in TEAM_CASES:
            cur.execute(f"SELECT {T.teamlessSql('r')} FROM (SELECT %s::text AS school, "
                        f"%s::int AS team_id, %s::text AS source) r", (school, tid, src))
            assert cur.fetchone()[0] is want, (school, tid, src)
        cur.execute(f"SELECT {T.teamlessSql('r')} FROM (SELECT NULL::text AS school, "
                    f"5::int AS team_id, 'anet'::text AS source) r")
        assert cur.fetchone()[0] is True, "never NULL"
        for first, last in (("Tadhg", "Murray"), ("Mary-Kate", "O'Neil"),
                            ("  Jake ", "SMITH  "), ("Jo", ""), ("Zoë", "Ng")):
            cur.execute(f"SELECT {T.nameKeySql('%s', '%s')}", (first, last))
            got = cur.fetchone()[0]
            # normName with its two-token floor lifted (the SQL applies the
            # floor separately, as position(' ' in nm) > 0)
            assert got == (T.normName(f"{first} {last}") or got.strip()), got
            assert got == (T.normName(f"{first} {last} x") or "")[:-2], got
    finally:
        conn.rollback()
        conn.close()


FIXTURE = """
DROP TABLE IF EXISTS results, results_tf, athletes, meets, meets_tf, anet_team,
     person_link_log, person_redirect, person_probe, teamless_merge, teamless_veto;
CREATE TABLE results (result_id bigint PRIMARY KEY, athlete_id bigint,
  person_id bigint, source text, date text, grade text, school text,
  team_id int, team_slug text, speed_rating real, meet_id bigint, div_id bigint);
CREATE TABLE results_tf (LIKE results);
ALTER TABLE results_tf ADD COLUMN is_relay int;
CREATE TABLE athletes (athlete_id bigint, first_name text, last_name text,
  gender text, school text, person_id bigint);
CREATE TABLE meets (div_id bigint, meet_id bigint, meet_name text, state text);
CREATE TABLE meets_tf (div_id bigint, meet_id bigint, meet_name text, state text);
CREATE TABLE anet_team (team_id int PRIMARY KEY, state text);
INSERT INTO anet_team VALUES (55, 'CA'), (66, 'WA'), (67, 'WA'), (77, 'CA');
INSERT INTO meets VALUES
  (11, 1, 'Foot Locker Cross Country West Regional Championships', 'CA'),
  (12, 2, '2024 CIF State Cross Country Championships', 'CA'),
  (13, 3, 'CIF North Coast Section Championships', 'CA'),
  (14, 4, 'Crystal Springs Invitational', 'CA'),
  (15, 5, 'Greater Spokane League', 'WA'),
  (16, 6, 'Fall Open 5K', 'WA');
INSERT INTO meets_tf VALUES (21, 21, 'Summer All Comers', 'CA');
INSERT INTO athletes VALUES
  (13642378, 'Tadhg', 'Murray', 'M', 'Unknown', 13642378),
  (29603086, 'Tadhg', 'Murray', 'M', 'De La Salle', 29603086),
  (500, 'Sam', 'Lee', 'M', 'Unattached', 500),
  (501, 'Sam', 'Lee', 'M', 'Mead', 501),
  (502, 'Sam', 'Lee', 'M', 'Ferris', 502),
  (600, 'Ava', 'Chen', 'F', 'Unknown', 600),
  (601, 'Ava', 'Chen', 'F', 'Carondelet', 601);
-- Tadhg: the teamless profile, one Foot Locker row
INSERT INTO results VALUES
  (100, 13642378, 13642378, 'anet', '2024-12-07', NULL, 'Danville CA', 90001, NULL, 129.4, 1, 11);
-- his career, and one teamless row of his own (a profile with a team AND a
-- teamless row is a namesake, never a candidate)
INSERT INTO results VALUES
  (201, 29603086, 29603086, 'anet', '2024-10-12', '12', 'De La Salle', 55, NULL, 128.6, 4, 14),
  (202, 29603086, 29603086, 'anet', '2024-11-23', '12', 'De La Salle', 55, NULL, 123.7, 3, 13),
  (203, 29603086, 29603086, 'anet', '2024-11-30', '12', 'De La Salle', 55, NULL, 131.2, 2, 12);
INSERT INTO results_tf VALUES
  (204, 29603086, 29603086, 'anet', '2024-07-10', '11', 'Unattached', 0, NULL, 120.0, 21, 21, 0);
-- Sam Lee: a teamless profile and two real namesakes that autumn
INSERT INTO results VALUES
  (300, 500, 500, 'anet', '2024-11-02', NULL, 'Unattached', 0, NULL, 110.0, 6, 16),
  (301, 501, 501, 'anet', '2024-10-19', '11', 'Mead', 66, NULL, 111.0, 5, 15),
  (302, 501, 501, 'anet', '2024-10-26', '11', 'Mead', 66, NULL, 110.5, 5, 15),
  (303, 502, 502, 'anet', '2024-10-19', '12', 'Ferris', 67, NULL, 109.0, 5, 15),
  (304, 502, 502, 'anet', '2024-10-26', '12', 'Ferris', 67, NULL, 109.5, 5, 15);
-- Ava Chen: a seventh grader's teamless row, a senior namesake
INSERT INTO results VALUES
  (400, 600, 600, 'anet', '2024-12-07', '7', 'Unknown', 0, NULL, 100.0, 1, 11),
  (401, 601, 601, 'anet', '2024-11-23', '12', 'Carondelet', 77, NULL, 101.0, 3, 13),
  (402, 601, 601, 'anet', '2024-11-30', '12', 'Carondelet', 77, NULL, 100.5, 2, 12);
"""


def test_gather_write_resolve_undo_on_postgres():
    import person_redirects as PR
    conn = _pg()
    cur = conn.cursor()
    try:
        cur.execute(FIXTURE)
        cur.execute(PR.SNAPSHOT)                 # 01a, before anything moves
        conn.commit()

        cands, namesakes, counts = T.gather(cur, "2024-08-01")
        conn.rollback()
        assert set(cands) == {13642378, 500, 600}, "29603086 has a team: no candidate"
        assert [t.pid for t in namesakes[13642378]] == [29603086]
        assert len(namesakes[13642378][0].rows) == 3, "real-team rows only"
        v = T.judge(cands, namesakes)
        assert v[13642378].target == 29603086, v[13642378]
        assert v[500].reason.startswith("ambiguous")
        assert v[600].reason == "generation mismatch"

        dec = [(13642378, 29603086, "Tadhg Murray", "; ".join(v[13642378].evidence))]
        out = T.write(conn, dec)
        assert out["XC rows moved"] == 1 and out["TF rows moved"] == 0
        assert out["athletes rows repointed"] == 1 and out["redirects written"] == 1
        cur = conn.cursor()
        cur.execute("SELECT person_id FROM results WHERE result_id = 100")
        assert cur.fetchone()[0] == 29603086
        cur.execute("SELECT sport, result_id, from_person, to_person, rule "
                    "FROM person_link_log")
        assert cur.fetchall() == [("XC", 100, 13642378, 29603086, "teamless")]
        cur.execute("SELECT old_id, new_id FROM person_redirect")
        assert cur.fetchall() == [(13642378, 29603086)]

        # ★ 13c0 derives the same redirect from the 01a snapshot -- which
        #   works only because the athletes row moved too -- and does not drop it
        cur.execute(PR.RESOLVE)
        assert [(o, n) for o, n, _k in cur.fetchall()] == [(13642378, 29603086)]
        cur.execute(PR.CAME_BACK)
        assert cur.rowcount == 0
        conn.commit()

        # idempotent: a second run moves nothing and decides nothing new
        again = T.write(conn, dec)
        assert again == {"decisions new": 0, "XC rows moved": 0, "TF rows moved": 0,
                         "athletes rows repointed": 0, "redirects written": 0}
        cur = conn.cursor()
        cands2, _n, _c = T.gather(cur, "2024-08-01")
        conn.rollback()
        assert 13642378 not in cands2

        # sticky: a re-scrape seeds the next teamless row with the old id
        cur = conn.cursor()
        cur.execute("INSERT INTO results VALUES (101, 13642378, 13642378, 'anet', "
                    "'2024-12-14', NULL, 'Danville CA', 90001, NULL, 130.0, 1, 11)")
        conn.commit()
        assert T.write(conn, [])["XC rows moved"] == 1
        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM results WHERE person_id = 13642378")
        assert cur.fetchone()[0] == 0

        # undo one: rows, athletes and redirect back, and vetoed for good
        T.undo(conn, "13642378")
        cur = conn.cursor()
        cur.execute("SELECT result_id, person_id FROM results "
                    "WHERE result_id IN (100, 101) ORDER BY 1")
        assert cur.fetchall() == [(100, 13642378), (101, 13642378)]
        cur.execute("SELECT person_id FROM athletes WHERE athlete_id = 13642378")
        assert cur.fetchone()[0] == 13642378
        cur.execute("SELECT count(*) FROM person_redirect")
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT count(*) FROM person_link_log")
        assert cur.fetchone()[0] == 0
        cands3, _n, _c = T.gather(cur, "2024-08-01")
        conn.rollback()
        assert 13642378 not in cands3, "vetoed"
        assert T.write(conn, [])["XC rows moved"] == 0
    finally:
        conn.rollback()
        cur = conn.cursor()
        cur.execute("DROP TABLE IF EXISTS results, results_tf, athletes, meets, "
                    "meets_tf, anet_team, person_link_log, person_redirect, "
                    "person_probe, teamless_merge, teamless_veto")
        conn.commit()
        conn.close()
