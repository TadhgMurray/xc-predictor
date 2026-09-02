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
    assert "def countRows(cur, f):" in rk
    assert "count(DISTINCT person_id) FROM ranking_results" in rk, "best times counts people"
    assert "SELECT count(*) FROM ranking_results" in rk, "performances counts rows"
