# Project: xc-predictor / tests
# File:    test_school_logo_host.py
# Purpose: plausibleHost -- which addresses are allowed to give a crest.
#
#   python -m pytest -q tests/test_school_logo_host.py
#   python tests/test_school_logo_host.py
#
# ★ THE TWO FAILURES THIS SITS BETWEEN, and they pull opposite ways.
#
#   2026-09-17, the owner: "it was giving random ass pictures that are not on
#   anet. Lawrence hs got the world athletics picture?" -- a wrong
#   school_website row returns that domain's logo, faithfully. plausibleHost
#   was written to refuse those.
#
#   2026-09-17, the owner again: "Penn state still has no logo at all." The
#   same test threw away most of the COLLEGE corpus, because a college
#   athletics site is branded by its MASCOT and not by its school:
#   gopsusports.com, goducks.com, rolltide.com, guhoyas.com, gohuskies.com,
#   hawkeyesports.com, und.com, cuse.com, byucougars.com. Not one carries a
#   name token, none is .edu, none reads as "schooly".
#
# ★ WHAT SEPARATES THEM IS THE ADDRESS'S PROVENANCE, NOT ITS HOSTNAME. The
#   Lawrence row was a GUESS (Wikidata, or a search). Penn State's row was
#   written by anet_teams.storeAddress from anet's WebsiteSport, keyed on the
#   TEAM ID -- the same construction that already exempts the anet mascot
#   itself. So the name test applies to guesses and the blocklist applies to
#   everything.
import io
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "racecast")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import scrape_school_logos as S                                    # noqa: E402


# the real athletics domains of real programmes in the corpus
COLLEGES = [
    ("Penn State", "https://gopsusports.com/"),
    ("Oregon", "https://goducks.com/"),
    ("Alabama", "https://rolltide.com/"),
    ("Notre Dame", "https://und.com/"),
    ("Syracuse", "https://cuse.com/"),
    ("Washington", "https://gohuskies.com/"),
    ("Georgetown", "https://guhoyas.com/"),
    ("Iowa", "https://hawkeyesports.com/"),
    ("BYU", "https://byucougars.com/"),
]


class Guessed(unittest.TestCase):
    """An address nobody vouched for still has to earn the crest."""

    def test_a_governing_body_is_never_a_school(self):
        self.assertFalse(
            S.plausibleHost("Lawrence", "https://worldathletics.org/logo.png",
                            "school"))

    def test_an_aggregator_is_never_a_school(self):
        for host in ("https://www.milesplit.com/", "https://maxpreps.com/",
                     "https://www.tfrrs.org/"):
            self.assertFalse(S.plausibleHost("Lawrence", host, "school"), host)

    def test_the_school_s_own_name_earns_it(self):
        self.assertTrue(
            S.plausibleHost("Stanford", "https://gostanford.com/", "school"))

    def test_a_dot_edu_earns_it(self):
        self.assertTrue(S.plausibleHost("Penn State", "https://www.psu.edu/",
                                        "school"))

    def test_an_anet_crest_is_exempt_whatever_the_host(self):
        self.assertTrue(
            S.plausibleHost("Anything", "https://lh3.googleusercontent.com/x",
                            "anet"))


class DistrictCodes(unittest.TestCase):
    """⚠ "sd" WAS A BARE SUBSTRING of a run-together hostname, so the guard
    the owner asked for admitted newsdaily.com, wisdomtree.com,
    sportsdesk.net and kidsdirect.org. A district code is a WORD."""

    def test_a_two_letter_code_inside_a_longer_word_is_not_a_district(self):
        for url in ("https://newsdaily.com/", "https://wisdomtree.com/",
                    "https://sportsdesk.net/", "https://kidsdirect.org/",
                    "https://goldsmiths.com/"):
            self.assertFalse(S.plausibleHost("Lawrence", url, "school"), url)

    def test_the_district_hosts_that_really_exist_still_pass(self):
        for url in ("https://usd497.org/", "https://sd44.bc.ca/",
                    "https://www.cusd.org/", "https://north-isd.net/",
                    "https://www.ccsd.net/"):
            self.assertTrue(S.plausibleHost("Anytown", url, "school"), url)

    def test_the_long_words_are_still_substrings(self):
        for url in ("https://mydistrict.org/", "https://someacademy.com/",
                    "https://lawrenceschools.org/"):
            self.assertTrue(S.plausibleHost("Anytown", url, "school"), url)


class Provenance(unittest.TestCase):
    """The Penn State half: an address anet resolved by team id."""

    def test_every_one_of_these_is_refused_as_a_guess(self):
        """The measurement. Without provenance the name test refuses most of
        the college corpus -- which is the bug, stated as a number."""
        refused = [(sc, url) for sc, url in COLLEGES
                   if not S.plausibleHost(sc, url, "school")]
        self.assertEqual(len(refused), len(COLLEGES),
                         f"these now pass the NAME test on their own: {refused}")

    def test_and_every_one_is_allowed_when_anet_gave_the_address(self):
        for sc, url in COLLEGES:
            self.assertTrue(S.plausibleHost(sc, url, "school", source="anet"),
                            f"{sc} {url}")

    def test_a_hand_set_address_is_trusted_too(self):
        self.assertTrue(S.plausibleHost("Penn State", "https://gopsusports.com/",
                                        "school", source="manual"))

    def test_provenance_does_not_defeat_the_blocklist(self):
        """anet's WebsiteSport pointing at a vendor or a governing body is
        still nobody's crest -- trust the ADDRESS, not the host."""
        for url in ("https://worldathletics.org/", "https://www.wordpress.com/",
                    "https://x.com/pennstate", "https://www.milesplit.com/"):
            self.assertFalse(
                S.plausibleHost("Penn State", url, "school", source="anet"), url)

    def test_an_unknown_source_is_still_a_guess(self):
        for source in (None, "", "wikidata", "search", "directory"):
            self.assertFalse(
                S.plausibleHost("Penn State", "https://gopsusports.com/",
                                "school", source=source), repr(source))


def _scraperSource():
    with io.open(os.path.join(_ROOT, "scripts", "scrape_school_logos.py"),
                 encoding="utf-8") as fh:
        return fh.read()


class Wiring(unittest.TestCase):
    """The provenance has to actually reach the check."""

    def test_the_queue_selects_the_source(self):
        src = _scraperSource()
        self.assertIn("l.status, w.source", src)
        self.assertIn('"etag", "modified", "status", "source")', src)

    def test_the_worker_passes_it_and_tolerates_a_short_row(self):
        src = _scraperSource()
        self.assertIn("source = row[9] if len(row) > 9 else None", src)
        self.assertIn("school=(None if override_url else school),\n"
                      "                                    source=source)", src)

    def test_fetchLogo_hands_it_to_the_check(self):
        src = _scraperSource()
        self.assertIn("plausibleHost(school, url, kind, source)", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
