# The recruiting gate is the fifty states, and it is NOT pool_resolve.inScope.
import os
import sys
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "racecast"))
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "engine"))

from us_state import isFiftyState, stateCode, fiftyStateList   # noqa: E402


class ItKeepsTheStates(unittest.TestCase):
    def test_codes_and_case(self):
        for s in ("CA", "ca", "Ca", "NY", "WY", "AK", "HI"):
            self.assertTrue(isFiftyState(s), s)

    # The state column really holds 18,938 spelled-out names; testing only
    # the two-letter codes threw the American ones away once already.
    def test_spelled_out_names(self):
        self.assertEqual(stateCode("Texas"), "TX")
        self.assertEqual(stateCode("north carolina"), "NC")
        self.assertEqual(stateCode("District of Columbia"), "DC")

    def test_all_fifty_plus_dc(self):
        self.assertEqual(len(fiftyStateList()), 51)


class ItRefusesEverythingElse(unittest.TestCase):
    def test_foreign(self):
        for s in ("ON", "BC", "AB", "New Zealand", "Auckland", "England"):
            self.assertFalse(isFiftyState(s), s)

    # ★ THE WHOLE POINT OF A SEPARATE SET. inScope counts these as American
    #   rows, correctly, for the boards. Recruiting asked a narrower
    #   question and must not get inScope's answer by accident.
    def test_territories_and_military(self):
        for s in ("PR", "GU", "VI", "AS", "MP", "AE", "AP", "AA"):
            self.assertFalse(isFiftyState(s), s)

    def test_unknown_is_not_american_here(self):
        for s in (None, "", "   ", "ZZ", "99"):
            self.assertFalse(isFiftyState(s), repr(s))


class ItDoesNotMoveTheBoards(unittest.TestCase):
    """inScope must keep answering the way the boards depend on."""

    def test_inscope_is_unchanged(self):
        from pool_resolve import inScope
        self.assertTrue(inScope(None))       # unprovable, so kept
        self.assertTrue(inScope("PR"))
        self.assertTrue(inScope("AE"))
        self.assertFalse(inScope("Auckland"))


if __name__ == "__main__":
    unittest.main()


class ForeignRowsOffTheBoards(unittest.TestCase):
    """Owner, 2026-09-26: national teams at US meets, and a Japanese
    regional whose prefecture numbers read as US codes, were on the boards."""

    def test_national_teams_are_out_whatever_the_meet_state(self):
        from pool_resolve import inScope
        self.assertFalse(inScope("MD", "Spain"))
        self.assertFalse(inScope("WA", "Great Britain & N.I."))
        self.assertFalse(inScope("CAROLINA", "Puerto Rico"))
        self.assertFalse(inScope("MA", "Kenya (KEN)"))

    def test_us_schools_named_for_countries_stay(self):
        from pool_resolve import inScope
        for school in ("Poland", "Norway", "Georgia", "Jamaica", "Lebanon",
                       "Spain Park", "Colegio San Ignacio"):
            self.assertTrue(inScope("ME", school), school)

    def test_numeric_region_codes_are_foreign(self):
        from pool_resolve import inScope
        self.assertFalse(inScope("28"))
        self.assertFalse(inScope("46", "Katsura Prefectural-Kyoto"))
