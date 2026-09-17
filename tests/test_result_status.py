# Project: xc-predictor / tests
# File:    test_result_status.py
# Purpose: one vocabulary and one rule for "why does this row have no time".
#          No database.
#
#   python -m pytest -q tests/test_result_status.py
#
# ⚠⚠ THE FIVE SPELLINGS this replaces (owner, 2026-09-17: "capture the real
#    DNF/DNS flags, not just the sentinel times"):
#
#      time_seconds < 999999              export_for_analysis
#      NULL time or place 0               resolve_fanout, tfrrs leg
#      999999 time or place 0             resolve_fanout, anet leg
#      time is None or > 100000           census_no_distance
#      time_seconds < DNF_SENTINEL        reference_fit
#
#    Two of them already disagreed (999999 vs 100000), and every one is a
#    guess at something the source states outright.
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))

import result_status as R                                       # noqa: E402


class TheVocabulary(unittest.TestCase):

    def test_the_tokens_the_feeds_actually_write(self):
        for t in ("DNF", "DNS", "DQ", "SCR", "WD", "NT", "NH", "ND", "NM",
                  "DNP", "FS"):
            self.assertEqual(R.normalise(t), R.ALIASES.get(t, t), t)

    def test_case_dots_and_dashes_do_not_matter(self):
        for t in ("dnf", " DNF ", "D.N.F.", "d-n-f"):
            self.assertEqual(R.normalise(t), "DNF", t)

    def test_dsq_is_stored_as_dq(self):
        self.assertEqual(R.normalise("DSQ"), "DQ")

    def test_a_time_is_never_a_status(self):
        """The caller hands this every result cell without filtering."""
        for t in ("21:26.1", "5:44.9", "9.88", "", "  ", None, "1", "Grace"):
            self.assertIsNone(R.normalise(t), repr(t))


class TheFiveAnswers(unittest.TestCase):

    def test_a_dns_is_not_a_dnf(self):
        """★ THE WHOLE POINT. Someone who never started is no evidence about
        their fitness; someone who dropped at 4k is. Both arrived as the same
        sentinel."""
        self.assertEqual(R.kind("DNF"), "dnf")
        self.assertEqual(R.kind("DNS"), "dns")
        self.assertNotEqual(R.kind("DNF"), R.kind("DNS"))
        self.assertTrue(R.startedRace("DNF"))
        self.assertFalse(R.startedRace("DNS"))

    def test_a_dq_is_a_result_that_happened(self):
        """Its TIME is real and its PLACE is not, so a rating may use it and
        a scoring pass may not."""
        self.assertEqual(R.kind("DQ"), "dq")
        self.assertTrue(R.isFinish("DQ"))
        self.assertTrue(R.isRated("DQ"))

    def test_scratched_and_withdrawn_never_started(self):
        for t in ("SCR", "WD"):
            self.assertEqual(R.kind(t), "dns", t)
            self.assertFalse(R.startedRace(t), t)

    def test_a_field_event_no_mark_started_but_recorded_nothing(self):
        for t in ("NH", "ND", "NM", "NT", "DNP"):
            self.assertEqual(R.kind(t), "nomark", t)
            self.assertTrue(R.startedRace(t), t)
            self.assertFalse(R.isFinish(t), t)


class TheSentinelIsTheLastResort(unittest.TestCase):
    """Rows stored before the column existed have only the magic number."""

    def test_a_real_time_with_no_status_is_a_finish(self):
        self.assertEqual(R.kind(None, 1286.1), "ok")
        self.assertTrue(R.isFinish(None, 1286.1))

    def test_the_sentinel_with_no_status_is_a_non_finish(self):
        self.assertEqual(R.kind(None, 999999), "dns")
        self.assertFalse(R.isFinish(None, 999999))

    def test_a_null_time_with_no_status_is_a_non_finish(self):
        self.assertEqual(R.kind(None, None), "dns")

    def test_the_status_beats_the_sentinel_when_both_are_there(self):
        """A DNF row still carries 999999; the letters are what decide."""
        self.assertEqual(R.kind("DNF", 999999), "dnf")
        self.assertTrue(R.startedRace("DNF", 999999))

    def test_the_two_thresholds_that_disagreed(self):
        """census_no_distance used 100000 and everything else 999999. A
        100,000-second race is 27 hours, so they only ever disagreed about
        rows that do not exist -- but they DID disagree."""
        self.assertEqual(R.SENTINEL, 999999)
        self.assertEqual(R.kind(None, 100001), "ok")     # not a non-finish
        self.assertEqual(R.kind(None, 999999), "dns")

    def test_rubbish_in_the_time_is_not_a_finish(self):
        self.assertEqual(R.kind(None, "banana"), "dns")


class AnetsManyFieldNames(unittest.TestCase):

    def test_the_first_real_status_among_the_candidates_wins(self):
        """anet's JSON puts it in whichever of Result / ShortCode / Status /
        ResultText it feels like that season."""
        self.assertEqual(R.fromFields(None, "21:26.1", "DNF", None), "DNF")
        self.assertEqual(R.fromFields("DSQ", "DNF"), "DQ")
        self.assertIsNone(R.fromFields(None, "", "16:12.4"))


class OneSpellingInSql(unittest.TestCase):

    def test_the_fragment_names_the_sentinel_once(self):
        sql = R.finishedSql("r")
        self.assertIn("r.status", sql)
        self.assertIn(str(R.SENTINEL), sql)
        self.assertIn("r.time_seconds IS NOT NULL", sql)
        self.assertNotIn("100000", sql)

    def test_it_takes_the_callers_alias(self):
        self.assertIn("x.status", R.finishedSql("x"))


if __name__ == "__main__":
    unittest.main(verbosity=2)


# ===================================================================== #
#  THE WIRING -- four writers, one vocabulary                            #
# ===================================================================== #

def _read(*parts):
    import io
    with io.open(os.path.join(_ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


class EveryFeedCapturesIt(unittest.TestCase):
    """⚠ BEFORE THIS, ONE WRITER OF FOUR DID. anet XC had a status column
    (issue 59); anet TF kept the letters in `mark`, the same column a FIELD
    event's real mark lives in; and both tfrrs feeds threw them away."""

    def test_the_vocabulary_is_defined_once(self):
        db = _read("scripts", "database.py")
        self.assertNotIn('_STATUS_VOCAB = {"DNF"', db,
                         "database.py must not keep its own copy")
        self.assertIn("from result_status import fromFields", db)

    def test_anet_track_has_its_own_column_now(self):
        db = _read("scripts", "database.py")
        i = db.index("def saveResultsTFBulk(")
        body = db[i:db.index("# _cleanJson", i)]
        self.assertIn('_ensureResultsStatus(conn, "results_tf")', body)
        self.assertIn("status = _statusOf(result)", body)
        self.assertIn("source, id_system, person_id, status)", body)

    def test_the_field_event_mark_is_left_alone(self):
        """_statusOf returns None for a real mark, so an NH is recorded as a
        no-height and a genuine mark is untouched."""
        self.assertIsNone(R.normalise("6-04.00"))
        self.assertEqual(R.normalise("NH"), "NH")

    def test_tfrrs_xc_carries_it_from_cell_to_column(self):
        pxc = _read("tfrrs", "parser", "parse_xc.py")
        self.assertIn('status = _statusText(_textAt("time"))', pxc)
        self.assertIn('"status":            status,', pxc)
        save = _read("tfrrs", "scraper", "save_tfrrs.py")
        self.assertIn('"status":        parsed.get("status"),', save)
        self.assertIn('row.get("status"),', save)
        self.assertIn("scraped_at, splits_json, team_slug, status", save)

    def test_a_rescrape_never_erases_the_letters(self):
        """COALESCE, like school: a re-scrape that comes back without them
        must not wipe what we already have."""
        db = _read("scripts", "database.py")
        self.assertIn("status        = COALESCE(EXCLUDED.status, results.status)", db)
        self.assertIn("status        = COALESCE(EXCLUDED.status, results_tf.status)", db)
        save = _read("tfrrs", "scraper", "save_tfrrs.py")
        self.assertIn("COALESCE(EXCLUDED.status, results.status)", save)

    def test_the_column_is_a_migration_not_a_hope(self):
        """Two writers depend on it, and a saver that creates it lazily is
        UndefinedColumn mid-scrape for whichever one runs first -- the shape
        results_tf.team_slug produced on the server."""
        db = _read("scripts", "database.py")
        self.assertIn("def _migrateResultsAddStatus(cursor):", db)
        self.assertIn("_migrateResultsAddStatus(cursor)", db)
        i = db.index("def _migrateResultsAddStatus(")
        body = db[i:i + 600]
        self.assertIn('for table in ("results", "results_tf")', body)
        # and the tfrrs savers still ensure it themselves
        save = _read("tfrrs", "scraper", "save_tfrrs.py")
        self.assertEqual(save.count("_ensureResultsStatus(conn,"), 2)
