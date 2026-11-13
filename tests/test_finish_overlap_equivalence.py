"""10_rankings_finish, the 2026-11-13 speed-ups, against the finish before
them: the same ranking_results and athlete_season, row for row, in the same
physical order where the build lays one out, with the same indexes.

    XCP_TWIN_TEST_DSN="host=127.0.0.1 port=5432 user=postgres dbname=postgres" \\
    PGPASSWORD=... python -m pytest -q tests/test_finish_overlap_equivalence.py

The three changes (racecast/build_ranking_results.py), each with its switch:
  * _SEASON_UNITS_IN_REWRITE (XCP_SEASON_UNITS_IN_REWRITE): under
    XCP_RR_ORDERED the season units are stamped on one row per key and the
    board-order copy takes them by a join, instead of an UPDATE of every
    shadow row before the copy;
  * _SEASON_BESIDE_INDEXES (XCP_SEASON_BESIDE_INDEXES): athlete_season built
    in a thread while ranking_results_new's indexes build;
  * _VACUUM_PARALLEL (XCP_VACUUM_PARALLEL): the two VACUUMs side by side.

The corpus is synthetic and built for the stamping's branches: a college
by its campus (the directory), a campus row with no division (the second
pass overwrites it), a name shared by a college and high schools in
several states (Amherst), a school found by (school, state), a school
name held in one state only (found anywhere) and in two (not), a college
found by name anywhere, names that are not teams (cleared), NULL schools
and states, school_unit rows with a NULL is_college, ties in votes, and a
school_identity that moves season states before the stamping reads them.
The real `--stage finish` (main) runs on it once per variant from the same
starting database, and the results are compared with EXCEPT ALL both ways.
"""
import _env  # noqa: F401  -- sets XCP_DB_PASSWORD, must precede config
import contextlib
import io
import os
import random
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "scripts"),
           os.path.join(_ROOT, "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DSN = os.environ.get("XCP_TWIN_TEST_DSN")
_DB = "xcp_finish_equiv_test"
_TABLES = ("ranking_results", "athlete_season")


def _B():
    import build_ranking_results as B
    return B


# ------------------------------------------------------------------ corpus --

_SCHOOLS_HS = ["Amherst", "Jesuit", "Lincoln", "Central", "Washington", "Northside",
               "Only One", "Valley Prep", "Unattached", "N/A", "Leduc Track and Field",
               None]
_SCHOOLS_COL = ["Tufts University", "Amherst", "Jesuit", "Oregon", "Riverside College",
                "Unattached", "Nowhere Tech", None]
_STATES = ["MA", "NE", "WI", "LA", "CA", "OR", "WA", "TX", "NV", "CT", None]
_UNIT = ("division", "region", "conference", "league", "state_div", "section_div",
         "district", "county", "class", "area", "section")


def _schoolUnit(cur, rnd):
    cur.execute("""CREATE TABLE school_unit (school text, state text, is_college boolean,
                       votes int, division text, region text, conference text, league text,
                       state_div text, section_div text, district text, county text,
                       class text, area text, section text)""")
    rows = []

    def add(school, state, col, votes, **vals):
        rows.append((school, state, col, votes) + tuple(vals.get(u) for u in _UNIT))

    # the campus pass: each directory college at its campus state, and the
    # same name as a high school there and elsewhere
    add("Tufts University", "MA", True, 50, division="D3", conference="NESCAC", region="NE")
    add("Amherst", "MA", True, 40, division="D3", conference="NESCAC", region="NE")
    add("Amherst", "MA", False, 30, division="HS-MA", league="PVIAC", county="Hampshire")
    add("Amherst", "WI", False, 20, division="WIAA-D2", league="CWC", county="Portage")
    add("Amherst", "NE", False, 10, division="NSAA-C", league="LCC")
    # a campus row with NO division: the (school, state) pass overwrites it
    add("Jesuit", "LA", True, 45, division=None, conference="GSC", region="SC")
    add("Jesuit", "LA", False, 44, division="LHSAA-5A", district="D1")
    add("Jesuit", "CA", False, 43, division="CIF-SJ", section="Sac-Joaquin", area="North")
    add("Jesuit", "OR", False, 42, division="OSAA-6A", league="Metro")
    add("Oregon", "OR", True, 60, division="D1", conference="Pac-12", region="West")
    # a school in one state only, with a tie in votes inside its key
    add("Lincoln", "OR", False, 5, division="OSAA-5A-a", league="PIL")
    add("Lincoln", "OR", False, 5, division="OSAA-5A-b", league="PIL")
    # a school name in two states: no fallback for a third
    add("Central", "CA", False, 9, division="CIF-CS", section="Central")
    add("Central", "TX", False, 9, division="UIL-6A", district="D6", **{"class": "6A"})
    # a college known by name only, in two states with tied votes
    add("Riverside College", "NY", True, 7, division="NJCAA", region="R3")
    add("Riverside College", "PA", True, 7, division="NJCAA-b", region="R19")
    # a college-ness the table does not know
    add("Valley Prep", "NV", None, 3, division="NIAA-3A")
    add("Valley Prep", "CA", None, 3, division="CIF-SD")
    add("Only One", "WA", False, 4, division="WIAA-2A", county="King")
    # names that are not teams, still in an older school_unit
    add("Unattached", "CA", False, 99, division="none-should-show")
    add("Unattached", "MA", True, 99, division="none-should-show")
    add("N/A", "TX", False, 9, division="none-should-show")
    # the rest at random, as the restamp test builds it
    v = 100
    for sch in ("Washington", "Northside", "Nowhere Tech"):
        for st in rnd.sample(_STATES[:-1], 3):
            for col in (True, False, None):
                if rnd.random() < 0.5:
                    v += 1
                    add(sch, st, col, v if rnd.random() < 0.8 else 100,
                        **{u: (f"{sch[:3]}-{st}-{u}" if rnd.random() < 0.85 else None)
                           for u in _UNIT})
    import psycopg2.extras
    psycopg2.extras.execute_values(cur, "INSERT INTO school_unit VALUES %s", rows)


def _corpus(conn, seed=11):
    """The live tables (empty, the shapes the lab gives them), the stream's
    output in ranking_results_new, and the lookup tables."""
    import layout_lab as L
    import psycopg2.extras
    B = _B()
    rnd = random.Random(seed)
    with conn.cursor() as cur:
        cur.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
        cur.execute(L.ddl("ranking_results", L.RR_COLS,
                          extra=" WITH (autovacuum_enabled = false)"))
        cur.execute(L.ddl("athlete_season", L.AS_COLS,
                          extra=" WITH (autovacuum_enabled = false)"))
        # a hand-made index on the live table rides along (indexDefs)
        cur.execute("CREATE INDEX rr_hand_idx ON ranking_results (school, year)")
        _schoolUnit(cur, rnd)
        cur.execute("""CREATE TABLE college_directory (name_norm text, name text, state text,
                           division text, source text)""")
        cur.execute("""INSERT INTO college_directory VALUES
                         ('tufts university', 'Tufts University', 'MA', 'D3', 'w'),
                         ('amherst', 'Amherst College', 'MA', 'D3', 'w'),
                         ('jesuit', 'Jesuit University', 'LA', 'D2', 'w'),
                         ('oregon', 'University of Oregon', 'OR', 'D1', 'w')""")
        cur.execute("CREATE TABLE school_identity (school text, state text, is_primary boolean)")
        cur.execute("""INSERT INTO school_identity VALUES
                         ('Jesuit', 'CA', true), ('Jesuit', 'LA', false), ('Jesuit', 'OR', false),
                         ('Lincoln', 'OR', true), ('Amherst', 'WI', true), ('Amherst', 'MA', false),
                         ('Central', 'TX', true), ('Central', 'CA', false)""")
    conn.commit()
    B.createShadow(conn, B._LOAD_TABLE, "ranking_results")
    rows, rid = [], 0
    pools = ["hs_m", "hs_f", "ms_m", "ms_f", "college_m", "college_f", "pro_m"]
    for i in range(1500):
        pid = 1000 + i * 7 + (i % 5)
        pool = rnd.choice(pools)
        college = pool.startswith("college")
        home = rnd.choice(_SCHOOLS_COL if college else _SCHOOLS_HS)
        y0 = rnd.randint(2018, 2025)
        for year in range(y0, min(2026, y0 + rnd.randint(1, 3)) + 1):
            for sport in ("XC", "TF"):
                if rnd.random() < 0.3:
                    continue
                for k in range(rnd.randint(1, 7)):
                    rid += 1
                    school = home if rnd.random() < 0.85 else rnd.choice(
                        _SCHOOLS_COL if college else _SCHOOLS_HS)
                    state = rnd.choice(_STATES)
                    sprint = sport == "TF" and rnd.random() < 0.15
                    rating = (None if sprint or rnd.random() < 0.05 else
                              round(rnd.gauss(120, 18) - (25 if rnd.random() < 0.03 else 0), 2))
                    dist = 200.0 if sprint else rnd.choice([1600.0, 3200.0, 5000.0, 8000.0])
                    unitv = [None] * len(_UNIT)
                    if college and rnd.random() < 0.6:
                        unitv[0] = "D1-row"          # the season school pick reads division
                    rows.append((
                        sport, rid if not (college and pid % 2) else -rid, pid, pool, rating,
                        f"{year}-{rnd.randint(9, 11)}-{rnd.randint(1, 28):02d}", year, state,
                        school, rnd.choice(["9", "10", "11", "12", "FR-1", "SO-2", None]),
                        rnd.randint(1, 400), rnd.randint(1, 6), None,
                        round(rnd.uniform(500, 1800), 2), dist,
                        rnd.randint(1, 10**6) if sport == "TF" else None,
                        *unitv, None, None))
    rnd.shuffle(rows)                                  # the stream's scrape order
    cols = ", ".join(f'"{c}"' for c, _t in L.RR_COLS)
    with conn.cursor() as cur:
        psycopg2.extras.execute_values(
            cur, f"INSERT INTO {B._LOAD_TABLE} ({cols}) VALUES %s", rows, page_size=5000)
        cur.execute("ANALYZE school_unit; ANALYZE college_directory; ANALYZE school_identity")
    conn.commit()
    return len(rows)


# --------------------------------------------------------------- harness --

@pytest.fixture(scope="module")
def db():
    if not DSN:
        pytest.skip("set XCP_TWIN_TEST_DSN to a scratch Postgres")
    import psycopg2
    admin = psycopg2.connect(DSN)
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(f"DROP DATABASE IF EXISTS {_DB} WITH (FORCE)")
        cur.execute(f"CREATE DATABASE {_DB}")
    tdsn = " ".join(p for p in DSN.split() if not p.startswith("dbname=")) + f" dbname={_DB}"
    try:
        yield tdsn
    finally:
        with admin.cursor() as cur:
            cur.execute(f"DROP DATABASE IF EXISTS {_DB} WITH (FORCE)")
        admin.close()


def _finish(tdsn, monkeypatch, snap, *, lean, ordered, fold, beside, vac_par,
            patches=()):
    """A fresh corpus, the real `--stage finish`, and its two tables kept in
    schema `snap` with each row's physical position (rn, by ctid)."""
    import psycopg2
    B = _B()

    @contextlib.contextmanager
    def pooled():
        c = psycopg2.connect(tdsn)
        try:
            yield c
        finally:
            c.close()

    monkeypatch.setattr(B, "getConn", pooled)
    monkeypatch.setattr(B, "buildBoardSizes", lambda conn: None)   # the site's counts
    monkeypatch.setenv("XCP_RR_LEAN", "1" if lean else "0")
    monkeypatch.setenv("XCP_RR_ORDERED", "1" if ordered else "0")
    monkeypatch.setattr(B, "_SEASON_UNITS_IN_REWRITE", fold)
    monkeypatch.setattr(B, "_SEASON_BESIDE_INDEXES", beside)
    monkeypatch.setattr(B, "_VACUUM_PARALLEL", vac_par)
    for name, value in patches:
        monkeypatch.setattr(B, name, value)
    monkeypatch.setattr(sys, "argv", ["build_ranking_results.py", "--stage", "finish"])
    B._PHASES.clear()
    conn = psycopg2.connect(tdsn)
    try:
        _corpus(conn)
    finally:
        conn.close()
    log = io.StringIO()
    with contextlib.redirect_stdout(log):
        B.main()
    conn = psycopg2.connect(tdsn)
    try:
        with conn.cursor() as cur:
            cur.execute(f"DROP SCHEMA IF EXISTS {snap} CASCADE; CREATE SCHEMA {snap}")
            for t in _TABLES:
                cur.execute(f"CREATE TABLE {snap}.{t} AS SELECT row_number() OVER "
                            f"(ORDER BY ctid) AS rn, * FROM public.{t}")
            cur.execute(f"""CREATE TABLE {snap}.idx AS
                            SELECT tablename, indexname, indexdef FROM pg_indexes
                            WHERE schemaname = 'public' AND tablename IN %s""", (_TABLES,))
            cur.execute(f"""CREATE TABLE {snap}.rel AS
                            SELECT relname, relpersistence, reloptions, relkind
                            FROM pg_class WHERE relnamespace = 'public'::regnamespace
                              AND relname IN %s""", (_TABLES,))
            cur.execute(f"""CREATE TABLE {snap}.cols AS
                            SELECT c.relname, a.attnum, a.attname,
                                   format_type(a.atttypid, a.atttypmod) AS typ, a.attnotnull
                            FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid
                            WHERE c.relnamespace = 'public'::regnamespace
                              AND c.relname IN %s AND a.attnum > 0
                              AND NOT a.attisdropped""", (_TABLES,))
        conn.commit()
    finally:
        conn.close()
    return log.getvalue()


def _diff(tdsn, a, b, table, cols="*"):
    """Rows of a.table not in b.table and the reverse (EXCEPT ALL)."""
    import psycopg2
    conn = psycopg2.connect(tdsn)
    try:
        with conn.cursor() as cur:
            out = []
            for x, y in ((a, b), (b, a)):
                cur.execute(f"SELECT count(*) FROM (SELECT {cols} FROM {x}.{table} "
                            f"EXCEPT ALL SELECT {cols} FROM {y}.{table}) d")
                out.append(cur.fetchone()[0])
            cur.execute(f"SELECT (SELECT count(*) FROM {a}.{table}), "
                        f"(SELECT count(*) FROM {b}.{table})")
            return tuple(out), cur.fetchone()
    finally:
        conn.close()


def _noRn(tdsn, snap, table):
    import psycopg2
    conn = psycopg2.connect(tdsn)
    try:
        with conn.cursor() as cur:
            cur.execute("""SELECT string_agg(quote_ident(attname), ', ' ORDER BY attnum)
                           FROM pg_attribute WHERE attrelid = %s::regclass AND attnum > 0
                             AND NOT attisdropped AND attname <> 'rn'""",
                        (f"{snap}.{table}",))
            return cur.fetchone()[0]
    finally:
        conn.close()


def _scalar(tdsn, sql):
    import psycopg2
    conn = psycopg2.connect(tdsn)
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            return cur.fetchone()
    finally:
        conn.close()


def _assertSame(tdsn, old, new, as_in_order):
    for t in _TABLES:
        # the physical order too, where the build lays one out: rr always
        # (the stream's order, or board order), athlete_season when ordered
        cols = "*" if (t == "ranking_results" or as_in_order) else _noRn(tdsn, old, t)
        (a, b), (n_old, n_new) = _diff(tdsn, old, new, t, cols)
        assert n_old == n_new and n_old > 0, (t, n_old, n_new)
        assert (a, b) == (0, 0), (t, cols[:20], a, b)
    for meta in ("idx", "rel", "cols"):
        (a, b), (n_old, n_new) = _diff(tdsn, old, new, meta)
        assert (a, b) == (0, 0) and n_old == n_new and n_old > 0, (meta, a, b, n_old, n_new)


# ----------------------------------------------------------------- tests --

_OLD = dict(fold=False, beside=False, vac_par=False)
_NEW = dict(fold=True, beside=True, vac_par=True)


@pytest.mark.parametrize("lean,ordered", [(False, True), (True, True), (False, False)],
                         ids=["ordered", "lean_ordered", "flags_off"])
def test_the_new_finish_builds_the_old_tables(db, monkeypatch, lean, ordered):
    tag = f"{'l' if lean else ''}{'o' if ordered else 'x'}"
    old_log = _finish(db, monkeypatch, f"snap_old_{tag}", lean=lean, ordered=ordered, **_OLD)
    new_log = _finish(db, monkeypatch, f"snap_new_{tag}", lean=lean, ordered=ordered, **_NEW)
    _assertSame(db, f"snap_old_{tag}", f"snap_new_{tag}", as_in_order=ordered)
    # the stamping did its work: every branch is in the result
    stamped = _scalar(db, f"""
        SELECT count(*) FILTER (WHERE school = 'Tufts University' AND division = 'D3'),
               count(*) FILTER (WHERE school = 'Amherst' AND pool LIKE 'college%%'
                                  AND conference = 'NESCAC'),
               count(*) FILTER (WHERE school = 'Amherst' AND pool NOT LIKE 'college%%'
                                  AND division LIKE 'WIAA%%'),
               count(*) FILTER (WHERE school = 'Jesuit' AND pool LIKE 'college%%'
                                  AND conference = 'GSC' AND division IS NULL),
               count(*) FILTER (WHERE school = 'Lincoln' AND division LIKE 'OSAA-5A%%'),
               count(*) FILTER (WHERE school = 'Riverside College' AND division LIKE 'NJCAA%%'),
               count(*) FILTER (WHERE school IN ('Unattached', 'N/A') AND division IS NOT NULL),
               count(*) FILTER (WHERE school IS NULL)
        FROM snap_new_{tag}.athlete_season""")
    assert all(n > 0 for n in stamped[:6]) and stamped[6] == 0 and stamped[7] > 0, stamped
    # the switches did what they say
    if ordered:
        assert "unit columns from tmp_season_unit_keys" in new_log
        assert "unit columns from" not in old_log
    else:
        assert "unit columns from" not in new_log
    assert "[season] " in new_log and "[season] " not in old_log
    assert "(side by side)" in new_log and "(side by side)" not in old_log


def test_the_fold_without_room_to_sort_stamps_by_key(db, monkeypatch):
    """The ordered rewrite stands down (no disk room): the keys are copied
    on by UPDATE before SET LOGGED -- the same table as the old stamping
    followed by SET LOGGED."""
    room = [("_freeBytes", lambda cur: 0)]
    _finish(db, monkeypatch, "snap_old_room", lean=False, ordered=True, patches=room, **_OLD)
    log = _finish(db, monkeypatch, "snap_new_room", lean=False, ordered=True,
                  patches=room, **_NEW)
    assert "unit columns copied from the keys" in log
    _assertSame(db, "snap_old_room", "snap_new_room", as_in_order=False)


def test_a_failed_stamping_leaves_the_same_empty_units(db, monkeypatch):
    """The stamping raises: the old finish swapped in NULL unit columns,
    and so does the fold (the keys stay as they were built, empty)."""
    def boom(_s):
        raise ValueError("boom")
    bad = [("_isTeamName", boom)]
    _finish(db, monkeypatch, "snap_old_fail", lean=False, ordered=True, patches=bad, **_OLD)
    _finish(db, monkeypatch, "snap_new_fail", lean=False, ordered=True, patches=bad, **_NEW)
    _assertSame(db, "snap_old_fail", "snap_new_fail", as_in_order=True)
    n = _scalar(db, "SELECT count(*) FROM snap_new_fail.athlete_season "
                    "WHERE division IS NOT NULL OR conference IS NOT NULL")[0]
    assert n == 0


def test_fewer_shards_beside_the_indexes_build_the_same_table(db, monkeypatch):
    """XCP_SEASON_OVERLAP_SHARDS: a person is in one shard whatever the
    count, so the seasons are the same."""
    _finish(db, monkeypatch, "snap_old_sh", lean=False, ordered=True, **_OLD)
    _finish(db, monkeypatch, "snap_new_sh", lean=False, ordered=True,
            patches=[("_SEASON_OVERLAP_SHARDS", 3)], **_NEW)
    _assertSame(db, "snap_old_sh", "snap_new_sh", as_in_order=True)
