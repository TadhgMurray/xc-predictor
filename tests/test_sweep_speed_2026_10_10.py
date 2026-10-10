# Project: xc-predictor / tests
# File:    test_sweep_speed_2026_10_10.py
# Purpose: the speed / reliability / security items of the 2026-10-10 sweep
#          (docs/SWEEP-2026-10-10.md, D1 D2 D5 D6 D8 D10 D14 D15 D16 D17).
#
# ! NO DATABASE. Routes are exercised through their helpers with fakes, and
#   the pipeline scripts by their source, the way test_pipeline_failures.py
#   and test_page_cache_headers.py already do.
import contextlib
import inspect
import io
import os
import re
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import _env  # noqa: F401,E402  -- sets XCP_DB_PASSWORD, must precede config

try:
    import flask                                                  # noqa: F401
    import psycopg2.errors as pgerr
    _HAVE = True
except ImportError:
    _HAVE = False


def read(*p):
    return io.open(os.path.join(_ROOT, *p), encoding="utf-8").read()


# ------------------------------------------------------------------ #
#  D1 -- the board APIs are public and cached
# ------------------------------------------------------------------ #

@unittest.skipUnless(_HAVE, "flask/psycopg2 not installed")
class BoardApisAreCached(unittest.TestCase):
    def setUp(self):
        import app as A
        import ttlcache
        self.A = A
        ttlcache.clear()

    def _wrapped(self, status=200, body='{"rows": []}'):
        calls = {"n": 0}

        def view():
            calls["n"] += 1
            return self.A.app.response_class(body, status=status,
                                             mimetype="application/json")
        return self.A._publicBoard("t-%d" % status)(view), calls

    def test_one_compute_for_the_same_query_in_any_order(self):
        fn, calls = self._wrapped()
        for qs in ("a=1&b=2", "b=2&a=1", "a=1&b=2"):
            with self.A.app.test_request_context("/api/rankings?" + qs):
                resp = fn()
                self.assertEqual(resp.status_code, 200)
                self.assertEqual(resp.get_data(as_text=True), '{"rows": []}')
                cc = resp.headers["Cache-Control"]
                self.assertIn("public", cc)
                self.assertIn(f"s-maxage={self.A.PAGE_S_MAXAGE}", cc)
                # and _headers keeps it (setdefault), not no-store
                self.assertEqual(self.A._headers(resp).headers["Cache-Control"], cc)
        self.assertEqual(calls["n"], 1)

    def test_another_query_is_another_entry(self):
        fn, calls = self._wrapped()
        for qs in ("board=pr", "board=ability"):
            with self.A.app.test_request_context("/api/rankings?" + qs):
                fn()
        self.assertEqual(calls["n"], 2)

    def test_an_error_is_never_stored(self):
        fn, calls = self._wrapped(status=400, body='{"error": "x"}')
        for _ in range(2):
            with self.A.app.test_request_context("/api/rankings?bad=1"):
                resp = fn()
                self.assertEqual(resp.status_code, 400)
                self.assertIn("no-store",
                              self.A._headers(resp).headers["Cache-Control"])
        self.assertEqual(calls["n"], 2)

    def test_the_four_routes_are_wrapped(self):
        for ep in ("api_rankings", "api_teams", "api_courses",
                   "api_rankings_rank"):
            view = self.A.app.view_functions[ep]
            self.assertTrue(hasattr(view, "__wrapped__"), ep)

    def test_nothing_user_specific_feeds_them(self):
        """The cache is shared by every viewer, so nothing in these routes
        may read the session, a cookie or the account."""
        for ep in ("api_rankings", "api_teams", "api_courses",
                   "api_rankings_rank"):
            src = inspect.getsource(self.A.app.view_functions[ep].__wrapped__)
            for bad in ("session", "cookies", "_accounts", "currentSession"):
                self.assertNotIn(bad, src, f"{ep} reads {bad}")
            self.assertIsNone(re.search(r"\bg\.", src), f"{ep} reads flask.g")
        for mod in ("rankings.py", "teams.py", "courses.py", "school_units.py"):
            src = read("racecast", mod)
            self.assertNotIn("from flask import", src, mod)
            self.assertNotIn("request.cookies", src, mod)

    def test_a_failed_count_does_not_print_postgres(self):
        src = inspect.getsource(self.A.app.view_functions["api_rankings"].__wrapped__)
        self.assertNotIn('f"count failed: {exc}"', src)


# ------------------------------------------------------------------ #
#  D2 -- ?r= only where it does work
# ------------------------------------------------------------------ #

@unittest.skipUnless(_HAVE, "flask/psycopg2 not installed")
class ResultLinksUseTheFragment(unittest.TestCase):
    def setUp(self):
        import app as A
        self.A = A

    def test_athlete_href(self):
        A = self.A
        with A.app.test_request_context("/race/xc/1/2"):
            from flask import g
            # no pins computed: the old ?r= (never a broken highlight)
            self.assertEqual(A.athleteHref(7, 99), "/athlete/7?r=99")
            g.rid_pins = frozenset({-5})
            self.assertEqual(A.athleteHref(7, 99), "/athlete/7#race-99")
            self.assertEqual(A.athleteHref(7, -5), "/athlete/7?r=-5")
            self.assertEqual(A.athleteHref(7, None), "/athlete/7")
            self.assertEqual(A.athleteHref(7, ""), "/athlete/7")

    def test_a_useless_pin_redirects_to_the_shared_url(self):
        A = self.A
        with A.app.test_request_context("/race/xc/1/2?school=Tufts&r=-123"):
            resp = A._dropUselessPin(-123)
        self.assertEqual(resp.status_code, 302)
        loc = resp.headers["Location"]
        self.assertTrue(loc.endswith("/race/xc/1/2?school=Tufts#r-123"), loc)
        self.assertIn("public", resp.headers["Cache-Control"])

    def test_a_folded_copy_leaves_its_id_on_the_survivor(self):
        A = self.A
        anet = {"sport": "XC", "canon_meet_id": 5, "time_raw": 900.0,
                "result_id": 1, "source": "anet", "meet": "M",
                "speed_rating": 100.0, "date": "2025-09-01"}
        tfrrs = dict(anet, result_id=-2, source="tfrrs", meet=None,
                     speed_rating=None)
        out = A._merge_by_canon([tfrrs, anet])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["result_id"], 1)
        self.assertEqual(out[0]["alias_ids"], [-2])
        out = A._merge_by_canon([anet, tfrrs])
        self.assertEqual(out[0]["alias_ids"], [-2])

    def test_the_templates(self):
        race = read("racecast", "templates", "race.html")
        self.assertNotIn("?r={{", race)
        self.assertGreaterEqual(race.count("athlete_href("), 3)
        ath = read("racecast", "templates", "athlete.html")
        self.assertIn('class="race-alias"', ath)
        lf = read("racecast", "static", "link-flash.js")
        self.assertIn("race-alias", lf)
        self.assertIn("r=(-?\\d+)", lf)

    def _routeWithOneFeed(self, url):
        """GET `url` with a database whose meet has ONE feed, 'anet'."""
        A = self.A

        class Cur:
            def __init__(self):
                self.last = None

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def execute(self, sql, params=None):
                if "GROUP  BY source" in sql or "GROUP BY source" in sql:
                    self.last = [{"source": "anet", "n": 10}]
                elif "SELECT source FROM results" in sql:
                    self.last = [{"source": "anet"}]
                else:
                    raise AssertionError("the redirect ran more than the "
                                         "source lookup: " + sql[:80])

            def fetchone(self):
                return self.last[0] if self.last else None

            def fetchall(self):
                return list(self.last or [])

        @contextlib.contextmanager
        def conn():
            class C:
                def cursor(self, **k):
                    return Cur()

                def rollback(self):
                    pass
            yield C()
        saved = A.getConn
        A.getConn = conn
        try:
            return A.app.test_client().get(url)
        finally:
            A.getConn = saved

    def test_old_pinned_links_land_on_the_shared_page(self):
        r = self._routeWithOneFeed("/race/xc/10/20?r=555")
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r.headers["Location"].endswith("/race/xc/10/20#r555"))
        r = self._routeWithOneFeed("/race/tf/10/3/20?r=-555")
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r.headers["Location"].endswith("/race/tf/10/3/20#r-555"))

    def test_the_race_routes_redirect_a_useless_pin(self):
        src = inspect.getsource(self.A.race_xc)
        self.assertIn("_dropUselessPin", src)
        self.assertIn("g.rid_pins", src)
        src = inspect.getsource(self.A.race_tf)
        self.assertIn("_dropUselessPin", src)
        self.assertIn("g.rid_pins", src)


# ------------------------------------------------------------------ #
#  D10 -- /course/<anything> is a 404
# ------------------------------------------------------------------ #

@unittest.skipUnless(_HAVE, "flask/psycopg2 not installed")
class UnknownCourseIs404(unittest.TestCase):
    def test_no_distances_and_no_results_is_none(self):
        import app as A
        saved = (A.get_course_distances, A.get_course_header)
        try:
            A.get_course_distances = lambda cur, name: []
            A.get_course_header = lambda cur, name, dist=None: None
            self.assertIsNone(A.buildCourseCtx(None, "Nowhere Park", None))
        finally:
            A.get_course_distances, A.get_course_header = saved
        self.assertIn("abort(404)", inspect.getsource(A.course))
        self.assertIn("if not ctx or not ctx.get(\"header\")",
                      read("racecast", "build_course_boards.py"))


# ------------------------------------------------------------------ #
#  D5 / D6 / D16 -- quiet swaps, the flag only as a fallback
# ------------------------------------------------------------------ #

class FakeSwapConn:
    """Raises LockNotAvailable on DROP while `busy` lasts."""

    def __init__(self, busy, flag_seen):
        self.busy, self.sql, self.flag_seen = busy, [], flag_seen

    def cursor(self):
        conn = self

        class C:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def execute(self, sql, params=None):
                conn.sql.append(sql % params if params else sql)
                if sql.startswith("DROP"):
                    import maintenance
                    conn.flag_seen.append(os.path.exists(maintenance.FLAG))
                    if conn.busy:
                        conn.busy -= 1
                        raise pgerr.LockNotAvailable("busy")
        return C()

    def commit(self):
        pass

    def rollback(self):
        pass


@unittest.skipUnless(_HAVE, "flask/psycopg2 not installed")
class SwapsAreQuietFirst(unittest.TestCase):
    def setUp(self):
        import maintenance
        self.m = maintenance
        self.tmp = tempfile.mkdtemp()
        self._flag = maintenance.FLAG
        maintenance.FLAG = os.path.join(self.tmp, "flag")

    def tearDown(self):
        self.m.FLAG = self._flag

    def test_the_quiet_wait_is_well_under_the_site_lock_timeout(self):
        site = re.search(r"XCP_DB_LOCK_TIMEOUT_MS=(\d+)",
                         read("deploy", "server_setup.sh"))
        self.assertIsNotNone(site)
        self.assertLessEqual(self.m.QUIET_LOCK_MS * 2, int(site.group(1)))
        import dbfast
        self.assertLess(dbfast.SWAP_WAIT_S * 1000, int(site.group(1)))

    def test_a_busy_table_is_swapped_without_the_flag(self):
        import dbfast
        seen = []
        conn = FakeSwapConn(busy=3, flag_seen=seen)
        n = dbfast.swapTable(conn, "t", analyze=False, quiet=True,
                             quiet_pause=0)
        self.assertEqual(n, 4)
        self.assertEqual(seen, [False] * 4)            # never in maintenance
        # (the fake pastes the parameter in unquoted)
        self.assertIn(f"SET LOCAL lock_timeout = {self.m.QUIET_LOCK_MS}ms",
                      conn.sql)
        self.assertFalse(os.path.exists(self.m.FLAG))

    def test_the_flag_is_the_fallback_and_is_cleared(self):
        import dbfast
        seen = []
        conn = FakeSwapConn(busy=5, flag_seen=seen)
        dbfast.swapTable(conn, "t", analyze=False, quiet=True,
                         quiet_tries=3, quiet_pause=0, wait=0)
        self.assertEqual(seen, [False, False, False, True, True, True])
        self.assertFalse(os.path.exists(self.m.FLAG))

    def test_never_winning_raises_and_clears_the_flag(self):
        import dbfast
        conn = FakeSwapConn(busy=99, flag_seen=[])
        with self.assertRaises(RuntimeError):
            dbfast.swapTable(conn, "t", analyze=False, quiet=True,
                             quiet_tries=2, quiet_pause=0, tries=2, wait=0)
        self.assertFalse(os.path.exists(self.m.FLAG))

    def test_the_big_swaps_go_quiet_first(self):
        for parts in (("racecast", "build_ranking_results.py"),
                      ("engine", "merge_column.py"),
                      ("racecast", "build_school_identity.py")):
            src = read(*parts)
            self.assertIn("swapQuietlyFirst(", src, parts[-1])
        br = read("racecast", "build_ranking_results.py")
        self.assertNotIn('with siteMaintenance("swap ranking_results")', br)
        mc = read("engine", "merge_column.py")
        self.assertNotIn("lock_timeout = '10s'", mc)

    def test_the_hand_rolled_swaps_use_swapTable(self):
        si = read("racecast", "search_index.py")
        self.assertNotIn("lock_timeout = '5s'", si)
        cd = read("scripts", "build_college_directory.py")
        self.assertIn('swapTable(conn, "college_directory"', cd)
        self.assertNotIn("lock_timeout = '5s'", cd)
        gm = read("scripts", "build_group_means.py")
        self.assertIn('swapTable(conn, "group_scale"', gm)
        self.assertNotIn('DROP TABLE IF EXISTS group_scale"', gm)
        pr = read("scripts", "person_redirects.py")
        self.assertIn('swapTable(conn, "person_probe")', pr)
        self.assertNotIn("DROP TABLE IF EXISTS person_probe;", pr)


# ------------------------------------------------------------------ #
#  D8 -- /search is rate limited
# ------------------------------------------------------------------ #

class SearchIsLimited(unittest.TestCase):
    def test_the_map_lists_search(self):
        src = read("deploy", "nginx_limits.sh")
        block = src[src.index("map $request_uri $xcp_lim_api"):]
        block = block[:block.index("}")]
        self.assertRegex(block, r"~\^/search\s+\$binary_remote_addr;")


# ------------------------------------------------------------------ #
#  D14 -- one connection per request
# ------------------------------------------------------------------ #

class _PoolConn:
    def __init__(self, pool):
        self.pool, self.rollbacks = pool, 0

    def rollback(self):
        self.rollbacks += 1

    def cursor(self, *a, **k):
        return None


@unittest.skipUnless(_HAVE, "flask/psycopg2 not installed")
class OneConnectionPerRequest(unittest.TestCase):
    def setUp(self):
        import db_timing
        self.dt = db_timing
        self.pool = {"out": 0, "taken": 0, "closed": 0}
        pool = self.pool

        @contextlib.contextmanager
        def fake():
            pool["out"] += 1
            pool["taken"] += 1
            conn = _PoolConn(pool)
            broken = False
            try:
                yield conn
            except (pgerr.InterfaceError, pgerr.OperationalError):
                broken = True
                raise
            finally:
                pool["out"] -= 1
                pool["closed"] += broken
        self._real = db_timing._REAL_GETCONN
        db_timing._REAL_GETCONN = fake
        self.app = flask.Flask(__name__)
        self.app.teardown_appcontext(db_timing.releaseRequestConn)

    def tearDown(self):
        self.dt._REAL_GETCONN = self._real

    def test_sequential_blocks_share_one_checkout(self):
        with self.app.test_request_context("/athlete/1"):
            with self.dt.getConn() as a:
                pass
            with self.dt.getConn() as b:
                pass
            self.assertIs(a._conn, b._conn)
            self.assertEqual(self.pool["taken"], 1)
            self.assertEqual(a._conn.rollbacks, 2)      # each block still ends clean
            self.assertEqual(self.pool["out"], 1)
        self.assertEqual(self.pool["out"], 0)            # teardown gave it back

    def test_a_nested_block_gets_its_own(self):
        with self.app.test_request_context("/athlete/1"):
            with self.dt.getConn() as a:
                with self.dt.getConn() as b:
                    self.assertIsNot(a._conn, b._conn)
            self.assertEqual(self.pool["taken"], 2)
        self.assertEqual(self.pool["out"], 0)

    def test_a_broken_connection_is_not_kept(self):
        with self.app.test_request_context("/athlete/1"):
            with self.assertRaises(pgerr.OperationalError):
                with self.dt.getConn():
                    raise pgerr.OperationalError("server closed the connection")
            self.assertEqual(self.pool["closed"], 1)
            with self.dt.getConn():
                pass
            self.assertEqual(self.pool["taken"], 2)
        self.assertEqual(self.pool["out"], 0)

    def test_outside_a_request_nothing_changes(self):
        with self.dt.getConn():
            pass
        with self.dt.getConn():
            pass
        self.assertEqual(self.pool["taken"], 2)
        self.assertEqual(self.pool["out"], 0)

    def test_the_site_registers_the_teardown(self):
        self.assertIn("app.teardown_appcontext(db_timing.releaseRequestConn)",
                      read("racecast", "app.py"))


# ------------------------------------------------------------------ #
#  D15 -- the signed-in hint
# ------------------------------------------------------------------ #

@unittest.skipUnless(_HAVE, "flask/psycopg2 not installed")
class SignedInHint(unittest.TestCase):
    def _cookies(self, resp):
        return [v for k, v in resp.headers.items() if k == "Set-Cookie"]

    def test_login_and_logout_set_the_hint(self):
        import accounts as AC
        app = flask.Flask(__name__)
        with app.test_request_context("/login"):
            r = flask.Response("x")
            AC.setCookie(r, "raw")
            hint = [c for c in self._cookies(r) if c.startswith("rc_si=")]
            self.assertTrue(hint and hint[0].startswith("rc_si=1"))
            self.assertNotIn("HttpOnly", hint[0])
            r = flask.Response("x")
            AC.clearCookie(r)
            hint = [c for c in self._cookies(r) if c.startswith("rc_si=")]
            self.assertTrue(hint and hint[0].startswith("rc_si=0"))

    def test_a_signed_out_answer_sets_zero(self):
        import app as A
        c = A.app.test_client()
        r = c.get("/api/me")
        self.assertEqual(r.get_json(), {"signed_in": False})
        self.assertTrue(any(v.startswith("rc_si=0") for v in self._cookies(r)))

    def test_the_topbar_skips_the_fetch_on_zero(self):
        js = read("racecast", "static", "topbar-search.js")
        i = js.index("rc_si=0")
        self.assertLess(i, js.index("fetch('/api/me'"))


# ------------------------------------------------------------------ #
#  D17 -- leftovers
# ------------------------------------------------------------------ #

@unittest.skipUnless(_HAVE, "flask/psycopg2 not installed")
class Leftovers(unittest.TestCase):
    def test_no_ddl_per_report(self):
        import app as A
        src = inspect.getsource(A.api_report)
        self.assertNotIn("CREATE TABLE", src)
        self.assertNotIn("CREATE TABLE", inspect.getsource(A.api_predict_share))

    def test_ensure_table_runs_the_ddl_once(self):
        import app as A
        ran = []

        class Cur:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def execute(self, sql, params=None):
                ran.append(sql)

            def fetchone(self):
                return (None,)

        class Conn:
            def cursor(self):
                return Cur()

            def commit(self):
                pass
        A._TABLES_READY.discard("t_once")
        A._ensureTable(Conn(), "t_once", "CREATE TABLE t_once (x int)")
        A._ensureTable(Conn(), "t_once", "CREATE TABLE t_once (x int)")
        self.assertEqual(ran.count("CREATE TABLE t_once (x int)"), 1)

    def test_a_pixel_bomb_is_refused_before_the_decode(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow not installed")
        import accounts as AC
        buf = io.BytesIO()
        # 9000 x 8000 = 72M pixels, a few KB as a 1-bit PNG
        Image.new("1", (9000, 8000)).save(buf, "PNG")
        self.assertLess(len(buf.getvalue()), AC.MAX_PHOTO_BYTES)
        with self.assertRaises(AC.AccountsError) as cm:
            AC.processPhoto(buf.getvalue())
        self.assertIn("too many pixels", str(cm.exception))
        self.assertEqual(AC.MAX_PHOTO_PIXELS, AC.MAX_PHOTO_BYTES * 8)


if __name__ == "__main__":
    unittest.main()
