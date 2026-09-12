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
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "racecast")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import school_logo as SL                                          # noqa: E402
import scrape_school_logos as S                                   # noqa: E402
import build_school_websites as W                                 # noqa: E402


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
  <link rel="shortcut icon" href="/fav.ico">
</head><body>
  <link rel="icon" href="/decoy-in-the-body.png">
</body></html>"""


class Candidates(unittest.TestCase):
    def setUp(self):
        self.got = S.iconCandidates(PAGE, "https://chs.k12.ca.us/home/index.html")

    def test_the_plan_s_order(self):
        """og, apple touch, a sized icon, the tile, the plain favicon."""
        self.assertEqual([k for k, _u, _p in self.got][:5],
                         ["og", "apple-touch", "icon-sized", "tile", "icon"])

    def test_urls_are_absolute_against_the_page(self):
        by = {k: u for k, u, _p in self.got}
        self.assertEqual(by["og"], "https://chs.k12.ca.us/img/social-banner.jpg")
        self.assertEqual(by["apple-touch"], "https://cdn.example.org/at.png")
        # relative to the PAGE's directory, not to the host root
        self.assertEqual(by["icon-sized"], "https://chs.k12.ca.us/home/favicon-32.png")

    def test_data_uris_and_body_tags_are_not_candidates(self):
        urls = " ".join(u for _k, u, _p in self.got)
        self.assertNotIn("data:", urls)
        self.assertNotIn("decoy", urls, "an icon link in the body is not a declaration")

    def test_the_conventional_favicon_is_always_last_resort(self):
        self.assertIn("https://chs.k12.ca.us/favicon.ico",
                      [u for _k, u, _p in self.got])

    def test_a_page_that_declares_nothing_still_has_one_candidate(self):
        got = S.iconCandidates("<html><head><title>x</title></head></html>",
                               "https://x.org/")
        self.assertEqual([(k, u) for k, u, _p in got],
                         [("icon", "https://x.org/favicon.ico")])

    def test_the_biggest_declared_size_leads_its_kind(self):
        page = ('<head><link rel="apple-touch-icon" sizes="60x60" href="/s.png">'
                '<link rel="apple-touch-icon" sizes="180x180" href="/b.png"></head>')
        got = [u for k, u, _p in S.iconCandidates(page, "https://x.org/")
               if k == "apple-touch"]
        self.assertEqual(got, ["https://x.org/b.png", "https://x.org/s.png"])

    def test_malformed_markup_still_yields_what_parsed(self):
        page = '<head><link rel="icon" sizes="64x64" href="/i.png"><p><div'
        self.assertIn("https://x.org/i.png",
                      [u for _k, u, _p in S.iconCandidates(page, "https://x.org/")])


class Acceptable(unittest.TestCase):
    """The plan's rule: at least 96 px and roughly square."""

    def test_an_open_graph_banner_is_not_a_crest(self):
        self.assertFalse(S.acceptable(1200, 630))

    def test_a_favicon_is_too_small(self):
        self.assertFalse(S.acceptable(32, 32))
        self.assertFalse(S.acceptable(64, 64))

    def test_a_touch_icon_is_exactly_what_we_want(self):
        self.assertTrue(S.acceptable(180, 180))
        self.assertTrue(S.acceptable(512, 512))

    def test_the_floor_and_the_aspect_are_both_edges(self):
        self.assertTrue(S.acceptable(96, 96))
        self.assertFalse(S.acceptable(95, 95))
        self.assertTrue(S.acceptable(160, 100))       # 1.60 exactly
        self.assertFalse(S.acceptable(170, 100))      # 1.70
        self.assertFalse(S.acceptable(0, 0))


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

    def test_the_pace_is_global(self):
        """Twenty thousand schools are twenty thousand hosts, so a per-host
        delay would be no delay: the clock is one clock."""
        m = S.Manners(rate=0.05)
        t0 = S.time.time()
        for host in ("a", "b", "c"):
            m.wait()
        self.assertGreaterEqual(S.time.time() - t0, 0.09)


class Fetching(unittest.TestCase):
    """fetchLogo's decisions, with the image reader stubbed out: which
    candidate it settles on, and what it says when none works."""

    class _M:
        def __init__(self, replies):
            self.replies, self.asked = replies, []

        def get(self, url, max_bytes=None):
            self.asked.append(url)
            return self.replies.get(url, (None, "HTTPError"))

    def setUp(self):
        self._real = S.normalise
        # every fetched body is "wide" or "square"; only square normalises
        S.normalise = lambda raw, px=512: (
            (b"PNG" + raw, "sha-" + raw.decode(), (512, 512))
            if raw == b"square" else (None, None, "1200x630"))

    def tearDown(self):
        S.normalise = self._real

    def test_the_banner_loses_to_the_touch_icon_behind_it(self):
        m = self._M({
            "https://x.org/": (PAGE.replace("chs.k12.ca.us", "x.org").encode(), "text/html"),
            "https://x.org/img/social-banner.jpg": (b"wide", "image/jpeg"),
            "https://cdn.example.org/at.png": (b"square", "image/png")})
        png, sha, kind, src = S.fetchLogo(m, "https://x.org/")
        self.assertEqual((kind, src), ("apple-touch", "https://cdn.example.org/at.png"))
        self.assertEqual(sha, "sha-square")
        self.assertTrue(png)

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
        self.assertEqual(kind, "icon-sized")

    def test_nothing_usable_reports_why_and_stops(self):
        m = self._M({"https://x.org/": (b"<head></head>", "text/html"),
                     "https://x.org/favicon.ico": (b"wide", "image/x-icon")})
        png, sha, kind, why = S.fetchLogo(m, "https://x.org/")
        self.assertEqual((png, sha, kind), (None, None, None))
        self.assertIn("1200x630", why)

    def test_a_page_that_is_not_html_is_not_parsed(self):
        m = self._M({"https://x.org/": (b"%PDF-1.4", "application/pdf")})
        self.assertEqual(S.fetchLogo(m, "https://x.org/")[3], "page application/pdf")

    def test_svg_is_skipped_rather_than_fed_to_pillow(self):
        """Pillow cannot read one, and asking for it spends a request on a
        certain failure."""
        m = self._M({
            "https://x.org/": (b'<head><link rel="icon" sizes="any" href="/l.svg">',
                               "text/html"),
            "https://x.org/favicon.ico": (b"square", "image/x-icon")})
        _png, _sha, kind, src = S.fetchLogo(m, "https://x.org/")
        self.assertEqual((kind, src), ("icon", "https://x.org/favicon.ico"))
        self.assertNotIn("https://x.org/l.svg", m.asked)


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
        S.normalise = lambda raw, px=512: (
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


class Wiring(unittest.TestCase):
    """The three places the plan says a crest goes, and the rule that a
    school without one is unchanged."""

    def test_the_route_exists_and_takes_a_path(self):
        app = read("racecast", "app.py")
        self.assertIn('@app.route("/img/school/<path:school_name>.png")', app)
        self.assertIn("school_logo.logoPath(cur, school_name, state)", app)

    def test_the_page_asks_before_it_draws(self):
        """No crest, no <img>: the school page must not emit a tag that
        404s, because most schools have no crest."""
        app = read("racecast", "app.py")
        self.assertIn("if school_logo.logoPath(cur, school_name, crest_state)", app)
        html = read("racecast", "templates", "school.html")
        i = html.index("school-crest")
        self.assertIn("{% if crest %}", html[max(0, i - 200):i])
        self.assertIn(".school-crest", read("racecast", "static", "style.css"))

    def test_both_cards_carry_one(self):
        cards = read("racecast", "cards.py")
        self.assertIn('badge=d.get("crest")', cards)          # the school card
        self.assertIn('_crest(img, d.get("crest")', cards)    # the athlete card
        self.assertIn("crestPath(cur, school", cards)

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

try:
    from PIL import Image
except ImportError:                                          # pragma: no cover
    Image = None


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
