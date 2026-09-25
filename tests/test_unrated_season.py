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


class ItRefusesToRunAgainstANotNullColumn(unittest.TestCase):
    """! FAIL BEFORE THE SORT, NOT AFTER IT. createShadow copies the live
    table's constraints, and this aggregate's own header says its multi-GB
    group-by sort is what decides the step's runtime. A NOT NULL left over
    from an old migration would blow up an hour in, at the end of a
    pipeline, on a server nobody is watching. The probe costs one catalog
    lookup and names the ALTER that fixes it.

    Verified on Postgres 16 against a nullable table and one with NOT NULL
    on two of the three columns; the probe returned [] and
    ['best_rating', 'mean_rating']."""

    def setUp(self):
        with io.open(os.path.join(_ROOT, "racecast",
                                  "build_ranking_results.py"),
                     encoding="utf-8") as fh:
            src = fh.read()
        i = src.index("def refreshAthleteSeason")
        self.body = src[i:src.index("\ndef ", i + 10)]

    def test_it_checks_before_it_aggregates(self):
        self.assertLess(self.body.index("is_nullable"),
                        self.body.index("cur.execute(sql)"))

    def test_it_names_all_three_rating_columns(self):
        for col in ("mean_rating", "decayed_rating", "best_rating"):
            self.assertIn(f"'{col}'", self.body, col)

    def test_it_repairs_the_shadow_rather_than_stopping(self):
        """⚠ A WARNING HERE WOULD BE READ AS NOISE and the step would fail
        anyway, three hours later, with a constraint violation instead of
        an explanation. It used to RAISE instead -- and on 2026-09-24 that
        stopped the boards swapping for a third run, after every index had
        built. The shadow is the script's own table, read by nothing until
        swapIn, so the constraint is dropped THERE, before the aggregate,
        and the swap carries the fix to the live table."""
        i = self.body.index("ALTER TABLE {_LOAD_SEASON}")
        self.assertIn("DROP NOT NULL", self.body[i:i + 120])
        self.assertLess(i, self.body.index("cur.execute(sql)"))
        self.assertNotIn("ALTER TABLE athlete_season ", self.body)


class TheBoardsExcludeThem(unittest.TestCase):
    """⚠⚠ A RATING-LESS SEASON IS NOT A BOARD ROW. Measured on Postgres 16
    with 40 rated and 60 unrated seasons in one pool:

        board size without the filter   140   (should be 80)
        the top three of that board     the three unrated rows, blank

    `ORDER BY mean_rating DESC` puts NULLs FIRST in Postgres, so the board
    was not merely diluted, it was HEADED by seasons with no rating. And
    countOf is the denominator for "top 2%" on the athlete header while
    rankOf is the numerator -- letting the two disagree would not fail, it
    would silently misreport every percentile on the site.

    ★ SO THE PREDICATE LIVES IN floorSql, NOT AT THE FIVE CALL SITES. One
      rule, every reader, which is the argument season_floor's own header
      already makes about the race floor."""

    def test_the_floor_requires_a_rating_in_both_branches(self):
        import sys
        sys.path.insert(0, os.path.join(_ROOT, "racecast"))
        from season_floor import floorSql
        for explicit in (True, False):
            sql = floorSql(explicit)
            self.assertIn("s.mean_rating IS NOT NULL", sql, explicit)
        # the default branch's OR must stay parenthesised, or the AND
        # would bind tighter and the open-season exemption would vanish
        self.assertTrue(floorSql(False).startswith("(s.n_races"))
        self.assertIn(") AND s.mean_rating IS NOT NULL", floorSql(False))

    def test_the_floor_honours_its_alias(self):
        """! build_recruiting calls it with 'c' and 'h'."""
        import sys
        sys.path.insert(0, os.path.join(_ROOT, "racecast"))
        from season_floor import floorSql
        self.assertIn("c.mean_rating IS NOT NULL", floorSql(False, "c"))

    def test_every_ability_board_query_goes_through_it(self):
        """! IF ONE CALL SITE STOPPED USING floorSql the numerator and the
        denominator would part company. They are counted here."""
        with io.open(os.path.join(_ROOT, "racecast", "rankings.py"),
                     encoding="utf-8") as fh:
            src = fh.read()
        self.assertGreaterEqual(src.count("floorSql(f['min_races_explicit'])"), 5)


class TheOtherReadersTheAuditNamed(unittest.TestCase):
    """The sites a sweep of every athlete_season reader found, each of which
    either crashed or answered wrongly once the column could be NULL."""

    def _text(self, rel):
        with io.open(os.path.join(_ROOT, *rel.split("/")), encoding="utf-8") as fh:
            return fh.read()

    def test_the_athlete_header_picks_a_rated_season(self):
        """⚠ THE CRASH. This ORDER BY names no rating column, so an unrated
        season won whenever it was the newest with three races -- and
        app.py's `float(athlete["rating"])` then raised on every load."""
        for rel in ("racecast/app.py", "racecast/cards.py"):
            self.assertIn("(mean_rating IS NOT NULL) DESC", self._text(rel), rel)

    def test_board_sanity_ranks_rated_seasons_only(self):
        """⚠ A row_number() OVER (ORDER BY mean_rating DESC) with no NULLS
        LAST. The checker would have stopped flagging ceiling breaches --
        a sanity check that fails OPEN."""
        src = self._text("scripts/board_sanity.py")
        # ! SLICE THE WHOLE QUERY, not a byte count -- the explaining comment
        #   pushed the predicate past a 600-char window and the assertion
        #   failed on the comment rather than on the SQL.
        i = src.index("row_number() OVER (PARTITION BY pool")
        q = src[i:src.index('""", (sport', i)]
        self.assertIn("mean_rating IS NOT NULL", q)

    def test_verdict16_cannot_bucket_a_null(self):
        """! width_bucket(NULL) is NULL, which becomes its own group, and the
        loop then computes `90 + (band - 1) * 10` on it."""
        src = self._text("scripts/verdict16.py")
        i = src.index("SELECT width_bucket(")
        q = src[i:src.index('""", (args.pool', i)]
        self.assertIn("x.mean_rating IS NOT NULL", q)
        self.assertIn("t.mean_rating IS NOT NULL", q)

    def test_the_max_year_probes_pick_a_year_with_ratings_in_it(self):
        """! These choose which season a board or a build window sits on. A
        year whose only seasons are unrated makes the page come back empty
        rather than showing the newest real board."""
        for rel in ("racecast/recruiting.py", "racecast/build_recruiting.py"):
            src = self._text(rel)
            i = src.index("SELECT max(year) AS y FROM athlete_season")
            self.assertIn("mean_rating IS NOT NULL", src[i:i + 400], rel)

    def test_a_predicted_squad_needs_something_to_predict_from(self):
        """! NULLS LAST only parks an unrated season at the tail, where it
        still consumes a squad slot and the top-7 slice."""
        src = self._text("racecast/predict.py")
        i = src.index("WHERE  s.school = ANY(%(schools)s)")
        self.assertIn("s.mean_rating IS NOT NULL", src[i:i + 700])

    def test_a_null_rating_is_never_read_as_zero(self):
        """⚠ `float(r["mean_rating"] or 0)` turns an absence into a
        catastrophically bad measurement, and this dict gets averaged."""
        src = self._text("scripts/diag_model_quality.py")
        i = src.index("def seasonRatings")
        body = src[i:i + 900]
        self.assertNotIn('mean_rating"] or 0', body)
        self.assertIn("mean_rating IS NOT NULL", body)


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
