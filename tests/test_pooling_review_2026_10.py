# Project: xc-predictor / tests
# File:    test_pooling_review_2026_10.py
# Purpose: the clear pooling bugs fixed by the 2026-10 pooling review
#          (docs/REVIEW-POOLING-2026-10.md, "Fixed in this pass").
#
#   1. the ability gate refused the HAND LIST too (Guillaume Tremblay)
#   2. a club runner's CORROBORATED school grade was read as no grade
#   3. a tfrrs "Unattached" row's race ceiling was looked up and ignored
#   4. the club-majority gate counted CALENDAR years, every other pooling
#      fact counts academic ones -- and it dropped a sport with no club row
#      from the denominator
#
#   python -m pytest -q tests/test_pooling_review_2026_10.py
#   XCP_TWIN_TEST_DSN=postgresql://... for the SQL half (4)
import ast
import io
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _env  # noqa: E402,F401
for _p in (_ROOT, os.path.join(_ROOT, "engine"), os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pool_resolve as pr                                      # noqa: E402

TREMBLAY = 32703729


def _poolfor(grade, gender, source, school, season_level=None):
    """poolFor's shape without its pickles: the grade's level, else the
    season's (as tests/test_pro_ability_gate.py)."""
    import normalize_distance as nd
    lv = nd.GRADE_TO_LEVEL.get(nd.normalizeGrade(grade)) if grade is not None else None
    lv = lv or season_level
    if lv is None:
        return None
    return f"{lv}_{'m' if gender == 'M' else 'f'}"


def _resolve(**kw):
    base = dict(grade=None, gender="M", source="anet", school="Some Club",
                sport="TF", poolfor=_poolfor, season=2025)
    base.update(kw)
    return pr.resolvePool(base.pop("grade"), **base)


# ---- 1. the hand list outranks the ability gate ------------------------ #

def test_a_hand_listed_professional_survives_a_failed_ability_gate():
    """★ The case at the top of pool_resolve: grade 6 on his feed, a 1:54
    800 m, so not sub-14:00 -- and still a professional, because the list is
    a person's word rather than an inference the gate exists to check."""
    assert pr.isProPerson(TREMBLAY, "TF", 2025)
    assert _resolve(grade="6", person_id=TREMBLAY,
                    pro_ability=False) == "pro_m|TF"
    assert _resolve(grade="6", person_id=TREMBLAY,
                    pro_ability=None) == "pro_m|TF"


def test_the_gate_still_refuses_everyone_not_on_the_list():
    assert _resolve(grade="6", person_id=1, is_pro=True,
                    pro_ability=False) == "ms_m|TF"


def test_the_list_is_still_per_season():
    """Andrew Hunter is listed from 2017 on; his high school before that is
    a high school career, gate or no gate."""
    hunter = 26054637
    assert _resolve(grade="11", person_id=hunter, season=2014,
                    pro_ability=False) == "hs_m|TF"


# ---- 2. a corrected grade on a club is a grade ------------------------- #

def test_a_club_runner_with_a_corroborated_grade_keeps_it():
    """grade_sanity writes grade_untrusted for EVERY season it has a verdict
    for, so grade_untrusted=True arrives with a corroborated '10'. That is
    the youth-club runner the club rule promises to leave alone."""
    got = _resolve(grade="10", team_level="club", grade_untrusted=True,
                   fixed_grade="10", grade_verdict="corroborated")
    assert got == "hs_m|TF"


def test_a_gradeless_club_row_is_still_professional():
    assert _resolve(grade=None, team_level="club") == "pro_m|TF"


def test_a_distrusted_grade_with_no_replacement_is_still_no_grade():
    """stale_grade and friends: the flag with nothing in fixed_grade."""
    assert _resolve(grade="10", team_level="club",
                    grade_untrusted=True) == "pro_m|TF"


def test_a_corroborated_grade_on_a_club_with_professionals_still_sweeps():
    """The OTHER club rule is unchanged: a season raced mostly for a club
    with professionals is professional whatever grade it carries (owner,
    2026-09-16)."""
    assert _resolve(grade="10", team_level="club", team_has_pros=True,
                    grade_untrusted=True, fixed_grade="10") == "pro_m|TF"


# ---- 3. an unattached tfrrs row reads its race ------------------------- #

def test_a_stated_ceiling_makes_an_entry_unattached():
    for lvl in ("elem", "ms", "hs", "college", "pro"):
        assert pr.unattachedEntry(False, lvl), lvl
    assert pr.unattachedEntry(True, None)


def test_no_ceiling_changes_nothing():
    for v in (None, "", "club", "nonsense"):
        assert not pr.unattachedEntry(False, v), v


def test_a_tfrrs_unattached_row_in_a_high_school_race_is_a_high_schooler():
    """The end to end the pack now produces: tfrrs, no team id, school
    'Unattached', a high school race. It used to keep no_team False and
    land on the grade, then on 'tfrrs means college'."""
    no_team = pr.unattachedEntry(False, "hs")
    got = pr.resolvePool(None, "F", "tfrrs", "Unattached", "XC", season=2025,
                         no_team=no_team, race_top_level="hs",
                         poolfor=_poolfor)
    assert got == "hs_f|XC"


def _src(rel):
    with io.open(os.path.join(_ROOT, *rel.split("/")), encoding="utf-8") as fh:
        return fh.read()


def _function(src, name):
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(src, node)
    raise AssertionError(f"{name} not found")


def test_the_pack_asks_the_helper():
    # ! THE TEAM FACTS MOVED INTO rowTeamFacts (P1), which the pack and the
    #   fill both call; the helper is asked there now.
    src = _src("engine/speed_ratings.py")
    assert "rowTeamFacts(" in _function(src, "packResults")
    assert "unattachedEntry(no_team, race_top_level)" in _function(src, "rowTeamFacts")


# ---- 4. the club majority, on the academic year ------------------------ #

def test_the_pack_asks_the_club_gate_with_the_academic_year():
    body = _function(_src("engine/speed_ratings.py"), "rowTeamFacts")
    assert "clubSeason(person_id, _academicYear(race_date))" in body
    assert ".year)" not in body


def test_neither_club_loader_reads_a_calendar_year():
    src = _src("engine/speed_ratings_db.py")
    for name in ("loadClubMajority", "loadClubPros"):
        assert "substr(r.date, 1, 4)::int" not in _function(src, name), name


@pytest.fixture
def pg(monkeypatch):
    dsn = os.environ.get("XCP_TWIN_TEST_DSN")
    if not dsn:
        pytest.skip("set XCP_TWIN_TEST_DSN to a scratch Postgres")
    import contextlib
    import psycopg2
    import speed_ratings_db as sdb
    conn = psycopg2.connect(dsn)

    @contextlib.contextmanager
    def _conn():
        yield conn
    monkeypatch.setattr(sdb, "getConn", _conn)
    yield conn, sdb
    conn.rollback()
    conn.close()


CLUB, SCHOOL = 77, 5


def test_club_majority_is_judged_per_academic_season(pg):
    conn, sdb = pg
    with conn.cursor() as cur:
        cur.execute("""
            DROP TABLE IF EXISTS results, results_tf;
            CREATE TABLE results (result_id bigserial, person_id bigint,
                date text, school text, team_id bigint);
            CREATE TABLE results_tf (result_id bigserial,
                person_id bigint, date text, school text, team_id bigint);
        """)
        xc, tf = [], []
        # person 1: three club XC races in fall 2024, four school track
        # races in spring 2025 -- ONE academic season, 3 of 7 for the club.
        # The calendar clock saw 2024 as 3 of 3 and called it a club year --
        # and so did the per-table HAVING on ANY clock: the track table had
        # no club row, so its four rows never reached the denominator.
        for d in ("2024-09-07", "2024-10-05", "2024-11-02"):
            xc.append((1, d, "Some TC", CLUB))
        for d in ("2025-03-01", "2025-04-01", "2025-05-01", "2025-05-15"):
            tf.append((1, d, "Some HS", SCHOOL))
        # person 2: the whole 2024-25 season for the club, fall and spring
        for d in ("2024-09-07", "2024-10-05"):
            xc.append((2, d, "Some TC", CLUB))
        for d in ("2025-02-01", "2025-04-01"):
            tf.append((2, d, "Some TC", CLUB))
        cur.executemany("INSERT INTO results (person_id, date, school, team_id)"
                        " VALUES (%s, %s, %s, %s)", xc)
        cur.executemany("INSERT INTO results_tf (person_id, date, school,"
                        " team_id) VALUES (%s, %s, %s, %s)", tf)
    # ! COMMITTED, in public: the loader probes information_schema for
    #   public.results.team_id and rolls back its own temp tables
    conn.commit()
    try:
        got = sdb.loadClubMajority([CLUB], [], [])
    finally:
        with conn.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS results, results_tf")
        conn.commit()
    assert (1, 2024) not in got and (1, 2025) not in got
    assert (2, 2024) in got
    assert (2, 2025) not in got      # spring 2025 is the 2024 season


# ---- 5. age bands: the pattern is matched against a lower-cased grade ---- #

def test_every_age_marker_can_match_a_lowercased_grade():
    import re
    import age_band_grades as abg
    rx = re.compile(abg.UNAMBIGUOUS_AGE)
    for g in ("6U", "13-14", "19+", "Open", "20+"):
        assert rx.match(g.strip().lower()), g
    assert not rx.match("11-12") and not rx.match("10")
