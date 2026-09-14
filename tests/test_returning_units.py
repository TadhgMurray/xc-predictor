"""The returning-teams board reads unit columns off athlete_season, which
does not reliably have them.

★ team_season IS BUILT WITH ALL NINE, so _fieldWhere can name them without
  asking. athlete_season gets them from a migration that has run on some
  databases and not others -- which is why the ability board probes
  (rankings._rowHasUnit) rather than trusting them. The returning board
  named all nine in its SELECT list with no probe, so on a database missing
  one, every "Graduating (removed)" request is an UndefinedColumn 500
  (owner, 2026-09-14: "The teams ranking page graduating thing doesn't
  work (errors)").
"""
import io
import os
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*p):
    with io.open(os.path.join(_ROOT, *p), encoding="utf-8") as fh:
        return fh.read()


class ReturningUnits(unittest.TestCase):

    def setUp(self):
        self.src = read("racecast", "teams.py")

    def test_the_select_list_is_probed_not_assumed(self):
        self.assertIn("def athleteUnitCols():", self.src)
        self.assertNotIn(
            'units = "".join(f\', s."{c}"\' for c in sorted(TEAM_UNIT_COLS))',
            self.src,
            "the unconditional SELECT list is the 500")

    def test_a_missing_column_becomes_null_not_a_missing_key(self):
        """Every row rankTeams sees has the same shape whichever database
        it came from."""
        self.assertIn('NULL::text AS "{c}"', self.src)

    def test_the_filter_skips_a_unit_this_table_cannot_answer(self):
        i = self.src.index("def _athleteFieldWhere")
        body = self.src[i:self.src.index("def getReturningField")]
        self.assertIn("have = athleteUnitCols()", body)
        self.assertIn("c in TEAM_UNIT_COLS and c in have", body)

    def test_it_probes_presence_not_populated_ness(self):
        """_rowHasUnit also demands values, which is right for a filter and
        wrong for a SELECT list: the only question there is whether naming
        the column raises."""
        i = self.src.index("def athleteUnitCols():")
        body = self.src[i:i + 1400]
        self.assertIn("information_schema.columns", body)
        self.assertIn("table_name = 'athlete_season'", body)
        self.assertNotIn("null_frac", body)

    def test_an_unreachable_database_answers_none_rather_than_raising(self):
        i = self.src.index("def athleteUnitCols():")
        body = self.src[i:i + 1400]
        self.assertIn("except Exception:", body)

    def test_the_team_table_still_names_its_columns_directly(self):
        """team_season is built with them; probing there would be cargo
        cult and would cost a query per board."""
        i = self.src.index("def _fieldWhere")
        body = self.src[i:self.src.index("def _subjectWhere")]
        self.assertIn('t."{c}" = ANY', body)
        self.assertNotIn("athleteUnitCols()", body)

    def test_the_cache_exists_so_a_rebuild_is_picked_up_without_a_restart(self):
        self.assertIn("_ATHLETE_COLS_TTL", self.src)

    def test_removing_grades_still_demands_one_year(self):
        """Unchanged, and correct: an all-time board with the seniors
        removed is a field of squads from thirty seasons."""
        self.assertIn("removing grades needs exactly one year selected",
                      self.src)


if __name__ == "__main__":
    unittest.main()
