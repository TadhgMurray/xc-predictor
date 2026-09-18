# Project: xc-predictor / tests
# File:    test_pg_guard.py
# Purpose: every heavy read-only diagnostic bounds its own connection.
#
#   python tests/test_pg_guard.py
#
# ⚠⚠ WHY (owner, 2026-09-18):
#
#     psycopg2.errors.DiskFull: could not write to file
#     "base/pgsql_tmp/pgsql_tmp4056952.593": No space left on device
#
#    diag_indoor_level -- READ-ONLY -- filled the server's temp space while an
#    11-hour scrape was running. temp_file_limit makes the query die instead of
#    the disk, and it is per-session so it cannot touch the scrapers.
import ast
import io
import os
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# every diagnostic that scans a whole result table
HEAVY = ("scripts/diag_indoor_level.py",
         "engine/rating_outliers.py",
         "engine/diag_difficulty_calibration.py")


def read(rel):
    with io.open(os.path.join(_ROOT, *rel.split("/")), encoding="utf-8") as fh:
        return fh.read()


class TheGuardItself(unittest.TestCase):

    def setUp(self):
        self.src = read("scripts/pg_guard.py")

    # ★ temp_file_limit IS THE ONE THAT MATTERS. The others are manners.
    def test_it_sets_a_temp_file_limit(self):
        self.assertIn("temp_file_limit", self.src)
        self.assertIn("statement_timeout", self.src)
        self.assertIn("work_mem", self.src)

    # ! ONE SET AT A TIME, so a managed Postgres refusing one does not cost
    #   the others.
    def test_each_setting_is_attempted_separately(self):
        i = self.src.index("for name, value in want:")
        body = self.src[i:]
        self.assertIn("try:", body)
        self.assertIn("except Exception", body)

    # ! PER SESSION. A guard that changed the server would throttle the
    #   scrapers, which is worse than the problem.
    def test_it_is_session_scoped_not_global(self):
        self.assertIn("SET {name}", self.src)
        self.assertNotIn("ALTER SYSTEM", self.src)
        self.assertNotIn("SET GLOBAL", self.src)

    def test_the_limits_are_overridable_from_the_environment(self):
        for var in ("XCP_DIAG_TEMP_LIMIT", "XCP_DIAG_TIMEOUT",
                    "XCP_DIAG_WORK_MEM"):
            self.assertIn(var, self.src)


class EveryHeavyDiagnosticUsesIt(unittest.TestCase):

    def test_they_all_guard_before_querying(self):
        for rel in HEAVY:
            src = read(rel)
            self.assertIn("from pg_guard import guard", src, rel)
            self.assertIn("guard(cur)", src, rel)
            # ! FIRST THING IN THE CURSOR BLOCK. A guard set after the heavy
            #   query has already spilled is decoration. Checked as "within the
            #   few lines after the cursor opens", which is robust to whether
            #   the query runs inline or through a helper.
            i = src.index("with conn.cursor() as cur:")
            self.assertIn("guard(cur)", src[i:i + 500], rel)


# ⚠⚠ AND THE REAL FIX WAS NOT A FASTER QUERY, IT WAS THE RIGHT ONE (owner,
#    2026-09-18: "idk what ur doing for that query but its def awful it should
#    not be this hard"). Correct. The claim was about DIFFICULTY, and the
#    difficulty per cell is published in course_difficulties -- 74k rows, with
#    the surface in the cell key. Scanning 192M result rows to infer it was the
#    mistake; making that scan cheaper was treating the symptom.
class TheAnswerComesFromThePublishedDifficulty(unittest.TestCase):

    def setUp(self):
        self.src = read("scripts/diag_indoor_level.py")

    def test_section_one_reads_course_difficulties_not_result_rows(self):
        i = self.src.index("=== 1. the difficulty the engine PUBLISHED")
        block = self.src[i:self.src.index("if args.athletes:", i)]
        self.assertIn("FROM   course_difficulties", block)
        self.assertNotIn("results_tf", block)

    # ! THE SURFACE IS IN THE CELL KEY, read the way bracket_engine reads it:
    #   starts with "TF:", and the part before "@" ends with ":in".
    def test_it_reads_the_surface_the_way_the_engine_does(self):
        self.assertIn("split_part(course_name, '@', 1) LIKE '%%:in'", self.src)
        self.assertIn("course_name LIKE 'TF:%%'", self.src)

    def test_the_expensive_scan_is_opt_in(self):
        self.assertIn('ap.add_argument("--athletes"', self.src)
        self.assertLess(self.src.index("=== 1. the difficulty the engine"),
                        self.src.index("if args.athletes:"))


class TheIndoorScanNoLongerSpills(unittest.TestCase):
    """When it IS asked for, --athletes must not spill either: the first
    version asked percentile_cont per (person, season) over 192M rows, sorting
    into ~10M groups. It counts first (hash aggregate, no sort) and takes
    medians only for the small set that has both surfaces."""

    def setUp(self):
        self.src = read("scripts/diag_indoor_level.py")

    def test_it_counts_before_it_sorts(self):
        # pass 1 is a COUNT..HAVING into a temp table, not a percentile
        i = self.src.index("CREATE TEMP TABLE _ind_both")
        pass1 = self.src[i:self.src.index("pass 2")]
        self.assertIn("HAVING count(*) FILTER", pass1)
        self.assertNotIn("percentile_cont", pass1)

    def test_pass_two_is_restricted_to_that_set(self):
        i = self.src.index("pass 2")
        pass2 = self.src[i:]
        self.assertIn("FROM   _ind_both b", pass2)
        self.assertIn("percentile_cont", pass2)

    # ! AND THE DEFAULT WINDOW IS NARROW. The run that filled the disk was
    #   --since 2015; four seasons give the same answer.
    def test_the_default_window_is_narrow(self):
        self.assertIn("DEFAULT_SINCE = 2021", self.src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
