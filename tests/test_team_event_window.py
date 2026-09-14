"""The Teams board takes the same event window the Ability board does.

★ "Team rankings need to have the same event thing as ability" (owner,
  2026-09-14). A track team's rating is the top five of an aggregate over
  everything its athletes ran, so an 800 squad and a 5000 squad are scored
  on one number and neither projects an autumn.

★ AND IT IS THE SAME ESTIMATOR, NOT A SECOND ONE. athlete_season has
  already aggregated the events away; rankings._abilitySource recomputes
  the season over ranking_results, which carries a distance per row. The
  teams path reuses it, so a restricted team board is scored on exactly
  the numbers the restricted athlete board shows.
"""
import io
import os
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*p):
    with io.open(os.path.join(_ROOT, *p), encoding="utf-8") as fh:
        return fh.read()


class Parsing(unittest.TestCase):

    def setUp(self):
        self.src = read("racecast", "teams.py")

    def test_the_window_is_parsed_in_metres_both_ends(self):
        for key in ("dist_min", "dist_max"):
            self.assertIn(f'f["{key}"]', self.src)
        self.assertIn('must be a distance in metres', self.src)
        self.assertIn("must be between 0 and 200000 metres", self.src)

    def test_an_inverted_window_is_refused(self):
        self.assertIn("dist_min is greater than dist_max", self.src)

    def test_it_needs_exactly_one_year(self):
        """No prebuilt board for a restricted window, so the field is raced
        live -- and every season at once is a pile, not a meet."""
        self.assertIn("an event window needs exactly one year selected",
                      self.src)
        i = self.src.index("an event window needs exactly one year")
        self.assertIn('len(f["year"] or ()) != 1',
                      self.src[max(0, i - 400):i])


class TheSource(unittest.TestCase):

    def setUp(self):
        self.src = read("racecast", "teams.py")

    def test_the_restricted_board_reuses_the_ability_estimator(self):
        self.assertIn("from rankings import _abilitySource", self.src)
        self.assertIn("return _abilitySource(f, params, inner)", self.src)

    def test_the_unrestricted_board_still_reads_the_prebuilt_table(self):
        i = self.src.index("def _athleteSource")
        body = self.src[i:self.src.index("def getReturningField")]
        self.assertIn('return "athlete_season s", athleteUnitCols()', body)

    def test_the_inner_predicates_are_unqualified(self):
        """_abilitySource filters `r` and reuses these verbatim; a s.-
        qualified predicate would not resolve inside it."""
        i = self.src.index("def _athleteSource")
        body = self.src[i:self.src.index("def getReturningField")]
        self.assertIn('" AND pool = %(pool)s AND sport = %(sport)s"', body)
        self.assertNotIn("s.pool = %(pool)s", body)

    def test_the_unit_columns_follow_whichever_shape_is_in_use(self):
        """Naming one the subquery left out is the same UndefinedColumn
        the returning board just learned about."""
        i = self.src.index("def _athleteSource")
        body = self.src[i:self.src.index("def getReturningField")]
        self.assertIn('_rowHasUnit(c, "athlete_season")', body)
        self.assertIn("source, have = _athleteSource(f, params)", self.src)

    def test_the_from_clause_is_the_chosen_source(self):
        self.assertIn("FROM   {source}", self.src)
        self.assertNotIn("FROM   athlete_season s\n", self.src)


class TheRouting(unittest.TestCase):

    def setUp(self):
        self.src = read("racecast", "teams.py")

    def test_a_restricted_board_is_raced_not_read(self):
        """team_season holds a meet scored on the WHOLE season's ratings,
        so serving it under an event filter answers with numbers that
        ignore the filter."""
        self.assertIn("if gradeExcluded(f) or eventRestricted(f):", self.src)

    def test_the_response_says_which_kind_of_board_it_is(self):
        self.assertIn('"event_window": eventRestricted(f)', self.src)
        self.assertIn('"returning": gradeExcluded(f)', self.src)

    def test_the_window_rides_back_so_the_page_can_explain_itself(self):
        self.assertIn('"dist_min": f.get("dist_min")', self.src)


class TheControl(unittest.TestCase):

    def test_it_survives_the_teams_tab(self):
        html = read("racecast", "templates", "rankings.html")
        self.assertIn('<div class="field ability-only teams-ok">', html)

    def test_the_css_lets_exactly_that_one_through(self):
        css = read("racecast", "static", "style.css")
        self.assertIn('.rankings-page[data-board="teams"] '
                      '.ability-only.teams-ok { display: flex; }', css)
        self.assertIn('.rankings-page[data-board="teams"] '
                      '.ability-only { display: none; }', css)

    def test_the_board_sends_it(self):
        js = read("racecast", "static", "rankings.js")
        self.assertIn('if (state.board === "ability" || '
                      'state.board === "teams") {', js)

    def test_the_other_boards_still_do_not(self):
        """The API refuses it there rather than ignoring it."""
        js = read("racecast", "static", "rankings.js")
        i = js.index('q.set("dist_min", ev.value)')
        guard = js[max(0, i - 300):i]
        self.assertIn("state.board ===", guard)
        self.assertNotIn('"performance"', guard)
        self.assertNotIn('"pr"', guard)


if __name__ == "__main__":
    unittest.main()
