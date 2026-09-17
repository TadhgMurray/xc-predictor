# Project: xc-predictor / tests
# File:    test_tfrrs_row_colmap.py
# Purpose: parseXCRow reads the columns the detector found, not fixed indices
#          -- and the page parser finds them once per table.
#
#   python -m pytest -q tests/test_tfrrs_row_colmap.py
#
# ! A STUB DOM, NOT BeautifulSoup. The parsers use a handful of bs4 methods
#   (get_text, find, find_all, get) and nothing else, so a forty-line stand-in
#   exercises the real parsing code with no bs4 installed and no HTML fixture
#   to keep in step. The sandbox has no bs4; the server does; this runs in
#   both.
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "tfrrs", "parser"))

import column_map as C                                          # noqa: E402
import parse_xc as P                                            # noqa: E402


class _A:
    def __init__(self, href, text):
        self._h, self._t = href, text

    def get(self, k, default=""):
        return self._h if k == "href" else default

    def get_text(self):
        return self._t


class _Cell:
    """One <td>: text, and zero or more <a> children."""

    def __init__(self, text, href=None):
        self._t, self._links = text, ([_A(href, text)] if href else [])

    def get_text(self):
        return self._t

    def find(self, name):
        return self._links[0] if name == "a" and self._links else None

    def find_all(self, name, **kw):
        return list(self._links) if name == "a" else []


class _Row:
    def __init__(self, cells):
        self._cells = cells

    def find_all(self, name, **kw):
        return list(self._cells) if name == "td" else []


ATH = "https://www.tfrrs.org/athletes/8270167/Conn_College/Grace_McDonough.html"
TEAM = "https://www.tfrrs.org/teams/xc/CT_college_f_Conn_College.html"


def todaysRow():
    return _Row([_Cell("13"), _Cell("Grace McDonough", ATH), _Cell("SR-4"),
                 _Cell("Conn College", TEAM), _Cell("5:44.9"),
                 _Cell("21:26.1"), _Cell("11")])


def shiftedRow():
    """tfrrs inserts a bib column at index 1."""
    return _Row([_Cell("13"), _Cell("1041"), _Cell("Grace McDonough", ATH),
                 _Cell("SR-4"), _Cell("Conn College", TEAM), _Cell("5:44.9"),
                 _Cell("21:26.1"), _Cell("11")])


def asTuples(row):
    return [(c.get_text().strip(), [a.get("href") for a in c.find_all("a")])
            for c in row.find_all("td")]


class TodaysLayoutIsUnchanged(unittest.TestCase):
    """Every existing caller passes no colmap; behaviour must be identical."""

    def test_no_colmap_reads_exactly_what_it_always_did(self):
        got = P.parseXCRow(todaysRow())
        self.assertTrue(got["ok"], got)
        self.assertEqual(got["place"], 13)
        self.assertEqual(got["name"], "Grace McDonough")
        self.assertEqual(got["athlete_native_id"], 8270167)
        self.assertEqual(got["year_raw"], "SR-4")
        self.assertEqual(got["time_seconds"], 1286.1)   # 21:26.1, not 5:44.9
        self.assertEqual(got["score"], 11)

    def test_the_detected_map_for_todays_layout_is_the_default(self):
        cm, note = C.detectColumns([asTuples(todaysRow())])
        self.assertEqual(cm, C.XC_DEFAULT)
        self.assertIsNone(note)


class AShiftedTable(unittest.TestCase):
    """★ THE WHOLE POINT. The same row, one column wider."""

    def test_the_old_fixed_indices_store_the_pace_as_the_time(self):
        """Recorded as an assertion so the damage is on the record: with no
        colmap, a shifted row's time comes from the AVG MILE cell."""
        got = P.parseXCRow(shiftedRow())
        self.assertEqual(got["time_seconds"], 344.9)    # 5:44.9 -- a pace
        self.assertNotEqual(got["name"], "Grace McDonough")

    def test_with_the_detected_map_every_field_is_right_again(self):
        cm, note = C.detectColumns([asTuples(shiftedRow())])
        got = P.parseXCRow(shiftedRow(), cm)
        self.assertTrue(got["ok"], got)
        self.assertEqual(got["place"], 13)
        self.assertEqual(got["name"], "Grace McDonough")
        self.assertEqual(got["athlete_native_id"], 8270167)
        self.assertEqual(got["year_raw"], "SR-4")
        self.assertEqual(got["time_seconds"], 1286.1)   # the finish, not 5:44.9
        self.assertEqual(got["score"], 11)
        self.assertIn("layout moved", note)


class RefusingRatherThanGuessing(unittest.TestCase):

    def test_an_unrecognised_layout_is_refused_not_read(self):
        got = P.parseXCRow(todaysRow(), {"place": 0, "team": 3})
        self.assertFalse(got["ok"])
        self.assertIn("not recognised", got["note"])

    def test_a_short_row_is_still_refused_first(self):
        short = _Row([_Cell("1"), _Cell("x")])
        self.assertFalse(P.parseXCRow(short)["ok"])


class TheWiring(unittest.TestCase):
    """The page parser has to compute it once per table and hand it down."""

    @staticmethod
    def _src():
        import io
        with io.open(os.path.join(_ROOT, "tfrrs", "parser",
                                  "parse_xc_page.py"), encoding="utf-8") as fh:
            return fh.read()

    def test_the_map_is_computed_once_per_table_not_per_row(self):
        src = self._src()
        i = src.index("def _parseRaceTable(")
        body = src[i:src.index("\n# ------", i)]
        self.assertIn("colmap, note = detectColumns(", body)
        self.assertIn("parseXCRow(tr, colmap)", body)
        self.assertLess(body.index("detectColumns("),
                        body.index("for tr in rows"))

    def test_a_layout_change_is_announced(self):
        self.assertIn('print(f"[tfrrs] meet {meet_id} event {event_id}: {note}"',
                      self._src())

    def test_the_sample_is_bounded(self):
        """! The adapters live in column_map now -- both page parsers need
        them, and a second copy is a second thing to keep in step."""
        import io as _io
        with _io.open(os.path.join(_ROOT, "tfrrs", "parser", "column_map.py"),
                      encoding="utf-8") as fh:
            cm = fh.read()
        self.assertIn("COLMAP_SAMPLE = 12", cm)
        self.assertIn("rows[:limit]", cm)
        self.assertIn("def sampleCells(rows, limit=COLMAP_SAMPLE):", cm)
        self.assertIn("def headerTexts(table):", cm)
        # and the XC page parser imports rather than redefines them
        self.assertNotIn("def _sampleCells(", self._src())

    def test_the_tf_page_parser_does_it_too(self):
        """★ THE EXTENSION. TF's four fixed columns were read by index exactly
        like XC's, and would shift exactly like XC's -- but its RESULT cell is
        chosen by the decoy decoder and must not be second-guessed here."""
        import io as _io
        with _io.open(os.path.join(_ROOT, "tfrrs", "parser",
                                   "parse_tf_page.py"), encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn("default=TF_DEFAULT", src)
        self.assertIn("parseTFRow(tr, hidden_classes, result_kind, colmap)", src)
        with _io.open(os.path.join(_ROOT, "tfrrs", "parser", "parse_tf.py"),
                      encoding="utf-8") as fh:
            tf = fh.read()
        # the result cell stays the decoder's
        self.assertIn("_realResultCellText(cells, hidden_classes)", tf)
        self.assertIn('trustworthy(cm, ("athlete",))', tf)


if __name__ == "__main__":
    unittest.main(verbosity=2)


# ===================================================================== #
#  THE TRACK ROW -- four fixed columns in front of a decoy-guarded result #
# ===================================================================== #
#
# ★ TF IS DIFFERENT AND ONLY HALF OF IT NEEDS THIS. TFRRS plants several
#   decoy TIME cells per row and hides the fakes with injected CSS;
#   parse_tf._realResultCellText reads that CSS to pick the real one. That
#   decoder is already content-driven and is the crux of the TF parser, so
#   column_map must not second-guess it. What TF gains is PL / NAME / YEAR /
#   TEAM, which were read by index exactly like XC's.

import parse_tf as PT                                          # noqa: E402

TF_ATH = "https://www.tfrrs.org/athletes/track/8050334/Oregon/Cole_Hocker.html"
TF_TEAM = "https://www.tfrrs.org/teams/tf/OR_college_m_Oregon.html"


class _ClassedCell(_Cell):
    """A <td> that also carries a class, which is how TF marks its decoys."""

    def __init__(self, text, href=None, css=None):
        super().__init__(text, href)
        self.attrs = {"class": [css] if css else []}

    def get(self, k, default=None):
        return self.attrs.get(k, default)


def tfRow(shifted=False):
    cells = [_ClassedCell("1"), _ClassedCell("Cole Hocker", TF_ATH),
             _ClassedCell("SO-2"), _ClassedCell("Oregon", TF_TEAM),
             _ClassedCell("3:50.55", css="compiled_round_4_601_11"),
             _ClassedCell("9:99.99", css="compiled_round_4_601_97")]
    if shifted:
        cells.insert(1, _ClassedCell("1041"))     # a bib column appears
    return _Row(cells)


class TheTrackRow(unittest.TestCase):

    def test_todays_layout_is_unchanged_with_no_colmap(self):
        got = PT.parseTFRow(tfRow(), {"compiled_round_4_601_97"}, "running")
        self.assertTrue(got["ok"], got)
        self.assertEqual(got["place"], 1)
        self.assertEqual(got["name"], "Cole Hocker")
        self.assertEqual(got["year_raw"], "SO-2")
        self.assertEqual(got["team_name"], "Oregon")

    def test_a_shifted_track_row_is_read_wrong_without_the_map(self):
        got = PT.parseTFRow(tfRow(shifted=True), {"compiled_round_4_601_97"}, "running")
        self.assertNotEqual(got.get("name"), "Cole Hocker")

    def test_and_right_with_it(self):
        rows = tfRow(shifted=True)
        cm, note = C.detectColumns([asTuples(rows)], default=C.TF_DEFAULT)
        got = PT.parseTFRow(rows, {"compiled_round_4_601_97"}, "running", cm)
        self.assertTrue(got["ok"], got)
        self.assertEqual(got["place"], 1)
        self.assertEqual(got["name"], "Cole Hocker")
        self.assertEqual(got["year_raw"], "SO-2")
        self.assertEqual(got["team_name"], "Oregon")
        self.assertIn("layout moved", note or "")

    def test_the_decoy_decoder_still_picks_the_result(self):
        """The real time, not the hidden one -- with or without a map."""
        for cm in (None, C.TF_DEFAULT):
            got = PT.parseTFRow(tfRow(), {"compiled_round_4_601_97"}, "running", cm)
            self.assertEqual(got["time_seconds"], 230.55)      # 3:50.55

    def test_tf_does_not_require_a_time_column_to_be_trusted(self):
        """Requiring one would refuse every well-formed track row."""
        self.assertTrue(C.trustworthy(C.TF_DEFAULT, ("athlete",)))
        self.assertFalse(C.trustworthy(C.TF_DEFAULT))          # the XC rule
