"""normalize_distance.loadGraphLevels (pooling review P3, 2026-10-26): the
school-level graph's 'S:<school>' nodes answer levelForSchool for a row's
school name; 'T:<team_id>' nodes are left to team_level. Before this, the
whole key was normalised ('s cornell') and nothing ever matched."""
import contextlib
import os
import sys
import types

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in ("engine", "scripts"):
    sys.path.insert(0, os.path.join(_ROOT, _p))
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")

import normalize_distance as nd                                  # noqa: E402


class _Cur:
    def __init__(self, rows):
        self.rows, self.sql = rows, []

    def execute(self, sql, *a):
        self.sql.append(sql)

    def fetchone(self):
        return ("school_level_graph",)

    def fetchall(self):
        sql = self.sql[-1]
        assert "LIKE 'S:%'" in sql and "substr(school, 3)" in sql
        return [(name[2:], lvl) for name, lvl in self.rows if name.startswith("S:")]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_school_nodes_answer_by_name(monkeypatch):
    rows = [("S:Cornell", "college"), ("S:Duke University", "college"), ("T:123", "hs")]
    cur = _Cur(rows)
    conn = types.SimpleNamespace(cursor=lambda: cur)
    db = types.ModuleType("database")

    @contextlib.contextmanager
    def getConn():
        yield conn
    db.getConn = getConn
    monkeypatch.setitem(sys.modules, "database", db)
    monkeypatch.setattr(nd, "_GRAPH_LEVELS", None)
    got = nd.loadGraphLevels()
    assert got[nd.normSchoolKey("Cornell")] == "college"
    assert got[nd.normSchoolKey("Duke University")] == "college"
    assert not any(k.startswith(("s ", "t ")) for k in got)
    assert nd.levelForSchool("Cornell") == "college"
    monkeypatch.setattr(nd, "_GRAPH_LEVELS", None)
