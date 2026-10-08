"""A stray anet profile whose exact (name, school) is already on another
person's profile set joins that person (owner, 2026-10-07, Soheib Dissa:
"merge obviously doesn't work" -- persons 31559611 and 32891852, each a lone
'UNAT-Duke' profile, beside his career at 32309981, whose profiles already
hold 'UNAT-Duke').

    python -m pytest -q tests/test_link_profile_school.py

The decision is pure and tested on small lists; gather -> write -> undo runs
on a scratch Postgres when one is offered:

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

import link_profile_school as L                                  # noqa: E402

R, M = L.Row, L.Member

# the diag of 2026-10-07, cut down: the career, the one-race stray, the
# profile with no race at all
SOHEIB = M(32309981, "Soheib Dissa", ["M"], False, [
    R("2023-10-14", "XC", "11"), R("2024-04-20", "TF", "11"),
    R("2024-10-12", "XC", "12"), R("2025-10-18", "XC", "Fr"),
    R("2025-12-06", "TF", "Freshman")])
STRAY_1500 = M(31559611, "Soheib Dissa", ["M"], True, [R("2026-04-16", "TF", "-")])
STRAY_EMPTY = M(32891852, "Soheib Dissa", [], True, [])


# ---- what a school that identifies something is ------------------------- #

def test_identifying_school():
    yes = ["UNAT-Duke", "Unattached-Duke", "UNAT-Duke (NC)", "Newtown", "Northeast"]
    no = ["", "Unattached", "05-Unattached", "Unattached (OR)", "Unknown",
          "Unattached Runner", "Un-attached", "Club", "Sandyhook CT",
          "Newtown CT", "Sandy Hook-CT", "Unat"]
    assert [s for s in yes if not L.identifyingSchool(s)] == []
    assert [s for s in no if L.identifyingSchool(s)] == []


# ---- the decision -------------------------------------------------------- #

def test_soheib_strays_join_the_career():
    v = L.decideGroup([STRAY_1500, SOHEIB, STRAY_EMPTY])
    assert v.reason == L.MATCH, v
    assert v.target == 32309981
    assert v.movers == (31559611, 32891852)


def test_two_careers_under_one_key_is_refused():
    other = M(555, "Soheib Dissa", ["M"], False, [R("2019-10-01", "XC", "12")])
    v = L.decideGroup([SOHEIB, other, STRAY_1500])
    assert v.target is None and v.reason == "two careers share the profile school"


def test_sexes_that_disagree_are_refused():
    her = STRAY_1500._replace(gender=["F"])
    assert L.decideGroup([SOHEIB, her]).reason == "sexes disagree"


def test_same_cross_country_day_is_two_runners():
    twin = STRAY_1500._replace(rows=[R("2025-10-18", "XC", None)])
    assert L.decideGroup([SOHEIB, twin]).reason == "same cross country day: two runners"


def test_generation_mismatch_is_refused():
    # a namesake at the same school, a ninth grader six years later
    kid = STRAY_1500._replace(rows=[R("2030-10-01", "XC", "9")])
    assert L.decideGroup([SOHEIB, kid]).reason == "generation mismatch"


def test_no_career_and_a_tie_is_refused():
    a = STRAY_1500
    b = STRAY_1500._replace(pid=999, rows=[R("2026-05-01", "TF", "-")])
    assert L.decideGroup([a, b]).reason == "no career and no clear target"
    c = b._replace(rows=b.rows + [R("2026-05-08", "TF", "-")])
    v = L.decideGroup([a, c])
    assert (v.target, v.movers) == (999, (31559611,))


def test_groups_chain_through_shared_keys():
    g = L.groupsOf({("soheib dissa", "unat-duke"): {1, 2, 3},
                    ("soheib dissa", "unattached-duke"): {1, 3},
                    ("ann lee", "mead"): {7, 8}})
    assert sorted(sorted(p) for p, _k in g) == [[1, 2, 3], [7, 8]]


# ---- the SQL, on a scratch Postgres -------------------------------------- #

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


_TABLES = ("results, results_tf, athletes, person_link_log, person_redirect, "
           "profile_school_merge, profile_school_veto")

FIXTURE = f"""
DROP TABLE IF EXISTS {_TABLES};
CREATE TABLE results (result_id bigint, athlete_id bigint, person_id bigint,
  source text, date text, grade text, school text);
CREATE TABLE results_tf (LIKE results);
ALTER TABLE results_tf ADD COLUMN meet_id bigint, ADD COLUMN event_short text;
CREATE TABLE athletes (athlete_id bigint, first_name text, last_name text,
  gender text, school text, person_id bigint);
INSERT INTO athletes VALUES
  -- Soheib's career profile, and a tfrrs-linked row makes it a career
  (32309981, 'Soheib', 'Dissa', 'M', 'Newtown', 32309981),
  (32309981, 'Soheib', 'Dissa', 'M', 'Unattached', 32309981),
  (32309981, 'Soheib', 'Dissa', 'M', 'UNAT-Duke', 32309981),
  (32309981, 'Soheib', 'Dissa', 'M', 'Unattached-Duke', 32309981),
  (31559611, 'Soheib', 'Dissa', 'M', 'UNAT-Duke', 31559611),
  (32891852, 'Soheib', 'Dissa', 'M', 'UNAT-Duke', 32891852),
  (32891852, 'Soheib', 'Dissa', 'M', 'Unattached-Duke', 32891852),
  -- namesakes on no identifying school: never touched
  (31888727, 'Soheib', 'Dissa', 'M', 'Unattached', 31888727),
  (32691524, 'Soheib', 'Dissa', 'M', 'Dissa Track Club', 32691524),
  -- John Rivera: two careers at Brooks, a stray beside them -- refused
  (100, 'John', 'Rivera', 'M', 'Brooks', 100),
  (200, 'John', 'Rivera', 'M', 'Brooks', 200),
  (300, 'John', 'Rivera', 'M', 'Brooks', 300);
INSERT INTO results VALUES
  (1, 32309981, 32309981, 'anet', '2024-10-12', '12', 'Newtown'),
  (2, 32309981, 32309981, 'anet', '2025-10-18', 'Fr', 'Unattached'),
  (3, NULL,     32309981, 'tfrrs', '2025-10-18', 'Freshman', 'Unattached'),
  (4, 31888727, 31888727, 'anet', '2025-06-01', NULL, 'Unattached'),
  (10, 100, 100, 'anet', '2015-10-01', '11', 'Brooks'),
  (11, NULL, 100, 'tfrrs', '2017-10-01', 'Fr', 'Brooks'),
  (20, 200, 200, 'anet', '2022-10-01', '10', 'Brooks'),
  (21, NULL, 200, 'tfrrs', '2024-10-01', 'Fr', 'Brooks');
INSERT INTO results_tf VALUES
  (50, 31559611, 31559611, 'anet', '2026-04-16', '-', 'UNAT-Duke');
"""


def test_gather_write_undo_on_postgres():
    conn = _pg()
    cur = conn.cursor()
    try:
        cur.execute(FIXTURE)
        conn.commit()
        groups, keys = L.gather(cur)
        conn.rollback()
        verdicts = L.judge(groups)
        by_members = {tuple(m.pid for m in groups[i]): v for i, v in verdicts.items()}
        soheib = by_members[(31559611, 32309981, 32891852)]
        assert soheib.reason == L.MATCH and soheib.target == 32309981
        assert by_members[(100, 200, 300)].reason == "two careers share the profile school"
        assert not any(31888727 in k or 32691524 in k for k in by_members), \
            "'Unattached' and a school nobody else has join nobody"

        # --person narrows to that person's names and finds the same group
        cur = conn.cursor()
        g1, _k1 = L.gather(cur, 31559611)
        conn.rollback()
        assert [[m.pid for m in ms] for ms in g1.values()] == [[31559611, 32309981, 32891852]]

        dec = L.decisionsOf(groups, keys, verdicts)
        assert sorted(d[:2] for d in dec) == [(31559611, 32309981), (32891852, 32309981)]
        out = L.write(conn, dec)
        assert out["TF rows moved"] == 1 and out["XC rows moved"] == 0
        assert out["athletes rows repointed"] == 3 and out["redirects written"] == 2
        cur = conn.cursor()
        cur.execute("SELECT person_id FROM results_tf WHERE result_id = 50")
        assert cur.fetchone()[0] == 32309981
        cur.execute("SELECT DISTINCT person_id FROM athletes "
                    "WHERE athlete_id IN (31559611, 32891852)")
        assert cur.fetchall() == [(32309981,)]
        cur.execute("SELECT old_id, new_id FROM person_redirect ORDER BY 1")
        assert cur.fetchall() == [(31559611, 32309981), (32891852, 32309981)]
        cur.execute("SELECT sport, result_id, from_person, to_person, rule FROM person_link_log")
        assert cur.fetchall() == [("TF", 50, 31559611, 32309981, "profile_school")]

        # sticky: a re-scrape seeds the stray's next row with its own id
        cur.execute("INSERT INTO results_tf VALUES "
                    "(51, 31559611, 31559611, 'anet', '2026-05-01', '-', 'UNAT-Duke')")
        conn.commit()
        assert L.write(conn, [])["TF rows moved"] == 1

        # undo one: back, and vetoed, so the next gather leaves it alone
        L.undo(conn, "31559611")
        cur = conn.cursor()
        cur.execute("SELECT result_id, person_id FROM results_tf ORDER BY 1")
        assert cur.fetchall() == [(50, 31559611), (51, 31559611)]
        cur.execute("SELECT DISTINCT person_id FROM athletes WHERE athlete_id = 31559611")
        assert cur.fetchall() == [(31559611,)]
        cur.execute("SELECT old_id FROM person_redirect")
        assert cur.fetchall() == [(32891852,)]
        g2, _k2 = L.gather(cur)
        conn.rollback()
        assert not any(m.pid == 31559611 for ms in g2.values() for m in ms), "vetoed"
    finally:
        conn.rollback()
        cur = conn.cursor()
        cur.execute(f"DROP TABLE IF EXISTS {_TABLES}")
        conn.commit()
        conn.close()


# ---- the owner's site-wide dry run, 2026-10-08 ---------------------------- #

def test_names_that_differ_by_a_number_are_refused():
    a = M(22449377, "1A Boys 8th Grade FAT Standard", ["M"], True, [R("2024-05-01", "TF", None)])
    b = M(25100544, "1A Boys 7th Grade FAT Standard", ["M"], True, [R("2025-05-01", "TF", None)] * 3)
    assert L.decideGroup([a, b]).reason == "names differ by a number"


def test_a_bracketed_bib_is_not_part_of_the_name():
    a = M(21298812, "Aarav (1011) Shah", ["M"], True, [R("2024-04-01", "TF", "10")] * 3)
    b = M(24463894, "Aarav (1045) Shah", ["M"], True, [R("2025-04-01", "TF", "11")])
    assert L.decideGroup([a, b]).reason == L.MATCH


def test_two_in_one_track_race_is_two_runners():
    a = M(1, "Aaliyah Brown", ["F"], True, [R("2025-05-03", "TF", None, 77, "100m")] * 2)
    b = M(2, "Aaliyah Brown", ["F"], True, [R("2025-05-03", "TF", None, 77, "100m")])
    assert L.decideGroup([a, b]).reason == "same track race: two runners"
    c = M(3, "Aaliyah Brown", ["F"], True, [R("2025-05-03", "TF", None, 77, "200m")])
    assert L.decideGroup([a, c]).reason == L.MATCH          # two events, one runner


def test_two_careers_join_only_on_request_and_only_if_nothing_contradicts():
    """Owner, 2026-10-08, Jason Minicozzi: a Rivers (MA) high school career
    and a second profile with Rivers track AND his Tufts cross country."""
    hs = M(101, "Jason Minicozzi", ["M"], False, [
        R("2023-11-03", "XC", "10"), R("2024-11-09", "XC", "11"),
        R("2025-11-08", "XC", "12"), R("2026-05-16", "TF", "12")])
    split = M(102, "Jason Minicozzi", ["M"], False, [
        R("2025-04-12", "TF", "11"), R("2026-09-19", "XC", "FR-1"),
        R("2026-10-03", "XC", "FR-1")])
    assert L.decideGroup([hs, split]).reason == "two careers share the profile school"
    v = L.decideGroup([hs, split], allow_careers=True)
    assert v.reason == L.MATCH and v.target == 101 and v.movers == (102,)
    # a same-day cross country clash still refuses, careers or not
    clash = M(103, "Jason Minicozzi", ["M"], False, [R("2025-11-08", "XC", "12")])
    assert L.decideGroup([hs, clash], allow_careers=True).reason == \
        "same cross country day: two runners"


CAREER_FIXTURE = f"""
DROP TABLE IF EXISTS {_TABLES}, profile_school_athletes;
CREATE TABLE results (result_id bigint, athlete_id bigint, person_id bigint,
  source text, date text, grade text, school text);
CREATE TABLE results_tf (LIKE results);
ALTER TABLE results_tf ADD COLUMN meet_id bigint, ADD COLUMN event_short text;
CREATE TABLE athletes (athlete_id bigint, first_name text, last_name text,
  gender text, school text, person_id bigint);
INSERT INTO athletes VALUES
  (101, 'Jason', 'Minicozzi', 'M', 'Rivers', 101),
  (102, 'Jason', 'Minicozzi', 'M', 'Rivers', 102),
  (103, 'Jason', 'Minicozzi', 'M', 'Tufts', 102),
  (100, 'John', 'Rivera', 'M', 'Brooks', 100),
  (200, 'John', 'Rivera', 'M', 'Brooks', 200);
INSERT INTO results VALUES
  (1, 101, 101, 'anet', '2023-11-03', '10', 'Rivers'),
  (2, 101, 101, 'anet', '2024-11-09', '11', 'Rivers'),
  (3, 101, 101, 'anet', '2025-11-08', '12', 'Rivers'),
  (4, 103, 102, 'anet', '2026-09-19', 'FR-1', 'Tufts'),
  (5, NULL, 102, 'tfrrs', '2026-10-03', NULL, 'Tufts'),
  (10, 100, 100, 'anet', '2015-10-01', '11', 'Brooks'),
  (11, NULL, 100, 'tfrrs', '2017-10-01', 'Fr', 'Brooks'),
  (20, 200, 200, 'anet', '2022-10-01', '10', 'Brooks'),
  (21, NULL, 200, 'tfrrs', '2024-10-01', 'Fr', 'Brooks');
INSERT INTO results_tf VALUES
  (50, 102, 102, 'anet', '2025-04-12', '11', 'Rivers', 1, '1500m');
"""


def test_two_careers_write_and_undo_on_postgres():
    """--careers: every row under the moved career (its tfrrs row and the
    second profile's rows too) and every profile move, and --undo puts all
    of it back."""
    conn = _pg()
    cur = conn.cursor()
    try:
        cur.execute(CAREER_FIXTURE)
        conn.commit()
        groups, keys = L.gather(cur)
        conn.rollback()
        verdicts = L.judge(groups, allow_careers=True)
        by = {tuple(m.pid for m in groups[i]): v for i, v in verdicts.items()}
        v = by[(101, 102)]
        assert v.reason == L.MATCH and v.target in (101, 102)
        tgt, mover = v.target, (102 if v.target == 101 else 101)
        # two Brooks runners a generation apart stay two, careers or not
        assert by[(100, 200)].reason == "generation mismatch"
        L.write(conn, L.decisionsOf(groups, keys, verdicts))
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT person_id FROM results WHERE result_id IN (1, 2, 3, 4, 5)")
        assert cur.fetchall() == [(tgt,)], "every row, the tfrrs one included"
        cur.execute("SELECT DISTINCT person_id FROM results_tf")
        assert cur.fetchall() == [(tgt,)]
        cur.execute("SELECT DISTINCT person_id FROM athletes WHERE athlete_id IN (101, 102, 103)")
        assert cur.fetchall() == [(tgt,)], "every profile"
        L.undo(conn, "all")
        cur = conn.cursor()
        cur.execute("SELECT result_id, person_id FROM results WHERE result_id <= 5 ORDER BY 1")
        assert cur.fetchall() == [(1, 101), (2, 101), (3, 101), (4, 102), (5, 102)]
        cur.execute("SELECT athlete_id, person_id FROM athletes "
                    "WHERE athlete_id IN (101, 102, 103) ORDER BY 1")
        assert cur.fetchall() == [(101, 101), (102, 102), (103, 102)]
        assert mover in (101, 102)
    finally:
        conn.rollback()
        cur = conn.cursor()
        cur.execute(f"DROP TABLE IF EXISTS {_TABLES}, profile_school_athletes")
        conn.commit()
        conn.close()


def test_only_careers_lists_the_career_joins_alone():
    hs = M(101, "Jason Minicozzi", ["M"], False, [R("2025-11-08", "XC", "12")])
    split = M(102, "Jason Minicozzi", ["M"], False, [R("2026-09-19", "XC", "FR-1")])
    groups = {0: [hs, split], 1: [SOHEIB, STRAY_1500]}
    verdicts = L.judge(groups, allow_careers=True)
    assert set(L.careerMerges(groups, verdicts)) == {0}
