"""One public rating scale, and the rankings page that opens on the season in
season (owner, 2026-10-10, the approved renders).

  - the HS-equivalent is the only scale shown by default: the two-button
    "HS-equivalent / Own pool" switch is gone from single-pool pages
    (athlete, race, school) and is a small "Advanced" box with one checkbox
    on the multi-pool boards (rankings, conversions, predictions ...);
  - the definitions describe that scale (the rating "i", About, coaches);
  - /rankings opens on the home page's sport (app._homeSport), "Both" still
    an option, and a bare link's preview card draws that board.

The database is stubbed; /rankings goes through the real route.

    python -m pytest -q tests/test_one_public_scale.py
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _env  # noqa: F401,E402  -- sets XCP_DB_PASSWORD, must precede config
for _p in ("racecast", "engine", "scripts"):
    sys.path.insert(0, os.path.join(ROOT, _p))

T = os.path.join(ROOT, "racecast", "templates")


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


# ---------------------------------------------------------------- the control
SINGLE_POOL = ("athlete.html", "what_it_takes.html", "race.html", "race_tf.html",
               "meet_tf.html", "compiled.html", "compiled_tf.html", "school.html",
               "school_prs.html", "landing.html")
ADVANCED = ("rankings.html", "conversions.html", "predictions.html")


def test_single_pool_pages_have_no_scale_switch():
    for f in SINGLE_POOL:
        t = read("racecast", "templates", f)
        assert "scale_toggle(" not in t, f
        assert "import rv, scale_toggle" not in t, f


def test_every_remaining_caller_gets_the_advanced_box():
    callers = [f for f in os.listdir(T) if f.endswith(".html") and f != "_scale.html"
               and "{{ scale_toggle(" in read("racecast", "templates", f)]
    for f in ADVANCED:
        assert f in callers, f
    assert not set(callers) & set(SINGLE_POOL), callers
    s = read("racecast", "templates", "_scale.html")
    macro = s[s.index("{% macro scale_toggle"):]
    assert 'id="scale-toggle"' in macro and "<summary>Advanced</summary>" in macro
    assert "<input type=\"checkbox\" data-scale-own> Show each pool's own scale" in macro
    assert 'data-scale="pool"' not in macro and "Own pool</button>" not in macro
    # ! the `same` flag survives, as before: a class for scripts
    assert "scale_toggle(hidden=False, same=False)" in macro
    assert "{% if same %} is-same{% endif %}" in macro


def test_scale_view_wires_the_checkbox_and_keeps_the_preference():
    js = read("racecast", "static", "scale-view.js")
    # the stored choice is still read, and still only "pool" moves anyone
    assert 'localStorage.getItem(SCALE_KEY) === "pool" ? "pool" : "hs"' in js
    # a page without the control is always HS
    assert 'var hasControl = !!document.getElementById("scale-toggle");' in js
    assert 'window.rcScale = { mode: hasControl ? load() : "hs" };' in js
    assert 'box.querySelector("input[data-scale-own]")' in js
    assert 'choose(own.checked ? "pool" : "hs")' in js
    assert "localStorage.setItem(SCALE_KEY, window.rcScale.mode)" in js


def test_the_definitions_describe_the_hs_scale():
    e = read("racecast", "templates", "_explain.html")
    assert "100 is an average high-school runner; every level is shown on that one scale." in e
    assert "'runner in the pool'" not in e          # the old fallback text
    a = read("racecast", "templates", "about.html")
    sec = a[a.index('id="rating"'):a.index('id="how"')]
    assert "100 is an average high-school" in sec and "100 is the mean of that pool" not in sec
    c = read("racecast", "templates", "coaches.html")
    assert "Ratings are on one scale: 100 is an average high-school runner" in c
    assert "Ratings are pool-relative" not in c


def test_the_explain_macro_renders():
    import jinja2
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(T), autoescape=True)
    env.filters.update(diffpct=str, daypct=str, dayabs=str, dayword=str, daycapped=bool)
    out = env.from_string('{% from "_explain.html" import rating_i %}{{ rating_i() }}').render(
        RACE_DAY_SPORTS=())
    assert "100 is an average high-school runner; every level is shown on that one scale." in out
    tog = env.from_string('{% from "_scale.html" import scale_toggle %}'
                          '{{ scale_toggle(hidden=True, same=True) }}').render()
    assert 'class="scale-toggle scale-adv is-same"' in tog and 'style="display:none"' in tog


# ---------------------------------------------------------------- /rankings
def _rankings(monkeypatch, meta, qs=""):
    import app as A

    class _Cur:
        def __enter__(self): return self
        def __exit__(self, *a): return False

    class _Conn(_Cur):
        def cursor(self, **kw): return _Cur()

    monkeypatch.setattr(A, "getConn", lambda: _Conn())
    monkeypatch.setattr(A, "get_homepage_meta", lambda cur: dict(meta))
    monkeypatch.setattr(A, "_RANKINGS_SPORT_CACHE", {})
    monkeypatch.setattr(A.app, "before_request_funcs", {})
    with A.app.test_client() as c:
        r = c.get("/rankings" + qs)
    assert r.status_code == 200
    return r.get_data(as_text=True)


def _selected_sport(html):
    sel = html[html.index('<select id="sport"'):]
    sel = sel[:sel.index("</select>")]
    return re.findall(r'<option value="(\w+)" selected>', sel)


def test_rankings_opens_on_the_season_in_season(monkeypatch):
    html = _rankings(monkeypatch, {"season_year_XC": "2026", "season_year_TF": "2026"})
    assert _selected_sport(html) == ["XC"]                        # autumn: a tie is XC
    assert 'id="rk-kicker">HS boys cross country<' in html
    # a bare link's card draws the board the page opens on
    assert "/card/board.png?sport=XC" in html
    html = _rankings(monkeypatch, {"season_year_XC": "2025", "season_year_TF": "2026"})
    assert _selected_sport(html) == ["TF"]
    assert '<option value="both">Both</option>' in html          # still offered


def test_a_link_that_names_a_sport_keeps_it(monkeypatch):
    html = _rankings(monkeypatch, {"season_year_XC": "2026"}, "?board=ability&sport=both&pool=hs_f")
    # the select's default is only the starting point; the URL restore wins
    # client-side, and the card follows the link, not the default
    assert "card/board.png?board=ability&amp;sport=both&amp;pool=hs_f" in html
    assert "sport=XC" not in html.split("card/board.png", 1)[1].split('"', 1)[0]


def test_a_failed_meta_read_costs_nothing(monkeypatch):
    import app as A

    def boom():
        raise RuntimeError("no database")
    monkeypatch.setattr(A, "getConn", boom)
    monkeypatch.setattr(A, "_RANKINGS_SPORT_CACHE", {})
    assert A._rankingsDefaultSport() == "XC"
