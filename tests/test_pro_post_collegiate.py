"""After college, a club season is a professional season (owner, 2026-10-22,
"agreed with new rules"; Rynard Swanepoel, https://racecast.co/athlete/32542423).

    XCP_TWIN_TEST_DSN=postgresql://... python -m pytest -q tests/test_pro_post_collegiate.py

Swanepoel raced all of 2026 for Atlanta Track Club / ATC Elite and sat 10th
on the college_m board: tfrrs writes "SR-4" where a grade goes, so the club
rule kept him college. pro_flag.buildPostCollegiate: an earlier college
season, no school or college race this season, >= 3 races for a club.

Runs against a scratch Postgres when XCP_TWIN_TEST_DSN is set, like
tests/test_pro_school_veto.py.
"""
import os
import sys

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
os.environ.setdefault("XCP_DB_QUIET", "1")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in ("scripts", "engine"):
    _d = os.path.join(_ROOT, _p)
    if _d not in sys.path:
        sys.path.insert(0, _d)

import pytest                                                     # noqa: E402


@pytest.fixture
def pg():
    dsn = os.environ.get("XCP_TWIN_TEST_DSN")
    if not dsn:
        pytest.skip("set XCP_TWIN_TEST_DSN to a scratch Postgres")
    import psycopg2
    conn = psycopg2.connect(dsn)
    yield conn
    conn.rollback()
    conn.close()


RYNARD = 32542423       # college slugs, then an elite club with "SR-4"
REDSHIRT = 2            # college, then a year unattached (team_id 0)
TRANSFER = 3            # college, then another college
KID = 4                 # a high schooler on a youth club, no college
MIXED = 5               # college, then a club season with one college race
FEW = 6                 # college, then only two club races
NCAA_ONLY = 7           # an NCAA meet, then a season on an anet club
PRO_ALREADY = 8         # college, then club -- and already pro_flag's
NIKE, CLUB, HS = 901, 902, 903

COLLEGE = "GA_college_m_Georgia"
OTHER_COLLEGE = "OR_college_m_Oregon"
ATC = "GA_club_m_Atlanta_Track_Club"


def _seed(cur):
    cur.execute("""
        DROP TABLE IF EXISTS results, results_tf, meets, meets_tf, grade_fix,
                             athlete_season, pro_athlete_season;
        CREATE TABLE results (result_id bigserial, person_id bigint,
            meet_id bigint, div_id bigint, date text, grade text,
            school text, team_id bigint, team_slug text);
        CREATE TABLE results_tf (result_id bigserial, person_id bigint,
            meet_id bigint, div_id bigint, event_id bigint, date text,
            grade text, school text, team_id bigint, team_slug text);
        CREATE TABLE meets (meet_id bigint, div_id bigint, meet_name text,
                            division text);
        CREATE TABLE meets_tf (meet_id bigint, div_id bigint, event_id bigint,
                               meet_name text, division text,
                               event_short text);
    """)
    cur.execute("INSERT INTO meets VALUES (1, 1, 'Pre-Nats', 'Men'), "
                "(2, 1, 'NCAA Division I Championships', 'Men'), "
                "(3, 1, 'Club Champs', 'Men')")
    xc = []

    def run(pid, date, school, team_id=None, slug=None, grade=None, meet=1):
        xc.append((pid, meet, 1, date, grade, school, team_id, slug))

    for p in (RYNARD, REDSHIRT, TRANSFER, MIXED, FEW, PRO_ALREADY):
        for d in ("2024-09-14", "2024-10-12", "2025-09-13", "2025-10-11"):
            run(p, d, "Georgia", slug=COLLEGE, grade="JR")
    days = ("2026-09-05", "2026-09-19", "2026-10-03", "2026-10-17")
    for d in days:
        run(RYNARD, d, "Atlanta Track Club Elite", slug=ATC, grade="SR-4",
            meet=3)
        run(REDSHIRT, d, "Unattached", team_id=0)
        run(TRANSFER, d, "Oregon", slug=OTHER_COLLEGE, grade="SR")
        run(KID, d, "Atlanta Track Club", slug=ATC, grade="11", meet=3)
        run(PRO_ALREADY, d, "Nike", team_id=NIKE, meet=3)
    for d in days[:3]:
        run(MIXED, d, "Atlanta Track Club", slug=ATC, meet=3)
    run(MIXED, days[3], "Georgia", slug=COLLEGE, grade="SR")
    for d in days[:2]:
        run(FEW, d, "Atlanta Track Club", slug=ATC, meet=3)
    run(NCAA_ONLY, "2025-11-22", "Somewhere", meet=2)
    for d in days[:3]:
        run(NCAA_ONLY, d, "Boulder Club", team_id=CLUB, meet=3)
    cur.executemany("""INSERT INTO results (person_id, meet_id, div_id, date,
                       grade, school, team_id, team_slug)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""", xc)


def _run(cur):
    import pro_flag as PF
    PF.buildNcaaSeasons(cur)
    PF.buildSchoolSeasons(cur, team_levels={NIKE: "club", CLUB: "club",
                                            HS: "hs"}, pro_teams=set())
    cur.execute("DROP TABLE IF EXISTS tmp_pro_season")
    cur.execute("CREATE TEMP TABLE tmp_pro_season "
                "(person_id bigint, yr int, pro_races int, round int)")
    cur.execute("INSERT INTO tmp_pro_season VALUES (%s, 2026, 3, 0)",
                (PRO_ALREADY,))
    PF.buildPostCollegiate(cur)
    cur.execute("SELECT person_id, yr FROM tmp_post_collegiate")
    got = set(cur.fetchall())
    added = PF.addPostCollegiate(cur)
    return got, added


def test_who_moves(pg):
    with pg.cursor() as cur:
        _seed(cur)
        got, added = _run(cur)
    assert got == {(RYNARD, 2026), (NCAA_ONLY, 2026)}
    assert added == 2


def test_the_college_seasons_themselves_stay_college(pg):
    with pg.cursor() as cur:
        _seed(cur)
        got, _ = _run(cur)
    assert (RYNARD, 2024) not in got and (RYNARD, 2025) not in got


def test_the_rows_land_in_tmp_pro_season_marked(pg):
    import pro_flag as PF
    with pg.cursor() as cur:
        _seed(cur)
        _run(cur)
        cur.execute("SELECT round, pro_races FROM tmp_pro_season "
                    "WHERE person_id = %s", (RYNARD,))
        assert cur.fetchall() == [(PF._POST_COLLEGIATE_ROUND, 4)]


def test_off_by_default(monkeypatch):
    import pro_flag as PF
    monkeypatch.delenv("XCP_POST_COLLEGIATE", raising=False)
    assert not PF.postCollegiateOn()
    monkeypatch.setenv("XCP_POST_COLLEGIATE", "1")
    assert PF.postCollegiateOn()
