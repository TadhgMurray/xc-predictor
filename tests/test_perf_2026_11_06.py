"""The 2026-11-06 speed pass (docs/PERF-2026-11-06.md).

    python -m pytest -q tests/test_perf_2026_11_06.py
    XCP_TWIN_TEST_DSN="host=/tmp/pgtest port=54329 user=postgres dbname=postgres" \\
        python -m pytest -q tests/test_perf_2026_11_06.py      # + the plans

1. The ability board sorted on the season best reads an index in its own
   order (51.8 s on /api/rankings, sort=best, no year).
2. The results index carries every column the athlete page's place count
   and the race page's feed list read, so both are index-only.
3. meet_sources runs once per request (a race view asked three times).
4. An athlete page's race links carry ?r= only where it picks the feed.
5. Public responses may be served stale by the edge when the origin errors;
   the worker's board copy no longer follows the edge TTL.
6. scripts/edge_purge.py: a no-op without credentials, batches URLs.
"""
import io
import json
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


def _read(*parts):
    with io.open(os.path.join(_ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


def _canonical(table):
    src = _read("racecast", "build_ranking_results.py")
    block = src[src.index("_CANONICAL_INDEXES = {"):]
    block = block[:block.index("\n}")]
    block = block[block.index(f'"{table}": ['):]
    block = block[:block.index("\n    ],")]
    return dict(re.findall(r'\("(\w+)",\s*\n?\s*"([^"]+)"', block))


def _wanted():
    src = _read("scripts", "add_page_indexes.py")
    block = src[src.index("WANTED = ["):]
    block = block[:block.index("\n]")]
    return {name: spec for _t, _c, name, spec in
            re.findall(r'\("(\w+)",\s*"(\w+)",\s*"(\w+)",\s*\n?\s*"([^"]+)"\)', block)}


# ---------------------------------------------------------------- 1 --

def _boardOrder(qs):
    from werkzeug.datastructures import MultiDict
    from urllib.parse import parse_qsl
    import rankings
    f, err = rankings.parseFilters(MultiDict(parse_qsl(qs)))
    assert not err, err
    return rankings._orderBy(f, rankings._SORTS_ABILITY, "s.mean_rating DESC",
                             "s.person_id")


def _cols(spec):
    return [c.strip() for c in spec.strip("()").split(",")]


def test_the_season_best_order_is_an_index_order():
    """The index must list the ORDER BY's columns in its exact directions
    after the equalities, or the planner sorts the whole pool again."""
    order = _boardOrder("board=ability&pool=hs_m&sport=XC&scope=usa&scale=hs"
                        "&limit=50&offset=50&sort=best")
    keys = [k.replace("s.", "").strip() for k in order.split(",")]
    idx = _canonical("athlete_season")
    assert _cols(idx["as_pool_sport_best_idx"]) == ["pool", "sport"] + keys
    assert _cols(idx["as_pool_best_idx"]) == ["pool"] + keys


def test_the_live_table_step_builds_the_same_ones():
    want, canon = _wanted(), _canonical("athlete_season")
    for name in ("as_pool_sport_best_idx", "as_pool_best_idx"):
        assert want[name] == canon[name]


# ---------------------------------------------------------------- 2 --

def test_the_results_index_covers_the_place_count_and_the_feed_list():
    spec = _wanted()["idx_results_meet_src_cover"]
    key, _, inc = spec.partition(" INCLUDE ")
    assert _cols(key)[:2] == ["meet_id", "source"]        # meet_sources, the pin probe
    assert set(_cols(key)) == {"meet_id", "source", "div_id"}
    # every column the athlete page's place lateral reads off r2
    app_src = _read("racecast", "app.py")
    lat = app_src[app_src.index("THE RACE PAGE'S PLACE (owner, 2026-10-31"):]
    lat = lat[:lat.index(") pl ON TRUE")]
    read = set(re.findall(r"r2\.(\w+)", lat))
    from result_status import placedSql
    read |= set(re.findall(r"r2\.(\w+)", placedSql("r2")))
    assert read <= set(_cols(key)) | set(_cols(inc)), read - set(_cols(key)) - set(_cols(inc))


def test_the_results_tables_are_vacuumed_after_a_run():
    sh = _read("deploy", "run_pipeline.sh")
    assert "gentle_vacuum.py --table results --table results_tf" in sh
    assert sh.index("step 18_vacuum") < sh.index("18c_vacuum_results")


# ---------------------------------------------------------------- 3 --

class _Cur:
    def __init__(self, rows):
        self.rows, self.n, self.sql = rows, 0, []

    def execute(self, sql, params=None):
        self.n += 1
        self.sql.append((sql, params))

    def fetchall(self):
        return [dict(r) for r in self.rows]


def test_meet_sources_once_per_request():
    import flask
    import app as A
    cur = _Cur([{"source": "anet", "n": 300}, {"source": "tfrrs", "n": 80}])
    with flask.Flask(__name__).test_request_context("/race/xc/1/2"):
        a = A.meet_sources(cur, "results", 1)
        a[0]["source"] = "edited"                 # a caller's edit stays its own
        b = A.meet_sources(cur, "results", 1)
        A.meet_sources(cur, "results_tf", 1)      # another table is another key
    assert cur.n == 2
    assert b == [{"source": "anet", "n": 300}, {"source": "tfrrs", "n": 80}]
    with flask.Flask(__name__).test_request_context("/race/xc/1/2"):
        A.meet_sources(cur, "results", 1)         # a new request asks again
    assert cur.n == 3


# ---------------------------------------------------------------- 4 --

class _PinCur(_Cur):
    """Answers the index check from `indexed` and the probe from `multi`."""
    def __init__(self, indexed=("results", "results_tf")):
        super().__init__([])
        self.indexed, self.probed = set(indexed), []
        self.multi = {"results": {10}, "results_tf": set()}

    def execute(self, sql, params=None):
        super().execute(sql, params)
        self.one = None
        if "pg_index" in sql:
            self.one = {"?column?": 1} if params[0] in self.indexed else None
            return
        m = re.search(r"FROM\s+(results(?:_tf)?)\s+x", sql or "")
        if m:
            self.probed.append(m.group(1))
        self.rows = ([{"meet_id": i} for i in params[0]
                      if i in self.multi[m.group(1)]] if m else [])

    def fetchone(self):
        return self.one


@pytest.fixture(autouse=True)
def _fresh_index_check():
    import app as A
    A._FEED_PROBE_INDEX.clear()
    yield
    A._FEED_PROBE_INDEX.clear()


_RACES = [{"sport": "XC", "meet_id": 10, "source": "anet"},
          {"sport": "XC", "meet_id": 11, "source": "anet"},
          {"sport": "TF", "meet_id": 12, "source": "tfrrs"},
          {"sport": "XC", "meet_id": 13, "source": None},      # no feed: pinned
          {"sport": "XC", "meet_id": None, "source": "anet"}]


def test_feed_pins_only_for_multi_feed_meets():
    import app as A
    races = [dict(r) for r in _RACES]
    A._stampFeedPins(_PinCur(), races)
    assert [r["feed_pin"] for r in races] == [True, False, False, True, True]


def test_no_probe_without_the_index():
    """Deployed before the index is built: no heap-reading probe, every
    link keeps ?r= (the old page exactly)."""
    import app as A
    races = [dict(r) for r in _RACES]
    cur = _PinCur(indexed={"results"})              # results_tf has none
    A._stampFeedPins(cur, races)
    assert cur.probed == ["results"]
    assert [r["feed_pin"] for r in races] == [True, False, True, True, True]
    cur = _PinCur(indexed=())
    races = [dict(r) for r in _RACES]
    A._FEED_PROBE_INDEX.clear()
    A._stampFeedPins(cur, races)
    assert cur.probed == [] and all(r["feed_pin"] for r in races)


def test_a_failed_probe_keeps_every_pin():
    import app as A

    class Boom(_Cur):
        def execute(self, sql, params=None):
            if "unnest" in sql:
                raise RuntimeError("no index yet")

        class connection:                                  # noqa: N801
            @staticmethod
            def rollback():
                pass
    races = [{"sport": "XC", "meet_id": 11, "source": "anet"}]
    A._stampFeedPins(Boom([]), races)
    assert races[0]["feed_pin"] is True


def test_the_template_goes_bare_only_on_an_explicit_false():
    import jinja2
    src = _read("racecast", "templates", "athlete.html")
    xc = re.search(r'<a href="(/race/xc/[^"]+)"', src).group(1)
    t = jinja2.Environment().from_string(xc)
    base = {"meet_id": 5, "div_id": 6, "result_id": -77}
    assert t.render(race={**base, "feed_pin": False}) == "/race/xc/5/6#r-77"
    assert t.render(race={**base, "feed_pin": True}) == "/race/xc/5/6?r=-77"
    assert t.render(race=base) == "/race/xc/5/6?r=-77"          # not stamped: old link
    tf_meet = re.search(r'<a href="(/meet/\{\{[^"]+)"', src).group(1)
    t = jinja2.Environment().from_string(tf_meet)
    # a TF meet page reads ?r= for its scroll anchor: always kept
    assert "?r=" in t.render(race={**base, "sport": "TF", "feed_pin": False})
    assert "#r" in t.render(race={**base, "sport": "XC", "feed_pin": False})


@pytest.fixture
def pg():
    dsn = os.environ.get("XCP_TWIN_TEST_DSN")
    if not dsn:
        pytest.skip("set XCP_TWIN_TEST_DSN to a scratch Postgres to run the plans")
    import psycopg2
    import psycopg2.extras
    conn = psycopg2.connect(dsn)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("CREATE TEMP TABLE results (meet_id bigint, source text, div_id bigint,"
                " time_seconds real, result_id bigint, school text, status text)")
    cur.execute("""INSERT INTO results
                   SELECT g / 200, CASE WHEN g / 200 = 7 AND g % 2 = 0 THEN 'tfrrs'
                                        ELSE 'anet' END,
                          (g / 50) % 4, 900 + g % 300, g, 'S' || g % 40, NULL
                   FROM generate_series(1, 200000) g""")
    yield conn, cur
    conn.rollback()
    conn.close()


def test_the_pin_probe_on_postgres(pg):
    import app as A
    _conn, cur = pg
    got = A._multiFeedMeets(cur, "results", [(7, "anet"), (7, "tfrrs"), (8, "anet"),
                                             (9, "zzz"), (None, "anet"), (10, None)])
    assert got == {7, 9}


def test_the_index_makes_both_reads_index_only(pg):
    _conn, cur = pg
    for idx, _spec in [("cover", _wanted()["idx_results_meet_src_cover"])]:
        cur.execute(f"CREATE INDEX {idx} ON results {_spec}")
    cur.execute("ANALYZE results")
    cur.execute("SET enable_seqscan = off; SET enable_bitmapscan = off")

    def plan(sql):
        cur.execute("EXPLAIN " + sql)
        return "\n".join(r["QUERY PLAN"] for r in cur.fetchall())
    assert "Index Only Scan" in plan(
        "SELECT source, count(*) FROM results WHERE meet_id = 7 AND source IS NOT NULL"
        " GROUP BY source")
    assert "Index Only Scan" in plan(
        "SELECT count(*) FILTER (WHERE r2.time_seconds < 950 AND r2.status IS NULL"
        " AND r2.result_id > 3 AND r2.school IS NOT NULL) FROM results r2"
        " WHERE r2.meet_id = 7 AND r2.div_id = 1 AND r2.source = 'anet'")


# ---------------------------------------------------------------- 5 --

def _headers_for(path, status=200):
    import flask as f
    import app as A
    with f.Flask(__name__).test_request_context(path):
        return A._headers(f.Response("x", status=status)).headers


def test_public_pages_may_be_served_stale_on_error():
    cc = _headers_for("/athlete/21548899")["Cache-Control"]
    assert "public" in cc and "stale-if-error=" in cc
    # never on an error, never on a private path
    assert "stale" not in _headers_for("/athlete/1", status=503)["Cache-Control"]
    assert "stale" not in _headers_for("/account/x")["Cache-Control"]


def test_the_board_api_and_the_redirect_use_the_same_header():
    import app as A
    assert A._BOARD_CACHE_CONTROL == A.PUBLIC_CACHE_CONTROL
    import flask
    with flask.Flask(__name__).test_request_context("/race/xc/1/2?r=5&school=X"):
        resp = A._dropUselessPin(5)
    assert resp.headers["Cache-Control"] == A.PUBLIC_CACHE_CONTROL
    assert resp.headers["Location"].endswith("/race/xc/1/2?school=X#r5")


def test_the_worker_board_copy_does_not_follow_the_edge_ttl():
    src = _read("racecast", "app.py")
    assert 'os.environ.get("XCP_BOARD_API_TTL", "900")' in src


# ---------------------------------------------------------------- 6 --

def test_edge_purge_is_a_noop_without_credentials(capsys):
    import edge_purge as E

    def never(*a, **k):
        raise AssertionError("no request without credentials")
    assert E.purge(env={}, opener=never) is None
    assert "skipped" in capsys.readouterr().out


def test_edge_purge_sends_everything_or_batches():
    import edge_purge as E
    assert E.bodies([]) == [{"purge_everything": True}]
    b = E.bodies([f"https://racecast.co/athlete/{i}" for i in range(65)])
    assert [len(x["files"]) for x in b] == [30, 30, 5]
    sent = []

    class R:
        def __init__(self, body):
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"success": True}).encode()

    def opener(req, timeout=None):
        sent.append((req.full_url, req.get_header("Authorization"),
                     req.get_header("User-agent"), json.loads(req.data)))
        return R(req.data)
    env = {"XCP_CF_ZONE_ID": "z1", "XCP_CF_API_TOKEN": "t1"}
    assert E.purge(env=env, opener=opener) is True
    url, auth, ua, body = sent[0]
    assert url.endswith("/zones/z1/purge_cache") and auth == "Bearer t1"
    assert body == {"purge_everything": True}
    assert "racecast@racecast.co" in ua


def test_the_pipeline_purges_after_the_swap_and_at_the_end():
    sh = _read("deploy", "run_pipeline.sh")
    assert sh.index("step 10_rankings_finish") < sh.index("step 10_edge_purge")
    assert sh.rindex("\nbgwait\n") < sh.index("step 19_edge_purge") < sh.rindex("\nsummarise")
    assert "edge_purge.py --delay 960" in sh
    assert "step 14_edge_purge" in _read("deploy", "nightly_update.sh")


# ---------------------------------------------------------------- 7 --
# The PR board's candidates, one stored distance at a time (12.6-16.9 s live
# on board=pr&sport=XC&pool=hs_m&distance=5000).

def _prSql(qs):
    from werkzeug.datastructures import MultiDict
    from urllib.parse import parse_qsl
    import rankings

    class C:
        def execute(self, sql, params=None):
            self.sql, self.params = sql, params

        def fetchall(self):
            return []
    rankings._hasEventKind = lambda cur=None: True
    rankings._rowHasUnit = lambda c, t="ranking_results": False
    f, err = rankings.parseFilters(MultiDict(parse_qsl(qs)))
    assert not err, err
    c = C()
    rankings.getPrRankings(c, f)
    return c.sql, c.params


def test_the_pr_board_reads_one_distance_at_a_time():
    sql, _ = _prSql("board=pr&sport=XC&pool=hs_m&distance=5000&limit=50")
    flat = " ".join(sql.split())
    assert "dists(p, d) AS" in flat and "distance = dists.d" in flat
    assert "pools(p) AS" not in flat                      # one pool: no pool walk
    allp, _ = _prSql("board=pr&sport=XC&pool=all&distance=5000&limit=50")
    flat = " ".join(allp.split())
    assert "pools(p) AS" in flat and "pool IS NULL" in flat


def test_on_a_field_event_it_keeps_the_plain_query():
    import rankings
    assert rankings._prPerDistance({"distance": 5000, "pool": "all"}, False)
    assert not rankings._prPerDistance({"distance": 5000, "pool": "hs_m"}, True)
    assert not rankings._prPerDistance({"distance": None, "pool": "hs_m"}, False)


def test_the_pr_board_answers_the_same_and_uses_the_index(pg):
    import rankings
    _conn, cur = pg
    cur.execute("""CREATE TEMP TABLE ranking_results AS
        SELECT CASE WHEN g % 3 = 0 THEN 'hs_f' ELSE 'hs_m' END AS pool,
               CASE WHEN g % 4 = 0 THEN 'TF' ELSE 'XC' END AS sport,
               (ARRAY[5000, 4999.9, 5001, 8000])[1 + g % 4]::real AS distance,
               (900 + (g * 7919) % 900)::real AS time_seconds,
               g::bigint AS result_id, (g % 5000)::bigint AS person_id,
               (150 + g % 40)::real AS speed_rating, date '2024-10-01' AS race_date,
               2024 AS year, 'WI'::text AS state, 'S'::text AS school, '11'::text AS grade,
               (g / 300)::bigint AS meet_id, 1::bigint AS div_id, NULL::bigint AS canon_meet_id,
               NULL::bigint AS event_id, NULL::text AS event_kind, NULL::real AS mark
        FROM generate_series(1, 60000) g""")
    cur.execute("CREATE INDEX ON ranking_results (pool, sport, distance, time_seconds)")
    cur.execute("CREATE TEMP TABLE athletes (athlete_id bigint, person_id bigint,"
                " first_name text, last_name text, gender text)")
    cur.execute("INSERT INTO athletes SELECT g, g, 'A', 'B' || g, 'M'"
                " FROM generate_series(0, 4999) g")
    cur.execute("CREATE TEMP TABLE results_tf (person_id bigint, athlete_name text)")
    cur.execute("ALTER TABLE results ADD COLUMN person_id bigint, ADD COLUMN athlete_name text")
    # a row with no pool: pool=all must still find it (its own branch)
    cur.execute("""INSERT INTO ranking_results (pool, sport, distance, time_seconds, result_id,
                       person_id, speed_rating, race_date, year, state, school, grade, meet_id, div_id)
                   VALUES (NULL, 'XC', 5000, 850, 999999, 4999, 160, '2024-10-01', 2024,
                           'WI', 'S', '11', 1, 1)""")
    cur.execute("ANALYZE ranking_results")
    for qs in ("board=pr&sport=XC&pool=hs_m&distance=5000&limit=50",
               "board=pr&sport=XC&pool=hs_m&distance=5000&limit=50&offset=200",
               "board=pr&sport=both&pool=hs_f&distance=5000&limit=50",
               "board=pr&sport=XC&pool=all&distance=5000&limit=50",
               "board=pr&sport=both&pool=all&distance=5000&limit=50&offset=100",
               "board=pr&sport=XC&pool=all&gender=m&distance=5000&limit=50"):
        new_sql, params = _prSql(qs)
        cur.execute(new_sql, params)
        new = [dict(r) for r in cur.fetchall()]
        old = rankings._prPerDistance
        rankings._prPerDistance = lambda f, is_field: False
        try:
            old_sql, params = _prSql(qs)
        finally:
            rankings._prPerDistance = old
        cur.execute(old_sql, params)
        assert new == [dict(r) for r in cur.fetchall()], qs
        assert len(new) == 50
        if "pool=all&distance" in qs and "offset" not in qs:
            assert new[0]["result_id"] == 999999           # the row with no pool
    sql, params = _prSql("board=pr&sport=XC&pool=hs_m&distance=5000&limit=50")
    cur.execute("SET enable_seqscan = off")
    cur.execute("EXPLAIN " + sql, params)
    plan = "\n".join(r["QUERY PLAN"] for r in cur.fetchall())
    assert "distance = dists.d" in plan and "Seq Scan on ranking_results" not in plan


# ---------------------------------------------------------------- 8 --

def test_the_web_process_connects_without_jit():
    """~1 s of JIT compile per board query (25 ms off, 1,155 ms on, the PR
    board's per-distance plan): the site's connections carry jit=off, the
    service unit's timeouts stay beside it, and XCP_DB_JIT=on still wins."""
    import subprocess
    code = ("import sys; sys.path[:0] = ['racecast', 'scripts', 'tests']; import _env; "
            "import app, database; print(database._PG_CONFIG_WITH_TIMEOUT.get('options'))")

    def opts(**env):
        e = {k: v for k, v in os.environ.items() if k != "XCP_DB_JIT"}
        e.update(env, XCP_DB_QUIET="1")
        out = subprocess.run([sys.executable, "-W", "ignore", "-c", code], cwd=_ROOT,
                             env=e, capture_output=True, text=True, timeout=300)
        return out.stdout.strip().splitlines()[-1]
    assert opts(XCP_DB_STATEMENT_TIMEOUT_MS="55000") == "-c statement_timeout=55000 -c jit=off"
    assert opts(XCP_DB_JIT="on") == "-c jit=on"


# ---------------------------------------------------------------- 9 --
# sharedcache: what it takes computed once for the box, not once per worker

def test_shared_cache_computes_once_and_shares(tmp_path):
    import sharedcache as SC
    d = str(tmp_path / "c")
    calls = []

    def compute():
        calls.append(1)
        return {"genders": {"boys": (1, 2)}, "n": len(calls)}
    clock = [1000.0]
    v1, at1 = SC.get("wit-WI", compute, ttl=60, now=lambda: clock[0], path=d)
    clock[0] += 30
    v2, at2 = SC.get("wit-WI", compute, ttl=60, now=lambda: clock[0], path=d)
    assert v1 == v2 == {"genders": {"boys": (1, 2)}, "n": 1} and len(calls) == 1
    assert at1 == at2 == 1000.0                     # when it was computed
    clock[0] += 31                                  # past the ttl: computed again
    v3, _ = SC.get("wit-WI", compute, ttl=60, now=lambda: clock[0], path=d)
    assert v3["n"] == 2


def test_shared_cache_does_not_share_a_failure(tmp_path):
    import sharedcache as SC
    d = str(tmp_path / "c")
    n = []

    def compute():
        n.append(1)
        return {"error": "UndefinedTable"}
    for _ in range(2):
        SC.get("wit-CA", compute, ttl=600, keep=lambda v: not v.get("error"), path=d)
    assert len(n) == 2


def test_shared_cache_refuses_a_directory_others_can_write(tmp_path):
    import sharedcache as SC
    d = tmp_path / "open"
    d.mkdir()
    os.chmod(d, 0o777)
    n = []
    for _ in range(2):
        SC.get("k", lambda: n.append(1) or {"v": 1}, ttl=600, path=str(d))
    assert len(n) == 2 and not list(d.glob("*.pkl"))


def test_shared_cache_one_compute_across_processes(tmp_path):
    """Two workers asking at once: one computes, the other waits on the lock
    and reads the file."""
    import subprocess
    d, log = str(tmp_path / "c"), tmp_path / "calls.txt"
    code = (f"import sys, time; sys.path.insert(0, 'racecast'); import sharedcache as S\n"
            f"def c():\n    open({str(log)!r}, 'a').write('x'); time.sleep(1.0); return {{'v': 7}}\n"
            f"print(S.get('wit-NY', c, ttl=600, path={d!r})[0]['v'])")
    ps = [subprocess.Popen([sys.executable, "-c", code], cwd=_ROOT, stdout=subprocess.PIPE,
                           text=True) for _ in range(3)]
    outs = [p.communicate(timeout=60)[0].strip() for p in ps]
    assert outs == ["7", "7", "7"] and log.read_text() == "x"


def test_marks_for_reads_the_shared_copy_and_keeps_its_time(tmp_path, monkeypatch):
    import cuts
    import sharedcache
    import ttlcache
    monkeypatch.setattr(sharedcache, "DIR", str(tmp_path / "c"))
    n = []

    def fake(cur, state):
        n.append(state)
        return {"state": state, "genders": {}}
    monkeypatch.setattr(cuts, "_computeUncached", fake)
    ttlcache._store.pop(("wit", "ZZ"), None)
    a = cuts.marksFor(None, "ZZ")
    ttlcache._store.pop(("wit", "ZZ"), None)        # another worker: no memory copy
    b = cuts.marksFor(None, "ZZ")
    ttlcache._store.pop(("wit", "ZZ"), None)
    assert n == ["ZZ"]
    assert a["computed_at"] == b["computed_at"] and "_shared_at" not in a
    assert {k: v for k, v in a.items() if k != "computed_at"} == {"state": "ZZ", "genders": {}}


# --------------------------------------------------------------- 10 --
# The PR board tries a small candidate set first, on the default order only

class _CandCur:
    """Answers each run with `rows_for(cand)` rows; records the cand asked."""
    def __init__(self, rows_for):
        self.rows_for, self.cands = rows_for, []

    def execute(self, sql, params=None):
        self.cands.append(params["cand"])

    def fetchall(self):
        return [{"i": i} for i in range(self.rows_for(self.cands[-1]))]


def _prFilters(qs):
    from werkzeug.datastructures import MultiDict
    from urllib.parse import parse_qsl
    import rankings
    rankings._hasEventKind = lambda cur=None: True
    rankings._rowHasUnit = lambda c, t="ranking_results": False
    f, err = rankings.parseFilters(MultiDict(parse_qsl(qs)))
    assert not err, err
    return f


def test_pr_small_first_pass_and_fallback():
    import rankings as R
    f = _prFilters("board=pr&sport=XC&pool=hs_m&distance=5000&limit=50&offset=50")
    full, small = 100 * R.CANDIDATE_FACTOR, 100 * R.PR_FIRST_FACTOR
    cur = _CandCur(lambda cand: 50)                       # the small set fills the page
    assert len(R.getPrRankings(cur, f)) == 50 and cur.cands == [small]
    cur = _CandCur(lambda cand: 50 if cand == full else 12)   # it does not: the full set
    assert len(R.getPrRankings(cur, f)) == 50 and cur.cands == [small, full]
    for other in ("&sort=school", "&dir=DESC", "&sort=date"):
        cur = _CandCur(lambda cand: 50)                   # other orders: the full set only
        R.getPrRankings(cur, _prFilters("board=pr&sport=XC&pool=hs_m&distance=5000&limit=50"
                                        + other))
        assert cur.cands == [50 * R.CANDIDATE_FACTOR], other


def test_pr_small_first_pass_answers_the_same_on_postgres(pg):
    """Same rows as the full set, including a pool of many-race athletes
    where the small set cannot fill the page and the fallback runs."""
    import rankings as R
    _conn, cur = pg
    cur.execute("""CREATE TEMP TABLE ranking_results AS
        SELECT CASE WHEN g % 3 = 0 THEN 'hs_f' ELSE 'hs_m' END AS pool, 'XC'::text AS sport,
               (ARRAY[5000, 4999.9, 5001])[1 + g % 3]::real AS distance,
               (900 + (g::bigint * 7919) % 60000 / 50.0)::real AS time_seconds,
               g::bigint AS result_id,
               CASE WHEN g % 3 = 0 THEN (g % 40)::bigint ELSE (g % 7000)::bigint END AS person_id,
               (150 + g % 40)::real AS speed_rating, date '2024-10-01' AS race_date,
               2024 AS year, 'WI'::text AS state, 'S'::text AS school, '11'::text AS grade,
               (g / 300)::bigint AS meet_id, 1::bigint AS div_id, NULL::bigint AS canon_meet_id,
               NULL::bigint AS event_id, NULL::text AS event_kind, NULL::real AS mark
        FROM generate_series(1, 60000) g""")
    cur.execute("CREATE INDEX ON ranking_results (pool, sport, distance, time_seconds)")
    cur.execute("CREATE TEMP TABLE athletes (athlete_id bigint, person_id bigint,"
                " first_name text, last_name text, gender text)")
    cur.execute("INSERT INTO athletes SELECT g, g, 'A', 'B' || g, 'M' FROM generate_series(0, 6999) g")
    cur.execute("CREATE TEMP TABLE results_tf (person_id bigint, athlete_name text)")
    cur.execute("ALTER TABLE results ADD COLUMN person_id bigint, ADD COLUMN athlete_name text")
    cur.execute("ANALYZE ranking_results")
    before = R.PR_FIRST_FACTOR
    try:
        for qs in ("board=pr&sport=XC&pool=hs_m&distance=5000&limit=50",
                   "board=pr&sport=XC&pool=hs_m&distance=5000&limit=50&offset=300",
                   "board=pr&sport=XC&pool=hs_f&distance=5000&limit=50"):   # 40 athletes
            f = _prFilters(qs)
            R.PR_FIRST_FACTOR = 4
            small = [dict(r) for r in R.getPrRankings(cur, dict(f))]
            R.PR_FIRST_FACTOR = R.CANDIDATE_FACTOR
            full = [dict(r) for r in R.getPrRankings(cur, dict(f))]
            assert small == full, qs
    finally:
        R.PR_FIRST_FACTOR = before


# --------------------------------------------------------------- 11 --
# season_comps stored in the order the comps search reads it; its min()
# computed once for the box

def test_season_comps_is_written_in_the_search_order():
    import build_comps
    sql = " ".join(build_comps._BUILD.split()) if hasattr(build_comps, "_BUILD") else \
        " ".join(_read("racecast", "build_comps.py").split())
    assert "ORDER BY s.sport, s.pool, s.g, s.rating" in sql
    # the order matches the index the search reads through
    assert "(sport, pool, grade, rating)" in _read("racecast", "build_comps.py")


def test_comps_min_is_shared_across_workers(tmp_path, monkeypatch):
    import comps
    import sharedcache
    monkeypatch.setattr(sharedcache, "DIR", str(tmp_path / "c"))

    class C:
        n = 0

        def execute(self, sql, params=None):
            C.n += 1

        def fetchone(self):
            return {"n": 3}
    comps._MIN_CACHE.clear()
    assert comps._minRaces(C(), "XC", "hs_m") == 3
    comps._MIN_CACHE.clear()                       # another worker: no memory copy
    assert comps._minRaces(C(), "XC", "hs_m") == 3
    assert C.n == 1
    comps._MIN_CACHE.clear()


# --------------------------------------------------------------- 12 --
# The events-filtered ability board: the season median by windows, not by a
# CTE self-join the planner turned into a nested loop (26-55 s live).

def test_the_events_source_has_no_self_join():
    import rankings as R
    R._rowHasUnit = lambda c, t="ranking_results": False
    params = {}
    src = R._abilitySource({"dist_min": 3000, "dist_max": None}, params, " AND pool = %(pool)s")
    assert "JOIN" not in src.upper()
    assert "OVER (PARTITION BY person_id, pool, sport, year" in " ".join(src.split())
    assert "r.*" not in src


def test_the_window_median_is_percentile_cont_exactly(pg):
    """Group by group, the mean of the middle one or two (the rewrite) and
    percentile_cont(0.5) (the old med CTE) are the same double."""
    _conn, cur = pg
    cur.execute("""CREATE TEMP TABLE m AS
        SELECT (g % 997)::bigint AS k,
               (100 + ((g::bigint * 7919) % 100003) / 997.0)::real AS v
        FROM generate_series(1, 40000) g""")
    cur.execute("""
        WITH r AS (
            SELECT k, v, row_number() OVER (PARTITION BY k ORDER BY v) AS rn,
                   count(*) OVER (PARTITION BY k) AS n FROM m),
        w AS (
            SELECT DISTINCT k,
                   (max(v::float8) FILTER (WHERE rn = (n + 1) / 2) OVER (PARTITION BY k)
                    + max(v::float8) FILTER (WHERE rn = (n + 2) / 2) OVER (PARTITION BY k)) / 2
                       AS med FROM r),
        p AS (SELECT k, percentile_cont(0.5) WITHIN GROUP (ORDER BY v) AS med FROM m GROUP BY k)
        SELECT count(*) AS groups,
               count(*) FILTER (WHERE w.med IS DISTINCT FROM p.med) AS differ,
               count(*) FILTER (WHERE (SELECT count(*) FROM m WHERE m.k = p.k) % 2 = 0) AS even
        FROM p JOIN w USING (k)""")
    row = cur.fetchone()
    assert row["groups"] > 500 and row["even"] > 100
    assert row["differ"] == 0


# --------------------------------------------------------------- 13 --
# The Events presets, precomputed (athlete_season_ev)

def test_ev_preset_only_where_the_races_are_the_same():
    import rankings as R
    base = {"dist_min": 3000.0, "dist_max": None, "scope": "usa"}
    assert R._evPreset(base) == 3000
    assert R._evPreset(dict(base, year=[2026], pool="hs_m", sport="XC", gender="m")) == 3000
    for extra in ({"dist_min": 2000.0}, {"dist_max": 9000.0}, {"scope": "all"},
                  {"state": ["CA"]}, {"school": ["Calvin"]}, {"grade": ["11"]},
                  {"conference": ["HCC"]}):
        assert R._evPreset(dict(base, **extra)) is None, extra


def test_ability_source_reads_the_table_only_when_asked_and_fresh():
    import rankings as R
    R._rowHasUnit = lambda c, t="ranking_results": False
    f = {"dist_min": 3000.0, "dist_max": None, "scope": "usa"}
    R._EV_FRESH.update(at=1e18, ok=True)
    try:
        p = {}
        assert "athlete_season_ev" in R._abilitySource(f, p, "", precomputed=True)
        assert p["ev_dist"] == 3000
        assert "athlete_season_ev" not in R._abilitySource(f, {}, "")    # the team board
        R._EV_FRESH.update(ok=False)                                  # built from another rr
        assert "athlete_season_ev" not in R._abilitySource(f, {}, "", precomputed=True)
    finally:
        R._EV_FRESH.update(at=0.0, ok=False)
    src = _read("racecast", "rankings.py")
    assert src.count("_abilitySource(f, params, where, precomputed=True)") == 4
    assert "precomputed" not in _read("racecast", "teams.py")


def test_the_events_table_is_built_after_the_boards_go_live():
    sh = _read("deploy", "run_pipeline.sh")
    assert sh.index("step 10_rankings_finish") < sh.index("bgstep 10f4_season_events") \
        < sh.rindex("\nbgwait\n")
    nu = _read("deploy", "nightly_update.sh")
    assert nu.index("step 10_rankings_finish") < nu.index("step 10f4_season_events")
    import build_season_events as B
    assert tuple(B.R.EV_PRESETS) == (800, 1500, 3000, 5000)
    html = _read("racecast", "templates", "rankings.html")
    for d in (800, 1500, 3000, 5000):
        assert f'<option value="{d}">' in html


def test_events_boards_from_the_table_equal_the_live_ones(pg):
    import rankings as R
    import build_season_events as B
    from werkzeug.datastructures import MultiDict
    from urllib.parse import parse_qsl
    R._rowHasUnit = lambda c, t="ranking_results": False
    _conn, cur = pg
    cur.execute("""CREATE TEMP TABLE ranking_results AS
        SELECT CASE WHEN g % 2 = 0 THEN 'hs_m' ELSE 'ms_m' END AS pool, 'XC'::text AS sport,
               (ARRAY[3000, 4000, 5000, 1600])[1 + g % 4]::real AS distance,
               (900 + g % 600)::real AS time_seconds, g::bigint AS result_id,
               (g % 700)::bigint AS person_id,
               (CASE WHEN g % 17 = 0 THEN 100 + g % 7
                     ELSE 140 + ((g::bigint * 7919) % 400) / 10.0 END)::real AS speed_rating,
               date '2024-09-01' + (g % 60) AS race_date, 2023 + (g / 7) % 2 AS year,
               (ARRAY['WI','CA','TX','ON'])[1 + (g / 5) % 4] AS state,
               'School ' || (g / 11) % 30 AS school, (9 + g % 4)::text AS grade
        FROM generate_series(1, 20000) g""")
    cur.execute("CREATE TEMP TABLE athletes (athlete_id bigint, person_id bigint,"
                " first_name text, last_name text, gender text)")
    cur.execute("INSERT INTO athletes SELECT g, g, 'A', 'B' || g, 'M' FROM generate_series(0, 699) g")
    cur.execute("CREATE TEMP TABLE results_tf (person_id bigint, athlete_name text)")
    cur.execute("ALTER TABLE results ADD COLUMN person_id bigint, ADD COLUMN athlete_name text")
    sql, params = B.presetSql(800)
    cur.execute(f"CREATE TEMP TABLE athlete_season_ev AS {sql} WITH NO DATA", params)
    for d in R.EV_PRESETS:
        for k in range(3):
            sql, params = B.presetSql(d, k, 3)
            cur.execute(f"INSERT INTO athlete_season_ev {sql}", params)
    tcur = cur.connection.cursor()
    try:
        for b in ("pool=hs_m&sport=XC&dist_min=3000",
                  "pool=hs_m&sport=XC&dist_min=3000&year=2024&sort=best",
                  "pool=ms_m&sport=XC&dist_min=800&scale=hs&offset=50",
                  "pool=hs_m&sport=XC&dist_min=1500&min_races=1&sort=races"):
            f, err = R.parseFilters(MultiDict(parse_qsl("board=ability&limit=50&" + b)))
            assert not err, err
            got = {}
            for mode in (True, False):
                R._EV_FRESH.update(at=1e18, ok=mode)
                rows = [dict(r) for r in R.getAbilityRankings(cur, dict(f))]
                got[mode] = (rows, R.countOf(tcur, dict(f)),
                             R.rankOf(tcur, dict(f), rows[3]["person_id"]))
            assert got[True] == got[False], b
            assert len(got[True][0]) == 50
    finally:
        R._EV_FRESH.update(at=0.0, ok=False)


# --------------------------------------------------------------- 13b --
# One grade, precomputed (athlete_season_ev_grade, owner 2026-11-08)

def test_ev_grade_preset_only_for_one_grade_key():
    import rankings as R
    base = {"dist_min": 1500.0, "dist_max": None, "scope": "usa"}
    assert R._evGradePreset(base) is None                      # no grade: the plain table
    assert R._evGradePreset(dict(base, grade=["10"])) == (1500, "10")
    assert R._evGradePreset(dict(base, grade=["10", "10th"])) == (1500, "10")   # one key
    assert R._evGradePreset(dict(base, grade=["Senior"], year=[2026], pool="hs_m",
                                 sport="XC", gender="m")) == (1500, "sr")
    assert R._evGradePreset(dict(base, grade=["10", "11"])) is None   # a union: live
    for extra in ({"dist_min": 2000.0}, {"dist_max": 9000.0}, {"scope": "all"},
                  {"state": ["CA"]}, {"school": ["Calvin"]}, {"conference": ["HCC"]}):
        assert R._evGradePreset(dict(base, grade=["10"], **extra)) is None, extra
    assert R._evPreset(dict(base, grade=["10"])) is None       # never the all-grades table


def test_ability_source_reads_the_grade_table_only_when_asked_and_fresh():
    import rankings as R
    R._rowHasUnit = lambda c, t="ranking_results": False
    f = {"dist_min": 3000.0, "dist_max": None, "scope": "usa", "grade": ["11th"]}
    R._EV_FRESH.update(at=1e18, ok=True)
    R._EV_GRADE_FRESH.update(at=1e18, ok=True)
    try:
        p = {}
        src = R._abilitySource(f, p, "", precomputed=True)
        assert "athlete_season_ev_grade" in src and (p["ev_dist"], p["ev_gkey"]) == (3000, "11")
        assert "athlete_season_ev" not in R._abilitySource(f, {}, "")         # the team board
        assert "athlete_season_ev" not in R._abilitySource(
            dict(f, grade=["9", "10"]), {}, "", precomputed=True)            # two grades
        R._EV_GRADE_FRESH.update(ok=False)                             # built from another rr
        assert "athlete_season_ev" not in R._abilitySource(f, {}, "", precomputed=True)
    finally:
        R._EV_FRESH.update(at=0.0, ok=False)
        R._EV_GRADE_FRESH.update(at=0.0, ok=False)
    # the owner's board (2026-11-08, 3-22 s live) walks an index in its own
    # order after the equalities, with and without a year
    import build_season_events as B
    ix = {n: _cols(c) for n, c in B.GRADE_INDEXES}
    for yr, name in (("", "mean"), ("&year=2026", "year_mean")):
        order = _boardOrder("board=ability&pool=hs_m&sport=XC&scope=usa&scale=hs&limit=50"
                            f"&offset=0&dist_min=1500&grade=10&sort=rating{yr}")
        keys = [k.replace("s.", "").strip() for k in order.split(",")]
        assert ix[name] == ["dist_min", "gkey", "pool", "sport"] + (["year"] if yr else []) + keys
    assert ix["person"] == ["person_id"]


# hs_m, college_m and ms_m athletes (person % 3), 70 races each (r) over two
# seasons. Each season's ratings sit in their own band (2025: 70-153, 2026:
# 170-253), so one athlete's two seasons never tie on (rating, person) and
# the boards' order is total; inside a band ratings are whole numbers, so
# athletes tie on rating and the person_id tiebreak decides pages. Grades:
# every spelling the key folds (10, 10th, Sr, a class year, Fr/So/Jr/Sr,
# 13-16 in college), mixed within a season, NULL on one race in six, and
# every seventh athlete moves up a grade halfway through the season (two
# keys in one season) and runs 35 points faster after it -- so the
# season's median, and the outlier cut, differ from each grade's own.
_EVG_FIXTURE = """
    CREATE TEMP TABLE ranking_results AS
    WITH b AS (
        SELECT g, g % 900 AS person, g / 900 AS r, 2025 + (g / 900) % 2 AS year,
               CASE (g % 900) % 3 WHEN 0 THEN 'hs_m' WHEN 1 THEN 'college_m' ELSE 'ms_m' END AS pool,
               (g / 900) AS day
        FROM generate_series(0, 62999) g),
    c AS (
        SELECT b.*, ((person / 3) + year) % 4 + moved AS step, moved, (r + person / 3) % 6 AS sp
        FROM b, LATERAL (SELECT CASE WHEN person % 7 = 0 AND day >= 35 THEN 1 ELSE 0 END
                         AS moved) m)
    SELECT pool, 'XC'::text AS sport,
           CASE WHEN (r * 7 + person) % 53 = 0 THEN NULL
                ELSE (ARRAY[800, 1600, 3000, 5000, 1500])[1 + (r + person) % 5] END::real
               AS distance,
           (900 + g % 600)::real AS time_seconds, g::bigint AS result_id,
           person::bigint AS person_id,
           ((CASE WHEN year = 2025 THEN 100 ELSE 200 END)
            + CASE WHEN (r + person) % 17 = 0 THEN -30
                   ELSE ((g::bigint * 7919) % 19) + 35 * moved END)::real AS speed_rating,
           make_date(year, 9, 1) + day AS race_date, year,
           CASE WHEN (r + person) % 41 = 0 THEN 'ON'
                ELSE (ARRAY['WI','CA','TX'])[1 + person % 3] END AS state,
           'School ' || person % 30 AS school,
           CASE WHEN sp = 0 THEN NULL
                WHEN pool = 'hs_m' THEN
                    CASE WHEN sp = 1 THEN (9 + step) || 'th'
                         WHEN sp = 2 AND 9 + step = 12 THEN 'Sr'
                         WHEN sp = 3 AND 9 + step = 12 THEN (year + 1)::text
                         ELSE (9 + step)::text END
                WHEN pool = 'college_m' THEN
                    CASE WHEN sp = 1 THEN (13 + step)::text
                         ELSE (ARRAY['Fr','So','Jr','Sr','Sr'])[1 + step] END
                ELSE (6 + step % 3)::text END AS grade
    FROM c"""


def test_events_grade_boards_from_the_table_equal_the_live_ones(pg):
    import json
    import rankings as R
    import build_season_events as B
    from werkzeug.datastructures import MultiDict
    from urllib.parse import parse_qsl
    R._rowHasUnit = lambda c, t="ranking_results": False
    _conn, cur = pg
    cur.execute(_EVG_FIXTURE)
    cur.execute("CREATE TEMP TABLE athletes (athlete_id bigint, person_id bigint,"
                " first_name text, last_name text, gender text)")
    cur.execute("INSERT INTO athletes SELECT g, g, 'A', 'B' || g, 'M' FROM generate_series(0, 899) g")
    cur.execute("CREATE TEMP TABLE results_tf (person_id bigint, athlete_name text)")
    cur.execute("ALTER TABLE results ADD COLUMN person_id bigint, ADD COLUMN athlete_name text")
    # the build's own shape: an empty shadow, (preset, shard) INSERTs, indexes
    sql, params = B.presetSql(800, by_grade=True)
    cur.execute(f"CREATE TEMP TABLE athlete_season_ev_grade AS {sql} WITH NO DATA", params)
    for d in R.EV_PRESETS:
        for k in range(3):
            sql, params = B.presetSql(d, k, 3, by_grade=True)
            cur.execute(f"INSERT INTO athlete_season_ev_grade {sql}", params)
    for name, cols in B.GRADE_INDEXES:
        cur.execute(f"CREATE INDEX evg_{name} ON athlete_season_ev_grade {cols}")
    cur.execute("ANALYZE athlete_season_ev_grade")

    # the fixture holds what the test is about
    cur.execute("""SELECT count(*) AS n FROM (
                       SELECT 1 FROM athlete_season_ev_grade WHERE dist_min = 800
                       GROUP BY person_id, pool, sport, year HAVING count(*) > 1) q""")
    assert cur.fetchone()["n"] > 20                            # mid-season grade changes
    cur.execute("SELECT count(*) AS n FROM ranking_results WHERE grade IS NULL")
    assert cur.fetchone()["n"] > 1000
    cur.execute("SELECT count(*) AS n FROM athlete_season_ev_grade WHERE gkey IS NULL")
    assert cur.fetchone()["n"] == 0

    tcur = cur.connection.cursor()
    boards = []
    for pool, grades in (("hs_m", ("9", "10", "10th", "11", "12", "Sr", "senior", "13", "zz")),
                         ("college_m", ("fr", "So", "16", "senior", "12")),
                         ("ms_m", ("7", "8"))):
        for d in R.EV_PRESETS:
            for g in grades:
                for yr in ("", "&year=2026"):
                    for off in (0, 20):
                        boards.append(f"pool={pool}&sport=XC&scope=usa&scale=hs&dist_min={d}"
                                      f"&grade={g}{yr}&sort=rating&limit=20&offset={off}")
    boards += ["pool=hs_m&sport=XC&dist_min=1500&grade=10&sort=best&limit=20&offset=20",
               "pool=hs_m&sport=XC&dist_min=1500&grade=10&min_races=1&sort=races&limit=20",
               "pool=hs_m&sport=both&dist_min=3000&grade=11&year=2026&limit=20&offset=20",
               "pool=hs_m&sport=XC&dist_min=800&grade=12&year=2025&limit=20&offset=20",
               "pool=college_m&sport=XC&dist_min=5000&grade=jr&sort=name&limit=20&offset=20",
               "pool=hs_m&sport=XC&dist_min=800&grade=10&min_races=2&limit=20&offset=40"]
    full = ties = 0
    try:
        for b in boards:
            f, err = R.parseFilters(MultiDict(parse_qsl("board=ability&" + b)))
            assert not err, err
            got = {}
            for mode in (True, False):
                R._EV_GRADE_FRESH.update(at=1e18, ok=mode)
                p = {}
                src = R._abilitySource(f, p, R._whereClauses(f, p, False), precomputed=True)
                assert ("athlete_season_ev_grade" in src) is mode, b
                rows = [dict(r) for r in R.getAbilityRankings(cur, dict(f))]
                pid = rows[len(rows) // 2]["person_id"] if rows else 1
                got[mode] = (json.dumps(rows, default=str, sort_keys=True),
                             R.countOf(tcur, dict(f)), R.rankOf(tcur, dict(f), pid))
            assert got[True] == got[False], b
            rows = json.loads(got[True][0])
            full += len(rows) == 20
            ratings = [r["rating"] for r in rows]
            ties += len(ratings) != len(set(ratings))
        assert full > 100 and ties > 50, (full, ties)
    finally:
        R._EV_GRADE_FRESH.update(at=0.0, ok=False)


# --------------------------------------------------------------- 14 --
# prewarm: the swapped-in tables into memory before the first visitor

def test_prewarm_plan_takes_what_pages_read_in_priority_order():
    """The server's first run filled 16 GB smallest-first with indexes no
    page reads and left the boards cold."""
    import prewarm as W
    G = 1024 ** 3
    rels = [("results_native_id_idx", "i", G // 2),          # no page reads it
            ("idx_results_normalized_time", "i", G),
            ("ranking_results_rr_pr_time_idx", "i", 3 * G),   # prefixed live name
            ("season_comps", "r", 2 * G),
            ("idx_season_comps_match", "i", G // 4),
            ("athlete_season_as_pool_mean_idx", "i", 30 * G),  # too big for what is left
            ("idx_athlete_season_ev_mean", "i", G)]
    got = W.plan(rels, 7 * G)
    assert [n for n, _ in got] == ["season_comps", "idx_season_comps_match",
                                   "ranking_results_rr_pr_time_idx",
                                   "idx_athlete_season_ev_mean"]
    assert W.plan(rels, 0) == []


def test_prewarm_runs_after_the_last_swap_and_in_the_nightly():
    sh = _read("deploy", "run_pipeline.sh")
    assert sh.rindex("\nbgwait\n") < sh.index("step 19b_prewarm") < sh.rindex("\nsummarise")
    assert sh.index("step 18_vacuum") < sh.index("step 19b_prewarm")
    nu = _read("deploy", "nightly_update.sh")
    assert nu.index("step 10f4_season_events") < nu.index("step 13z_prewarm")


# --------------------------------------------------------------- 15 --
# The next-race line never holds a worker; a race predicted once per box

def test_soon_or_later_fast_slow_and_busy():
    import time as _t
    import pages3
    import ttlcache
    ttlcache.clear()
    assert pages3._soonOrLater(("t", 1), lambda: {"available": True, "v": 1},
                               ttl=60, ttl_of=None, wait=2) == {"available": True, "v": 1}
    calls = []

    def slow():
        calls.append(1)
        _t.sleep(0.6)
        return {"available": True, "v": 2}
    assert pages3._soonOrLater(("t", 2), slow, ttl=60, ttl_of=None, wait=0.05) is None
    # the slot is taken while it runs: nothing else starts
    assert pages3._soonOrLater(("t", 3), lambda: calls.append("x") or {}, ttl=60,
                               ttl_of=None, wait=0.05) is None
    _t.sleep(0.8)
    assert pages3._soonOrLater(("t", 2), slow, ttl=60, ttl_of=None, wait=0.05) == \
        {"available": True, "v": 2}
    assert calls == [1]
    ttlcache.clear()


def test_next_race_route_answers_not_yet_without_waiting(monkeypatch):
    import time as _t
    import app as A
    import pages3
    import ttlcache
    import upcoming_preview as U
    ttlcache.clear()
    monkeypatch.setattr(pages3, "NEXT_RACE_WAIT", 0.05)

    class _C:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def rollback(self):
            pass
    monkeypatch.setattr(pages3, "getConn", lambda: _C())
    monkeypatch.setattr(pages3, "_cursor", lambda conn: _C())

    def slow(cur, gc, pid, today):
        _t.sleep(0.5)
        return {"available": True, "races": [{"name": "Meet"}]}
    monkeypatch.setattr(U, "athleteNextRace", slow)
    c = A.app.test_client()
    t0 = _t.time()
    r = c.get("/api/next-race/athlete/123")
    assert _t.time() - t0 < 0.4
    assert r.get_json() == {"available": False, "pending": True}
    assert r.headers["Cache-Control"] == "no-store"
    _t.sleep(0.7)
    r = c.get("/api/next-race/athlete/123")
    assert r.get_json()["available"] is True and "public" in r.headers["Cache-Control"]
    ttlcache.clear()


def test_a_live_race_prediction_is_shared_across_workers(tmp_path, monkeypatch):
    import sharedcache
    import ttlcache
    import upcoming_preview as U
    import meet_forecast as MF
    monkeypatch.setattr(sharedcache, "DIR", str(tmp_path / "c"))
    monkeypatch.setattr(MF, "loadRace", lambda cur, m, d, s: None)
    n = []
    monkeypatch.setattr(U, "livePrediction",
                        lambda cur, m, d, s: n.append(1) or {"available": True, "runners": []})
    ttlcache.clear()
    a = U.racePrediction(None, 77, 3, "anet")
    ttlcache.clear()                                    # another worker
    b = U.racePrediction(None, 77, 3, "anet")
    assert a == b == {"available": True, "runners": []} and n == [1]
    ttlcache.clear()


# --------------------------------------------------------------- 16 --
# Search: public at the edge; the first letters computed once for the box

def test_search_responses_are_public():
    import app as A
    import ttlcache
    c = A.app.test_client()
    A._SEARCH_CACHE.clear()
    orig_api, orig_run = A._searchApiRows, A._run_search
    A._searchApiRows = lambda raw, kind, limit: [{"label": "Jo Smith", "kind": "athlete"}]
    A._run_search = lambda q, kind, year, offset: ([], {}, [])
    try:
        assert c.get("/search/api?q=smith").headers["Cache-Control"] == A.PUBLIC_CACHE_CONTROL
        r = c.get("/search?q=smith")
        assert r.status_code == 200 and r.headers["Cache-Control"] == A.PUBLIC_CACHE_CONTROL
        assert c.get("/search?q=smith&format=json").headers["Cache-Control"] == \
            A.PUBLIC_CACHE_CONTROL
    finally:
        A._searchApiRows, A._run_search = orig_api, orig_run
        A._SEARCH_CACHE.clear()


def test_short_queries_are_computed_once_for_the_box(tmp_path, monkeypatch):
    import app as A
    import sharedcache
    monkeypatch.setattr(sharedcache, "DIR", str(tmp_path / "c"))
    n = []
    for q in ("jo", "jo", "jo!", "smi"):                     # "jo!" is its own key
        A._SEARCH_CACHE.clear()                           # another worker each time
        A._searchCached(("api", q, "", 10), lambda: n.append(q) or [q], query=q)
    assert n == ["jo", "jo!", "smi"]
    n.clear()
    for _ in range(2):                                    # "smith" is short now (6, 2026-11-08)
        A._SEARCH_CACHE.clear()
        A._searchCached(("api", "smith", "", 10), lambda: n.append(1) or [], query="smith")
    assert n == [1]
    n.clear()
    for _ in range(2):                                    # past SEARCH_SHARED_LEN: per worker
        A._SEARCH_CACHE.clear()
        A._searchCached(("api", "smithson", "", 10), lambda: n.append(1) or [], query="smithson")
    assert n == [1, 1]
    A._SEARCH_CACHE.clear()


# --------------------------------------------------------------- 17 --
# The 2026-11-07 sweep: exactness of each rewrite

def test_pick_race_is_max_with_fewer_predictions():
    import random
    import upcoming_preview as U
    rnd = random.Random(7)
    labels = ["Varsity Boys", "JV Boys", "Open", "Frosh/Soph", "Boys 5K"]
    for _ in range(3000):
        races = [{"div_id": i, "label": rnd.choice(labels),
                  "school_n": rnd.choice([0, 3, 7, 7, 12]),
                  "persons": {9} if rnd.random() < 0.3 else set()} for i in range(rnd.randint(1, 6))]
        field = {r["div_id"] for r in races if rnd.random() < 0.4}
        asked = []

        def in_field(r):
            asked.append(r["div_id"])
            return r["div_id"] in field
        got = U.pickRace(races, 9, in_field)
        want = max(races, key=lambda r: U.raceOrder(r, 9, r["div_id"] in field))
        assert got is want
        assert len(asked) <= len(races)
    # a returning runner's race costs no prediction at all
    races = [{"div_id": 1, "label": "JV", "persons": {9}}, {"div_id": 2, "label": "Varsity"}]
    assert U.pickRace(races, 9, lambda r: 1 / 0)["div_id"] == 1


def test_shared_failure_expires_before_a_success(tmp_path):
    import sharedcache as SC
    d, clock, n = str(tmp_path / "c"), [1000.0], []
    ttl_of = lambda v: 3600 if v.get("available") else 300           # noqa: E731
    f = lambda: n.append(1) or {"available": False}                  # noqa: E731
    SC.get("k", f, ttl=3600, ttl_of=ttl_of, now=lambda: clock[0], path=d)
    clock[0] += 200
    SC.get("k", f, ttl=3600, ttl_of=ttl_of, now=lambda: clock[0], path=d)
    assert len(n) == 1                         # a failure is shared for 300 s ...
    clock[0] += 200
    SC.get("k", f, ttl=3600, ttl_of=ttl_of, now=lambda: clock[0], path=d)
    assert len(n) == 2                         # ... and no longer


def test_merge_vacuums_the_new_table_before_the_swap():
    src = _read("engine", "merge_column.py")
    body = src[src.index("def mergeColumn("):]
    assert body.index("conn.commit()") < body.index("_vacuumNew(conn, table)") \
        < body.index("_swapSiege(conn, table, cap)")
    assert "SET vacuum_cost_delay = 0" in src and 'XCP_MERGE_VACUUM' in src


def test_one_value_is_an_equality():
    import rankings as R
    p = {}
    assert R._anyOrEq("state", ["OK"], p) == " AND state = %(state_one)s" and p["state_one"] == "OK"
    p = {}
    assert R._anyOrEq("state", ["OK", "TX"], p) == " AND state = ANY(%(state)s)"


def test_unit_page_names_after_the_cut():
    import pages3
    q = " ".join(pages3._UNIT_ATHLETES.split())
    assert q.index("LIMIT 100) s") < q.index("{names}")
    assert "ORDER BY s.mean_rating DESC NULLS LAST, s.person_id" in q


def test_entrant_names_once_per_person_on_postgres(pg):
    import predict as P
    _conn, cur = pg
    cur.execute("DROP TABLE pg_temp.results")          # the fixture's, not this shape
    cur.execute("""CREATE TEMP TABLE results AS
        SELECT 'Calvin'::text AS school, (g % 3 + 1)::bigint AS person_id,
               '2025-10-0' || (1 + g % 9) AS date, (1000 + g)::real AS time_seconds,
               '11'::text AS grade, 'hs_m'::text AS rating_pool, (150 + g % 9)::real AS speed_rating,
               CASE WHEN g % 3 = 2 THEN ' Row Name ' END AS athlete_name
        FROM generate_series(1, 30) g""")
    cur.execute("CREATE TEMP TABLE athletes (athlete_id bigint, person_id bigint, first_name text,"
                " last_name text, gender text)")
    cur.execute("INSERT INTO athletes VALUES (1, 1, 'Ann', 'Lee', 'M'), (2, 2, '', '', 'M')")
    P._PG["has"] = False
    rows = {r["person_id"]: r for r in P._raceEntrantRows(cur, ["Calvin"], "XC", 2025)}
    assert rows[1]["name"] == "Ann Lee"                 # the athletes row
    assert rows[3]["name"] == "Row Name"                # no athletes row: the result's own name
    assert rows[2]["name"] is None                      # blank everywhere
    assert rows[1]["n_races"] == 10
    P._PG["has"] = None
