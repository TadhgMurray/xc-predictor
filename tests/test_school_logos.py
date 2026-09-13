# Project: xc-predictor / tests
# File:    test_school_logos.py
# Purpose: The school crest job (305) without a network, a database or
#          Pillow -- which is everything this sandbox has.
#
# ★ WHAT IS WORTH PINNING HERE. The scraper's risk is not the HTTP; it is
#   the JUDGEMENT either side of it: which icon a page's declarations pick,
#   which of them is allowed to be a crest, which school a directory row
#   belongs to, and which crest is a district's rather than a school's. A
#   wrong answer to any of those puts the wrong picture on a real school's
#   page, which is worse than the blank the site has today. Every one of
#   them is arithmetic or string work, so every one of them is tested.
#
# ⚠ ROBOTS IS TESTED, NOT ASSUMED. The one thing in this job that can
#   embarrass the site is fetching what a school asked us not to, so the
#   refusal path is exercised against a stubbed server rather than trusted
#   to read correctly.
import io
import json
import os
import sys
import time
import unittest
import urllib.error

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "racecast")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import school_logo as SL                                          # noqa: E402
import scrape_school_logos as S                                   # noqa: E402
import build_school_websites as W                                 # noqa: E402

try:                    # the box has Pillow; this sandbox may not
    from PIL import Image
except ImportError:                                          # pragma: no cover
    Image = None


def read(*p):
    with io.open(os.path.join(_ROOT, *p), encoding="utf-8") as fh:
        return fh.read()


# ===================================================================== #
#  WHICH ICON A PAGE MEANS                                              #
# ===================================================================== #

PAGE = """<!doctype html><html><head>
  <meta property="og:image" content="/img/social-banner.jpg">
  <link rel="apple-touch-icon" sizes="180x180" href="//cdn.example.org/at.png">
  <link rel="icon" type="image/png" sizes="32x32" href="favicon-32.png">
  <link rel="icon" href="data:image/png;base64,AAAA">
  <meta name="msapplication-TileImage" content="/tile.png">
  <link rel="manifest" href="/site.webmanifest">
  <link rel="shortcut icon" href="/fav.ico">
</head><body>
  <link rel="icon" href="/decoy-in-the-body.png">
</body></html>"""


class Candidates(unittest.TestCase):
    def setUp(self):
        self.got, self.manifest = S.iconCandidates(
            PAGE, "https://chs.k12.ca.us/home/index.html")

    def test_the_declared_icons_come_before_the_social_banner(self):
        """★ THE ORDER CHANGED after the first real run. og:image was
        first, per the plan, and it is a 1200x630 banner on nearly every
        CMS -- so every school spent a request on a certain rejection."""
        kinds = [k for k, _u, _p in self.got]
        self.assertEqual(kinds[:5], ["apple-touch", "icon-sized", "tile",
                                     "og", "icon"])
        self.assertLess(kinds.index("apple-touch"), kinds.index("og"))

    def test_urls_are_absolute_against_the_page(self):
        by = {k: u for k, u, _p in self.got}
        self.assertEqual(by["og"], "https://chs.k12.ca.us/img/social-banner.jpg")
        self.assertEqual(by["apple-touch"], "https://cdn.example.org/at.png")
        # relative to the PAGE's directory, not to the host root
        self.assertEqual(by["icon-sized"], "https://chs.k12.ca.us/home/favicon-32.png")

    def test_the_manifest_is_returned_separately(self):
        """It is the best source of all and the only one that costs a
        request to discover, so fetchLogo keeps it as a second tier."""
        self.assertEqual(self.manifest, "https://chs.k12.ca.us/site.webmanifest")
        self.assertNotIn("webmanifest", " ".join(u for _k, u, _p in self.got))

    def test_data_uris_and_body_tags_are_not_candidates(self):
        urls = " ".join(u for _k, u, _p in self.got)
        self.assertNotIn("data:", urls)
        self.assertNotIn("decoy", urls, "an icon link in the body is not a declaration")

    def test_the_conventional_favicon_is_always_last_resort(self):
        self.assertIn("https://chs.k12.ca.us/favicon.ico",
                      [u for _k, u, _p in self.got])

    def test_a_page_that_declares_nothing_still_has_one_candidate(self):
        got, manifest = S.iconCandidates(
            "<html><head><title>x</title></head></html>", "https://x.org/")
        self.assertEqual([(k, u) for k, u, _p in got],
                         [("icon", "https://x.org/favicon.ico")])
        self.assertIsNone(manifest)

    def test_the_biggest_declared_size_leads_its_kind(self):
        page = ('<head><link rel="apple-touch-icon" sizes="60x60" href="/s.png">'
                '<link rel="apple-touch-icon" sizes="180x180" href="/b.png"></head>')
        got = [u for k, u, _p in S.iconCandidates(page, "https://x.org/")[0]
               if k == "apple-touch"]
        self.assertEqual(got, ["https://x.org/b.png", "https://x.org/s.png"])

    def test_malformed_markup_still_yields_what_parsed(self):
        page = '<head><link rel="icon" sizes="64x64" href="/i.png"><p><div'
        self.assertIn("https://x.org/i.png",
                      [u for _k, u, _p in S.iconCandidates(page, "https://x.org/")[0]])


class Manifest(unittest.TestCase):
    """A web app manifest's icons are the best marks a modern site
    publishes: square by convention, 192 or 512 px."""

    def test_biggest_first_and_absolute(self):
        raw = (b'{"name":"CHS","icons":[{"src":"i192.png","sizes":"192x192"},'
               b'{"src":"/i512.png","sizes":"512x512"}]}')
        self.assertEqual(S.manifestIcons(raw, "https://x.org/a/site.webmanifest"),
                         [("manifest", "https://x.org/i512.png", 512),
                          ("manifest", "https://x.org/a/i192.png", 192)])

    def test_rubbish_is_no_icons_rather_than_an_exception(self):
        for raw in (b"", b"<html>", b"[]", b'{"icons":"nope"}', b'{"icons":[1,2]}'):
            self.assertEqual(S.manifestIcons(raw, "https://x.org/m.json"), [], raw)


class Athletics(unittest.TestCase):
    """★ THE WHOLE POINT OF THE SECOND PASS (owner, 2026-09-12). MIT's home
    page gives the institutional wordmark; a results table wants the
    Engineers mark, and it is one link away on the page we already have."""

    MIT = ('<html><body>'
           '<a href="https://www.facebook.com/MITAthletics">Facebook</a>'
           '<a href="/education">Education</a>'
           '<a href="https://mitathletics.com/">Athletics</a>'
           '</body></html>')

    def test_the_athletics_domain_is_found(self):
        self.assertEqual(S.athleticsLink(self.MIT, "https://web.mit.edu/"),
                         "https://mitathletics.com/")

    def test_a_social_page_about_athletics_is_never_it(self):
        page = ('<a href="https://www.facebook.com/CHSAthletics">CHS Athletics</a>'
                '<a href="https://twitter.com/chsathletics">Athletics</a>')
        self.assertIsNone(S.athleticsLink(page, "https://chs.org/"))

    def test_a_separate_domain_beats_a_path_on_the_school_s_own_site(self):
        page = ('<a href="/athletics">Athletics</a>'
                '<a href="https://chsathletics.org/">Teams</a>')
        self.assertEqual(S.athleticsLink(page, "https://chs.org/"),
                         "https://chsathletics.org/")

    def test_the_go_something_sports_pattern(self):
        page = '<a href="https://gocougarsports.com/">Cougar Athletics</a>'
        self.assertEqual(S.athleticsLink(page, "https://chs.org/"),
                         "https://gocougarsports.com/")

    def test_a_path_on_the_school_s_own_site_still_counts(self):
        page = '<a href="/athletics/">Athletics</a><a href="/apply">Apply</a>'
        self.assertEqual(S.athleticsLink(page, "https://chs.org/"),
                         "https://chs.org/athletics/")

    def test_a_page_with_no_athletics_says_so(self):
        self.assertIsNone(S.athleticsLink(
            '<a href="/apply">Apply</a><a href="/news">News</a>', "https://chs.org/"))

    def test_a_nickname_domain_is_found_by_its_caption_alone(self):
        """★ THE CASE THAT MATTERS MOST FOR COLLEGES. An athletics domain
        is very often a nickname with no tell in it -- gopoets.com,
        rolltide.com -- and the page's own caption is the only thing that
        identifies it."""
        for host in ("https://gopoets.com/", "https://rolltide.com/"):
            self.assertEqual(
                S.athleticsLink(f'<a href="{host}">Athletics</a>'
                                '<a href="/apply">Apply</a>', "https://x.edu/"),
                host)

    def test_an_opaque_cms_url_captioned_athletics_is_followed(self):
        """School CMSes give /page/1234. The caption is the signal, and
        being wrong costs one request against a site we already have."""
        self.assertEqual(S.athleticsLink('<a href="/page/1234">Athletics</a>',
                                         "https://chs.org/"),
                         "https://chs.org/page/1234")

    def test_a_host_that_merely_contains_a_social_name_is_not_social(self):
        """"x.com" as a substring also matches phoenix.com."""
        self.assertEqual(S.athleticsLink('<a href="https://phoenix.com/x">Athletics</a>',
                                         "https://chs.org/"),
                         "https://phoenix.com/x")


class Acceptable(unittest.TestCase):
    """★ THE FLOOR AND THE CAP BOTH MOVED after the first real run: 96 px
    and 1.6:1 threw away most of what school sites publish, and these are
    drawn at 18 px and 44 px. What the rule still has to do is reject a
    16 px favicon and a social banner."""

    def test_a_social_banner_is_never_a_crest(self):
        self.assertFalse(S.acceptable(1200, 630, "og"))
        self.assertFalse(S.acceptable(1200, 630))
        self.assertFalse(S.acceptable(1200, 630, "school:og"))

    def test_a_tiny_favicon_is_still_too_small(self):
        self.assertFalse(S.acceptable(32, 32, "icon"))
        self.assertFalse(S.acceptable(16, 16, "icon"))

    def test_the_sizes_school_sites_actually_publish_are_kept_now(self):
        for px in (48, 64, 96, 180, 192, 512):
            self.assertTrue(S.acceptable(px, px, "apple-touch"), px)

    def test_a_wordmark_is_a_mark_when_the_site_declared_it_an_icon(self):
        """★ SHAPE ALONE CANNOT SEPARATE a 1.9:1 banner from a 3:1
        wordmark -- the DECLARATION can. A file a site names as its own
        icon is its mark whatever shape it is."""
        self.assertTrue(S.acceptable(240, 90, "apple-touch"))
        self.assertTrue(S.acceptable(240, 90, "school:manifest"))
        self.assertFalse(S.acceptable(240, 90, "og"),
                         "the same shape from an og tag is a banner")
        self.assertFalse(S.acceptable(240, 90))

    def test_the_edges(self):
        self.assertTrue(S.acceptable(48, 48, "icon"))
        self.assertFalse(S.acceptable(47, 47, "icon"))
        self.assertTrue(S.acceptable(300, 100, "icon"))       # 3.0 exactly
        self.assertFalse(S.acceptable(310, 100, "icon"))
        self.assertFalse(S.acceptable(0, 0, "icon"))


# ===================================================================== #
#  MANNERS                                                              #
# ===================================================================== #

class _Resp:
    def __init__(self, body, ctype="text/html", length=None):
        self.body, self.headers = body, {"Content-Type": ctype}
        if length is not None:
            self.headers["Content-Length"] = str(length)

    def read(self, n=-1):
        return self.body[:n] if n and n > 0 else self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Robots(unittest.TestCase):
    def _server(self, pages):
        calls = []

        def urlopen(req, timeout=None):
            url = req.full_url if hasattr(req, "full_url") else req
            calls.append(url)
            if url not in pages:
                raise OSError("404")
            return _Resp(*pages[url])
        return urlopen, calls

    def setUp(self):
        self._real = S.urllib.request.urlopen

    def tearDown(self):
        S.urllib.request.urlopen = self._real

    def test_a_disallow_is_final_and_nothing_is_fetched(self):
        urlopen, calls = self._server({
            "https://x.org/robots.txt": (b"User-agent: *\nDisallow: /\n", "text/plain"),
            "https://x.org/": (b"<html></html>", "text/html")})
        S.urllib.request.urlopen = urlopen
        m = S.Manners(rate=0)
        body, why = m.get("https://x.org/")
        self.assertIsNone(body)
        self.assertEqual(why, "robots")
        self.assertEqual(calls, ["https://x.org/robots.txt"],
                         "the page must not be fetched after a refusal")

    def test_robots_is_read_once_per_host(self):
        urlopen, calls = self._server({
            "https://x.org/robots.txt": (b"User-agent: *\nAllow: /\n", "text/plain"),
            "https://x.org/a": (b"A", "text/html"),
            "https://x.org/b": (b"B", "text/html")})
        S.urllib.request.urlopen = urlopen
        m = S.Manners(rate=0)
        self.assertEqual(m.get("https://x.org/a")[0], b"A")
        self.assertEqual(m.get("https://x.org/b")[0], b"B")
        self.assertEqual(calls.count("https://x.org/robots.txt"), 1)

    def test_a_missing_robots_is_permission(self):
        """The standard's own default, and most school CMSes serve none."""
        urlopen, _calls = self._server({"https://x.org/": (b"<html>", "text/html")})
        S.urllib.request.urlopen = urlopen
        self.assertEqual(S.Manners(rate=0).get("https://x.org/")[0], b"<html>")

    def test_a_refusal_is_never_retried_and_never_raises(self):
        def urlopen(req, timeout=None):
            raise OSError("connection refused")
        S.urllib.request.urlopen = urlopen
        body, why = S.Manners(rate=0).get("https://x.org/")
        self.assertIsNone(body)
        self.assertIn("Error", why + "Error")

    def test_an_oversized_body_is_dropped_by_its_header(self):
        urlopen, _c = self._server({
            "https://x.org/robots.txt": (b"", "text/plain"),
            "https://x.org/big.png": (b"x" * 10, "image/png", 99 * 1024 * 1024)})
        S.urllib.request.urlopen = urlopen
        self.assertEqual(S.Manners(rate=0).get("https://x.org/big.png"),
                         (None, "too big"))

    def test_one_host_is_paced_and_different_hosts_are_not(self):
        """★ THE UNIT IS THE HOST, and the first version had it wrong. A
        global one-a-second is not politeness when every school is a
        different server -- it is just a day of waiting, and each server
        still sees its three requests. What a server experiences is the
        gap between requests to IT."""
        m = S.Manners(rate=0.05)
        t0 = S.time.time()
        for _ in range(3):
            m.wait("one.org")
        paced = S.time.time() - t0
        self.assertGreaterEqual(paced, 0.09)
        t0 = S.time.time()
        for host in ("a.org", "b.org", "c.org", "d.org"):
            m.wait(host)
        self.assertLess(S.time.time() - t0, 0.04, "different hosts never wait")

    def test_the_workers_do_not_serialise_on_each_other(self):
        """Twenty-four schools at once means twenty-four different servers
        at once; the run is an hour, not a day."""
        import concurrent.futures as cf
        m = S.Manners(rate=0.2)
        t0 = S.time.time()
        with cf.ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda i: m.wait(f"host{i}.org"), range(8)))
        self.assertLess(S.time.time() - t0, 0.15)

    def test_robots_is_read_once_per_host_even_under_load(self):
        urlopen, calls = self._server({
            "https://x.org/robots.txt": (b"User-agent: *\nAllow: /\n", "text/plain"),
            "https://x.org/a": (b"A", "text/html")})
        S.urllib.request.urlopen = urlopen
        import concurrent.futures as cf
        m = S.Manners(rate=0)
        with cf.ThreadPoolExecutor(max_workers=8) as pool:
            got = list(pool.map(lambda _i: m.get("https://x.org/a")[0], range(8)))
        self.assertEqual(got, [b"A"] * 8)
        self.assertEqual(calls.count("https://x.org/robots.txt"), 1)


class Reasons(unittest.TestCase):
    """★ "URLError" WAS 1,886 OF THE FIRST RUN'S MISSES and said nothing.
    DNS, a dead certificate and a refused connection all arrive as one
    class, and only some of them are worth doing anything about."""

    def test_each_kind_of_failure_is_named(self):
        import socket
        import ssl
        cases = [
            (urllib.error.URLError(socket.gaierror(-2, "Name or service not known")), "dns"),
            (urllib.error.URLError(ssl.SSLCertVerificationError("expired")), "ssl"),
            (urllib.error.URLError(ConnectionRefusedError()), "refused"),
            (urllib.error.URLError(ConnectionResetError()), "reset"),
            (TimeoutError(), "timeout"),
        ]
        for exc, want in cases:
            self.assertEqual(S._reason(exc), want, exc)

    def test_a_refusal_is_final_and_a_broken_address_is_not(self):
        for final in ("school robots", "school HTTP 403", "school HTTP 429",
                      "school HTTP 401", "page too big"):
            self.assertFalse(S.retryable(final), final)
        for worth in ("school HTTP 404", "school dns", "school ssl",
                      "school HTTP 500", "school reset"):
            self.assertTrue(S.retryable(worth), worth)


class BrokenCertificates(unittest.TestCase):
    """⚠ SCHOOL DISTRICT CERTIFICATES EXPIRE CONSTANTLY, and the school is
    still the school. The verified attempt always happens first; only a
    certificate failure earns a second, unverified one, and the hosts that
    needed it are named at the end of the run."""

    def setUp(self):
        self._real = S.urllib.request.urlopen
        self.addCleanup(setattr, S.urllib.request, "urlopen", self._real)

    def _server(self, fail_with, secure_ok=False):
        self.calls = []

        def urlopen(req, timeout=None, context=None):
            self.calls.append(context is not None)
            if context is None and not secure_ok:
                raise fail_with
            return _Resp(b"<html>", "text/html")
        return urlopen

    def test_a_certificate_failure_earns_one_unverified_retry(self):
        import ssl
        S.urllib.request.urlopen = self._server(
            urllib.error.URLError(ssl.SSLCertVerificationError("expired")))
        m = S.Manners(rate=0)
        body, _ct = m.get("https://chs.k12.ca.us/")
        self.assertEqual(body, b"<html>")
        self.assertEqual(self.calls, [False, False, True],
                         "robots, then the verified try, then the unverified one")
        self.assertIn("chs.k12.ca.us", m.insecureHosts)

    def test_anything_else_is_never_retried_unverified(self):
        import socket
        S.urllib.request.urlopen = self._server(
            urllib.error.URLError(socket.gaierror(-2, "nope")))
        m = S.Manners(rate=0)
        self.assertEqual(m.get("https://chs.k12.ca.us/"), (None, "dns"))
        self.assertNotIn(True, self.calls)
        self.assertEqual(m.insecureHosts, set())

    def test_a_verified_fetch_never_reaches_the_fallback(self):
        S.urllib.request.urlopen = self._server(None, secure_ok=True)
        m = S.Manners(rate=0)
        self.assertEqual(m.get("https://chs.k12.ca.us/")[0], b"<html>")
        self.assertNotIn(True, self.calls)
        self.assertEqual(m.insecureHosts, set())


class Addresses(unittest.TestCase):
    def test_the_spellings_a_directory_gets_wrong(self):
        self.assertEqual(S.homeVariants("http://chs.k12.ca.us/pages/home.aspx"),
                         ["http://chs.k12.ca.us/pages/home.aspx",
                          "http://chs.k12.ca.us/",
                          "https://chs.k12.ca.us/"])
        self.assertEqual(S.homeVariants("https://www.foo.edu/"),
                         ["https://www.foo.edu/", "http://www.foo.edu/",
                          "https://foo.edu/"])
        self.assertEqual(S.homeVariants("http://bar.org"),
                         ["http://bar.org", "https://bar.org/",
                          "http://www.bar.org/"])

    def test_it_is_capped_and_never_repeats_itself(self):
        for url in ("http://a.org/", "https://www.a.org/x?y=1", "http://a.org"):
            got = S.homeVariants(url)
            self.assertLessEqual(len(got), S.HOME_TRIES, url)
            self.assertEqual(len(got), len(set(got)), url)

    def test_rubbish_yields_nothing_to_try(self):
        for junk in ("", "not a url", "mailto:x@y.z", "/relative"):
            self.assertEqual(S.homeVariants(junk), [], junk)


class Fetching(unittest.TestCase):
    """fetchLogo's decisions, with the image reader stubbed out: which site
    it prefers, which candidate it settles on, what it says when none work."""

    class _M:
        def __init__(self, replies):
            self.replies, self.asked = replies, []

        def get(self, url, max_bytes=None, etag=None, modified=None):
            self.asked.append(url)
            return self.replies.get(url, (None, "HTTPError"))

    def setUp(self):
        self._real = S.normalise
        # every fetched body is "wide" or "square"; only square normalises
        S.normalise = lambda raw, px=512, ctype="", kind=None: (
            (b"PNG" + raw, "sha-" + raw.decode(), (512, 512))
            if raw == b"square" else (None, None, "1200x630"))

    def tearDown(self):
        S.normalise = self._real

    MIT = ('<head><link rel="apple-touch-icon" sizes="180x180" href="/mit.png">'
           '</head><body><a href="https://mitathletics.com/">Athletics</a></body>')

    def test_the_athletics_mark_beats_the_school_s_own(self):
        """★ THE OWNER'S CASE. web.mit.edu has a perfectly good touch icon
        -- the institutional wordmark -- and we take the Engineers mark
        from mitathletics.com anyway, because that is the one a results
        table wants."""
        m = self._M({
            "https://web.mit.edu/": (self.MIT.encode(), "text/html"),
            "https://web.mit.edu/mit.png": (b"square", "image/png"),
            "https://mitathletics.com/": (
                b'<head><link rel="apple-touch-icon" href="/eng.png"></head>',
                "text/html"),
            "https://mitathletics.com/eng.png": (b"square", "image/png")})
        _png, _sha, kind, src = S.fetchLogo(m, "https://web.mit.edu/")
        self.assertEqual(src, "https://mitathletics.com/eng.png")
        self.assertEqual(kind, "athletics:apple-touch")

    def test_the_school_s_own_is_kept_when_athletics_yields_nothing(self):
        m = self._M({
            "https://web.mit.edu/": (self.MIT.encode(), "text/html"),
            "https://web.mit.edu/mit.png": (b"square", "image/png"),
            "https://mitathletics.com/": (b"<head></head>", "text/html")})
        _png, _sha, kind, src = S.fetchLogo(m, "https://web.mit.edu/")
        self.assertEqual((kind, src), ("school:apple-touch",
                                       "https://web.mit.edu/mit.png"))

    def test_a_school_with_no_athletics_link_costs_no_extra_request(self):
        m = self._M({
            "https://x.org/": (b'<head><link rel="icon" sizes="256x256" href="/c.png">'
                               b'</head><body><a href="/apply">Apply</a></body>',
                               "text/html"),
            "https://x.org/c.png": (b"square", "image/png")})
        S.fetchLogo(m, "https://x.org/")
        self.assertEqual(m.asked, ["https://x.org/", "https://x.org/c.png"])

    def test_the_banner_loses_to_the_touch_icon(self):
        m = self._M({
            "https://x.org/": (PAGE.replace("chs.k12.ca.us", "x.org").encode(),
                               "text/html"),
            "https://x.org/img/social-banner.jpg": (b"wide", "image/jpeg"),
            "https://cdn.example.org/at.png": (b"square", "image/png")})
        _png, sha, kind, src = S.fetchLogo(m, "https://x.org/")
        self.assertEqual((kind, src), ("school:apple-touch",
                                       "https://cdn.example.org/at.png"))
        self.assertEqual(sha, "sha-square")
        self.assertNotIn("https://x.org/img/social-banner.jpg", m.asked,
                         "the banner is not even fetched now: it sorts after")

    def test_the_manifest_is_the_second_tier(self):
        """Not fetched while a declared icon still works; fetched, and
        preferred to nothing, when none does."""
        page = (b'<head><link rel="icon" sizes="64x64" href="/f.png">'
                b'<link rel="manifest" href="/m.json"></head>')
        m = self._M({
            "https://x.org/": (page, "text/html"),
            "https://x.org/f.png": (b"wide", "image/png"),
            "https://x.org/favicon.ico": (b"wide", "image/x-icon"),
            "https://x.org/m.json": (b'{"icons":[{"src":"/i512.png","sizes":"512x512"}]}',
                                     "application/json"),
            "https://x.org/i512.png": (b"square", "image/png")})
        _png, _sha, kind, src = S.fetchLogo(m, "https://x.org/")
        self.assertEqual((kind, src), ("school:manifest", "https://x.org/i512.png"))

    def test_a_known_logo_file_costs_no_page_visit(self):
        m = self._M({"https://commons/logo.png": (b"square", "image/png")})
        png, _sha, kind, src = S.fetchLogo(m, "https://x.org/",
                                           direct="https://commons/logo.png")
        self.assertEqual(kind, "direct")
        self.assertEqual(m.asked, ["https://commons/logo.png"])
        self.assertTrue(png and src)

    def test_a_dead_logo_file_falls_back_to_the_home_page(self):
        m = self._M({
            "https://commons/gone.png": (None, "HTTPError"),
            "https://x.org/": (b'<head><link rel="icon" sizes="256x256" href="/c.png">',
                               "text/html"),
            "https://x.org/c.png": (b"square", "image/png")})
        _png, _sha, kind, _src = S.fetchLogo(m, "https://x.org/",
                                             direct="https://commons/gone.png")
        self.assertEqual(kind, "school:icon-sized")

    def test_nothing_usable_reports_why_and_stops(self):
        m = self._M({"https://x.org/": (b"<head></head>", "text/html"),
                     "https://x.org/favicon.ico": (b"wide", "image/x-icon")})
        png, sha, kind, why = S.fetchLogo(m, "https://x.org/")
        self.assertEqual((png, sha, kind), (None, None, None))
        self.assertIn("1200x630", why)
        self.assertTrue(why.startswith("school"), why)

    def test_a_page_that_is_not_html_is_not_parsed(self):
        m = self._M({"https://x.org/": (b"%PDF-1.4", "application/pdf")})
        self.assertEqual(S.fetchLogo(m, "https://x.org/")[3],
                         "school application/pdf",
                         "the reason reported is the address as GIVEN, not "
                         "our own last guess at it")

    def test_a_stale_deep_link_falls_back_to_the_site_root(self):
        """★ 1,844 OF THE FIRST RUN'S MISSES were a 404: a directory's link
        into a page that moved, while the site itself was fine."""
        m = self._M({
            "http://chs.org/pages/home.aspx": (None, "HTTP 404"),
            "http://chs.org/": (b'<head><link rel="icon" sizes="128x128" href="/i.png">',
                                "text/html"),
            "http://chs.org/i.png": (b"square", "image/png")})
        _png, _sha, kind, src = S.fetchLogo(m, "http://chs.org/pages/home.aspx")
        self.assertEqual((kind, src), ("school:icon-sized", "http://chs.org/i.png"))

    def test_http_falls_back_to_https(self):
        m = self._M({
            "http://chs.org/": (None, "reset"),
            "https://chs.org/": (b'<head><link rel="icon" sizes="128x128" href="/i.png">',
                                 "text/html"),
            "https://chs.org/i.png": (b"square", "image/png")})
        self.assertTrue(S.fetchLogo(m, "http://chs.org/")[0])

    def test_a_refusal_is_never_re_asked_in_another_spelling(self):
        """robots said no, or the server did: a different spelling of the
        same address is the same refusal, and asking again is rude."""
        for final in ("robots", "HTTP 403", "HTTP 429"):
            m = self._M({"https://chs.org/": (None, final)})
            _p, _s, _k, why = S.fetchLogo(m, "https://chs.org/")
            self.assertEqual(why, f"school {final}")
            self.assertEqual(m.asked, ["https://chs.org/"], final)

    def test_a_broken_address_is_re_asked_at_most_three_ways(self):
        m = self._M({})
        S.fetchLogo(m, "http://chs.org/a/b.aspx")
        self.assertEqual(m.asked, ["http://chs.org/a/b.aspx", "http://chs.org/",
                                   "https://chs.org/"])

    def test_only_so_many_icons_are_tried_per_site(self):
        """A site that declares eight broken icons is a site with no crest,
        not eight requests."""
        links = "".join(f'<link rel="icon" sizes="99x99" href="/i{i}.png">'
                        for i in range(8))
        m = self._M({"https://x.org/": (f"<head>{links}</head>".encode(), "text/html")})
        S.fetchLogo(m, "https://x.org/")
        self.assertLessEqual(len(m.asked), 1 + S.MAX_TRIES)


class Refresh(unittest.TestCase):
    """The plan's step 5: quarterly, only-if-changed. A settled run should
    cost ONE conditional request per school and read no home pages."""

    class _M:
        def __init__(self, replies):
            self.replies, self.asked, self.conditional = replies, [], []
            self.etag = self.modified = None

        def get(self, url, max_bytes=None, etag=None, modified=None):
            self.asked.append(url)
            if etag or modified:
                self.conditional.append(url)
            return self.replies.get(url, (None, "HTTPError"))

    def setUp(self):
        self._real = S.normalise
        S.normalise = lambda raw, px=512, ctype="", kind=None: (
            (b"PNG", "sha-" + raw.decode(), (512, 512))
            if raw == b"square" else (None, None, "1200x630"))

    def tearDown(self):
        S.normalise = self._real

    def test_a_crest_that_has_not_moved_costs_one_request(self):
        m = self._M({"https://x.org/crest.png": (None, "304")})
        self.assertIs(S.refetch(m, "https://x.org/crest.png", etag='"abc"'),
                      S.UNCHANGED)
        self.assertEqual(m.asked, ["https://x.org/crest.png"])
        self.assertEqual(m.conditional, ["https://x.org/crest.png"])

    def test_a_crest_that_has_moved_comes_back_with_a_new_hash(self):
        m = self._M({"https://x.org/crest.png": (b"square", "image/png")})
        png, sha = S.refetch(m, "https://x.org/crest.png", etag='"old"')
        self.assertEqual((png, sha), (b"PNG", "sha-square"))

    def test_a_source_url_that_has_gone_falls_back_to_discovery(self):
        """None here means "read the home page again", which is exactly
        what a school that redesigned its site needs."""
        m = self._M({})
        self.assertIsNone(S.refetch(m, "https://x.org/gone.png"))
        self.assertIsNone(S.refetch(m, None))
        self.assertEqual(m.asked, ["https://x.org/gone.png"])

    def test_an_image_that_no_longer_qualifies_is_a_rediscovery_too(self):
        m = self._M({"https://x.org/crest.png": (b"wide", "image/png")})
        self.assertIsNone(S.refetch(m, "https://x.org/crest.png"))


class Migrations(unittest.TestCase):
    """⚠ "CREATE TABLE IF NOT EXISTS" NEVER ADDS A COLUMN, so a table made
    by last week's run keeps last week's shape and the first INSERT with a
    new column dies with UndefinedColumn -- on the server, mid-run, which
    is exactly how this was found. The ALTERs are derived FROM the DDL so
    they cannot drift from it."""

    class _Cur:
        def __init__(self):
            self.sql = []

        def execute(self, q, p=None):
            self.sql.append(" ".join(q.split()))

    def alters(self, ddl):
        cur = self._Cur()
        SL_ = __import__("scrape_school_logos")
        SL_.ensureTable(cur, ddl)
        return [q for q in cur.sql if q.startswith("ALTER")]

    def test_every_column_in_the_ddl_gets_an_add_if_not_exists(self):
        got = self.alters(S.DDL)
        self.assertIn("ALTER TABLE school_logo ADD COLUMN IF NOT EXISTS "
                      "shared boolean NOT NULL DEFAULT false", got)
        self.assertEqual(len(got), 12)

    def test_the_column_that_broke_the_server_run(self):
        self.assertIn("ALTER TABLE anet_division ADD COLUMN IF NOT EXISTS "
                      "custom boolean NOT NULL DEFAULT false",
                      self.alters(A.DIV_DDL))

    def test_a_not_null_with_no_default_is_relaxed(self):
        """Postgres cannot add one to a table that already has rows, and
        failing to start is worse than a nullable column on old rows."""
        got = self.alters("CREATE TABLE IF NOT EXISTS t (a text NOT NULL, "
                          "b int NOT NULL DEFAULT 0, PRIMARY KEY (a))")
        self.assertEqual(got, ["ALTER TABLE t ADD COLUMN IF NOT EXISTS a text",
                               "ALTER TABLE t ADD COLUMN IF NOT EXISTS b int "
                               "NOT NULL DEFAULT 0"])

    def test_constraints_are_not_mistaken_for_columns(self):
        got = self.alters("CREATE TABLE IF NOT EXISTS t (a int PRIMARY KEY, "
                          "b numeric(4,1), UNIQUE (b), CHECK (b > 0))")
        self.assertEqual(got, ["ALTER TABLE t ADD COLUMN IF NOT EXISTS a int",
                               "ALTER TABLE t ADD COLUMN IF NOT EXISTS b numeric(4,1)"],
                         "an inline PRIMARY KEY is invalid on an ALTER, and "
                         "numeric(4,1) must survive the comma split")

    def test_no_script_executes_a_bare_create(self):
        """One way in, or the next added column breaks the next run."""
        import glob
        for f in glob.glob(os.path.join(_ROOT, "scripts", "*.py")):
            src = io.open(f, encoding="utf-8").read()
            for name in ("DDL", "TEAM_DDL", "DIV_DDL", "MAP_DDL", "GAP_DDL"):
                self.assertNotIn(f"cur.execute({name})", src,
                                 f"{os.path.basename(f)}: use ensureTable")


class Worklist(unittest.TestCase):
    """★ THE ORDER IS THE POINT (owner: "way too slow"). Alphabetical spent
    the first hour on academies with four athletes. Descending athlete
    count means --limit 2000 covers the schools that appear on most pages
    of the site, and the tail can run overnight or never."""

    class _Cur:
        def __init__(self, identity=True, rows=()):
            self.identity, self.rows, self.sql, self.params = identity, rows, [], []

        def execute(self, sql, params=None):
            self.sql.append(sql)
            self.params.append(params)

        def fetchone(self):
            return ["public.school_identity" if self.identity else None]

        def fetchall(self):
            return list(self.rows)

    def test_the_biggest_programmes_come_first(self):
        cur = self._Cur()
        S.targets(cur)
        sql = cur.sql[-1]
        self.assertIn("ORDER  BY COALESCE(si.n_athletes, 0) DESC", sql)
        self.assertIn("LEFT JOIN school_identity si", sql)

    def test_it_still_works_before_school_identity_is_built(self):
        cur = self._Cur(identity=False)
        S.targets(cur)
        sql = cur.sql[-1]
        self.assertIn("ORDER  BY 0 DESC", sql)
        self.assertNotIn("school_identity si", sql)

    def test_the_filters_are_parameters_in_the_right_order(self):
        cur = self._Cur()
        S.targets(cur, refresh_days=30, only="Jesuit", state="ca", limit=50,
                  retry_failed=True)
        self.assertEqual(cur.params[-1], [30, True, "%Jesuit%", "CA", 50])
        self.assertIn("w.school ILIKE %s", cur.sql[-1])
        self.assertIn("LIMIT %s", cur.sql[-1])

    def test_redo_does_not_skip_what_was_fetched_today(self):
        """"fetched < today - 0" excludes today, which is exactly the row
        you are redoing an hour after changing the rules."""
        src = read("scripts", "scrape_school_logos.py")
        self.assertIn("args.refresh_days, args.retry_failed = -1, True", src)
        cur = self._Cur()
        S.targets(cur, refresh_days=-1, retry_failed=True)
        self.assertEqual(cur.params[-1][:2], [-1, True])

    def test_a_row_comes_back_in_the_shape_the_worker_unpacks(self):
        row = ("Jesuit", "CA", "https://x", None, None, None, None, None, "ok")
        cur = self._Cur(rows=[row])
        self.assertEqual(S.targets(cur), [row])


class Redo(unittest.TestCase):
    """⚠ THE CHEAP REFRESH IS WRONG AFTER THE PICKING CHANGES. A 304 keeps
    the crest we already have, so every school holding its institutional
    logo would keep it and never be offered the athletics one."""

    ROW = ("Jesuit", "CA", "https://x.org/", None, None,
           "https://x.org/old.png", '"etag"', None, "ok")

    def setUp(self):
        self.seen = []
        real_re, real_fetch = S.refetch, S.fetchLogo
        S.refetch = lambda *a, **k: (self.seen.append("refetch"), S.UNCHANGED)[1]
        S.fetchLogo = lambda *a, **k: (self.seen.append("fetch"),
                                       (b"PNG", "sha", "athletics:icon", "u"))[1]
        self.addCleanup(setattr, S, "refetch", real_re)
        self.addCleanup(setattr, S, "fetchLogo", real_fetch)

    class _M:
        etag = modified = None

    def test_a_normal_run_takes_the_cheap_refresh(self):
        res = S.workOne(self._M(), self.ROW)
        self.assertEqual(self.seen, ["refetch"])
        self.assertTrue(res.get("unchanged"))

    def test_redo_rediscovers_from_the_home_page(self):
        res = S.workOne(self._M(), self.ROW, rediscover=True)
        self.assertEqual(self.seen, ["fetch"])
        self.assertEqual(res["kind"], "athletics:icon")

    def test_the_run_passes_the_flag_through(self):
        self.assertIn("workOne(manners, r, args.redo)",
                      read("scripts", "scrape_school_logos.py"))


class Crashproof(unittest.TestCase):
    """One malformed page must not end a two-hour run."""

    def test_a_worker_turns_any_exception_into_that_school_s_failure(self):
        def boom(*a, **k):
            raise ValueError("something in a page")
        real, S.fetchLogo = S.fetchLogo, boom
        self.addCleanup(setattr, S, "fetchLogo", real)
        row = ("Jesuit", "CA", "https://x", None, None, None, None, None, None)
        res = S.workOne(None, row)
        self.assertEqual((res["school"], res["png"]), ("Jesuit", None))
        self.assertIn("crashed ValueError", res["why"])


class Svg(unittest.TestCase):
    """Skipping SVG was pure lost coverage; reading it needs cairosvg, and
    not having cairosvg must stay a skip rather than a crash."""

    def test_without_cairosvg_an_svg_is_a_reason_not_an_exception(self):
        real = S._svgToPng
        S._svgToPng = lambda raw, px: None
        self.addCleanup(setattr, S, "_svgToPng", real)
        png, sha, why = S.normalise(b'<svg xmlns="http://www.w3.org/2000/svg"/>')
        self.assertEqual((png, sha), (None, None))
        self.assertIn("cairosvg", why)

    @unittest.skipIf(Image is None, "Pillow is not installed in this sandbox")
    def test_a_rasterised_svg_goes_through_the_normal_rules(self):
        buf = io.BytesIO()
        Image.new("RGBA", (256, 256), (9, 9, 9, 255)).save(buf, "PNG")
        real = S._svgToPng
        S._svgToPng = lambda raw, px: buf.getvalue()
        self.addCleanup(setattr, S, "_svgToPng", real)
        png, sha, size = S.normalise(b'<svg xmlns="http://www.w3.org/2000/svg"/>',
                                     kind="icon")
        self.assertEqual(size, (256, 256))
        self.assertTrue(png and len(sha) == 64)


# ===================================================================== #
#  ATHLETIC.NET: THE IDS WE ALREADY HAVE                                #
# ===================================================================== #

import anet_teams as A                                            # noqa: E402

# the real TeamNav/Team payload for Tufts (team 21480), trimmed
TUFTS = json.dumps({
    "currentSiteSupport": True, "isTeamCoach": False,
    "jwtTeamHome": "eyJ...", "seasonInfo": {"year": 2026, "seasonId": 2026},
    "team": {"IDTeam": 21480, "Name": "Tufts", "TeamCode": "Tuft", "Level": 8,
             "City": "Medford", "State": "MA", "ZipCode": "2155",
             "Country": "USA", "RegionID": 2034, "Address": None,
             "MascotUrl": "//lh3.googleusercontent.com/9iUjQmBwrwYS9chYCBF",
             "Website": "https://gotuftsjumbos.com/",
             "WebsiteSport": "https://gotuftsjumbos.com/"}}).encode()


class Anet(unittest.TestCase):
    """One call per team answers three problems: the crest, the ATHLETICS
    address, and where the school is."""

    class _Cur:
        def __init__(self, tables=("school_identity", "person_home_state")):
            self.tables, self.sql, self.params = tables, [], []

        def execute(self, sql, params=None):
            self.sql.append(sql)
            self.params.append(params)

        def fetchone(self):
            # _tableExists passes the name as a PARAMETER, not in the SQL
            args = self.params[-1] or ()
            want = (args[0] if args else "").replace("public.", "")
            return [f"public.{want}" if want in self.tables else None]

        def fetchall(self):
            return []

    # ---- the payload ---------------------------------------------------

    NAV = json.dumps({
        "team": {"ID": 21480, "Name": "Tufts", "Level": 8, "hasIndoor": True,
                 "City": "Medford", "State": "MA", "ZipCode": "2155",
                 "Mascot": "Jumbos", "MascotUrl": "//lh3.googleusercontent.com/9iUjQ"},
        "divisions": [{"id": 85463, "b": 79, "name": "  United States", "gender": "x"},
                      {"id": 85691, "b": 89, "name": "College", "gender": "x"},
                      {"id": 85733, "b": 2583, "name": "NCAA", "gender": "x"},
                      {"id": 85812, "b": 2587, "name": "DIII", "gender": "x"},
                      {"id": 85849, "b": 2671, "name": "NESCAC", "gender": "x"}],
        "customDivisions": [{"IDDivision": 89634, "DivName": "ECAC Div III"}],
        "grades": [{"IDGrade": 21, "GradeDesc": "Freshman"}]}).encode()

    def test_both_spellings_of_the_id_are_accepted(self):
        """⚠ TeamNav/Team returns team.ID and GetTeamCore returns
        team.IDTeam. Requiring IDTeam is what made the first real run
        report "200 application/json" with nothing in it."""
        self.assertEqual(A.parseTeam(self.NAV)["IDTeam"], 21480)
        self.assertEqual(A.parseTeam(TUFTS)["IDTeam"], 21480)

    def test_the_two_endpoints_are_merged_not_chosen_between(self):
        """They carry different fields: divisions, mascot and colours are
        TeamNav's; WebsiteSport, TeamCode and the season list are
        GetTeamCore's."""
        m = A.mergeTeams(A.parseTeam(self.NAV), A.parseTeam(TUFTS))
        self.assertEqual(m["Mascot"], "Jumbos")                 # nav only
        self.assertEqual(m["TeamCode"], "Tuft")                 # core only
        self.assertEqual(m["WebsiteSport"], "https://gotuftsjumbos.com/")
        self.assertEqual(m["IDTeam"], 21480)
        self.assertTrue(m["hasIndoor"])

    def test_a_merge_of_nothing_is_none(self):
        self.assertIsNone(A.mergeTeams(None, {}))

    def test_the_season_list_becomes_a_lifespan(self):
        t = A.parseTeam(json.dumps({
            "team": {"ID": 1}, "seasonInfo": {"seasons": [2026, 1949, 2024]}}).encode())
        self.assertEqual((t["_seasons"][0], t["_seasons"][-1]), (1949, 2026))

    def test_a_college_DOES_carry_its_division(self):
        """★ THE OWNER'S "they do not have the division" IS A HIGH SCHOOL
        fact. A college path is United States > College > NCAA > DIII >
        NESCAC, so `division` and `conference` are both right there -- only
        the HS side (no D1/D2 inside a section) is missing them."""
        names = [n for _d, _b, _i, n, _g in A.parseDivisions(self.NAV)]
        self.assertIn("DIII", names)
        self.assertIn("NESCAC", names)

    def test_a_custom_division_is_kept_but_flagged(self):
        """"ECAC Div III" is a real affiliation hanging off the tree with
        no depth of its own, so it is stored marked rather than as a rung."""
        t = A.parseTeam(self.NAV)
        self.assertEqual(t["_custom"], [{"IDDivision": 89634,
                                         "DivName": "ECAC Div III"}])
        src = read("scripts", "anet_teams.py")
        self.assertIn("custom   boolean NOT NULL DEFAULT false", src)

    def test_the_team_object_is_read_out_of_the_nav_payload(self):
        t = A.parseTeam(TUFTS)
        self.assertEqual((t["Name"], t["City"], t["State"], t["ZipCode"]),
                         ("Tufts", "Medford", "MA", "2155"))
        self.assertEqual(t["WebsiteSport"], "https://gotuftsjumbos.com/",
                         "the athletics site, handed over rather than guessed")

    def test_rubbish_is_none_rather_than_an_exception(self):
        for raw in (b"", b"<html>404</html>", b"{}", b'{"team": null}',
                    b'{"team": {"Name": "no id"}}'):
            self.assertIsNone(A.parseTeam(raw), raw)

    def test_the_crest_url_is_absolute_and_asks_google_for_512(self):
        """MascotUrl is protocol-relative and lives on Google's CDN, not
        anet's -- so the image costs anet nothing, and lh3 serves a sized
        copy for an =sN suffix."""
        got = A.mascotUrls(A.parseTeam(TUFTS))
        self.assertEqual(got[0],
                         "https://lh3.googleusercontent.com/9iUjQmBwrwYS9chYCBF=s512")
        self.assertEqual(got[1],
                         "https://lh3.googleusercontent.com/9iUjQmBwrwYS9chYCBF")

    def test_a_team_with_no_mascot_asks_for_nothing(self):
        for url in (None, "", "   ", "javascript:void(0)"):
            self.assertEqual(A.mascotUrls({"MascotUrl": url}), [], repr(url))

    def test_a_non_google_host_is_taken_as_it_is(self):
        self.assertEqual(A.mascotUrls({"MascotUrl": "https://cdn.x/a.png"}),
                         ["https://cdn.x/a.png"])

    # ---- the worklist ---------------------------------------------------

    def test_the_team_is_modal_per_school_AND_state(self):
        cur = self._Cur()
        A.teams(cur)
        sql = cur.sql[-1]
        self.assertIn("DISTINCT ON (school, state)", sql)
        self.assertIn("person_home_state", sql)
        self.assertIn("ORDER  BY si.n_athletes DESC", sql)

    def test_it_reads_both_sports_results_tables(self):
        cur = self._Cur()
        A.teams(cur)
        self.assertIn("FROM results\n", cur.sql[-1])
        self.assertIn("FROM results_tf\n", cur.sql[-1])

    def test_a_team_already_stored_is_skipped_unless_redo(self):
        cur = self._Cur()
        A.teams(cur)
        self.assertIn("AND a.team_id IS NULL", cur.sql[-1])
        cur = self._Cur()
        A.teams(cur, redo=True)
        self.assertNotIn("AND a.team_id IS NULL", cur.sql[-1])

    def test_it_survives_a_database_without_home_states(self):
        cur = self._Cur(tables=("school_identity",))
        A.teams(cur)
        self.assertNotIn("person_home_state", cur.sql[-1])
        self.assertIn("'' AS state", cur.sql[-1])

    def test_it_refuses_to_run_without_school_identity(self):
        with self.assertRaises(SystemExit):
            A.teams(self._Cur(tables=()))

    # ---- what it writes -------------------------------------------------

    def test_the_athletics_site_lands_in_the_address_book(self):
        cur = self._Cur()
        self.assertTrue(A.storeAddress(cur, "Tufts", "MA", A.parseTeam(TUFTS)))
        self.assertIn("INSERT INTO school_website", cur.sql[-1])
        self.assertIn("https://gotuftsjumbos.com/", cur.params[-1])

    def test_an_address_set_by_hand_is_never_overwritten(self):
        cur = self._Cur()
        A.storeAddress(cur, "Tufts", "MA", A.parseTeam(TUFTS))
        self.assertIn("source IS DISTINCT FROM 'manual'", cur.sql[-1])

    def test_a_team_with_no_website_writes_no_address(self):
        cur = self._Cur()
        self.assertFalse(A.storeAddress(cur, "X", "CA", {"Name": "X"}))
        self.assertEqual(cur.sql, [])

    def test_the_place_is_kept_for_elevation_and_identity(self):
        cur = self._Cur()
        A.storeTeam(cur, "Tufts", "MA", A.parseTeam(TUFTS))
        params = cur.params[-1]
        for want in (21480, "Medford", "MA", "2155", "USA", 8, 2034):
            self.assertIn(want, params, want)

    # ---- the two safety rails -------------------------------------------

    def test_robots_is_obeyed_unless_explicitly_overridden(self):
        src = read("scripts", "anet_teams.py")
        self.assertIn('ap.add_argument("--ignore-robots"', src)
        self.assertIn("manners.allowed = lambda url: (True, 0.0)", src)
        self.assertNotIn("default=True", src.split("--ignore-robots")[1][:200])

    def test_it_sends_the_client_header_anet_s_own_site_sends(self):
        """★ THE FIRST RUN GOT 200 application/json BACK WITH NO TEAM IN
        IT, which is what an API answers when it does not recognise the
        caller. scripts/scraper.py has sent anet-appinfo against
        GetResultsData3 for a year; this sends the same."""
        self.assertEqual(A.HEADERS.get("anet-appinfo"), "web:web:0:240")
        src = read("scripts", "anet_teams.py")
        self.assertEqual(src.count("extra=HEADERS"), 3,
                         "both endpoints and the probe")

    def test_the_failure_shows_what_came_back(self):
        """A diagnosis that discards the body is not a diagnosis. That was
        the actual bug the first time this ran."""
        src = read("scripts", "anet_teams.py")
        self.assertIn("What anet actually said", src)
        self.assertIn('raw[:600].decode("utf-8", "replace")', src)
        self.assertIn('ap.add_argument("--probe"', src)

    def test_extra_headers_reach_the_request(self):
        sent = {}

        def urlopen(req, timeout=None, **kw):
            sent.update(req.headers)
            return _Resp(b"{}", "application/json")
        real = S.urllib.request.urlopen
        S.urllib.request.urlopen = urlopen
        self.addCleanup(setattr, S.urllib.request, "urlopen", real)
        S.Manners(rate=0).get("https://x.org/a", extra={"Anet-Appinfo": "z"})
        self.assertEqual(sent.get("Anet-appinfo"), "z")

    def test_a_dead_endpoint_stops_the_run_early(self):
        src = read("scripts", "anet_teams.py")
        self.assertIn("if i == ABORT_AFTER and meta == 0:", src)
        self.assertIn("conn.rollback()", src)
        self.assertLessEqual(A.ABORT_AFTER, 25)

    def test_athlete_photos_are_not_taken(self):
        """Most of the people in this corpus are minors. The photo slot is
        theirs to fill (283), and nothing here goes near it."""
        src = read("scripts", "anet_teams.py")
        for never in ("GetAthletes", "photo", "Photo", "mugshot", "headshot"):
            self.assertNotIn(never, src.replace("Athlete photos are NOT taken", ""),
                             never)


# ===================================================================== #
#  WHAT ANET'S UNIT IDS MEAN                                            #
# ===================================================================== #

import anet_units as U                                            # noqa: E402


def _world():
    """A slice of NCS and CCS as anet nests it, with our own units beside
    it. Realistic in the one way that matters: some areas hold two leagues
    and some hold one, because that difference is what decides whether a
    unit can be told apart from its parent at all."""
    obs = []

    def school(name, units, path):
        for depth, (b, nm) in enumerate(path):
            obs.append((b, "xc", depth + 2, nm, name, "CA", units))

    CA = ((278, "California"),)
    NCS = CA + ((319, "North Coast"),)
    TV = NCS + ((334, "Valley"),)
    for i in range(10):                       # EBAL, inside Tri-Valley
        school(f"E{i}", {"league": "EBAL", "area": "Tri-Valley Area",
                         "section": "NCS", "state_unit": "CA"},
               TV + ((337, "East Bay Ath."),))
    school("Odd", {"league": "DFAL", "area": "Tri-Valley Area",   # mislabelled
                   "section": "NCS", "state_unit": "CA"},
           TV + ((337, "East Bay Ath."),))
    school("Gap", {"area": "Tri-Valley Area", "section": "NCS",   # no league
                   "state_unit": "CA"}, TV + ((337, "East Bay Ath."),))
    for i in range(8):                        # DFAL, also inside Tri-Valley
        school(f"F{i}", {"league": "DFAL", "area": "Tri-Valley Area",
                         "section": "NCS", "state_unit": "CA"},
               TV + ((338, "Diablo Foothill"),))
    for i in range(9):                        # Marin: one league in one area
        school(f"M{i}", {"league": "MCAL", "area": "Marin", "section": "NCS",
                         "state_unit": "CA"},
               NCS + ((400, "Marin"), (401, "Marin Co. Ath.")))
    for i in range(7):                        # a second section entirely
        school(f"S{i}", {"league": "WCAL", "area": "Bay", "section": "CCS",
                         "state_unit": "CA"},
               CA + ((500, "Central Coast"), (501, "Bay"), (502, "West Cath.")))
    return obs


class AnetUnits(unittest.TestCase):
    """anet's names are truncated ("North Coast", "Valley", "East Bay
    Ath.") and carry no competitive division, so we throw them away and
    learn each id's meaning from the units we already infer."""

    def setUp(self):
        self.obs = _world()
        self.learned = U.learn(self.obs)

    def name(self, base):
        got = self.learned.get((base, "xc"))
        return (got[0], got[1]) if got else None

    def test_each_rung_gets_its_own_unit(self):
        self.assertEqual(self.name(278), ("state_unit", "CA"))
        self.assertEqual(self.name(319), ("section", "NCS"))
        self.assertEqual(self.name(334), ("area", "Tri-Valley Area"))
        self.assertEqual(self.name(337), ("league", "EBAL"))
        self.assertEqual(self.name(338), ("league", "DFAL"))

    def test_a_league_is_not_named_after_its_section(self):
        """★ THE TRAP, and the first version fell in it. Every EBAL school
        is also an NCS school, so a plain majority vote inside b=337 elects
        section='NCS' as easily as it does inside b=319. What separates
        them is the other direction -- nearly every EBAL school carries
        b=337, while only a fraction of NCS schools do."""
        self.assertEqual(self.name(337), ("league", "EBAL"))
        self.assertNotEqual(self.name(337), ("section", "NCS"))

    def test_a_parent_with_one_child_is_split_by_anet_s_own_depth(self):
        """Marin the area and MCAL the league hold exactly the same
        schools; nothing in the data separates them, but the ids that
        share a school set are the rungs of one path."""
        self.assertEqual(self.name(400), ("area", "Marin"))
        self.assertEqual(self.name(401), ("league", "MCAL"))
        self.assertEqual(self.name(500), ("section", "CCS"))
        self.assertEqual(self.name(501), ("area", "Bay"))
        self.assertEqual(self.name(502), ("league", "WCAL"))

    def test_and_says_when_it_could_not_actually_tell(self):
        """The repo's own rule: conflict is recorded, not resolved."""
        self.assertTrue(self.learned[(400, "xc")][6], "Marin was a coin toss")
        self.assertFalse(self.learned[(337, "xc")][6], "EBAL was not")

    def test_a_school_contradicting_its_own_id_is_flagged(self):
        self.assertEqual(U.disagreements(self.obs, self.learned),
                         [("Odd", "CA", "xc", "league", "DFAL", "EBAL")])

    def test_a_school_with_no_league_is_a_gap_anet_can_fill(self):
        self.assertEqual(U.gaps(self.obs, self.learned),
                         [("Gap", "CA", "xc", "league", "EBAL", 337)])

    def test_an_id_too_thin_or_too_scattered_is_left_unnamed(self):
        self.assertEqual(U.learn(self.obs, min_support=50), {})
        self.assertEqual(U.learn(self.obs, min_match=1.01), {})

    def test_a_state_that_puts_its_class_in_the_tree_gets_the_class(self):
        """★ CALIFORNIA MISLED US. Its path carries no division at all --
        US > HS > California > North Coast > Valley > East Bay Ath. -- so
        the first version excluded class and the *_div columns outright.
        Then the first real run named b=197 "4A" and b=698 "6A" and b=1694
        "Division 1": Washington, Oregon and Michigan put the class or the
        division IN the tree, and with no column to match they each fell
        back to their parent and came out as the STATE. Which is wrong."""
        obs = []

        def school(name, units, path):
            for depth, (b, nm) in enumerate(path):
                obs.append((b, "xc", depth + 2, nm, name, "WA", units))

        WA = ((196, "Washington"),)
        for i in range(9):        # 4A schools, two leagues
            school(f"A{i}", {"state_unit": "WA", "class": "4A",
                             "league": "KingCo" if i < 5 else "NPSL"},
                   WA + ((197, "4A"), (900 if i < 5 else 901, "L")))
        for i in range(8):        # 3A schools
            school(f"B{i}", {"state_unit": "WA", "class": "3A",
                             "league": "Metro"},
                   WA + ((198, "3A"), (902, "Metro")))
        learned = U.learn(obs)
        self.assertEqual(learned[(196, "xc")][:2], ("state_unit", "WA"))
        self.assertEqual(learned[(197, "xc")][:2], ("class", "4A"),
                         "not state_unit='WA', which is what it did before")
        self.assertEqual(learned[(198, "xc")][:2], ("class", "3A"))

    def test_california_still_comes_out_right(self):
        """The state with no division in its path must not regress."""
        self.assertEqual(self.name(278), ("state_unit", "CA"))
        self.assertEqual(self.name(319), ("section", "NCS"))

    def test_the_report_runs_on_all_of_it(self):
        dis = U.disagreements(self.obs, self.learned)
        gap = U.gaps(self.obs, self.learned)
        text = U.report(self.obs, self.learned, dis, gap)
        self.assertIn("EBAL", text)
        self.assertIn("AMBIGUOUS", text)
        self.assertIn("we say league='DFAL'", text)

    def test_nothing_here_writes_to_school_unit(self):
        """The inference stays the source of truth; this is a proposal."""
        src = read("scripts", "anet_units.py")
        self.assertNotIn("UPDATE school_unit", src)
        self.assertNotIn("INSERT INTO school_unit ", src)


class AnetDivisions(unittest.TestCase):
    def test_the_array_order_is_the_hierarchy(self):
        raw = json.dumps({"divisions": [
            {"id": 167952, "b": 79, "name": " United States", "gender": "x"},
            {"id": 168416, "b": 2, "name": "High School", "gender": "x"},
            {"id": 168546, "b": 278, "name": "California", "gender": "x"},
            {"id": 168618, "b": 319, "name": "North Coast", "gender": "x"},
            {"id": 168639, "b": 334, "name": "Valley", "gender": "x"},
            {"id": 168642, "b": 337, "name": "East Bay Ath.", "gender": "x"},
        ]}).encode()
        got = A.parseDivisions(raw)
        self.assertEqual([(d, b) for d, b, _i, _n, _g in got],
                         [(0, 79), (1, 2), (2, 278), (3, 319), (4, 334), (5, 337)])
        self.assertEqual(got[0][3], "United States", "the leading space is trimmed")

    def test_a_row_with_no_stable_id_is_dropped(self):
        raw = json.dumps({"divisions": [{"id": 1, "name": "no b"},
                                        {"b": 5, "id": 2, "name": "ok"}]}).encode()
        self.assertEqual([b for _d, b, _i, _n, _g in A.parseDivisions(raw)], [5])

    def test_rubbish_is_no_divisions(self):
        for raw in (b"", b"<html>", b"{}", b'{"divisions": null}',
                    b'{"divisions": "nope"}', b'{"divisions": [1, 2]}'):
            self.assertEqual(A.parseDivisions(raw), [], raw)

    def test_both_sports_are_taken_because_they_disagree(self):
        src = read("scripts", "anet_teams.py")
        self.assertIn('ap.add_argument("--sports", default="xc,tf"', src)
        self.assertIn("PRIMARY KEY (team_id, sport, base_id)", src)


# ===================================================================== #
#  THE WHOLE THING, AGAINST A REAL SERVER ON LOOPBACK                   #
# ===================================================================== #

@unittest.skipIf(Image is None, "Pillow is not installed in this sandbox")
class EndToEnd(unittest.TestCase):
    """★ THE STUBS CANNOT CATCH EVERYTHING. Two of this job's three bugs
    after the first run were in the seams -- robots and pacing and threads
    and Pillow all at once -- so this stands up real HTTP servers on
    loopback and runs the real code against them. No outside network.

    The fixture IS the owner's case: a school site with a perfectly good
    institutional icon that links to an athletics site with a different
    one."""

    INST, ENG = (20, 20, 90), (150, 20, 40)

    def _png(self, size, colour):
        b = io.BytesIO()
        Image.new("RGBA", size, colour + (255,)).save(b, "PNG")
        return b.getvalue()

    def _serve(self, pages):
        import http.server
        import socketserver
        import threading
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                outer.hits.append(self.path)
                got = pages.get(self.path)
                if got is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                body, ctype = got
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), H)
        srv.daemon_threads = True
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        return srv.server_address[1]

    ROBOTS = (b"User-agent: *\nAllow: /\n", "text/plain")

    def setUp(self):
        self.hits = []

    def test_the_athletics_mark_wins_and_the_banner_is_never_fetched(self):
        ath_port = self._serve({
            "/robots.txt": self.ROBOTS,
            "/": (b'<head><link rel="apple-touch-icon" href="/eng.png"></head>',
                  "text/html"),
            "/eng.png": (self._png((256, 256), self.ENG), "image/png")})
        school_port = self._serve({
            "/robots.txt": self.ROBOTS,
            "/": (f'<head><meta property="og:image" content="/banner.jpg">'
                  f'<link rel="apple-touch-icon" sizes="180x180" href="/inst.png">'
                  f'</head><body><a href="http://127.0.0.1:{ath_port}/">Athletics</a>'
                  f'</body>'.encode(), "text/html"),
            "/inst.png": (self._png((180, 180), self.INST), "image/png"),
            "/banner.jpg": (self._png((1200, 630), (0, 0, 0)), "image/png")})

        png, sha, kind, src = S.fetchLogo(S.Manners(rate=0),
                                          f"http://127.0.0.1:{school_port}/")
        self.assertEqual(kind, "athletics:apple-touch")
        self.assertTrue(src.endswith("/eng.png"))
        im = Image.open(io.BytesIO(png))
        self.assertEqual(im.size, (SL.LOGO_PX, SL.LOGO_PX))
        self.assertEqual(im.convert("RGB").getpixel((256, 256)), self.ENG,
                         "the crest on the card is the ENGINEERS mark")
        self.assertEqual(len(sha), 64)
        self.assertNotIn("/banner.jpg", self.hits)
        self.assertNotIn("/inst.png", self.hits,
                         "the athletics link is read BEFORE any icon is fetched")

    def test_robots_is_obeyed_against_a_real_server(self):
        port = self._serve({
            "/robots.txt": (b"User-agent: *\nDisallow: /\n", "text/plain"),
            "/": (b"<head></head>", "text/html")})
        png, _sha, _kind, why = S.fetchLogo(S.Manners(rate=0),
                                            f"http://127.0.0.1:{port}/")
        self.assertIsNone(png)
        self.assertIn("robots", why)
        self.assertEqual(self.hits, ["/robots.txt"])

    def test_many_hosts_at_once_do_not_wait_on_each_other(self):
        """★ THE FIX FOR "way too slow". Twelve schools, a full second
        between requests to any ONE of them, twelve workers: about a
        second in total, not twelve."""
        import concurrent.futures as cf
        ports = [self._serve({
            "/robots.txt": self.ROBOTS,
            "/": (b'<head><link rel="icon" sizes="128x128" href="/i.png"></head>',
                  "text/html"),
            "/i.png": (self._png((128, 128), self.ENG), "image/png")})
            for _ in range(12)]
        manners = S.Manners(rate=0.4)
        t0 = time.time()
        with cf.ThreadPoolExecutor(max_workers=12) as pool:
            out = list(pool.map(
                lambda p: S.fetchLogo(manners, f"http://127.0.0.1:{p}/"), ports))
        spent = time.time() - t0
        self.assertTrue(all(o[0] for o in out), "every one of them got a crest")
        # three requests per host at 0.4s = 1.2s whatever the width; one
        # global clock would be 12 x 1.2 = 14s
        self.assertLess(spent, 3.0,
                        f"{spent:.1f}s for 12 hosts: the pace is serialising")


# ===================================================================== #
#  ONE CREST ON FORTY PAGES                                             #
# ===================================================================== #

class Shared(unittest.TestCase):
    def test_a_district_crest_is_flagged(self):
        rows = [(f"School {i}", "abc") for i in range(S.SHARED_MIN)]
        self.assertEqual(S.sharedShas(rows), {"abc"})

    def test_one_short_of_the_threshold_is_not(self):
        rows = [(f"School {i}", "abc") for i in range(S.SHARED_MIN - 1)]
        self.assertEqual(S.sharedShas(rows), set())

    def test_one_school_in_two_states_is_still_one_school(self):
        """school_identity splits a shared name into clusters, and both
        clusters store the same crest. That is not a district."""
        rows = [("Kingston", "abc")] * 9
        self.assertEqual(S.sharedShas(rows), set())

    def test_a_missing_hash_is_not_a_match(self):
        self.assertEqual(S.sharedShas([(f"S{i}", None) for i in range(9)]), set())


# ===================================================================== #
#  WHICH SCHOOL A DIRECTORY ROW IS                                      #
# ===================================================================== #

class Names(unittest.TestCase):
    def test_high_school_and_its_spellings_come_off(self):
        for spelling in ("De La Salle High School", "De La Salle HS",
                         "De La Salle H.S.", "De La Salle Senior High",
                         "DE LA SALLE  high school"):
            self.assertEqual(W.normSchool(spelling), "de la salle", spelling)

    def test_university_and_college_stay_on(self):
        """Dropping them makes Boston College and Boston University one
        school, and one of them then wears the other's crest."""
        self.assertNotEqual(W.normSchool("Boston College"),
                            W.normSchool("Boston University"))

    def test_the_usual_abbreviations_are_expanded(self):
        self.assertEqual(W.normSchool("Mt. Vernon"), W.normSchool("Mount Vernon"))
        self.assertEqual(W.normSchool("St. Mary's"), W.normSchool("Saint Marys"))
        self.assertEqual(W.normSchool("Ft. Collins HS"), "fort collins")

    def test_a_url_cell_is_read_or_refused(self):
        self.assertEqual(W.cleanUrl("www.chs.k12.ca.us"), "http://www.chs.k12.ca.us/")
        self.assertEqual(W.cleanUrl("HTTPS://A.EDU/x?y=1#f"), "https://a.edu/x")
        for junk in ("", "  ", "N/A", "none", "-", "mailto:x@y.z", "not applicable"):
            self.assertIsNone(W.cleanUrl(junk), junk)


class Matching(unittest.TestCase):
    ROWS = [
        {"name": "De La Salle High School", "state": "CA",
         "url": "https://dlshs.org/", "source": "csv"},
        {"name": "Highland High School", "state": "UT",
         "url": "https://highland.slcschools.org/", "source": "csv"},
        {"name": "Highland High School", "state": "CA",
         "url": "https://highland.buhsd.org/", "source": "csv"},
        {"name": "Brigham Young University", "state": "UT",
         "url": "https://byu.edu/", "source": "wikidata"},
    ]

    def test_a_clean_match_is_taken(self):
        matched, review = W.matchSchools([("De La Salle", "CA")], self.ROWS)
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0]["url"], "https://dlshs.org/")
        self.assertEqual(review, [])

    def test_two_schools_of_one_name_take_their_own_state(self):
        matched, _r = W.matchSchools([("Highland", "UT"), ("Highland", "CA")], self.ROWS)
        self.assertEqual({m["state"]: m["url"] for m in matched},
                         {"UT": "https://highland.slcschools.org/",
                          "CA": "https://highland.buhsd.org/"})

    def test_the_wrong_state_is_reviewed_not_guessed(self):
        _m, review = W.matchSchools([("Highland", "NY")], self.ROWS)
        self.assertEqual([(r[0], r[2]) for r in review], [("Highland", "unmatched")])

    def test_a_name_two_rows_disagree_about_is_reviewed(self):
        rows = self.ROWS + [{"name": "De La Salle", "state": "CA",
                             "url": "https://other.example/", "source": "csv"}]
        matched, review = W.matchSchools([("De La Salle", "CA")], rows)
        self.assertEqual(matched, [])
        self.assertEqual(review[0][2], "ambiguous")

    def test_a_feed_short_name_matches_only_for_a_known_college(self):
        """"BYU" reaches "Brigham Young University" through the college
        directory's own normalisation -- and only for a name the directory
        knows, so no high school gets that licence."""
        ckey = W.collegeKey("BYU")
        matched, _r = W.matchSchools([("BYU", "UT")], self.ROWS, colleges={ckey})
        self.assertEqual([m["url"] for m in matched], ["https://byu.edu/"])
        matched, review = W.matchSchools([("BYU", "UT")], self.ROWS, colleges=set())
        self.assertEqual(matched, [])
        self.assertEqual(review[0][2], "unmatched")

    def test_a_row_with_no_website_and_no_logo_is_reviewed(self):
        rows = [{"name": "Nowhere High School", "state": "MT", "url": None,
                 "source": "csv"}]
        matched, review = W.matchSchools([("Nowhere", "MT")], rows)
        self.assertEqual(matched, [])
        self.assertEqual(review[0][2], "no website")

    def test_a_logo_file_alone_is_enough(self):
        rows = [{"name": "Nowhere High School", "state": "MT", "url": None,
                 "direct_logo": "https://commons/x.png", "source": "wikidata"}]
        matched, _r = W.matchSchools([("Nowhere", "MT")], rows)
        self.assertEqual(matched[0]["direct_logo"], "https://commons/x.png")


class Csv(unittest.TestCase):
    def _write(self, text, suffix=".csv"):
        import tempfile
        fh = tempfile.NamedTemporaryFile("w", suffix=suffix, delete=False,
                                         encoding="utf-8")
        fh.write(text)
        fh.close()
        self.addCleanup(os.unlink, fh.name)
        return fh.name

    def test_the_ccd_spelling(self):
        path = self._write("NCESSCH,SCH_NAME,ST,WEBSITE\n"
                           "060001,Piedmont High School,CA,www.piedmont.k12.ca.us\n")
        rows = W.readCsv(path)
        self.assertEqual(rows, [{"name": "Piedmont High School", "state": "CA",
                                 "url": "http://www.piedmont.k12.ca.us/",
                                 "source": "csv"}])

    def test_the_private_survey_spelling_and_a_tab_file(self):
        path = self._write("PINST\tPSTABB\tURL\nSaint Ignatius\tCA\thttps://si.org\n",
                           suffix=".tsv")
        self.assertEqual(W.readCsv(path)[0]["url"], "https://si.org/")

    def test_a_file_with_no_website_column_says_what_it_saw(self):
        path = self._write("SCH_NAME,ST\nPiedmont,CA\n")
        with self.assertRaises(SystemExit) as e:
            W.readCsv(path)
        self.assertIn("site", str(e.exception))
        self.assertIn("sch_name", str(e.exception))


# ===================================================================== #
#  THE SITE'S SIDE                                                      #
# ===================================================================== #

class Serving(unittest.TestCase):
    def test_the_scraper_and_the_site_agree_on_the_file_name(self):
        self.assertEqual(S.fileFor("Jesuit", "CA"), SL.fileFor("Jesuit", "CA"))
        self.assertNotEqual(SL.fileFor("Jesuit", "CA"), SL.fileFor("Jesuit", "OR"))
        self.assertEqual(SL.fileFor("Jesuit", "ca"), SL.fileFor("Jesuit", "CA"))

    def test_a_free_text_school_name_never_reaches_the_filesystem(self):
        name = SL.fileFor("Chisago Lakes/Rush City", "MN")
        self.assertRegex(name, r"^[0-9a-f]{16}\.png$")
        self.assertTrue(SL.pathFor(name).startswith(SL.LOGO_DIR))

    def test_a_stored_name_that_is_not_ours_is_refused(self):
        for junk in ("../../etc/passwd", "/etc/passwd", "x.png", "", None,
                     "deadbeefdeadbeef.png.exe"):
            self.assertIsNone(SL.pathFor(junk), junk)

    def test_the_url_encodes_a_slash_and_carries_the_state(self):
        self.assertEqual(SL.logoUrl("Chisago Lakes/Rush City", "mn"),
                         "/img/school/Chisago%20Lakes%2FRush%20City.png?state=MN")
        self.assertEqual(SL.logoUrl("Jesuit"), "/img/school/Jesuit.png")

    def test_the_row_for_a_state_and_the_refusal_to_guess(self):
        rows = [{"state": "WA", "path": "a"}, {"state": "MO", "path": "b"}]
        self.assertEqual(SL.pickRow(rows, "WA")["path"], "a")
        self.assertIsNone(SL.pickRow(rows, ""),
                          "two real schools share this name; a guess is a wrong crest")
        self.assertEqual(SL.pickRow([{"state": "UT", "path": "d"}], "")["path"], "d")
        self.assertEqual(SL.pickRow([{"state": "", "path": "c"}], "CA")["path"], "c")


class Degrades(unittest.TestCase):
    """Every failure is None, because the site renders without a crest."""

    class _Cur:
        def __init__(self, boom=None, rows=None):
            self.boom, self.rows, self.connection = boom, rows or [], self
            self.rolled = False

        def execute(self, sql, args=None):
            if self.boom:
                raise self.boom

        def fetchone(self):
            return [None]

        def fetchall(self):
            return self.rows

        def rollback(self):
            self.rolled = True

    def setUp(self):
        SL._HAVE.update({"at": 0.0, "ok": False})
        self.addCleanup(SL._HAVE.update, {"at": 0.0, "ok": False})

    def test_no_table_is_no_crest_and_no_second_query(self):
        cur = self._Cur()
        self.assertIsNone(SL.logoRow(cur, "Jesuit", "CA"))
        self.assertFalse(SL.tableExists(cur))

    def test_a_broken_query_rolls_back_rather_than_poisoning_the_page(self):
        cur = self._Cur(boom=RuntimeError("no such column"))
        self.assertFalse(SL.tableExists(cur, force=True))
        self.assertTrue(cur.rolled)

    def test_an_override_of_none_suppresses_a_crest_that_exists(self):
        SL._HAVE.update({"at": S.time.time(), "ok": True})
        cur = self._Cur(rows=[{"school": "X", "state": "CA", "path": "a" * 16 + ".png",
                               "kind": "icon", "source_url": "u", "shared": False,
                               "override": "none"}])
        self.assertIsNone(SL.logoPath(cur, "X", "CA"))

    def test_a_district_crest_does_not_serve_but_an_override_publishes_it(self):
        SL._HAVE.update({"at": S.time.time(), "ok": True})
        row = {"school": "X", "state": "CA", "path": "b" * 16 + ".png",
               "kind": "icon", "source_url": "u", "shared": True, "override": None}
        cur = self._Cur(rows=[row])
        self.assertIsNone(SL.logoPath(cur, "X", "CA"))
        row["override"] = "https://x.org/real.png"
        # still None here only because the file is not on this disk; the
        # shared flag is no longer the reason
        self.assertIsNotNone(SL.logoRow(cur, "X", "CA"))


# ===================================================================== #
#  THE MENTION: ONE CREST BESIDE EVERY SCHOOL'S NAME                    #
# ===================================================================== #

class Mentions(unittest.TestCase):
    """crestUrl and its helpers answer from the start-up cache and never
    query: they are called once per row of every table on the site."""

    def setUp(self):
        SL._CRESTS.update(loaded=True, map={
            "Jesuit": [("CA", "ab12cd34")],
            "Highland": [("UT", "d1"), ("CA", "d2")],   # two schools, one name
            "Nameless": [("", "e5")],                   # stored under no state
        })
        self.addCleanup(SL._CRESTS.update, {"loaded": False, "map": {}})

    def test_a_school_with_a_crest_gets_a_url_with_no_query(self):
        self.assertEqual(SL.crestUrl("Jesuit", "CA"),
                         "/img/school/Jesuit.png?state=CA&v=ab12cd34")
        self.assertEqual(SL.crestUrl("Jesuit", "CA", 64),
                         "/img/school/Jesuit.png?state=CA&px=64&v=ab12cd34")

    def test_every_size_of_one_crest_carries_the_same_version(self):
        """★ THE BUG THIS EXISTS FOR (owner, 2026-09-13): Tufts showed the
        new crest on a race page and the old one on its school page. Two
        sizes are two URLs, the file's URL does not change when its
        CONTENTS do, and the route hands out a week of cache -- so each URL
        kept whatever it happened to fetch first."""
        small = SL.crestUrl("Jesuit", "CA", 64)
        big = SL.crestUrl("Jesuit", "CA", 128)
        self.assertNotEqual(small, big)
        self.assertTrue(small.endswith("v=ab12cd34"))
        self.assertTrue(big.endswith("v=ab12cd34"))
        SL._CRESTS["map"]["Jesuit"] = [("CA", "99999999")]
        self.assertNotEqual(SL.crestUrl("Jesuit", "CA", 64), small,
                            "a new picture must be a new URL")

    def test_a_mention_with_no_state_resolves_the_same_as_the_page(self):
        """A race row passes no state and the school page passes one; if
        they resolved differently the two would show different crests."""
        self.assertEqual(SL.crestUrl("Jesuit", None, 64),
                         SL.crestUrl("Jesuit", "CA", 64))

    def test_a_school_with_none_gets_nothing_at_all(self):
        self.assertIsNone(SL.crestUrl("Nobody", "CA"))
        self.assertEqual(SL.crestImg("Nobody", "CA"), "")

    def test_a_name_two_schools_share_takes_the_state_it_is_given(self):
        self.assertEqual(SL.crestState("Highland", "UT")[0], "UT")
        self.assertIsNone(SL.crestState("Highland", "NY"))

    def test_and_with_no_state_it_follows_the_link(self):
        """★ owner, 2026-09-13: a race page's team row showed Amherst's
        link going to the right school and NO crest beside it. Refusing to
        guess looked safe, but the link is not refusing -- /school/Amherst
        with no state lands on the primary cluster, so the crest has to
        land there too. Guessing DIFFERENTLY from the link is the bug;
        guessing the same way is the fix."""
        import school_identity as SI
        SI._LABELS.update(loaded=True, map={"Highland": "UT"})
        self.addCleanup(SI._LABELS.update, {"loaded": False, "map": {}})
        self.assertEqual(SL.crestState("Highland", None)[0], "UT")

    def test_but_a_name_the_identity_cannot_place_still_shows_nothing(self):
        import school_identity as SI
        SI._LABELS.update(loaded=True, map={})
        self.addCleanup(SI._LABELS.update, {"loaded": False, "map": {}})
        self.assertIsNone(SL.crestState("Highland", None))

    def test_one_row_answers_without_a_state_and_a_stateless_row_answers_for_any(self):
        self.assertEqual(SL.crestState("Jesuit", None)[0], "CA")
        self.assertEqual(SL.crestState("Nameless", "TX")[0], "")
        self.assertEqual(SL.crestUrl("Nameless", "TX"),
                         "/img/school/Nameless.png?v=e5")

    def test_the_markup_escapes_a_scraped_name(self):
        """School names are free text: 'Smith & "Jones"' is the kind of
        thing that breaks a page written with an f-string."""
        SL._CRESTS["map"]['Smith & "Jones"'] = [("CA", "f0")]
        img = str(SL.crestImg('Smith & "Jones"', "CA"))
        self.assertIn("%26", img)                  # the & is encoded in the URL
        self.assertIn("&amp;px=64", img)           # and the separator escaped
        self.assertNotIn('"Jones"', img)
        self.assertIn('alt=""', img)               # decorative: the name follows
        self.assertIn('loading="lazy"', img)

    def test_the_board_rows_are_stamped_only_where_there_is_one(self):
        rows = [{"school": "Jesuit", "state": "CA"},
                {"school": "Nobody", "state": "CA"},
                {"school": None, "state": None}]
        SL.stampCrests(rows)
        self.assertEqual(rows[0]["crest"],
                         "/img/school/Jesuit.png?state=CA&px=64&v=ab12cd34")
        self.assertNotIn("crest", rows[1])
        self.assertNotIn("crest", rows[2])

    def test_a_search_hit_is_read_back_off_its_link(self):
        self.assertEqual(SL.crestUrlForLink("/school/Highland?state=UT"),
                         "/img/school/Highland.png?state=UT&px=64&v=d1")
        self.assertEqual(SL.crestUrlForLink("/school/Jesuit"),
                         "/img/school/Jesuit.png?state=CA&px=64&v=ab12cd34")
        self.assertIsNone(SL.crestUrlForLink("/school/Highland"))
        for junk in ("/athlete/12", "", None, "/schools/ca", "https://x/school/Y"):
            self.assertIsNone(SL.crestUrlForLink(junk), junk)

    def test_an_empty_cache_is_an_empty_site(self):
        SL._CRESTS.update({"loaded": True, "map": {}})
        self.assertIsNone(SL.crestUrl("Jesuit", "CA"))
        self.assertEqual(SL.crestImg("Jesuit", "CA"), "")
        self.assertEqual(SL.stampCrests([{"school": "Jesuit"}]), [{"school": "Jesuit"}])


class Thumbs(unittest.TestCase):
    """A results table names forty schools; none of them wants 512 px."""

    def test_a_size_we_do_not_offer_serves_the_file_itself(self):
        self.assertEqual(SL.thumbPath("/x/a.png", None), "/x/a.png")
        self.assertEqual(SL.thumbPath("/x/a.png", 999), "/x/a.png")

    def test_an_unwritable_cache_serves_the_file_itself(self):
        self.assertEqual(SL.thumbPath("/no/such/file.png", 64), "/no/such/file.png")

    @unittest.skipIf(Image is None, "Pillow is not installed in this sandbox")
    def test_a_thumb_is_drawn_once_and_reused(self):
        import tempfile
        d = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, d, True)
        big = os.path.join(d, "a" * 16 + ".png")
        Image.new("RGBA", (512, 512), (1, 2, 3, 255)).save(big)
        old_dir, SL.THUMB_DIR = SL.THUMB_DIR, os.path.join(d, "thumbs")
        self.addCleanup(setattr, SL, "THUMB_DIR", old_dir)
        small = SL.thumbPath(big, 64)
        self.assertNotEqual(small, big)
        self.assertEqual(Image.open(small).size, (64, 64))
        self.assertLess(os.path.getsize(small), os.path.getsize(big))
        self.assertEqual(SL.thumbPath(big, 64), small)          # reused


class NoDeadLinks(unittest.TestCase):
    """★ "Unattached" HAS NO PAGE, and three separate places were offering
    one anyway (owner, 2026-09-13). school_page 404s every name
    panels.isTeamName rejects, so a link to one is a dead link, a search
    hit for one goes nowhere, and a sitemap full of them costs crawl budget
    and the sitemap's own credibility."""

    def test_every_school_link_in_every_template_is_guarded(self):
        import glob
        import re
        for f in sorted(glob.glob(os.path.join(_ROOT, "racecast", "templates",
                                               "*.html"))):
            name = os.path.basename(f)
            if name in ("school.html", "school_prs.html"):
                continue           # its own page: navigation, not a mention
            with io.open(f, encoding="utf-8") as fh:
                for n, line in enumerate(fh, 1):
                    if re.search(r'href="/school/', line):
                        self.assertIn("is_team", line, f"{name}:{n}")

    def test_the_sitemap_and_the_search_index_both_filter(self):
        self.assertIn("if isTeamName(r[0])", read("racecast", "build_sitemap.py"))
        self.assertIn("if not s or not isTeamName(s)",
                      read("racecast", "search_index.py"))

    def test_the_filter_rejects_what_it_should(self):
        try:                            # panels imports psycopg2; the box has it
            from panels import isTeamName
        except ImportError as exc:      # pragma: no cover
            self.skipTest(str(exc))
        for junk in ("Unattached", "unattached", "Independent", "No Team",
                     "SW Individuals -6 (AZ)"):
            self.assertFalse(isTeamName(junk), junk)
        for real in ("Amherst", "De La Salle", "Chisago Lakes/Rush City"):
            self.assertTrue(isTeamName(real), real)


class OneCourseOneUrl(unittest.TestCase):
    """⚠ course_difficulties IS KEYED THE WAY THE ENGINE KEYS A CELL --
    "XC:<venue>", and under --era-years once per two-year era. The site
    links to the bare name. Anything building a URL off that table has to
    come through courseDisplayName or it emits a URL nothing links to,
    once per era."""

    def setUp(self):
        sys.path.insert(0, os.path.join(_ROOT, "racecast"))
        from courses import courseDisplayName
        self.name = courseDisplayName

    def test_the_prefix_and_the_era_both_come_off(self):
        self.assertEqual(self.name("XC:Crystal Springs@e3"), "Crystal Springs")
        self.assertEqual(self.name("XC:Crystal Springs"), "Crystal Springs")
        self.assertEqual(self.name("TF:Hayward Field"), "Hayward Field")

    def test_an_unprefixed_name_is_left_alone(self):
        self.assertEqual(self.name("Mt. SAC"), "Mt. SAC")

    def test_nothing_is_empty_rather_than_an_exception(self):
        for junk in (None, "", "   ", "XC:", "XC:@e2"):
            self.assertEqual(self.name(junk), "", repr(junk))

    def test_the_eras_collapse_to_one_row(self):
        keys = ["XC:Woodward Park@e1", "XC:Woodward Park@e2", "XC:Woodward Park"]
        self.assertEqual({self.name(k) for k in keys}, {"Woodward Park"})

    def test_both_builders_go_through_it(self):
        self.assertIn("courseDisplayName", read("racecast", "build_sitemap.py"))
        self.assertIn("courseDisplayName", read("racecast", "search_index.py"))


class CollegeDirectoryScope(unittest.TestCase):
    """⚠ THE DIRECTORY IS KEYED ON THE NAME ALONE, so every "Amherst" in
    the country took Amherst College's NCAA DIII -- including Amherst,
    Nebraska, which is a high school and a middle school (owner,
    2026-09-13). And excluding the whole NAME from the exact pass then
    denied Nebraska the units it does have."""

    def test_the_campus_pass_is_gated_on_a_college_pool(self):
        src = read("racecast", "build_ranking_results.py")
        i = src.index("WHERE s.school = c.school")
        self.assertIn("s.pool LIKE 'college", src[i:i + 120])

    def test_and_it_no_longer_claims_the_name_from_everyone_else(self):
        src = read("racecast", "build_ranking_results.py")
        self.assertNotIn("s.school NOT IN (SELECT school FROM tmp_campus)", src)


class LevelSplit(unittest.TestCase):
    """★ Amherst (MA) IS TWO SCHOOLS: Amherst College (NESCAC) and Amherst
    Regional Middle School. One name, one state, one page, one crest, and
    NESCAC written over seventh graders. A home state cannot separate
    them; the level can, and the pool carries it."""

    def test_the_level_comes_off_the_pool(self):
        sys.path.insert(0, os.path.join(_ROOT, "racecast"))
        from school_identity import levelOf
        self.assertEqual(levelOf("college_m"), "college")
        self.assertEqual(levelOf("hs_f"), "hs")
        self.assertEqual(levelOf("ms_m|something"), "ms")
        self.assertIsNone(levelOf(""))
        self.assertIsNone(levelOf(None))

    def test_one_athlete_has_one_level(self):
        """A single mis-pooled season must not mint an institution -- which
        is the very error this table exists to detect."""
        src = read("racecast", "build_school_identity.py")
        self.assertIn("DISTINCT ON (school, person_id)", src)
        self.assertIn("ORDER BY school, person_id, n DESC, level", src)

    def test_the_split_uses_the_same_thresholds_as_the_state_split(self):
        src = read("racecast", "school_identity.py")
        i = src.index("def levelChips")
        self.assertIn("MIN_ATHLETES", src[i:i + 900])
        self.assertIn("MIN_SHARE", src[i:i + 900])
        self.assertIn("if len(rows) < 2", src[i:i + 1400],
                      "one institution is not a split")

    def test_it_is_a_separate_table_not_a_new_key(self):
        """The identity table's key is (school, state) and two passes
        collapse states into one row. Re-keying it means rewriting both,
        untested, under every school page."""
        src = read("racecast", "build_school_identity.py")
        self.assertIn("CREATE TABLE school_level_new", src)
        self.assertIn("school_level", src[src.index("for t in (\"person_home_state"):
                                          src.index("for t in (\"person_home_state") + 200],
                      "and it has to ride the same atomic swap")

    def test_a_missing_table_changes_nothing(self):
        src = read("racecast", "school_identity.py")
        i = src.index("def levelChips")
        self.assertIn('_tableExists(cur, "school_level")', src[i:i + 400])

    def test_the_page_scopes_and_keeps_an_unpooled_row(self):
        """Dropping a row because we failed to infer its pool would hide a
        real athlete to enforce a guess."""
        app = read("racecast", "app.py")
        self.assertIn("levelOf(r.get(\"pool\")) in (None, level)", app)
        self.assertIn('request.args.get("level")', app)

    def test_the_pool_note_is_recorded_where_it_will_be_found(self):
        """The owner asked for this on the record: level is the missing
        constraint on the pool, and anet's Level is a second witness."""
        src = read("racecast", "build_school_identity.py")
        self.assertIn("READ THIS BEFORE TOUCHING", src)
        self.assertIn("anet_team.level", src)
        self.assertIn("DETECTABLE error", src)


class CollegeTeamNotShattered(unittest.TestCase):
    """★ Amherst SCORED 318 AT A DIII MEET WITH ALL SEVEN PLACE COLUMNS
    BLANK, while its seven runners sat in the results right there (owner,
    2026-09-13).

    A home state is where an athlete races MOST, and a college races away
    most weekends -- so Amherst's seven came out MA, CT and NY, the
    collision split put them in three pseudo-teams, none reached five
    scorers, none was scoreable, and the published-score graft had nothing
    to attach. The same failure school_identity documents for BYU. And the
    cure was already in the database: school_state_alias exists to say
    which cluster each original home state resolved to."""

    class _Cur:
        """Answers the three queries splitCollisionTeams asks."""

        def __init__(self, alias=True):
            self.alias, self.last = alias, None

        def execute(self, sql, args=None):
            self.last = (sql, args)

        def fetchone(self):
            sql, args = self.last
            want = (args or ("",))[0]
            present = want != "school_state_alias" or self.alias
            return {"present": present}

        def fetchall(self):
            sql, _a = self.last
            if "FROM school_identity" in sql:          # two real Amhersts
                return [{"school": "Amherst", "state": "MA"},
                        {"school": "Amherst", "state": "NE"}]
            if "FROM person_home_state" in sql:        # a college travels
                return [{"person_id": 1, "state": "MA"},
                        {"person_id": 2, "state": "CT"},
                        {"person_id": 3, "state": "NY"},
                        {"person_id": 4, "state": "MA"},
                        {"person_id": 5, "state": "VT"}]
            if "FROM school_state_alias" in sql:
                return [{"school": "Amherst", "home_state": h, "state": "MA"}
                        for h in ("CT", "NY", "VT")]
            return []

    def rows(self):
        return [{"school": "Amherst", "person_id": i} for i in range(1, 6)]

    def split(self, alias=True):
        from meet_compile import splitCollisionTeams, _KEYSEP
        rows = self.rows()
        splitCollisionTeams(self._Cur(alias=alias), rows)
        return [r["school"] for r in rows], _KEYSEP

    def setUp(self):
        try:
            import meet_compile                        # noqa: F401
        except ImportError as exc:                     # pragma: no cover
            self.skipTest(str(exc))

    def test_the_seven_stay_one_team(self):
        got, sep = self.split()
        self.assertEqual(set(got), {f"Amherst{sep}MA"},
                         "a travel state must not mint a second Amherst")

    def test_and_the_other_real_amherst_is_still_a_different_team(self):
        """The split has to keep doing its actual job: Amherst NE is a
        different school from Amherst MA."""
        from meet_compile import splitCollisionTeams, _KEYSEP
        rows = self.rows() + [{"school": "Amherst", "person_id": 9}]
        cur = self._Cur()
        real = cur.fetchall

        def fetchall():
            got = real()
            if got and "home_state" not in got[0] and "person_id" in got[0]:
                return got + [{"person_id": 9, "state": "NE"}]
            return got
        cur.fetchall = fetchall
        splitCollisionTeams(cur, rows)
        self.assertEqual(rows[-1]["school"], f"Amherst{_KEYSEP}NE")
        self.assertEqual(rows[0]["school"], f"Amherst{_KEYSEP}MA")

    def test_a_state_that_is_not_a_cluster_falls_to_the_biggest(self):
        """Without the alias table the travel states are unknown -- they
        must still not become teams of their own."""
        got, sep = self.split(alias=False)
        self.assertEqual(set(got), {f"Amherst{sep}MA"})


class BoardOutline(unittest.TestCase):
    """★ TWO DIFFERENT BOARDS, TWO DIFFERENT RULES (owner, 2026-09-13, and
    it took two goes). /rankings draws a <table class="rk">; the HOME page
    draws a CSS grid, .board-grid, which the table rule cannot reach. Both
    needed a frame and each needed its own."""

    def css(self):
        return read("racecast", "static", "style.css")

    def test_the_rankings_table_is_framed(self):
        css = self.css()
        i = css.index(".rankings-page table.rk {")
        block = css[i:i + 400]
        self.assertIn("border: 1px solid var(--rk-line)", block)
        self.assertIn("border-collapse: separate", block,
                      "under `collapse` a table's own border does not draw")

    def test_the_home_grid_is_framed_too(self):
        css = self.css()
        i = css.index(".board-grid {")
        block = css[i:i + 400]
        self.assertIn("border: 1px solid #ddd", block)
        self.assertIn("overflow: hidden", block)

    def test_neither_frame_doubles_against_the_cells(self):
        css = self.css()
        self.assertIn(".rankings-page table.rk tbody tr:last-child td "
                      "{ border-bottom: 0; }", css)
        self.assertIn(".board-grid .head { border-top: none; }", css)


class Wiring(unittest.TestCase):
    """Every place a school is named, and the rule that a school without a
    crest is unchanged."""

    def test_the_route_exists_takes_a_path_and_a_size(self):
        app = read("racecast", "app.py")
        self.assertIn('@app.route("/img/school/<path:school_name>.png")', app)
        self.assertIn("school_logo.logoPath(cur, school_name, state)", app)
        self.assertIn('school_logo.thumbPath(path, request.args.get("px"', app)

    def test_the_cache_is_loaded_at_start_up_beside_the_labels(self):
        """Every mention has to know whether there IS a crest before it
        writes an <img>; one query at start-up answers all of them."""
        app = read("racecast", "app.py")
        i = app.index("school_logo.loadCrests(getConn)")
        self.assertIn("school_identity.loadLabels(getConn)", app[max(0, i - 800):i])
        self.assertIn('app.jinja_env.globals["crest"] = school_logo.crestImg', app)

    def test_no_template_writes_the_image_url_by_hand(self):
        """A hand-written /img/school/ tag is one that cannot know whether
        the file exists, so it 404s for most schools. Everything goes
        through crest(), which returns nothing when there is nothing --
        except the two search rows, which read a flag the server stamped."""
        import glob
        for f in glob.glob(os.path.join(_ROOT, "racecast", "templates", "*.html")):
            with io.open(f, encoding="utf-8") as fh:
                html = fh.read()
            for line in html.splitlines():
                if "/img/school/" in line:
                    self.fail(f"{os.path.basename(f)}: {line.strip()[:80]}")
            if "school-mark" in html and "crest(" not in html:
                self.assertIn("r.crest", html, os.path.basename(f))

    def test_the_school_pages_wear_their_own_crest(self):
        for name in ("school.html", "school_prs.html"):
            html = read("racecast", "templates", name)
            self.assertIn('cls="school-crest"', html, name)
        css = read("racecast", "static", "style.css")
        self.assertIn(".school-crest", css)
        self.assertIn(".school-mark", css)

    def test_every_table_that_names_schools_marks_them(self):
        """The mention sites, named one by one: a template dropped off this
        list is a page where schools quietly stopped having crests."""
        for name in ("race.html", "compiled.html", "compiled_tf.html",
                     "course.html", "meet_tf.html", "athlete.html",
                     "landing.html", "home.html", "compare.html",
                     "recruit.html", "schools.html", "_tf_points.html",
                     "school.html", "school_prs.html"):
            self.assertIn("crest(", read("racecast", "templates", name), name)

    def test_the_boards_the_browser_draws_are_stamped_server_side(self):
        app = read("racecast", "app.py")
        self.assertGreaterEqual(app.count("stampCrests(rows"), 3,
                                "the athlete board and both team boards")
        self.assertIn('stampCrests(rows, state_key="school_state")', app,
                      "a result row's `state` is the VENUE's, not the school's")
        js = read("racecast", "static", "rankings.js")
        self.assertIn("function crestMark(url)", js)
        self.assertIn("r.crest", js)

    def test_both_cards_carry_one(self):
        cards = read("racecast", "cards.py")
        self.assertIn('badge=d.get("crest")', cards)          # the school card
        self.assertIn('_crest(img, d["crest"], _r + 24', cards)   # the athlete card
        self.assertIn("crestPath(cur, school", cards)

    def test_the_athlete_card_puts_it_beside_the_name(self):
        """Owner, 2026-09-12: next to the name, the way athletic.net does
        it -- not overlapping the photo slot's corner."""
        cards = read("racecast", "cards.py")
        i = cards.index('_crest(img, d["crest"], _r + 24')
        before = cards[max(0, i - 700):i]
        self.assertIn("textbbox", before, "centred on the name's own glyph box")
        self.assertIn("NAME_CREST + 24", before,
                      "the room comes out of the name BEFORE it is fitted")
        self.assertNotIn("px + PHOTO - 74", cards, "the old corner badge is gone")

    def test_the_pipeline_does_not_run_the_scraper(self):
        """The plan's rule: a job that talks to twenty thousand strangers
        never runs beside a pipeline step, and never on the pipeline's
        clock."""
        for name in ("run_pipeline.sh",):
            self.assertNotIn("scrape_school_logos",
                             read("deploy", name))


# ===================================================================== #
#  THE IMAGE ITSELF (only where Pillow is installed -- the box, not here)#
# ===================================================================== #

@unittest.skipIf(Image is None, "Pillow is not installed in this sandbox")
class Normalise(unittest.TestCase):
    def _png(self, w, h, mode="RGBA", colour=(10, 20, 30, 255)):
        buf = io.BytesIO()
        Image.new(mode, (w, h), colour[:4 if mode == "RGBA" else 3]).save(buf, "PNG")
        return buf.getvalue()

    def test_a_touch_icon_becomes_a_square_of_the_served_size(self):
        png, sha, size = S.normalise(self._png(180, 180))
        self.assertEqual(size, (180, 180))
        self.assertEqual(len(sha), 64)
        self.assertEqual(Image.open(io.BytesIO(png)).size, (SL.LOGO_PX, SL.LOGO_PX))

    def test_a_wide_badge_is_letterboxed_not_stretched(self):
        png, _sha, _s = S.normalise(self._png(240, 160))
        im = Image.open(io.BytesIO(png))
        self.assertEqual(im.size, (SL.LOGO_PX, SL.LOGO_PX))
        self.assertEqual(im.mode, "RGBA")
        self.assertEqual(im.getpixel((2, 2))[3], 0, "the ground stays transparent")

    def test_a_banner_and_a_favicon_are_refused_with_their_size(self):
        self.assertEqual(S.normalise(self._png(1200, 630))[:2], (None, None))
        self.assertEqual(S.normalise(self._png(32, 32))[2], "32x32")

    def test_a_transparent_margin_is_trimmed_before_the_rule_is_applied(self):
        """A 512 canvas holding a 40 px mark is a 40 px mark."""
        im = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
        im.paste(Image.new("RGBA", (40, 40), (0, 0, 0, 255)), (10, 10))
        buf = io.BytesIO()
        im.save(buf, "PNG")
        self.assertEqual(S.normalise(buf.getvalue())[2], "40x40")

    def test_the_same_bytes_hash_the_same_and_different_ones_do_not(self):
        a = S.normalise(self._png(200, 200))[1]
        b = S.normalise(self._png(200, 200))[1]
        c = S.normalise(self._png(200, 200, colour=(200, 0, 0, 255)))[1]
        self.assertEqual(a, b, "the district sweep counts identical bytes")
        self.assertNotEqual(a, c)

    def test_a_crest_the_disk_lost_leaves_the_card_exactly_as_it_was(self):
        """The rule the whole job rests on: no crest changes nothing. A row
        that points at a file the disk no longer has must draw the card
        byte-for-byte as a school with no row at all."""
        import cards
        d = {"name": "Jane Runner", "school": "De La Salle (CA)", "grade": "Jr",
             "rating": 148.2, "season": "2026 XC season", "best": 151.0,
             "best_sport": "XC", "races": 43, "seasons": 4, "units": [],
             "ranks": ["Nation #212"]}
        none = cards.renderAthleteCard(dict(d, crest=None))
        gone = cards.renderAthleteCard(dict(d, crest="/no/such/crest.png"))
        self.assertEqual(none, gone)
        s = {"title": "De La Salle (CA)", "sub": "2026 cross country",
             "team": 152.4, "athletes": 61, "ranks": [("Nation", 3)],
             "top": [{"name": "R", "grade": "Sr", "rating": 160.0}]}
        self.assertEqual(cards.renderSchoolCard(dict(s, crest=None)),
                         cards.renderSchoolCard(dict(s, crest="/no/such.png")))

    def test_rubbish_is_a_reason_not_an_exception(self):
        png, sha, why = S.normalise(b"<html>not an image</html>")
        self.assertEqual((png, sha), (None, None))
        self.assertIn("unreadable", why)


if __name__ == "__main__":
    unittest.main()
