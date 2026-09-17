# An XC meet page is not a track meet, however it is asked about.
#
# prefill_tfrrs_queue seeds every TFRRS id under BOTH sports on purpose, so
# the classifier is the ONLY thing standing between a cross country meet and
# the track tables. It used to ignore the sport it was asked about.
import os
import re
import ast
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

XC_PAGE = """
<html><body>
  <h3>Men's Race - 8000 Meters Individual Results (8k)</h3>
  <table class="tablesaw tablesaw-xc"><tr><td>1</td></tr></table>
  <a href="/results/xc/12345/some-invite">Race Results</a>
</body></html>
"""

TF_PAGE = """
<html><body>
  <a href="/results/12345/m/some-relays">Men</a>
  <a href="/results/12345/f/some-relays">Women</a>
  <table class="tablesaw"><tr><td>1</td></tr></table>
</body></html>
"""

# An event leaf: it links UP to a different parent id.
EVENT_LEAF = """
<html><body><a href="/results/99999/m/parent-meet">Parent</a></body></html>
"""

# ⚠ THE PAGE THAT CAUSED IT. TFRRS serves the XC meet at the bare
#   /results/<id> the TF claim fetches, and the page can carry a self-link
#   the TF pattern matches as well as its XC tables.
XC_PAGE_WITH_TF_SHAPED_LINK = """
<html><body>
  <table class="tablesaw tablesaw-xc"><tr><td>1</td></tr></table>
  <a href="/results/12345/m/some-invite">Men</a>
</body></html>
"""


try:
    from bs4 import BeautifulSoup
    HAVE_BS4 = True
except ImportError:                 # the parser's own dependency, not ours
    HAVE_BS4 = False


def _classifier():
    src = open(os.path.join(ROOT, "tfrrs", "driver", "run_tfrrs.py")).read()
    tree = ast.parse(src)
    ns = {"re": re, "BeautifulSoup": BeautifulSoup}
    for node in tree.body:
        if isinstance(node, ast.Assign) and getattr(
                node.targets[0], "id", "") in {"_TF_SELF_RE", "_XC_SELF_RE",
                                               "SPORT_XC", "SPORT_TF"}:
            exec(ast.get_source_segment(src, node), ns)
        if isinstance(node, ast.FunctionDef) and node.name == "_isMeetPage":
            exec(ast.get_source_segment(src, node), ns)
    return ns["_isMeetPage"]


@unittest.skipUnless(HAVE_BS4, "beautifulsoup4 not installed")
class ItAsksAboutTheSportItWasGiven(unittest.TestCase):
    def setUp(self):
        self.isMeetPage = _classifier()

    def test_xc_page_claimed_as_xc_is_a_meet(self):
        self.assertTrue(self.isMeetPage(XC_PAGE, 12345, "XC"))

    # ★ THE BUG, EXACTLY. This returned True, the track parser got the
    #   cross country page, and the race appeared twice -- once right, and
    #   once in track under the heading the parser scraped off it.
    def test_xc_page_claimed_as_tf_is_not_a_meet(self):
        self.assertFalse(self.isMeetPage(XC_PAGE, 12345, "TF"))

    def test_xc_page_with_a_tf_shaped_self_link_is_still_not_track(self):
        self.assertFalse(
            self.isMeetPage(XC_PAGE_WITH_TF_SHAPED_LINK, 12345, "TF"))

    def test_tf_page_claimed_as_tf_is_a_meet(self):
        self.assertTrue(self.isMeetPage(TF_PAGE, 12345, "TF"))

    def test_tf_page_claimed_as_xc_is_not_a_meet(self):
        self.assertFalse(self.isMeetPage(TF_PAGE, 12345, "XC"))

    def test_an_event_leaf_is_never_a_meet(self):
        for sport in ("XC", "TF", None):
            with self.subTest(sport=sport):
                self.assertFalse(self.isMeetPage(EVENT_LEAF, 12345, sport))

    # The sportless call is what the function did before; keeping it means a
    # caller without a sport is no worse off than it was.
    def test_no_sport_keeps_the_old_answer(self):
        self.assertTrue(self.isMeetPage(XC_PAGE, 12345, None))
        self.assertTrue(self.isMeetPage(TF_PAGE, 12345, None))


if __name__ == "__main__":
    unittest.main()
