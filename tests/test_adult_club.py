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


def test_not_a_school():
    import re as _re
    # the Postgres pattern, read by Python: no \b, so the two agree here
    pat = _re.compile(GS.NOT_SCHOOL_RE)
    for s in ("unattached", "19-unattached", "", "united states", "usa", "u.s.a.",
              "jamaica", "unat", "n/a"):
        assert pat.search(s), s
    for s in ("crater", "mercer island", "united states naval academy prep",
              "una high", "canada college", "justin northwest"):
        assert not pat.search(s), s


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
        DROP TABLE IF EXISTS results, results_tf, race_top_level, athlete_season;
        CREATE TABLE athlete_season (person_id bigint, sport text, year int, pool text);
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
        # 4: unattached + club at elite meets, no adult club and no earlier
        #    grade -> kept (signal 3)
        (4, "2025-04-18", "Unattached", 10, 1, "anet"),
        (4, "2025-05-10", "Gazelle Sports Elite", 11, 1, "anet"),
        # 8: Bobby Poynter's shape (owner, 2026-10-11) -- a club, then
        #    Unattached and "United States" at elite meets, graduated -> moved
        (8, "2025-04-18", "Brentwood Track Club", 10, 1, "anet"),
        (8, "2025-05-10", "Unattached", 11, 1, "anet"),
        (8, "2025-06-01", "United States", 12, 1, "anet"),
        # 9: the same, but one high school race on top -> kept (signal 2)
        (9, "2025-04-18", "Brentwood Track Club", 10, 1, "anet"),
        (9, "2025-05-10", "Unattached", 13, 1, "anet"),
        # 5: a ZAP "12" with no history -- its club reads adult because the
        #    last build pooled the club's other athletes pro (6, 7)
        (5, "2025-04-18", "ZAP Endurance", 10, 1, "anet"),
        (6, "2025-04-18", "ZAP Endurance", 10, 1, "anet"),
        (7, "2025-04-18", "ZAP Endurance", 11, 1, "anet"),
    ]
    cur.executemany("INSERT INTO results_tf VALUES (%s,%s,%s,%s,%s,%s)", rows)
    for meet, lvl in ((10, "college"), (11, "pro"), (12, "college"), (13, "hs")):
        cur.execute(f"INSERT INTO race_top_level SELECT {_raceKeyExpr('x')}, %s, 1, 1, 1 "
                    f"FROM (SELECT %s::bigint meet_id, 1::bigint div_id, 'anet'::text source) x",
                    (lvl, meet))
    ay = 2024                       # the academic year of a 2025 spring
    cur.executemany("INSERT INTO athlete_season VALUES (%s, 'TF', %s, %s)",
                    [(5, ay, "hs_m"), (6, ay, "pro_m"), (7, ay, "pro_m")])
    hs = {(1, ay): 12, (1, 2014): 9, (2, ay): 12, (3, ay): 12, (4, ay): 12,
          (5, ay): 12, (8, ay): 12, (8, 2014): 9, (9, ay): 12, (9, 2014): 9}
    got = GS.adultClubSeasons(cur, hs, college_start={})
    conn.rollback()
    assert got == {(1, ay): "gazelle sports elite", (5, ay): "zap endurance",
                   (8, ay): "brentwood track club"}, got


def test_unattached_arm_needs_an_earlier_adult_season():
    # (owner, 2026-10-12) Jason Intravaia: PDC professional in 2024, then
    # "Grade 10" unattached at college meets -> moved, no club named
    key = (1, 2025)
    got = GS._adultClubDecide([key], {key: (False, True)}, {}, set(), {key: 10},
                              adult_from={1: 2024})
    assert got == {key: ""}
    # the same season with no earlier adult season: a real tenth grader who
    # only raced unattached at college meets is left alone
    assert GS._adultClubDecide([key], {key: (False, True)}, {}, set(), {key: 10}) == {}
    # a school row or a high school race that season still keeps it
    assert GS._adultClubDecide([key], {key: (True, True)}, {}, set(), {key: 10},
                               adult_from={1: 2024}) == {}
    assert GS._adultClubDecide([key], {key: (False, False)}, {}, set(), {key: 10},
                               adult_from={1: 2024}) == {}
    # adult the SAME season is not earlier
    assert GS._adultClubDecide([key], {key: (False, True)}, {}, set(), {key: 10},
                               adult_from={1: 2025}) == {}


def test_unattached_arm_by_own_graduation():
    # Jack Falkowski: grade 12 in 2025, an unattached "9" the next year
    key = (1, 2026)
    got = GS._adultClubDecide([key], {key: (False, True)}, {}, set(),
                              {key: 9, (1, 2025): 12})
    assert got == {key: ""}


def test_past_school_level():
    key = (1, 2026)
    # mostly college races, never a pro, a year out of school -> college
    assert GS.pastSchoolLevel(key, {key: (3, 1)}, {}, {1: 2026}) == "college"
    # after a professional season -> pro (Intravaia)
    assert GS.pastSchoolLevel(key, {key: (3, 0)}, {1: 2025}, {1: 2024}) == "pro"
    # mostly pro races -> pro
    assert GS.pastSchoolLevel(key, {key: (1, 2)}, {}, {1: 2026}) == "pro"
    # past NCAA eligibility of the first year out of school -> pro
    assert GS.pastSchoolLevel(key, {key: (3, 0)}, {},
                              {1: 2026 - GS.POST_ELIGIBILITY_SEASONS}) == "pro"
    # no races read -> pro, as before
    assert GS.pastSchoolLevel(key, {}, {}, {}) == "pro"


def test_prior_adult_seasons():
    acad = {(1, 2023): {"grade": "12", "level": None},
            (1, 2024): {"grade": None, "level": "pro"},
            (2, 2024): {"grade": "FR", "level": None},
            (3, 2024): {"grade": None, "level": "college"}}
    adult, pro = GS.priorAdultSeasons(acad)
    assert adult == {1: 2024, 2: 2024, 3: 2024}
    assert pro == {1: 2024}


def test_season_ceilings_on_postgres():
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
    cur.executemany("INSERT INTO results_tf VALUES (%s,%s,%s,%s,%s,%s)", [
        (1, "2026-01-17", "Unattached", 10, 1, "anet"),
        (1, "2026-01-31", "Unattached", 10, 1, "anet"),
        (1, "2026-02-14", "Unattached", 11, 1, "anet"),
        (2, "2026-01-17", "Unattached", 11, 1, "anet")])
    for meet, lvl in ((10, "college"), (11, "pro")):
        cur.execute(f"INSERT INTO race_top_level SELECT {_raceKeyExpr('x')}, %s, 1, 1, 1 "
                    f"FROM (SELECT %s::bigint meet_id, 1::bigint div_id, 'anet'::text source) x",
                    (lvl, meet))
    got = GS.seasonCeilings(cur, [(1, 2025), (2, 2025), (3, 2025)])
    conn.rollback()
    assert got == {(1, 2025): (2, 1), (2, 2025): (0, 1)}, got


def test_a_brand_run_into_elite_is_a_club():
    # owner, 2026-10-13: Jake Ritter's 2024 season stayed high school on
    # "rabbitELITE", which the word boundary before "elite" did not match
    import re
    rx = re.compile(GS.CLUB_NAME_RE.replace(r"\m", r"\b").replace(r"\M", r"\b"))
    for club in ("rabbitelite", "rabbit elite", "nike elite", "railroad athletics",
                 "bowerman track club"):
        assert rx.search(club), club
    for school in ("jesuit high school", "elk grove",
                   "eliteville central"):
        assert not rx.search(school), school


# ---- 2026-10-23: initials, Spanish club words, grades 6-12, one-man clubs ----

def test_a_clubs_initials_are_the_club():
    got = GS.clubAliases({(1, 2022): {"royal city athletics club"}, (2, 2024): {"zap endurance"},
                          (3, 2024): {"the ab club"}})
    assert (1, "rcac", "royal city athletics club") in got
    assert not any(a == "ze" for _p, a, _c in got)            # under three letters: never an alias
    assert (3, "abc", "the ab club") in got


def test_spanish_club_words_are_clubs():
    rx = re.compile(GS.CLUB_NAME_RE.replace(r"\m", r"\b").replace(r"\M", r"\b"))
    assert rx.search("equipo albatros tlalpan") and rx.search("club deportivo toluca")
    assert not rx.search("jesuit high school")


def test_a_one_man_club_at_pro_meets_is_adult_but_a_junior_with_a_school_race_is_not():
    k1, k2 = (2, 2024), (3, 2024)
    facts = {k1: (False, True), k2: (False, True)}
    clubs = {k1: {"equipo albatros tlalpan"}, k2: {"youth tc"}}
    got = GS._adultClubDecide([k1, k2], facts, clubs, set(), {k1: 12, k2: 10},
                              never_school={2}, pro_seasons={k1, k2})
    assert got == {k1: "equipo albatros tlalpan"}
    # no professional race that season: kept
    assert GS._adultClubDecide([k1], facts, clubs, set(), {k1: 12},
                               never_school={2}, pro_seasons=set()) == {}
