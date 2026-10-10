"""The SEO pass (2026-10-10): canonical rules, titles, JSON-LD shapes, the
sitemap's collection rules and IndexNow's "changed" rule. No database:
the sitemap runs against a stub cursor, the head against Flask's own
template engine.

    python -m pytest -q tests/test_seo.py
"""
import gzip
import json
import os
import re
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (ROOT, os.path.join(ROOT, "racecast"), os.path.join(ROOT, "engine"),
           os.path.join(ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")

import seo                                                      # noqa: E402

ORIGIN = "https://racecast.co"


def _read(*p):
    with open(os.path.join(ROOT, *p), encoding="utf-8") as fh:
        return fh.read()


# --------------------------------------------------------------------- #
#  canonical rules
# --------------------------------------------------------------------- #
class Canonical(unittest.TestCase):
    def test_highlight_and_tracking_args_are_stripped(self):
        self.assertEqual(seo.canonicalPath("/athlete/12", {"r": "99", "utm_source": "x"}),
                         "/athlete/12")
        self.assertEqual(seo.canonicalPath("/race/xc/5/7", {"school": "Jesuit", "r": "-3"}),
                         "/race/xc/5/7")

    def test_alt_is_kept_only_when_it_selects_another_page(self):
        # the route resolved it: index 1 is a different meet
        self.assertEqual(seo.canonicalPath("/meet/xc/5", {"alt": "1"}, alt=1), "/meet/xc/5?alt=1")
        # the route resolved it: the bare URL serves this same race
        self.assertEqual(seo.canonicalPath("/race/xc/5/7", {"alt": "1"}, alt=None), "/race/xc/5/7")
        # no route verdict: the argument as given, 0 and junk are the bare page
        self.assertEqual(seo.canonicalPath("/race/tf/5/1/2", {"alt": "2"}), "/race/tf/5/1/2?alt=2")
        self.assertEqual(seo.canonicalPath("/meet/tf/5", {"alt": "0"}), "/meet/tf/5")
        self.assertEqual(seo.canonicalPath("/meet/tf/5", {"alt": "x"}), "/meet/tf/5")
        # alt means nothing off meet and race pages
        self.assertEqual(seo.canonicalPath("/athlete/5", {"alt": "1"}), "/athlete/5")

    def test_selecting_args_are_kept(self):
        self.assertEqual(seo.canonicalPath("/meets", {"course": "Lane CC", "page": "2"}),
                         "/meets?course=Lane+CC")

    def test_no_trailing_slash(self):
        self.assertEqual(seo.canonicalPath("/about/", {}), "/about")
        self.assertEqual(seo.canonicalPath("/", {}), "/")


# --------------------------------------------------------------------- #
#  JSON-LD shapes
# --------------------------------------------------------------------- #
class Shapes(unittest.TestCase):
    def test_ld_json_drops_nulls_and_is_script_safe(self):
        out = str(seo.ldJson({"@type": "Person", "name": "St. Mary's </script> \"Q\"",
                              "affiliation": None, "x": {"y": None}, "z": []}))
        self.assertNotIn("</script>", out)
        self.assertNotIn("'", out)
        back = json.loads(out)
        self.assertEqual(back, {"@type": "Person", "name": "St. Mary's </script> \"Q\""})

    def test_breadcrumbs(self):
        ld = seo.crumbsLd([("Schools", "/schools"), ("Athletes", None),
                           ("Jesuit (OR)", "/school/Jesuit?state=OR"), ("Owen", None)], ORIGIN)
        self.assertEqual(ld["@type"], "BreadcrumbList")
        items = ld["itemListElement"]
        self.assertEqual([i["position"] for i in items], [1, 2, 3])     # contiguous
        self.assertEqual([i["name"] for i in items], ["Schools", "Jesuit (OR)", "Owen"])
        self.assertTrue(all(i["item"].startswith(ORIGIN + "/") for i in items[:-1]))
        self.assertNotIn("item", items[-1])                              # the page itself
        self.assertIsNone(seo.crumbsLd([], ORIGIN))
        self.assertEqual(seo.defaultCrumbs("/", "Home"), [])
        self.assertEqual(seo.defaultCrumbs("/about", "About"), [("Racecast", "/"), ("About", None)])

    def test_home_is_organization_and_website_with_search(self):
        org, site = seo.homeLd(ORIGIN, "desc")
        self.assertEqual(org["@type"], "Organization")
        self.assertTrue(org["logo"].startswith(ORIGIN))
        self.assertEqual(site["@type"], "WebSite")
        act = site["potentialAction"]
        self.assertEqual(act["@type"], "SearchAction")
        self.assertIn("{search_term_string}", act["target"]["urlTemplate"])
        self.assertEqual(act["query-input"], "required name=search_term_string")

    def test_sports_event(self):
        ld = json.loads(str(seo.ldJson(seo.eventLd(
            "Nike XC 2025 Results", ORIGIN + "/meet/xc/5?alt=1", "2025-10-04 09:00",
            "Lane CC", "OR", 44.05, -123.1))))
        self.assertEqual(ld["@type"], "SportsEvent")
        self.assertEqual(ld["startDate"], "2025-10-04")
        self.assertEqual(ld["url"], ORIGIN + "/meet/xc/5?alt=1")
        loc = ld["location"]
        self.assertEqual(loc["@type"], "Place")
        self.assertEqual(loc["name"], "Lane CC")
        self.assertEqual(loc["address"]["addressRegion"], "OR")
        self.assertEqual(loc["geo"]["@type"], "GeoCoordinates")
        # nothing known about the place: no half-built location
        bare = json.loads(str(seo.ldJson(seo.eventLd("X", ORIGIN + "/meet/xc/1"))))
        self.assertNotIn("location", bare)
        self.assertNotIn("startDate", bare)


# --------------------------------------------------------------------- #
#  athletes: titles, descriptions, Person
# --------------------------------------------------------------------- #
ALLTIME = {"XC": {"events": {"5000m": {"result": "15:02.3"}}},
           "TF": {"events": {"800m": {"result": "1:58.0"}, "1600m": {"result": "4:21.9"}}}}
RANKS = [{"label": "Nation", "rank": 41}, {"label": "OR", "rank": 3},
         {"label": "Team", "rank": 1}]


class Athletes(unittest.TestCase):
    def test_class_of(self):
        self.assertEqual(seo.classOf("11", 2025, "hs_m"), 2027)
        self.assertEqual(seo.classOf("Jr", 2025, "hs_f"), 2027)
        self.assertEqual(seo.classOf("2028", None, "college_m"), 2028)
        self.assertIsNone(seo.classOf("JR-3", 2025, "college_m"))
        self.assertIsNone(seo.classOf("11", None, "hs_m"))

    def test_title_and_description_use_real_facts(self):
        a = {"name": "Owen Castellano", "person_id": 7, "class_of": 2027,
             "rating": 120.0, "rating_hs": 124.6, "n_races": 14}
        out = seo.athleteSeo(a, ALLTIME, RANKS, "Jesuit (OR)", "/school/Jesuit?state=OR", ORIGIN)
        self.assertEqual(out["title"], "Owen Castellano — Jesuit (OR) cross country & track")
        self.assertEqual(seo.fullTitle(out["title"]),
                         "Owen Castellano — Jesuit (OR) cross country & track | Racecast")
        d = out["description"]
        self.assertTrue(d.startswith("Owen Castellano, Jesuit (OR)."))
        for fact in ("Class of 2027.", "5K XC PR 15:02.3", "1600m PR 4:21.9",
                     "Season rating 124.6, #41 nationally, #3 in OR."):
            self.assertIn(fact, d)
        self.assertLessEqual(len(d), seo.DESC_MAX)
        self.assertFalse(out["noindex"])

    def test_missing_facts_drop_out(self):
        out = seo.athleteSeo({"name": "A B", "person_id": 1, "n_races": 1}, None, None,
                             None, None, ORIGIN)
        self.assertEqual(out["title"], "A B cross country & track")
        self.assertNotIn("Class of", out["description"])
        self.assertNotIn("None", out["description"])

    def test_person_shape(self):
        out = seo.athleteSeo({"name": "Owen", "person_id": 7, "n_races": 3}, None, None,
                             "Jesuit (OR)", "/school/Jesuit?state=OR", ORIGIN, "/img/p/7.jpg")
        ld = json.loads(str(seo.ldJson(out["ld"])))
        self.assertEqual(ld["@type"], "Person")
        self.assertEqual(ld["url"], ORIGIN + "/athlete/7")
        self.assertEqual(ld["image"], ORIGIN + "/img/p/7.jpg")
        self.assertEqual(ld["memberOf"]["@type"], "SportsTeam")
        self.assertEqual(ld["memberOf"]["url"], ORIGIN + "/school/Jesuit?state=OR")
        self.assertEqual(ld["affiliation"]["name"], "Jesuit (OR)")
        self.assertEqual(out["crumbs"][-1], ("Owen", None))

    def test_thin_pages_are_noindex(self):
        self.assertTrue(seo.athleteSeo({"name": "A", "person_id": 1, "n_races": 0})["noindex"])
        self.assertTrue(seo.athleteSeo({"name": "Unnamed athlete", "unnamed": True,
                                        "person_id": 1, "n_races": 4})["noindex"])


# --------------------------------------------------------------------- #
#  the rendered head
# --------------------------------------------------------------------- #
try:
    import flask                                               # noqa: F401
    _HAVE_FLASK = True
except ImportError:
    _HAVE_FLASK = False


def _ld_blocks(html):
    return [json.loads(m) for m in re.findall(
        r'<script type="application/ld\+json">(.*?)</script>', html, re.S)]


@unittest.skipUnless(_HAVE_FLASK, "flask not installed")
class RenderedHead(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import app as A
        cls.A = A

    def render(self, url, body, canonical_alt=seo._UNSET):
        from flask import g, render_template_string
        with self.A.app.test_request_context(url):
            if canonical_alt is not seo._UNSET:
                g.canonical_alt = canonical_alt
            return render_template_string(body + '{% include "_meta.html" %}')

    def test_alt_meet_canonical_and_r_noindex(self):
        html = self.render("/meet/xc/5?alt=1&r=3&school=Jesuit",
                           '{% set meta_title = "Nike 2025 Results" %}', canonical_alt=1)
        self.assertIn('<link rel="canonical" href="https://racecast.co/meet/xc/5?alt=1">', html)
        self.assertIn('content="noindex,follow"', html)
        self.assertIn("<title>Nike 2025 Results | Racecast</title>", html)

    def test_every_page_has_breadcrumbs_and_valid_json(self):
        html = self.render("/about", '{% set meta_title = "About \\"us\\" & St. Mary\'s" %}')
        blocks = _ld_blocks(html)
        crumbs = [b for b in blocks if b.get("@type") == "BreadcrumbList"]
        self.assertEqual(len(crumbs), 1)
        self.assertEqual(crumbs[0]["itemListElement"][-1]["name"], 'About "us" & St. Mary\'s')
        self.assertEqual(html.count("<title>"), 1)

    def test_home_has_organization_and_website(self):
        html = self.render("/", '{% set meta_title = "Rankings" %}')
        types = [b["@type"] for b in _ld_blocks(html)]
        self.assertIn("Organization", types)
        self.assertIn("WebSite", types)
        self.assertNotIn("BreadcrumbList", types)

    def test_athlete_head_through_the_global(self):
        from flask import render_template_string
        with self.A.app.test_request_context("/athlete/7?r=1"):
            html = render_template_string(
                '{% set _seo = seo_athlete(a, alltime, ranks, "Jesuit (OR)", "/school/Jesuit?state=OR") %}'
                '{% set meta_title = _seo.title %}{% set meta_description = _seo.description %}'
                '{% set meta_ld = _seo.ld %}{% set meta_crumbs = _seo.crumbs %}'
                '{% include "_meta.html" %}',
                a={"name": "Owen Castellano", "person_id": 7, "n_races": 9,
                   "class_of": 2027, "rating_hs": 124.6},
                alltime=ALLTIME, ranks=RANKS)
        self.assertIn("<title>Owen Castellano — Jesuit (OR) cross country &amp; track | Racecast</title>", html)
        self.assertIn('<link rel="canonical" href="https://racecast.co/athlete/7">', html)
        self.assertIn("Class of 2027", html)
        types = [b["@type"] for b in _ld_blocks(html)]
        self.assertEqual(sorted(types), ["BreadcrumbList", "Person"])

    def test_page_heads_use_the_shared_rules(self):
        for name in ("meet.html", "meet_tf.html", "race.html", "race_tf.html"):
            src = _read("racecast", "templates", name)
            self.assertIn("seo_event_ld(", src, name)
            self.assertIn("meta_crumbs", src, name)
            self.assertNotIn('"url": site_origin ~ request.path', src, name)
        self.assertIn("seo_athlete(", _read("racecast", "templates", "athlete.html"))
        self.assertIn('{% include "_meta.html" %}', _read("racecast", "templates", "conversions.html"))
        self.assertIn('name="robots" content="noindex', _read("racecast", "templates", "search.html"))

    def test_trailing_slash_is_a_301_and_unknown_paths_stay_404(self):
        c = self.A.app.test_client()
        r = c.get("/about/?x=1")
        self.assertEqual(r.status_code, 301)
        self.assertTrue(r.headers["Location"].endswith("/about?x=1"))
        self.assertNotEqual(c.get("/no-such-page-zz/").status_code, 301)

    def test_robots_keeps_private_paths_out_and_names_the_index(self):
        body = self.A.app.test_client().get("/robots.txt").get_data(as_text=True)
        for p in ("/api/", "/account", "/debug/", "/search"):
            self.assertIn(f"Disallow: {p}", body)
        self.assertIn("Sitemap: https://racecast.co/sitemap.xml", body)


# --------------------------------------------------------------------- #
#  the sitemap's collection, on a stub database
# --------------------------------------------------------------------- #
class _Cur:
    def __init__(self, tables):
        self.tables, self.sql, self.rows, self.log = tables, "", [], []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.log.append(sql)
        self.sql = sql
        s = " ".join(sql.split())
        if s.startswith("SELECT to_regclass"):
            self.rows = [(params[0] if params[0] in self.tables else None,)]
        elif "information_schema.columns" in s:
            self.rows = [("n_twin",)]
        elif "FROM school_identity" in s:
            self.rows = [("Jesuit", "OR"), ("Unattached", "OR")]
        elif "FROM course_difficulties" in s:
            self.rows = [("XC:Lane CC",)]
        elif "FROM meet_agg_xc" in s:
            self.rows = [(5, "anet", 100, 100, 0), (5, "tfrrs", 50, 50, 0)]
        elif "FROM meet_agg_tf" in s:
            self.rows = [(9, "anet", 10, 10, 0)]
        elif "FROM results_tf" in s:
            self.rows = [(9, 1, 2, "2026-04-02"), (9, 1, 3, "2019-04-02")]
        elif "FROM results" in s:
            self.rows = [(5, 7, "2025-10-04"), (5, 8, "2025-10-05"), (4, 1, "2012-09-01")]
        elif "FROM athlete_season" in s:
            self.rows = [(11, "2025-11-01"), (12, "2024-05-01")]
        else:
            self.rows = []

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)


class _Conn:
    def __init__(self, tables):
        self.cur = _Cur(tables)

    def cursor(self, *a, **k):
        return self.cur


ALL_TABLES = {"school_identity", "course_difficulties", "meet_agg_xc", "meet_agg_tf",
              "results", "results_tf", "athlete_season", "person_redirect"}


class SitemapCollect(unittest.TestCase):
    def setUp(self):
        import build_sitemap as BS
        self.BS = BS

    def test_kinds_lastmods_and_canonical_ids(self):
        conn = _Conn(ALL_TABLES)
        by = self.BS.collect(conn, race_years=0)
        for kind in ("pages", "schools", "courses", "meets", "races", "athletes"):
            self.assertIn(kind, by)
        self.assertIn(("/school/Jesuit?state=OR", None), by["schools"])
        self.assertFalse(any("Unattached" in p for p, _ in by["schools"]))
        meets = dict(by["meets"])
        # the newest race is the meet's lastmod; the second meet is ?alt=1
        self.assertEqual(meets["/meet/xc/5"], "2025-10-05")
        self.assertIn("/meet/xc/5?alt=1", meets)
        self.assertEqual(meets["/meet/tf/9"], "2026-04-02")
        self.assertEqual(len(by["races"]), 5)
        # the state boards are in the fixed pages
        self.assertTrue(any(p.startswith("/rankings/") for p, _ in by["pages"]))
        self.assertTrue(any(p.startswith("/schools/") for p, _ in by["pages"]))
        # athletes: the redirect anti-join is in the query
        q = [s for s in conn.cur.log if "athlete_season" in s and "GROUP" in s][0]
        self.assertIn("person_redirect", q)

    def test_no_redirect_table_no_anti_join(self):
        conn = _Conn(ALL_TABLES - {"person_redirect"})
        self.BS.collect(conn, race_years=0)
        q = [s for s in conn.cur.log if "athlete_season" in s and "GROUP" in s][0]
        self.assertNotIn("person_redirect", q)

    def test_race_window(self):
        import datetime as dt
        self.assertIsNone(self.BS.raceSince(0))
        self.assertEqual(self.BS.raceSince(2, dt.date(2026, 10, 10)), "2025-08-01")
        self.assertEqual(self.BS.raceSince(1, dt.date(2026, 3, 1)), "2025-08-01")
        by = self.BS.collect(_Conn(ALL_TABLES), race_years=50)
        self.assertEqual(len(by["races"]), 5)
        self.assertFalse(self.BS.raceWanted(None, "2025-08-01"))
        self.assertTrue(self.BS.raceWanted(None, None))

    def test_written_files_hold_the_urls(self):
        by = self.BS.collect(_Conn(ALL_TABLES), race_years=0)
        with tempfile.TemporaryDirectory() as d:
            files = self.BS.writeSitemaps(by, d, ORIGIN)
            idx = open(os.path.join(d, "sitemap.xml"), encoding="utf-8").read()
            for f in files:
                self.assertIn(f"{ORIGIN}/static/sitemaps/{f}", idx)
            with gzip.open(os.path.join(d, "sitemap-meets.xml.gz"), "rt") as fh:
                body = fh.read()
            self.assertIn(f"<loc>{ORIGIN}/meet/xc/5?alt=1</loc>", body)
            self.assertIn("<lastmod>2025-10-05</lastmod>", body)


# --------------------------------------------------------------------- #
#  IndexNow: changed pages only
# --------------------------------------------------------------------- #
class IndexNowPick(unittest.TestCase):
    def setUp(self):
        import indexnow_submit as IN
        self.IN = IN

    def test_only_changed_and_new(self):
        entries = [("sitemap-pages.xml.gz", "https://r/rankings", ""),
                   ("sitemap-athletes-1.xml.gz", "https://r/athlete/1", "2026-10-09"),
                   ("sitemap-athletes-1.xml.gz", "https://r/athlete/2", "2025-01-01"),
                   ("sitemap-schools.xml.gz", "https://r/school/Old", ""),
                   ("sitemap-schools.xml.gz", "https://r/school/New", "")]
        out, undated = self.IN.pick(entries, "2026-10-07", {"https://r/school/Old"})
        self.assertEqual(out, ["https://r/rankings", "https://r/athlete/1", "https://r/school/New"])
        self.assertEqual(undated, {"https://r/school/Old", "https://r/school/New"})
        # first run, no state: seed, submit no undated school
        out, _ = self.IN.pick(entries, "2026-10-07", None)
        self.assertNotIn("https://r/school/New", out)

    def test_state_round_trip_and_unescaped_locs(self):
        with tempfile.TemporaryDirectory() as d:
            sm = os.path.join(d, "sitemaps")
            os.makedirs(sm)
            with gzip.open(os.path.join(sm, "sitemap-schools.xml.gz"), "wt") as fh:
                fh.write("<url><loc>https://r/school/A?state=OR&amp;level=hs</loc></url>\n")
            old = self.IN.SITEMAPS
            self.IN.SITEMAPS = sm
            try:
                state = os.path.join(d, "state", "seen.txt.gz")
                self.assertEqual(self.IN.collect(3, state=state), [])     # seeded
                self.assertEqual(self.IN._readSeen(state),
                                 {"https://r/school/A?state=OR&level=hs"})
                self.assertEqual(self.IN.collect(3, state=state), [])     # unchanged
            finally:
                self.IN.SITEMAPS = old


# --------------------------------------------------------------------- #
#  pipeline wiring
# --------------------------------------------------------------------- #
class Wiring(unittest.TestCase):
    def test_nightly_builds_the_sitemap_after_redirects_off_the_chain(self):
        sh = _read("deploy", "nightly_update.sh")
        self.assertRegex(sh, r"(?m)^step 13d_sitemap ")
        self.assertRegex(sh, r"(?m)^step 13e_indexnow ")
        self.assertLess(sh.index("step 13c0_person_redirects"), sh.index("step 13d_sitemap"))
        self.assertLess(sh.index("step 13d_sitemap"), sh.index("step 13e_indexnow"))

    def test_full_pipeline_too(self):
        sh = _read("deploy", "run_pipeline.sh")
        self.assertRegex(sh, r"(?m)^step 13d_sitemap ")
        self.assertLess(sh.index("step 13c0_person_redirects"), sh.index("step 13d_sitemap"))

    def test_indexnow_state_is_not_served(self):
        import indexnow_submit as IN
        self.assertNotIn(os.sep + "static" + os.sep, os.path.abspath(IN.STATE))


if __name__ == "__main__":
    unittest.main()


def test_ldjson_drops_a_templates_missing_values():
    # an athlete with no school must not 500 the page (2026-10-10)
    import seo
    from jinja2 import ChainableUndefined
    out = str(seo.ldJson({"@type": "Person", "name": "A",
                          "memberOf": ChainableUndefined(name="school"),
                          "list": [ChainableUndefined(), "x"]}))
    assert "memberOf" not in out and '"list":["x"]' in out
