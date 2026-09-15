"""The coach edition (282/283): the /coaches product and the topbar switch.

    python -m pytest -q tests/test_coach_view.py

★ PINNED BY SOURCE, LIKE THE OTHER WIRING TESTS. There is no database here,
  so what these check is the part that breaks silently: a route renamed
  without its links, a per-reader block creeping into cached HTML, or the
  old coach-search URL losing its redirect.
"""
import io
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*p):
    return io.open(os.path.join(ROOT, *p), encoding="utf-8").read()


def test_the_routes_exist_and_the_old_one_redirects():
    app = read("racecast", "app.py")
    assert '@app.route("/coaches")' in app
    assert '@app.route("/coaches/recruits")' in app
    assert '@app.route("/recruiting/search")' in app
    # the old URL is in the wild: it must move, not disappear, and it must
    # not be a second copy of the page
    assert 'return redirect("/coaches/recruits", code=301)' in app
    assert app.count('render_template("recruiting_search.html"') == 1


def test_the_topbar_switches_on_the_path_not_a_cookie():
    top = read("racecast", "templates", "_topbar.html")
    assert "{% set coach_view = here.startswith('/coaches') %}" in top
    assert 'class="viewswitch"' in top
    assert 'href="/coaches"' in top and 'href="/coaches/recruits"' in top
    # ⚠ the whole point: nothing about the reader may reach the cached bar.
    #   Comments are stripped first -- the block above explains at length
    #   why there is no cookie here, and the word itself is not the bug.
    import re
    markup = re.sub(r"\{#.*?#\}", "", top, flags=re.S)
    for banned in ("session", "cookie", "current_user", "account."):
        assert banned not in markup, banned


def test_the_coach_home_is_cacheable_and_not_private():
    app = read("racecast", "app.py")
    # /coaches must NOT join the no-store list -- it is a public page
    private = app.split("_PRIVATE_PREFIXES", 1)[1].split(")", 1)[0]
    assert "/coaches" not in private
    page = read("racecast", "templates", "coaches.html")
    assert "coaches.js" in page and 'id="coach-mine"' in page
    # the signed-out copy is what ships in the HTML; JS only ever replaces it
    assert "Sign in" in page
    assert "{{ account" not in page and "{{ claims" not in page


def test_the_per_reader_block_rides_the_topbar_s_one_request():
    js = read("racecast", "static", "coaches.js")
    assert "fetch(" not in js                    # no second /api/me call
    assert "window.xcpMe" in js and "xcp:me" in js
    # a school name can contain a slash and the route is a <path:> converter
    assert "replace(/%2F/g, '/')" in js
    # and the failure path must still fire, or this block waits forever
    top = read("racecast", "static", "topbar-search.js")
    catch = top.split(".catch(", 1)[1]
    assert "xcp:me" in catch.split("})", 1)[0] + catch[:400]


def test_api_me_carries_what_the_coach_home_renders():
    src = read("racecast", "accounts.py")
    assert '"level_label": c.get("level_label") or ""' in src
    assert '"admin": isAdmin(a)' in src


def test_the_links_moved_with_the_route():
    assert 'href="/coaches/recruits"' in read("racecast", "templates", "recruiting.html")
    for f in (("racecast", "templates", "recruiting.html"),
              ("racecast", "templates", "coaches.html")):
        assert '"/recruiting/search"' not in read(*f)
    sitemap = read("racecast", "build_sitemap.py")
    assert '"/coaches"' in sitemap and '"/coaches/recruits"' in sitemap


def test_the_bar_reflows_instead_of_pushing_the_account_off_screen():
    """b1bf920 overflowed by 23px at 1180 and 145px at 1024, with the
    account chip off the right edge; the fix is the band between the
    desktop bar and the existing 700px rule."""
    css = read("racecast", "static", "style.css")
    band = css.split("@media (max-width: 1200px)", 1)[1].split("}", 6)[0]
    assert "flex-wrap: wrap" in band
    assert ".topbar .search-wrap" in css.split("@media (max-width: 1200px)", 1)[1][:700]
    assert ".topbar .viewswitch a.is-on" in css
