# Project: xc-predictor / tests
# File:    test_tfrrs_column_map.py
# Purpose: which tfrrs column is which, decided from content. No network, no
#          BeautifulSoup, no database.
#
#   python -m pytest -q tests/test_tfrrs_column_map.py
#
# ⚠⚠ THE FAILURE (owner, 2026-09-17: "make sure tfrrs parses well (no column
#    shifts and such)"). Every parser reads by fixed index and the only guard
#    is `len(cells) < N` -- which catches too FEW columns and passes a shifted
#    row trivially, because a shifted row has MORE. Insert one column and the
#    finish time silently comes from the AVG MILE cell: a per-mile pace stored
#    as a race time, no error, discovered weeks later from the ratings. There
#    is no cached HTML to re-parse.
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "tfrrs", "parser"))

import column_map as C                                          # noqa: E402

A = ["https://www.tfrrs.org/athletes/8270167/Conn_College/Grace_McDonough.html"]
T = ["https://www.tfrrs.org/teams/xc/CT_college_f_Conn_College.html"]

# the layout every page has had so far
TODAY = [
    [("13", []), ("Grace McDonough", A), ("SR-4", []), ("Conn College", T),
     ("5:44.9", []), ("21:26.1", []), ("11", [])],
    [("14", []), ("Ana Ruiz", A), ("JR-3", []), ("Conn College", T),
     ("5:47.2", []), ("21:35.0", []), ("12", [])],
]
HEADER = ["PL", "NAME", "YR", "TEAM", "Avg. Mile", "TIME", "SCORE"]


def shift(rows, at=1, text="1041"):
    """tfrrs inserts a column (a bib number) at `at`."""
    return [r[:at] + [(text, [])] + r[at:] for r in rows]


class TodaysLayout(unittest.TestCase):

    def test_it_reads_the_same_columns_the_parsers_hardcode(self):
        got, note = C.detectColumns(TODAY, HEADER)
        self.assertEqual(got, C.XC_DEFAULT)
        self.assertIsNone(note, "nothing moved, so nothing to say")

    def test_it_works_with_no_header_at_all(self):
        got, note = C.detectColumns(TODAY)
        self.assertEqual(got, C.XC_DEFAULT)
        self.assertIsNone(note)

    def test_the_finish_time_is_never_the_average_mile(self):
        """★ THE ONE GENUINE AMBIGUITY -- both cells parse as times, and
        parse_xc's header has warned since the beginning that reading [4]
        "would give a per-mile pace and silently corrupt every XC time"."""
        got, _ = C.detectColumns(TODAY, HEADER)
        self.assertEqual(got["time"], 5)
        self.assertEqual(got["avg_mile"], 4)
        # and with no header, the LARGER time still wins
        got, _ = C.detectColumns(TODAY)
        self.assertEqual(got["time"], 5)


class AShiftedTable(unittest.TestCase):
    """The whole point: tfrrs inserts a column and the parse stays correct."""

    def test_every_field_follows_the_shift(self):
        rows = shift(TODAY)
        got, note = C.detectColumns(rows, ["PL", "BIB"] + HEADER[1:])
        self.assertEqual(got["athlete"], 2)
        self.assertEqual(got["year"], 3)
        self.assertEqual(got["team"], 4)
        self.assertEqual(got["time"], 6)
        self.assertEqual(got["score"], 7)

    def test_and_it_says_so_rather_than_going_quiet(self):
        got, note = C.detectColumns(shift(TODAY))
        self.assertIsNotNone(note)
        self.assertIn("layout moved", note)
        self.assertIn("time 5->6", note)

    def test_a_column_removed_is_handled_too(self):
        """No avg-mile column: the one remaining time is the finish."""
        rows = [r[:4] + r[5:] for r in TODAY]
        got, _ = C.detectColumns(rows)
        self.assertEqual(got["time"], 4)
        self.assertEqual(got["team"], 3)

    def test_the_old_code_path_would_have_read_the_pace(self):
        """Stated as an assertion so the damage is on the record: at the
        hardcoded index 5, a shifted row yields 5:44.9 -- a per-mile pace."""
        rows = shift(TODAY)
        self.assertEqual(rows[0][5][0], "5:44.9")       # what cells[5] becomes
        got, _ = C.detectColumns(rows)
        self.assertEqual(rows[0][got["time"]][0], "21:26.1")


class OddRows(unittest.TestCase):
    """A single row can be strange; the table's map is what most agree on."""

    def test_a_name_only_finisher_does_not_move_the_map(self):
        """Old meets list finishers with no <a> at all."""
        odd = [("99", []), ("R. Nameless", []), ("", []), ("Unattached", []),
               ("6:10.0", []), ("24:02.0", []), ("", [])]
        got, note = C.detectColumns(TODAY + [odd], HEADER)
        self.assertEqual(got, C.XC_DEFAULT)
        self.assertIsNone(note)

    def test_a_blank_year_does_not_move_the_map(self):
        odd = [("40", []), ("Pat Blank", A), ("", []), ("Conn College", T),
               ("6:02.0", []), ("23:10.0", []), ("", [])]
        got, _ = C.detectColumns(TODAY + [odd], HEADER)
        self.assertEqual(got["year"], 2)

    def test_a_score_is_not_a_time(self):
        """A bare integer must never parse as seconds -- a score of 11 is not
        eleven seconds."""
        self.assertIsNone(C._seconds("11"))
        self.assertIsNone(C._seconds(""))
        self.assertTrue(C._seconds("21:26.1"))

    def test_a_place_is_not_a_year(self):
        """"11" matches the high-school year vocabulary AND is a perfectly
        good finishing position; only a cell AFTER the athlete can be a
        year."""
        got, _ = C.detectColumns(TODAY)
        self.assertEqual(got["place"], 0)
        self.assertNotEqual(got["year"], 0)


class RefusingRatherThanGuessing(unittest.TestCase):

    def test_without_an_athlete_or_a_time_the_map_is_not_trusted(self):
        self.assertTrue(C.trustworthy(C.XC_DEFAULT))
        self.assertFalse(C.trustworthy({"place": 0, "team": 3}))
        self.assertFalse(C.trustworthy({"athlete": 1}))

    def test_an_empty_table_falls_back_to_the_documented_layout(self):
        got, note = C.detectColumns([])
        self.assertEqual(got, C.XC_DEFAULT)
        self.assertIsNone(note)


if __name__ == "__main__":
    unittest.main(verbosity=2)
