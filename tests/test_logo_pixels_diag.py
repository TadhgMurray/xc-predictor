# Project: xc-predictor / tests
# File:    test_logo_pixels_diag.py
# Purpose: the crest diagnostic separates the four causes of a black
#          background, so the next claim about it is evidence rather than a
#          theory (owner, 2026-09-17: "How do you want to prove the
#          backgrounds?"). Pillow only.
#
#   python -m pytest -q tests/test_logo_pixels_diag.py
import io
import os
import sys
import tempfile
import unittest

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "racecast")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import diag_logo_pixels as D                                   # noqa: E402

try:
    from PIL import Image
except ImportError:                                            # pragma: no cover
    Image = None


@unittest.skipIf(Image is None, "Pillow not installed")
class FourCauses(unittest.TestCase):
    """Each verdict has to be reachable, or the diagnostic cannot tell me I
    am wrong -- which is the only reason it exists."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def _file(self, ground, flatten, name="x.png"):
        im = Image.new("RGBA", (200, 160), ground)
        im.paste(Image.new("RGBA", (80, 60), (200, 40, 40, 255)), (60, 50))
        if flatten:
            im = im.convert("RGB")
        p = os.path.join(self.dir, name)
        im.save(p)
        return p

    def _quiet(self, path):
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            return D.describe(path, "t")

    def test_an_opaque_black_ground_says_so(self):
        got = self._quiet(self._file((0, 0, 0, 255), True))
        self.assertIn("OPAQUE BLACK GROUND", got["verdict"])
        self.assertEqual(got["clear"], 0)
        self.assertLess(got["lum"], 30)

    def test_an_opaque_white_ground_says_it_predates_the_fix(self):
        got = self._quiet(self._file((255, 255, 255, 255), True))
        self.assertIn("OPAQUE WHITE GROUND", got["verdict"])

    def test_a_transparent_file_sends_me_looking_elsewhere(self):
        got = self._quiet(self._file((0, 0, 0, 0), False))
        self.assertIn("the black is elsewhere", got["verdict"])
        self.assertGreater(got["clear"], got["solid"])

    def test_a_coloured_ground_is_neither(self):
        got = self._quiet(self._file((16, 32, 72, 255), True))
        self.assertIn("coloured ground", got["verdict"])

    def test_an_unreadable_file_is_reported_not_raised(self):
        p = os.path.join(self.dir, "junk.png")
        open(p, "wb").write(b"not a png")
        self.assertIsNone(self._quiet(p))

    def test_it_reads_and_never_writes(self):
        src = open(os.path.join(_ROOT, "scripts", "diag_logo_pixels.py")).read()
        for forbidden in (".save(", "os.remove", "os.replace", "UPDATE ",
                          "DELETE ", "INSERT "):
            self.assertNotIn(forbidden, src, forbidden)


if __name__ == "__main__":
    unittest.main()
