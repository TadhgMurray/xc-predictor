"""A merged spelling's school page sends the reader to its school
(2026-11-01): race pages link the feed's own spelling, and the boards file
it under the merged name (school_name_alias)."""
import os
import sys

os.environ.setdefault("XCP_DB_PASSWORD", "x")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for d in ("racecast", "scripts", "engine"):
    sys.path.insert(0, os.path.join(ROOT, d))

import app as A                                                 # noqa: E402


def test_a_merged_spelling_redirects(monkeypatch):
    monkeypatch.setitem(A._SCHOOL_ALIAS, "at", 1e18)
    monkeypatch.setitem(A._SCHOOL_ALIAS, "map", {"Conn College": "Connecticut College"})
    c = A.app.test_client()
    r = c.get("/school/Conn%20College?sport=TF")
    assert r.status_code == 301 and r.headers["Location"].endswith("/school/Connecticut%20College?sport=TF")
    r = c.get("/school/Conn%20College/records")
    assert r.status_code == 301 and r.headers["Location"].endswith("/school/Connecticut%20College/records")
    assert A._schoolAliasRedirect("Connecticut College") is None
