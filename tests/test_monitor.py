"""Usage counts and error monitoring (owner, 2026-10-10).

    python -m pytest -q tests/test_monitor.py
    node tests/test_usage_beacon.js          # the browser half: DNT / GPC

Stub cursors only: no Postgres here. Path normalisation, the beacon's
filters, aggregation into counts, fingerprints, and the batched mail.
"""
import contextlib
import datetime
import os
import re
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for d in ("scripts", "engine", "racecast"):
    sys.path.insert(0, os.path.join(ROOT, d))
if "database" not in sys.modules:
    _db = types.ModuleType("database")

    @contextlib.contextmanager
    def _noConn():
        yield None
    _db.getConn = _noConn
    _db.initPool = lambda *a, **k: None
    sys.modules["database"] = _db

import pytest                                                    # noqa: E402
flask = pytest.importorskip("flask")
import usage as U                                                # noqa: E402
import app_errors as E                                           # noqa: E402
import monitor as M                                              # noqa: E402

UTC = datetime.timezone.utc


class Cur:
    """Records statements; answers fetches from a list of (pattern, rows)."""
    def __init__(self, answers=()):
        self.answers = list(answers)
        self.sql, self.many = [], []
        self.rows = []
        self.rowcount = 1

    def execute(self, sql, params=None):
        self.sql.append((sql, params))
        self.rows = []
        for pat, rows in self.answers:
            if re.search(pat, sql, re.S):
                self.rows = list(rows)
                break

    def executemany(self, sql, seq):
        self.many.append((sql, list(seq)))

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Conn:
    def __init__(self, cur, fail=False):
        self.cur, self.fail = cur, fail
        self.commits = self.rollbacks = 0

    def cursor(self, *a, **k):
        if self.fail:
            raise RuntimeError("database is down")
        return self.cur

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def site():
    app = flask.Flask(__name__)
    for rule in ("/", "/athlete/<int:person_id>", "/race/xc/<int:meet_id>/<int:div_id>",
                 "/school/<path:school_name>", "/rankings/<sport>/<pool>"):
        app.add_url_rule(rule, rule, lambda **k: "ok")
    app.add_url_rule("/api/x", "apix", lambda: "ok")
    return app


# ---------------------------------------------------------------- paths

def test_route_templates_hide_every_id_and_name():
    assert U.normalizeRule("/athlete/<int:person_id>") == "/athlete/:person_id"
    assert U.normalizeRule("/race/xc/<int:meet_id>/<int:div_id>") == "/race/xc/:meet_id/:div_id"
    assert U.normalizeRule("/school/<path:school_name>/prs") == "/school/:school_name/prs"
    assert U.normalizeRule("/rankings/<sport>/<pool>") == "/rankings/:sport/:pool"
    assert U.normalizeRule("") == "" and U.normalizeRule("athlete/1") == ""


def test_only_the_sites_own_routes_are_stored():
    known = U.knownTemplates(site().url_map)
    assert "/athlete/:person_id" in known and "/" in known
    assert not any(t.startswith("/api/") for t in known)
    assert U.pathTemplate("/athlete/<int:person_id>", known) == "/athlete/:person_id"
    # a real path, a made-up route, junk: never stored as given
    for raw in ("/athlete/12345", "/evil/<int:x>", "<script>", None, 7):
        assert U.pathTemplate(raw, known) == U.UNKNOWN_TPL


def test_referrer_is_a_site_never_a_url():
    assert U.refHost("https://www.google.com/search?q=jane+doe") == "google.com"
    assert U.refHost("https://l.facebook.com/l.php?u=x") == "facebook.com"
    assert U.refHost("news.bbc.co.uk") == "bbc.co.uk"
    assert U.refHost("t.co") == "t.co"
    assert U.refHost("https://racecast.co/athlete/1", "racecast.co") is None
    assert U.refHost("www.racecast.co", "racecast.co") is None
    assert U.refHost("") is None and U.refHost("not a host!") is None


def test_bots_and_opt_outs_are_dropped():
    assert U.isBot("Mozilla/5.0 (compatible; Googlebot/2.1)")
    assert U.isBot("python-requests/2.31") and U.isBot("") and U.isBot(None)
    assert U.isBot("Mozilla/5.0 HeadlessChrome/120.0")
    assert not U.isBot("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0) Safari/604.1")
    assert U.beaconOptedOut({"DNT": "1"}) and U.beaconOptedOut({"Sec-GPC": "1"})
    assert not U.beaconOptedOut({"DNT": "0"}) and not U.beaconOptedOut({})


def test_beacon_parsing_keeps_only_known_names():
    known = U.knownTemplates(site().url_map)
    view = U.parseBeacon({"k": "v", "t": "/athlete/<int:person_id>", "s": "p",
                          "r": "www.google.com"}, known, "racecast.co")
    assert view == [("/athlete/:person_id", "view", "phone"),
                    ("/athlete/:person_id", "ref:google.com", "phone")]
    ev = U.parseBeacon({"k": "e", "t": "/", "s": "d",
                        "e": ["predict:run", "drop table", "follow:click", 3]}, known)
    assert ev == [("/", "predict:run", "desktop"), ("/", "follow:click", "desktop")]
    assert U.parseBeacon({"k": "e", "e": ["predict:run"] * 500}, known).__len__() == U.MAX_EVENTS_PER_BEACON
    assert U.parseBeacon("x", known) == [] and U.parseBeacon({"k": "x"}, known) == []


# ---------------------------------------------------------------- aggregation

def test_counts_aggregate_and_flush_as_one_upsert_per_key():
    buf = U.Counts()
    day = datetime.date(2026, 10, 10)
    for _ in range(5):
        buf.add([("/", "view", "phone")], day)
    buf.add([("/", "view", "desktop"), ("/", "predict:run", "desktop")], day)
    cur = Cur()
    conn = Conn(cur)
    assert U.flush(conn, buf) == 3 and conn.commits == 1 and len(buf) == 0
    sql, rows = cur.many[0]
    assert "ON CONFLICT (day, path_template, event, screen)" in sql
    assert "n = usage_daily.n + excluded.n" in sql
    assert sorted(rows) == sorted([(day, "/", "view", "phone", 5),
                                   (day, "/", "view", "desktop", 1),
                                   (day, "/", "predict:run", "desktop", 1)])
    assert U.flush(conn, buf) == 0            # nothing held: no statement


def test_a_failed_flush_keeps_the_counts():
    buf = U.Counts()
    buf.add([("/", "view", "phone")] * 3)
    with pytest.raises(RuntimeError):
        U.flush(Conn(Cur(), fail=True), buf)
    assert buf.take()[0][-1] == 3


def test_the_buffer_is_capped():
    buf = U.Counts(max_keys=2)
    buf.add([("/a", "view", "phone"), ("/b", "view", "phone"), ("/c", "view", "phone")])
    assert len(buf) == 2 and buf.dropped == 1


def test_no_per_hit_rows_and_no_personal_columns():
    ddl = U.DDL.lower()
    for col in ("ip", "user_agent", "session", "cookie", "visitor", "email"):
        assert not re.search(rf"\b{col}\b", ddl), col
    assert "primary key (day, path_template, event, screen)" in ddl


# ---------------------------------------------------------------- fingerprints

def _boom(kind):
    try:
        if kind == "attr":
            E.fingerprintKey(123)             # AttributeError inside app_errors
        else:
            U.Counts().putBack([(1,)])          # ValueError inside usage
    except Exception as exc:                  # noqa: BLE001
        return exc


def test_fingerprint_is_type_plus_innermost_app_frame():
    a, b = _boom("attr"), _boom("attr")
    fa = E.appFrame(a.__traceback__)
    assert fa[0] == "racecast/app_errors.py" and fa[1] == "fingerprintKey"
    assert E.pyFingerprint("AttributeError", fa) == E.pyFingerprint("AttributeError",
                                                                    E.appFrame(b.__traceback__))
    key, title = E.pyFingerprint("AttributeError", fa)
    assert title == "AttributeError @ racecast/app_errors.py:fingerprintKey" and len(key) == 16
    # the line number is not part of it: an edit above does not make it new
    moved = (fa[0], fa[1], fa[2] + 40)
    assert E.pyFingerprint("AttributeError", moved)[0] == key
    # another type, or another function, is another group
    assert E.pyFingerprint("KeyError", fa)[0] != key
    c = _boom("index")
    assert E.pyFingerprint("ValueError", E.appFrame(c.__traceback__))[0] != key
    assert E.appFrame(c.__traceback__)[:2] == ("racecast/usage.py", "putBack")


def test_record_exception_groups_and_scrubs():
    buf = E.Groups()
    for _ in range(3):
        assert E.recordException(_boom("attr"), "/athlete/:person_id", buf)
    got = buf.take()
    assert len(got) == 1
    (g,) = got.values()
    assert g["count"] == 3 and g["path"] == "/athlete/:person_id"
    assert "Traceback" in g["detail"] and g["kind"] == "py"
    assert E.scrub("for jane.doe@example.com token=abc password=hunter2") == \
        "for <email> token=abc password=<redacted>"
    assert "<token>" in E.scrub("sid " + "a1b2c3" * 8)
    assert E.scrub("a_very_long_function_name_without_digits_in_it") == \
        "a_very_long_function_name_without_digits_in_it"
    long = "x" * 20000
    assert len(E.trimTraceback(long)) <= E.TB_LIMIT


def test_js_errors_only_from_our_own_scripts():
    ok = E.parseJsError({"k": "x", "x": {"m": "x is undefined", "f": "/static/rankings.js", "l": 812}})
    assert ok == ("x is undefined", "/static/rankings.js", 812)
    for bad in ({"m": "Script error.", "f": "/static/a.js", "l": 0},
                {"m": "boom", "f": "chrome-extension://abc/x.js", "l": 1},
                {"m": "boom", "f": "https://evil.example/x.js", "l": 1},
                {"m": "", "f": "/static/a.js", "l": 1}):
        assert E.parseJsError({"k": "x", "x": bad}) is None
    assert E.jsFingerprint("row 12 missing", "/static/a.js", 5)[0] == \
        E.jsFingerprint("row 99 missing", "/static/a.js", 5)[0]


def test_slow_line_comes_from_the_statement_timeout():
    assert E.slowRequestMs({"XCP_DB_STATEMENT_TIMEOUT_MS": "55000"}) == 27500
    assert E.slowRequestMs({}) == 27500
    assert E.slowRequestMs({"XCP_DB_STATEMENT_TIMEOUT_MS": "20000"}) == 10000
    assert E.slowRequestMs({"XCP_SLOW_REQUEST_MS": "8000",
                            "XCP_DB_STATEMENT_TIMEOUT_MS": "55000"}) == 8000


def test_error_upsert_reopens_a_resolved_group():
    sql = " ".join(E.UPSERT.split())
    assert "count = app_error.count + excluded.count" in sql
    assert "notify_pending = app_error.notify_pending OR app_error.resolved_at IS NOT NULL" in sql
    assert "resolved_at = NULL" in sql
    buf = E.Groups()
    E.recordJs("boom", "/static/a.js", 3, "/", buf)
    cur = Cur([(r"INSERT INTO app_error", [{"notify_pending": True}])])
    n, pending = E.flush(Conn(cur), buf)
    assert n == 1 and pending is True


# ---------------------------------------------------------------- the mail

T0 = datetime.datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
ROWS = [{"fingerprint": "a" * 16, "kind": "py", "title": "KeyError @ racecast/app.py:x",
         "count": 4, "last_path": "/", "last_traceback": "Traceback...\nKeyError: 'k'",
         "first_seen": T0, "reopened_at": None},
        {"fingerprint": "b" * 16, "kind": "js", "title": "JS boom @ /static/a.js:3",
         "count": 1, "last_path": "/", "last_traceback": "boom", "first_seen": T0,
         "reopened_at": T0}]


def test_window_rule():
    assert E.dueToSend(None, T0)
    assert not E.dueToSend(T0 - datetime.timedelta(minutes=14), T0)
    assert E.dueToSend(T0 - E.NOTIFY_WINDOW, T0)


def test_one_mail_per_batch_never_per_hit():
    sent = []
    send = lambda a, s, t: sent.append((a, s, t)) or True      # noqa: E731
    cur = Cur([(r"FROM app_error_notify", [{"last_sent_at": None}]),
               (r"FROM app_error WHERE notify_pending", ROWS)])
    conn = Conn(cur)
    assert E.notifyPending(conn, send, ["owner@example.com"], now=T0) == "sent"
    assert len(sent) == 1
    subject, text = sent[0][1], sent[0][2]
    assert "1 new" in subject and "1 back after resolved" in subject
    assert "[NEW] KeyError" in text and "[BACK] JS boom" in text
    marked = [p for s, p in cur.sql if s.startswith("UPDATE app_error SET notify_pending = false")]
    assert marked and set(marked[0][0]) == {"a" * 16, "b" * 16}
    assert any("UPDATE app_error_notify SET last_sent_at" in s for s, _ in cur.sql)


def test_inside_the_window_it_waits_and_sends_nothing():
    sent = []
    cur = Cur([(r"FROM app_error_notify", [{"last_sent_at": T0 - datetime.timedelta(minutes=3)}]),
               (r"FROM app_error WHERE notify_pending", ROWS)])
    assert E.notifyPending(Conn(cur), lambda *a: sent.append(a), ["o@x.co"], now=T0) == "wait"
    assert not sent and not any(s.startswith("UPDATE") for s, _ in cur.sql)


def test_another_worker_holding_the_notifier_means_no_second_mail():
    cur = Cur([(r"FROM app_error_notify", [])])            # SKIP LOCKED: no row
    assert E.notifyPending(Conn(cur), lambda *a: 1 / 0, ["o@x.co"], now=T0) == "busy"


def test_a_failed_send_puts_the_groups_back():
    cur = Cur([(r"FROM app_error_notify", [{"last_sent_at": None}]),
               (r"FROM app_error WHERE notify_pending", ROWS)])
    assert E.notifyPending(Conn(cur), lambda *a: False, ["o@x.co"], now=T0) == "failed"
    assert any(s.startswith("UPDATE app_error SET notify_pending = true") for s, _ in cur.sql)


def test_nothing_waiting_sends_nothing():
    cur = Cur([(r"FROM app_error_notify", [{"last_sent_at": None}])])
    assert E.notifyPending(Conn(cur), lambda *a: 1 / 0, ["o@x.co"], now=T0) == "none"


# ---------------------------------------------------------------- the wiring

@pytest.fixture
def client(monkeypatch):
    app = site()
    app.config["TESTING"] = False
    app.config["PROPAGATE_EXCEPTIONS"] = False

    @app.route("/explode")
    def explode():
        return {}["missing"]

    monkeypatch.setattr(M, "_ensureThread", lambda: None)
    monkeypatch.setitem(M._STATE, "known", None)
    monkeypatch.setattr(U, "BUFFER", U.Counts())
    monkeypatch.setattr(E, "BUFFER", E.Groups())
    M.install(app, lambda: (None, flask.redirect("/login")))
    return app.test_client()


UA = "Mozilla/5.0 (Windows NT 10.0) Chrome/120.0 Safari/537.36"
HDR = {"Origin": "http://localhost", "User-Agent": UA}
VIEW = b'{"k":"v","t":"/athlete/<int:person_id>","s":"p","r":"www.google.com"}'


def test_beacon_counts_sets_no_cookie_and_answers_204(client):
    r = client.post("/api/b", data=VIEW, headers=HDR, content_type="text/plain")
    assert r.status_code == 204 and not r.data
    assert "Set-Cookie" not in r.headers
    rows = sorted(U.BUFFER.take())
    assert [(t, e, s, n) for _, t, e, s, n in rows] == [
        ("/athlete/:person_id", "ref:google.com", "phone", 1),
        ("/athlete/:person_id", "view", "phone", 1)]


@pytest.mark.parametrize("extra", [{"DNT": "1"}, {"Sec-GPC": "1"},
                                   {"User-Agent": "Googlebot/2.1"},
                                   {"Origin": "https://evil.example"}])
def test_beacon_drops_opt_outs_bots_and_other_sites(client, extra):
    r = client.post("/api/b", data=VIEW, headers={**HDR, **extra}, content_type="text/plain")
    assert r.status_code == 204 and len(U.BUFFER) == 0


def test_beacon_never_fails_on_junk(client):
    for body in (b"{", b"null", b"[]", b'{"k":"e","e":"x"}', b"x" * 5000):
        assert client.post("/api/b", data=body, headers=HDR).status_code == 204
    assert len(U.BUFFER) == 0


def test_js_error_beacon_is_grouped(client):
    body = b'{"k":"x","t":"/","x":{"m":"a is null","f":"/static/rankings.js","l":9}}'
    client.post("/api/b", data=body, headers=HDR)
    (g,) = E.BUFFER.take().values()
    assert g["kind"] == "js" and g["path"] == "/"


def test_unhandled_exception_is_recorded_without_touching_the_db(client):
    r = client.get("/explode")
    assert r.status_code == 500
    (g,) = E.BUFFER.take().values()
    assert g["kind"] == "py" and g["title"].startswith("KeyError @ ")
    assert g["path"] == "/explode"


def test_admin_pages_are_gated(client):
    assert client.get("/account/status/usage").status_code == 302
    assert client.get("/account/status/errors").status_code == 302
    assert client.post("/account/status/errors/resolve").status_code == 302


def test_flush_survives_a_dead_database(monkeypatch):
    @contextlib.contextmanager
    def dead():
        raise RuntimeError("could not connect")
        yield                                                    # noqa
    monkeypatch.setitem(sys.modules, "database", types.SimpleNamespace(getConn=dead))
    monkeypatch.setattr(U, "BUFFER", U.Counts())
    U.BUFFER.add([("/", "view", "phone")])
    M.flushNow()                                                 # must not raise
    assert len(U.BUFFER) == 1


def test_tables_come_from_init_never_a_request():
    src = open(os.path.join(ROOT, "racecast", "monitor.py"), encoding="utf-8").read()
    calls = [m.start() for m in re.finditer(r"(?<!def )initTables\(conn\)", src)]
    assert len(calls) == 1 and calls[0] > src.index("def main(")
    topbar = open(os.path.join(ROOT, "racecast", "templates", "_topbar.html"), encoding="utf-8").read()
    assert topbar.count("static_v('usage.js')") == 1
