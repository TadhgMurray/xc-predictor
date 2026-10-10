"""The home page as built from the owner-approved render (2026-10-10): the
black hero with the search, the nine photos with their focal points, the
four task links, and the snapshot with one row of level tabs, top ten per
table, a Teams tab and a track-5K column. The database is stubbed; the page
is rendered through the real home() route.

    python -m pytest -q tests/test_home_hero.py
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _env  # noqa: F401,E402  -- sets XCP_DB_PASSWORD, must precede config
for _p in ("racecast", "engine", "scripts"):
    sys.path.insert(0, os.path.join(ROOT, _p))


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


# ---------------------------------------------------------------- source
def test_the_old_controls_are_gone():
    h = read("racecast", "templates", "home.html")
    for gone in ("hero-mosaic", "rank-controls", "pool-chips", "data-toggle=",
                 "scale_toggle()", "mtile"):
        assert gone not in h, gone
    js = read("racecast", "static", "home.js")
    assert "wireToggle" not in js and ".chip" not in js
    assert "data-level" in js


def test_every_focal_point_outranks_the_shared_rule():
    css = read("racecast", "static", "style.css")
    assert "hero-mosaic" not in css and not re.search(r"^\.mtile|^\.m\d \{", css, re.M)
    for n in range(1, 10):
        assert re.search(r"\.hm-photos img\.hp-m%d \{ object-position: \d+%% \d+%%; \}" % n, css), n
    # Kipchoge keeps his clock
    assert ".hm-photos img.hp-m8 { object-position: 48% " in css
    h = read("racecast", "templates", "home.html")
    for n in range(1, 10):
        assert "('m%d'," % n in h
    assert "static_exists(slot ~ '.webp')" in h


def test_the_hero_search_is_the_topbar_search():
    h = read("racecast", "templates", "home.html")
    assert 'data-live-search="hm-search-results"' in h and 'id="hm-search-results"' in h
    js = read("racecast", "static", "topbar-search.js")
    assert "input[data-live-search]" in js
    assert js.count("fetch('/search/api?q=") == 1          # one copy, not two


def test_teams_read_the_stored_board():
    t = read("racecast", "teams.py")
    body = t[t.index("def homeTopTeams"):t.index("#  SINGLE RACES AT A COURSE")]
    assert "t.span = 'season'" in body and "t.scope = 'usa'" in body
    assert "n_athletes >= 5" in body and "raceStored" not in body


# ---------------------------------------------------------------- track 5K
def test_track5k_matches_rating_clock():
    import conversions as cv
    from test_rating_clock import _stub
    _stub()
    t = cv.track5k(100, "hs_m")
    c = cv.ratingClock(100, "hs_m", "TF", 5000.0)
    assert t and t == c["time"]
    assert cv.track5k(146, "hs_m") < t                     # m:ss, faster
    rows = cv.stampTrack5k([{"rating": 130.0, "pool": "hs_f"}, {"rating": None, "pool": "hs_m"}])
    assert rows[0]["track5k"] and rows[1]["track5k"] is None


# ---------------------------------------------------------------- render
def _render(monkeypatch):
    import app as A
    import weekend

    rows = []
    for board in ("athlete", "performance"):
        for pool in ("hs_m", "hs_f", "college_m", "college_f", "ms_m"):
            for i in range(25):
                rows.append(dict(sport="XC", scope="season", board=board, pool=pool,
                                 rank=i + 1, person_id=i, name=f"{pool} {board} {i + 1}",
                                 name_link=f"/athlete/{i}", school="Summit", state="OR",
                                 rating=130.0 - i, season_year=2026,
                                 detail=("15:01.0 · Some Meet" if board == "performance"
                                         else "4 races"),
                                 link="/race/xc/1/1" if board == "performance" else None))
    teams = {p: [dict(school=f"Team {p} {i}", state="OR", pool=p, sport="XC", year=2026,
                      rank=i + 1, points=50 + i * 20, n_athletes=8, top5_mean=120.0 - i)
                 for i in range(10)] for p in ("hs_m", "hs_f", "college_m", "college_f")}

    class _Cur:
        def __enter__(self): return self
        def __exit__(self, *a): return False

    class _Conn(_Cur):
        def cursor(self, **kw): return _Cur()

    monkeypatch.setattr(A, "getConn", lambda: _Conn())
    monkeypatch.setattr(A, "get_homepage_panels", lambda cur: [dict(r) for r in rows])
    monkeypatch.setattr(A, "get_homepage_meta", lambda cur: {
        "season_year_XC": "2026", "season_year_TF": "2026", "fact_results": "61.6M"})
    monkeypatch.setattr(A, "get_homepage_recent", lambda cur: {})
    monkeypatch.setattr(A, "_homeTeams", lambda cur, meta: teams)
    monkeypatch.setattr(weekend, "comingUpCached", lambda getConn, today=None: [])
    import conversions as cv
    monkeypatch.setattr(cv, "ratingSeconds",
                        lambda r, pool, sport="XC", distance=None: 1210.0 * (100.0 / float(r)) ** 0.77)
    monkeypatch.setattr(A.app, "before_request_funcs", {})
    with A.app.test_client() as c:
        r = c.get("/")
    assert r.status_code == 200
    return r.get_data(as_text=True)


def test_the_page_renders_in_the_new_shape(monkeypatch):
    html = _render(monkeypatch)
    assert "Every result on one comparable scale." in html
    assert 'class="hero-mosaic"' not in html
    # the hero's clock line, computed: 100 -> 20:10 under the stub, 146 faster
    assert "100 is an average high schooler, about a 20:10 5K on a track; a 146 is about" in html
    assert 'href="/about#rating"' in html
    # four task links
    for href in ("/search", "/predictions", "/conversions", "/recruiting"):
        assert f'href="{href}"><b>' in html
    # one row of level tabs, three board tabs
    assert html.count('data-level="') == 4 + 1          # four tabs + the section's own
    for tab in ("athletes", "performances", "teams"):
        assert f'data-tab="{tab}"' in html
    # top ten, not five and not twenty-five; middle school is not on home
    assert ">hs_m athlete 10<" in html and ">hs_m athlete 11<" not in html
    assert "ms_m athlete" not in html
    # the Teams tab, linked to the teams board
    assert "Team hs_f 9" in html and "board=teams" in html
    # the 5K column
    assert 'title="Track 5K this rating is worth">5K</th>' in html
    assert "More levels" in html
