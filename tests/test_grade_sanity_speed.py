# Project: xc-predictor / tests
# File:    test_grade_sanity_speed.py
# Purpose: the 2026-10-25 speed work on grade_sanity changed how the answer
#          is computed and nothing about the answer. Each new path is run
#          beside the old one it replaced, on the same input.
#
# ★ WHY (run log, 04_grade_sanity 4,292 s on the critical path; owner: "you
#   can multithread/core as necessary. You have freedom as long as it
#   remains accurate and working"). The slow phases were rule 4 (826 s), the
#   allraces build (420 s), 5f (379 s), the write (349 s), 5e's race count
#   (286 s) and rule 7's staging (219 s). The changes:
#     - the build's SQL functions say PARALLEL SAFE (they were unsafe by
#       default, which kept every statement calling one on one core);
#     - rule 4 runs as ANALYZEd UNLOGGED stages with nested loops off, its
#       mode() as a count per level, and its season table is 5e's race count;
#     - rule 5f's candidates are UNLOGGED, not TEMP, its `mine` a table, and
#       its team codes are read in a thread while 5e counts;
#     - grade_fix is written with COPY, grade_untrusted from grade_fix.
#
# The pure-Python half runs anywhere. The SQL half needs a scratch Postgres,
# like test_thin_field_sql.py, and skips without one:
#   XCP_TWIN_TEST_DSN="host=/tmp/pgtest port=54329 user=postgres dbname=postgres"
import io
import os
import random
import sys
from concurrent.futures import Future

import pytest

import _env  # noqa: F401  -- sets XCP_DB_PASSWORD, must precede config

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in ("engine", "scripts"):
    sys.path.insert(0, os.path.join(ROOT, p))

import grade_sanity as GS                                        # noqa: E402

DB = "xcp_grade_speed_test"


# ------------------------------------------------------------------ #
#  THE OLD CODE, VERBATIM -- the reference every test compares against
# ------------------------------------------------------------------ #

# grade_sanity._FIELD_SQL before 2026-10-25 (comments dropped), with the
# 2026-10-26 rules written into it the slow way: a tie goes to the HIGHER
# level (owner), and a season's events are counted in races, not rows; and
# the 2026-10-27 one: an open race casts no vote for its ungraded runners
_RANK = "CASE {x} WHEN 'pro' THEN 5 WHEN 'college' THEN 4 WHEN 'hs' THEN 3 WHEN 'ms' THEN 2 WHEN 'elem' THEN 1 ELSE 0 END"
OLD_FIELD_SQL = f"""
    WITH by_grade AS (
        SELECT person_id, acad, grade_folded AS grade,
               count(DISTINCT race) AS n
        FROM   allraces
        WHERE  grade_folded IS NOT NULL
        GROUP  BY 1, 2, 3
    ),
    best AS (
        SELECT person_id, acad, max(n) AS top_n
        FROM   by_grade GROUP BY 1, 2
    ),
    needy AS (
        SELECT a.person_id, a.acad
        FROM   (SELECT DISTINCT person_id, acad FROM allraces) a
        LEFT   JOIN best b
               ON b.person_id = a.person_id AND b.acad = a.acad
        WHERE  b.top_n IS NULL OR b.top_n < %(minc)s
    ),
    wanted AS (
        SELECT DISTINCT a.meet_id, a.div_id, a.source, a.event_key
        FROM   needy n
        JOIN   allraces a ON a.person_id = n.person_id AND a.acad = n.acad
    ),
    event_level AS (
        SELECT w.meet_id, w.div_id, w.source, w.event_key,
               CASE WHEN count(*) FILTER (WHERE a.grade IS NOT NULL) = 0
                    THEN 'pro'
                    ELSE (SELECT gradeLevel(a2.grade) FROM allraces a2
                          WHERE  a2.meet_id = w.meet_id AND a2.div_id = w.div_id
                            AND  a2.source = w.source AND a2.event_key = w.event_key
                            AND  gradeLevel(a2.grade) IS NOT NULL
                          GROUP  BY 1
                          ORDER  BY count(*) DESC, {_RANK.format(x="gradeLevel(a2.grade)")} DESC
                          LIMIT  1)
               END AS level,
               count(*) - count(*) FILTER (WHERE gradeLevel(a.grade) IS NOT NULL)
                   >= OPEN_SHARE_TOKEN * count(*) AS open_field,
               count(*) AS n_entrants,
               count(*) FILTER (WHERE a.grade IS NOT NULL) AS n_graded
        FROM   wanted w
        JOIN   allraces a
               ON a.meet_id = w.meet_id AND a.div_id = w.div_id
              AND a.source = w.source AND a.event_key = w.event_key
        GROUP  BY 1, 2, 3, 4
        HAVING count(*) FILTER (WHERE gradeLevel(a.grade) IS NOT NULL)
                   >= {GS.MIN_FIELD_GRADED}
            OR (count(*) FILTER (WHERE a.grade IS NOT NULL) = 0
                AND count(*) >= {GS.MIN_FIELD_GRADED})
    )
    SELECT DISTINCT ON (n.person_id, n.acad)
           n.person_id, n.acad, e.level
    FROM   needy n
    JOIN   allraces a ON a.person_id = n.person_id AND a.acad = n.acad
    JOIN   event_level e
           ON e.meet_id = a.meet_id AND e.div_id = a.div_id
          AND e.source = a.source AND e.event_key = a.event_key
    WHERE  e.level IS NOT NULL
      AND  NOT (e.open_field AND e.level <> 'pro' AND gradeLevel(a.grade) IS NULL)
    GROUP  BY n.person_id, n.acad, e.level
    ORDER  BY n.person_id, n.acad, count(DISTINCT a.race) DESC, {_RANK.format(x="e.level")} DESC
"""

def oldField(share=None):
    """OLD_FIELD_SQL with the open-race share (GS.OPEN_FIELD_SHARE, off by
    default) laid in, as GS.fieldBuild lays it into the new stages."""
    return OLD_FIELD_SQL.replace("OPEN_SHARE_TOKEN", repr(float(
        GS.OPEN_FIELD_SHARE if share is None else share)))


# rule 5e's own count before 2026-10-25
OLD_RACE_COUNT_SQL = """
    SELECT person_id, acad, count(DISTINCT race) AS races
    FROM   allraces GROUP BY 1, 2
"""

# grade_sanity._TEAM_LEVEL_SQL before 2026-10-25, `mine` a CTE
OLD_TEAM_LEVEL_SQL = f"""
    WITH teams AS (
        SELECT r.person_id, {GS._ACAD_R} AS acad,
               COALESCE(t.level = ANY(%(codes)s), false) AS hs
        FROM   results r
        JOIN   tl_cand c ON c.person_id = r.person_id
        LEFT   JOIN anet_team t ON t.team_id = r.team_id
        WHERE  r.date IS NOT NULL
        UNION ALL
        SELECT r.person_id, {GS._ACAD_R},
               COALESCE(t.level = ANY(%(codes)s), false)
        FROM   results_tf r
        JOIN   tl_cand c ON c.person_id = r.person_id
        LEFT   JOIN anet_team t ON t.team_id = r.team_id
        WHERE  r.date IS NOT NULL
    ),
    team_ok AS (
        SELECT t.person_id, t.acad
        FROM   teams t
        JOIN   tl_cand c ON c.person_id = t.person_id AND c.acad = t.acad
        GROUP  BY 1, 2
        HAVING bool_and(t.hs)
    ),
    mine AS (
        SELECT a.person_id, a.acad, a.meet_id, a.div_id, a.source, a.event_key
        FROM   allraces a
        JOIN   team_ok k ON k.person_id = a.person_id AND k.acad = a.acad
    ),
    wanted AS (
        SELECT DISTINCT meet_id, div_id, source, event_key FROM mine
    ),
    event_level AS (
        SELECT w.meet_id, w.div_id, w.source, w.event_key,
               mode() WITHIN GROUP (ORDER BY gradeLevel(a.grade))
                   FILTER (WHERE gradeLevel(a.grade) IS NOT NULL) AS level
        FROM   wanted w
        JOIN   allraces a
               ON a.meet_id = w.meet_id AND a.div_id = w.div_id
              AND a.source = w.source AND a.event_key = w.event_key
        GROUP  BY 1, 2, 3, 4
        HAVING count(*) FILTER (WHERE gradeLevel(a.grade) IS NOT NULL)
                   >= {GS.MIN_FIELD_GRADED}
    )
    SELECT m.person_id, m.acad
    FROM   mine m
    JOIN   event_level e
           ON e.meet_id = m.meet_id AND e.div_id = m.div_id
          AND e.source = m.source AND e.event_key = m.event_key
    GROUP  BY 1, 2
    HAVING bool_and(e.level = 'hs')
"""


def old_team_level_candidates(acad, seasons_of):
    """grade_sanity.teamLevelCandidates before 2026-10-25."""
    out = []
    for key, v in acad.items():
        g = str(v.get("grade") or "").strip()
        if (seasons_of.get(key[0]) == 1 and v.get("method") != "pinned"
                and g.isdigit() and 1 <= int(g) <= 8):
            out.append(key)
    return out


def old_write_rows(resolved):
    """What write() handed execute_values before 2026-10-25: grade_fix's
    rows in dict order, grade_untrusted's in sorted order."""
    fix = [(p, s, v["grade"], v["level"], v["method"], v.get("trust", "high"))
           for (p, s), v in resolved.items()]
    return fix, sorted(resolved.keys())


# ------------------------------------------------------------------ #
#  COPY's text format, read back
# ------------------------------------------------------------------ #

_UNESC = {"\\": "\\", "t": "\t", "n": "\n", "r": "\r"}


def _readCopy(data):
    """COPY text format -> rows of str/None (the four escapes and \\N, which
    is everything copyText writes)."""
    rows = []
    for line in data.split("\n")[:-1]:
        row = []
        for field in line.split("\t"):
            if field == "\\N":
                row.append(None)
                continue
            out, i = [], 0
            while i < len(field):
                c = field[i]
                if c == "\\":
                    out.append(_UNESC[field[i + 1]])
                    i += 2
                else:
                    out.append(c)
                    i += 1
            row.append("".join(out))
        rows.append(tuple(row))
    return rows


def _asText(rows):
    """The rows as the server stores them in text: ints as their digits."""
    return [tuple(None if v is None else str(v) for v in r) for r in rows]


NASTY = ["9", "SR", "", " 12 ", "a\tb", "back\\slash", "line\nbreak", "cr\rhere",
         "\\N", "\\.", "tab\\t", "Ünïcödé", "trailing\\"]


def test_copy_text_round_trips_every_character_copy_escapes():
    rnd = random.Random(7)
    rows = [(rnd.randrange(10**12), rnd.randrange(1990, 2036), rnd.choice(NASTY + [None]),
             rnd.choice([None, "hs", "college"]), rnd.choice(NASTY), "high")
            for _ in range(5000)]
    data = "".join(GS.copyLines(rows))
    assert _readCopy(data) == _asText(rows)


@pytest.mark.parametrize("chunk_rows,read_size", [(1, 1), (3, 7), (1000, 8192), (7, -1)])
def test_the_copy_source_streams_the_same_bytes_in_any_chunking(chunk_rows, read_size):
    rnd = random.Random(chunk_rows)
    rows = [(i, 2024, rnd.choice(NASTY + [None]), None, "field", "low") for i in range(400)]
    src = GS._CopySource(iter(rows), chunk_rows=chunk_rows)
    got = b""
    while True:
        b = src.read(read_size)
        if not b:
            break
        got += b
    assert got == "".join(GS.copyLines(rows)).encode("utf-8")
    assert _readCopy(got.decode("utf-8")) == _asText(rows)


# ------------------------------------------------------------------ #
#  write(): a stub cursor that records what reaches the server
# ------------------------------------------------------------------ #

class _Conn:
    encoding = "UTF8"


class _RecCursor:
    def __init__(self):
        self.sql, self.copies = [], {}
        self.connection = _Conn()

    def execute(self, sql, params=None):
        self.sql.append(" ".join(sql.split()))

    def copy_expert(self, sql, f, size=8192):
        buf = io.BytesIO()
        while True:
            b = f.read(size)
            if not b:
                break
            buf.write(b)
        self.copies[" ".join(sql.split())] = buf.getvalue().decode("utf-8")
        self.sql.append(" ".join(sql.split()))

    def fetchall(self):
        return []


def _resolved(seed, n=3000):
    rnd = random.Random(seed)
    out = {}
    for _ in range(n):
        key = (rnd.randrange(1, 10**9), rnd.randrange(2000, 2027))
        v = {"grade": rnd.choice(["9", "12", "SR", None, "7-8"] + NASTY),
             "level": rnd.choice([None, "hs", "ms", "college", "pro"]),
             "method": rnd.choice(["corroborated", "field", "no_evidence", "pinned",
                                   "a\tmethod"])}
        if rnd.random() < 0.8:
            v["trust"] = rnd.choice(["high", "low"])
        out[key] = v
    return out


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_write_sends_the_rows_the_inserts_sent(seed):
    resolved = _resolved(seed)
    cur = _RecCursor()
    assert GS.write(cur, resolved) == len(resolved)
    want_fix, want_untrusted = old_write_rows(resolved)
    (copy_sql, data), = cur.copies.items()
    assert copy_sql == ("COPY grade_fix (person_id, season, grade, level, method, trust) "
                        "FROM STDIN")
    # same rows, same order (the table's physical order is the dict's, as before)
    assert _readCopy(data) == _asText(want_fix)
    # grade_untrusted: every key, sorted -- now copied server-side from grade_fix
    assert {(p, s) for p, s, *_ in want_fix} == set(want_untrusted)
    ins = [s for s in cur.sql if s.startswith("INSERT INTO grade_untrusted")]
    assert ins == ["INSERT INTO grade_untrusted (person_id, season) SELECT person_id, "
                   "season FROM grade_fix ORDER BY person_id, season"]
    # both keys, and each one after its rows
    order = cur.sql
    assert order.index("ALTER TABLE grade_fix ADD PRIMARY KEY (person_id, season)") \
        > order.index(copy_sql)
    assert order.index("ALTER TABLE grade_untrusted ADD PRIMARY KEY (person_id, season)") \
        > order.index(ins[0])


# ------------------------------------------------------------------ #
#  rule 5f in Python
# ------------------------------------------------------------------ #

@pytest.mark.parametrize("seed", range(6))
def test_team_level_candidates_are_the_old_ones_in_the_old_order(seed):
    rnd = random.Random(seed)
    acad, seasons_of = {}, {}
    for _ in range(4000):
        pid, ay = rnd.randrange(1, 900), rnd.randrange(2015, 2027)
        acad[(pid, ay)] = {
            "grade": rnd.choice(["8", " 7 ", "1", "0", "9", "12", "SR", None, "", "08",
                                 "7-8", "x", 6]),
            "level": None,
            "method": rnd.choice(["corroborated", "pinned", "field"])}
        seasons_of[pid] = seasons_of.get(pid, 0) + 1
    if seed % 2:
        seasons_of = {p: (1 if rnd.random() < 0.6 else n) for p, n in seasons_of.items()}
    want = old_team_level_candidates(acad, seasons_of)
    assert GS.teamLevelCandidates(acad, seasons_of) == want


def test_team_level_seasons_takes_the_codes_from_a_future():
    acad = {(1, 2026): {"grade": "8", "level": None, "method": "corroborated"}}
    fut = Future()
    fut.set_result([])                 # no high school codes: the rule is inert
    cur = _RecCursor()
    assert GS.teamLevelSeasons(cur, acad, {1: 1}, hs_codes=fut) == set()
    assert cur.sql == []
    fut = Future()
    fut.set_result([4])
    cur = _RecCursor()
    assert GS.teamLevelSeasons(cur, acad, {1: 1}, hs_codes=fut) == set()
    joined = "\n".join(cur.sql)
    assert "CREATE UNLOGGED TABLE tl_cand" in joined and "TEMP" not in joined
    assert _readCopy(cur.copies["COPY tl_cand (person_id, acad) FROM STDIN"]) == [("1", "2026")]


# ------------------------------------------------------------------ #
#  the SQL text
# ------------------------------------------------------------------ #

def test_every_build_function_is_parallel_safe():
    stmts = [s for s in GS.splitStatements(GS._BUILD) if "CREATE OR REPLACE FUNCTION" in s]
    assert len(stmts) == 9
    for s in stmts:
        assert s.rstrip().endswith("LANGUAGE sql IMMUTABLE PARALLEL SAFE"), s.splitlines()[0]


def test_the_staged_statements_take_no_parameters():
    # runTimed executes them without params: a %(name)s would reach the server
    for sql in (GS.fieldBuild(), GS._FIELD_SQL, GS._FIELD_DROP):
        assert "%(" not in sql and GS._OPEN_TOKEN not in sql
    heads = [s.split("\n")[-1] if s.startswith("--") else s
             for s in GS.splitStatements(GS.fieldBuild())]
    assert sum("CREATE UNLOGGED TABLE" in s for s in heads) == 5


# ------------------------------------------------------------------ #
#  THE SQL HALF -- old statement and new stages side by side
# ------------------------------------------------------------------ #

@pytest.fixture
def cur():
    dsn = os.environ.get("XCP_TWIN_TEST_DSN")
    if not dsn:
        pytest.skip("set XCP_TWIN_TEST_DSN to a scratch Postgres to run the SQL")
    import psycopg2
    admin = psycopg2.connect(dsn)
    admin.autocommit = True
    with admin.cursor() as c:
        c.execute(f"DROP DATABASE IF EXISTS {DB}")
        c.execute(f"CREATE DATABASE {DB}")
    test_dsn = " ".join(p for p in dsn.split() if not p.startswith("dbname=")) + f" dbname={DB}"
    cx = psycopg2.connect(test_dsn)
    c = cx.cursor()
    # the build's functions, and nothing else of it
    for s in GS.splitStatements(GS._BUILD):
        if "FUNCTION" in s.split("$$")[0]:
            c.execute(s)
    # the build's own parallel settings, so the new stages run as in production
    for s in ("SET max_parallel_workers_per_gather = 8", "SET parallel_setup_cost = 0",
              "SET parallel_tuple_cost = 0", "SET min_parallel_table_scan_size = 0"):
        c.execute(s)
    c.execute("""CREATE UNLOGGED TABLE allraces (
                     person_id bigint, acad int, grade text, grade_folded text,
                     meet_id bigint, div_id bigint, source text, event_key bigint,
                     race bigint)""")
    yield c
    cx.close()
    with admin.cursor() as c2:
        c2.execute(f"DROP DATABASE IF EXISTS {DB}")
    admin.close()


# grades, including spellings normGrade never emits: a grade with no level
# is where count(a.grade) and count(level) part company
_GRADES = (["9", "10", "11", "12", "6", "7", "8", "3", "FR", "SO", "JR", "SR", "RS",
            "7-8", "9-10", "11-12"] * 3 + [None] * 12 + ["XYZ", "-", "2026"])


def _fixture(rnd, n_people=160, n_events=70):
    rows = []
    events = [(m, rnd.choice([1, 2, None]) if rnd.random() < 0.05 else rnd.choice([1, 2]),
               rnd.choice(["anet", "tfrrs"]), rnd.choice([-1, 7]))
              for m in range(1, n_events + 1)]
    for ev in events:
        kind = rnd.random()
        for _ in range(rnd.randint(1, 14)):
            pid, ay = rnd.randrange(1, n_people), rnd.choice([2023, 2024])
            if kind < 0.15:
                g = None                           # an event nobody graded
            elif kind < 0.35:
                g = rnd.choice(["9", "8"])         # two levels, often tied
            else:
                g = rnd.choice(_GRADES)
            gf = g if rnd.random() < 0.85 else rnd.choice([None, "12", "SR"])
            race = rnd.choice([None, rnd.randrange(1, 40), rnd.randrange(1, 40)])
            rows.append((pid, ay, g, gf) + ev + (race,))
            if rnd.random() < 0.15:                # the same race scraped twice
                rows.append(rows[-1])
    return rows


def _load(cur, rows):
    from psycopg2.extras import execute_values
    cur.execute("TRUNCATE allraces")
    execute_values(cur, "INSERT INTO allraces VALUES %s", rows)
    cur.execute("ANALYZE allraces")


def _newField(cur, share=None):
    GS.runTimed(cur, GS.fieldBuild(share), label="test", slow=1e9)
    cur.execute(GS._FIELD_SQL)
    got = cur.fetchall()
    cur.execute("SELECT person_id, acad, races FROM season_races")
    races = cur.fetchall()
    cur.execute(GS._FIELD_DROP)
    return got, races


@pytest.mark.parametrize("seed", range(8))
def test_rule4_stages_match_the_one_statement(cur, seed):
    _load(cur, _fixture(random.Random(seed)))
    cur.execute(oldField(), {"minc": GS.MIN_CORROBORATION})
    want = cur.fetchall()
    cur.execute(OLD_RACE_COUNT_SQL)
    want_races = cur.fetchall()
    got, got_races = _newField(cur)
    # DISTINCT ON: one row per season, so the row ORDER is the only freedom
    assert sorted(got) == sorted(want)
    assert {r[:2]: r[2] for r in got} == {r[:2]: r[2] for r in want}
    assert sorted(got_races) == sorted(want_races)
    assert want, "the seed should give rule 4 something to say"
    assert any(level == "pro" for _p, _a, level in want)


def test_rule4_ties_and_ungraded_events(cur):
    rows = []
    # event 1: 2 hs, 2 ms entrants -> a tie, to the higher level: 'hs'
    # (and an elem/hs tie goes to hs too, where the text order said elem)
    for pid, g in ((1, "9"), (2, "10"), (3, "7"), (4, "8")):
        rows.append((pid, 2024, g, g, 1, 1, "anet", -1, 100 + pid))
    # event 2: 3 college, 3 elem -> 'college'
    for pid, g in ((5, "SR"), (6, "JR"), (7, "FR"), (8, "3"), (9, "4"), (10, "5")):
        rows.append((pid, 2024, g, g, 2, 1, "anet", -1, 200 + pid))
    # event 5: 2 elem, 2 hs -> a tie, to 'hs' (the text order said 'elem')
    for pid, g in ((13, "4"), (14, "5"), (15, "9"), (16, "11")):
        rows.append((pid, 2024, g, g, 5, 1, "anet", -1, 500 + pid))
    # event 3: nobody graded -> 'pro'; event 4: graded but no level -> nothing
    # (an ungraded field needs MIN_FIELD_GRADED entrants to say 'pro', P5;
    #  event 6 is one ungraded runner alone, which says nothing)
    rows += [(pid, 2024, None, None, 3, 1, "anet", -1, 300 + pid) for pid in (17, 18, 19)]
    rows += [(20, 2024, None, None, 6, 1, "anet", -1, 600)]
    rows += [(11, 2024, None, None, 3, 1, "anet", -1, 300),
             (12, 2024, "XYZ", "XYZ", 4, 1, "anet", -1, 400),
             (12, 2024, None, None, 4, 1, "anet", -1, 401)]
    # the athletes 1 and 5 are needy (one race each); 11 and 12 too
    _load(cur, rows)
    cur.execute(oldField(), {"minc": GS.MIN_CORROBORATION})
    want = sorted(cur.fetchall())
    got, _races = _newField(cur)
    assert sorted(got) == want
    levels = {p: lv for p, _a, lv in want}
    assert levels[1] == "hs" and levels[3] == "hs"
    assert levels[5] == "college" and levels[8] == "college"
    assert levels[11] == "pro" and 12 not in levels and 20 not in levels
    assert levels[13] == "hs" and levels[15] == "hs"


@pytest.mark.parametrize("seed", range(5))
def test_rule5f_two_statements_match_the_one(cur, seed):
    from psycopg2.extras import execute_values
    rnd = random.Random(100 + seed)
    rows = _fixture(rnd, n_people=60, n_events=40)
    _load(cur, rows)
    cols = ("result_id bigint, person_id bigint, date text, team_id bigint")
    cur.execute(f"CREATE TABLE results ({cols}); CREATE TABLE results_tf ({cols})")
    cur.execute("CREATE TABLE anet_team (team_id bigint, level int)")
    execute_values(cur, "INSERT INTO anet_team VALUES %s", [(t, rnd.choice([3, 4, 4, 8])) for t in range(20)])
    rid = 0
    for pid, ay, *_ in rows:
        rid += 1
        table = "results" if rnd.random() < 0.5 else "results_tf"
        team = rnd.choice([None, 999] + list(range(20)) * 3)
        date = f"{ay if rnd.random() < 0.5 else ay + 1}-{rnd.choice(['03', '09'])}-01"
        cur.execute(f"INSERT INTO {table} VALUES (%s, %s, %s, %s)", (rid, pid, date, team))
    cand = sorted({(pid, ay) for pid, ay, *_ in rows if rnd.random() < 0.7})
    cand = list({pid: (pid, ay) for pid, ay in cand}.values())     # one season each
    codes = {"codes": [4]}
    cur.execute("CREATE TEMP TABLE tl_cand (person_id bigint, acad int, PRIMARY KEY (person_id, acad))")
    execute_values(cur, "INSERT INTO tl_cand VALUES %s", cand)
    cur.execute(OLD_TEAM_LEVEL_SQL, codes)
    want = set(cur.fetchall())
    cur.execute("DROP TABLE tl_cand")
    acad = {k: {"grade": "8", "level": None, "method": "corroborated"} for k in cand}
    got = GS.teamLevelSeasons(cur, acad, {p: 1 for p, _a in cand}, hs_codes=[4])
    assert got == want
    assert want and len(want) < len(cand), "the seed should move some seasons, not all"
    cur.execute("SELECT to_regclass('tl_cand'), to_regclass('tl_mine')")
    assert cur.fetchone() == (None, None)


def _snapshot(cur, table):
    cur.execute(f"SELECT md5(string_agg(t::text, '|' ORDER BY person_id, season)), count(*) "
                f"FROM {table} t")
    rows = cur.fetchone()
    cur.execute("""
        SELECT a.attname, format_type(a.atttypid, a.atttypmod), a.attnotnull,
               pg_get_expr(d.adbin, d.adrelid)
        FROM   pg_attribute a
        LEFT   JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
        WHERE  a.attrelid = %s::regclass AND a.attnum > 0 AND NOT a.attisdropped
        ORDER  BY a.attnum""", (table,))
    cols = cur.fetchall()
    cur.execute("""SELECT conname, contype, pg_get_constraintdef(oid) FROM pg_constraint
                   WHERE conrelid = %s::regclass ORDER BY conname""", (table,))
    cons = cur.fetchall()
    cur.execute("SELECT indexname, indexdef FROM pg_indexes WHERE tablename = %s", (table,))
    idx = sorted(cur.fetchall())
    return rows, cols, cons, idx


def test_write_makes_the_same_tables(cur):
    from psycopg2.extras import execute_values
    resolved = _resolved(11, n=4000)
    # the old write, verbatim
    fix, untrusted = old_write_rows(resolved)
    cur.execute("DROP TABLE IF EXISTS grade_fix")
    cur.execute("""CREATE TABLE grade_fix (
                       person_id bigint NOT NULL,
                       season    int    NOT NULL,
                       grade     text,
                       level     text,
                       method    text   NOT NULL,
                       trust     text   NOT NULL DEFAULT 'high',
                       PRIMARY KEY (person_id, season))""")
    execute_values(cur, "INSERT INTO grade_fix VALUES %s", fix, page_size=5000)
    cur.execute("DROP TABLE IF EXISTS grade_untrusted")
    cur.execute("""CREATE TABLE grade_untrusted (
                       person_id bigint NOT NULL,
                       season    int    NOT NULL,
                       PRIMARY KEY (person_id, season))""")
    execute_values(cur, "INSERT INTO grade_untrusted VALUES %s", untrusted, page_size=5000)
    want = (_snapshot(cur, "grade_fix"), _snapshot(cur, "grade_untrusted"))
    cur.execute("SELECT person_id, season FROM grade_untrusted")
    want_phys = cur.fetchall()
    GS.write(cur, resolved)
    got = (_snapshot(cur, "grade_fix"), _snapshot(cur, "grade_untrusted"))
    assert got == want
    # and grade_untrusted is laid down sorted, as the sorted INSERT laid it
    cur.execute("SELECT person_id, season FROM grade_untrusted")
    assert cur.fetchall() == want_phys


def test_an_open_races_graded_few_do_not_pool_its_ungraded_runners(cur):
    """Ethan Weiner's races (owner, 2026-10-27): 77 of 83 runners ungraded,
    a few high schoolers graded. The ungraded runner gets no 'hs' from them;
    a graded junior in the same race keeps 'hs'; where most ARE graded, an
    ungraded runner still takes the field's level, as before."""
    rows = []
    # event 7: open -- 4 graded high schoolers, 10 ungraded adults
    for pid, g in ((31, "11"), (32, "10"), (33, "12"), (34, "9")):
        rows.append((pid, 2026, g, g, 7, 1, "anet", -1, 700 + pid))
    for pid in range(40, 50):
        rows.append((pid, 2026, None, None, 7, 1, "anet", -1, 700 + pid))
    # event 8: a school race -- 4 graded, 2 ungraded
    for pid, g in ((51, "11"), (52, "10"), (53, "12"), (54, "9")):
        rows.append((pid, 2026, g, g, 8, 1, "anet", -1, 800 + pid))
    for pid in (55, 56):
        rows.append((pid, 2026, None, None, 8, 1, "anet", -1, 800 + pid))
    _load(cur, rows)
    # the rule is off by default (unmeasured, see GS.OPEN_FIELD_OFF): the
    # adults still take 'hs'
    got_off, _r = _newField(cur)
    assert {p: lv for p, _a, lv in got_off}[40] == "hs"
    cur.execute(oldField(0.5), {"minc": GS.MIN_CORROBORATION})
    want = sorted(cur.fetchall())
    got, _races = _newField(cur, 0.5)
    assert sorted(got) == want
    levels = {p: lv for p, _a, lv in want}
    assert 40 not in levels and 49 not in levels          # no 'hs' for the adults
    assert levels[31] == "hs"                              # the graded junior keeps it
    assert levels[55] == "hs" and levels[56] == "hs"       # a school race still names them



def test_compare_counts_each_kind_of_change():
    old = {(1, 2026): ("hs", "field"), (2, 2026): ("hs", "field"), (3, 2026): ("ms", "majority")}
    new = {(2, 2026): ("pro", "no_grades"), (3, 2026): ("ms", "majority"), (4, 2026): ("hs", "field")}
    got = GS.compareVerdicts(old, new)
    assert got == {"hs/field -> -": 1, "hs/field -> pro/no_grades": 1, "- -> hs/field": 1}
