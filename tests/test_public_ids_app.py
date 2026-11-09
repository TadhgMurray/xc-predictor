"""public_ids wired into the real app (owner, 2026-11-08: "just do all")."""
import contextlib
import datetime
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in ("racecast", "scripts", "engine"):
    sys.path.insert(0, os.path.join(_ROOT, _d))
os.environ.setdefault("XCP_DB_PASSWORD", "unused")


def _app(monkeypatch):
    import app as A
    import public_ids as P
    monkeypatch.setenv("XCP_PUBLIC_ID_KEY", "b" * 64)
    P.encode.cache_clear()
    P.decode.cache_clear()
    monkeypatch.setattr(A, "getConn", lambda: contextlib.nullcontext(None))
    return A, P


def test_an_old_numeric_page_url_is_sent_to_its_token(monkeypatch):
    A, P = _app(monkeypatch)
    c = A.app.test_client()
    r = c.get("/athlete/27034607?r=78008678")
    assert r.status_code == 301
    assert r.headers["Location"].endswith(f"/athlete/{P.encode('a', 27034607)}?r={P.encode('r', 78008678)}")
    r = c.get("/race/xc/290015/1158313")
    assert r.status_code == 301 and P.encode("m", 290015) in r.headers["Location"]
    monkeypatch.setenv("XCP_PUBLIC_ID_LEGACY", "off")
    assert c.get("/athlete/27034607").status_code == 404


def test_a_page_leaves_with_tokens(monkeypatch):
    A, P = _app(monkeypatch)
    import activity as AC
    t = datetime.datetime.now()
    fd = {"events": [{"at": t, "kind": "pipeline", "level": "bad", "new": True, "title": "x",
                      "summary": "y", "detail": [], "links": [("Big Invite", "/meet/xc/290015")]}],
          "n_new": 1, "errors": {}}
    monkeypatch.setattr(AC, "feed", lambda conn, seen=None: fd)
    monkeypatch.setenv("XCP_ADMIN_EMAILS", "owner@example.com")
    monkeypatch.setattr(A._accounts, "currentSession",
                        lambda: {"csrf": "t", "account": {"email": "owner@example.com"}})
    body = A.app.test_client().get("/account/status/activity").get_data(as_text=True)
    assert f"/meet/xc/{P.encode('m', 290015)}" in body and "/meet/xc/290015" not in body
