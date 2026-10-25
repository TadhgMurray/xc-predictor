"""03 pro_flag runs faster and answers exactly as before (2026-10-25, owner:
"freedom as long as it remains accurate and working").

    python -m pytest -q tests/test_pro_flag_speed.py
    XCP_TWIN_TEST_DSN=postgresql://... python -m pytest -q tests/test_pro_flag_speed.py

The step took 3,096 s on the server; 1,870 s of it was the post-collegiate
team levels and 602 s the school veto. Four changes, all in SQL:

  1. buildTeamSeasons fences its subquery with OFFSET 0, so a row's team
     level is worked out once instead of once per FILTER (four times), and
     _rowLevelSql reads the level once (COALESCE) instead of twice.
  2. The pro-team name test (pool_resolve._normSchool in SQL, two regex
     passes) is guarded by a LIKE the name implies, so a row that cannot
     read "HOKA ..." skips the regexes.
  3. The school veto asks the name only of a school-team row (a CASE), not
     of every row the scan reads.
  4. buildPostCollegiate's `two` compares each person's latest school
     season with the first college one, instead of probing the index once
     per collegian.

The first half needs no database: it checks the guard's one promise -- a
school that normalizes to a _PRO_TEAMS name always passes the LIKE -- on
the Python twin of the normalization, over hand-picked and random schools.
The second half runs the PRE-CHANGE SQL (copied verbatim below) and the new
SQL side by side on a scratch Postgres, row by row and table by table, when
XCP_TWIN_TEST_DSN is set, like tests/test_pro_post_collegiate.py.
"""
import os
import random
import re
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
import pro_flag as PF                                             # noqa: E402

SCHOOLS = [
    "HOKA NAZ Elite", "Hoka One One", "HOKA", "hoka-njnytc", "HOKA NJNYTC",
    "HOKA  NAZ  Elite.", "Hoka  One One!", " hoka ", "H.O.K.A.", "Hoka\tOne One",
    "Hoka\u00a0One One", "Hoka\nNAZ Elite", "HOKA NAZ-Elite", "Hokaa",
    "HOKA NAZ Elite Team", "Team HOKA", "\u0130stanbul Hoka", "Hoka\u00df",
    "Shoreline Lakes Academy", "Hopkins Academy", "Herriman", "Unattached",
    "Atlanta Track Club Elite", "Nike", "", None, "  ", "!!!", "h o k a",
    "Ho-Ka", "HOKA ONE ONE (USA)", "hoka one one", "HoKa NjNyTc",
]


def _like(pattern, s):
    """SQL LIKE for the patterns _proTeamNameSql writes (% and letters)."""
    rx = ".*".join(re.escape(part) for part in pattern.split("%"))
    return re.fullmatch(rx, s, flags=re.DOTALL) is not None


def _guards(sql):
    """The LIKE patterns in a _proTeamNameSql() string."""
    return re.findall(r"LIKE '([^']*)'", sql)


def _guardPasses(sql, school):
    low = (school or "").lower()
    pats = _guards(sql)
    return "TRUE" in sql.split(" AND ")[0] or any(_like(p, low) for p in pats)


# ---- 1. the guard never turns away a name that matches ----------------- #

def test_the_guard_is_one_pattern_today():
    assert _guards(PF._proTeamNameSql()) == ["%h%o%k%a%"]


@pytest.mark.parametrize("school", SCHOOLS)
def test_every_school_that_reads_a_pro_team_passes_the_guard(school):
    sql = PF._proTeamNameSql()
    if pr._normSchool(school) in pr._PRO_TEAMS:
        assert _guardPasses(sql, school)


def test_random_schools_never_slip_past_the_guard():
    rng = random.Random(20261025)
    alphabet = ("hokaHOKA nNjJyYtTcCeElLiIzZ" + "-.!()'\t\u00a0\u0130\u00df"
                + "abcdefgxyz0123456789")
    words = sorted(pr._PRO_TEAMS) + ["club", "elite", "tc", "high school"]
    hits = 0
    sql = PF._proTeamNameSql()
    for _ in range(20000):
        if rng.random() < 0.5:
            s = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 18)))
        else:                       # a real name, then roughed up
            s = "".join(c.upper() if rng.random() < 0.3 else c
                        for c in rng.choice(words))
            s = "".join(c + (rng.choice("-. \t") if rng.random() < 0.15 else "")
                        for c in s)
        if pr._normSchool(s) in pr._PRO_TEAMS:
            hits += 1
            assert _guardPasses(sql, s), repr(s)
    assert hits > 1000                      # the test exercised the matches


def test_odd_names_keep_the_guard_honest(monkeypatch):
    """Names with punctuation, digits, a quote, or nothing alphanumeric:
    the guard is looser, never stricter."""
    odd = frozenset({"hoka", "b.a.a. 1", "o'neill tc", "---", "zz top 2"})
    monkeypatch.setattr(pr, "_PRO_TEAMS", odd)
    sql = PF._proTeamNameSql()
    assert "'o''neill tc'" in sql           # the IN list still quotes
    assert "TRUE" in sql                    # '---' has no letters: no guard
    for s in SCHOOLS + ["B.A.A. 1", "O'Neill TC", "ZZ Top 2!"]:
        if pr._normSchool(s) in odd:
            assert _guardPasses(sql, s)


def test_only_the_shortest_patterns_are_kept(monkeypatch):
    monkeypatch.setattr(pr, "_PRO_TEAMS",
                        frozenset({"hoka one one", "hoka", "nike oregon tc",
                                   "nike otc", "ab"}))
    assert _guards(PF._proTeamNameSql()) == ["%a%b%", "%h%o%k%a%",
                                             "%n%i%k%e%o%t%c%"]


# ---- 2. the old SQL and the new, side by side --------------------------- #
#
# ! VERBATIM FROM engine/pro_flag.py BEFORE 2026-10-25. Do not "fix" these:
#   they are the reference the new SQL is held to.

def _oldSchoolTeamSql(cur, results):
    from pool_resolve import _PRO_TEAMS
    has_team = PF.hasColumn(cur, results, "team_id")
    has_slug = PF.hasColumn(cur, results, "team_slug")
    slug = ("(split_part(r.team_slug, '_', 3) <> '' AND "
            "lower(split_part(r.team_slug, '_', 2)) IN ('hs', 'ms'))"
            if has_slug else "FALSE")
    if has_team:
        joins = """
            LEFT JOIN tmp_team_level tl ON tl.team_id = r.team_id
            LEFT JOIN tmp_pro_team pt ON pt.team_id = r.team_id"""
        level = f"""CASE WHEN r.team_id = 0 THEN FALSE
                         WHEN pt.team_id IS NOT NULL THEN FALSE
                         WHEN tl.team_id IS NOT NULL THEN tl.school
                         ELSE COALESCE({slug}, FALSE) END"""
    else:
        joins, level = "", f"COALESCE({slug}, FALSE)"
    norm = ("btrim(regexp_replace(regexp_replace(lower(COALESCE(r.school, '')),"
            " '[^a-z0-9 ]+', '', 'g'), '\\s+', ' ', 'g'))")
    names = ", ".join("'" + n.replace("'", "''") + "'" for n in sorted(_PRO_TEAMS))
    return joins, f"({level}) AND {norm} NOT IN ({names})"


def _oldRowLevelSql(cur, results):
    from pool_resolve import _PRO_TEAMS
    has_team = PF.hasColumn(cur, results, "team_id")
    has_slug = PF.hasColumn(cur, results, "team_slug")
    lv_list = ", ".join(f"'{x}'" for x in PF._ROW_LEVELS)
    slug = (f"CASE WHEN split_part(r.team_slug, '_', 3) <> '' AND "
            f"lower(split_part(r.team_slug, '_', 2)) IN ({lv_list}) "
            f"THEN lower(split_part(r.team_slug, '_', 2)) END"
            if has_slug else "NULL::text")
    if has_team:
        joins = """
            LEFT JOIN tmp_team_lv tv ON tv.team_id = r.team_id
            LEFT JOIN tmp_pro_team pt ON pt.team_id = r.team_id"""
        level = f"""CASE WHEN r.team_id = 0 THEN 'none'
                         WHEN pt.team_id IS NOT NULL THEN 'pro'
                         WHEN tv.team_id IS NOT NULL THEN tv.level
                         ELSE {slug} END"""
    else:
        joins, level = "", slug
    norm = ("btrim(regexp_replace(regexp_replace(lower(COALESCE(r.school, '')),"
            " '[^a-z0-9 ]+', '', 'g'), '\\s+', ' ', 'g'))")
    names = ", ".join("'" + n.replace("'", "''") + "'" for n in sorted(_PRO_TEAMS))
    team = ("(r.team_id IS NOT NULL AND r.team_id <> 0)" if has_team else "FALSE")
    if has_slug:
        team = f"({team} OR r.team_slug IS NOT NULL)"
    expr = f"""CASE WHEN {norm} IN ({names}) THEN 'pro'
                    WHEN ({level}) IS NOT NULL THEN ({level})
                    WHEN {team} THEN 'other'
               END"""
    return joins, expr


def _oldTeamSeasons(cur):
    """buildTeamSeasons before the OFFSET 0, into tmp_team_season_old."""
    cur.execute("DROP TABLE IF EXISTS tmp_team_rows_old")
    cur.execute("""CREATE TEMP TABLE tmp_team_rows_old
                   (person_id bigint, yr int, n_school int, n_college int,
                    n_club int, n_other int)""")
    for results in ("results", "results_tf"):
        joins, lv = _oldRowLevelSql(cur, results)
        cur.execute(f"""
            INSERT INTO tmp_team_rows_old
            SELECT person_id, yr,
                   count(*) FILTER (WHERE lv IN ('hs', 'ms', 'elem')),
                   count(*) FILTER (WHERE lv = 'college'),
                   count(*) FILTER (WHERE lv IN ('club', 'pro')),
                   count(*) FILTER (WHERE lv = 'other')
            FROM (SELECT r.person_id, {PF._YR} AS yr, {lv} AS lv
                  FROM {results} r
                  {joins}
                  WHERE r.person_id IS NOT NULL
                    AND r.date ~ '^(19|20)[0-9]{{2}}') x
            GROUP BY 1, 2""")
    cur.execute("DROP TABLE IF EXISTS tmp_team_season_old")
    cur.execute("""CREATE TEMP TABLE tmp_team_season_old AS
                   SELECT person_id, yr, sum(n_school)::int AS n_school,
                          sum(n_college)::int AS n_college,
                          sum(n_club)::int AS n_club,
                          sum(n_other)::int AS n_other
                   FROM tmp_team_rows_old GROUP BY 1, 2""")


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


HS, MS, COLLEGE, CLUB, PRO_TEAM, UNLEVELLED = 11, 12, 13, 14, 15, 16
LEVELS = {HS: "hs", MS: "ms", COLLEGE: "college", CLUB: "club",
          PRO_TEAM: "club", 17: "elem"}
TEAM_IDS = [None, 0, HS, MS, COLLEGE, CLUB, PRO_TEAM, UNLEVELLED, 17]
SLUGS = [None, "UT_hs_m_Herriman", "GA_college_m_Georgia", "GA_club_m_ATC",
         "CA_ms_f_Z", "x_HS_y", "x_elem_y", "x_hs_", "bad", "", "a_pro_b"]
GRADES = ["12", "9th", "SR", "SR-4", "-", None]
DATES = ["2019-09-14", "2020-03-01", "2021-10-02", "2024-05-05", "1899-01-01",
         "bad"]
# the four shapes a results table may take (hasColumn decides the SQL)
SHAPES = {"results": ("team_id", "team_slug"), "results_tf": ("team_id",),
          "eq_slug_only": ("team_slug",), "eq_bare": ()}


def _seed(cur):
    cur.execute("DROP TABLE IF EXISTS results, results_tf, eq_slug_only, "
                "eq_bare, grade_fix")
    rng = random.Random(7)
    for table, cols in SHAPES.items():
        extra = "".join(f", {c} {'bigint' if c == 'team_id' else 'text'}"
                        for c in cols)
        cur.execute(f"""CREATE TABLE {table} (result_id bigserial,
                        person_id bigint, meet_id bigint, div_id bigint,
                        event_id bigint, date text, grade text,
                        school text{extra})""")
        rows = []
        for i, school in enumerate(SCHOOLS):
            for t in TEAM_IDS:
                for s in SLUGS:
                    rows.append((1 + (i * 7 + (t or 0)) % 40,
                                 rng.choice(DATES), rng.choice(GRADES),
                                 school, t, s))
        names = ", ".join(("person_id", "date", "grade", "school") + cols)
        keep = {"team_id": 4, "team_slug": 5}
        cur.executemany(
            f"INSERT INTO {table} ({names}) VALUES "
            f"({', '.join(['%s'] * (4 + len(cols)))})",
            [r[:4] + tuple(r[keep[c]] for c in cols) for r in rows])


def _teams(cur):
    """tmp_team_level, tmp_team_lv, tmp_pro_team, as buildSchoolSeasons
    writes them."""
    PF.buildSchoolSeasons(cur, team_levels=LEVELS, pro_teams={PRO_TEAM})


def test_the_school_team_predicate_is_the_same_row_by_row(pg):
    with pg.cursor() as cur:
        _seed(cur)
        _teams(cur)
        for table in SHAPES:
            oj, old = _oldSchoolTeamSql(cur, table)
            nj, new = PF._schoolTeamSql(cur, table)
            assert oj == nj
            cur.execute(f"""SELECT r.result_id, ({old}), ({new})
                            FROM {table} r {nj} ORDER BY 1""")
            rows = cur.fetchall()
            assert len(rows) == len(SCHOOLS) * len(TEAM_IDS) * len(SLUGS)
            assert all(o is n for _i, o, n in rows), table
            if SHAPES[table]:               # no team and no slug: never school
                assert {o for _i, o, _n in rows} == {True, False}, table


def test_the_row_level_is_the_same_row_by_row(pg):
    with pg.cursor() as cur:
        _seed(cur)
        _teams(cur)
        for table in SHAPES:
            oj, old = _oldRowLevelSql(cur, table)
            nj, new = PF._rowLevelSql(cur, table)
            assert oj == nj
            cur.execute(f"""SELECT r.result_id, ({old}), ({new})
                            FROM {table} r {nj} ORDER BY 1""")
            rows = cur.fetchall()
            bad = [(i, o, n) for i, o, n in rows if o != n]
            assert not bad, (table, bad[:5])
            seen = {o for _i, o, _n in rows}
            if table == "results":                    # every branch reached
                assert seen >= {"pro", "none", "hs", "ms", "elem", "college",
                                "club", "other", None}


def test_team_seasons_are_the_same_table(pg):
    with pg.cursor() as cur:
        _seed(cur)
        _teams(cur)
        _oldTeamSeasons(cur)
        PF.buildTeamSeasons(cur)
        cols = "person_id, yr, n_school, n_college, n_club, n_other"
        cur.execute(f"SELECT {cols} FROM tmp_team_season_old ORDER BY 1, 2")
        old = cur.fetchall()
        cur.execute(f"SELECT {cols} FROM tmp_team_season ORDER BY 1, 2")
        assert cur.fetchall() == old
        assert len(old) > 50 and any(r[2] and r[4] for r in old)


def test_the_level_is_worked_out_once_per_row(pg):
    """OFFSET 0 is what keeps the level out of the four FILTERs. Counted,
    not read off a plan (EXPLAIN prints the expression either way): the
    level is wrapped in a function that says each time it is called."""
    class Count:                    # not a list: psycopg2 trims lists to 50
        n = 0

        def append(self, notice):
            self.n += "level worked out" in notice

    with pg.cursor() as cur:
        _seed(cur)
        _teams(cur)
        cur.execute("""CREATE FUNCTION pg_temp.said(t text) RETURNS text
                       LANGUAGE plpgsql IMMUTABLE AS $$
                       BEGIN RAISE NOTICE 'level worked out'; RETURN t; END $$""")
        live = "r.person_id IS NOT NULL AND r.date ~ '^(19|20)[0-9]{2}'"
        cur.execute(f"SELECT count(*) FROM results r WHERE {live}")
        n_rows = cur.fetchone()[0]
        joins, lv = PF._rowLevelSql(cur, "results")
        for fence, per_row in (("", 4), ("OFFSET 0", 1)):
            pg.notices = Count()
            cur.execute(f"""
                SELECT person_id, yr,
                       count(*) FILTER (WHERE lv IN ('hs', 'ms', 'elem')),
                       count(*) FILTER (WHERE lv = 'college'),
                       count(*) FILTER (WHERE lv IN ('club', 'pro')),
                       count(*) FILTER (WHERE lv = 'other')
                FROM (SELECT r.person_id, {PF._YR} AS yr,
                             pg_temp.said({lv}) AS lv
                      FROM results r {joins}
                      WHERE {live} {fence}) x
                GROUP BY 1, 2""")
            cur.fetchall()
            assert pg.notices.n == per_row * n_rows, fence


# the post-collegiate query before 2026-10-25, verbatim but for its name
_OLD_POST_COLLEGIATE = """
        CREATE TEMP TABLE tmp_post_collegiate_old AS
        WITH col AS (
            SELECT person_id, yr FROM tmp_team_season WHERE n_college > 0
            UNION SELECT person_id, yr FROM tmp_ncaa_season),
        two AS (
            SELECT DISTINCT t.person_id
            FROM tmp_team_season t
            JOIN (SELECT person_id, min(yr) AS y0 FROM col GROUP BY 1) f
              ON f.person_id = t.person_id
            WHERE t.n_school > 0 AND t.yr > f.y0)
        SELECT s.person_id, s.yr, s.n_club, s.n_other,
               min(c.yr) AS first_college, max(c.yr) AS last_college
        FROM tmp_team_season s
        JOIN col c ON c.person_id = s.person_id AND c.yr < s.yr
        WHERE s.n_school = 0 AND s.n_college = 0
          AND s.n_club + s.n_other >= {min_races}
          AND NOT EXISTS (SELECT 1 FROM tmp_ncaa_season n
                          WHERE n.person_id = s.person_id AND n.yr = s.yr)
          AND NOT EXISTS (SELECT 1 FROM tmp_school_season n
                          WHERE n.person_id = s.person_id AND n.yr = s.yr)
          AND NOT EXISTS (SELECT 1 FROM tmp_pro_season p
                          WHERE p.person_id = s.person_id AND p.yr = s.yr)
          AND NOT EXISTS (SELECT 1 FROM two WHERE two.person_id = s.person_id)
        GROUP BY s.person_id, s.yr, s.n_club, s.n_other"""


def test_post_collegiate_is_the_same_table(pg, monkeypatch):
    """Random careers -- school, college, club, unattached seasons in any
    order, NCAA and school-veto and pro seasons sprinkled over them, a NULL
    year here and there -- and the old query and the new agree exactly."""
    rng = random.Random(1025)
    team, ncaa, school, pro = [], [], [], []
    for person in range(1, 3001):
        for yr in sorted(rng.sample(range(2010, 2026), rng.randint(1, 9))):
            yr = None if rng.random() < 0.01 else yr
            team.append((person, yr, rng.choice([0, 0, 0, 1, 4]),
                         rng.choice([0, 0, 1, 3]), rng.choice([0, 1, 2, 3, 5]),
                         rng.choice([0, 0, 1, 2])))
            if rng.random() < 0.05:
                ncaa.append((person, yr))
            if rng.random() < 0.05:
                school.append((person, yr))
            if rng.random() < 0.03:
                pro.append((person, yr, 3, 0))
    with pg.cursor() as cur:
        for name, cols, rows in (
                ("tmp_team_season", "person_id bigint, yr int, n_school int, "
                 "n_college int, n_club int, n_other int", team),
                ("tmp_ncaa_season", "person_id bigint, yr int", ncaa),
                ("tmp_school_season", "person_id bigint, yr int", school),
                ("tmp_pro_season", "person_id bigint, yr int, pro_races int, "
                 "round int", pro)):
            cur.execute(f"DROP TABLE IF EXISTS {name}")
            cur.execute(f"CREATE TEMP TABLE {name} ({cols})")
            if rows:
                ph = ", ".join(["%s"] * len(rows[0]))
                cur.executemany(f"INSERT INTO {name} VALUES ({ph})", rows)
        cur.execute("CREATE INDEX ON tmp_team_season (person_id, yr)")
        cur.execute("ANALYZE tmp_team_season")
        cur.execute(_OLD_POST_COLLEGIATE.format(min_races=PF._MIN_PRO_RACES))
        monkeypatch.setattr(PF, "buildTeamSeasons", lambda _cur: None)
        PF.buildPostCollegiate(cur)
        cols = "person_id, yr, n_club, n_other, first_college, last_college"
        cur.execute(f"SELECT {cols} FROM tmp_post_collegiate_old ORDER BY 1, 2")
        old = cur.fetchall()
        cur.execute(f"SELECT {cols} FROM tmp_post_collegiate ORDER BY 1, 2")
        assert cur.fetchall() == old
        assert len(old) > 50                    # the rule fired
        # and `two` mattered: some people were taken out by it
        cur.execute("""SELECT count(DISTINCT s.person_id)
                       FROM tmp_team_season s
                       JOIN tmp_team_season c ON c.person_id = s.person_id
                                             AND c.n_college > 0
                                             AND c.yr < s.yr
                       WHERE s.n_school > 0""")
        assert cur.fetchone()[0] > 50
