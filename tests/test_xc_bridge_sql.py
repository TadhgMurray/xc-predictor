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
        # ! THE ACTUAL SENTENCE, not the bare word -- "UNKNOWN" appears in
        #   half a dozen places and would pass whatever the report said.
        self.assertIn("has no is_indoor here", self.src)
        self.assertIn("then an upper ", self.src)


class TheJoinCannotExplode(unittest.TestCase):

    def setUp(self):
        self.src = read()

    def test_the_day_tables_are_built_once_and_reused(self):
        """! ONE BUILD, MANY WINDOWS. The day tables do not depend on the
        window, so rebuilding them per window would multiply the only
        expensive part of the run by the number of windows asked for."""
        self.assertIn("def build(", self.src)
        self.assertIn("def measure(cur, window)", self.src)
        i = self.src.index("def measure(cur, window)")
        body = self.src[i:self.src.index("\ndef ", i + 10)]
        self.assertNotIn("CREATE TEMP TABLE", body)

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


class ItCannotHang(unittest.TestCase):
    """⚠⚠ owner, 2026-09-20: "I have a feeling the bridge is gonna hang can you
       fix it". It would have.

       Reducing each side to DISTINCT (person, day) did almost nothing to the
       XC side -- an athlete races cross country at most once a day, so
       distinct person-days is very nearly the row count -- and the join then
       hash-matched an athlete's ENTIRE CAREER against itself before the range
       predicate narrowed anything. Ten years is ~150 XC days against ~200
       track days: thirty thousand pairs per athlete.

       The shape that works narrows the PEOPLE first and then asks each XC day
       a yes/no question through an index. Verified on a live Postgres: the
       plan is an Index Cond on (person_id, d) with one probe per XC day, not
       a join.
    """

    def setUp(self):
        self.src = read()

    def test_the_range_join_is_gone(self):
        """★ THE EXPLOSION ITSELF. A range predicate in a JOIN ... ON over
        person_id is the thing that cannot be allowed back."""
        self.assertNotIn("FROM   xc JOIN tf", self.src)
        self.assertNotIn("tf.d BETWEEN xc.d", self.src)

    def test_each_xc_day_is_an_index_probe(self):
        self.assertIn("EXISTS (SELECT 1 FROM b_tf t", self.src)
        self.assertIn("CREATE INDEX ON b_tf (person_id, d)", self.src)
        self.assertIn("CREATE INDEX ON b_xc (person_id, d)", self.src)

    def test_the_people_are_narrowed_before_anything_expensive(self):
        """★ MOST CROSS-COUNTRY RUNNERS ARE NOT IN results_tf AT ALL, and they
        cannot carry the anchor by definition. They leave first, and that step
        needs no date cast, which also makes it the cheapest."""
        i = self.src.index("CREATE TEMP TABLE b_people")
        self.assertLess(i, self.src.index("CREATE TEMP TABLE b_xc"))
        self.assertIn("INTERSECT", self.src)

    def test_a_statement_cannot_run_forever(self):
        """! IT GIVES UP LOUDLY rather than being discovered tomorrow."""
        self.assertIn("SET statement_timeout", self.src)
        self.assertIn("DEFAULT_TIMEOUT_S", self.src)
        self.assertIn('"--timeout"', self.src)

    def test_the_sample_escapes_its_percent_sign(self):
        """⚠⚠ psycopg2 OWNS THE PERCENT SIGN. Every execute passes a params
        dict, so the driver interpolates the string first and a bare `% 100`
        is a broken placeholder -- "dict is not a sequence". Doubling it is
        how a literal modulo survives into the SQL, and no amount of reading
        the query finds this: it took running --sample against a real
        Postgres."""
        self.assertIn("%% 100", self.src)
        self.assertNotIn("(abs(hashtext(person_id::text)) % 100)", self.src)

    def test_the_sample_is_on_people_not_rows(self):
        """! SAMPLING ROWS WOULD BIAS EVERY SHARE toward athletes who race a
        lot; sampling PEOPLE does not, which is what makes --sample 5 a valid
        answer rather than a hint."""
        self.assertIn("hashtext(person_id::text)", self.src)
        self.assertIn("percent of ATHLETES", self.src)


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
        self.assertIn("VOTE-WEIGHTED", self.src)
        self.assertIn("share of ROWS", self.src)

    def test_outdoor_pairs_are_not_counted_as_evidence_about_surface(self):
        """⚠ AN XC-TO-OUTDOOR PAIR SPANS FALL TO SPRING, which is fitness
        plus surface inseparably -- the reason mu is a definition. The report
        must say so beside the number, or the headline overstates the case."""
        self.assertIn("mu is a definition", self.src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
