"""The rating-scale redraw must not hand one board's rows to another
board's renderer.

★ THE SCREENSHOT (owner, 2026-09-14): the Teams board with "undefined" in
  #, POINTS and RUNNERS, blanks under TOP 5 AVG and 5TH RUNNER, and TEAM /
  STATE / YEAR filled in correctly -- fixed by pressing Apply.

  renderBoard dispatches on state.board. Switching tabs sets state.board and
  starts a fetch; until it lands, _lastBoard still holds the PREVIOUS
  board's rows. scale-view.js dispatches rc-scale-change from its own init
  whenever the stored mode is "hs", which is the DEFAULT -- so the listener
  drew ability rows with renderTeams. Ability rows carry school, state and
  year and none of rank/points/n_athletes: exactly three undefined columns
  and two blank ratings.
"""
import io
import os
import re
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*p):
    with io.open(os.path.join(_ROOT, *p), encoding="utf-8") as fh:
        return fh.read()


class BoardMemo(unittest.TestCase):

    def setUp(self):
        self.js = read("racecast", "static", "rankings.js")
        i = self.js.index('document.addEventListener("rc-scale-change"')
        self.listener = self.js[i:self.js.index("async function load()")]

    def test_the_memo_records_which_board_the_rows_came_from(self):
        self.assertIn("_lastBoard = { board: state.board, rows, data }", self.js)

    def test_a_redraw_for_another_board_is_refused(self):
        self.assertIn("if (_lastBoard.board !== state.board) return;",
                      self.listener)

    def test_it_is_checked_before_the_refetch_decision(self):
        """data.hs_movable belongs to the old board too -- deciding the
        refetch on another board's flag is the same bug."""
        guard = self.listener.index("_lastBoard.board !== state.board")
        movable = self.listener.index("_lastBoard.data.hs_movable")
        self.assertLess(guard, movable)

    def test_a_load_in_flight_is_left_to_finish(self):
        self.assertIn("if (state.busy) return;", self.listener)

    def test_renderBoard_still_dispatches_on_the_current_board(self):
        """The guard is the fix precisely because this stays true."""
        i = self.js.index("function renderBoard(rows, data)")
        self.assertIn("state.board ===", self.js[i:i + 500])

    def test_the_default_scale_is_what_fires_the_event_on_load(self):
        """If HS were not the default this would be a rare race instead of
        the ordinary path."""
        sv = read("racecast", "static", "scale-view.js")
        self.assertIn('=== "pool" ? "pool" : "hs"', sv)
        i = sv.index("function init()")
        self.assertIn('rc-scale-change', sv[i:i + 400])

    def test_every_assignment_to_the_memo_carries_a_board_or_is_a_clear(self):
        for m in re.finditer(r"_lastBoard\s*=\s*([^;]+);", self.js):
            value = m.group(1).strip()
            self.assertTrue(value == "null" or "board:" in value,
                            f"_lastBoard = {value} has no board on it")


if __name__ == "__main__":
    unittest.main()
