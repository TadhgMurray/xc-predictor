# Project: xc-predictor / tests
# File:    test_ddl_columns.py
# Purpose: every column the DDL declares exists on every database, and every
#          column the code INSERTs is reported when it does not. No database.
#
#   python -m pytest -q tests/test_ddl_columns.py
#
# ⚠⚠ "CREATE TABLE IF NOT EXISTS" NEVER ADDS A COLUMN. A table made by last
#    season's code keeps last season's shape for ever, and the first INSERT
#    naming a newer column dies with UndefinedColumn -- on the server,
#    mid-run. THREE TIMES IN ONE DAY (2026-09-17):
#
#      results_tf.team_slug   killed link_tfrrs_to_anet, and had silently
#                             degraded anet_teams' crest levels for weeks
#      results.status         would have killed whichever scraper ran first
#      meets_tf.venue_name    killed the venue backfill -- and saveMeetTF
#                             INSERTs it, so the anet TRACK SCRAPE too
#
#    The hand-written _migrate* functions are the bug: they are a thing to
#    remember, and they get forgotten.
import io
import os
import re
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _db():
    with io.open(os.path.join(_ROOT, "scripts", "database.py"),
                 encoding="utf-8") as fh:
        return fh.read()


_SRC = _db()
_NS = {"re": re}
for _name in ("_NOT_A_COLUMN", "_CREATE_RE", "_INSERT_RE"):
    _i = _SRC.index(f"{_name} = ")
    exec(compile(_SRC[_i:_SRC.index("\n\n\n", _i)], "ddl", "exec"), _NS)
for _fn, _end in (("def _ddlColumns(", "\ndef _relaxed("),
                  ("def _relaxed(", "\ndef ensureDdlColumns("),
                  ("def _insertColumns(", "\ndef auditInsertColumns(")):
    _i = _SRC.index(_fn)
    exec(compile(_SRC[_i:_SRC.index(_end, _i)], "ddl", "exec"), _NS)

ddlColumns = _NS["_ddlColumns"]
relaxed = _NS["_relaxed"]
insertColumns = _NS["_insertColumns"]
CREATE_RE = _NS["_CREATE_RE"]


def _tables(text):
    out = {}
    for m in CREATE_RE.finditer(text):
        out[m.group(1)] = ddlColumns(text[m.end():])
    return out


class ParsingTheRealDdl(unittest.TestCase):
    """Three parser bugs found here, each of which made the fix look wrong."""

    def test_every_create_table_is_found_not_just_the_first(self):
        """⚠ `\\((.*)` under re.S is GREEDY: the first CREATE TABLE swallowed
        the rest of the file, finditer returned ONE match, and six of
        _createCoreTables' seven tables were invisible."""
        i = _SRC.index("def _createCoreTables(")
        body = _SRC[i:_SRC.index("def _createTFTables(", i)]
        self.assertEqual(len(_tables(body)), 7)
        self.assertIn("results", _tables(body))

    def test_a_columns_documentation_does_not_eat_the_column(self):
        """⚠ This project documents a column on the lines ABOVE it, and that
        prose contains commas -- "(owner, 2026-09-16: ...)". Splitting on
        commas before stripping comments shredded one column into fragments
        of English: meets_tf came out with columns called 'AND', 'so', 'it'
        and 'for', and no venue_name."""
        cols = dict(ddlColumns("""
            div_id     BIGINT,
            -- ★ THE VENUE'S NAME (owner, 2026-09-16: "make sure tf venue
            --   names go in so we can not label our id as venue").
            venue_name TEXT,
            state      TEXT
        )"""))
        self.assertEqual(sorted(cols), ["div_id", "state", "venue_name"])
        self.assertEqual(cols["venue_name"], "TEXT")

    def test_no_core_table_parses_to_a_word_of_english(self):
        junk = {"and", "so", "it", "for", "the", "a", "is", "of", "that",
                "this", "to", "we", "our", "make", "sure"}
        found = []
        for text in (_SRC,):
            for table, cols in _tables(text).items():
                for name, _spec in cols:
                    if name.lower() in junk:
                        found.append(f"{table}.{name}")
        self.assertEqual(found, [])

    def test_a_bracketed_type_survives_the_comma_split(self):
        cols = dict(ddlColumns("a NUMERIC(4,1), b DECIMAL(10, 2), c TEXT)"))
        self.assertEqual(cols["a"], "NUMERIC(4,1)")
        self.assertEqual(cols["c"], "TEXT")

    def test_constraints_are_not_columns(self):
        cols = dict(ddlColumns("a INT, b TEXT, PRIMARY KEY (a, b))"))
        self.assertEqual(sorted(cols), ["a", "b"])


class TheAlterIsSafe(unittest.TestCase):

    def test_a_not_null_without_a_default_is_relaxed(self):
        """Postgres cannot add one to a table that already has rows."""
        self.assertEqual(relaxed("TEXT NOT NULL"), "TEXT")
        self.assertEqual(relaxed("INTEGER NOT NULL DEFAULT 0"),
                         "INTEGER NOT NULL DEFAULT 0")

    def test_a_primary_key_is_dropped(self):
        self.assertEqual(relaxed("BIGINT PRIMARY KEY"), "BIGINT")

    def test_it_only_ever_adds(self):
        i = _SRC.index("def ensureDdlColumns(")
        body = _SRC[i:_SRC.index("\n\n\n", i)]
        self.assertIn("ADD COLUMN IF NOT EXISTS", body)
        for forbidden in ("DROP COLUMN", "ALTER COLUMN", "TYPE ", "SET DEFAULT"):
            self.assertNotIn(forbidden, body, forbidden)

    def test_one_bad_column_does_not_lose_the_rest(self):
        i = _SRC.index("def ensureDdlColumns(")
        body = _SRC[i:_SRC.index("\n\n\n", i)]
        self.assertIn("SAVEPOINT ddlcol", body)
        self.assertIn("ROLLBACK TO SAVEPOINT ddlcol", body)
        self.assertIn("continue", body)


class TheColumnsThatBitUs(unittest.TestCase):

    def test_meets_tf_declares_the_venue_name_and_url(self):
        cols = dict(_tables(_SRC).get("meets_tf") or [])
        self.assertIn("venue_name", cols)
        self.assertIn("meet_url", cols)

    def test_both_result_tables_declare_status_through_a_migration(self):
        self.assertIn("def _migrateResultsAddStatus(cursor):", _SRC)
        self.assertIn('for table in ("results", "results_tf")', _SRC)

    def test_createtables_runs_the_derived_pass(self):
        i = _SRC.index("def createTables():")
        body = _SRC[i:_SRC.index("\n\n", i + 200)]
        self.assertIn("ensureDdlColumns(cursor, _ddl)", body)
        self.assertIn("auditInsertColumns(cursor)", body)


class TheInsertAudit(unittest.TestCase):
    """The derived ALTERs can only add what the DDL DECLARES. meets_tf
    INSERTs track_type, source and id_system and declares none of them, so
    those are REPORTED -- a column with no declared type has no type to add
    it with, and guessing is how a TEXT column becomes an unusable BIGINT."""

    def test_it_reads_the_insert_column_lists(self):
        got = insertColumns(_SRC)
        self.assertIn("meets_tf", got)
        self.assertIn("venue_name", got["meets_tf"])
        self.assertIn("track_type", got["meets_tf"])
        self.assertIn("status", got["results"])
        self.assertIn("status", got["results_tf"])

    def test_a_comment_inside_a_column_list_is_ignored(self):
        got = insertColumns("INSERT INTO t (a, -- why\n b, c) VALUES %s")
        self.assertEqual(got["t"], {"a", "b", "c"})

    def test_it_reports_rather_than_alters(self):
        i = _SRC.index("def auditInsertColumns(")
        body = _SRC[i:_SRC.index("\n\n\n", i)]
        self.assertNotIn("ALTER TABLE", body)
        self.assertIn("information_schema.columns", body)
        self.assertIn("does not have", body.lower().replace("⚠ ", ""))

    def test_the_backfill_script_asks_for_its_column_first(self):
        with io.open(os.path.join(_ROOT, "scripts", "backfill_tf_venues.py"),
                     encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn("ensureCoreColumns()", src)
        body = src[src.index("def main("):]
        self.assertLess(body.index("ensureCoreColumns()"), body.index("census(cur)"))


if __name__ == "__main__":
    unittest.main(verbosity=2)


class ItNeverBecomesTheThingEverythingWaitsOn(unittest.TestCase):
    """⚠⚠ THE OUTAGE (owner, 2026-09-17): two instances "both hung they're
    not gonna run". Three mistakes compounding:

      1. ADD COLUMN IF NOT EXISTS was issued for all 98 columns every time,
         and it takes an ACCESS EXCLUSIVE LOCK even when the column is
         already there and nothing changes;
      2. ensureCoreColumns committed ONCE at the end, so it HELD those locks
         on twelve tables at once -- results (39M) and results_tf (191M)
         among them;
      3. no lock_timeout, so it waited for ever behind an open reader WHILE
         HOLDING exclusive locks, and everything queued behind it.

    That is not a slow migration, it is a site outage with a progress bar."""

    def test_it_reads_the_catalogue_before_it_takes_a_lock(self):
        self.assertIn("def ddlPlan(cursor, ddl):", _SRC)
        i = _SRC.index("def ddlPlan(")
        body = _SRC[i:_SRC.index("\n\n\n", i)]
        self.assertIn("_liveColumns(cursor, table)", body)
        self.assertNotIn("ALTER TABLE", body, "the plan must take no locks")

    def test_a_column_that_is_already_there_is_never_altered(self):
        i = _SRC.index("def ddlPlan(")
        body = _SRC[i:_SRC.index("\n\n\n", i)]
        self.assertIn("if n.lower() not in have", body)

    def test_every_alter_has_a_lock_timeout(self):
        """It yields instead of queueing: the column is added next run."""
        self.assertIn("_LOCK_TIMEOUT = ", _SRC)
        # in BOTH functions that alter, the timeout is set before the ALTER
        for fn in ("def ensureDdlColumns(", "def ensureCoreColumns("):
            i = _SRC.index(fn)
            body = _SRC[i:_SRC.index("\n\n\n", i)]
            self.assertIn("SET LOCAL lock_timeout", body, fn)
            self.assertLess(body.index("SET LOCAL lock_timeout"),
                            body.index("ALTER TABLE {table}"), fn)

    def test_locks_are_never_held_across_tables(self):
        i = _SRC.index("def ensureCoreColumns(")
        body = _SRC[i:_SRC.index("\n\n\n", i)]
        self.assertIn("conn.commit()", body)
        # the plan is read and committed BEFORE any ALTER is attempted
        self.assertLess(body.index("conn.commit()"), body.index("ALTER TABLE"))
        # and again after each column
        self.assertGreaterEqual(body.count("conn.commit()"), 3)

    def test_a_no_op_run_says_so_rather_than_going_quiet(self):
        i = _SRC.index("def ensureCoreColumns(")
        body = _SRC[i:_SRC.index("\n\n\n", i)]
        self.assertIn("no locks taken", body)

    def test_a_skipped_column_is_always_reported(self):
        """A column skipped because the table was busy is a column the next
        INSERT dies on; silence makes that a mystery at 3am."""
        i = _SRC.index("def ensureDdlColumns(")
        body = _SRC[i:_SRC.index("\n\n\n", i)]
        self.assertIn("could not add", body)
        # printed unconditionally, not behind `if verbose`
        j = body.index("could not add")
        self.assertNotIn("if verbose", body[max(0, j - 200):j])
