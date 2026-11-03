"""unit_names.notAConference: meets and region numerals are not conferences
(owner's board, 2026-11-03)."""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "racecast"))
from unit_names import notAConference  # noqa: E402


def test_the_owners_list_is_not_conferences():
    for v in ("BORDER WAR", "JACK SAINT CLAIR MEMORIAL", "XIV", "XVI", "XIX", "CCS XC",
              "COLLEGIATE", "HEART MEN'S AND WOMEN'S", "Region XIV", "", None):
        assert notAConference(v), v


def test_real_conferences_pass():
    for v in ("ACC", "SEC", "BIG TEN", "BIG 12", "A-10", "MTN WEST", "NESCAC", "LIBERTY",
              "SUMMIT", "USA", "EAST", "GNAC", "NE10", "NORTHEAST-10", "EMPIRE 8",
              "UNITED EAST", "PACWEST", "MCCAA WESTERN", "HEART", "IVY LEAGUE"):
        assert not notAConference(v), v
