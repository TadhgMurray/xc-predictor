# Project: xc-predictor / tests
# File:    test_speed.py
# Purpose: The speed pass (racecast/speed.py, 2026-11-08): what the browser
#          may fetch before a click, and the service worker's switch.
#
# ★ WHAT THIS PROTECTS.
#   - A hover never prefetches a private or per-person path (/account,
#     /login, /logout, /api/, /search ...) or a download: the speculation
#     rules exclude every one of app._PRIVATE_PREFIXES, so a new private
#     prefix is excluded the day it is added.
#   - The service worker is OFF unless XCP_SW=on, and off means /sw.js is the
#     kill switch (a worker that drops its caches and unregisters), served
#     no-cache so a switched-off worker cannot outlive the flag.
#   - With it on, the worker it serves refuses every private prefix and every
#     non-GET (run through node on the body /sw.js would send).
import json
import os
import re
import shutil
import subprocess
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# a placeholder so app.py can be imported; nothing here opens a connection
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")

import speed  # noqa: E402

_STATIC = os.path.join(_ROOT, "racecast", "static")
_PRIVATE = ("/api/", "/search", "/debug/", "/compare", "/login", "/logout",
            "/auth/", "/account")

try:
    import flask  # noqa: F401
    _HAVE_FLASK = True
except ImportError:
    _HAVE_FLASK = False


def _rulesOf(html):
    m = re.search(r'<script type="speculationrules">(.*?)</script>', str(html), re.S)
    return json.loads(m.group(1)) if m else None


def _never(rule):
    """The href_matches list a rule excludes."""
    for c in rule["where"]["and"]:
        if "not" in c and "href_matches" in c["not"]:
            return c["not"]["href_matches"]
    return []


class SpeculationRules(unittest.TestCase):
    def test_default_is_prefetch_on_hover(self):
        rules = _rulesOf(speed.headHtml(_PRIVATE, "prefetch", "off"))
        self.assertEqual(list(rules), ["prefetch"])
        self.assertEqual(rules["prefetch"][0]["eagerness"], "moderate")

    def test_every_private_prefix_is_excluded(self):
        for mode in ("prefetch", "prerender"):
            rules = _rulesOf(speed.headHtml(_PRIVATE, mode, "off"))
            for kind, rs in rules.items():
                never = _never(rs[0])
                for p in _PRIVATE:
                    self.assertIn(p + "*", never, f"{mode}/{kind} would fetch {p}")

    def test_downloads_and_new_tabs_are_excluded(self):
        rule = _rulesOf(speed.headHtml(_PRIVATE, "prefetch", "off"))["prefetch"][0]
        never = _never(rule)
        for pat in ("/*.csv", "/*.png", "/*.ics", "/*\\?*format=*"):
            self.assertIn(pat, never)
        sel = [c["not"]["selector_matches"] for c in rule["where"]["and"]
               if "not" in c and "selector_matches" in c["not"]][0]
        for s in ("[download]", "[rel~=nofollow]", "[target=_blank]", ".no-prefetch"):
            self.assertIn(s, sel)

    def test_prerender_only_on_the_listed_pages(self):
        rules = _rulesOf(speed.headHtml(_PRIVATE, "prerender", "off"))
        clauses = rules["prerender"][0]["where"]["and"]
        only = [c["href_matches"] for c in clauses
                if "href_matches" in c and isinstance(c["href_matches"], list)]
        self.assertEqual(only, [list(speed.PRERENDER_PATHS)])
        for p in ("/updates*", "/rankings*"):
            self.assertNotIn(p, only[0])

    def test_off_prints_no_rules(self):
        self.assertIsNone(_rulesOf(speed.headHtml(_PRIVATE, "off", "off")))

    def test_rules_cannot_close_the_script(self):
        html = str(speed.headHtml(_PRIVATE + ("/x</script>",), "prefetch", "off"))
        self.assertEqual(html.count("</script>"), html.count("<script"))


class ServiceWorkerSwitch(unittest.TestCase):
    def test_off_registers_nothing_and_unregisters(self):
        html = str(speed.headHtml(_PRIVATE, "off", "off"))
        self.assertNotIn(".register(", html)
        self.assertIn("unregister", html)

    def test_on_registers_the_root_worker(self):
        html = str(speed.headHtml(_PRIVATE, "off", "on"))
        self.assertIn('register("/sw.js")', html)

    def test_off_body_is_the_kill_switch(self):
        body = speed.swBody(_STATIC, _PRIVATE, "off")
        self.assertIn("unregister", body)
        self.assertIn("caches.delete", body)
        self.assertNotIn("respondWith", body)

    def test_on_body_has_no_placeholders_left(self):
        body = speed.swBody(_STATIC, _PRIVATE, "on")
        self.assertNotIn("__RC_SW_", body)
        self.assertIn(json.dumps(list(_PRIVATE)), body)

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_on_body_refuses_private_paths_and_posts(self):
        import app as A
        body = speed.swBody(_STATIC, A._PRIVATE_PREFIXES, "on")
        probe = body + """
;var O = "https://racecast.co", bad = [];
PRIV.forEach(function (p) {
  ["navigate", "cors"].forEach(function (mode) {
    var u = O + p + "x?y=1";
    if (route("GET", u, O, mode) !== "") bad.push("GET " + u + " " + mode);
  });
});
["/athlete/1", "/api/rankings?board=ability", "/static/a.js?v=1"].forEach(function (p) {
  if (route("POST", O + p, O, "navigate") !== "") bad.push("POST " + p);
});
if (route("GET", O + "/athlete/1", O, "navigate") !== "page") bad.push("page not cached");
if (!storable(200, "public, max-age=300", "basic") || storable(200, "no-store", "basic")) bad.push("storable");
process.stdout.write(JSON.stringify(bad));
""".replace("PRIV", json.dumps(list(A._PRIVATE_PREFIXES)))
        out = subprocess.run(["node", "-e", probe], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(json.loads(out.stdout), [])


class HeroPhotos(unittest.TestCase):
    """home.html's nine photos: the 640 px copy is offered only beside the
    original, with sizes saying how wide the cover-cropped tile draws."""

    def _attrs(self, slot, span=1):
        return str(speed.heroSrcset(_STATIC, lambda f: "/static/" + f + "?v=1", slot, span))

    def test_the_wide_tile_offers_both(self):
        a = self._attrs("m2", 2)
        self.assertIn("/static/m2-640.webp?v=1 640w", a)
        self.assertIn("/static/m2.webp?v=1 1100w", a)
        # Bolt's 1100x619 on a 1350 px desktop draws 540 px wide (40vw)
        self.assertIn("max(40vw, 420px)", a)
        # on a phone the tile is ~131 px tall: 1100/619 * 134 = 239 px drawn
        self.assertIn("(max-width: 760px) max(34vw, 239px)", a)

    def test_a_photo_already_small_gets_nothing(self):
        self.assertEqual(self._attrs("m1"), "")          # 399 px wide

    def test_no_copy_no_srcset(self):
        self.assertEqual(self._attrs("m-not-there"), "")

    def test_every_copy_is_640_wide_and_smaller(self):
        from PIL import Image
        for i in range(1, 10):
            small = os.path.join(_STATIC, "m%d-640.webp" % i)
            if not os.path.exists(small):
                continue
            with Image.open(small) as im:
                self.assertEqual(im.size[0], 640, small)
            self.assertLess(os.path.getsize(small),
                            os.path.getsize(os.path.join(_STATIC, "m%d.webp" % i)), small)

    def test_home_template_asks_for_it(self):
        with open(os.path.join(_ROOT, "racecast", "templates", "home.html"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn("hero_srcset(slot", src)


class PrerenderSafety(unittest.TestCase):
    """A prerendered page runs its scripts before anyone sees it (speed.py's
    prerender rules, and Chrome's own address-bar guesses). The scripts on
    the prerendered pages that record or show something on load wait for
    the page to be shown: the view beacon (tests/test_usage_beacon.js runs
    it), the follow help's "seen" mark, and the arrival flash."""

    def test_on_load_effects_wait_for_activation(self):
        for f in ("usage.js", "follow.js", "link-flash.js", "race-page.js"):
            with open(os.path.join(_STATIC, f), encoding="utf-8") as fh:
                src = fh.read()
            self.assertIn("document.prerendering", src.replace("d.prerendering", "document.prerendering"), f)
            self.assertIn("prerenderingchange", src, f)


@unittest.skipUnless(_HAVE_FLASK, "flask not installed")
class Wiring(unittest.TestCase):
    def test_meta_partial_prints_it(self):
        with open(os.path.join(_ROOT, "racecast", "templates", "_meta.html"), encoding="utf-8") as f:
            self.assertIn("{{ speed_head() }}", f.read())

    def test_sw_route_is_never_held(self):
        import app as A
        with A.app.test_client() as c:
            r = c.get("/sw.js")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.mimetype, "application/javascript")
        self.assertIn("no-cache", r.headers["Cache-Control"])
        self.assertNotIn("public", r.headers["Cache-Control"])
        self.assertEqual(r.headers["Service-Worker-Allowed"], "/")
        if speed.SW != "on":
            self.assertIn("unregister", r.get_data(as_text=True))

    def test_speed_head_global(self):
        import app as A
        self.assertIn("speed_head", A.app.jinja_env.globals)


if __name__ == "__main__":
    unittest.main()
