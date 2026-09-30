"""A school season is not made professional by the fields it raced (owner,
2026-09-30: Jackson Spencer, https://racecast.co/athlete/30178075).

    XCP_DB_PASSWORD=x python -m pytest -q tests/test_pro_school_veto.py

Spencer, Herriman UT, grade 12 in academic year 2025: every senior row read
113-120 on the pro scale where his junior year read 133-143 in hs_m. By the
code, pro_flag's field traversal made the season professional -- the
Bowerman Mile and the Festival of Miles (a mile and its 1500 split each)
were fields of confirmed professionals, and the World Cross Country U20 race
was a field of foreign juniors the national-team seed had flagged, because
_SENIOR_EXCLUDE read the meet name and not the division.

The rule: a season whose rows carry a school grade (1-12, grade_sanity's
when it has one) at a school team is vetoed from INFERRED professional
status -- the traversal, the national-team seed, a 'pro' season verdict, an
unattached row's 'pro' race ceiling -- the way NCAA eligibility already is.
The hand list still decides.

The SQL half runs against a scratch Postgres when XCP_TWIN_TEST_DSN is set,
like tests/test_college_verdict_school_season.py.
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

import pool_resolve as pr                                         # noqa: E402

SPENCER, LUTKENHAUS = 30178075, 30245014
AY = 2025                                  # fall 2025 XC, spring 2026 TF


def _pool(grade="12", **kw):
    base = dict(gender="M", source="anet", school="Herriman", sport="TF",
                season=AY, person_id=SPENCER, team_level="hs")
    base.update(kw)
    return pr.resolvePool(grade, **base)


# ---- the backstop in resolvePool --------------------------------------- #

def test_spencers_senior_rows_stay_high_school_under_pro_evidence():
    """pro_athlete_season written before the rule (is_pro), a 'pro' season
    verdict from the fields, grade_sanity's corroborated 12: hs_m."""
    assert _pool(is_pro=True) == "hs_m|TF"
    assert _pool(season_level="pro") == "hs_m|TF"
    assert _pool(is_pro=True, grade_untrusted=True, fixed_grade="12") \
        == "hs_m|TF"
    assert _pool(is_pro=True, sport="XC", school="Herriman") == "hs_m|XC"


def test_his_unattached_bowerman_mile_is_screened_by_his_corroborated_grade():
    kw = dict(school="Unattached", team_level=None, no_team=True,
              race_top_level="pro")
    assert _pool(grade_untrusted=True, fixed_grade="12", **kw) == "hs_m|TF"
    # ! only grade_sanity's grade screens the ceiling: a raw '12' is the
    #   one a scraper copies forward (Pieter Heesters), so it does not
    assert _pool(**kw) == "pro_m|TF"


def test_a_hand_listed_season_stays_professional():
    """Cooper Lutkenhaus turned professional at 16: his 2025-26 season is
    professional whatever grade his feed still writes, and his junior
    track spring before it is a school season."""
    kw = dict(person_id=LUTKENHAUS, school="Northwest")
    assert _pool(is_pro=True, **kw) == "pro_m|TF"
    assert _pool(is_pro=True, sport="XC", **kw) == "pro_m|XC"
    assert _pool(is_pro=True, season=AY - 1, **kw) == "hs_m|TF"
    assert pr.isProPerson(LUTKENHAUS, "TF", AY) \
        and pr.isProPerson(LUTKENHAUS, "XC", AY)
    assert not pr.isProPerson(LUTKENHAUS, "TF", AY - 1)
    assert not pr.isProPerson(LUTKENHAUS, "XC", AY - 1)


def test_a_synthetic_hand_listed_schoolboy_stays_professional(monkeypatch):
    # labels: the 2026 track season is STORED 2025 (season_year)
    monkeypatch.setitem(pr._PRO_SEASONS, 4242, (2026, None, None))
    assert _pool(is_pro=True, person_id=4242, season=2024) == "hs_m|TF"
    assert _pool(is_pro=True, person_id=4242, season=2025) == "pro_m|TF"
    assert _pool(is_pro=False, person_id=4242, season=2025) == "pro_m|TF"
    assert _pool(is_pro=True, person_id=4242, season=2025, sport="XC") \
        == "hs_m|XC"


def test_what_the_veto_does_not_touch():
    # an unattached, gradeless runner in professional fields: pro as before
    assert _pool(None, school="Unattached", team_level=None, no_team=True,
                 race_top_level="pro") == "pro_m|TF"
    assert _pool(None, is_pro=True, team_level=None, school="Zzz TC") \
        == "pro_m|TF"
    # a grade on a team nobody has levelled is not school eligibility --
    # an elite squad's feed writes year counts where grades go
    assert _pool("11", is_pro=True, team_level=None) == "pro_m|TF"
    assert _pool("6", is_pro=True, team_level="club") == "pro_m|TF"
    # the owner's team rules are not inference about the athlete
    assert _pool("12", team_pro=True) == "pro_m|TF"
    assert _pool("12", team_has_pros=True, team_level=None) == "pro_m|TF"
    # a class word or an age band is not a school grade
    assert _pool("SR", is_pro=True) == "pro_m|TF"
    assert _pool("11-12", is_pro=True) == "pro_m|TF"
    # a stale grade: grade_sanity's verdict carries no grade, no veto
    assert _pool("12", is_pro=True, grade_untrusted=True, fixed_grade=None,
                 fixed_level="pro") == "pro_m|TF"


def test_the_predicate_itself():
    v = pr.schoolSeasonVetoesPro
    assert v("12", "hs")
    assert v("9th", "hs") and v("8", "ms")
    assert not v("12", None) and not v("12", "club") and not v("12", "college")
    assert not v("12", "hs", no_team=True) and not v("12", "hs", team_pro=True)
    assert not v("SO-2", "hs") and not v(None, "hs")
    assert v("SO-2", "hs", fixed_grade="10") is False     # the Amherst rule
    assert not v("12", "hs", grade_untrusted=True)         # stale: no grade
    assert v("-", "hs", grade_untrusted=True, fixed_grade="12")
    assert not v("12", "hs", school="HOKA NAZ Elite")


# ---- the source: pro_flag on a scratch Postgres ------------------------ #

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


HERRIMAN, OTHER_HS, USA_TEAM, NIKE = 5, 6, 900, 901
PROS = list(range(100, 110))        # ten seeded professionals
JUNIORS = list(range(200, 208))     # foreign juniors at the U20 race
SENIOR_KENYAN = 300
UNATTACHED = 400                    # no team, no grade, professional fields
COLLEGIAN = 500                     # Diamond League fields + an NCAA meet
TAG_ALONG = 600                     # unattached; rides on Spencer's fraction
KIDS = list(range(700, 706))        # Herriman's and a rival's seniors


def _seed(cur):
    cur.execute("""
        DROP TABLE IF EXISTS results, results_tf, meets, meets_tf, grade_fix,
                             athlete_season, pro_athlete_season;
        CREATE TABLE results (result_id bigserial, person_id bigint,
            meet_id bigint, div_id bigint, date text, grade text,
            school text, team_id bigint);
        CREATE TABLE results_tf (result_id bigserial, person_id bigint,
            meet_id bigint, div_id bigint, event_id bigint, date text,
            grade text, school text, team_id bigint);
        CREATE TABLE meets (meet_id bigint, div_id bigint, meet_name text,
                            division text);
        CREATE TABLE meets_tf (meet_id bigint, div_id bigint, event_id bigint,
                               meet_name text, division text,
                               event_short text);
        CREATE TABLE grade_fix (person_id bigint, season int, grade text,
                                level text, method text, trust text);
    """)
    tf, xc = [], []

    def run(pid, meet, event, date, grade=None, school="Nike", team=NIKE):
        tf.append((pid, meet, 1, event, date, grade, school, team))

    meets_tf = [
        (100, 1, 1, "Prefontaine Classic - Eugene Diamond League", "Men", "Mile"),
        (100, 1, 2, "Prefontaine Classic - Eugene Diamond League", "Men", "1500m"),
        (101, 1, 1, "Rome Diamond League", "Men", "1500m"),
        (102, 1, 1, "Oslo Diamond League", "Men", "Mile"),
        (103, 1, 1, "Paris Diamond League", "Men", "1500m"),
        (200, 1, 1, "HOKA Festival of Miles", "Pro Men", "Mile"),
        (200, 1, 2, "HOKA Festival of Miles", "Pro Men", "1500m"),
        (300, 1, 1, "UHSAA 6A State Championships", "Boys", "1600m"),
        (301, 1, 1, "Arcadia Invitational", "Boys", "3200m"),
        (400, 1, 1, "Oregon Open 1", "Men", "1500m"),
        (401, 1, 1, "Oregon Open 2", "Men", "1500m"),
        (402, 1, 1, "Oregon Open 3", "Men", "1500m"),
        (500, 1, 1, "NCAA Division I Outdoor Championships", "Men", "1500m"),
    ]
    for p in PROS:
        for m in (101, 102, 103):                         # the seed: 3 each
            run(p, m, 1, "2026-06-01")
        for e in (1, 2):                                  # Pre, Festival
            run(p, 100, e, "2026-07-04")
            run(p, 200, e, "2026-07-18")
    for p in PROS[:4]:                                    # three open 1500s
        for m in (400, 401, 402):
            run(p, m, 1, "2026-05-02")
    for who, grade, school, team in (
            (SPENCER, "12", "Herriman", HERRIMAN),
            (LUTKENHAUS, "12", "Northwest", OTHER_HS)):
        for e in (1, 2):
            run(who, 100, e, "2026-07-04", grade, "Unattached", 0)
            run(who, 200, e, "2026-07-18", grade, school, team)
        run(who, 300, 1, "2026-05-23", grade, school, team)
        run(who, 301, 1, "2026-04-10", grade, school, team)
    for m in (400, 401, 402):                             # 4 pros + S + one
        run(SPENCER, m, 1, "2026-05-02", "12", "Herriman", HERRIMAN)
        run(TAG_ALONG, m, 1, "2026-05-02", None, "Unattached", 0)
    for e in (1, 2):
        run(UNATTACHED, 100, e, "2026-07-04", None, "Unattached", 0)
    run(UNATTACHED, 200, 1, "2026-07-18", None, "Unattached", 0)
    for m in (101, 102, 103):
        run(COLLEGIAN, m, 1, "2026-06-01", "JR", "Oregon", None)
    run(COLLEGIAN, 500, 1, "2026-06-12", "JR", "Oregon", None)
    for k in KIDS:
        run(k, 300, 1, "2026-05-23", "12",
            "Herriman" if k % 2 else "Lone Peak", HERRIMAN if k % 2 else 7)
        run(k, 301, 1, "2026-04-10", "12", "Lone Peak", 7)

    # World Cross Country: the U20 race and the senior one, one meet
    for d, division in ((1, "U20 Men"), (2, "Senior Men")):
        cur.execute("INSERT INTO meets VALUES (400, %s, %s, %s)",
                    (d, "World Cross Country Championships", division))
    xc.append((SPENCER, 400, 1, "2026-01-10", "12", "USA", USA_TEAM))
    for j, country in zip(JUNIORS, ("Kenya", "Ethiopia", "Uganda", "Eritrea",
                                    "Kenya", "Ethiopia", "Uganda", "Eritrea")):
        xc.append((j, 400, 1, "2026-01-10", None, country, None))
    xc.append((SENIOR_KENYAN, 400, 2, "2026-01-10", None, "Kenya", None))
    # his high school cross country season
    cur.execute("INSERT INTO meets VALUES (410, 1, 'Region 3 Championships', "
                "'Boys Varsity')")
    for k in [SPENCER] + KIDS:
        xc.append((k, 410, 1, "2025-10-15", "12", "Herriman", HERRIMAN))

    cur.executemany("INSERT INTO meets_tf VALUES (%s,%s,%s,%s,%s,%s)", meets_tf)
    cur.executemany("""INSERT INTO results_tf (person_id, meet_id, div_id,
                       event_id, date, grade, school, team_id)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""", tf)
    cur.executemany("""INSERT INTO results (person_id, meet_id, div_id, date,
                       grade, school, team_id)
                       VALUES (%s,%s,%s,%s,%s,%s,%s)""", xc)
    cur.execute("INSERT INTO grade_fix VALUES (%s, %s, '12', NULL, 'm', 'high')",
                (SPENCER, AY))


def _flag(cur, min_added=1):
    import pro_flag as PF
    grains = [(t, m, PF.raceGrain(cur, t))
              for t, m in (("results", "meets"), ("results_tf", "meets_tf"))]
    PF.buildRaceTables(cur, grains)
    PF.buildSeed(cur, team_levels={HERRIMAN: "hs", OTHER_HS: "hs", 7: "hs",
                                   NIKE: "club", USA_TEAM: "club"},
                 pro_teams=set())
    PF.propagate(cur, grains, min_added=min_added)
    cur.execute("SELECT person_id FROM tmp_pro_season")
    pros = {p for (p,) in cur.fetchall()}
    cur.execute("SELECT person_id FROM tmp_school_season")
    return pros, {p for (p,) in cur.fetchall()}


def test_spencer_stays_out_and_carries_nobody_in(pg):
    cur = pg.cursor()
    _seed(cur)
    pros, school = _flag(cur)
    assert SPENCER in school and SPENCER not in pros
    # ⚠ his two rows at the Prefontaine Classic are two seed rows, not one
    #   per event of the division (the old JOIN on meets_tf counted each
    #   result once per event row and seeded him alone)
    cur.execute("SELECT count(*) FROM tmp_pro_race WHERE person_id = %s",
                (SPENCER,))
    assert cur.fetchone()[0] == 2
    # ★ he is not a confirmed professional in anybody's field: the three
    #   open 1500s were four pros, Spencer and one more -- 4/6 < 0.70
    assert TAG_ALONG not in pros
    # the Herriman and Lone Peak seniors never were in question
    assert not pros & set(KIDS)


def test_the_hand_list_and_the_old_cases_hold(pg):
    cur = pg.cursor()
    _seed(cur)
    pros, school = _flag(cur)
    assert LUTKENHAUS in pros and LUTKENHAUS not in school   # hand-listed
    assert set(PROS) <= pros                                  # seeded
    assert UNATTACHED in pros                                 # 3 pro fields
    assert COLLEGIAN not in pros                              # NCAA veto


def test_the_u20_race_at_a_senior_championship_is_not_a_senior_race(pg):
    cur = pg.cursor()
    _seed(cur)
    pros, _school = _flag(cur)
    assert SENIOR_KENYAN in pros
    assert not pros & set(JUNIORS)
    import pro_flag as PF
    assert PF.seniorLabelColumns(cur, "meets") == ("division",)
    assert PF.seniorLabelColumns(cur, "meets_tf") == ("division", "event_short")
    # and without the division the old answer: every junior seeded
    cur.execute(PF.nationalTeamSeedSql("results", "meets"),
                PF.nationalTeamSeedParams())
    assert set(JUNIORS) <= {p for p, _y in cur.fetchall()}
