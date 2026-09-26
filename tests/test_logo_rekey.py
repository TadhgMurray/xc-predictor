"""The crest key takes off only the ground that touches the edge (owner,
2026-09-26: the Jesuit (CA) crest lost its black outlines to it)."""
import io
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))

try:
    from PIL import Image, ImageDraw
    _HAVE = True
except ImportError:
    _HAVE = False


def crest():
    im = Image.new("RGBA", (200, 200), (0, 0, 0, 255))        # black card
    d = ImageDraw.Draw(im)
    d.ellipse((30, 30, 170, 170), fill=(255, 255, 255, 255))   # white disc
    d.ellipse((60, 60, 140, 140), outline=(0, 0, 0, 255), width=8)  # black ring
    d.rectangle((90, 90, 110, 110), fill=(6, 6, 6, 255))       # near-black mark
    return im


@unittest.skipUnless(_HAVE, "Pillow not installed")
class Key(unittest.TestCase):
    def setUp(self):
        import scrape_school_logos as L
        self.L = L

    def test_the_card_comes_off_and_the_marks_black_stays(self):
        im = crest()
        keyed, _n = self.L._keyGround(im, self.L._flatGround(im))
        a = keyed.getchannel("A")
        self.assertEqual(a.getpixel((2, 2)), 0)         # the card
        self.assertEqual(a.getpixel((63, 100)), 255)    # the ring
        self.assertEqual(a.getpixel((100, 100)), 255)   # the dark centre

    def test_holes_are_what_the_old_key_left(self):
        im = crest()
        old = im.copy()
        px = old.load()
        for x in range(200):
            for y in range(200):
                if max(px[x, y][:3]) < 30:
                    px[x, y] = (0, 0, 0, 0)
        self.assertGreater(self.L.enclosedHoles(old), self.L.REKEY_MIN_HOLES)
        data, _sha, _ = self.L.finish(im)
        fixed = Image.open(io.BytesIO(data)).convert("RGBA")
        self.assertLess(self.L.enclosedHoles(fixed), self.L.REKEY_MIN_HOLES)


@unittest.skipUnless(_HAVE, "Pillow not installed")
class Vanish(unittest.TestCase):
    """A light mark on a dark card keeps its card: keyed out, a white mark
    sits on the site's white and the crest is gone (owner, 2026-09-26)."""
    def setUp(self):
        import scrape_school_logos as L
        self.L = L

    def draw(self, mark):
        im = Image.new("RGBA", (200, 200), (0, 0, 0, 255))
        d = ImageDraw.Draw(im)
        d.ellipse((40, 40, 160, 160), fill=mark)
        return im

    def test_white_on_black_keeps_the_card(self):
        data, _sha, _ = self.L.finish(self.draw((255, 255, 255, 255)))
        out = Image.open(io.BytesIO(data)).convert("RGBA")
        self.assertEqual(out.getchannel("A").getpixel((5, 100)), 255)

    def test_a_coloured_mark_still_loses_its_card(self):
        data, _sha, _ = self.L.finish(self.draw((200, 20, 20, 255)))
        out = Image.open(io.BytesIO(data)).convert("RGBA")
        self.assertEqual(out.getchannel("A").getpixel((2, 2)), 0)


if __name__ == "__main__":
    unittest.main()
