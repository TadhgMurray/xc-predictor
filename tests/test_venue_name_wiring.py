# Project: xc-predictor / tests
# File:    test_venue_name_wiring.py
# Purpose: a scraped venue name reaches the table the engine reads.
#
#   python tests/test_venue_name_wiring.py
#
# ⚠⚠ WHY (owner, 2026-09-18): "did we ever fix the scraper not getting venue
#    name (also when it does get the venue name does it update it for
#    everything there)."
#
#    The answer was half-yes, and the half that was missing is the kind that
#    hides: saveMeetTFMeta DOES capture venue_name -- into meets_tf_meta. But
#    meets_tf, which is what the engine labels courses with, is filled only by
#    database.backfillMeetsTFVenueNames, and that was called by exactly ONE
#    MANUAL SCRIPT. So every scrape left the names in meets_tf_meta and the
#    engine went on printing a location id as a venue. That function's own
#    comment records the same failure happening to it once before: "AND IT WAS
#    NEVER CALLED. Written, committed, wired to nothing."
import ast
import io
import os
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(rel):
    with io.open(os.path.join(_ROOT, *rel.split("/")), encoding="utf-8") as fh:
        return fh.read()


class TheScraperCapturesIt(unittest.TestCase):

    def test_save_meet_tf_meta_writes_and_updates_venue_name(self):
        db = read("scripts/database.py")
        body = db[db.index("def saveMeetTFMeta("):]
        body = body[:body.index("\ndef ")]
        self.assertIn("venue_name", body)
        # ON CONFLICT must refresh it, or a meet first seen without a name
        # keeps the blank for ever
        self.assertIn("venue_name     = EXCLUDED.venue_name", body)


class BothLaunchersPropagateItWithoutBeingAsked(unittest.TestCase):

    LAUNCHERS = ("scripts/launcher.py", "tfrrs/driver/launch_tfrrs.py")

    def test_each_one_fills_venue_names_at_the_end(self):
        for rel in self.LAUNCHERS:
            src = read(rel)
            self.assertIn("def _fillVenueNames(", src, rel)
            self.assertIn("backfillMeetsTFVenueNames(conn", src, rel)

    # ! PASS 1 ONLY. Pass 1 joins each meet to its OWN meta row -- keyed,
    #   chunked, cheap, and exactly where a just-scraped name is. Passes 2 and
    #   3 aggregate all of meets_tf and stay an occasional job.
    def test_a_scrape_runs_only_the_cheap_pass(self):
        for rel in self.LAUNCHERS:
            src = read(rel)
            self.assertIn("passes=(1,)", src, rel)

    # ⚠ NEVER FATAL. A scrape that worked must not report failure because a
    #   tidy-up did not.
    def test_a_failure_to_fill_does_not_fail_the_scrape(self):
        for rel in self.LAUNCHERS:
            src = read(rel)
            i = src.index("def _fillVenueNames(")
            # ! EITHER KIND OF def. _fillVenueNames is the last plain def
            #   before `async def main()`, so searching for "\ndef " alone
            #   runs off the end of the file.
            ends = [src.index(k, i + 10) for k in ("\ndef ", "\nasync def ")
                    if k in src[i + 10:]]
            body = src[i:min(ends)] if ends else src[i:]
            self.assertIn("except Exception", body, rel)
            self.assertIn("skipped", body, rel)

    # ! AND IT POINTS AT THE FULL JOB, so nobody concludes the cheap pass is
    #   all there is.
    def test_it_names_the_script_for_the_expensive_passes(self):
        for rel in self.LAUNCHERS:
            self.assertIn("scripts/backfill_tf_venues.py", read(rel), rel)


class ThePassesDoWhatTheOwnerAsked(unittest.TestCase):
    """The second half of the question: does one name spread to everything at
    that venue? Yes -- by location_id, then by coordinates."""

    def setUp(self):
        db = read("scripts/database.py")
        self.body = db[db.index("def backfillMeetsTFVenueNames("):
                       db.index("def saveMeetTF(")]

    def test_pass_two_spreads_by_location_id(self):
        self.assertIn("src.location_id = t.location_id", self.body)

    def test_pass_three_spreads_by_coordinates(self):
        self.assertIn("FROM   vn_gps src", self.body)

    # ⚠⚠ ALL THREE ONLY FILL NULLS. None corrects a name already there, right
    #    or wrong -- worth knowing before trusting a venue label.
    def test_none_of_them_overwrites_an_existing_name(self):
        # ! COUNT THE SQL, NOT THE PROSE. The docstring says "all three only
        #   fill NULLs", which is itself an occurrence -- the first version of
        #   this test counted 4 and failed on its own explanation.
        sql = self.body.split('"""', 2)[2]
        self.assertEqual(sql.count("t.venue_name IS NULL"), 3)

    def test_the_passes_are_selectable(self):
        self.assertIn("passes=(1, 2, 3)", self.body)
        self.assertIn("if 1 in passes:", self.body)
        self.assertIn("if 2 not in passes and 3 not in passes:", self.body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
