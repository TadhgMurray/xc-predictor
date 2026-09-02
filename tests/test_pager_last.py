"""The rankings pager's Last button (issue 57, owner 2026-09-02).

    python -m pytest -q tests/test_pager_last.py
"""
import io
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*p):
    return io.open(os.path.join(ROOT, *p), encoding="utf-8").read()


def test_last_button_is_wired_end_to_end():
    tpl = read("racecast", "templates", "rankings.html")
    assert 'id="last"' in tpl and tpl.index('id="next"') < tpl.index('id="last"')
    js = read("racecast", "static", "rankings.js")
    assert 'q.set("count", "1")' in js and '$("last").addEventListener' in js
    assert 'Math.floor((data.total - 1) / PAGE_SIZE) * PAGE_SIZE' in js
    assert '$("last").disabled = $("next").disabled' in js
    app = read("racecast", "app.py")
    assert 'request.args.get("count") or "") == "1"' in app and "countRows(cur, f)" in app
    rk = read("racecast", "rankings.py")
    assert "def countRows(cur, f, timeout_ms=COUNT_TIMEOUT_MS):" in rk
    assert "count(DISTINCT person_id) FROM ranking_results" in rk, "best times counts people"
    assert "SELECT count(*) FROM ranking_results" in rk, "performances counts rows"


def test_default_boards_are_read_not_counted():
    import sys, types
    for d in ("scripts", "engine", "racecast"):
        sys.path.insert(0, os.path.join(ROOT, d))
    for name in ("database", "psycopg2", "psycopg2.extras", "psycopg2.errors"):
        sys.modules.setdefault(name, types.ModuleType(name))
    sys.modules["database"].getConn = lambda: None
    sys.modules["psycopg2"].extras = sys.modules["psycopg2.extras"]
    sys.modules["psycopg2"].errors = sys.modules["psycopg2.errors"]
    sys.modules["psycopg2.errors"].UndefinedColumn = type("UndefinedColumn", (Exception,), {})
    import rankings as RK
    from werkzeug.datastructures import MultiDict
    f, err = RK.parseFilters(MultiDict({"board": "ability", "pool": "hs_m", "sport": "XC"}))
    assert not err and RK.isDefaultBoard(f)
    f, _ = RK.parseFilters(MultiDict({"board": "ability", "pool": "hs_m", "sport": "XC", "state": "CA"}))
    assert not RK.isDefaultBoard(f), "a state narrows the board"
    f, _ = RK.parseFilters(MultiDict({"board": "ability", "pool": "hs_m", "min_races": "5"}))
    assert not RK.isDefaultBoard(f), "a typed floor narrows the board"
    f, _ = RK.parseFilters(MultiDict({"board": "pr", "pool": "hs_m", "distance": "5000"}))
    assert not RK.isDefaultBoard(f), "best times are never precounted"

    class Cur:
        def __init__(self): self.sql = []
        def execute(self, q, *a): self.sql.append(q.strip())
        def fetchone(self): return (8123,)
        connection = types.SimpleNamespace(rollback=lambda: None)
    cur = Cur()
    f, _ = RK.parseFilters(MultiDict({"board": "performance", "pool": "hs_f", "sport": "TF"}))
    assert RK.countRows(cur, f) == 8123
    assert any("FROM board_size" in q for q in cur.sql) and not any("count(*)" in q for q in cur.sql)
    brr = read("racecast", "build_ranking_results.py")
    assert "def buildBoardSizes" in brr and "VACUUM (ANALYZE) ranking_results" in brr
    assert brr.index("swapIn(conn)\n") < brr.index("buildBoardSizes(conn)\n")
    js = read("racecast", "static", "rankings.js")
    assert "pagerNote(data.reason" in js

