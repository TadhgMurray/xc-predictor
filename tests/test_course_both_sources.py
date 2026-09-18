# Project: xc-predictor / tests
# File:    test_course_both_sources.py
# Purpose: a course page shows the races held on it, from EITHER feed, and
#          still resolves the course through an index.
#
# ★ THE SYMPTOM (owner, 2026-09-18): "those meets that were 404ing are not
#   present on course pages". The ?r= source pin made the tfrrs copy of a
#   race reachable from the athlete page; the course page did not have it,
#   because it never did.
#
# ⚠ AND IT WAS NOT A FILTER. Every get_course_* query did
#       JOIN meets m ON m.div_id = r.div_id AND m.source = r.source
#   and `meets` is written by exactly one function -- anet's saveMeet.
#   save_tfrrs writes `results` and `meets_tfrrs`, never `meets`. So the
#   INNER JOIN dropped every tfrrs cross country row before the WHERE was
#   reached: they could not appear, on any board, at any distance.
#
# ⚠⚠ AND THE FIRST FIX HAD TO BE REVERTED WITHIN THE HOUR. Filtering on
#    COALESCE(m.course_name, mt.venue_name) is correct and unindexable, so
#    the planner joined 39M result rows to both meet tables and evaluated
#    the expression per row. The page went from fast to unusable. This file
#    pins BOTH properties, because either alone is a bug shipped.
import io
import os
import re
import ast
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_COURSE_QUERIES = (
    "get_course_header", "get_course_rating_bests",
    "get_course_team_rating_bests", "get_course_meets",
    "get_course_distances", "get_course_records",
    "get_course_team_records",
)


def _src():
    with io.open(os.path.join(_ROOT, "racecast", "app.py"),
                 encoding="utf-8") as fh:
        return fh.read()


def _functions(src):
    return {n.name: ast.get_source_segment(src, n)
            for n in ast.walk(ast.parse(src))
            if isinstance(n, ast.FunctionDef) and n.name in _COURSE_QUERIES}


class EveryCourseQueryReachesBothFeeds(unittest.TestCase):

    def setUp(self):
        self.src = _src()
        self.fns = _functions(self.src)

    def test_they_all_exist(self):
        self.assertEqual(sorted(self.fns), sorted(_COURSE_QUERIES))

    def test_none_joins_the_anet_only_table_directly(self):
        for name, body in self.fns.items():
            with self.subTest(fn=name):
                self.assertNotIn("JOIN meets m ON m.div_id", body)
                self.assertIn("_courseRowsCte()", body)

    def test_none_names_a_meet_table_of_its_own(self):
        """One definition of "raced on this course", not seven."""
        for name, body in self.fns.items():
            with self.subTest(fn=name):
                self.assertNotIn("FROM meets ", body)
                self.assertNotIn("FROM   meets ", body)
                self.assertNotIn("meets_tfrrs", body)

    # ⚠ A HELPER IN A PLAIN STRING IS A LITERAL "{_courseRowsCte()}" SENT TO
    #   POSTGRES. Four of these seven were plain strings before the fix.
    def test_every_query_naming_a_helper_is_an_f_string(self):
        for name, body in self.fns.items():
            for m in re.finditer(r'cur\.execute\((f?)"""(.*?)"""', body,
                                 re.S):
                if "{_" in m.group(2):
                    with self.subTest(fn=name):
                        self.assertEqual(m.group(1), "f")


class TheCourseIsResolvedByIndex(unittest.TestCase):
    """The half that got reverted. Both lookups must stay sargable."""

    def setUp(self):
        src = _src()
        i = src.index("def _courseRowsCte(")
        self.cte = src[i:src.index("\ndef ", i)]

    def test_the_anet_side_compares_the_column_itself(self):
        # a bare compare can use idx_meets_course_name; an expression cannot
        self.assertIn("m.course_name = %(course)s", self.cte)

    def test_the_tfrrs_side_compares_the_column_itself(self):
        self.assertIn("mt.venue_name = %(course)s", self.cte)

    # ★ THE REVERTED SHAPE, NAMED SO IT CANNOT COME BACK.
    def test_no_coalesce_over_the_course_name(self):
        self.assertNotIn("COALESCE(m.course_name", self.cte)
        for body in _functions(_src()).values():
            self.assertNotIn("COALESCE(m.course_name", body)

    def test_the_two_are_unioned_not_outer_joined(self):
        self.assertIn("UNION ALL", self.cte)
        self.assertNotIn("LEFT JOIN meets", self.cte)

    def test_the_index_it_needs_is_declared(self):
        with io.open(os.path.join(_ROOT, "scripts",
                                  "add_page_indexes.py"),
                     encoding="utf-8") as fh:
            idx = fh.read()
        self.assertIn('"meets_tfrrs"', idx)
        self.assertIn("venue_name", idx)
        self.assertIn('"meets",           "course_name"', idx)


if __name__ == "__main__":
    unittest.main(verbosity=2)
