"""The one nav and the predictions opening (owner, 2026-10-10: the approved
nav and predictions designs).

    python -m pytest -q tests/test_one_nav.py

★ RENDERED, NOT ONLY GREPPED. The Tools menu is built from url_for, so the
  test renders _topbar.html inside a request at real paths: a renamed route
  fails here at render time, and the lit item is the one production lights.
  The predictions opening renders through the real route with weekend.py's
  calendar stubbed -- no database here.
"""
import io
import os
import re
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import _env  # noqa: F401,E402  -- sets XCP_DB_PASSWORD, must precede config

pytest.importorskip("flask")
pytest.importorskip("psycopg2")


def read(*p):
    return io.open(os.path.join(_ROOT, *p), encoding="utf-8").read()


@pytest.fixture(scope="module")
def A():
    import app
    return app


def topbar(A, path):
    from flask import render_template
    with A.app.test_request_context(path):
        return render_template("_topbar.html")


def links(html):
    return re.findall(r'<a href="([^"]*)"[^>]*>([^<]+)</a>', html)


# ------------------------------------------------------------------ nav
def test_one_nav_no_edition_switch(A):
    for path in ("/", "/coaches", "/coaches/recruits", "/rankings"):
        h = topbar(A, path)
        assert "viewswitch" not in h and "data-ed" not in h and "topnav-ed" not in h, path
        # the same five sections for every reader, coach pages included
        nav = h.split('<nav class="topnav"', 1)[1].split("</nav>", 1)[0]
        tabs = [t for _, t in links(nav.split("<details", 1)[0])]
        assert tabs == ["Rankings", "Meets", "Predictions", "Conversions", "Recruiting"], path
        assert "Find recruits" not in h and ">More<" not in h, path


def test_the_tools_menu_links(A):
    h = topbar(A, "/rankings")
    menu = h.split('<div class="topnav-menu">', 1)[1].split("</div>", 1)[0]
    tools, ref = menu.split("<hr>")
    assert links(tools) == [
        ("/compare", "Compare two athletes"),
        ("/what-it-takes", "What it takes to make state"),
        ("/projections", "Who wins state"),
        ("/breakouts", "Breakouts"),
        ("/schools", "Schools by state"),
        ("/conversions#paces", "Training paces"),
        ("/scale", "Rating to time"),
        ("/goal", "Goal calculator"),
    ]
    assert links(ref) == [("/about", "About"), ("/report", "Report an issue")]
    # "Who wins state" is the predictions page's state-meets mode
    assert dict((t, u) for u, t in links(tools))["Who wins state"] == A.app.url_map.bind("x").build("pages2.projections_index")


@pytest.mark.parametrize("path,lit", [
    ("/compare", "Compare two athletes"), ("/what-it-takes/ca", "What it takes to make state"),
    ("/projections/ca", "Who wins state"), ("/breakouts", "Breakouts"), ("/schools/ca", "Schools by state"),
    ("/scale", "Rating to time"), ("/goal", "Goal calculator"), ("/about", "About"),
])
def test_a_tools_page_lights_tools_and_itself(A, path, lit):
    h = topbar(A, path)
    assert '<summary class="is-here">Tools</summary>' in h
    assert re.search(r'class="[^"]*is-here[^"]*">' + re.escape(lit) + "<", h)


def test_no_for_coaches_link_in_the_bar(A):
    # owner, 2026-10-11: "For coaches looks very off" -- out of the bar
    h = topbar(A, "/rankings")
    assert 'href="/coaches"' not in h and "For coaches" not in h
    assert "Recruiting</a>" in h and 'href="/recruiting"' in h


def test_the_phone_menu_button_is_accessible(A):
    h = topbar(A, "/rankings")
    btn = re.search(r'<button type="button" class="nav-toggle"[^>]*>', h).group(0)
    assert 'aria-expanded="false"' in btn and 'aria-controls="topnav"' in btn and 'aria-label="Menu"' in btn
    assert 'id="topnav"' in h
    js = read("racecast", "static", "topbar-search.js")
    tail = js.split("THE TOPBAR'S TOOLS MENU", 1)[1]
    assert "aria-expanded" in tail and "'Escape'" in tail and "nav-open" in tail


def test_the_edition_memory_is_gone_and_sign_in_next_stays():
    js = read("racecast", "static", "topbar-search.js")
    # comments out first: the block that replaced it says what went
    code = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
    code = re.sub(r"(?m)^\s*//.*$", "", code)
    assert "rc-edition" not in code and "sessionStorage" not in code and "viewswitch" not in code
    # kept: the sign-in next= and the rc_si hint ahead of /api/me
    assert "'/login?next=' +" in js
    assert js.index("rc_si=0") < js.index("fetch('/api/me'")


def test_coaches_page_says_recruit_search():
    page = read("racecast", "templates", "coaches.html")
    assert "Recruit search" in page and "Find recruits" not in page
    assert "Find recruits" not in read("racecast", "templates", "recruiting_search.html")


def test_phone_css_has_the_two_rows_and_the_panel():
    css = read("racecast", "static", "style.css")
    one = css.split("THE ONE NAV (owner, 2026-10-10", 1)[1].split("/* ---- /coaches", 1)[0]
    phone = one.split("@media (max-width: 700px)", 1)[1]
    assert ".topbar .search-wrap { order: 4; flex: 1 1 100%" in phone
    assert ".topbar.nav-open .topnav { display: flex; }" in phone
    assert "nav-coach" not in css      # no For coaches link in the bar (owner, 2026-10-11)


# ------------------------------------------------------- predictions
WEEK = [{"date": "2026-10-10", "label": "Today", "meets": [
    {"meet_id": 7, "sport": "XC", "source": "anet", "name": "Small Dual", "date": "2026-10-10",
     "venue": "Here Park", "state": "OR", "n_races": 2, "level": "HS"},
    {"meet_id": 8, "sport": "XC", "source": "tfrrs", "name": "Big Invite", "date": "2026-10-10",
     "venue": "Woodward Park", "state": "CA", "n_races": 20, "level": "College"},
]}]


@pytest.fixture
def client(A, monkeypatch):
    import weekend
    for d in WEEK:
        for m in d["meets"]:
            m["predict_href"] = weekend.predictHref(m)
    monkeypatch.setattr(weekend, "comingUpCached", lambda getConn, today=None: WEEK)
    return A.app.test_client()


def test_predictions_opens_on_the_week_and_who_wins_state(client):
    html = client.get("/predictions").get_data(as_text=True)
    week = html.split('id="pred-week-h"', 1)[1].split("</section>", 1)[0]
    # biggest first, each a Predict link into the page with its feed pinned
    assert week.index("Big Invite") < week.index("Small Dual")
    assert 'href="/predictions?meet_id=8&amp;sport=XC&amp;src=tfrrs"' in week
    assert "Sat Oct 10 · Woodward Park · CA" in week
    assert "All 2 meets this week" in week and 'href="/meets?view=upcoming"' in week
    card = html.split('id="pred-state-h"', 1)[1].split("</aside>", 1)[0]
    assert 'action="/projections"' in card and '<option value="ca">California</option>' in card
    assert 'href="/projections/ca">California</a>' in card
    # the Any race / State meets switch is gone; re-run comes after
    assert "predict-views" not in html and ">Any race<" not in html
    assert html.index('id="pred-open"') < html.index("Re-run a past race") < html.index('id="meet-input"')


def test_the_options_fold_and_the_mode_label(client):
    html = client.get("/predictions?meet_id=8&sport=XC&src=tfrrs").get_data(as_text=True)
    fold = html.split('<details class="pred-opts" id="pred-opts">', 1)[1].split("</details>", 1)[0]
    assert "<summary>Change date, course or team size</summary>" in fold
    for i in ('id="t-date"', 'id="t-course"', 'id="course-box"', 'id="per-team"', 'id="per-team-label"'):
        assert i in fold, i
    assert html.count('id="per-team"') == 1
    assert "Test the model on a past race" in html and "As it actually ran" not in html
    # the shared link's card still rides the page
    assert "card/predict.png?meet_id=8" in html


def test_an_empty_week_is_said_not_an_error(A, monkeypatch):
    import weekend
    monkeypatch.setattr(weekend, "comingUpCached", lambda getConn, today=None: [])
    r = A.app.test_client().get("/predictions")
    assert r.status_code == 200
    assert "No meet has been posted for the next seven days yet." in r.get_data(as_text=True)
