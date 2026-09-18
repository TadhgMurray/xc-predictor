# Project: xc-predictor / tests
# File:    test_chrome_path_shared.py
# Purpose: both scrapers launch the SAME browser, and it is real Chrome.
#
# ★ THE anet LAUNCHER LEARNED THIS AND THE TFRRS ONE DID NOT (2026-09-18).
#   launcher.py resolves a Chrome path and says why in a comment; launch_tfrrs
#   called pw.chromium.launch(headless=False) with no executable_path, i.e.
#   Playwright's BUNDLED chromium -- which does not exist on a box that never
#   ran `playwright install`, and which Cloudflare fingerprints and blocks on
#   one that did. Two launchers, two answers, one of them silently wrong.
import io
import os
import re
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_LAUNCHERS = ("scripts/launcher.py", "tfrrs/driver/launch_tfrrs.py")


def _read(rel):
    with io.open(os.path.join(_ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


# ! CODE ONLY. The comments in these files QUOTE the broken call they are
#   warning about -- "pw.chromium.launch(headless=False) with no
#   executable_path" -- so a scan over the raw text flags the explanation of
#   the bug as the bug. A test that reads prose is testing prose.
def _code(rel):
    return "\n".join(line for line in _read(rel).splitlines()
                      if not line.lstrip().startswith("#"))


class EveryBrowserLaunchNamesAnExecutable(unittest.TestCase):

    def test_no_launch_omits_the_executable(self):
        for rel in _LAUNCHERS:
            src = _code(rel)
            for m in re.finditer(r"chromium\.launch\((.*?)\)", src, re.S):
                with self.subTest(module=rel, call=m.group(0)[:60]):
                    self.assertIn("executable_path", m.group(1))

    def test_and_none_is_headless(self):
        """Cloudflare blocks headless, which is the same lesson."""
        for rel in _LAUNCHERS:
            src = _code(rel)
            for m in re.finditer(r"chromium\.launch\((.*?)\)", src, re.S):
                with self.subTest(module=rel):
                    self.assertIn("headless=False", m.group(1))

    def test_they_share_one_definition(self):
        for rel in _LAUNCHERS:
            with self.subTest(module=rel):
                self.assertIn("chrome_path import", _read(rel))
        # and the definition lives in exactly one place
        self.assertTrue(os.path.exists(
            os.path.join(_ROOT, "scripts", "chrome_path.py")))


class ItFailsWithTheFix(unittest.TestCase):

    def setUp(self):
        import sys
        sys.path.insert(0, os.path.join(_ROOT, "scripts"))
        import chrome_path
        self.mod = chrome_path

    def test_found_never_raises(self):
        """A module-level constant must not explode at import on a box with
        no Chrome -- the tests import these launchers."""
        self.mod.found()          # no assertion: it must simply not raise

    def test_the_error_names_every_path_and_the_command(self):
        if self.mod.found():
            self.skipTest("Chrome is installed here")
        with self.assertRaises(RuntimeError) as ctx:
            self.mod.chromePath()
        msg = str(ctx.exception)
        for path in self.mod.candidates():
            self.assertIn(path, msg)
        self.assertIn("apt-get install", msg)
        # and it says why chromium is not a substitute
        self.assertIn("fingerprints", msg)

    def test_the_environment_wins(self):
        os.environ["CHROME_PATH"] = "/nowhere/chrome"
        try:
            self.assertEqual(self.mod.chromePath(), "/nowhere/chrome")
        finally:
            del os.environ["CHROME_PATH"]


if __name__ == "__main__":
    unittest.main(verbosity=2)
