"""An athlete hidden at their request (owner, 2026-10-11: "remove/unpublish
my results"; racecast/person_optout.py). The helper, the 410 on their own
pages, and a rendered race page that names them as "Hidden athlete",
unlinked, with the place and the other runners untouched.

No Postgres: the race page reuses test_compiled_levels' stub cursor.

    python -m pytest -q tests/test_person_optout.py
"""
import json
import os
import sys

import _env  # noqa: F401  -- sets XCP_DB_PASSWORD, must precede config

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "scripts"),
           os.path.join(_ROOT, "engine"), os.path.dirname(os.path.abspath(__file__))):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest                                                  # noqa: E402

import person_optout as O                                      # noqa: E402

HID = 13950482


@pytest.fixture(autouse=True)
def _hidden():
    before = O.hiddenIds()
    O.setHidden([HID])
    yield
    O.setHidden(before)


# ---- the helper ----------------------------------------------------------- #

def test_is_hidden_takes_ints_and_digit_strings():
    assert O.isHidden(HID) and O.isHidden(str(HID))
    assert not O.isHidden(HID + 1)
    assert not O.isHidden(None) and not O.isHidden("abc")
    O.setHidden([])
    assert not O.isHidden(HID)


def test_rows_are_renamed_and_search_hits_dropped():
    ctx = {"rows": [{"person_id": HID, "name": "Jane Doe", "place": 3},
                    {"person_id": 7, "name": "Bob Roe", "place": 4}],
           "teams": [{"best_person_id": HID, "best_name": "Jane Doe"}],
           "hits": [{"kind": "athlete", "label": "Jane Doe", "link": f"/athlete/{HID}"},
                    {"kind": "athlete", "label": "Bob Roe", "link": "/athlete/7"}]}
    O.maskTree(ctx)
    assert ctx["rows"][0] == {"person_id": HID, "name": O.HIDDEN_NAME, "place": 3}
    assert ctx["rows"][1]["name"] == "Bob Roe"
    assert ctx["teams"][0]["best_name"] == O.HIDDEN_NAME
    assert [h["label"] for h in ctx["hits"]] == ["Bob Roe"]


def test_html_anchor_is_unwrapped_and_others_kept():
    html = (f'<td><a class="x" href="/athlete/{HID}?r=5">Hidden athlete</a></td>'
            f'<td><a href="/athlete/{HID}1">Other</a></td>')
    out = O.maskHtml(html)
    assert out.startswith("<td>Hidden athlete</td>")
    assert f'href="/athlete/{HID}1"' in out


def test_json_body_is_masked():
    body = json.dumps({"rows": [{"person_id": HID, "name": "Jane Doe"}]})
    assert "Jane Doe" not in O.maskJson(body)
    plain = json.dumps({"rows": [{"person_id": 7, "name": "Bob"}]})
    assert O.maskJson(plain) is plain


# ---- the site ------------------------------------------------------------- #

@pytest.fixture
def client(monkeypatch):
    import test_compiled_levels as T
    import meet_compile as MC
    import app as A
    monkeypatch.setattr(MC, "_RESULTS_COLS", {})
    rows = T.fixtureRows()

    class _CM:
        def __init__(s, c): s.c = c
        def __enter__(s): return s.c
        def __exit__(s, *a): return False

    class _Conn:
        def __enter__(s): return s
        def __exit__(s, *a): return False
        def cursor(s, **k): return _CM(T.StubCursor(rows))
        def rollback(s): pass

    monkeypatch.setattr(A, "getConn", lambda *a, **k: _Conn())
    monkeypatch.setattr(A, "_inMaintenance", lambda: False)
    monkeypatch.setattr(A, "meet_sources", lambda cur, t, m: [])
    monkeypatch.setattr(A, "pick_source", lambda s, alt: (None, None, []))
    monkeypatch.setattr(A, "get_meet_header", lambda cur, m, source=None: {
        "meet_id": m, "meet_name": "Fixture Invitational", "state": "OR",
        "course_name": None})

    def _hs(cur, sport, rows, distance=None):
        for r in rows:
            r["hs_rating"] = None
        return False
    monkeypatch.setattr(A, "stampRowsHs", _hs)
    A.app.config["TESTING"] = True
    return A.app.test_client()


@pytest.mark.parametrize("path", [
    f"/athlete/{HID}", f"/athlete/{HID}/profile", f"/card/athlete/{HID}.png",
    f"/recruit/{HID}", f"/compare?a=1&b={HID}", f"/api/athlete_results?person_id={HID}",
])
def test_own_pages_are_gone(client, path):
    r = client.get(path)
    assert r.status_code == 410
    assert r.headers["X-Robots-Tag"] == "noindex"
    assert "removed at their request" in r.get_data(as_text=True)


def test_race_page_names_them_hidden_and_unlinked(client):
    import test_compiled_levels as T
    dq = next(r for r in T.fixtureRows() if r["name"] == "Dq Runner")
    O.setHidden([dq["person_id"]])
    html = client.get("/race/xc/77/compiled/5000/M").get_data(as_text=True)
    assert "Dq Runner" not in html
    assert O.HIDDEN_NAME in html
    assert f'href="/athlete/{dq["person_id"]}' not in html
    # every other runner keeps their name and link
    other = next(r for r in T.fixtureRows()
                 if r["name"] != "Dq Runner" and r["div_id"] == dq["div_id"])
    assert f'href="/athlete/{other["person_id"]}' in html
