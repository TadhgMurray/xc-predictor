"""A high school senior's season is not a college season because of the meets
(owner's server, 2026-09-29: the blocker before the next 05_backfill).

    XCP_DB_PASSWORD=x python -m pytest -q tests/test_college_verdict_school_season.py

Two faults, both people's facts reproduced synthetically:

  (a) Tayvon Kitchen (29332123): athlete_season_level TF ay 2024 = 'college'
      (unattached winter races at college-hosted meets), grade 12 on every
      row, grade_fix 2024 '12', college_first_season 2025-08-01. His whole
      senior track season pooled college_m.
  (b) Ajani Salcido (19068584): 2021-07-02 Brooks PR, grade 12, Jesuit.
      grade_fix 2020 '12', 2021 'FR'; season levels ay 2020 hs, ay 2021
      college; first collegiate race 2021-09-23. The backfill keyed the July
      row on 2021 (a private July seam) and normalised it as college; the
      board keyed it on 2020 and rated it hs_m.

The SQL half (season_level's refusal at the source) runs against a scratch
Postgres when XCP_TWIN_TEST_DSN is set, like tests/test_twin_flag.py.
"""
import collections
import os
import sys
import types
from datetime import date

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
os.environ.setdefault("XCP_DB_QUIET", "1")
if "corrections" not in sys.modules:            # 165 MB, not in git
    _c = types.ModuleType("corrections")
    _c.__getattr__ = lambda name: {}
    sys.modules["corrections"] = _c
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in ("backfill", "racecast", "scripts", "engine"):
    _d = os.path.join(_ROOT, _p)
    if _d not in sys.path:
        sys.path.insert(0, _d)

import pytest                                                     # noqa: E402

import normalize_distance as nd                                   # noqa: E402
import pool_resolve as pr                                         # noqa: E402
# ! THE REAL database MODULE for these imports, as test_backfill_lookup_locks
#   does: in a whole-suite run an earlier test leaves a stub without dbJobs /
#   dbSetting in sys.modules. Put it back afterwards for the tests after us.
_stub = sys.modules.get("database")
if _stub is not None and not hasattr(_stub, "dbSetting"):
    del sys.modules["database"]
import backfill_normalize as B                                    # noqa: E402
import fill_ratings as fr                                         # noqa: E402
if _stub is not None:
    sys.modules["database"] = _stub
from season_year import seasonYearFromIso                         # noqa: E402

KITCHEN, SALCIDO, FRESHMAN, SENIOR, PIETER = (29332123, 19068584, 4242,
                                              5151, 6161)


@pytest.fixture(autouse=True)
def _no_db(monkeypatch):
    """The board's pro-ability read goes to a database; no verdict here."""
    monkeypatch.setattr(fr, "proAbilityFor", lambda *a, **k: None)


# ---- the backfill's pool path and the board's, on the same facts ------- #

def _tables(pid, gf, asl, first_date):
    """The server's season tables for one person, keyed as they are stored:
    grade_fix and athlete_season_level on the academic year."""
    return {"gf": {(pid, ay): v for ay, v in gf.items()},
            "asl": {(pid, ay): v for ay, v in asl.items()},
            "first": first_date}


def _backfillPool(pid, t, d, grade, school, sport="TF", gender="M",
                  source="anet"):
    """backfill_normalize._seasonPool on a synthetic stream row, with the
    lookups built the way _loadSeasonLevels builds them."""
    by_sport, combined, gradefix = {}, {}, {}
    for (p, ay), lvl in t["asl"].items():
        if isinstance(lvl, dict):
            for sp, v in lvl.items():
                if sp == "ALL":
                    combined[p * 10000 + ay] = v
                else:
                    by_sport[(p * 10000 + ay, sp)] = v
        else:
            combined[p * 10000 + ay] = lvl
    for (p, ay), v in t["gf"].items():
        gradefix[p * 10000 + ay] = v
    college_first = ({pid: nd.firstCollegeSeason(t["first"])}
                     if t["first"] else {})
    row = [None] * 14
    row[B._PERSON], row[B._DATE], row[B._GRADE] = pid, d, grade
    row[B._SRC], row[B._SCHOOL] = source, school
    return B._seasonPool(tuple(row), gender,
                         (by_sport, combined, gradefix, college_first),
                         college_first, sport)


Row = collections.namedtuple(
    "Row", "grade gender source school date season_level grade_untrusted "
           "fixed_grade fixed_level grade_verdict person_id is_pro "
           "college_first")


def _boardPool(pid, t, d, grade, school, sport="TF"):
    """fill_ratings._rowPool on the row the board query would hand it: the
    joins key every season table on seasonYearSql(r.date) -- the ONE clock."""
    ay = seasonYearFromIso(sport, d)
    lvl = t["asl"].get((pid, ay))
    if isinstance(lvl, dict):
        lvl = lvl.get(sport) or lvl.get("ALL")
    fix = t["gf"].get((pid, ay))
    row = Row(grade, "M", "anet", school, d, lvl, fix is not None,
              fix[0] if fix else None, fix[1] if fix else None, None, pid,
              False, t["first"])
    return fr._rowPool(row, sport)


SALCIDO_T = _tables(SALCIDO,
                    gf={2020: ("12", None), 2021: ("FR", None)},
                    asl={2020: "hs", 2021: "college"},
                    first_date=date(2021, 9, 23))

KITCHEN_T = _tables(KITCHEN,
                    gf={2024: ("12", None), 2025: ("FR", None)},
                    asl={2024: {"ALL": "hs", "TF": "college", "XC": "hs"},
                         2025: "college"},
                    first_date=date(2025, 8, 1))


def test_salcidos_july_row_is_his_senior_season_on_both_paths():
    """★ FAULT (b). 2021-07-02 closes season 2020 on season_year's clock, so
    both sides read grade_fix '12' and the hs verdict: hs_m, the scale his
    other senior-spring rows replay on."""
    d = "2021-07-02"
    assert B._academicYearOf(d) == seasonYearFromIso("TF", d) == 2020
    assert B._academicYearOf(date(2021, 7, 2)) == 2020
    assert _backfillPool(SALCIDO, SALCIDO_T, d, "12", "Jesuit") == "hs_m"
    assert _boardPool(SALCIDO, SALCIDO_T, d, "12", "Jesuit") == "hs_m"


def test_the_old_july_key_was_his_freshman_season():
    """What the replay's nt 1358.6 was: the backfill read ay 2021 -- grade_fix
    'FR', verdict college -- and normalised on the college anchor."""
    bad = {(SALCIDO, 2020): SALCIDO_T["gf"][(SALCIDO, 2021)]}
    t = dict(SALCIDO_T, gf=bad, asl={(SALCIDO, 2020): "college"})
    assert _backfillPool(SALCIDO, t, "2021-07-02", "12", "Jesuit") \
        == "college_m"


def test_salcidos_first_college_autumn_is_college():
    d = "2021-09-25"
    assert _backfillPool(SALCIDO, SALCIDO_T, d, "FR", "Oregon") == "college_m"
    assert _boardPool(SALCIDO, SALCIDO_T, d, "FR", "Oregon") == "college_m"


def test_kitchens_senior_track_season_is_high_school():
    """★ FAULT (a). TF ay 2024 says college, his grade is a corroborated 12,
    and his first collegiate season is 2025: the verdict does not stand, on
    either path, for every month of that season."""
    for d, school in (("2024-12-14", "Unattached"), ("2025-02-08", "Crater"),
                      ("2025-06-20", "Crater"), ("2025-07-12", "New Balance")):
        assert _backfillPool(KITCHEN, KITCHEN_T, d, "12", school) == "hs_m", d
        assert _boardPool(KITCHEN, KITCHEN_T, d, "12", school) == "hs_m", d


def test_kitchens_unattached_rows_are_screened_at_the_race_ceiling_too():
    """His winter indoor races as an unattached entrant carry the college
    ceiling (the owner's 2026-09-20 unattached rule). grade_sanity's own
    '12' for the season screens it; the raw grade alone would not."""
    kw = dict(gender="M", source="anet", school="Unattached", sport="TF",
              no_team=True, race_top_level="college", season=2024,
              college_first=date(2025, 8, 1))
    assert pr.resolvePool("12", grade_untrusted=True, fixed_grade="12",
                          **kw) == "hs_m|TF"
    # after his first collegiate season the same ceiling stands
    assert pr.resolvePool("12", grade_untrusted=True, fixed_grade="12",
                          **dict(kw, season=2025)) == "college_m|TF"


def test_kitchens_freshman_autumn_after_college_first_is_college():
    d = "2025-09-20"
    assert _backfillPool(KITCHEN, KITCHEN_T, d, "FR", "Oregon") == "college_m"
    assert _boardPool(KITCHEN, KITCHEN_T, d, "FR", "Oregon") == "college_m"


def test_a_stale_twelve_in_a_college_season_is_college_on_both_paths():
    """! THE ALIGNMENT. A freshman whose feed still writes '12' in his first
    college autumn: resolvePool's field rule takes the college verdict, and
    normPoolFor now does too -- it used to let the 12 silence it, writing a
    5000 m number for a row rated on 8000 m."""
    t = _tables(FRESHMAN, gf={}, asl={2025: "college"},
                first_date=date(2025, 8, 30))
    assert _backfillPool(FRESHMAN, t, "2025-10-04", "12", "Oregon") \
        == "college_m"
    assert _boardPool(FRESHMAN, t, "2025-10-04", "12", "Oregon") \
        == "college_m"


def test_a_senior_with_no_college_season_at_all_stays_high_school():
    """An HS senior racing unattached at college meets all year: a college
    verdict, grade 12, and no college_first_season row."""
    t = _tables(SENIOR, gf={}, asl={2025: "college"}, first_date=None)
    for d in ("2025-12-06", "2026-03-14", "2026-07-11"):
        assert _backfillPool(SENIOR, t, d, "12", "Unattached") == "hs_m", d
        assert _boardPool(SENIOR, t, d, "12", "Unattached") == "hs_m", d


# ---- the rule itself --------------------------------------------------- #

def test_the_rule_refuses_only_college_on_a_school_grade_before_college():
    v = nd.seasonVerdictFor
    assert v("college", "12", 2024, 2025) is None
    assert v("college", "12", 2024, None) is None
    assert v("college", "12", 2025, 2025) == "college"
    assert v("college", "9th", 2020, None) is None           # spellings
    assert v("college", "SO-2", 2024, None) == "college"      # a class word
    assert v("college", None, 2024, None) == "college"        # no grade
    assert v("college", "-", 2024, None) == "college"
    assert v("college", "11-12", 2024, None) == "college"     # an age band
    assert v("hs", "12", 2024, None) == "hs"
    assert v("pro", "12", 2024, None) == "pro"                 # Lutkenhaus
    # the Amherst runner: grade_sanity's 10 made of the feed's SO-2
    assert v("college", "10", 2024, None, raw_grade="SO-2") == "college"


def test_the_first_college_season_is_on_the_academic_clock():
    """first_date, never first_season (EXTRACT(year), a calendar year)."""
    assert nd.firstCollegeSeason(date(2022, 1, 15)) == 2021
    assert nd.firstCollegeSeason("2021-09-23") == 2021
    assert nd.firstCollegeSeason(2022) is None
    assert nd.firstCollegeSeason("junk") is None
    assert nd.firstCollegeSeason(None) is None


def test_a_stale_grade_verdict_keeps_the_college_ceiling():
    """Pieter Heesters: grade_sanity's stale_grade verdict carries no grade,
    so the screen is inert and his season stays what the races say."""
    assert pr.resolvePool("12", "M", "anet", "Unattached", "XC", season=2025,
                          season_level="college", grade_untrusted=True,
                          fixed_grade=None, fixed_level=None) \
        == "college_m|XC"


def test_the_engine_passes_the_date_it_already_loads():
    src = open(os.path.join(_ROOT, "engine", "speed_ratings.py"),
               encoding="utf-8").read()
    assert "college_first=None if pid is None else loadCollegeFirst().get(pid)" \
        in src
    fsrc = open(os.path.join(_ROOT, "engine", "fill_ratings.py"),
                encoding="utf-8").read()
    assert 'college_first=getattr(row, "college_first", None)' in fsrc


# ---- the board query carries the date ---------------------------------- #

class _Cur:
    def __init__(self, have): self.have = have; self.rows = []
    def execute(self, sql, params=None):
        if "to_regclass" in sql:
            self.rows = [("x" if self.have else None,)]
        else:
            self.rows = [(1,)] if self.have else []
    def fetchone(self): return self.rows[0] if self.rows else None
    def __enter__(self): return self
    def __exit__(self, *a): return False


class _Conn:
    def __init__(self, have): self.have = have
    def cursor(self): return _Cur(self.have)


def test_the_board_query_joins_college_first_season_where_it_exists():
    import build_ranking_results as brr
    for sport in ("XC", "TF"):
        with_t = brr._sourceSql(_Conn(True), sport)
        without = brr._sourceSql(_Conn(False), sport)
        assert "LEFT JOIN college_first_season cfs" in with_t
        assert "cfs.first_date AS college_first" in with_t
        assert "JOIN college_first_season" not in without
        assert "NULL::date AS college_first" in without
        assert "__COLLEGE_FIRST" not in with_t + without
        assert "__COLLEGE_FIRST" not in fr._sqlFor(sport)


# ---- the backfill's scale-split census --------------------------------- #

def test_the_census_counts_a_row_written_on_another_pools_anchor():
    split = {}
    row = [None] * 14
    row[B._RATING_POOL] = "college_m|TF"
    B._countSplit(split, tuple(row), "hs_m")                 # Kitchen
    row[B._RATING_POOL] = "pro_m"
    B._countSplit(split, tuple(row), "college_m")            # rated on college
    row[B._RATING_POOL] = "hs_m"
    B._countSplit(split, tuple(row), "hs_m")
    B._countSplit(split, tuple([None] * 13), "hs_m")         # no column
    assert split == {("hs_m", "college_m"): 1}


def test_the_stream_carries_the_rating_pool_last():
    cfg = B._configFor("TF")
    assert "r.rating_pool\n" in B._streamSQL(cfg, rating_pool=True)
    assert "NULL::text AS rating_pool" in B._streamSQL(cfg)
    assert B._RATING_POOL == 13 and B._SCHOOL == 12


# ---- the source: season_level's refusal on a scratch Postgres ---------- #

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


def _seed(cur, college_first=True):
    import level_graph
    cur.execute("""
        DROP TABLE IF EXISTS results, results_tf, race_level, meets, meets_tf,
                             grade_fix, college_first_season,
                             athlete_season_level;
        CREATE TABLE results (result_id bigint, person_id bigint,
            meet_id bigint, div_id bigint, source text, date text,
            grade text);
        CREATE TABLE results_tf (LIKE results);
        CREATE TABLE meets (meet_id bigint, div_id bigint, source text,
                            level_mask int);
        CREATE TABLE meets_tf (meet_id bigint, div_id bigint,
                               level_mask int);
        CREATE TABLE race_level (race bigint PRIMARY KEY, level text);
        CREATE TABLE grade_fix (person_id bigint, season int, grade text,
                                level text, method text, trust text);
        CREATE TABLE college_first_season (person_id bigint PRIMARY KEY,
                                           first_season int, first_date date);
    """)
    rows = [
        # Kitchen: unattached indoor at college meets (meet 1), grade 12
        (1, KITCHEN, 1, 1, "anet", "2024-12-14", "12"),
        (2, KITCHEN, 1, 1, "anet", "2025-01-18", "12"),
        (3, KITCHEN, 1, 1, "anet", "2025-02-08", "12"),
        # his freshman autumn indoor: a college season
        (4, KITCHEN, 1, 1, "anet", "2025-12-06", "FR"),
        # a senior with no college season at all
        (5, SENIOR, 1, 1, "anet", "2025-12-06", "12"),
        (6, SENIOR, 1, 1, "anet", "2026-02-07", "12"),
        # the Amherst runner: SO-2 in the feed, grade_sanity's 10
        (7, FRESHMAN, 1, 1, "tfrrs", "2025-03-01", "SO-2"),
        # Pieter: a stale 12, grade_sanity said stale_grade
        (8, PIETER, 1, 1, "anet", "2026-03-01", "12"),
    ]
    cur.executemany("INSERT INTO results_tf VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    rows)
    cur.execute(f"""INSERT INTO race_level
                    SELECT DISTINCT {level_graph._raceKeyExpr('r')}, 'college'
                    FROM results_tf r""")
    cur.executemany("INSERT INTO grade_fix VALUES (%s,%s,%s,%s,'m','high')", [
        (KITCHEN, 2024, "12", None), (KITCHEN, 2025, "FR", None),
        (FRESHMAN, 2024, "10", None), (PIETER, 2025, None, "pro")])
    if college_first:
        # first_season is the CALENDAR year; the rule must read first_date
        cur.execute("""INSERT INTO college_first_season VALUES
                       (%s, 2025, '2025-08-01')""", (KITCHEN,))


def _levels(cur):
    cur.execute("""SELECT person_id, ay, sport, level, refused
                   FROM athlete_season_level WHERE sport = 'TF'""")
    return {(p, a): (lvl, ref) for p, a, _s, lvl, ref in cur.fetchall()}


def test_the_source_refuses_kitchens_senior_verdict(pg):
    import season_level as SL
    cur = pg.cursor()
    _seed(cur)
    SL._collectVotes(cur)
    SL._resolveSeasons(cur)
    got = _levels(cur)
    assert got[(KITCHEN, 2024)] == (None, "college")         # refused
    assert got[(KITCHEN, 2025)] == ("college", None)         # a college season
    assert got[(SENIOR, 2025)] == (None, "college")          # no college at all
    assert got[(FRESHMAN, 2024)] == ("college", None)        # Amherst: SO-2
    assert got[(PIETER, 2025)] == ("college", None)          # stale_grade
    # and the 'ALL' fallback is refused alongside, so a COALESCE cannot
    # bring the verdict back
    cur.execute("""SELECT level FROM athlete_season_level
                   WHERE person_id = %s AND ay = 2024 AND sport = 'ALL'""",
                (KITCHEN,))
    assert cur.fetchone()[0] is None
    # the census' Python twin gives the same answers
    cur.execute("""SELECT asl.person_id, asl.ay, gf.person_id IS NOT NULL,
                          gf.grade, asl.n_school, asl.n_class,
                          asl.refused IS NOT NULL
                   FROM athlete_season_level asl
                   LEFT JOIN grade_fix gf ON gf.person_id = asl.person_id
                                         AND gf.season = asl.ay
                   WHERE asl.sport = 'TF'""")
    for pid, ay, has, fg, ns, nc, refused in cur.fetchall():
        before = not (pid == KITCHEN and ay >= 2025)
        assert (SL.seasonIsSchool(has, fg, ns, nc) and before) == refused, \
            (pid, ay)


def test_the_source_without_college_first_season_refuses_every_school_season(pg):
    import season_level as SL
    cur = pg.cursor()
    _seed(cur, college_first=False)
    cur.execute("DROP TABLE college_first_season")
    SL._collectVotes(cur)
    SL._resolveSeasons(cur)
    got = _levels(cur)
    assert got[(KITCHEN, 2024)] == (None, "college")
    assert got[(KITCHEN, 2025)] == ("college", None)         # FR: not school
    assert got[(FRESHMAN, 2024)] == ("college", None)


def test_the_dry_run_census_reads_the_stored_table(pg, capsys):
    """scripts/college_veto_census.py against the table the OLD builder
    wrote -- no n_school, no refused column -- counts Kitchen and the senior,
    and the July-seam count finds Salcido's row."""
    import college_veto_census as CV
    cur = pg.cursor()
    _seed(cur)
    cur.execute("""
        CREATE TABLE athlete_season_level (person_id bigint, ay int,
            sport text, level text, unanimous boolean);
        INSERT INTO athlete_season_level VALUES
            (%(k)s, 2024, 'TF', 'college', true),
            (%(k)s, 2024, 'ALL', 'hs', false),
            (%(k)s, 2025, 'TF', 'college', true),
            (%(s)s, 2025, 'TF', 'college', true),
            (%(f)s, 2024, 'TF', 'college', true);
        INSERT INTO results_tf VALUES
            (9, %(j)s, 2, 2, 'anet', '2021-07-02', '12');
        INSERT INTO grade_fix VALUES (%(j)s, 2020, '12', NULL, 'm', 'high'),
                                     (%(j)s, 2021, 'FR', NULL, 'm', 'high');
    """, {"k": KITCHEN, "s": SENIOR, "f": FRESHMAN, "j": 777})
    CV.faultA(cur, 5)
    CV.faultB(cur, 5)
    out = capsys.readouterr().out
    assert "REFUSED on rebuild: 2 verdict rows (TF 2)" in out
    assert f"person {KITCHEN}  ay 2024" in out
    assert "TF: 1 July rows; 1 read different facts" in out
    assert "1 of them a college class where the right season has a school" \
        in out
