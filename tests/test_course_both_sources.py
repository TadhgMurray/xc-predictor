# Project: xc-predictor / tests
# File:    test_course_both_sources.py
# Purpose: a course page shows the races held on it, from EITHER feed.
#
# ★ THE SYMPTOM (owner, 2026-09-18): "those meets that were 404ing are not
#   present on course pages". The ?r= source pin made the tfrrs copy of a
#   race reachable from the athlete page; the course page still did not
#   have it, because it never did.
#
# ⚠ AND IT WAS NOT A FILTER. Every get_course_* query did
#       JOIN meets m ON m.div_id = r.div_id AND m.source = r.source
#   and `meets` is written by exactly one function -- anet's saveMeet.
#   save_tfrrs writes `results` and `meets_tfrrs` and never `meets`. So an
#   INNER JOIN on `meets` dropped every tfrrs cross country row before the
#   WHERE was reached. They could not appear at any distance, on any board.
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
    out = {}
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.FunctionDef) and node.name in _COURSE_QUERIES:
            out[node.name] = ast.get_source_segment(src, node)
    return out


class EveryCourseQueryReachesBothFeeds(unittest.TestCase):

    def setUp(self):
        self.src = _src()
        self.fns = _functions(self.src)

    def test_they_all_exist(self):
        self.assertEqual(sorted(self.fns), sorted(_COURSE_QUERIES))

    def test_none_inner_joins_the_anet_only_table(self):
        for name, body in self.fns.items():
            with self.subTest(fn=name):
                self.assertNotIn("JOIN meets m ON m.div_id", body)
                self.assertIn("_courseJoin", body)

    # ! THE NAME HAS TO COALESCE TOO. Joining meets_tfrrs and then filtering
    #   on m.course_name still matches nothing for a tfrrs row.
    def test_none_filters_on_the_anet_name_alone(self):
        for name, body in self.fns.items():
            with self.subTest(fn=name):
                self.assertNotIn("m.course_name = %(course)s", body)
                self.assertIn("_xc_course_sql", body)

    # ⚠ A HELPER IN A PLAIN STRING IS A LITERAL "{_courseJoin('r')}" SENT
    #   TO POSTGRES. Every query that names one must be an f-string.
    def test_every_query_naming_a_helper_is_an_f_string(self):
        for name, body in self.fns.items():
            for m in re.finditer(r'cur\.execute\((f?)"""(.*?)"""', body,
                                 re.S):
                prefix, sql = m.group(1), m.group(2)
                if "{_" in sql:
                    with self.subTest(fn=name):
                        self.assertEqual(prefix, "f",
                                         f"{name}: helper in a plain string")


class TheJoinIsOuterOnBothSides(unittest.TestCase):
    """An inner join on either table re-creates the bug for the other feed."""

    # ! THE RETURNED SQL, NOT THE FUNCTION. The docstring explains the bug
    #   and so contains the words "LEFT JOIN" and "WHERE"; a test that reads
    #   the prose is testing the comment.
    @staticmethod
    def _sql():
        src = _src()
        i = src.index("def _courseJoin(")
        body = src[i:src.index("\ndef ", i + 10)]
        j = body.index('return f"""') + len('return f"""')
        return body[j:body.index('"""', j)]

    def test_both_sides_are_left_joins(self):
        body = self._sql()
        self.assertEqual(body.count("LEFT JOIN"), 2)
        self.assertIn("meets_tfrrs mt", body)
        self.assertIn("mt.sport = 'XC'", body)

    # ! GUARDED INSIDE THE ON CLAUSE, not the WHERE: an anet row must simply
    #   not match meets_tfrrs rather than be filtered out of the result.
    def test_the_tfrrs_side_is_guarded_by_source_in_the_on_clause(self):
        body = self._sql()
        self.assertIn("source = 'tfrrs'", body)
        self.assertNotIn("WHERE", body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
