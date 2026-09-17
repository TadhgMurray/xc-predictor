# Project: xc-predictor / tests
# File:    test_logo_ground.py
# Purpose: a crest's flat opaque ground comes off, whatever colour it is
#          (owner, 2026-09-16: "the backgrounds are black but the background
#          on anet are white, so idk where the black is coming from (I also
#          don't like it)"). Pillow only, no network, no database.
#
#   python -m pytest -q tests/test_logo_ground.py
import io
import os
import sys
import unittest

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "racecast")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import scrape_school_logos as S                                # noqa: E402

try:
    from PIL import Image
except ImportError:                                            # pragma: no cover
    Image = None


def _png(ground, flatten, size=(300, 240), mark=(90, 70, 120, 100)):
    """A red block on `ground`; `flatten` drops the alpha channel, which is
    what a re-encode to JPEG does -- and a flatten with no colour given is
    black, which is where the owner's black grounds come from."""
    im = Image.new("RGBA", size, ground)
    im.paste(Image.new("RGBA", (mark[2], mark[3]), (220, 30, 30, 255)),
             (mark[0], mark[1]))
    if flatten:
        im = im.convert("RGB")
    out = io.BytesIO()
    im.save(out, "PNG")
    return out.getvalue()


@unittest.skipIf(Image is None, "Pillow not installed")
class TheGround(unittest.TestCase):

    def test_the_owners_real_corner_sets_are_judged_correctly(self):
        """⚠ FROM THE 2026-09-17 SCAN OF THE LIVE DIRECTORY. The thresholds
        were 16/10 and 10 was too tight to be useful: 015113a0's corners are
        (9,13,22) (0,4,7) (11,11,11) (8,8,8) -- one flat near-black ground by
        eye, refused because its channels differ by 11. A JPEG's flat ground
        is a few levels of noise around one value."""
        def corners(cs):
            im = Image.new("RGBA", (80, 80), (200, 40, 40, 255))
            for (x, y), c in zip([(0, 0), (79, 0), (0, 79), (79, 79)], cs):
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        im.putpixel((min(max(x + dx, 0), 79),
                                     min(max(y + dy, 0), 79)), (*c, 255))
            return im
        keyed = {
            "near-black, noisy": [(9, 13, 22), (0, 4, 7), (11, 11, 11), (8, 8, 8)],
            "charcoal": [(36, 32, 33), (37, 33, 34), (36, 32, 33), (33, 29, 30)],
            "dark green": [(11, 69, 55)] * 4,
            "navy": [(17, 14, 83)] * 4,
        }
        for label, cs in keyed.items():
            self.assertIsNotNone(S._flatGround(corners(cs)), label)
        left = {
            "a real blue gradient": [(41, 96, 150), (3, 64, 129), (0, 66, 126),
                                     (1, 65, 127)],
            "white with a yellow corner": [(255, 255, 255), (255, 214, 81),
                                           (241, 246, 242), (253, 255, 252)],
            "a grey ramp": [(255, 255, 255), (216, 216, 216), (239, 239, 239),
                            (134, 134, 134)],
        }
        for label, cs in left.items():
            self.assertIsNone(S._flatGround(corners(cs)), label)

    def test_the_tolerance_is_at_least_as_wide_as_the_spread_it_accepts(self):
        """Or the corners themselves survive the key they just authorised."""
        self.assertGreaterEqual(S.KEY_TOLERANCE, S.KEY_MAX_SPREAD // 2)

    def test_a_flat_ground_is_found_whatever_colour_it_is(self):
        for colour in ((0, 0, 0, 255), (255, 255, 255, 255), (17, 34, 51, 255)):
            im = Image.open(io.BytesIO(_png(colour, True))).convert("RGBA")
            self.assertEqual(S._flatGround(im), colour[:3])

    def test_an_image_with_its_own_alpha_is_left_alone(self):
        """A crest that arrived transparent is already right, and keying it
        would be guessing at a colour it does not have."""
        im = Image.open(io.BytesIO(_png((0, 0, 0, 0), False))).convert("RGBA")
        self.assertIsNone(S._flatGround(im))

    def test_disagreeing_corners_are_not_a_ground(self):
        """A photograph or a gradient: keying one colour would punch holes."""
        im = Image.new("RGBA", (60, 60))
        for x in range(60):
            for y in range(60):
                im.putpixel((x, y), (x * 4 % 256, y * 4 % 256, 128, 255))
        self.assertIsNone(S._flatGround(im))

    def test_a_mark_that_reaches_its_own_corner_is_not_keyed(self):
        im = Image.new("RGBA", (60, 60), (255, 255, 255, 255))
        im.paste(Image.new("RGBA", (20, 20), (10, 10, 10, 255)), (0, 0))
        self.assertIsNone(S._flatGround(im))       # corners disagree

    def test_keying_makes_the_ground_transparent_and_nothing_else(self):
        im = Image.open(io.BytesIO(_png((0, 0, 0, 255), True))).convert("RGBA")
        keyed, n = S._keyGround(im, (0, 0, 0))
        self.assertGreater(n, 0)
        self.assertEqual(keyed.load()[0, 0][3], 0)          # ground gone
        self.assertEqual(keyed.load()[100, 80][3], 255)     # mark kept
        self.assertEqual(keyed.load()[100, 80][:3], (220, 30, 30))


@unittest.skipIf(Image is None, "Pillow not installed")
class EndToEnd(unittest.TestCase):

    def _normalised(self, raw):
        data, sha, wh = S.normalise(raw, px=64, kind="icon")
        self.assertIsNotNone(data, f"rejected: {wh}")
        return Image.open(io.BytesIO(data)).convert("RGBA"), wh

    def test_a_black_ground_is_cropped_away_like_a_transparent_one(self):
        """⚠ AND THIS IS THE SECOND HALF OF THE BUG. normalise promises
        "transparent margins come off", and getbbox() can only crop what is
        transparent -- so for every opaque source the margin STAYED and the
        mark was shrunk to fit a box it was already inside."""
        black, wh_black = self._normalised(_png((0, 0, 0, 255), True))
        clear, wh_clear = self._normalised(_png((0, 0, 0, 0), False))
        self.assertEqual(wh_black, (120, 100))     # the mark, not the canvas
        self.assertEqual(wh_black, wh_clear)       # same answer either way
        self.assertEqual(black.load()[0, 0][3], 0)

    def test_a_white_ground_too(self):
        white, wh = self._normalised(_png((255, 255, 255, 255), True))
        self.assertEqual(wh, (120, 100))

    def test_a_photograph_keeps_every_pixel_it_arrived_with(self):
        im = Image.new("RGBA", (300, 240))
        for x in range(300):
            for y in range(240):
                im.putpixel((x, y), (x % 256, y % 256, 128, 255))
        out = io.BytesIO(); im.save(out, "PNG")
        _got, wh = self._normalised(out.getvalue())
        self.assertEqual(wh, (300, 240))           # nothing keyed, nothing cropped


if __name__ == "__main__":
    unittest.main()


@unittest.skipIf(Image is None, "Pillow not installed")
class NothingSurvives(unittest.TestCase):
    """⚠ CAUGHT BY test_school_logos, NOT BY ME. An image that is ENTIRELY
    its ground -- a solid block, a one-colour badge -- has no ground to key:
    keying it leaves nothing, every colour normalises to the same empty
    square, and the district sweep would then see a thousand schools wearing
    one crest."""

    def _solid(self, colour, size=(200, 200)):
        out = io.BytesIO()
        Image.new("RGBA", size, colour).convert("RGB").save(out, "PNG")
        return out.getvalue()

    def test_a_solid_block_keeps_its_colour_and_its_hash(self):
        red = S.normalise(self._solid((200, 0, 0, 255)), px=64, kind="icon")
        blue = S.normalise(self._solid((0, 0, 200, 255)), px=64, kind="icon")
        self.assertIsNotNone(red[0])
        self.assertNotEqual(red[1], blue[1], "two colours, two crests")
        out = Image.open(io.BytesIO(red[0])).convert("RGBA")
        self.assertEqual(out.load()[32, 32][3], 255, "not keyed to nothing")

    def test_a_mark_too_small_to_keep_reverts_to_the_whole_image(self):
        """The same guard from the other side: if what survives the key is
        no longer an acceptable mark, the key is not worth having."""
        im = Image.new("RGBA", (200, 200), (255, 255, 255, 255))
        im.paste(Image.new("RGBA", (6, 6), (0, 0, 0, 255)), (100, 100))
        out = io.BytesIO(); im.convert("RGB").save(out, "PNG")
        data, _sha, wh = S.normalise(out.getvalue(), px=64, kind="icon")
        self.assertIsNotNone(data)
        self.assertEqual(wh, (200, 200), "a 6px survivor is not the mark")


# ===================================================================== #
#  THE BLACK BACKGROUND, AND WHY THE KEY WAS MISSING IT                 #
# ===================================================================== #

class TheOpaqueRegion(unittest.TestCase):
    """★ THE BUG (owner, 2026-09-16: "the backgrounds are black but the
    background on anet are white, so idk where the black is coming from").
    normalise centres a crest on a TRANSPARENT square. So a dark card that is
    not square -- or one that has been through here once already -- is an
    opaque black rectangle with transparent margins beside it, and the CANVAS
    corners are those margins. _flatGround saw alpha 0, said "it already has
    its own alpha", and left the black ground alone."""

    def _card(self, ground, size=(300, 240), card=(20, 40, 200, 120),
              mark=(60, 70, 80, 60)):
        """An opaque `ground` card inset in a transparent canvas, with a red
        mark on the card -- what a stored non-square crest looks like."""
        im = Image.new("RGBA", size, (0, 0, 0, 0))
        im.paste(Image.new("RGBA", (card[2], card[3]), ground),
                 (card[0], card[1]))
        im.paste(Image.new("RGBA", (mark[2], mark[3]), (220, 30, 30, 255)),
                 (mark[0], mark[1]))
        return im

    def test_a_dark_card_inside_a_transparent_canvas_is_found(self):
        for ground in ((0, 0, 0, 255), (9, 13, 22, 255), (17, 17, 17, 255)):
            im = self._card(ground)
            self.assertIsNotNone(S._flatGround(im),
                                 f"missed the ground {ground[:3]}")

    def test_the_canvas_corners_really_are_transparent(self):
        """The premise of the bug, asserted so the fixture cannot drift."""
        im = self._card((0, 0, 0, 255))
        w, h = im.size
        for xy in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)):
            self.assertEqual(im.getpixel(xy)[3], 0)

    def test_a_fully_opaque_image_is_probed_exactly_as_before(self):
        im = Image.new("RGBA", (200, 200), (17, 34, 51, 255))
        im.paste(Image.new("RGBA", (80, 60), (220, 30, 30, 255)), (60, 70))
        self.assertEqual(S._opaqueBox(im), (0, 0, 200, 200))
        self.assertEqual(S._flatGround(im), (17, 34, 51))

    def test_a_crest_that_is_all_mark_is_not_a_ground(self):
        """! A GROUND HAS SOMETHING ON IT. Once the probe moved to the opaque
        region, a transparent crest started reporting its own mark's colour as
        a ground -- and keying that empties the crest."""
        im = Image.new("RGBA", (200, 200), (0, 0, 0, 0))
        im.paste(Image.new("RGBA", (80, 60), (220, 30, 30, 255)), (40, 40))
        self.assertIsNone(S._flatGround(im))
        self.assertLess(S.GROUND_MAX_SHARE, 1.0)

    def test_the_repair_keys_the_ground_and_keeps_the_mark(self):
        out = io.BytesIO()
        self._card((0, 0, 0, 255)).save(out, "PNG")
        data, sha, why = S.regroundBytes(out.getvalue())
        self.assertIsNotNone(data, why)
        self.assertIn("keyed", why)
        fixed = Image.open(io.BytesIO(data)).convert("RGBA")
        # the black is gone...
        self.assertEqual(S._opaqueBox(fixed) is None, False)
        # ! getdata() is deprecated in Pillow 12 and gone in 14; the bands
        #   say the same thing and the codebase already avoids it in
        #   _keyGround.
        r, g, b, alpha = fixed.split()
        colours = {p[:3] for p in zip(r.tobytes(), g.tobytes(), b.tobytes(),
                                      alpha.tobytes()) if p[3] > 200}
        self.assertTrue(colours, "the mark was erased")
        for c in colours:
            self.assertGreater(max(c), 100, f"{c} is still the dark ground")
        self.assertEqual(len(sha), 64)

    def test_the_repair_is_idempotent(self):
        """A second run must be a no-op, or the pass cannot be re-run."""
        out = io.BytesIO()
        self._card((0, 0, 0, 255)).save(out, "PNG")
        once, _sha, _why = S.regroundBytes(out.getvalue())
        twice, _s2, why2 = S.regroundBytes(once)
        self.assertIsNone(twice, why2)
        self.assertIn(why2, ("no flat ground", "unchanged"))

    def test_the_repair_leaves_an_already_transparent_crest_alone(self):
        im = Image.new("RGBA", (200, 200), (0, 0, 0, 0))
        im.paste(Image.new("RGBA", (80, 60), (220, 30, 30, 255)), (40, 40))
        out = io.BytesIO()
        im.save(out, "PNG")
        data, _sha, why = S.regroundBytes(out.getvalue())
        self.assertIsNone(data)
        self.assertEqual(why, "no flat ground")

    def test_the_repair_refuses_when_keying_would_leave_nothing(self):
        """A solid one-colour badge: keying it makes every colour hash the
        same, which is the thousand-schools-one-crest bug."""
        im = Image.new("RGBA", (64, 64), (12, 12, 12, 255))
        out = io.BytesIO()
        im.save(out, "PNG")
        data, _sha, why = S.regroundBytes(out.getvalue())
        self.assertIsNone(data, why)

    def test_the_repair_never_touches_the_network(self):
        src = open(os.path.join(_ROOT, "scripts",
                                "scrape_school_logos.py")).read()
        i = src.index("def regroundAll(")
        j = src.index("def main():", i)
        body = src[i:j]
        for banned in ("requests", "urlopen", "fetch(", "source_url", "http"):
            self.assertNotIn(banned, body, f"{banned} in the repair pass")
        # and it only writes with --write
        self.assertIn("if write:", body)
