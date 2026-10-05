"""The site is installable (owner, 2026-10-05): /manifest.webmanifest names
the app and its icons, every icon file exists, and _meta.html links it with
the iOS tags. Served during a table swap like robots.txt."""
import json
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
class WebManifest(unittest.TestCase):
    def setUp(self):
        import app as A
        self.A = A
        self._orig = A._inMaintenance
        A._inMaintenance = lambda: False
        self.c = A.app.test_client()

    def tearDown(self):
        self.A._inMaintenance = self._orig

    def test_manifest_names_the_app_and_its_icons(self):
        r = self.c.get("/manifest.webmanifest")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.mimetype, "application/manifest+json")
        m = json.loads(r.data)
        self.assertEqual(m["start_url"], "/")
        self.assertEqual(m["display"], "standalone")
        sizes = {(i["sizes"], i["purpose"]) for i in m["icons"]}
        self.assertIn(("192x192", "any"), sizes)
        self.assertIn(("512x512", "any"), sizes)
        self.assertIn(("512x512", "maskable"), sizes)
        for i in m["icons"]:
            path = i["src"].split("?")[0].replace("/static/", "", 1)
            self.assertTrue(os.path.exists(os.path.join(self.A.app.static_folder, path)), path)

    def test_served_during_a_swap(self):
        self.A._inMaintenance = lambda: True
        self.assertEqual(self.c.get("/manifest.webmanifest").status_code, 200)

    def test_meta_links_it(self):
        s = open(os.path.join(_ROOT, "racecast", "templates", "_meta.html")).read()
        self.assertIn('rel="manifest" href="/manifest.webmanifest"', s)
        self.assertIn("apple-touch-icon", s)
        self.assertTrue(os.path.exists(os.path.join(
            _ROOT, "racecast", "static", "icons", "apple-touch-icon.png")))


if __name__ == "__main__":
    unittest.main()
