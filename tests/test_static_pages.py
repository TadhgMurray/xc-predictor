# Project: xc-predictor / tests
# File:    test_static_pages.py
# Purpose: racecast/build_static_pages.py writes only what nginx can send
#          byte for byte, under the key nginx will look for, and a file is
#          the app's own answer for its URL.
#
# ★ THREE LAYERS, CHEAPEST FIRST.
#   1. Pure: the key rules (Python's keyFor and the regexes written into the
#      nginx map agree on every tricky request line), which responses may
#      become files, the windows and the sweep, the site-file include.
#   2. The app, on a scratch database (XCP_TWIN_TEST_DSN, skipped without
#      one): every URL of the default list is built by one process and
#      rendered live by ANOTHER, fresh process -- the file must be the live
#      answer's bytes, and its headers the set nginx adds.
#   3. nginx itself, when the binary is on the box: gunicorn + nginx with the
#      generated config, and build_static_pages.verify() -- each URL as a
#      file and through the app, plain, gzip and HEAD, identical.
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest

import _env  # noqa: F401  -- sets XCP_DB_PASSWORD, must precede config

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in ("racecast", "scripts", "engine"):
    _p = os.path.join(_ROOT, _d)
    if _p not in sys.path:
        sys.path.insert(0, _p)

import build_static_pages as S  # noqa: E402

DSN = os.environ.get("XCP_TWIN_TEST_DSN", "host=/tmp/pgtest port=54329 user=postgres dbname=postgres")


# ------------------------------------------------------------------ #
#  1. pure
# ------------------------------------------------------------------ #

CASES = [
    ("/", "//@"),                         # the filesystem reads it as /@
    ("/rankings", "/rankings/@"),
    ("/rankings?", "/rankings/@"),
    ("/rankings/xc/hs-boys", "/rankings/xc/hs-boys/@"),
    ("/api/rankings?board=ability&pool=hs_m&sport=XC&limit=50",
     "/api/rankings/@board=ability&pool=hs_m&sport=XC&limit=50"),
    ("/api/rankings?state=CA%2CTX&board=ability", "/api/rankings/@state=CA%2CTX&board=ability"),
    ("/rankings/", None),                 # a 301 in the app
    ("//rankings", None),
    ("/./rankings", None),
    ("/x/../rankings", None),
    ("/rankings?x=a/b", None),            # a slash in the query: not one file name
    ("/rankings?q=a b", None),
    ("/rankings?q=\"x\"", None),
    ("/athlete@1", None),                 # "@" joins path and query; never in either
    ("/rankings?x=" + "a" * 300, None),   # past one file name
    ("/search?q=st.louis", "/search/@q=st.louis"),
    ("/rankings?x=/.", None),
]


class KeyRules(unittest.TestCase):
    def test_python_keys(self):
        for uri, want in CASES:
            self.assertEqual(S.keyFor(uri), want, uri)

    def test_nginx_map_says_the_same(self):
        """The regexes written into the nginx map, read back the way nginx
        reads a quoted string, give every case the same key."""
        conf = S.nginxHttp(headers={"p": [], "u": []})
        body = conf.split("map $request_uri $rcs_key {", 1)[1].split("}", 1)[0]
        rules = []
        for line in body.strip().splitlines():
            m = re.match(r'\s*"((?:[^"\\]|\\.)*)"\s+("(?:[^"\\]|\\.)*"|-);', line)
            if not m:
                continue
            # nginx unquoting: \\ \" \' lose the backslash, any other
            # escape (\. \? \d) reaches PCRE as written
            rx = re.sub(r"""\\([\\"'])""", r"\1", m.group(1))
            out = None if m.group(2) == "-" else m.group(2)[1:-1]
            self.assertTrue(rx.startswith("~"))
            rules.append((re.compile(rx[1:].replace("(?<", "(?P<")), out))
        self.assertEqual(len(rules), len(S.NGINX_KEY_RULES))
        for uri, want in CASES:
            got = None
            for rx, out in rules:
                m = rx.search(uri)
                if m:
                    if out is not None:
                        got = out
                        for k, v in m.groupdict().items():
                            got = got.replace("$" + k, v)
                    break
            if want is None:
                # nginx may still form a key for a name too long to exist;
                # the builder never writes one, so nginx finds no file
                self.assertTrue(got is None or len(uri) > 255 or S.keyFor(uri) is None, uri)
            else:
                self.assertEqual(got, want, uri)

    def test_root_and_query_files_do_not_collide(self):
        self.assertEqual(S._relFile("//@", "p", "html"), "k/@.p.html")
        self.assertEqual(S._relFile("/rankings/@", "p", "html"), "k/rankings/@.p.html")
        self.assertNotEqual(S._relFile(S.keyFor("/rankings/xc"), "p", "html"),
                            S._relFile(S.keyFor("/rankings?xc"), "p", "html"))


WANT = {"p": sorted([("Cache-Control", "public, max-age=300, s-maxage=900"),
                     ("X-Frame-Options", "DENY")]),
        "u": sorted([("Cache-Control", "public, max-age=300"), ("X-Frame-Options", "DENY")])}


def _h(cc="public, max-age=300, s-maxage=900", ct="text/html; charset=utf-8", **extra):
    h = {"Content-Type": ct, "Content-Length": "9", "Cache-Control": cc, "X-Frame-Options": "DENY"}
    h.update({k.replace("_", "-"): v for k, v in extra.items()})
    return h


class WhatMayBeAFile(unittest.TestCase):
    big = b"x" * 5000

    def check(self, status, headers, body=None):
        return S.profileOf(status, headers, self.big if body is None else body, WANT)[0]

    def test_a_page_and_a_board(self):
        self.assertEqual(self.check(200, _h()), ("p", "html"))
        self.assertEqual(self.check(200, _h(ct="application/json")), ("p", "json"))
        self.assertEqual(self.check(200, _h(cc="public, max-age=300", ct="application/json"), b"{}"),
                         ("u", "json"))

    def test_anything_per_request_or_unusual_is_left_to_the_app(self):
        for headers in (_h(Set_Cookie="a=1"), _h(Vary="Cookie"), _h(X_Robots_Tag="noindex"),
                        _h(cc="no-store"), _h(ct="text/plain; charset=utf-8"),
                        _h(Content_Disposition="attachment"), _h(Retry_After="120")):
            self.assertIsNone(self.check(200, headers), headers)
        for status in (301, 404, 410, 500, 503):
            self.assertIsNone(self.check(status, _h()), status)

    def test_a_body_nginx_would_treat_differently(self):
        self.assertIsNone(self.check(200, _h(), b"<!--#include virtual=\"/x\" -->" + self.big))
        # shorter than gzip_min_length: the app's answer is not compressed
        self.assertIsNone(self.check(200, _h(ct="application/json"), b"[]"))
        self.assertIsNone(self.check(200, _h(cc="public, max-age=300", ct="application/json")))


class Windows(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _gen(self, name, files):
        g = os.path.join(self.root, "gen", name)
        for rel, body in files.items():
            os.makedirs(os.path.dirname(os.path.join(g, rel)), exist_ok=True)
            with open(os.path.join(g, rel), "wb") as fh:
                fh.write(body)
        return g

    def test_this_window_and_the_next_and_nothing_older(self):
        now = time.mktime((2026, 11, 8, 14, 27, 0, 0, 0, -1))
        g1 = self._gen("20261108T142700-a", {"k/@.p.html": b"one"})
        S.publish(self.root, g1, now)
        self.assertEqual(sorted(os.listdir(os.path.join(self.root, "b"))),
                         ["2026-11-08T14:2", "2026-11-08T14:3"])
        g2 = self._gen("20261108T143700-b", {"k/@.p.html": b"two"})
        S.publish(self.root, g2, now + 600)
        self.assertEqual(sorted(os.listdir(os.path.join(self.root, "b"))),
                         ["2026-11-08T14:3", "2026-11-08T14:4"])
        with open(os.path.join(self.root, "b", "2026-11-08T14:3", "k", "@.p.html"), "rb") as fh:
            self.assertEqual(fh.read(), b"two")

    def test_a_dead_builder_stops_serving_within_two_windows(self):
        """Nothing extends the windows but a build: twenty minutes after the
        last one, the window nginx looks in does not exist."""
        now = time.mktime((2026, 11, 8, 14, 21, 0, 0, 0, -1))
        S.publish(self.root, self._gen("g", {"k/@.p.html": b"x"}), now)
        live = os.listdir(os.path.join(self.root, "b"))
        self.assertNotIn(S.window(now + 2 * S.WINDOW_S), live)
        self.assertIn(S.window(now + S.WINDOW_S), live)

    def test_old_generations_are_swept_but_the_newest_retired_two_stay(self):
        now = time.time()
        gens = [self._gen(f"2026110{i}T000000-x", {"k/@.p.html": b"x"}) for i in range(1, 6)]
        for g in gens:
            os.utime(g, (now - 7200, now - 7200))
        S.publish(self.root, gens[-1], now)
        left = sorted(os.listdir(os.path.join(self.root, "gen")))
        self.assertEqual(left, [os.path.basename(g) for g in gens[-3:]])

    def test_clear_unlinks_every_window(self):
        S.publish(self.root, self._gen("g", {"k/@.p.html": b"x"}))
        self.assertEqual(S.clear(self.root), 2)
        self.assertEqual(os.listdir(os.path.join(self.root, "b")), [])


SITE = """\
server {
    listen 80;
    server_name racecast.co;
    if ($host = racecast.co) { return 301 https://$host$request_uri; }
    return 404;
}
server {
    server_name racecast.co www.racecast.co;
    location /static/ { alias /srv/xc-predictor/racecast/static/; expires 7d; }
    location / {
        proxy_pass http://127.0.0.1:8000;  # the app
        proxy_set_header Host $host;
    }
    listen 443 ssl; # managed by Certbot
}
"""


class SiteInclude(unittest.TestCase):
    def test_only_the_block_that_proxies_gets_it_once(self):
        once = S.patchSite(SITE, "/etc/nginx/snippets/racecast-static.conf")
        self.assertEqual(once.count(S.INCLUDE_MARK), 1)
        second = once.split("server {")[2]
        self.assertIn("include /etc/nginx/snippets/racecast-static.conf;", second.splitlines()[1])
        self.assertEqual(S.patchSite(once, "/etc/nginx/snippets/racecast-static.conf"), once)
        self.assertEqual(S.unpatchSite(once), SITE)


class Signature(unittest.TestCase):
    def test_a_changed_header_is_a_changed_signature(self):
        a = S.signature({"p": [("Cache-Control", "public, max-age=300, s-maxage=900")], "u": []})
        b = S.signature({"p": [("Cache-Control", "public, max-age=300, s-maxage=21600")], "u": []})
        self.assertNotEqual(a, b)

    def test_the_snippet_carries_it(self):
        h = {"p": [("Cache-Control", "x")], "u": [("Cache-Control", "y")]}
        with tempfile.NamedTemporaryFile("w", suffix=".conf", delete=False) as fh:
            fh.write(S.nginxServer(h))
        try:
            self.assertEqual(S.installedSignature(fh.name), S.signature(h))
        finally:
            os.unlink(fh.name)


class FlagOff(unittest.TestCase):
    def test_off_builds_nothing_and_clears(self):
        root = tempfile.mkdtemp()
        try:
            os.makedirs(os.path.join(root, "b"))
            os.symlink("../gen/x", os.path.join(root, "b", "2026-11-08T14:2"))
            old = S.ENABLED
            S.ENABLED = False
            try:
                self.assertEqual(S.main(["--dir", root]), 0)
            finally:
                S.ENABLED = old
            self.assertEqual(os.listdir(os.path.join(root, "b")), [])
            self.assertFalse(os.path.exists(os.path.join(root, "gen")))
        finally:
            shutil.rmtree(root, ignore_errors=True)


# ------------------------------------------------------------------ #
#  2 and 3. the real app on a scratch database
# ------------------------------------------------------------------ #

SCHEMA = """
DROP TABLE IF EXISTS athlete_season, ranking_results, athletes, results, results_tf,
                     homepage_panels, homepage_meta, homepage_recent CASCADE;
SELECT setseed(0.42);
CREATE TABLE athletes (athlete_id bigint, person_id bigint, first_name text, last_name text,
                       school text, gender text);
INSERT INTO athletes SELECT g, g,
       (ARRAY['José','Liam','Ava','Zoë','Mia','Noah'])[1 + g % 6],
       (ARRAY['Smith','O''Brien','Nuñez','Lee'])[1 + (g / 6) % 4] || g,
       'School ' || (g % 90), CASE WHEN g % 2 = 0 THEN 'M' ELSE 'F' END
FROM generate_series(1, 6000) g;
CREATE TABLE results (person_id bigint, athlete_name text);
CREATE TABLE results_tf (person_id bigint, athlete_name text);
CREATE TABLE athlete_season (person_id bigint, pool text, sport text, year int,
    mean_rating real, decayed_rating real, best_rating real, n_races int,
    first_race date, last_race date, school text, state text, grade text,
    state_div text, class text, section text, section_div text,
    division text, region text, conference text, area text, league text);
INSERT INTO athlete_season (person_id, pool, sport, year, mean_rating, decayed_rating,
                            best_rating, n_races, first_race, last_race, school, state, grade)
SELECT p, (ARRAY['hs_m','hs_f','college_m','college_f','ms_m','ms_f'])[1 + p % 6],
       CASE WHEN p % 5 = 0 THEN 'TF' ELSE 'XC' END, 2023 + p % 4,
       CASE WHEN p % 97 = 0 THEN 150 ELSE (60 + random() * 100)::real END,
       70, (80 + random() * 80)::real, 3 + p % 5,
       DATE '2023-08-20' + (p % 4) * 365, DATE '2023-10-20' + (p % 4) * 365,
       CASE WHEN p % 211 = 0 THEN 'Results via athletic.net' ELSE 'School ' || (p % 90) END,
       (ARRAY['CA','TX','WI','NY','OR','IN'])[1 + p % 6], (9 + p % 4)::text
FROM generate_series(1, 6000) p;
CREATE TABLE ranking_results (sport text, result_id bigint, person_id bigint, pool text,
    speed_rating real, race_date date, year int, state text, school text, grade text,
    meet_id bigint, div_id bigint, canon_meet_id bigint, time_seconds real, distance real,
    event_id bigint, state_div text, class text, section text, section_div text,
    division text, region text, conference text, area text, league text,
    mark real, event_kind text);
INSERT INTO ranking_results (sport, result_id, person_id, pool, speed_rating, race_date, year,
    state, school, grade, meet_id, div_id, canon_meet_id, time_seconds, distance)
SELECT s.sport, s.person_id * 10 + k, s.person_id, s.pool, s.mean_rating + k,
       s.first_race + k * 7, s.year, s.state, s.school, s.grade, 1000 + k, k, 1000 + k,
       (900 + random() * 400)::real, 5000
FROM athlete_season s, generate_series(1, 3) k;
CREATE TABLE homepage_panels (board text, scope text, sport text, pool text, rank int,
    person_id bigint, name text, school text, state text, rating real, season_year int,
    detail text, link text, name_link text, visible boolean DEFAULT true);
INSERT INTO homepage_panels (board, scope, sport, pool, rank, person_id, name, school, state,
                             rating, season_year, detail, link, name_link)
SELECT 'athlete', 'season', 'XC', s.pool, row_number() OVER (PARTITION BY s.pool ORDER BY s.mean_rating DESC),
       s.person_id, 'Runner ' || s.person_id, s.school, s.state, s.mean_rating, 2026, 'd',
       '/athlete/' || s.person_id, '/athlete/' || s.person_id
FROM athlete_season s WHERE s.sport = 'XC' AND s.person_id % 40 = 0;
CREATE TABLE homepage_meta (key text, value text);
INSERT INTO homepage_meta VALUES ('season_year_XC', '2026'), ('season_year_TF', '2025'),
                                 ('default_sport', 'XC');
CREATE TABLE homepage_recent (sport text, meet_id bigint, meet_name text, course_name text,
                              state text, date date, n_results int, rank int);
INSERT INTO homepage_recent VALUES ('XC', 25861, 'State (results on athletic.net)', 'LaVern Gibson',
                                    'IN', '2026-11-01', 900, 1);
ANALYZE;
"""

_DRIVER = r"""
import json, os, sys, hashlib
sys.path[:0] = [os.path.join(os.getcwd(), d) for d in ("racecast", "scripts", "engine")]
import build_static_pages as S
mode = sys.argv[1]
if mode == "build":
    m = S.build(S.defaultUrls(), sys.argv[2], check_nginx=False, log=lambda *a: None)
    print(json.dumps(m))
else:
    urls = json.loads(sys.stdin.read())
    out = {}
    for url, status, headers, body, _s in S.renderAll(urls):
        out[url] = {"status": status, "sha256": hashlib.sha256(body).hexdigest(),
                    "headers": sorted(headers.items())}
    print(json.dumps(out))
"""


def _dbEnv(dbname, shared):
    from psycopg2.extensions import parse_dsn
    d = parse_dsn(DSN)
    env = dict(os.environ)
    env.update(XCP_DB_HOST=d.get("host", "127.0.0.1"), XCP_DB_PORT=str(d.get("port", 5432)),
               XCP_DB_USER=d.get("user", "postgres"), XCP_DB_NAME=dbname,
               XCP_DB_PASSWORD=d.get("password", "x"), XCP_DB_QUIET="1", XCP_PREWARM="0",
               XCP_SHARED_CACHE_DIR=shared, XCP_WARM_SITE="0", XCP_STATIC_HOST="racecast.co",
               XCP_DB_MIN_CONN="1", XCP_DB_MAX_CONN="3", PYTHONPATH="")
    return env


def _scratchDb():
    """A fresh database with the synthetic boards, or None (no server)."""
    try:
        import psycopg2
        conn = psycopg2.connect(DSN, connect_timeout=3)
    except Exception:                                    # noqa: BLE001
        return None
    name = "static_pages_t"
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(f"DROP DATABASE IF EXISTS {name}")
        cur.execute(f"CREATE DATABASE {name}")
    conn.close()
    from psycopg2.extensions import make_dsn
    c2 = psycopg2.connect(make_dsn(DSN, dbname=name))
    with c2, c2.cursor() as cur:
        cur.execute(SCHEMA)
    c2.close()
    return name


class _App(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = _scratchDb()
        if cls.db is None:
            raise unittest.SkipTest("no scratch Postgres (XCP_TWIN_TEST_DSN)")
        cls.tmp = tempfile.mkdtemp(prefix="racecast-static-test-")
        os.chmod(cls.tmp, 0o755)
        cls.root = os.path.join(cls.tmp, "static")
        os.makedirs(cls.root)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(getattr(cls, "tmp", ""), ignore_errors=True)

    def run_driver(self, args, shared, stdin=None, extra_env=None):
        env = _dbEnv(self.db, shared)
        env["XCP_STATIC_DIR"] = self.root
        env.update(extra_env or {})
        r = subprocess.run([sys.executable, "-c", _DRIVER] + args, cwd=_ROOT, env=env,
                           input=stdin, capture_output=True, text=True, timeout=600)
        self.assertEqual(r.returncode, 0, r.stderr[-3000:])
        return json.loads(r.stdout.strip().splitlines()[-1])


class FileIsTheLiveAnswer(_App):
    def test_every_url_in_the_list(self):
        """Built by one process, rendered live by another with its own empty
        caches: for every URL that became a file, the file's bytes are the
        live body's and the live headers are the set nginx adds."""
        m = self.run_driver(["build", self.root], os.path.join(self.tmp, "shared-build"))
        self.assertGreaterEqual(len(m["files"]), 30, m["skipped"])
        # the page and the board kinds the site requests on load are all here
        for must in ("/", "/rankings", "/rankings/xc/hs-boys", "/api/updates/latest",
                     "/api/rankings?board=ability&pool=hs_m&sport=XC&scope=usa&scale=hs"
                     "&limit=50&offset=0&sort=rating"):
            self.assertIn(must, m["files"], m["skipped"].get(must))
        live = self.run_driver(["live"], os.path.join(self.tmp, "shared-live"),
                               stdin=json.dumps(sorted(m["files"])))
        gen = os.path.join(self.root, "b", S.window())
        for url, meta in sorted(m["files"].items()):
            with open(os.path.join(gen, meta["file"]), "rb") as fh:
                body = fh.read()
            import hashlib
            self.assertEqual(live[url]["status"], 200, url)
            self.assertEqual(hashlib.sha256(body).hexdigest(), live[url]["sha256"], url)
            hs = dict(live[url]["headers"])
            self.assertNotIn("Set-Cookie", hs, url)
            self.assertNotIn("Vary", hs, url)
            # the scrub ran: no feed name in a file
            self.assertNotIn(b"athletic.net", body, url)

    def test_skipped_urls_say_why(self):
        m = self.run_driver(["build", self.root], os.path.join(self.tmp, "shared-build2"))
        for url, why in m["skipped"].items():
            self.assertTrue(why, url)


def _freePort():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@unittest.skipUnless(shutil.which("nginx") and shutil.which("gunicorn"), "nginx/gunicorn not installed")
class ThroughNginx(_App):
    """The generated nginx config in front of gunicorn: each built URL as a
    file and through the app (the bypass header), plain, gzip and HEAD."""

    def test_verify(self):
        app_port, ngx_port = _freePort(), _freePort()
        ngx = os.path.join(self.tmp, "nginx")
        for d in ("conf", "logs", "tmp"):
            os.makedirs(os.path.join(ngx, d), exist_ok=True)
        snippet = os.path.join(ngx, "conf", "racecast-static.conf")
        env = _dbEnv(self.db, os.path.join(self.tmp, "shared-ngx"))
        env.update(XCP_STATIC_DIR=self.root, XCP_STATIC_NGINX_SNIPPET=snippet,
                   XCP_STATIC_PAGES="1", XCP_STATIC_GZIP_MIN_LENGTH="1024")
        script = os.path.join(_ROOT, "racecast", "build_static_pages.py")
        for kind, path in (("http", os.path.join(ngx, "conf", "http.conf")), ("server", snippet)):
            r = subprocess.run([sys.executable, script, "--print-nginx", kind], cwd=_ROOT, env=env,
                               capture_output=True, text=True, timeout=300)
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            with open(path, "w") as fh:
                fh.write(r.stdout)
        user = "user root;\n" if os.geteuid() == 0 else ""
        with open(os.path.join(ngx, "conf", "nginx.conf"), "w") as fh:
            fh.write(f"""{user}worker_processes 1;
pid {ngx}/nginx.pid;
error_log {ngx}/logs/error.log;
events {{ worker_connections 256; }}
http {{
    include /etc/nginx/mime.types;
    default_type application/octet-stream;
    access_log off;
    client_body_temp_path {ngx}/tmp/b; proxy_temp_path {ngx}/tmp/p;
    fastcgi_temp_path {ngx}/tmp/f; uwsgi_temp_path {ngx}/tmp/u; scgi_temp_path {ngx}/tmp/s;
    gzip on; gzip_vary on; gzip_proxied any; gzip_comp_level 5; gzip_min_length 1024;
    gzip_types text/css text/plain application/javascript application/json;
    include {ngx}/conf/http.conf;
    server {{
        listen 127.0.0.1:{ngx_port};
        server_name _;
        include {snippet};
        location / {{
            proxy_pass http://127.0.0.1:{app_port};
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
        }}
    }}
}}
""")
        g = subprocess.Popen(
            [shutil.which("gunicorn"), "-w", "1", "--timeout", "120",
             "--pythonpath", ",".join(os.path.join(_ROOT, d) for d in ("racecast", "scripts", "engine")),
             "-b", f"127.0.0.1:{app_port}", "app:app"],
            cwd=_ROOT, env=dict(env, XCP_PRELOAD="0"),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        n = None
        try:
            n = subprocess.run(["nginx", "-p", ngx + "/", "-c", os.path.join(ngx, "conf", "nginx.conf")],
                               capture_output=True, text=True)
            self.assertEqual(n.returncode, 0, n.stderr)
            deadline = time.time() + 120
            while time.time() < deadline:
                try:
                    socket.create_connection(("127.0.0.1", app_port), timeout=1).close()
                    break
                except OSError:
                    time.sleep(0.5)
            r = subprocess.run([sys.executable, script], cwd=_ROOT, env=env,
                               capture_output=True, text=True, timeout=600)
            self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:])
            r = subprocess.run([sys.executable, script, "--verify", f"http://127.0.0.1:{ngx_port}"],
                               cwd=_ROOT, env=env, capture_output=True, text=True, timeout=600)
            self.assertEqual(r.returncode, 0, r.stdout[-4000:])
            self.assertRegex(r.stdout, r"\[verify\] (\d+) of \1 identical")
            # and a request nginx must not answer from a file is the app's
            import http.client
            c = http.client.HTTPConnection("127.0.0.1", ngx_port, timeout=60)
            c.request("GET", "/rankings", headers={"Host": "racecast.co", "X-Forwarded-Proto": "https",
                                                   "Cookie": "xcp_session=x"})
            resp = c.getresponse()
            resp.read()
            self.assertIsNotNone(resp.getheader("Content-Length"))     # the app, not a file
        finally:
            subprocess.run(["nginx", "-p", ngx + "/", "-c", os.path.join(ngx, "conf", "nginx.conf"),
                            "-s", "stop"], capture_output=True)
            g.terminate()
            g.wait(timeout=30)


if __name__ == "__main__":
    unittest.main()
