# Project: xc-predictor / tests
# File:    test_tf_venue_backfill.py
# Purpose: every anet TRACK meet gets its venue's NAME, without a scrape.
#
#   python -m pytest -q tests/test_tf_venue_backfill.py
#
# ★ THE SYMPTOM (owner, 2026-09-17): the engine labels a LOCATION ID as a
#   venue, because meets_tf.venue_name is NULL and the id is the only other
#   thing on the row.
#
# ⚠ AND THE FIX WAS ALREADY IN THE REPO, CALLED BY NOTHING.
#   database.backfillMeetsTFVenueNames was written, committed and wired to
#   nothing, so the names sat in meets_tf_meta while the engine printed ids.
#   school_team_link spent a day in exactly the same state. A function nobody
#   calls is not a fix, so this file pins the WIRING as hard as the SQL.
import io
import os
import re
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(*parts):
    with io.open(os.path.join(_ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


def _backfillBody():
    src = _read("scripts", "database.py")
    i = src.index("def backfillMeetsTFVenueNames(")
    return src[i:src.index("\ndef saveMeetTF(", i)]


class ThreePasses(unittest.TestCase):

    def test_the_meets_own_meta_row_first(self):
        self.assertIn("FROM   meets_tf_meta m", _backfillBody())

    def test_then_another_meet_at_the_same_location(self):
        """★ THE OWNER'S ASK: "backfills any meets where that venue currently
        has no name but id and such match". Two meets at location 8891 are at
        the same venue whether or not both of them said so."""
        body = _backfillBody()
        self.assertIn("src.location_id = t.location_id", body)
        self.assertIn("GROUP  BY location_id", body)

    def test_then_the_same_coordinates_for_rows_with_no_location_id(self):
        body = _backfillBody()
        self.assertIn("round(t.gps_lat::numeric, 5)", body)
        self.assertIn("round(t.gps_long::numeric, 5)", body)

    def test_the_passes_run_in_that_order(self):
        body = _backfillBody()
        self.assertLess(body.index("meets_tf_meta"), body.index("src.location_id"))
        self.assertLess(body.index("src.location_id"), body.index("round(t.gps_lat"))


class NeverOverwrites(unittest.TestCase):
    """A meet knows its own venue better than its neighbours do."""

    def test_every_pass_only_ever_fills_a_null(self):
        body = _backfillBody()
        updates = [m.start() for m in re.finditer(r"UPDATE meets_tf t SET", body)]
        self.assertEqual(len(updates), 3)
        for i, start in enumerate(updates):
            end = updates[i + 1] if i + 1 < len(updates) else len(body)
            self.assertIn("venue_name IS NULL", body[start:end],
                          f"pass {i + 1} can overwrite a stated name")

    def test_the_source_name_is_modal_so_reruns_do_not_churn(self):
        body = _backfillBody()
        self.assertEqual(body.count("mode() WITHIN GROUP"), 2)


class TheWiring(unittest.TestCase):
    """The half that was missing for weeks."""

    def test_there_is_a_command(self):
        self.assertTrue(os.path.exists(
            os.path.join(_ROOT, "scripts", "backfill_tf_venues.py")))

    def test_it_is_a_dry_run_by_default(self):
        src = _read("scripts", "backfill_tf_venues.py")
        self.assertIn('ap.add_argument("--write"', src)
        self.assertIn("DRY RUN", src)
        self.assertIn("if not a.write:", src)
        self.assertLess(src.index("if not a.write:"),
                        src.index("backfillMeetsTFVenueNames(conn)"))

    def test_the_pipeline_runs_it_before_anything_labels_a_venue(self):
        sh = _read("deploy", "run_pipeline.sh")
        self.assertIn("scripts/backfill_tf_venues.py --write", sh)
        self.assertLess(sh.index("02b_tf_venues"), sh.index("10_rankings_prepare"))

    def test_it_reports_what_is_still_unnamed(self):
        """A backfill that says only how many it filled cannot tell you it is
        finished."""
        self.assertIn("still have none", _backfillBody())


if __name__ == "__main__":
    unittest.main(verbosity=2)
