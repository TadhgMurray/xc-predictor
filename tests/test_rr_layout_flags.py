"""The boards tables' layout switches (racecast/rr_layout.py):
XCP_RR_LEAN and XCP_RR_ORDERED, docs/TABLE-LAYOUT-EXPERIMENTS.md steps 1-2.

    python -m pytest -q tests/test_rr_layout_flags.py
    XCP_TWIN_TEST_DSN="host=/tmp/pgtest port=54329 user=postgres dbname=postgres" \\
        python -m pytest -q tests/test_rr_layout_flags.py      # + Postgres

(a) Off (the default), the build sends today's DDL: the canonical list is
    the literal, every CREATE INDEX is the catalogue's or the list's with
    only the shadow's name put in, the shadow is LIKE the live table and
    SET LOGGED.
(b) On, the indexes are fillfactor 100 and the four rating trees partial,
    and verifyIndexes ACCEPTS what pg_indexes writes back
    (`WITH (fillfactor='100') WHERE (...)`) -- the trap the plan names: a
    signature that kept the storage clause would call every index missing
    and refuse the swap. 11b (add_page_indexes) and ensure_ranking_indexes
    read the lean trees as the ones they want.
(c) The real finish step (createShadow, relog, buildIndexes) under each
    switch builds tables on which every board, count and page lookup the
    site sends returns the same rows in the same order; the deep-page seek
    table (build_board_seek) and the events precompute's source rows too.
(d) The ordered rewrite keeps every row: count, multiset and checksum, in
    board order on disk; and it falls back to SET LOGGED when it has no room.

The Postgres tests make their own database (xcp_rr_layout_test) on the
scratch server and drop it afterwards.
"""
import contextlib
import os
import re
import sys

import _env  # noqa: F401  -- sets XCP_DB_PASSWORD, must precede config

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "scripts"),
           os.path.join(_ROOT, "engine"), os.path.dirname(os.path.abspath(__file__))):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest                                                  # noqa: E402

import rr_layout                                               # noqa: E402

_DB = "xcp_rr_layout_test"
_FLAGS = ("XCP_RR_LEAN", "XCP_RR_ORDERED")


@pytest.fixture
def off(monkeypatch):
    for k in _FLAGS:
        monkeypatch.delenv(k, raising=False)


@pytest.fixture
def lean(monkeypatch):
    monkeypatch.setenv("XCP_RR_LEAN", "1")
    monkeypatch.delenv("XCP_RR_ORDERED", raising=False)


def _B():
    import build_ranking_results as B
    return B


def _pgRendering(cols, ff=None):
    """What pg_indexes.indexdef says for a (cols) spec built with `ff`."""
    m = re.match(r"(.*?)\s+WHERE\s+(.*)$", cols, re.S)
    head, pred = (m.group(1), m.group(2)) if m else (cols, None)
    return (f"CREATE INDEX x ON public.t USING btree {head}"
            + (f" WITH (fillfactor='{ff}')" if ff else "")
            + (f" WHERE ({pred})" if pred else ""))


# ------------------------------------------------------------------ (a) --

def test_the_switches_default_off(off):
    assert not rr_layout.lean() and not rr_layout.ordered()
    for v in ("0", "", "no", "off"):
        os.environ["XCP_RR_LEAN"] = v
        assert not rr_layout.lean()
    os.environ.pop("XCP_RR_LEAN")


def test_off_the_canonical_list_is_the_literal(off):
    B = _B()
    for t, lst in B._CANONICAL_INDEXES.items():
        assert B.canonicalIndexes(t) == list(lst), t


def test_off_an_index_statement_is_unchanged(off):
    """Today's statements carry no storage clause; withStorage leaves them
    byte for byte. A lean build's clause is taken back out (the rollback)."""
    B = _B()
    for t, lst in B._CANONICAL_INDEXES.items():
        for n, cols in lst:
            sql = f"CREATE INDEX IF NOT EXISTS {t}_new_{n} ON public.{t}_new {cols}"
            assert rr_layout.withStorage(sql) == sql
            rendered = _pgRendering(cols)
            assert rr_layout.withStorage(rendered) == rendered
            assert rr_layout.withStorage(_pgRendering(cols, 100)) == rendered


# ------------------------------------------------------------------ (b) --

def test_lean_partial_is_the_four_rating_trees_only(lean):
    B = _B()
    got = {n: c for n, c in B.canonicalIndexes("ranking_results")}
    partial = {n for n, c in got.items() if "speed_rating IS NOT NULL" in c}
    assert partial == {"rr_pool_rating_idx", "rr_pool_sport_rating_idx",
                       "rr_board_rating_idx", "rr_perf_dist_rating_idx"}
    # ⚠ NOT the time trees: the PR per-distance walk has no time predicate
    for n in ("rr_board_time_idx", "rr_pr_time_idx", "rr_pr_sport_time_idx"):
        assert "WHERE" not in got[n], n
    assert got["rr_board_mark_idx"].endswith("WHERE event_kind IS NOT NULL")
    assert B.canonicalIndexes("athlete_season") == list(B._CANONICAL_INDEXES["athlete_season"])
    # every prefix of a partial tree is the prefix of a full one, so a lookup
    # that names no rating keeps an index
    full = [re.sub(r"\s+DESC|\(|\)", "", c).split(",") for n, c in got.items()
            if "WHERE" not in c]
    full = [[x.strip() for x in f] for f in full]
    for n in partial:
        keys = [x.strip() for x in re.sub(r"\s+DESC|\(|\)", "", got[n].split(" WHERE ")[0]).split(",")]
        prefix = [k for k in keys if k != "speed_rating"]
        assert any(f[:len(prefix)] == prefix for f in full), n


def test_lean_fillfactor_goes_before_the_predicate(lean):
    w = rr_layout.withStorage
    assert (w("CREATE INDEX a ON public.t (x, y DESC) WHERE y IS NOT NULL")
            == "CREATE INDEX a ON public.t (x, y DESC) WITH (fillfactor = 100) WHERE y IS NOT NULL")
    assert (w("CREATE INDEX a ON public.t USING btree (p) INCLUDE (q, r)")
            == "CREATE INDEX a ON public.t USING btree (p) INCLUDE (q, r) WITH (fillfactor = 100)")
    # replayed from a lean catalogue: one clause, not two
    assert (w("CREATE INDEX a ON public.t USING btree (p) WITH (fillfactor='100')")
            == "CREATE INDEX a ON public.t USING btree (p) WITH (fillfactor = 100)")
    # another access method, or other storage options: left alone
    gin = "CREATE INDEX a ON public.t USING gin (x gin_trgm_ops)"
    assert w(gin) == gin
    other = "CREATE INDEX a ON public.t USING btree (p) WITH (deduplicate_items='off')"
    assert w(other) == other


def test_lean_every_index_matches_its_catalogue_rendering(lean):
    """★ THE TRAP. pg_indexes writes `... WITH (fillfactor='100') WHERE
    (speed_rating IS NOT NULL)`; the signature must still be the canonical
    entry's, or verifyIndexes refuses the swap."""
    B = _B()
    for t in B._CANONICAL_INDEXES:
        for n, cols in B.canonicalIndexes(t):
            assert B._indexSig(cols) == B._indexSig(_pgRendering(cols, 100)), (t, n)
            assert B._indexSig(cols) == B._indexSig(_pgRendering(cols)), (t, n)
    # and a partial tree is NOT a full one: the build must not mistake them
    spec = dict(B.canonicalIndexes("ranking_results"))["rr_pool_rating_idx"]
    assert B._indexSig(spec) != B._indexSig("(pool, speed_rating DESC)")


class _Catalogue:
    """A connection whose every read answers `rows` (pg_indexes)."""
    def __init__(self, rows):
        self.rows, self.sql = rows, []

    def cursor(self):
        outer = self

        class C:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def execute(self, sql, *a):
                outer.sql.append(sql)

            def fetchall(self):
                return list(outer.rows)

            def fetchone(self):
                # _freeIndexName: no index of that name, no relation either
                return (None,) if "to_regclass" in outer.sql[-1] else None
        return C()

    def commit(self):
        pass

    def rollback(self):
        pass


def test_lean_verify_accepts_what_pg_writes_back(lean, monkeypatch):
    B = _B()
    rows = [(f"x{i}", _pgRendering(c, 100))
            for i, (_n, c) in enumerate(B.canonicalIndexes("ranking_results"))]
    monkeypatch.setattr(B, "getConn", lambda: pytest.fail("verify tried to rebuild"))
    B.verifyIndexes(_Catalogue([(r[1],) for r in rows]), "ranking_results_new", "ranking_results")


def test_lean_verify_refuses_a_full_tree_where_a_partial_is_wanted(lean, monkeypatch):
    """The other direction: a FULL rating tree is not the lean one, so the
    swap is refused rather than calling it present (and the rebuild runs)."""
    B = _B()
    rows = [(_pgRendering(re.sub(r" WHERE speed_rating IS NOT NULL", "", c), 100),)
            for _n, c in B.canonicalIndexes("ranking_results")]
    tried = []

    @contextlib.contextmanager
    def fake():
        tried.append(1)
        yield _Catalogue([])
    monkeypatch.setattr(B, "getConn", fake)
    with pytest.raises(RuntimeError, match="REFUSING"):
        B.verifyIndexes(_Catalogue(rows), "ranking_results_new", "ranking_results")
    assert len(tried) == 4


def test_lean_a_live_full_tree_is_rebuilt_partial_and_back(monkeypatch):
    """indexDefs: the catalogue's copy of a canonical name whose definition
    changed is dropped, and the list's is built -- in both directions, so
    turning the switch off rolls the trees back."""
    B = _B()
    full = "(pool, speed_rating DESC)"
    part = full + " WHERE speed_rating IS NOT NULL"
    live_full = [("ranking_results_rr_pool_rating_idx",
                  f"CREATE INDEX ranking_results_rr_pool_rating_idx ON public.ranking_results USING btree {full}")]
    live_part = [("ranking_results_rr_pool_rating_idx",
                  "CREATE INDEX ranking_results_rr_pool_rating_idx ON public.ranking_results "
                  "USING btree (pool, speed_rating DESC) WITH (fillfactor='100') "
                  "WHERE (speed_rating IS NOT NULL)")]
    monkeypatch.setenv("XCP_RR_LEAN", "1")
    defs = dict(B.indexDefs(_Catalogue(live_full), "ranking_results"))
    assert defs["ranking_results_rr_pool_rating_idx"].endswith(part)
    defs = dict(B.indexDefs(_Catalogue(live_part), "ranking_results"))
    assert "WHERE (speed_rating IS NOT NULL)" in defs["ranking_results_rr_pool_rating_idx"]
    monkeypatch.setenv("XCP_RR_LEAN", "0")
    defs = dict(B.indexDefs(_Catalogue(live_part), "ranking_results"))
    assert defs["ranking_results_rr_pool_rating_idx"].endswith(full)


def test_the_page_index_step_wants_the_lean_tree(lean):
    """11b (add_page_indexes) lists rr_perf_dist_rating_idx: under the switch
    it must want the partial tree and read `WITH (fillfactor='100')` as no
    difference, or it builds a full `_f` copy beside the lean one."""
    src = open(os.path.join(_ROOT, "scripts", "add_page_indexes.py"), encoding="utf-8").read()
    assert "rr_layout.leanSpec(table, name, spec)" in src
    assert 'rr_layout._ANY_WITH.sub("", s)' in src
    assert rr_layout.leanSpec("ranking_results", "rr_perf_dist_rating_idx",
                              "(pool, sport, distance, speed_rating DESC)").endswith(
        "WHERE speed_rating IS NOT NULL")
    ens = open(os.path.join(_ROOT, "scripts", "ensure_ranking_indexes.py"), encoding="utf-8").read()
    assert "canonicalIndexes(table)" in ens and "return _indexSig(ddl)" in ens


# --------------------------------------------------------- Postgres -----

def _dsnParts(dsn):
    return dict(kv.split("=", 1) for kv in dsn.split())


@pytest.fixture(scope="module")
def labdb(tmp_path_factory):
    """A database of its own, filled by the layout lab's generator at a
    small scale (its `base`: the live layout, the canonical indexes)."""
    dsn = os.environ.get("XCP_TWIN_TEST_DSN")
    if not dsn:
        pytest.skip("set XCP_TWIN_TEST_DSN to a scratch Postgres")
    import psycopg2
    admin = psycopg2.connect(dsn)
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(f"DROP DATABASE IF EXISTS {_DB} WITH (FORCE)")
        cur.execute(f"CREATE DATABASE {_DB}")
    parts = _dsnParts(dsn)
    env = {"XCP_DB_HOST": parts.get("host", "localhost"), "XCP_DB_PORT": parts.get("port", "5432"),
           "XCP_DB_USER": parts.get("user", "postgres"), "XCP_DB_NAME": _DB}
    saved = {k: os.environ.get(k) for k in list(env) + list(_FLAGS)}
    os.environ.update(env)
    for k in _FLAGS:
        os.environ.pop(k, None)
    import layout_lab as L
    import argparse
    out_dir = L.OUT_DIR
    L.OUT_DIR = str(tmp_path_factory.mktemp("layout_lab_out"))   # its JSON, not the repo's
    try:
        with contextlib.redirect_stdout(open(os.devnull, "w")):
            L.gen(argparse.Namespace(persons=3000))
        yield L
    finally:
        L.OUT_DIR = out_dir
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        with admin.cursor() as cur:
            cur.execute(f"DROP DATABASE IF EXISTS {_DB} WITH (FORCE)")
        admin.close()


@pytest.fixture
def builder(labdb, monkeypatch):
    """The builder, its pooled connections pointed at the lab database, and
    every statement any of its connections sends, recorded."""
    import psycopg2
    import psycopg2.extensions
    B = _B()
    sent = []

    class Rec(psycopg2.extensions.cursor):
        def execute(self, sql, args=None):
            sent.append(" ".join(str(sql).split()))
            return super().execute(sql, args)

    def connect():
        c = psycopg2.connect(labdb.dsn(), cursor_factory=Rec)
        return c

    @contextlib.contextmanager
    def pooled():
        c = connect()
        try:
            yield c
        finally:
            c.close()
    monkeypatch.setattr(B, "getConn", pooled)
    monkeypatch.setattr(labdb, "connect",
                        lambda autocommit=True: _withAuto(connect(), autocommit))
    return B, labdb, sent


def _withAuto(c, autocommit):
    c.autocommit = autocommit
    with c.cursor() as cur:
        cur.execute("SET jit = off")
    return c


def _variant(builder, name):
    B, L, sent = builder
    sent.clear()
    with contextlib.redirect_stdout(open(os.devnull, "w")):
        rep = L.builder_variant(name)
    return rep, list(sent)


def test_off_the_finish_sends_todays_ddl(builder):
    """(a) on Postgres: the shadow is LIKE the live table, SET LOGGED, and
    every CREATE INDEX is the live catalogue's definition (or the list's)
    with only the shadow's name put in -- no storage clause, no predicate
    added, no rewrite table."""
    B, L, _ = builder
    conn = L.connect()
    with conn.cursor() as cur:
        cur.execute("""SELECT tablename, indexname, indexdef FROM pg_indexes
                       WHERE schemaname = 'public'
                         AND tablename IN ('ranking_results', 'athlete_season')""")
        live = cur.fetchall()
    conn.close()
    _rep, sent = _variant(builder, "flag_off")
    for like in ("ranking_results", "athlete_season"):
        assert (f"CREATE UNLOGGED TABLE {like}_new (LIKE {like} INCLUDING DEFAULTS "
                f"INCLUDING CONSTRAINTS) WITH (autovacuum_enabled = false)") in sent
        assert f"ALTER TABLE {like}_new SET LOGGED" in sent
    assert not [s for s in sent if "_sorted" in s or "ORDER BY pool" in s]
    made = sorted(s for s in sent if s.startswith("CREATE INDEX"))
    want = sorted(" ".join(d.replace(f" ON public.{t} ", f" ON public.{t}_new ")
                           .replace(f"INDEX {n} ", f"INDEX IF NOT EXISTS {t}_new_{n} ").split())
                  for t, n, d in live)
    assert made == want
    assert not [s for s in made if "fillfactor" in s]


def _cols(cur, sch, t):
    cur.execute("""SELECT a.attname, format_type(a.atttypid, a.atttypmod), a.attnotnull,
                          pg_get_expr(d.adbin, d.adrelid), a.attcollation
                   FROM pg_attribute a LEFT JOIN pg_attrdef d
                        ON d.adrelid = a.attrelid AND d.adnum = a.attnum
                   WHERE a.attrelid = %s::regclass AND a.attnum > 0 AND NOT a.attisdropped
                   ORDER BY a.attnum""", (f"{sch}.{t}",))
    return cur.fetchall()


def _layout(cur, sch):
    """{table: {index signature: (reloptions, partial predicate)}}"""
    out = {}
    for t in ("ranking_results", "athlete_season"):
        cur.execute("""SELECT pg_get_indexdef(i.indexrelid), c.reloptions,
                              pg_get_expr(i.indpred, i.indrelid)
                       FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid
                       WHERE i.indrelid = %s::regclass""", (f"{sch}.{t}",))
        out[t] = {rr_layout.indexSig(d): (o, p) for d, o, p in cur.fetchall()}
    return out


@pytest.fixture(scope="module")
def built(labdb):
    """The four switch settings, each built once by the real finish step."""
    return {}


def _build(builder, built, name):
    if name not in built:
        built[name] = _variant(builder, name)
    return built[name]


def test_lean_builds_the_lean_indexes_and_narrow_rows(builder, built):
    """(b) on Postgres: what the lean build leaves, read back from the
    catalogue -- and verifyIndexes (inside buildIndexes) passed on it."""
    B, L, _ = builder
    _rep, sent = _build(builder, built, "flag_lean")
    made = [s for s in sent if s.startswith("CREATE INDEX")]
    assert made and all("WITH (fillfactor = 100)" in s for s in made)
    conn = L.connect()
    with conn.cursor() as cur:
        lay = _layout(cur, "lab_flag_lean")
        for t, idx in lay.items():
            assert all(o == ["fillfactor=100"] for o, _p in idx.values()), (t, idx)
        preds = {s for s, (_o, p) in lay["ranking_results"].items() if p}
        os.environ["XCP_RR_LEAN"] = "1"
        try:
            want = {B._indexSig(c) for _n, c in B.canonicalIndexes("ranking_results") if "WHERE" in c}
            assert preds == want
            # verify, again, explicitly, on the moved table: nothing missing
            have = {B._indexSig(d) for d in
                    (r[0] for r in _q(cur, "SELECT indexdef FROM pg_indexes WHERE schemaname = "
                                           "'lab_flag_lean' AND tablename = 'ranking_results'"))}
            assert {B._indexSig(c) for _n, c in B.canonicalIndexes("ranking_results")} <= have
        finally:
            os.environ.pop("XCP_RR_LEAN")
        # ★ THE NARROW ROW: the same columns, types, NOT NULLs, defaults and
        #   collations as the live table, only the order moved
        live = _cols(cur, "public", "ranking_results")
        lean_ = _cols(cur, "lab_flag_lean", "ranking_results")
        assert sorted(live) == sorted(lean_)
        assert [c[0] for c in live] != [c[0] for c in lean_]
        widths = [_q(cur, "SELECT typlen, typalign FROM pg_type WHERE oid = %s::regtype", (c[1],))[0]
                  for c in lean_]
        ranks = [rr_layout.alignRank(*w) for w in widths]
        assert ranks == sorted(ranks)
        # athlete_season keeps the live order
        assert _cols(cur, "public", "athlete_season") == _cols(cur, "lab_flag_lean", "athlete_season")
    conn.close()


def _q(cur, sql, args=None):
    cur.execute(sql, args)
    return cur.fetchall()


def test_the_narrow_shadow_is_like_the_live_one_column_for_column(builder):
    """(b) value-preserving: a live table with a CHECK, a default, a NOT
    NULL, a collation and a typmod; the narrow shadow has every one of them
    on the same column, and a row written by name reads back the same."""
    B, L, _ = builder
    conn = L.connect(autocommit=False)
    with conn.cursor() as cur:
        cur.execute("""DROP TABLE IF EXISTS nt_live, nt_like, nt_narrow;
            CREATE TABLE nt_live (k text COLLATE "C" NOT NULL, a bigint DEFAULT 7,
                                  n numeric(6,2), d date, b smallint, f boolean,
                                  r real CHECK (r > 0), v varchar(5) DEFAULT 'x',
                                  CONSTRAINT nt_pos CHECK (a >= 0))""")
        body = B._narrowShadowBody(cur, "nt_live")
        cur.execute("CREATE TABLE nt_like (LIKE nt_live INCLUDING DEFAULTS INCLUDING CONSTRAINTS)")
        cur.execute(f"CREATE TABLE nt_narrow ({body})")
        like, narrow = _cols(cur, "public", "nt_like"), _cols(cur, "public", "nt_narrow")
        assert sorted(like) == sorted(narrow)
        # 8-byte, 4-byte (live order kept), 2, 1, then the variable-width
        assert [c[0] for c in narrow] == ["a", "d", "r", "b", "f", "k", "n", "v"]
        con = "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid = %s::regclass ORDER BY 1"
        assert _q(cur, con, ("nt_like",)) == _q(cur, con, ("nt_narrow",))
        cols = "k, a, n, d, b, f, r, v"
        for t in ("nt_like", "nt_narrow"):
            cur.execute(f"INSERT INTO {t} ({cols}) VALUES ('z', 3, 1.25, '2024-01-02', 2, true, 1.5, 'ab'),"
                        f" ('y', DEFAULT, NULL, NULL, NULL, NULL, NULL, DEFAULT)")
        assert (_q(cur, f"SELECT {cols} FROM nt_like ORDER BY k")
                == _q(cur, f"SELECT {cols} FROM nt_narrow ORDER BY k"))
        with pytest.raises(Exception):
            cur.execute("INSERT INTO nt_narrow (k, r) VALUES ('q', -1)")
    conn.rollback()
    conn.close()


def _fingerprint(cur, sch, t):
    """count and an order-free checksum over the rows, columns by NAME (the
    lean row has another physical order)"""
    cols = [c[0] for c in sorted(_cols(cur, "public", t))]
    row = ", ".join(f'"{c}"' for c in cols)
    return _q(cur, f"SELECT count(*), sum(hashtext(ROW({row})::text)::bigint), "
                   f"md5(string_agg(ROW({row})::text, '|' ORDER BY ROW({row})::text)) "
                   f"FROM {sch}.{t}")[0]


@pytest.mark.parametrize("name", ["flag_ordered", "flag_both"])
def test_ordered_keeps_every_row_in_board_order(builder, built, name):
    """(d) every row, every value: count, checksum and the md5 of every row
    sorted, against the base; and the heap really is in board order."""
    B, L, _ = builder
    _rep, sent = _build(builder, built, name)
    assert [s for s in sent if s.startswith("INSERT INTO ranking_results_new_sorted")]
    assert [s for s in sent if s.startswith("INSERT INTO athlete_season_new_sorted")]
    assert not [s for s in sent if s.endswith("SET LOGGED")]
    conn = L.connect()
    with conn.cursor() as cur:
        sch = f"lab_{name}"
        for t in ("ranking_results", "athlete_season"):
            assert _fingerprint(cur, sch, t) == _fingerprint(cur, "public", t), t
            assert _q(cur, "SELECT relpersistence FROM pg_class WHERE oid = %s::regclass",
                      (f"{sch}.{t}",))[0][0] == "p"
            # ★ IN BOARD ORDER ON DISK. Exactly, up to the free-space map:
            #   an INSERT tops up a page's leftover bytes with a later, short
            #   row (0.2% of rows in the lab), so count the rows whose page
            #   is BEFORE the page of the row ahead of them on the board.
            order = rr_layout.BOARD_ORDER[t]
            n, back = _q(cur, f"""
                WITH a AS (SELECT (ctid::text::point)[0] AS pg,
                                  lag((ctid::text::point)[0]) OVER (ORDER BY {order}) AS prev
                           FROM {sch}.{t})
                SELECT count(*), count(*) FILTER (WHERE pg < prev) FROM a""")[0]
            assert back <= 0.01 * n, (t, n, back)
            base_back = _q(cur, f"""
                WITH a AS (SELECT (ctid::text::point)[0] AS pg,
                                  lag((ctid::text::point)[0]) OVER (ORDER BY {order}) AS prev
                           FROM public.{t})
                SELECT count(*) FILTER (WHERE pg < prev) FROM a""")[0][0]
            assert base_back > 20 * max(back, 1), (t, base_back, back)
    conn.close()


def test_ordered_falls_back_when_the_sort_has_no_room(builder, monkeypatch):
    """A sort that runs out of temp space rolls back whole: the shadow is
    untouched, set logged instead, and the build goes on."""
    B, L, _ = builder
    monkeypatch.setenv("XCP_RR_ORDERED", "1")
    monkeypatch.setattr(B, "_SEASON_WORK_MEM", ("64kB",))
    conn = L.connect(autocommit=False)
    with conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS rr_room_new, rr_room_new_sorted")
        cur.execute("CREATE UNLOGGED TABLE rr_room_new AS SELECT * FROM public.ranking_results")
        before = _q(cur, "SELECT count(*), sum(hashtext(t::text)::bigint) FROM rr_room_new t")
        cur.execute("SET temp_file_limit = '256kB'")
    conn.commit()
    monkeypatch.setitem(rr_layout.BOARD_ORDER, "rr_room", rr_layout.BOARD_ORDER["ranking_results"])
    with contextlib.redirect_stdout(open(os.devnull, "w")):
        B.relog(conn, "rr_room_new", "rr_room")
    with conn.cursor() as cur:
        assert _q(cur, "SELECT to_regclass('rr_room_new_sorted')")[0][0] is None
        assert _q(cur, "SELECT relpersistence FROM pg_class WHERE relname = 'rr_room_new'")[0][0] == "p"
        assert _q(cur, "SELECT count(*), sum(hashtext(t::text)::bigint) FROM rr_room_new t") == before
        cur.execute("DROP TABLE rr_room_new")
    conn.commit()
    conn.close()


def test_a_resumed_finish_does_not_sort_twice(builder, monkeypatch):
    B, L, _ = builder
    monkeypatch.setenv("XCP_RR_ORDERED", "1")
    conn = L.connect(autocommit=False)
    with conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS rr_again_new")
        cur.execute("CREATE TABLE rr_again_new AS SELECT * FROM public.ranking_results LIMIT 10")
        oid = _q(cur, "SELECT 'rr_again_new'::regclass::oid")
    conn.commit()
    with contextlib.redirect_stdout(open(os.devnull, "w")):
        assert B.orderedRewrite(conn, "rr_again_new", "ranking_results") is True
    with conn.cursor() as cur:
        assert _q(cur, "SELECT 'rr_again_new'::regclass::oid") == oid
        cur.execute("DROP TABLE rr_again_new")
    conn.commit()
    conn.close()


# --------------------------------------------- (c) the site's queries ---

_PAGES = None


def _boardRows(L, conn, sch):
    """Every board of the lab's list through the rankings functions, its
    count, and the seven page lookups, with search_path on `sch`."""
    import psycopg2.extras
    from urllib.parse import parse_qs
    import rankings as RK
    out = {}
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(f"SET search_path = {sch}, public")
        for label, qs in L.BOARDS:
            a = L._Args({k: (v if len(v) > 1 else v[0]) for k, v in parse_qs(qs).items()})
            f, err = RK.parseFilters(a)
            assert not err, (label, err)
            fn = {"performance": RK.getPerformanceRankings, "pr": RK.getPrRankings,
                  "ability": RK.getAbilityRankings}[f["board"]]
            out[label] = [dict(r) for r in fn(cur, f)]
            if f["board"] != "pr" or f.get("event"):
                with conn.cursor() as plain:
                    plain.execute(f"SET search_path = {sch}, public")
                    out[label + " count"] = RK.countRows(plain, f, timeout_ms=0)
        for label, sql in _pageSql(cur):
            cur.execute(sql)
            out[label] = [dict(r) for r in cur.fetchall()]
        cur.execute("RESET search_path")
    conn.rollback()
    return out


def _pageSql(cur):
    global _PAGES
    if _PAGES is None:
        cur.execute("""SELECT person_id FROM public.athlete_season WHERE pool = 'hs_m'
                       ORDER BY mean_rating DESC NULLS LAST, person_id LIMIT 1 OFFSET 7""")
        pid = cur.fetchone()["person_id"]
        cur.execute("""SELECT school FROM public.ranking_results WHERE pool = 'hs_f'
                       GROUP BY 1 ORDER BY count(*) DESC, 1 LIMIT 1""")
        school = cur.fetchone()["school"]
        cur.execute("""SELECT array_agg(result_id ORDER BY result_id) AS r FROM (
                         SELECT result_id FROM public.ranking_results WHERE sport = 'XC'
                         ORDER BY result_id LIMIT 300) q""")
        rids = cur.fetchone()["r"]
        lit = lambda v: cur.mogrify("%s", (v,)).decode()          # noqa: E731
        _PAGES = [
            ("X1", f"SELECT sport, race_date, year, distance, time_seconds, speed_rating, pool, "
                   f"meet_id, result_id FROM ranking_results WHERE person_id = {lit(pid)} "
                   f"ORDER BY race_date, result_id"),
            ("X3", f"SELECT result_id, pool, speed_rating FROM ranking_results WHERE sport = 'XC' "
                   f"AND result_id = ANY({lit(rids)}) ORDER BY result_id"),
            ("X4", f"SELECT result_id, person_id, speed_rating, year FROM ranking_results "
                   f"WHERE school = {lit(school)} AND sport = 'TF' "
                   f"ORDER BY speed_rating DESC NULLS LAST, result_id LIMIT 100"),
            ("X6", f"SELECT pool, sport, year, mean_rating, best_rating, n_races FROM athlete_season "
                   f"WHERE person_id = {lit(pid)} ORDER BY sport, year, pool"),
            ("X7", f"SELECT person_id, year, mean_rating FROM athlete_season WHERE school = {lit(school)} "
                   f"AND sport = 'XC' ORDER BY mean_rating DESC NULLS LAST, person_id"),
            # the build steps that read the tables after the swap: the seek
            # table's performance source rows, and the events precompute's
            ("seek source", "SELECT result_id, sport, speed_rating, time_seconds FROM ranking_results "
                            "WHERE (speed_rating IS NOT NULL OR time_seconds > 0) AND pool = 'hs_m' "
                            "ORDER BY speed_rating DESC NULLS LAST, result_id, sport"),
        ]
    return _PAGES


@pytest.mark.parametrize("name", ["flag_lean", "flag_ordered", "flag_both"])
def test_every_board_returns_the_same_rows_in_the_same_order(builder, built, name):
    """(c) the rankings functions themselves (not captured SQL), each board
    and count, against the build with both switches off."""
    B, L, _ = builder
    _build(builder, built, "flag_off")
    _build(builder, built, name)
    conn = L.connect(autocommit=False)
    try:
        off = _boardRows(L, conn, "lab_flag_off")
        on = _boardRows(L, conn, f"lab_{name}")
        base = _boardRows(L, conn, "public")
    finally:
        conn.close()
    assert sum(len(v) for v in off.values() if isinstance(v, list)) > 500
    for label in off:
        assert on[label] == off[label], (name, label)
        assert base[label] == off[label], ("base", label)


@pytest.mark.parametrize("name", ["flag_both"])
def test_the_seek_table_is_the_same_on_every_layout(builder, built, name):
    """build_board_seek reads ranking_results / athlete_season after the swap
    and stamps their oids; the positions it stores must not depend on the
    heap order or on partial trees."""
    import build_board_seek as S
    B, L, _ = builder
    _build(builder, built, "flag_off")
    _build(builder, built, name)
    conn = L.connect(autocommit=False)
    got = {}
    try:
        with conn.cursor() as cur:
            for sch in ("lab_flag_off", f"lab_{name}"):
                cur.execute(f"SET search_path = {sch}, public")
                rows = []
                for job in S._jobs():
                    sql, params = S.jobSql(job)
                    cur.execute(f"SELECT * FROM ({sql}) q ORDER BY 1, 2", params)
                    rows.extend(cur.fetchall())
                got[sch] = rows
                cur.execute("RESET search_path")
    finally:
        conn.rollback()
        conn.close()
    assert len(got["lab_flag_off"]) > 20
    assert got["lab_flag_off"] == got[f"lab_{name}"]


def test_the_signature_drops_the_orderings_postgres_drops():
    # 2026-11-10: e0126da's as_sort_grade_idx was stated `grade ASC NULLS
    # LAST`; pg_indexes says `grade`, so verifyIndexes refused every swap
    import rr_layout as R
    same = [("(pool, sport, grade ASC NULLS LAST, mean_rating DESC, person_id)",
             "CREATE INDEX i ON public.t USING btree (pool, sport, grade, mean_rating DESC, person_id)"),
            ("(a DESC NULLS FIRST)", "CREATE INDEX i ON t USING btree (a DESC)"),
            ("(a ASC NULLS FIRST)", "CREATE INDEX i ON t USING btree (a NULLS FIRST)"),
            ("(a DESC NULLS LAST, b)", "CREATE INDEX i ON t USING btree (a DESC NULLS LAST, b)")]
    for canon, stored in same:
        assert R.indexSig(canon) == R.indexSig(stored), canon
    # what changes an index still tells it apart
    assert R.indexSig("(a DESC)") != R.indexSig("(a)")
    assert R.indexSig("(a NULLS FIRST)") != R.indexSig("(a)")
    assert R.indexSig("(a DESC NULLS LAST)") != R.indexSig("(a DESC)")
