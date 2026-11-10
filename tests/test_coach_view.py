"""The coaches' pages are cut (owner, 2026-11-10: "maybe just cut it all,
but log for the feature chat that a coaches racecast would be nice"):
/coaches had 0 views in 14 days and no link in, /coaches/recruits 3. Every
old URL goes to /recruiting, and nothing links the old pages."""
import io
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*p):
    with io.open(os.path.join(_ROOT, *p), encoding="utf-8") as fh:
        return fh.read()


def test_every_coaches_url_goes_to_recruiting():
    app = read("racecast", "app.py")
    for route in ('@app.route("/coaches")', '@app.route("/coaches/recruits")',
                  '@app.route("/recruiting/search")'):
        assert route in app, route
    i = app.index('@app.route("/coaches")')
    assert 'return redirect("/recruiting", code=301)' in app[i:i + 400]


def test_no_page_links_the_cut_pages():
    tdir = os.path.join(_ROOT, "racecast", "templates")
    for f in os.listdir(tdir):
        if f.endswith(".html"):
            s = read("racecast", "templates", f)
            assert 'href="/coaches' not in s, f
    assert not os.path.exists(os.path.join(tdir, "coaches.html"))
    assert '"/coaches"' not in read("racecast", "build_sitemap.py")
