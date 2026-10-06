"""Rule 8 (grade_sanity): an adult club's "12" is not a high school senior
(owner, 2026-10-06: Tanner Chada, Gazelle Sports Elite). Three signals, all
required, so an elite senior racing a few pro meets is never moved.

    python -m pytest -q tests/test_adult_club.py
    XCP_TWIN_TEST_DSN="host=... dbname=postgres" runs the SQL half too
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for d in ("scripts", "engine", "racecast"):
    sys.path.insert(0, os.path.join(ROOT, d))
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")

import grade_sanity as GS                                       # noqa: E402

KEY = (1, 2024)


def decide(facts, clubs={"gazelle sports elite"}, adult=set(), grades=None):
    grades = grades if grades is not None else {KEY: 12}
    return GS._adultClubDecide([KEY], {KEY: facts}, {KEY: set(clubs)}, adult, grades)


def test_needs_all_three_signals():
    # club-only season, only college/pro races, an adult club -> moved
    assert decide((False, True), adult={"gazelle sports elite"}) == {KEY: "gazelle sports elite"}
    # a school row that season (Jackson Spencer, Evan Noonan's Nike Elite) -> kept
    assert decide((True, True), adult={"gazelle sports elite"}) == {}
    # a race with high schoolers on top -> kept
    assert decide((False, False), adult={"gazelle sports elite"}) == {}
    # neither an adult club nor an earlier graduation -> kept
    assert decide((False, True)) == {}


def test_earlier_graduation_stands_in_for_the_adult_club():
    # a grade 9 in 2014 graduates before 2024 (Tanner's 2015 tfrrs freshman)
    grades = {KEY: 12, (1, 2014): 9}
    assert decide((False, True), grades=grades) == {KEY: "gazelle sports elite"}
    # a junior the year before is the same class: not graduated
    grades = {KEY: 12, (1, 2023): 11}
    assert decide((False, True), grades=grades) == {}


def test_club_names_match_the_boards_label_rule():
    import build_ranking_results as B
    assert GS.CLUB_NAME_RE == B._CLUB_NAME_RE, "one club test, two places"


def test_rule8_on_postgres():
    import pytest
    dsn = os.environ.get("XCP_TWIN_TEST_DSN")
    if not dsn:
        pytest.skip("set XCP_TWIN_TEST_DSN to a scratch Postgres to run the SQL")
    import importlib
    psycopg2 = importlib.import_module("psycopg2")
    from level_graph import _raceKeyExpr
    conn = psycopg2.connect(dsn)
    cur = conn.cursor()
    cur.execute("""
        DROP TABLE IF EXISTS results, results_tf, race_top_level;
        CREATE TABLE results (person_id bigint, date text, school text,
                              meet_id bigint, div_id bigint, source text);
        CREATE TABLE results_tf (LIKE results);
        CREATE TABLE race_top_level (race bigint PRIMARY KEY, top_level text,
                                     n_levels int, known_frac real, n_teams int);""")
    rows = [
        # 1: Tanner -- club only, college/pro races, graduated (2014 grade 9)
        (1, "2025-04-18", "Gazelle Sports Elite", 10, 1, "anet"),
        (1, "2025-05-10", "Gazelle Sports Elite", 11, 1, "anet"),
        # 2: elite senior -- his school AND a club, same season
        (2, "2025-04-18", "Dana Hills", 10, 1, "anet"),
        (2, "2025-06-20", "Nike Elite", 12, 1, "anet"),
        # 3: club senior who ran one high school race
        (3, "2025-04-18", "Brentwood Track Club", 10, 1, "anet"),
        (3, "2025-05-01", "Brentwood Track Club", 13, 1, "anet"),
        # 4: unattached + club at elite meets -> kept (unattached counts against)
        (4, "2025-04-18", "Unattached", 10, 1, "anet"),
        (4, "2025-05-10", "Gazelle Sports Elite", 11, 1, "anet"),
    ]
    cur.executemany("INSERT INTO results_tf VALUES (%s,%s,%s,%s,%s,%s)", rows)
    for meet, lvl in ((10, "college"), (11, "pro"), (12, "college"), (13, "hs")):
        cur.execute(f"INSERT INTO race_top_level SELECT {_raceKeyExpr('x')}, %s, 1, 1, 1 "
                    f"FROM (SELECT %s::bigint meet_id, 1::bigint div_id, 'anet'::text source) x",
                    (lvl, meet))
    ay = 2024                       # the academic year of a 2025 spring
    hs = {(1, ay): 12, (1, 2014): 9, (2, ay): 12, (3, ay): 12, (4, ay): 12}
    got = GS.adultClubSeasons(cur, hs, college_start={})
    conn.rollback()
    assert got == {(1, ay): "gazelle sports elite"}, got
