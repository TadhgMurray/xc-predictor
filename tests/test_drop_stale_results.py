"""A re-posted division replaces its old rows (owner, 2026-11-02: UIAAA,
every runner listed twice)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))


class _Cur:
    def __init__(self):
        self.calls, self.rowcount = [], 2

    def execute(self, sql, args):
        self.calls.append((sql, args))


class _Conn:
    def __init__(self):
        self.cur = _Cur()

    def cursor(self):
        return self.cur


def test_each_saved_division_drops_what_it_did_not_write():
    import database
    conn = _Conn()
    res = [({"IDResult": 11}, {"ID": 5}, "A", 100), ({"IDResult": 12}, {"ID": 5}, "A", 100),
           ({"IDResult": 21}, {"ID": 5}, "B", 200),
           ({"IDResult": 3_000_000_000_001}, {"ID": 5}, "C", 300)]     # AthleticLIVE's: not touched
    n = database.dropStaleResults(conn, res)
    assert n == 4
    args = sorted((a[0], a[1], sorted(a[3])) for _s, a in conn.cur.calls)
    assert args == [(5, 100, [11, 12]), (5, 200, [21])]
    assert all("source = 'anet'" in s and "result_id < %s" in s for s, _a in conn.cur.calls)


def test_nothing_saved_removes_nothing():
    import database
    conn = _Conn()
    assert database.dropStaleResults(conn, []) == 0 and conn.cur.calls == []


def test_saver_calls_it():
    src = open(os.path.join(os.path.dirname(__file__), "..", "scripts", "scrape_results.py")).read()
    i = src.index("saveResultsBulk(conn, collected_results)")
    assert "dropStaleResults(conn, collected_results)" in src[i:i + 400]
