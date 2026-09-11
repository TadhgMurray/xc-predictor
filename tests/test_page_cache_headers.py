# Project: xc-predictor / tests
# File:    test_page_cache_headers.py
# Purpose: Pages are cacheable at the edge; errors and private paths are not.
#
# ★★ WHAT THIS PROTECTS. Two hours of nginx log, 2026-09-11:
#      YandexBot 11,246 | Applebot 6,760 | one scraper rotating fourteen
#      Chrome user agents ~17,600 (counts within 9% of each other, Chrome
#      99 through 136 in equal shares -- a hardcoded UA list) | Googlebot 619
#    About 460k requests a day, essentially none of them people, every one
#    reaching gunicorn and then Postgres on the box that runs the solve,
#    because only /static/ carried a cache header.
#
# ⚠⚠ THE ONE THAT WOULD REALLY HURT: a cached 503. The maintenance page is
#    served for every path during a table swap. Stored at the edge for 15
#    minutes it OUTLIVES the swap, and clearing the flag does not reach it --
#    the stale-flag outage again, in a place the fix cannot see. Only 200s
#    are cacheable, and everything else is explicitly no-store.
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ! A PLACEHOLDER SO app.py CAN BE IMPORTED, NOT A CREDENTIAL. scripts/config
#   refuses to build a DSN without one and says so loudly, by design -- but
#   importing app.py opens no connection, and nothing here touches the
#   database: _headers is a pure function of the request and the response.
#   setdefault, so a real local config still wins.
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")

try:
    import flask
    _HAVE_FLASK = True
except ImportError:
    _HAVE_FLASK = False


def _headers_for(path, status=200, method="GET"):
    """Run app._headers on a synthetic request/response without a database."""
    import flask as f
    import app as A
    probe = f.Flask(__name__)
    with probe.test_request_context(path, method=method):
        resp = f.Response("x", status=status)
        return A._headers(resp).headers


@unittest.skipUnless(_HAVE_FLASK, "flask not installed")
class PagesAreCacheable(unittest.TestCase):
    def test_an_athlete_page(self):
        cc = _headers_for("/athlete/21548899")["Cache-Control"]
        self.assertIn("public", cc)
        self.assertIn("s-maxage=", cc)

    def test_a_rankings_board_with_a_query_string(self):
        cc = _headers_for("/rankings?board=ability&pool=hs_f")["Cache-Control"]
        self.assertIn("public", cc)

    def test_the_edge_ttl_is_at_least_the_browser_ttl(self):
        """s-maxage is for the CDN, max-age for the person. A person
        reloading should see new numbers no later than a crawler does."""
        import app as A
        self.assertGreaterEqual(A.PAGE_S_MAXAGE, A.PAGE_MAX_AGE)

    def test_static_keeps_its_own_rule(self):
        self.assertIn("immutable",
                      _headers_for("/static/x.css?v=123")["Cache-Control"])


@unittest.skipUnless(_HAVE_FLASK, "flask not installed")
class ErrorsAreNeverStored(unittest.TestCase):
    def test_the_maintenance_503_is_not_cacheable(self):
        """⚠ THE ONE THAT MATTERS. A stored 503 keeps the site down after
        the swap that caused it has finished."""
        cc = _headers_for("/athlete/21548899", status=503)["Cache-Control"]
        self.assertIn("no-store", cc)
        self.assertNotIn("public", cc)

    def test_404_and_500_are_not_cacheable(self):
        for code in (404, 500):
            cc = _headers_for("/nope", status=code)["Cache-Control"]
            self.assertIn("no-store", cc, f"status {code}")

    def test_post_is_not_cacheable(self):
        cc = _headers_for("/api/convert", status=200,
                          method="POST")["Cache-Control"]
        self.assertIn("no-store", cc)


@unittest.skipUnless(_HAVE_FLASK, "flask not installed")
class PrivatePathsAreNeverStored(unittest.TestCase):
    def test_every_private_prefix(self):
        import app as A
        for p in A._PRIVATE_PREFIXES:
            cc = _headers_for(p + "x")["Cache-Control"]
            self.assertIn("no-store", cc, p)

    def test_robots_disallows_exactly_the_same_list(self):
        """★ ONE LIST, TWO CONSUMERS. A route added to the cache exclusion
        but missed in robots.txt is a crawled API; missed the other way is
        a cached search result."""
        import app as A
        with A.app.test_request_context("/robots.txt"):
            body = A.robots_txt().get_data(as_text=True)
        disallowed = {ln.split(":", 1)[1].strip()
                      for ln in body.splitlines()
                      if ln.startswith("Disallow:")}
        self.assertEqual(disallowed, set(A._PRIVATE_PREFIXES))


if __name__ == "__main__":
    unittest.main()
