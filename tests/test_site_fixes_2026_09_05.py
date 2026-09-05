"""Text pins for the 2026-09-05 page fixes (issues 179-183)."""
import io
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _src(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


def test_unit_filters_are_school_arrays_and_the_area_probe_retries():
    s = _src("racecast", "rankings.py")
    assert "def _unitSchools(" in s and 'AND school = ANY(%(' in s
    assert "school IN (SELECT u.school FROM school_unit" not in s
    assert "_UNIT_AREA_PRESENT = True if present else _t.time() + 300" in s


def test_apply_button_survives_a_throw_in_buildquery():
    js = _src("racecast", "static", "rankings.js")
    i = js.index("async function load()")
    body = js[i:i + 3000]
    assert body.index("try {") < body.index("query = buildQuery();")


def test_headers_noindex_and_the_board_season_number():
    a = _src("racecast", "app.py")
    assert "@app.after_request" in a and "max-age=31536000, immutable" in a
    assert '"X-Frame-Options", "DENY"' in a
    assert "def enrich_seasons(seasons, board_seasons=None)" in a
    assert "FROM athlete_season" in a[a.index("board_seasons = {}"):a.index("seasons = enrich_seasons(seasons, board_seasons)")]
    m = _src("racecast", "templates", "_meta.html")
    assert "noindex,follow" in m and "request.args.get('r')" in m
    v = _src("racecast", "templates", "venue_tf.html")
    assert "topbar-search.js" in v
    h = _src("racecast", "templates", "home.html")
    assert "static_exists(slot ~ '.webp')" in h
