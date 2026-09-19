# Project: xc-predictor / tests
# File:    test_link_supermajority.py
# Purpose: the third route to a tfrrs->anet link, checked against the REAL
#          cases from the 2026-09-19 dry run.
#
# ★ THE CASE IT EXISTS FOR. The margin test asks "does the winner beat the
#   runner-up by Nx". That is right when the rival is noise and wrong when the
#   rival is a same-named sibling college, because then the ratio is low
#   PRECISELY BECAUSE both schools are real. From the dry run, all rejected on
#   margin alone:
#
#       Carroll                 342 athletes  63% share  1.7x   Carroll (Wis.)
#       Ottawa                  292           46%        1.2x   Ottawa (Kan.)
#       Northwestern University  93           64%        1.8x   NU Club
#       Illinois State Univ.     61           55%        1.2x   Illinois State
#
#   Three hundred and forty-two agreeing athletes is not thin evidence.
#
# ⚠ AND THE LINE IS DRAWN SO THE AMBIGUOUS ONES STILL FAIL, because the owner's
#   standing rule is "I'd prefer to separate more than over merge". Carroll and
#   Northwestern pass; Ottawa at 46% and Illinois State at 55% do not, and they
#   are genuinely ambiguous.
#
#   python -m unittest tests.test_link_supermajority
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _env  # noqa: E402,F401  -- sets XCP_DB_PASSWORD; see tests/_env.py
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import link_tfrrs_to_anet as lk                                 # noqa: E402


def _case(school, anet_name, n_ath, share, ratio, state="XX"):
    """A `counted` entry reproducing one row of the dry run.

    ! share AND ratio ARE BOTH DERIVED FROM THE VOTES, so the fixture has to
      produce vote counts that yield them, not assert them directly -- that is
      what makes this a test of decide() rather than of arithmetic I retyped.
    """
    runner = int(round(n_ath / ratio))
    others = max(int(round(n_ath / share)) - n_ath - runner, 0)
    by_team = {1: (n_ath, 5), 2: (runner, 3)}
    if others:
        by_team[3] = (others, 2)
    # ! THE GUARD IS "SAME SIDE OF EVERY BAR", NOT "EXACTLY THIS RATIO", and
    #   the difference matters. Whole athletes rarely divide to a round ratio --
    #   93 athletes at a claimed 1.8x is really 93/52 = 1.788, which is fine
    #   because both are under 2.0. What is NOT fine is the case that started
    #   this: 20 athletes at a claimed 1.95x is really 20/10 = 2.00, which
    #   CROSSES the named bar, so the test passed a link it meant to reject and
    #   failed for a reason unrelated to the code. Checking the side is
    #   tolerance-free and tests the only thing the fixture has to get right.
    got = n_ath / float(runner) if runner else float("inf")
    for bar in (lk.MARGIN_NAMED, lk.MARGIN):
        assert (got >= bar) == (ratio >= bar), (
            f"the fixture cannot express {ratio}x with {n_ath} athletes: "
            f"runner={runner} gives {got:.3f}, which falls on the other side "
            f"of the {bar}x bar. Pick counts whose quotient stays on the "
            f"intended side.")
    return ({school: by_team},
            {1: (anet_name, state), 2: ("Sibling College", state),
             3: ("Third", state)})


class TheSupermajorityTier(unittest.TestCase):

    def _decide(self, school, anet_name, n_ath, share, ratio, **kw):
        counted, teams = _case(school, anet_name, n_ath, share, ratio)
        links, rejected = lk.decide(counted, teams, **kw)
        return links, rejected

    def test_carroll_links_on_volume_and_share(self):
        links, rejected = self._decide("Carroll", "Carroll (Wis.)",
                                       342, 0.63, 1.7)
        self.assertEqual(len(links), 1, f"rejected: {rejected}")
        self.assertEqual(links[0][9], "supermajority",
                         "it must be labelled as the tier that passed it, not "
                         "as a name match")

    def test_northwestern_links_too(self):
        links, _r = self._decide("Northwestern University",
                                 "Northwestern University Club", 93, 0.64, 1.8)
        self.assertEqual(len(links), 1)

    def test_ottawa_still_fails_at_46_percent(self):
        # ⚠ The point of the share bar. 292 athletes is plenty of volume, but a
        #   minority of the votes is not a verdict.
        links, rejected = self._decide("Ottawa", "Ottawa (Kan.)",
                                       292, 0.46, 1.2)
        self.assertEqual(links, [])
        self.assertEqual(len(rejected), 1)
        self.assertIn("supermajority needs", rejected[0][11],
                      "the reason must say the second route was tried and why "
                      "it failed, or a reader cannot tell which bar to argue "
                      f"with: {rejected[0][11]!r}")

    def test_illinois_state_still_fails_at_55_percent(self):
        links, _r = self._decide("Illinois State University",
                                 "Illinois State", 61, 0.55, 1.2)
        self.assertEqual(links, [])

    def test_volume_alone_is_not_enough(self):
        # 40 athletes at a 90% share: a clear majority of a SMALL vote.
        links, _r = self._decide("Tiny", "Tiny College", 40, 0.90, 1.5)
        self.assertEqual(links, [], "under SUPERMAJORITY_ATHLETES it must fail")

    def test_a_name_match_is_required(self):
        # ⚠ WITHOUT A NAME THE MARGIN IS THE ONLY PROTECTION THERE IS, so the
        #   blind tier keeps its 3x untouched however large the vote.
        counted, teams = _case("Something Else Entirely", "Carroll (Wis.)",
                               900, 0.70, 1.5)
        links, rejected = lk.decide(counted, teams)
        self.assertEqual(links, [])
        self.assertNotIn("supermajority", rejected[0][11],
                         "a nameless string should not even be offered the "
                         "second route")

    def test_the_tier_is_disableable_and_then_reproduces_the_old_behaviour(self):
        for kw in ({"super_athletes": 10 ** 9}, {"super_share": 1.01}):
            links, _r = self._decide("Carroll", "Carroll (Wis.)",
                                     342, 0.63, 1.7, **kw)
            self.assertEqual(links, [], f"not disabled by {kw}")

    def test_the_margin_route_still_works_and_is_labelled_as_itself(self):
        links, _r = self._decide("Williams", "Williams", 937, 0.999, 937.0)
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0][9], "name+athletes",
                         "a clean margin win must not be relabelled")

    def test_johnson_and_wales_is_now_rescued_by_the_tier(self):
        # ! IT USED TO BE THE MARGIN-DISPLAY EXAMPLE and it is no longer
        #   rejected at all: 86 athletes at 62% clears the supermajority bars,
        #   which is exactly the case the tier is for. Pinned here so the
        #   change of outcome is deliberate rather than noticed later.
        links, rejected = self._decide("Johnson & Wales",
                                       "Johnson & Wales (R.I.)", 86, 0.62, 1.95)
        self.assertEqual(len(links), 1, f"rejected: {rejected}")
        self.assertEqual(links[0][9], "supermajority")

    def test_a_rejection_message_can_no_longer_contradict_its_own_row(self):
        # ⚠ THE BUG: "1.9x" in the margin column against "margin 2.0x < 2.0x"
        #   in the reason -- one row calling a number both under and equal to
        #   the bar. It was 1.95 shown two ways, round(ratio, 2) at .1f against
        #   the raw value at .1f. Checked on a case the tier does NOT rescue,
        #   so a rejection message still exists to inspect.
        # ! 39 AND NOT 20, BECAUSE THE RATIO HAS TO BE REACHABLE WITH WHOLE
        #   ATHLETES. runner = round(20 / 1.95) = 10, so n_ath=20 actually
        #   yields 20/10 = 2.00 -- it cleared the bar and the test failed for
        #   a reason that was nothing to do with the code. 39/20 is 1.95
        #   exactly, and 39 is still under the supermajority floor.
        links, rejected = self._decide("Small & Wales", "Small & Wales (R.I.)",
                                       39, 0.62, 1.95)
        self.assertEqual(links, [])
        msg = rejected[0][11]
        self.assertIn("1.950", msg, f"three decimals, got {msg!r}")
        self.assertNotIn("margin 2.0x < 2.0x", msg)


if __name__ == "__main__":
    unittest.main()
