"""/account/admin/accounts (owner, 2026-10-27): every account and the
profiles it claims, admins only, read only.

    XCP_TWIN_TEST_DSN="host=... dbname=..." runs the query and the page on Postgres
"""
import contextlib
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
import admin_accounts as AA                                      # noqa: E402

KID = {"sid_hash": "h", "csrf": "t", "account": {"id": 41, "email": "kid@example.com", "role": "athlete"}}
OWNER = {"sid_hash": "o", "csrf": "t", "account": {"id": 1, "email": "owner@racecast.co", "role": "neither"}}


def _app(monkeypatch, sess):
    app = flask.Flask(__name__, template_folder=os.path.join(ROOT, "racecast", "templates"))
    app.register_blueprint(AA.bp)
    app.jinja_env.globals.update(static_v=lambda name: "/static/" + name)
    # the site-wide partials need the full app's filters; this page's own
    # template is what is under test
    import jinja2
    app.jinja_loader = jinja2.ChoiceLoader([
        jinja2.DictLoader({"_meta.html": "<title>{{ meta_title }}</title>", "_topbar.html": ""}),
        jinja2.FileSystemLoader(os.path.join(ROOT, "racecast", "templates"))])
    monkeypatch.setenv("XCP_ADMIN_EMAILS", "owner@racecast.co")
    monkeypatch.setattr(AC, "currentSession", lambda: sess)
    return app


def test_signed_out_goes_to_login_and_others_get_a_404(monkeypatch):
    r = _app(monkeypatch, None).test_client().get("/account/admin/accounts")
    assert r.status_code == 302 and "/login?next=" in r.headers["Location"]
    assert _app(monkeypatch, KID).test_client().get("/account/admin/accounts").status_code == 404


@pytest.fixture
def pg(monkeypatch):
    dsn = os.environ.get("XCP_TWIN_TEST_DSN")
    if not dsn:
        pytest.skip("set XCP_TWIN_TEST_DSN to a scratch Postgres to run the SQL")
    import psycopg2
    import psycopg2.extras
    conn = psycopg2.connect(dsn)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("DROP SCHEMA IF EXISTS t_accts CASCADE; CREATE SCHEMA t_accts; SET search_path TO t_accts")
    import follows as FO
    cur.execute(AC.DDL)
    cur.execute(FO.DDL)
    cur.execute("CREATE TABLE athletes (athlete_id bigint, person_id bigint, first_name text,"
                " last_name text, school text)")
    cur.execute("CREATE TABLE person_redirect (old_id bigint PRIMARY KEY, new_id bigint)")
    cur.execute("""INSERT INTO athletes VALUES (10, 10, 'Ann', 'Lee', 'Cedar'),
                                               (20, 20, 'Bo', 'Ray', 'Oak')""")
    cur.execute("INSERT INTO person_redirect VALUES (15, 20)")
    cur.execute("""INSERT INTO account (id, email, name, role, last_login_at) VALUES
                   (1, 'ann@example.com', 'Ann Lee', 'athlete', now()),
                   (2, 'coach@example.com', 'Coach', 'coach', NULL),
                   (3, 'gone@example.com', NULL, 'neither', NULL)""")
    cur.execute("UPDATE account SET deleted_at = now() WHERE id = 3")
    cur.execute("""INSERT INTO account_claim (account_id, kind, person_id) VALUES (1, 'athlete', 10),
                   (2, 'parent', 15)""")
    cur.execute("INSERT INTO account_claim (account_id, kind, school, state) VALUES (2, 'coach', 'Oak', 'OR')")
    cur.execute("INSERT INTO account_follow (account_id, kind, person_id) VALUES (1, 'athlete', 20)")

    @contextlib.contextmanager
    def _db():
        yield conn, cur
    monkeypatch.setattr(AC, "_db", _db)
    yield conn, cur
    conn.rollback()
    cur.execute("DROP SCHEMA IF EXISTS t_accts CASCADE")
    conn.commit()
    conn.close()


def test_the_list_and_its_claims(pg):
    _conn, cur = pg
    accounts, totals = AA.accountsPage(cur)
    by = {a["email"]: a for a in accounts}
    assert totals["n"] == 3 and totals["live"] == 2 and totals["recent"] == 1 and totals["claims"] == 3
    assert by["ann@example.com"]["claims"][0]["who"]["name"] == "Ann Lee"
    assert by["ann@example.com"]["n_follows"] == 1
    parent = [c for c in by["coach@example.com"]["claims"] if c["kind"] == "parent"][0]
    assert parent["who"]["merged_into"] == 20 and parent["who"]["name"] == "Bo Ray"
    assert [a["email"] for a in AA.accountsPage(cur, q="coach")[0]] == ["coach@example.com"]
    assert {a["email"] for a in AA.accountsPage(cur, claims_only=True)[0]} == \
        {"ann@example.com", "coach@example.com"}


def test_the_page_renders_for_the_owner(pg, monkeypatch):
    monkeypatch.setattr(AC, "tablesReady", lambda cur: True)   # the test's own schema, not public
    app = _app(monkeypatch, OWNER)
    r = app.test_client().get("/account/admin/accounts?claims=1")
    html = r.get_data(as_text=True)
    assert r.status_code == 200, html[:2000]
    assert "ann@example.com" in html and "/athlete/10" in html and "Ann Lee" in html
    assert "/athlete/20" in html and "merged into #20" in html
    assert "Oak / OR" in html and "gone@example.com" not in html
    assert 'name="robots" content="noindex"' in html
