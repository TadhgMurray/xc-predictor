# Project: xc-predictor / tests
# File:    test_xc_bridge_sql.py
# Purpose: the XC/track bridge diagnostic's SQL, pinned where it broke.
#
# ⚠⚠ IT BROKE ON THE OWNER'S FIRST RUN (2026-09-20):
#
#       psycopg2.errors.DatatypeMismatch: COALESCE types integer and boolean
#       cannot be matched
#       LINE 8: ... COALESCE(m.is_indoor, false) AS ...
#
#    meets_tf.is_indoor is an INTEGER. Every other reader in this repo already
#    knew -- venue_geometry_overrides writes {"is_indoor": 1},
#    speed_ratings_db.loadCourseGeometry does int(ind) -- and the diagnostic
#    assumed a boolean because the word reads like one.
#
#    The fix casts to ::int FIRST, so the expression is correct whether the
#    column is an integer or a boolean and survives a schema that changes
#    underneath it. These tests exist so the assumption cannot come back.
#
# ⚠ AND A SECOND BUG THE TRACEBACK HID. The first version joined results to
#   results_tf on person_id over a date range and counted the PAIRS: an
#   athlete with fifty XC rows and fifty track rows inside the window is two
#   and a half thousand pairs, over a 225M x 121M join. It would have run for
#   hours after it stopped erroring. Each side is now reduced to distinct race
#   DAYS before the join.
#
#   python -m unittest tests.test_xc_bridge_sql
import io
import os
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC = os.path.join(_ROOT, "engine", "diag_xc_track_bridge.py")


def read():
    with io.open(_SRC, encoding="utf-8") as fh:
        return fh.read()


class TheColumnTypes(unittest.TestCase):

    def setUp(self):
        self.src = read()

    def test_is_indoor_is_cast_before_it_is_defaulted(self):
        """★ ::int FIRST. `COALESCE(m.is_indoor, false)` is the exact
        expression that raised DatatypeMismatch on the live database."""
        self.assertIn("COALESCE(m.is_indoor::int, 0) <> 0", self.src)
        self.assertNotIn("COALESCE(m.is_indoor, false)", self.src)

    def test_it_agrees_with_every_other_reader_in_the_repo(self):
        """! ONE ANSWER ABOUT ONE COLUMN. venue_geometry_overrides gates on
        the integer 1; loadCourseGeometry does int(ind). A diagnostic that
        disagreed about the type is a diagnostic that cannot be trusted about
        the numbers either."""
        with io.open(os.path.join(_ROOT, "engine", "venue_geometry_overrides.py"),
                     encoding="utf-8") as fh:
            self.assertIn('"is_indoor": 1', fh.read())

    def test_the_absent_column_degrades_rather_than_raises(self):
        """! A DIAGNOSTIC MUST NOT BE THE THING THAT BREAKS. Without the
        column the split is unknown, and it SAYS so rather than reporting
        zero indoor pairs as if that were a finding."""
        self.assertIn("NULL::boolean", self.src)
        self.assertIn("information_schema.columns", self.src)
        self.assertIn("to_regclass", self.src)
        self.assertIn("split is UNKNOWN", self.src)


class TheJoinCannotExplode(unittest.TestCase):

    def setUp(self):
        self.src = read()

    def test_each_side_is_reduced_to_distinct_race_days_first(self):
        """⚠ THE PERFORMANCE BUG THE TYPE ERROR WAS HIDING. Without DISTINCT
        the pair count is quadratic in an athlete's races."""
        # ! THE TWO CTEs THAT FEED THE JOIN, NAMED. Counting DISTINCTs over a
        #   window caught the bridged_day/bridged_person CTEs too, which is an
        #   assertion about the wrong thing.
        for cte in ("xc AS MATERIALIZED", "tf AS MATERIALIZED"):
            i = self.src.index(cte)
            head = self.src[i:i + 200]
            self.assertIn("SELECT DISTINCT", head,
                          f"{cte} does not reduce to distinct race days")

    def test_the_date_filter_cannot_be_separated_from_the_cast(self):
        """⚠ date IS TEXT and `date::date` throws on anything that is not a
        real date. MATERIALIZED keeps the planner from evaluating the cast on
        rows the regex would have removed. Verified against a live Postgres
        with a junk 'unknown' date in the table."""
        self.assertIn("AS MATERIALIZED", self.src)
        self.assertIn("_DATE_OK", self.src)

    def test_the_first_run_is_bounded(self):
        """! A FLOOR ON THE YEARS, so the first run is not a research
        project. Lowering it is one flag."""
        self.assertIn("DEFAULT_SINCE", self.src)
        self.assertIn('"--since"', self.src)


class TheVerdictIsDecidedBeforeTheNumberIsSeen(unittest.TestCase):
    """★ THRESHOLDS WRITTEN DOWN, so the answer is not reinterpreted later by
    whoever is arguing for what they already wanted."""

    def setUp(self):
        self.src = read()

    def test_all_three_verdicts_exist(self):
        for word in ("FAT ENOUGH", "THIN", "ABSENT"):
            self.assertIn(word, self.src)

    def test_the_share_is_of_rows_not_athletes(self):
        """★ THE GAUGE IS VOTE-WEIGHTED: an anchor reaching a thousand
        athletes with one race each carries far less than one reaching a
        hundred who race twenty times."""
        self.assertIn("xc_rows", self.src)
        self.assertIn("ROWS, NOT ATHLETES", self.src)

    def test_outdoor_pairs_are_not_counted_as_evidence_about_surface(self):
        """⚠ AN XC-TO-OUTDOOR PAIR SPANS FALL TO SPRING, which is fitness
        plus surface inseparably -- the reason mu is a definition. The report
        must say so beside the number, or the headline overstates the case."""
        self.assertIn("mu is a definition", self.src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
