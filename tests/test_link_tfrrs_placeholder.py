"""An id's own minted person is a placeholder, not a second person (owner,
2026-11-03: 315 UAA runners left unlinked)."""
import os
import sys

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
for sub in ("scripts", "engine", "racecast"):
    sys.path.insert(0, os.path.join(ROOT, sub))


def test_the_fanout_sees_past_the_minted_placeholder(monkeypatch):
    import link_tfrrs_rows as L
    seen = []

    class C:
        rowcount = 0
        def execute(self, sql, p=None): seen.append(sql)
        def fetchone(self): return (0,)
    monkeypatch.setattr(L, "_skips", lambda cur: {"XC": "", "TF": ""})
    L.fanout(C(), False)
    sql = next(x for x in seen if "CREATE TEMP TABLE tl_nat" in x)
    assert "THEN 1000000000" in sql and "+ x.native_id" in sql
    assert "FILTER (WHERE NOT minted) <= 1" in sql
    assert "COALESCE(min(person_id) FILTER (WHERE NOT minted)" in sql
