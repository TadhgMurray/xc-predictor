"""Accounts (283), the first cut: the pure pieces, and the wiring by source.

    python -m pytest -q tests/test_accounts.py
"""
import contextlib
import io
import os
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
import accounts as AC                                            # noqa: E402


def read(*p):
    return io.open(os.path.join(ROOT, *p), encoding="utf-8").read()


def test_emails_are_normalised_and_checked():
    assert AC.normEmail("  Kid@Example.COM ") == "kid@example.com"
    assert AC.normEmail("nope") is None and AC.normEmail("") is None and AC.normEmail(None) is None
    assert AC.normEmail("a@b") is None and AC.normEmail("a b@c.d") is None
    assert AC.normEmail("x@" + "y" * 250 + ".com") is None


def test_next_is_a_path_on_this_site_only():
    assert AC.safeNext("/recruiting?athlete=7") == "/recruiting?athlete=7"
    assert AC.safeNext("https://evil.com/") == "/account"
    assert AC.safeNext("//evil.com/") == "/account"
    assert AC.safeNext("/\\evil.com") == "/account"
    assert AC.safeNext("/login?next=/x") == "/account"          # never back into the flow
    assert AC.safeNext("/auth/google/callback") == "/account"
    assert AC.safeNext("") == "/account" and AC.safeNext(None, "/") == "/"


def test_tokens_are_random_and_only_their_hash_is_stored():
    raw, h = AC.newToken()
    raw2, h2 = AC.newToken()
    assert raw != raw2 and len(raw) >= 40 and len(h) == 64 and h == AC.hashToken(raw)
    assert "token_hash" in AC.DDL and "sid_hash" in AC.DDL
    for t in ("account", "login_token", "account_session", "account_claim", "auth_event"):
        assert f"CREATE TABLE IF NOT EXISTS {t} (" in AC.DDL
    assert "ON DELETE CASCADE" in AC.DDL           # deleting an account takes its sessions and claims


def test_claim_forms():
    row, err = AC.parseClaimForm({"kind": "athlete", "person_id": "7"})
    assert err is None and row == {"kind": "athlete", "person_id": 7, "school": None, "state": None, "level": None}
    assert AC.parseClaimForm({"kind": "athlete", "person_id": "x"})[1]
    assert AC.parseClaimForm({"kind": "athlete", "person_id": "0"})[1]
    assert AC.parseClaimForm({"kind": "what"})[1]
    row, err = AC.parseClaimForm({"kind": "coach_team", "school": "Tufts", "state": "ma", "level": "college"})
    assert err is None and row["school"] == "Tufts" and row["state"] == "MA" and row["level"] == "college"
    row, err = AC.parseClaimForm({"kind": "coach_team", "school": "Tufts", "state": "", "level": "hs"})
    assert err is None and row["state"] is None
    assert AC.parseClaimForm({"kind": "coach_team", "school": "", "level": "hs"})[1]
    assert AC.parseClaimForm({"kind": "coach_team", "school": "T", "state": "Mass", "level": "hs"})[1]
    assert AC.parseClaimForm({"kind": "coach_team", "school": "T", "level": "pro"})[1]
    row, err = AC.parseClaimForm({"kind": "coach_self", "person_id": "12"})
    assert err is None and row["kind"] == "coach_self" and row["person_id"] == 12


def test_csrf_needs_the_token_and_this_origin():
    app = flask.Flask(__name__)
    sess = {"csrf": "abc123"}
    with app.test_request_context("/account/role", method="POST", data={"csrf": "abc123"},
                                  headers={"Origin": "http://localhost"}):
        assert AC.csrfOk(sess)
    with app.test_request_context("/account/role", method="POST", data={"csrf": "abc123"},
                                  headers={"Origin": "http://evil.com"}):
        assert not AC.csrfOk(sess)
    with app.test_request_context("/account/role", method="POST", data={"csrf": "abc123"}):
        assert not AC.csrfOk(sess)                      # no Origin and no Referer
    with app.test_request_context("/account/role", method="POST", data={"csrf": "wrong"},
                                  headers={"Origin": "http://localhost"}):
        assert not AC.csrfOk(sess)
    with app.test_request_context("/account/role", method="POST", data={"csrf": "abc123"},
                                  headers={"Referer": "http://localhost/account"}):
        assert AC.csrfOk(sess)
    assert not AC.csrfOk(None)


def test_client_ip_trusts_only_nginx_s_header():
    # a visitor can send CF-Connecting-IP or X-Forwarded-For themselves;
    # only X-Real-IP, which nginx overwrites, names them
    app = flask.Flask(__name__)
    with app.test_request_context("/", headers={"X-Real-IP": "1.2.3.4", "CF-Connecting-IP": "6.6.6.6",
                                                "X-Forwarded-For": "5.6.7.8, 9.9.9.9"}):
        assert AC.clientIp() == "1.2.3.4"
    with app.test_request_context("/", headers={"CF-Connecting-IP": "6.6.6.6", "X-Forwarded-For": "5.6.7.8"},
                                  environ_base={"REMOTE_ADDR": "127.0.0.1"}):
        assert AC.clientIp() == "127.0.0.1"
    with app.test_request_context("/", headers={"X-Forwarded-Proto": "https"}):
        assert AC.isSecure()


def test_features_are_off_until_configured(monkeypatch):
    for k in ("XCP_MAIL_PROVIDER", "XCP_MAIL_KEY", "XCP_GOOGLE_CLIENT_ID", "XCP_GOOGLE_CLIENT_SECRET",
              "XCP_TURNSTILE_SITEKEY", "XCP_TURNSTILE_SECRET", "XCP_ADMIN_EMAILS"):
        monkeypatch.delenv(k, raising=False)
    assert not AC.mailEnabled() and not AC.googleEnabled() and AC.turnstileSitekey() == ""
    assert not AC.isAdmin({"email": "a@b.com"})
    monkeypatch.setenv("XCP_MAIL_PROVIDER", "resend")
    monkeypatch.setenv("XCP_MAIL_KEY", "k")
    monkeypatch.setenv("XCP_GOOGLE_CLIENT_ID", "id")
    monkeypatch.setenv("XCP_GOOGLE_CLIENT_SECRET", "s")
    monkeypatch.setenv("XCP_TURNSTILE_SITEKEY", "site")
    monkeypatch.setenv("XCP_TURNSTILE_SECRET", "sec")
    monkeypatch.setenv("XCP_ADMIN_EMAILS", "Owner@Racecast.co, x@y.z")
    assert AC.mailEnabled() and AC.googleEnabled() and AC.turnstileSitekey() == "site"
    assert AC.isAdmin({"email": "owner@racecast.co"}) and not AC.isAdmin({"email": "a@b.com"})
    subject, text = AC.loginMail("https://racecast.co/login/t/abc")
    assert "https://racecast.co/login/t/abc" in text and str(AC.TOKEN_MINUTES) in text


def test_the_wiring():
    app = read("racecast", "app.py")
    assert "app.register_blueprint(_accounts.bp)" in app
    assert '"/login", "/logout", "/auth/", "/account"' in app       # never cached at the edge
    top = read("racecast", "templates", "_topbar.html")
    assert 'id="topbar-account"' in top and 'href="/login"' in top
    js = read("racecast", "static", "topbar-search.js")
    assert "fetch('/api/me'" in js and "xcp:me" in js and "data-person-id" not in js or "personId" in js
    assert 'data-person-id="{{ athlete.person_id }}"' in read("racecast", "templates", "athlete.html")
    assert "fromAccount" in read("racecast", "static", "recruiting.js")
    for tpl in ("login.html", "account.html"):
        assert 'name="csrf"' in read("racecast", "templates", tpl) or tpl == "login.html"
    login = read("racecast", "templates", "login.html")
    assert 'name="age_ok"' in login and 'method="post"' in login
    assert "account.js" in read("racecast", "templates", "account.html")
    assert "XCP_GOOGLE_CLIENT_ID" in read("deploy", "server_setup.sh")
    for route in ('@bp.route("/login")', '@bp.route("/login/t/<token>", methods=["POST"])',
                  '@bp.route("/auth/google/callback")', '@bp.route("/logout", methods=["POST"])',
                  '@bp.route("/account")', '@bp.route("/account/claim", methods=["POST"])',
                  '@bp.route("/account/delete", methods=["POST"])', '@bp.route("/api/me")'):
        assert route in read("racecast", "accounts.py"), route


def test_the_picture_is_re_encoded_square_and_stripped(tmp_path):
    PIL = pytest.importorskip("PIL")
    from PIL import Image
    import io as _io
    buf = _io.BytesIO()
    img = Image.new("RGB", (1200, 900), (10, 120, 200))
    img.save(buf, "JPEG", exif=Image.Exif() if hasattr(Image, "Exif") else b"")
    jpeg, w, h = AC.processPhoto(buf.getvalue())
    assert (w, h) == (AC.PHOTO_SIZE, AC.PHOTO_SIZE)          # square, shrunk to the cap
    out = Image.open(_io.BytesIO(jpeg))
    assert out.format == "JPEG" and out.size == (512, 512) and not out.getexif()
    small = _io.BytesIO(); Image.new("RGB", (300, 200)).save(small, "PNG")
    assert AC.processPhoto(small.getvalue())[1:] == (200, 200)   # under the cap: cropped, not enlarged
    tall = _io.BytesIO(); Image.new("RGB", (300, 900)).save(tall, "PNG")
    assert AC.processPhoto(tall.getvalue())[1:] == (300, 300)
    tiny = _io.BytesIO(); Image.new("RGB", (40, 40)).save(tiny, "PNG")
    with pytest.raises(AC.AccountsError):
        AC.processPhoto(tiny.getvalue())
    with pytest.raises(AC.AccountsError):
        AC.processPhoto(b"not an image at all")
    with pytest.raises(AC.AccountsError):
        AC.processPhoto(b"")
    with pytest.raises(AC.AccountsError):
        AC.processPhoto(b"x" * (AC.MAX_PHOTO_BYTES + 1))
    assert AC.photoUrl("1-abc.jpg") == "/static/photos/1-abc.jpg" and AC.photoUrl(None) is None


def test_the_picture_is_wired():
    src = read("racecast", "accounts.py")
    assert "CREATE TABLE IF NOT EXISTS account_photo (" in AC.DDL
    for route in ('@bp.route("/account/photo", methods=["POST"])', '@bp.route("/account/name", methods=["POST"])'):
        assert route in src, route
    assert 'f.read(MAX_PHOTO_BYTES + 1)' in src            # never the whole upload into memory
    assert "photo = _accounts.photoFor(cur, person_id)" in read("racecast", "app.py")
    ath = read("racecast", "templates", "athlete.html")
    assert 'id="ath-avatar"' in ath and "ath-avatar-empty" in ath and "{{ athlete.photo }}" in ath
    assert "ath-avatar-add" in read("racecast", "static", "topbar-search.js")
    acct = read("racecast", "templates", "account.html")
    assert 'enctype="multipart/form-data"' in acct and 'action="/account/name"' in acct and "<h1>Settings</h1>" in acct
    assert "racecast/static/photos/" in read(".gitignore")


def test_the_mail_never_holds_the_page_or_the_transaction(monkeypatch):
    """(owner, 2026-10-03: "the sign in page can hang"). The send waits at
    most MAIL_WAIT_SECONDS and happens after the token is committed, never
    inside requestLink's transaction."""
    import time
    calls = []

    def slow(to, subject, text):
        time.sleep(1.0)
        calls.append(to)
        return True
    monkeypatch.setattr(AC, "sendMail", slow)
    t = time.time()
    assert AC.sendMailWithin("a@b.co", "s", "t", seconds=0.1) == "slow"
    assert time.time() - t < 0.6                               # answered, not waited out
    monkeypatch.setattr(AC, "sendMail", lambda *a: True)
    assert AC.sendMailWithin("a@b.co", "s", "t", seconds=2) == "sent"
    monkeypatch.setattr(AC, "sendMail", lambda *a: False)
    assert AC.sendMailWithin("a@b.co", "s", "t", seconds=2) == "failed"

    class Cur:
        def __init__(self):
            self.sql = []

        def execute(self, sql, args=None):
            self.sql.append(sql)

        def fetchone(self):
            return {"n": 0}
    monkeypatch.setenv("XCP_MAIL_PROVIDER", "resend")
    monkeypatch.setenv("XCP_MAIL_KEY", "k")
    monkeypatch.setattr(AC, "sendMail", lambda *a: pytest.fail("mailed inside the transaction"))
    app = flask.Flask(__name__)
    with app.test_request_context("/login", method="POST"):
        cur = Cur()
        status, url = AC.requestLink(cur, "kid@example.com", "1.2.3.4", "/account", True)
    assert status == "send" and url.startswith(AC.siteOrigin() + "/login/t/")
    assert any("INSERT INTO login_token" in q for q in cur.sql)
    src = read("racecast", "accounts.py")
    assert "status = deliverLink(email, url)" in src
    assert 'unsent == "slow"' in read("racecast", "templates", "login.html")


def _flowApp(monkeypatch, tokens, ages, made):
    """A Flask app with the accounts blueprint over in-memory stand-ins for
    the token, account and session tables."""
    app = flask.Flask(__name__, template_folder=os.path.join(ROOT, "racecast", "templates"))
    app.jinja_env.globals["static_v"] = lambda f: "/static/" + f
    app.jinja_env.globals["site_origin"] = "https://racecast.co"
    app.register_blueprint(AC.bp)
    # login.html carries the site's topbar (owner, 2026-10-10), which
    # url_for()s these site pages; stand-ins so the links can be built
    # (the Tools menu's too, 2026-10-10: blueprint endpoints keep their dot)
    for ep in ("predictions_page", "about_page", "report_page", "compare_page",
               "schools_directory", "conversions_page", "coaches_page",
               "cuts.wit_index", "pages2.projections_index",
               "pages2.breakouts_page", "scale.scale_page", "goal.goal_page"):
        app.add_url_rule("/" + ep, ep, lambda: "")

    class Conn:
        def commit(self):
            pass

    @contextlib.contextmanager
    def fakeDb():
        yield Conn(), None
    monkeypatch.setattr(AC, "_db", fakeDb)
    monkeypatch.setattr(AC, "currentSession", lambda: None)

    def peek(cur, raw, kind="email"):
        row = tokens.get(raw)
        return dict(row) if row and not row.get("used") else None

    def consume(cur, raw, kind="email"):
        row = peek(cur, raw, kind)
        if row:
            tokens[raw]["used"] = True
        return row
    monkeypatch.setattr(AC, "peekToken", peek)
    monkeypatch.setattr(AC, "consumeToken", consume)
    monkeypatch.setattr(AC, "accountAgeOk", lambda cur, email, sub=None: ages.get(email, False))

    def make(cur, email, age_ok=False, google_sub=None, name=None):
        made.append((email, age_ok))
        return {"id": 1, "email": email}
    monkeypatch.setattr(AC, "findOrCreateAccount", make)
    monkeypatch.setattr(AC, "openSession", lambda cur, aid: "sid")

    def step(cur, email, next_path):
        tokens["step"] = {"email": email, "next_path": next_path, "age_ok": False}
        return "step"
    monkeypatch.setattr(AC, "ageStepToken", step)
    return app


ORIGIN = {"Origin": "http://localhost"}


def test_the_age_question_is_asked_once_on_the_continue_step(monkeypatch):
    """(owner, 2026-10-04: the 13+ box had to be ticked before the Google
    button did anything). The first form has no box; a first sign-in is
    asked on the continue step, before any account exists, and the token
    survives an unticked tap."""
    tokens = {"new": {"email": "kid@x.co", "next_path": "/account", "age_ok": False},
              "back": {"email": "old@x.co", "next_path": "/", "age_ok": False}}
    ages, made = {"old@x.co": True}, []
    monkeypatch.setenv("XCP_GOOGLE_CLIENT_ID", "id")
    monkeypatch.setenv("XCP_GOOGLE_CLIENT_SECRET", "s")
    monkeypatch.delenv("XCP_MAIL_PROVIDER", raising=False)
    c = _flowApp(monkeypatch, tokens, ages, made).test_client()

    form = c.get("/login").get_data(as_text=True)
    assert 'name="age_ok"' not in form and "Continue with Google" in form
    assert 'name="email"' not in form and "switched off" in form        # no mail provider: no email box

    page = c.get("/login/t/new").get_data(as_text=True)
    assert 'name="age_ok"' in page and "required" in page
    assert 'name="age_ok"' not in c.get("/login/t/back").get_data(as_text=True)   # answered before
    assert c.get("/login/t/nope").status_code == 410

    r = c.post("/login/t/new", headers=ORIGIN)
    assert r.status_code == 400 and not made and not tokens["new"].get("used")
    r = c.post("/login/t/new", data={"age_ok": "on"}, headers=ORIGIN)
    assert r.status_code == 302 and made == [("kid@x.co", True)] and tokens["new"]["used"]
    r = c.post("/login/t/back", headers=ORIGIN)
    assert r.status_code == 302 and made[-1] == ("old@x.co", False)    # account already 13+

    monkeypatch.setattr(AC, "googleFinish", lambda cur, code, state: ("g@x.co", "sub", "G", "/account", False))
    page = c.get("/auth/google/callback?code=c&state=s").get_data(as_text=True)
    assert "One last step" in page and 'action="/login/t/step"' in page and len(made) == 2
    r = c.post("/login/t/step", data={"age_ok": "on"}, headers=ORIGIN)
    assert r.status_code == 302 and made[-1] == ("g@x.co", True)


def test_no_mail_provider_means_no_pretend_email(monkeypatch):
    """(owner, 2026-10-04: "I don't think the non-google sign in actually
    works"). Without a provider the email post is refused up front instead
    of showing "Check your email" for a link that never leaves the log."""
    monkeypatch.delenv("XCP_MAIL_PROVIDER", raising=False)
    monkeypatch.delenv("XCP_GOOGLE_CLIENT_ID", raising=False)
    c = _flowApp(monkeypatch, {}, {}, []).test_client()
    r = c.post("/login", data={"email": "a@b.co"}, headers=ORIGIN)
    body = r.get_data(as_text=True)
    assert r.status_code == 503 and "switched off" in body and "Check your email" not in body
    assert "Sign-in is not switched on yet" in c.get("/login").get_data(as_text=True)
