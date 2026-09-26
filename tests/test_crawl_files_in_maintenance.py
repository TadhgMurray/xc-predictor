"""robots.txt and the sitemap answer during a table swap (2026-09-26):
Google reads a 5xx on robots.txt as "stop crawling this site"."""
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")

try:
    import flask                                               # noqa: F401
    _HAVE = True
except ImportError:
    _HAVE = False


@unittest.skipUnless(_HAVE, "flask not installed")
class CrawlFilesInMaintenance(unittest.TestCase):
    def setUp(self):
        import app as A
        self.A = A
        self._orig = A._inMaintenance
        A._inMaintenance = lambda: True
        self.c = A.app.test_client()

    def tearDown(self):
        self.A._inMaintenance = self._orig

    def test_robots_is_served_during_a_swap(self):
        r = self.c.get("/robots.txt")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"Sitemap:", r.data)

    def test_google_verification_is_served_during_a_swap(self):
        tok = sorted(self.A.GOOGLE_VERIFY_TOKENS)[0]
        self.assertEqual(self.c.get(f"/{tok}.html").status_code, 200)

    def test_sitemap_is_not_a_503(self):
        self.assertNotEqual(self.c.get("/sitemap.xml").status_code, 503)

    def test_pages_still_503_during_a_swap(self):
        self.assertEqual(self.c.get("/about").status_code, 503)


if __name__ == "__main__":
    unittest.main()


class FinishTimesAreNotTeams(unittest.TestCase):
    def test_times_are_refused_and_schools_kept(self):
        from panels import isTeamName
        for t in ("5:22.9", "12:16.57", "2:44.48", "4:01"):
            self.assertFalse(isTeamName(t), t)
        for s in ("Jesuit", "49ers Academy", "1st Philadelphia", "Oregon"):
            self.assertTrue(isTeamName(s), s)
