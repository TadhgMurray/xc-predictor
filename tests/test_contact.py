# ★ Contact without a report (owner, 2026-10-23): /contact and /api/contact.
import contextlib
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import _env  # noqa: F401,E402

pytest.importorskip("flask")


@pytest.fixture(scope="module")
def A():
    import app
    return app


class _Cur:
    def __init__(self):
        self.sql = []

    def execute(self, sql, params=None):
        self.sql.append((sql, params))

    def fetchone(self):
        return (77,)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Conn:
    def __init__(self):
        self.cur = _Cur()
        self.commits = 0

    def cursor(self, *a, **k):
        return self.cur

    def commit(self):
        self.commits += 1


@pytest.fixture
def wired(A, monkeypatch):
    conn = _Conn()

    @contextlib.contextmanager
    def getConn():
        yield conn
    mails = []
    monkeypatch.setattr(A, "getConn", getConn)
    monkeypatch.setattr(A, "_ensureTable", lambda *a, **k: None)
    monkeypatch.setattr(A, "_reportThrottled", lambda ip: False)
    monkeypatch.setattr(A, "_mailAdmins", lambda subject, text, reply_to: mails.append((subject, text, reply_to)))
    monkeypatch.setattr(A, "_inMaintenance", lambda: False)
    return A.app.test_client(), conn, mails


def test_the_page_renders_with_its_topics(A, monkeypatch):
    monkeypatch.setattr(A, "_inMaintenance", lambda: False)
    r = A.app.test_client().get("/contact?topic=data")
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and 'value="data" selected' in html and 'id="website"' in html


def test_a_message_is_stored_and_mailed_with_reply_to(wired):
    c, conn, mails = wired
    r = c.post("/api/contact", json={"topic": "data", "name": "Pat Coach", "email": "Pat@Example.com",
                                     "message": "We time 40 meets a year and would share results."})
    assert r.status_code == 200 and r.get_json() == {"ok": True}
    sql, params = conn.cur.sql[0]
    assert "INSERT INTO issue_reports" in sql and params[0] == "contact:data" and params[3] == "pat@example.com"
    assert params[2].startswith("From: Pat Coach")
    assert mails and mails[0][2] == "pat@example.com" and "#77" in mails[0][1]


def test_an_email_and_a_message_are_needed_and_bots_get_nothing(wired):
    c, conn, mails = wired
    assert c.post("/api/contact", json={"message": "hello there"}).status_code == 400
    assert c.post("/api/contact", json={"email": "a@b.co", "message": ""}).status_code == 400
    r = c.post("/api/contact", json={"email": "a@b.co", "message": "buy now", "website": "spam.example"})
    assert r.status_code == 200 and not conn.cur.sql and not mails
