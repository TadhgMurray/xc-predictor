# Project: xc-predictor / tests
# File:    test_unrated_season.py
# Purpose: a season made entirely of unrated races still produces an
#          athlete_season row -- and adding it changes nothing else.
#
# ★★ THE MEASUREMENT (De La Salle, live box, 2026-09-21):
#
#        ranking_results   1,036 athlete-seasons under the name
#        athlete_season       43
#        of its 686 people, 617 had NO athlete_season row at all
#
#    The engine rates nothing under 800m, on purpose, and rates no field
#    event. Those rows reach ranking_results unrated anyway (issue #46: the
#    sprint PR boards rank a CLOCK and never needed a rating). But
#    _ATHLETE_SEASON_SQL's season_med CTE was built `WHERE speed_rating IS
#    NOT NULL`, so a person-season made entirely of sprints and throws
#    produced no group and the INNER JOIN dropped it.
#
#    Downstream that is five symptoms from one cause: no school roster line,
#    no school athlete count, no (school, state) cluster -- De La Salle's
#    chips read LA(19), CA(19) for a programme that is 1,446 athletes in
#    California -- no year in the year bar, and a `currentSeason` that opens
#    the page on whatever year a distance runner last raced.
#
# ⚠ THE CHANGE IS ADDITIVE, AND THAT IS THE PART TO KEEP. A season with any
#   rated race must come out BYTE-IDENTICAL: n_races still counts rated races
#   only. A hybrid -- two rated 800s and four unrated sprints -- must stay at
#   n_races 2, or every mixed athlete's race count silently inflates and the
#   decayed rating dilutes (the aggregate's own comment says why).
#
#   Verified against a real Postgres 16 before shipping, on a fixture with a
#   distance runner (one race 30 points under the median, dropped), a pure
#   sprinter, a hybrid, and a field athlete with one unattached row:
#
#       old EXCEPT ALL new                          -> 0 rows
#       new EXCEPT ALL old                          -> 2 rows
#       of those additions, any with a rating       -> 0 rows
#
#   python -m unittest tests.test_unrated_season
import ast
import io
import os
import re
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _sql():
    """_ATHLETE_SEASON_SQL, rendered, without importing the module (it pulls
    numpy and psycopg2)."""
    with io.open(os.path.join(_ROOT, "racecast", "build_ranking_results.py"),
                 encoding="utf-8") as fh:
        src = fh.read()
    ns = {}
    want = {"_SEASON_Q", "_DECAY_K", "_ANCHOR", "_SEASON_OUTLIER_PTS",
            "_ATHLETE_SEASON_SQL"}
    for node in ast.parse(src).body:
        if (isinstance(node, ast.Assign)
                and getattr(node.targets[0], "id", "") in want):
            exec(ast.get_source_segment(src, node), ns)   # noqa: S102
    if "_ATHLETE_SEASON_SQL" not in ns:
        raise AssertionError("_ATHLETE_SEASON_SQL is gone")
    return ns["_ATHLETE_SEASON_SQL"], ns


class AnUnratedSeasonStillCounts(unittest.TestCase):

    def setUp(self):
        self.sql, self.ns = _sql()

    def test_the_median_cte_no_longer_filters_the_group_away(self):
        """⚠ THE BUG ITSELF. `WHERE speed_rating IS NOT NULL` on the CTE
        removed the whole GROUP, not just the unrated rows in it -- so the
        join had nothing to match and the season vanished. The median must
        come from a FILTER, which drops the rows and keeps the group."""
        cte = self.sql[self.sql.index("WITH season_med"):
                       self.sql.index("INSERT INTO")]
        self.assertIn("FILTER (WHERE speed_rating IS NOT NULL)", cte)
        self.assertNotIn("WHERE  speed_rating IS NOT NULL", cte)

    def test_the_cte_reports_how_many_rated_rows_the_group_has(self):
        cte = self.sql[self.sql.index("WITH season_med"):
                       self.sql.index("INSERT INTO")]
        self.assertIn("count(speed_rating)", cte)
        self.assertIn("n_rated", cte)

    def test_a_rated_season_keeps_exactly_the_old_predicate(self):
        """★ BYTE-IDENTICAL FOR EVERY SEASON THAT ALREADY HAD A ROW. The
        rated arm must still be `speed_rating IS NOT NULL AND speed_rating >=
        med - OUTLIER`, so n_races stays a count of RATED races."""
        where = self.sql[self.sql.index("WHERE (sm.n_rated"):]
        pts = self.ns["_SEASON_OUTLIER_PTS"]
        self.assertIn("sm.n_rated > 0", where)
        self.assertIn("speed_rating IS NOT NULL", where)
        self.assertIn(f"speed_rating >= sm.med - {pts}", where)

    def test_the_unrated_arm_is_the_only_other_way_in(self):
        """! AND IT IS GATED ON n_rated = 0, not on `speed_rating IS NULL`.
        Gating on the row would let a hybrid's four unrated sprints join its
        two rated 800s and take n_races from 2 to 6."""
        where = self.sql[self.sql.index("WHERE (sm.n_rated"):
                         self.sql.index("GROUP BY base.person_id")]
        self.assertIn("OR sm.n_rated = 0", where)
        self.assertNotIn("OR speed_rating IS NULL", where)

    def test_the_join_is_still_an_inner_join_on_the_season_key(self):
        """! season_med now has a row for EVERY group, so the inner join no
        longer drops anything -- and it must stay inner, because a LEFT join
        would let a NULL n_rated fall through both arms silently."""
        self.assertIn("JOIN season_med sm", self.sql)
        self.assertNotIn("LEFT JOIN season_med", self.sql)
        for col in ("person_id", "pool", "sport", "year"):
            self.assertIn(f"sm.{col} = base.{col}", self.sql, col)


class TheRatingColumnsComeOutNull(unittest.TestCase):
    """★ NOT ZERO. A rating of 0 would sort onto the bottom of every board
    and read as a real, terrible result; NULL is the absence the readers
    already guard for."""

    def setUp(self):
        self.sql, _ns = _sql()

    def test_the_decayed_rating_divides_rather_than_coalescing(self):
        """! sum(rating * w) over all-NULL is NULL, and NULL / anything is
        NULL. A COALESCE(...,0) on the numerator would turn a sprinter's
        season into a decayed rating of exactly 0."""
        body = self.sql[self.sql.index("sum(speed_rating"):
                        self.sql.index("max(speed_rating)")]
        self.assertNotIn("COALESCE(sum(speed_rating", body)
        self.assertIn("nullif(", body)

    def test_nothing_coalesces_a_rating_to_a_number(self):
        head = self.sql[self.sql.index("SELECT base.person_id"):
                        self.sql.index("count(*)")]
        self.assertNotIn("COALESCE(speed_rating", head)


class WhatMustTolerateIt(unittest.TestCase):
    """⚠⚠ IN POSTGRES, `ORDER BY x DESC` PUTS NULLS FIRST. Every board that
    sorts athlete_season on a rating column must either filter the NULLs out
    or say NULLS LAST, or a rating-less season heads the board."""

    def _text(self, rel):
        with io.open(os.path.join(_ROOT, *rel.split("/")), encoding="utf-8") as fh:
            return fh.read()

    def _fn(self, body, name):
        """One function's source. ! TO THE END OF FILE when it is the LAST
        def -- schoolTopAthletes is, and slicing to the next `\ndef ` raised
        ValueError instead of testing it."""
        i = body.index(f"def {name}")
        j = body.find("\ndef ", i + 10)
        return body[i:] if j < 0 else body[i:j]

    def test_the_school_roster_sorts_nulls_last(self):
        q = self._fn(self._text("racecast/school.py"), "schoolRoster")
        self.assertIn("ORDER  BY s.mean_rating DESC NULLS LAST", q)

    def test_the_school_boards_still_require_a_rating(self):
        """! schoolBest and schoolTopAthletes are BOARDS, not rosters: an
        athlete with no rating has nothing to be ranked by and must not
        appear. They filter, and that is why they need no NULLS LAST."""
        body = self._text("racecast/school.py")
        for fn in ("schoolBest", "schoolTopAthletes"):
            self.assertRegex(self._fn(body, fn),
                             r"(speed_rating|best_rating)\s+IS NOT NULL", fn)

    def test_the_roster_template_guards_every_rating_cell(self):
        html = self._text("racecast/templates/school.html")
        i = html.index("{% for r in rows %}")
        row = html[i:html.index("{% endfor %}", i)]
        self.assertIn("r.mean_rating is not none", row)
        self.assertIn("r.best_rating is not none", row)


if __name__ == "__main__":
    unittest.main(verbosity=2)
