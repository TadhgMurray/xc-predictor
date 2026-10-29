"""diag_backfill_row runs the backfill's own classifier on the rows asked
for and prints its reason (2026-10-29). Offline, with the backfill and the
database stood in.

    python -m pytest -q tests/test_diag_backfill_row.py
"""
import contextlib
import io
import os
import sys
import types
from contextlib import redirect_stdout

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))


class Cur:
    def __init__(self):
        self.sql = []

    def execute(self, sql, params=None):
        self.sql.append((sql, params))

    def fetchone(self):
        return (True,)

    def fetchall(self):
        return [(60883789, "anet", 263025, 1043377, None, None, 1602.1, "Jr", None,
                 "2025-08-29", 30178078, 555, "Wake Forest", None)]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_prints_the_classifier_reason(monkeypatch):
    cur = Cur()

    class Conn:
        def cursor(self):
            return cur

        def rollback(self):
            pass

    @contextlib.contextmanager
    def getConn():
        yield Conn()
    db = types.ModuleType("database")
    db.getConn = getConn
    B = types.ModuleType("backfill_normalize")
    (B._ID, B._SRC, B._MEET, B._DIV, B._EVENT_ID, B._EVENT_SHORT, B._TIME, B._GRADE, B._AID,
     B._DATE, B._PERSON, B._CANON, B._SCHOOL, B._RATING_POOL) = range(14)
    B._configFor = lambda sport, mode: types.SimpleNamespace(sport=sport, table="results")
    B._buildLookups = lambda conn, cfg: (None, {}, None, {}, {}, set(), {}, None,
                                         set(), set(), set(), {(30178078, 555): 8000.0}, set())
    B._makeRowFn = lambda *a, **k: (lambda row: (row[0], None, "no_distance", None))
    B._streamSQL = lambda cfg, age_band, rating_pool: "SELECT ... FROM results r"
    monkeypatch.setitem(sys.modules, "database", db)
    monkeypatch.setitem(sys.modules, "backfill_normalize", B)
    import diag_backfill_row as D
    monkeypatch.chdir(ROOT)
    out = io.StringIO()
    with redirect_stdout(out):
        D.main([60883789])
    text = out.getvalue()
    assert "-> no_distance" in text and "dropped division 8000.0" in text
    assert any("r.result_id = ANY(%s)" in s for s, _p in cur.sql)
