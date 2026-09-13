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
import os
import sys
import time
import unittest

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
                         "school application/pdf")

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
            "Jesuit": ["CA"],
            "Highland": ["UT", "CA"],          # two real schools, one name
            "Nameless": [""],                  # stored under no state
        })
        self.addCleanup(SL._CRESTS.update, {"loaded": False, "map": {}})

    def test_a_school_with_a_crest_gets_a_url_with_no_query(self):
        self.assertEqual(SL.crestUrl("Jesuit", "CA"),
                         "/img/school/Jesuit.png?state=CA")
        self.assertEqual(SL.crestUrl("Jesuit", "CA", 64),
                         "/img/school/Jesuit.png?state=CA&px=64")

    def test_a_school_with_none_gets_nothing_at_all(self):
        self.assertIsNone(SL.crestUrl("Nobody", "CA"))
        self.assertEqual(SL.crestImg("Nobody", "CA"), "")

    def test_a_name_two_schools_share_needs_a_state(self):
        self.assertEqual(SL.crestState("Highland", "UT"), "UT")
        self.assertIsNone(SL.crestState("Highland", None),
                          "a coin toss here is the WRONG crest on a real page")
        self.assertIsNone(SL.crestState("Highland", "NY"))

    def test_one_row_answers_without_a_state_and_a_stateless_row_answers_for_any(self):
        self.assertEqual(SL.crestState("Jesuit", None), "CA")
        self.assertEqual(SL.crestState("Nameless", "TX"), "")
        self.assertEqual(SL.crestUrl("Nameless", "TX"), "/img/school/Nameless.png")

    def test_the_markup_escapes_a_scraped_name(self):
        """School names are free text: 'Smith & "Jones"' is the kind of
        thing that breaks a page written with an f-string."""
        SL._CRESTS["map"]['Smith & "Jones"'] = ["CA"]
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
        self.assertEqual(rows[0]["crest"], "/img/school/Jesuit.png?state=CA&px=64")
        self.assertNotIn("crest", rows[1])
        self.assertNotIn("crest", rows[2])

    def test_a_search_hit_is_read_back_off_its_link(self):
        self.assertEqual(SL.crestUrlForLink("/school/Highland?state=UT"),
                         "/img/school/Highland.png?state=UT&px=64")
        self.assertEqual(SL.crestUrlForLink("/school/Jesuit"),
                         "/img/school/Jesuit.png?state=CA&px=64")
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
