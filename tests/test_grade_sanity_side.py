# Project: xc-predictor / tests
# File:    test_grade_sanity_side.py
# Purpose: grade_sanity's side-by-side path (SideWork, 2026-11-16) writes
#          the tables the one-connection path writes: the same rows, the
#          same values, the same allraces indexes in the same OID order.
#
# ★ WHY (run 20261113_150959: 04_grade_sanity 2,500 s, the critical path of
#   block 04, every phase on one connection). The independent reads -- rule
#   7, the reports, [2c]'s two reads, [5] / [5b] / [5e], rule 8's
#   athlete_season and club rows, the three allraces indexes -- now run on
#   their own connections and are applied where the old path ran them. This
#   runs `grade_sanity.py --write` three ways on one synthetic corpus that
#   fires every rule (corroborated, pins, 2c both arms, 4 with open and
#   college votes, 5, 7, 3b, 5b, 5e, 8 both arms, 5c, 5d, 5f, 6, trust):
#   XCP_GRADE_SANITY_PARALLEL=0, the default, and the default with every
#   side job failing (statement_timeout 1ms: each runs again on the main
#   connection) -- and compares every table the run leaves, EXCEPT ALL both
#   ways.
#
# The SQL half needs a scratch Postgres, like test_thin_field_sql.py:
#   XCP_TWIN_TEST_DSN="host=127.0.0.1 user=postgres password=pw dbname=postgres"
import os
import random
import subprocess
import sys

import pytest

import _env  # noqa: F401  -- sets XCP_DB_PASSWORD, must precede config

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in ("engine", "scripts"):
    sys.path.insert(0, os.path.join(ROOT, p))

import grade_sanity as GS                                        # noqa: E402

DB = "xcp_grade_side_test"
CORPUS = ["results", "results_tf", "anet_team", "race_top_level", "athletes",
          "college_directory", "meets", "meets_tfrrs", "dist_override",
          "athlete_season", "grade_pin", "age_band_result", "result_twin"]


# ------------------------------------------------------------------ #
#  THE PURE HALF
# ------------------------------------------------------------------ #

def test_the_build_splits_at_its_three_indexes():
    # buildRaces splits only when the build ends in allraces' three indexes
    # and its ANALYZE; an edit that breaks that shape runs on one connection
    stmts = GS.splitStatements(GS._BUILD)
    idx = [k for k, s in enumerate(stmts) if GS._ALLRACES_INDEX_RE.match(s)]
    assert len(idx) == 3 and idx == list(range(idx[0], idx[0] + 3))
    assert stmts[idx[-1] + 1:] == ["ANALYZE allraces"]


def test_the_switch(monkeypatch):
    monkeypatch.delenv("XCP_PG_SNAPSHOT_FILE", raising=False)
    monkeypatch.delenv(GS.SIDE_ENV, raising=False)
    assert GS.sideOn()
    monkeypatch.setenv(GS.SIDE_ENV, "0")
    assert not GS.sideOn()
    monkeypatch.setenv(GS.SIDE_ENV, "1")
    monkeypatch.setenv("XCP_PG_SNAPSHOT_FILE", "/tmp/x")
    assert not GS.sideOn()          # a commit cannot see past a snapshot


def test_the_pins_still_bracket_the_rules():
    # the side reads are started from helpers, so [0] still sits between
    # [2] and [2c] in _resolve's own text (test_upload_link_pins)
    src = open(os.path.join(ROOT, "engine", "grade_sanity.py")).read()
    body = src[src.index("def resolve("):src.index("def write(")]
    first = body.index("_PP.applyGradePins(acad, grade_pins)")
    assert body.index("_CORROBORATED_SQL") < first < body.index("_BARE_CLASS_SQL")


def test_index_columns():
    assert GS._indexColumns("CREATE INDEX ON allraces (person_id, acad, race)") == \
        "person_id,acad,race"
    assert GS._indexColumns("CREATE INDEX allraces_x ON public.allraces USING btree "
                            "(meet_id, div_id, source, event_key)") == \
        "meet_id,div_id,source,event_key"


# ------------------------------------------------------------------ #
#  THE SQL HALF -- a synthetic corpus, three runs, every table compared
# ------------------------------------------------------------------ #

def raceKey(meet, div, src):
    import hashlib
    h = hashlib.md5(f"{meet}:{div}:{src}".encode()).hexdigest()[:15]
    return int(h, 16)          # 60 bits, as ('x'||substr(md5,1,15))::bit(60)::bigint


def corpus(rnd, NP):
    results, results_tf = [], []
    meets = {}                 # meet -> (level, sport, src, dist, date-year-month)
    mid = [0]

    def meet(level, sport, year):
        mid[0] += 1
        m = mid[0]
        src = rnd.choice(["anet", "anet", "tfrrs"])
        month = rnd.choice(["09", "10", "11"]) if sport == "XC" else rnd.choice(["03", "04", "05"])
        meets[m] = (level, sport, src, rnd.choice([5000.0, 8000.0, 6000.0, 3000.0]), year, month)
        return m

    by_level = {}
    for year in range(2015, 2027):
        for level, n in (("hs", 14), ("ms", 6), ("college", 8), ("pro", 3), ("open", 4)):
            for sport in ("XC", "TF"):
                for _ in range(n):
                    by_level.setdefault((level, year, sport), []).append(meet(level, sport, year))
    rid = [0]
    teams = {"hs": (1, 1), "ms": (2, 3), "college": (3, 2), "club": (4, 4)}

    def row(pid, level, year, grade, school, team, t, sport=None, mlevel=None, m=None, dk=None):
        sport = sport or rnd.choice(["XC", "TF"])
        ml = mlevel or level
        if m is None:
            ms = by_level.get((ml, year, sport))
            if not ms:
                return
            m = rnd.choice(ms)
        lv, sp, src, dist, y, month = meets[m]
        rid[0] += 1
        div = m * 10 + (dk or rnd.choice([1, 2]))
        day = rnd.randint(1, 28)
        date = f"{y}-{month}-{day:02d}"
        tm = round(t * (dist / 5000.0) ** 1.06, 1) if t else None
        if sp == "XC":
            results.append((rid[0], pid, pid, date, grade, m, div, src, tm, school, team))
        else:
            ev = rnd.choice([1, 2, 3])
            short = {1: "5000m", 2: "3000m", 3: "Mile"}[ev]
            d = {1: 5000.0, 2: 3000.0, 3: 1609.34}[ev]
            tm = round(t * (d / 5000.0) ** 1.06, 1) if t else None
            results_tf.append((rid[0], pid, pid, date, grade, m, div, src, tm, school, team,
                               ev if rnd.random() > 0.05 else None,
                               1 if rnd.random() < 0.02 else 0, short))

    athletes, season_pool, pins = [], [], []
    clubs = ["royal city athletics club", "Royal City AC", "RCAC", "Zap Endurance",
             "Bay Area Track Club", "Youth Harriers", "Mexico", "Kenya", "Puerto Rico",
             "Unattached", "Brooks Beasts"]
    hs_names = [f"Lake {k} High School" for k in range(40)]
    col_names = [f"State {k} University" for k in range(15)]
    ms_names = [f"Hill {k} Middle School" for k in range(20)]
    for pid in range(1, NP + 1):
        g = rnd.choice("MF")
        athletes.append((pid, pid, g, f"F{pid}", f"L{pid}", None))
        kind = rnd.random()
        base = 1000 if g == "M" else 1120
        start = rnd.randint(2014, 2024)
        if kind < 0.40:            # high school, sometimes messy
            sch = rnd.choice(hs_names)
            for k in range(rnd.randint(1, 4)):
                ay = start + k
                gr = str(9 + k)
                if rnd.random() < 0.05:
                    gr = str(rnd.randint(6, 12))          # an outlier
                if rnd.random() < 0.08:
                    gr = rnd.choice(["Jr", "Sr", "So", "Fr", "Junior"])   # bare word
                if rnd.random() < 0.04:
                    gr = "12"                              # stale
                for _ in range(rnd.choice([1, 1, 2, 3, 4])):
                    w = gr if rnd.random() > 0.1 else rnd.choice([None, "-", gr + "th", "11-12"])
                    row(pid, "hs", ay, w, sch if rnd.random() > 0.1 else rnd.choice(clubs),
                        teams["hs"][0] if rnd.random() > 0.2 else None,
                        base + rnd.uniform(-80, 200),
                        mlevel=rnd.choice(["hs"] * 8 + ["open", "college"]))
            if rnd.random() < 0.3:                         # then college
                col = rnd.choice(col_names)
                for k, cl in enumerate(["FR-1", "SO-2", "JR-3", "SR-4"][:rnd.randint(1, 4)]):
                    ay = start + 4 + k
                    for _ in range(rnd.randint(1, 3)):
                        row(pid, "college", ay, rnd.choice([cl, cl[:2], cl[:2].title()]), col,
                            teams["college"][0], base - 60 + rnd.uniform(-50, 100))
                if rnd.random() < 0.4:                     # then a club "12"
                    for k in range(rnd.randint(1, 3)):
                        ay = start + 9 + k
                        if ay > 2026:
                            break
                        row(pid, "pro", ay, rnd.choice(["12", "12", None, "7"]), rnd.choice(clubs[:6]),
                            teams["club"][0], base - 40 + rnd.uniform(-30, 80),
                            mlevel=rnd.choice(["pro", "college", "open"]))
                    season_pool.append((pid, min(2026, start + 9), "pro_" + g.lower()))
        elif kind < 0.55:          # middle school, some elite
            sch = rnd.choice(ms_names)
            for k in range(rnd.randint(1, 3)):
                ay = start + k
                gr = str(6 + k)
                for _ in range(rnd.randint(1, 3)):
                    row(pid, "ms", ay, gr, sch, teams["ms"][0], base + 150 + rnd.uniform(0, 200))
                if rnd.random() < 0.08:
                    row(pid, "ms", ay, gr, rnd.choice(clubs[:6]), None,
                        base - 200 + rnd.uniform(-50, 20), mlevel=rnd.choice(["college", "pro"]))
        elif kind < 0.70:          # college with definite classes, word-heavy, mixed
            col = rnd.choice(col_names)
            for k in range(rnd.randint(1, 6)):
                ay = start + k
                cl = ["FR-1", "SO-2", "JR-3", "SR-4", "SR-4", "Sr"][k]
                for _ in range(rnd.randint(1, 3)):
                    w = rnd.choice([cl, cl[:2], "Senior", None]) if rnd.random() < 0.3 else cl
                    if rnd.random() < 0.05:
                        w = str(rnd.randint(9, 12))         # mixed season
                    row(pid, "college", ay, w, col, teams["college"][0],
                        base - 50 + rnd.uniform(-60, 100))
            if rnd.random() < 0.3:
                season_pool.append((pid, start + 1, "college_" + g.lower()))
        elif kind < 0.85:          # adults, ungraded, open and pro races
            for k in range(rnd.randint(1, 4)):
                ay = start + k
                if ay > 2026:
                    break
                for _ in range(rnd.randint(1, 3)):
                    row(pid, "open", ay, rnd.choice([None, None, "-", "12", "10"]),
                        rnd.choice(clubs), teams["club"][0] if rnd.random() < 0.5 else None,
                        base - 20 + rnd.uniform(-40, 300),
                        mlevel=rnd.choice(["open", "pro", "college", "hs"]))
            if rnd.random() < 0.5:
                season_pool.append((pid, start, "pro_" + g.lower()))
        elif kind < 0.88:          # rule 5f: an eighth grader's lone season on a HS team
            ay = rnd.randint(2015, 2026)
            for _ in range(2):
                row(pid, "hs", ay, "8", rnd.choice(hs_names), teams["hs"][0],
                    base + rnd.uniform(0, 200), sport="XC")
        else:                      # one race, lone words, national teams
            ay = rnd.randint(2015, 2026)
            row(pid, rnd.choice(["hs", "college", "open"]), ay,
                rnd.choice(["Sr", "Jr", "So", None, "8", "11", "-"]),
                rnd.choice(hs_names[:3] + clubs + col_names[:2]),
                rnd.choice([None, teams["hs"][0], teams["college"][0]]),
                base + rnd.uniform(-100, 300))
        if rnd.random() < 0.01:
            pins.append((pid, start, "10", None))
    # rule 5c / 5d: cohorts of word-heavy mixed seasons sharing one event, and
    # the field verdicts of the few graded others in it
    pid = NP
    for _ in range(40):
        year = rnd.randint(2015, 2026)
        m = rnd.choice(by_level[("hs", year, "XC")])
        for k in range(rnd.randint(5, 8)):
            pid += 1
            athletes.append((pid, pid, "M", f"F{pid}", f"L{pid}", None))
            row(pid, "hs", year, "Jr", "Lake 1 High School", None, 1100, m=m, dk=1)
            row(pid, "hs", year, "Jr", "Lake 1 High School", None, 1110, sport="XC")
            row(pid, "hs", year, "Junior", "Lake 1 High School", None, 1120, sport="XC")
            row(pid, "hs", year, "11", "Lake 1 High School", None, 1130, sport="XC")
        for k in range(2):
            pid += 1
            athletes.append((pid, pid, "F", f"F{pid}", f"L{pid}", None))
            row(pid, "hs", year, "10", "Lake 2 High School", None, 1200, m=m, dk=1)
    anet_team = [(1, 1, "lake high", "Lake High School"), (2, 3, "hill ms", "Hill Middle School"),
                 (3, 2, "state u", "State University"), (4, 4, "royal city athletics club",
                                                          "Royal City Athletics Club")]
    rtl, meets_rows, meets_tfrrs = [], [], []
    seen = set()
    for r in results + results_tf:
        key = (r[5], r[6], r[7])
        if key in seen:
            continue
        seen.add(key)
        lv = meets[r[5]][0]
        top = {"open": rnd.choice(["pro", "college", "hs"])}.get(lv, lv)
        rtl.append((raceKey(*key), top))
        meets_rows.append((r[6], r[7], meets[r[5]][3]))
    rtl = list({k: (k, t) for k, t in rtl}.values())
    meets_rows = list({(d, s): (d, s, x) for d, s, x in meets_rows}.values())
    college_dir = [(n,) for n in col_names[:10]]
    # the age-band table names banded rows only ("11-12" in an age division)
    age_band = [(r[0], "XC") for r in results if r[4] == "11-12"][:30] + \
               [(r[0], "TF") for r in results_tf if r[4] == "11-12"][:30]
    twins = [("XC", r[0], "twin") for r in rnd.sample(results, 25)] + \
            [("TF", r[0], "twin") for r in rnd.sample(results_tf, 25)]
    season_pool = list({(p, y): (p, y, pl) for p, y, pl in season_pool}.values())
    pins = list({(p, y): (p, y, g, lv) for p, y, g, lv in pins}.values())
    return dict(results=results, results_tf=results_tf, athletes=athletes,
                anet_team=anet_team, race_top_level=rtl, meets=meets_rows,
                college_directory=college_dir, athlete_season=season_pool,
                grade_pin=pins, age_band_result=age_band, result_twin=twins)


def _dsn():
    dsn = os.environ.get("XCP_TWIN_TEST_DSN")
    if not dsn:
        pytest.skip("set XCP_TWIN_TEST_DSN to a scratch Postgres to run grade_sanity")
    return dsn


def _kv(dsn):
    return dict(p.split("=", 1) for p in dsn.split())


def _load(dsn, seed, n_people):
    import psycopg2
    from psycopg2.extras import execute_values
    admin = psycopg2.connect(dsn)
    admin.autocommit = True
    with admin.cursor() as c:
        c.execute(f"DROP DATABASE IF EXISTS {DB}")
        c.execute(f"CREATE DATABASE {DB}")
    admin.close()
    test_dsn = " ".join(p for p in dsn.split() if not p.startswith("dbname=")) + f" dbname={DB}"
    cx = psycopg2.connect(test_dsn)
    c = cx.cursor()
    cols = ("result_id bigint, person_id bigint, athlete_id bigint, date text, grade text, "
            "meet_id bigint, div_id bigint, source text, time_seconds float8, school text, "
            "team_id bigint")
    for ddl in (f"CREATE TABLE results ({cols})",
                f"CREATE TABLE results_tf ({cols}, event_id bigint, is_relay int, event_short text)",
                "CREATE TABLE anet_team (team_id bigint, level int, school text, name text)",
                "CREATE TABLE race_top_level (race bigint PRIMARY KEY, top_level text)",
                "CREATE TABLE athletes (athlete_id bigint, person_id bigint, gender text, "
                "first_name text, last_name text, school text)",
                "CREATE TABLE college_directory (name text)",
                "CREATE TABLE meets (div_id bigint, source text, distance real)",
                "CREATE TABLE meets_tfrrs (meet_id bigint, sport text, "
                "division_distances jsonb, distance real)",
                "CREATE TABLE dist_override (meet_id bigint, div_id bigint, distance real)",
                "CREATE TABLE athlete_season (person_id bigint, year int, pool text)",
                "CREATE TABLE grade_pin (person_id bigint, season int, grade text, level text)",
                "CREATE TABLE age_band_result (result_id bigint, sport text)",
                "CREATE TABLE result_twin (sport text NOT NULL, result_id bigint NOT NULL, "
                "reason text NOT NULL, PRIMARY KEY (sport, result_id))",
                "CREATE INDEX ON results (person_id)"):
        c.execute(ddl)
    for t, rows in corpus(random.Random(seed), n_people).items():
        if rows:
            execute_values(c, f"INSERT INTO {t} VALUES %s", rows, page_size=5000)
    cx.commit()
    for t in CORPUS:
        c.execute(f"ANALYZE {t}")
    cx.commit()
    return cx, test_dsn


def _scratch(c):
    c.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY 1")
    return [t for (t,) in c.fetchall() if t not in CORPUS]


def _run(cx, dsn, mode, env_extra):
    c = cx.cursor()
    for t in _scratch(c):               # every run starts from the corpus alone
        c.execute(f'DROP TABLE public."{t}" CASCADE')
    cx.commit()
    kv = _kv(dsn)
    env = dict(os.environ, XCP_DB_HOST=kv.get("host", "127.0.0.1"),
               XCP_DB_PORT=kv.get("port", "5432"), XCP_DB_NAME=DB,
               XCP_DB_USER=kv.get("user", "postgres"), XCP_DB_QUIET="1", **env_extra)
    if kv.get("password"):
        env["XCP_DB_PASSWORD"] = kv["password"]
    env.pop("XCP_PG_SNAPSHOT_FILE", None)
    p = subprocess.run([sys.executable, "-u", os.path.join(ROOT, "engine", "grade_sanity.py"),
                        "--write"], env=env, cwd=ROOT, capture_output=True, text=True,
                       timeout=1200)
    assert p.returncode == 0, (mode, (p.stdout + p.stderr)[-3000:])
    c.execute(f"DROP SCHEMA IF EXISTS snap_{mode} CASCADE; CREATE SCHEMA snap_{mode}")
    tabs = _scratch(c)
    for t in tabs:
        c.execute(f'CREATE TABLE snap_{mode}."{t}" AS SELECT * FROM public."{t}"')
    c.execute("""SELECT pg_get_indexdef(indexrelid) FROM pg_index
                 WHERE indrelid = 'allraces'::regclass ORDER BY indexrelid""")
    idx = [d for (d,) in c.fetchall()]
    cx.commit()
    return tabs, idx, p.stdout


def test_side_by_side_writes_the_same_tables():
    dsn = _dsn()
    cx, test_dsn = _load(dsn, seed=7, n_people=3000)
    try:
        runs = {m: _run(cx, test_dsn, m, e) for m, e in (
            # the 2026-11-16 statements: one connection, the event joins
            # unstaged (2026-11-22)
            ("seq", {GS.SIDE_ENV: "0", GS.EVENT_STAGED_ENV: "0"}),
            ("seqst", {GS.SIDE_ENV: "0"}),
            ("side", {GS.SIDE_ENV: "1"}),
            ("fail", {GS.SIDE_ENV: "1", "XCP_GRADE_SANITY_SIDE_TIMEOUT": "1ms"}))}
        tabs, idx, out = runs["seq"]
        assert "side by side" not in out and "side by side" in runs["side"][2]
        # the overlap (2026-11-20): fld_best from [2]'s grouping, patched
        assert "fld_best_pre" not in out and "fld_best: fld_best_pre" in runs["side"][2]
        assert "failed on its own connection" in runs["fail"][2]
        # the person shards (2026-11-22) ran, and the staged event joins
        assert "person shard 4 of 4" in runs["side"][2]
        assert "single statement instead" not in runs["side"][2] + runs["seqst"][2]
        assert {"grade_fix", "grade_untrusted", "allraces", "gradekind"} <= set(tabs)
        c = cx.cursor()
        # every rule fired on the corpus
        c.execute("SELECT DISTINCT method FROM snap_seq.grade_fix")
        methods = {m for (m,) in c.fetchall()}
        assert {"corroborated", "pinned", "bare_class", "bare_field", "field", "no_grades",
                "stale_grade", "elite_field", "post_collegiate", "adult_club",
                "contradicted", "thin_field", "team_level", "no_evidence"} <= methods, methods
        for mode in ("seqst", "side", "fail"):
            m_tabs, m_idx, _ = runs[mode]
            assert sorted(m_tabs) == sorted(tabs), mode
            assert m_idx == idx, (mode, m_idx)
            for t in tabs:
                for a, b in (("seq", mode), (mode, "seq")):
                    c.execute(f'SELECT count(*) FROM (SELECT * FROM snap_{a}."{t}" EXCEPT ALL '
                              f'SELECT * FROM snap_{b}."{t}") x')
                    assert c.fetchone()[0] == 0, (t, a, b)
    finally:
        cx.close()
        import psycopg2
        admin = psycopg2.connect(dsn)
        admin.autocommit = True
        with admin.cursor() as c2:
            c2.execute(f"DROP DATABASE IF EXISTS {DB}")
        admin.close()
