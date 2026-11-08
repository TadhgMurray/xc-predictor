# Project: xc-predictor / tests
# File:    test_edge_cache.py
# Purpose: one spelling per URL, and nothing per-reader at the edge
#          (racecast/edge_cache.py, static/board-url.js,
#          deploy/cloudflare/worker.js, scripts/edge_snapshot.py; 2026-11-08).
#
# ★ WHAT THIS PROTECTS. Cloudflare keys its cache on the URL byte for byte;
#   measured live, a reordered query, a trailing '&', an 'offset=0', a
#   utm_source or an fbclid were each a MISS beside a HIT. The canonical
#   spelling lives in three languages (Python for the app and the snapshot
#   publisher, the board page's JS, the edge Worker), and they must agree to
#   the byte or the Worker's key and the page's request are two URLs again.
#   And the rule that outranks every hit rate: a response that sets a cookie
#   or read the reader's session is never public.
import json
import os
import shutil
import subprocess
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")

import edge_cache as EC                                    # noqa: E402

flask = pytest.importorskip("flask")

_QUERIES = [
    "board=ability&pool=hs_m&sport=XC&scope=usa&scale=hs&limit=50&offset=0&sort=rating",
    "pool=hs_m&board=ability&sport=XC&scope=usa&scale=hs&limit=50&offset=0&sort=rating",
    "board=ability&pool=hs_m&sport=XC&scope=usa&scale=hs&limit=50&offset=0&sort=rating&",
    "board=pr&pool=all&sport=TF&distance=1600&offset=50&limit=50",
    "state=CA%2CTX&grade=9&grade=10&board=ability&utm_source=x&fbclid=abc",
    "q=o%27brien+smith&kind=athlete",
    "school=St.+Mary%27s+(CA)&board=performance&offset=0",
    "q=%E2%98%85star~*x",
    "b=2&a=1&b=1&a=3",
    "UTM_Campaign=z&gclid=1&_ga=2&srsltid=3&mc_cid=4&a=1",
    "",
]


def _py(qs):
    return EC.canonicalQuery(EC.parseQuery(qs))


# ---------------------------------------------------------------- the spec
def test_one_spelling_for_reordered_and_padded_queries():
    a, b, c = (_py(q) for q in _QUERIES[:3])
    assert a == b == c
    assert "offset" not in a
    assert a == "board=ability&limit=50&pool=hs_m&scale=hs&scope=usa&sort=rating&sport=XC"


def test_tracking_parameters_go_and_nothing_else_does():
    got = _py("state=CA%2CTX&grade=9&grade=10&board=ability&utm_source=x&fbclid=abc")
    assert got == "board=ability&grade=9&grade=10&state=CA%2CTX"
    assert _py("UTM_Campaign=z&gclid=1&_ga=2&srsltid=3&mc_cid=4&a=1") == "a=1"
    # a default only the server knows is KEPT (rankings.js: scope "including
    # the default"), and so is an empty value (sport= is a 400, absent is not)
    assert "scope=usa" in _py("scope=usa&board=ability")
    assert _py("sport=&board=ability") == "board=ability&sport="
    # page two is not page one
    assert "offset=50" in _py("board=ability&offset=50")


def test_repeated_names_keep_their_order():
    assert _py("b=2&a=1&b=1&a=3") == "a=1&a=3&b=2&b=1"


def test_encoding_is_the_browsers():
    """URLSearchParams.toString: space '+', '*' kept, '~' and ',' encoded."""
    assert EC.formEncode("a b*~,'") == "a+b*%7E%2C%27"


# --------------------------------------------- the JS copies agree, to the byte
_NODE = shutil.which("node")


def _node(script):
    out = subprocess.run([_NODE, "--input-type=module", "-e", script],
                         capture_output=True, text=True, timeout=60, cwd=_ROOT)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@pytest.mark.skipif(not _NODE, reason="node not installed")
def test_board_url_js_matches_the_python_spec():
    js = os.path.join(_ROOT, "racecast", "static", "board-url.js")
    got = _node(
        "import { createRequire } from 'module';"
        "const require = createRequire(import.meta.url);"
        f"const m = require({json.dumps(js)});"
        f"const qs = {json.dumps(_QUERIES)};"
        "console.log(JSON.stringify(qs.map(q => m.canonicalQs(q))));")
    assert got == [_py(q) for q in _QUERIES]


@pytest.mark.skipif(not _NODE, reason="node not installed")
def test_the_worker_matches_the_python_spec():
    js = os.path.join(_ROOT, "deploy", "cloudflare", "worker.js")
    got = _node(
        f"const m = await import({json.dumps('file://' + js)});"
        f"const qs = {json.dumps(_QUERIES)};"
        "console.log(JSON.stringify(qs.map(q => m.canonicalQs(q))));")
    assert got == [_py(q) for q in _QUERIES]


@pytest.mark.skipif(not _NODE, reason="node not installed")
def test_the_worker_serves_a_snapshot_and_falls_through_otherwise():
    js = os.path.join(_ROOT, "deploy", "cloudflare", "worker.js")
    got = _node(
        f"const m = await import({json.dumps('file://' + js)});"
        "const seen = [];"
        "globalThis.fetch = async (r) => { seen.push(typeof r === 'string' ? r : r.url);"
        "  return new Response('origin'); };"
        "const store = {'current': '7', 'g7:/api/rankings?board=ability&pool=hs_m':"
        "  {value: '{\"rows\":[1]}', metadata: {ct: 'application/json', cc: 'public, max-age=300'}}};"
        "const env = {SNAP: {get: async (k) => store[k] ?? null,"
        "  getWithMetadata: async (k) => store[k] ?? {value: null, metadata: null}}};"
        "const a = await m.default.fetch(new Request("
        "  'https://racecast.co/api/rankings?pool=hs_m&offset=0&board=ability&utm_source=t'), env);"
        "const b = await m.default.fetch(new Request("
        "  'https://racecast.co/api/rankings?pool=hs_f&board=ability'), env);"
        "const c = await m.default.fetch(new Request("
        "  'https://racecast.co/api/rankings?board=ability&pool=hs_m', {method: 'POST', body: 'x'}), env);"
        "console.log(JSON.stringify({a: await a.text(), acc: a.headers.get('cache-control'),"
        "  b: await b.text(), seen}));")
    assert got["a"] == '{"rows":[1]}'                  # any spelling -> the snapshot
    assert got["acc"] == "public, max-age=300"
    assert got["b"] == "origin"                        # not in the snapshot -> origin
    assert got["seen"][0] == "https://racecast.co/api/rankings?board=ability&pool=hs_f"
    assert got["seen"][1].endswith("/api/rankings?board=ability&pool=hs_m")   # POST untouched


def test_rankings_js_sends_every_board_request_canonical():
    src = open(os.path.join(_ROOT, "racecast", "static", "rankings.js"), encoding="utf-8").read()
    assert src.count("fetch(boardUrl(") >= 3
    for old in ('fetch(endpoint + "?"', 'fetch("/api/rankings?"', 'fetch("/api/rankings/rank?"'):
        assert old not in src, old
    tpl = open(os.path.join(_ROOT, "racecast", "templates", "rankings.html"), encoding="utf-8").read()
    assert tpl.index("static_v('board-url.js')") < tpl.index("static_v('rankings.js')")


# ----------------------------------- equivalent URLs, identical answers
def test_equivalent_board_urls_parse_to_the_same_filters():
    from werkzeug.datastructures import MultiDict
    import rankings as RK
    seen = []
    for qs in _QUERIES[:3] + ["sort=rating&sport=XC&scale=hs&scope=usa&pool=hs_m&board=ability&limit=50"]:
        f, err = RK.parseFilters(MultiDict(EC.parseQuery(qs)))
        assert err is None
        seen.append(f)
    assert all(f == seen[0] for f in seen)
    canon, _e = RK.parseFilters(MultiDict(EC.parseQuery(_py(_QUERIES[0]))))
    assert canon == seen[0]


def test_equivalent_board_urls_get_byte_identical_responses(monkeypatch):
    import contextlib
    import app as A
    import conversions
    import school_identity
    import school_units
    import ttlcache
    ttlcache.clear()

    class _Cur:
        def __enter__(self): return self
        def __exit__(self, *a): return False

    class _Conn:
        def cursor(self, **kw): return _Cur()
        def rollback(self): pass

    @contextlib.contextmanager
    def fake_conn():
        yield _Conn()

    def board(cur, f):
        return [{"person_id": 7, "name": "A Runner", "rating": 101.5, "pool": f["pool"],
                 "school": "X", "offset_seen": f["offset"], "sort_seen": f["sort"]}]

    monkeypatch.setattr(A, "getConn", fake_conn)
    monkeypatch.setattr(A, "getAbilityRankings", board)
    monkeypatch.setattr(A, "stampBoardRows", lambda rows, **kw: False)
    monkeypatch.setattr(A, "stampCrests", lambda rows, **kw: None)
    monkeypatch.setattr(conversions, "stampBoardClocks", lambda rows: None)
    monkeypatch.setattr(conversions, "stampFiveK", lambda rows, *a, **kw: None)
    monkeypatch.setattr(school_units, "applyUnitFilters", lambda cur, f, args: None)
    monkeypatch.setattr(school_identity, "teamState", lambda *a: None)
    monkeypatch.setattr(school_identity, "primaryState", lambda *a: None)

    client = A.app.test_client()
    bodies = []
    for qs in _QUERIES[:3] + [_py(_QUERIES[0])]:
        ttlcache.clear()                 # each answer computed, not replayed
        r = client.get("/api/rankings?" + qs)
        assert r.status_code == 200, r.get_data(as_text=True)
        assert "public" in r.headers["Cache-Control"]
        assert "Set-Cookie" not in r.headers
        bodies.append(r.get_data())
    assert len(set(bodies)) == 1


# --------------------------------------------- tracking parameters: a 301
def _client():
    import app as A
    return A, A.app.test_client()


def test_a_page_with_tracking_parameters_is_a_public_redirect_to_the_plain_page():
    A, c = _client()
    r = c.get("/robots.txt?utm_source=fb&fbclid=IwAR123")
    assert r.status_code == 301
    assert r.headers["Location"].endswith("/robots.txt")
    assert r.headers["Cache-Control"] == A.PUBLIC_CACHE_CONTROL
    r = c.get("/robots.txt?year=2025&utm_medium=x&src=2")
    assert r.status_code == 301
    # every other parameter as sent, the feed by NUMBER (CLAUDE.md)
    assert r.headers["Location"].endswith("/robots.txt?year=2025&src=2")
    assert c.get("/robots.txt").status_code == 200


def test_tracking_redirect_leaves_apis_and_sign_in_alone():
    assert EC.trackingRedirectTarget("/api/rankings", "board=ability&utm_source=x") is None
    assert EC.trackingRedirectTarget("/auth/google/callback", "code=1&utm_source=x") is None
    assert EC.trackingRedirectTarget("/athlete/5", "r=9") is None
    assert EC.trackingRedirectTarget("/athlete/5", "gclid=9") == "/athlete/5"


def test_a_redirect_names_the_feed_by_number():
    _A, c = _client()
    r = c.get("/robots.txt/?src=2")                 # the trailing-slash 301
    assert r.status_code == 301
    loc = r.headers["Location"]
    assert "src=2" in loc and "tfrrs" not in loc


# --------------------------------------- public lookups, private readers
def _hook(A, name):
    return next(f for f in A.app.after_request_funcs[None] if f.__name__ == name)


def test_every_listed_lookup_api_is_a_get_route_under_api():
    import app as A
    rules = {r.endpoint: r for r in A.app.url_map.iter_rules()}
    for ep in EC.PUBLIC_API_ENDPOINTS:
        assert ep in rules, ep
        assert "GET" in rules[ep].methods and rules[ep].rule.startswith("/api/"), ep
    for never in ("accounts.api_me", "follows.follow_state", "monitor.beacon"):
        assert never not in EC.PUBLIC_API_ENDPOINTS


def test_a_listed_lookup_answer_is_public_and_an_unlisted_one_is_not():
    import app as A
    hook = _hook(A, "_edgePublicApis")
    with A.app.test_request_context("/api/rankings/units?pool=hs_m"):
        resp = hook(A.app.response_class("{}", mimetype="application/json"))
        resp = A._headers(resp)
        assert resp.headers["Cache-Control"] == A.PUBLIC_CACHE_CONTROL
    with A.app.test_request_context("/api/rankings/units?pool=bad"):
        resp = A._headers(hook(A.app.response_class("{}", status=400)))
        assert "no-store" in resp.headers["Cache-Control"]
    with A.app.test_request_context("/api/me"):
        resp = A._headers(hook(A.app.response_class("{}", mimetype="application/json")))
        assert "no-store" in resp.headers["Cache-Control"]


def test_a_request_that_read_the_session_is_never_public():
    import app as A
    from flask import g
    hook = _hook(A, "_edgePublicApis")
    with A.app.test_request_context("/athlete/1"):
        g._xcp_session = None               # accounts.currentSession was asked
        resp = A._headers(hook(A.app.response_class("<p>")))
        assert resp.headers["Cache-Control"] == EC.PRIVATE


def test_a_cookie_makes_any_response_private():
    pub = "public, max-age=300, s-maxage=900"
    got = EC.guardHeaders([("Cache-Control", pub), ("Set-Cookie", "a=1")])
    assert ("Cache-Control", EC.PRIVATE) in got and ("Cache-Control", pub) not in got
    got = EC.guardHeaders([("Cache-Control", pub), ("Vary", "Accept-Encoding, Cookie")])
    assert ("Cache-Control", EC.PRIVATE) in got
    same = [("Cache-Control", pub), ("Vary", "Accept-Encoding")]
    assert EC.guardHeaders(same) == same
    nostore = [("Cache-Control", "no-store"), ("Set-Cookie", "a=1")]
    assert EC.guardHeaders(nostore) == nostore


def test_the_guard_sees_headers_added_after_every_hook():
    app = flask.Flask("probe")

    @app.route("/x")
    def x():
        r = flask.make_response("x")
        r.headers["Cache-Control"] = "public, s-maxage=900"
        return r

    @app.after_request
    def late(resp):                          # a hook that slips a cookie in
        resp.set_cookie("late", "1")
        return resp

    app.wsgi_app = EC._Guard(app.wsgi_app)
    r = app.test_client().get("/x")
    assert r.headers["Cache-Control"] == EC.PRIVATE


def test_no_cookie_or_cookie_vary_on_public_pages_that_need_no_database():
    _A, c = _client()
    for path in ("/robots.txt", "/manifest.webmanifest", "/google3e7b9f167371bc58.html"):
        r = c.get(path)
        assert r.status_code == 200, path
        assert "Set-Cookie" not in r.headers, path
        assert "cookie" not in (r.headers.get("Vary") or "").lower(), path
        assert "public" in r.headers["Cache-Control"], path


def test_the_updates_dot_carries_the_edge_ttl():
    src = open(os.path.join(_ROOT, "racecast", "app.py"), encoding="utf-8").read()
    body = src[src.index('@app.route("/api/updates/latest")'):][:1400]
    assert 'resp.headers["Cache-Control"] = PUBLIC_CACHE_CONTROL' in body


# --------------------------------------------------- the snapshot publisher
_LOG = [
    '1.2.3.4 - - [08/Nov/2026:03:00:00 +0000] "GET /api/rankings?pool=hs_m&board=ability&offset=0 HTTP/1.1" 200 100 "-" "UA"',
    '1.2.3.4 - - [08/Nov/2026:03:00:01 +0000] "GET /api/rankings?board=ability&pool=hs_m HTTP/1.1" 200 100 "-" "UA"',
    '1.2.3.4 - - [08/Nov/2026:03:00:02 +0000] "GET /api/rankings?board=ability&pool=hs_f HTTP/1.1" 200 100 "-" "UA"',
    '1.2.3.4 - - [08/Nov/2026:03:00:03 +0000] "GET /athlete/5 HTTP/1.1" 200 100 "-" "UA"',
    '1.2.3.4 - - [08/Nov/2026:03:00:04 +0000] "POST /api/b HTTP/1.1" 204 0 "-" "UA"',
    '1.2.3.4 - - [08/Nov/2026:03:00:05 +0000] "GET /search/api?q=sm HTTP/1.1" 200 100 "-" "UA"',
]


def test_snapshot_keys_are_the_canonical_spelling_ranked_by_use():
    import edge_snapshot as S
    keys = S.routeRequests(_LOG)
    assert keys.count("/api/rankings?board=ability&pool=hs_m") == 2
    assert S.topKeys(keys, 1) == ["/api/rankings?board=ability&pool=hs_m"]


def test_the_estimate_counts_what_the_snapshot_would_answer():
    import edge_snapshot as S
    est = S.estimate(_LOG, ["/api/rankings?board=ability&pool=hs_m"])
    assert est["origin_requests"] == 6
    assert est["route_requests"] == 4
    assert est["snapshot_answers"] == 2
    assert est["respelled_by_canonical"] == 1
    assert est["distinct_raw_urls"] == 4 and est["distinct_route_urls"] == 3


def test_publish_is_a_noop_without_credentials_and_flips_the_pointer_last(capsys):
    import edge_snapshot as S
    assert S.publish([("/api/rankings?a=1", b"{}", "application/json", "public")], env={}) is None
    calls = []

    class _R:
        def __init__(self): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b'{"success": true}'

    def opener(req, timeout=0):
        calls.append((req.get_method(), req.full_url.rsplit("/", 2)[-1], req.data))
        return _R()

    env = {"XCP_CF_ACCOUNT_ID": "a", "XCP_CF_KV_NAMESPACE": "n", "XCP_CF_KV_TOKEN": "t"}
    assert S.publish([("/api/rankings?a=1", b'{"x":1}', "application/json", "public")],
                     env=env, opener=opener, gen=42) is True
    assert calls[0][1] == "bulk" and calls[-1][1:] == ("current", b"42")
    pair = json.loads(calls[0][2])[0]
    assert pair["key"] == "g42:/api/rankings?a=1" and pair["value"] == '{"x":1}'
    assert pair["metadata"]["cc"] == "public"


def test_the_snapshot_user_agent_is_the_sites_own():
    import edge_snapshot as S
    assert S.UA.endswith("(racecast@racecast.co)")


def test_nginx_may_keep_only_public_cookie_free_200s():
    """deploy/nginx_microcache.sh: the app's X-Accel-Expires decides."""
    import edge_cache as E
    pub = [("Cache-Control", "public, max-age=300, s-maxage=900")]
    assert E.microcacheHeader("200 OK", pub) == str(E.MICRO_TTL)
    assert E.microcacheHeader("404 NOT FOUND", pub) == "0"
    assert E.microcacheHeader("301 MOVED PERMANENTLY", pub) == "0"
    assert E.microcacheHeader("200 OK", pub + [("Set-Cookie", "a=b")]) == "0"
    assert E.microcacheHeader("200 OK", pub + [("Vary", "Cookie")]) == "0"
    assert E.microcacheHeader("200 OK", [("Cache-Control", "private, no-store")]) == "0"
    assert E.microcacheHeader("200 OK", [("Cache-Control", "no-cache")]) == "0"
    assert E.microcacheHeader("200 OK", []) == "0"


def test_every_response_carries_the_nginx_instruction():
    import app as A
    c = A.app.test_client()
    r = c.get("/robots.txt", base_url="https://racecast.co")
    assert r.headers.get("X-Accel-Expires") in ("0", str(__import__("edge_cache").MICRO_TTL))
    r = c.get("/api/me", base_url="https://racecast.co")
    assert r.headers.get("X-Accel-Expires") == "0"
