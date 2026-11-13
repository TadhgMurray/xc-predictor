"""A race page's PR / season-best reads are shared for the box
(app.stampRecordFlags, 2026-11-12): a second view of the same race reads no
SQL and stamps the same flags; another race reads its own."""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "scripts"), os.path.dirname(os.path.abspath(__file__))):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import _env  # noqa: E402,F401


class _Cur:
    def __init__(self):
        self.sql = []
        self.connection = self

    def rollback(self):
        pass

    def execute(self, sql, params=None):
        self.sql.append(sql)
        self.rows = ([{"person_id": 1, "best_before": 250.0, "season_best_before": 260.0}]
                     if "GROUP  BY person_id" in sql else [])

    def fetchall(self):
        return self.rows


def test_the_same_race_reads_once(tmp_path, monkeypatch):
    import app as A
    import sharedcache
    monkeypatch.setattr(sharedcache, "DIR", str(tmp_path / "shared"))
    monkeypatch.setattr(sharedcache, "tableOid", lambda cur, t, now=None: 9)

    def rows():
        return [{"person_id": 1, "time_seconds": 245.0, "result_id": 5}]
    cur = _Cur()
    a, b = rows(), rows()
    A.stampRecordFlags(cur, "XC", a, 5000, "2026-10-01")
    n = len(cur.sql)
    A.stampRecordFlags(cur, "XC", b, 5000, "2026-10-01")
    assert len(cur.sql) == n and a == b
    A.stampRecordFlags(cur, "XC", rows(), 5000, "2026-10-08")
    assert len(cur.sql) > n
