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
    assert "WITH RECURSIVE dists" in sql and "distance = dists.d" in sql


def test_without_a_pool_or_on_a_field_event_it_keeps_the_plain_query():
    import rankings
    assert not rankings._prPerDistance({"distance": 5000, "pool": "all"}, False)
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
    cur.execute("ANALYZE ranking_results")
    for qs in ("board=pr&sport=XC&pool=hs_m&distance=5000&limit=50",
               "board=pr&sport=XC&pool=hs_m&distance=5000&limit=50&offset=200",
               "board=pr&sport=both&pool=hs_f&distance=5000&limit=50"):
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
